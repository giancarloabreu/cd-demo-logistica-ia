package br.com.cddemo.redespacho.coletor

import android.content.Context
import br.com.cddemo.redespacho.rede.Api
import br.com.cddemo.redespacho.rede.ErroApi
import br.com.cddemo.redespacho.rede.ResultadoLeitura
import com.fasterxml.jackson.annotation.JsonIgnoreProperties
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

@JsonIgnoreProperties(ignoreUnknown = true)
data class LeituraPendente(
    val idempotencyKey: String = "",
    val palletCodigo: String = "",
    val itemId: Long = 0,
    val codigoLido: String = "",
    val lidoEm: String = "",
    /** Quem bipou. 0 = leitura gravada por versão anterior do app, sem dono. */
    val usuarioId: Long = 0,
    val usuarioNome: String = "",
)

/**
 * Fila de envio das leituras.
 *
 * O bipe entra aqui e é enviado em segundo plano. Falha de rede **não desfaz**
 * a contagem: a leitura fica na fila e vai quando o wifi voltar — é o que
 * sustenta a operação no salto entre APs, sem o separador ter que bipar de
 * novo. A chave de idempotência garante que reenviar não conte duas vezes.
 *
 * Cada leitura guarda **quem bipou** e só é enviada com o login dele. Com o
 * token de outro usuário o servidor a recusaria (pallet de outro separador) e
 * ela seria descartada como recusa — caixa no pallet sem registro. Leituras de
 * outro usuário ficam **retidas** no aparelho até o dono entrar.
 *
 * Nada aqui apaga leitura que não foi aceita ou recusada pelo servidor: nem o
 * logout, nem a troca de usuário, nem reiniciar o app.
 *
 * Recusa de negócio (item errado, código não cadastrado, item fechado) é
 * definitiva: sai da fila na hora e desfaz o incremento.
 *
 * Ver docs/API.md §6 e docs/TELAS-COLETOR.md §5.1.
 */
class FilaLeituras(
    contexto: Context,
    private val escopo: CoroutineScope,
    private val obterApi: () -> Api?,
) {

    private val prefs = contexto.getSharedPreferences("fila_leituras", Context.MODE_PRIVATE)
    private val json = jacksonObjectMapper()
    private val trava = Mutex()

    private val fila = ArrayDeque<LeituraPendente>()

    /** Leituras do usuário logado (e as antigas, sem dono) esperando envio. */
    private val _pendentes = MutableStateFlow(0)
    val pendentes: StateFlow<Int> = _pendentes.asStateFlow()

    /** Leituras de outros usuários, guardadas até o dono entrar neste aparelho. */
    private val _retidas = MutableStateFlow(0)
    val retidas: StateFlow<Int> = _retidas.asStateFlow()

    private val _donosRetidas = MutableStateFlow<List<String>>(emptyList())
    val donosRetidas: StateFlow<List<String>> = _donosRetidas.asStateFlow()

    private val _online = MutableStateFlow(true)
    val online: StateFlow<Boolean> = _online.asStateFlow()

    /**
     * O servidor recusou o token: foi reiniciado (bandeja, parar.bat,
     * restauração) e as sessões, que moram na memória dele, caíram. Separado
     * de [online] porque o remédio é outro — entrar de novo, não esperar a rede.
     */
    private val _sessaoExpirada = MutableStateFlow(false)
    val sessaoExpirada: StateFlow<Boolean> = _sessaoExpirada.asStateFlow()

    /**
     * Muda a cada leitura que entra ou sai da fila. Quem consulta o servidor
     * guarda a versão antes e descarta a resposta se ela mudou: o retrato veio
     * de antes daquela leitura.
     */
    @Volatile var versao: Long = 0
        private set

    /** Servidor aceitou: a contagem dele é a que vale. */
    var aoAceitar: (LeituraPendente, ResultadoLeitura) -> Unit = { _, _ -> }

    /** Recusa definitiva: desfaz o incremento e avisa o separador. */
    var aoRecusar: (LeituraPendente, ErroApi) -> Unit = { _, _ -> }

    private var trabalhando = false

    @Volatile private var usuarioAtual: Long? = null

    init {
        // Leituras que sobraram de uma sessão anterior (bateria, app fechado,
        // atualização instalada) não podem sumir: voltam para a fila ao abrir
        // e começam a ser enviadas assim que o dono entrar. Antes ficavam
        // paradas até o próximo bipe — e travavam Sair e Encerrar pallet.
        runCatching {
            prefs.getString("pendentes", null)?.let { texto ->
                fila.addAll(json.readValue<List<LeituraPendente>>(texto))
            }
        }
        atualizarContagens()
        if (fila.isNotEmpty()) drenar()
    }

    fun enfileirar(leitura: LeituraPendente) {
        escopo.launch {
            trava.withLock {
                fila.addLast(leitura)
                versao++
                atualizarContagens()
                persistir()
            }
            drenar()
        }
    }

    /** Há leitura do usuário logado esperando envio. */
    fun temPendencia(): Boolean = _pendentes.value > 0

    /**
     * Leituras deste item ainda na fila — o servidor ainda não as conta.
     * Tudo aqui roda na thread principal (escopo do ViewModel), então ler a
     * fila fora da trava não disputa com quem a altera.
     */
    fun pendentesDoItem(itemId: Long): Int = fila.count { it.itemId == itemId }

    /** Leituras deste pallet ainda na fila: encerrar agora as deixaria fora do cartão. */
    fun pendentesDoPallet(codigo: String): Int = fila.count { it.palletCodigo.equals(codigo.trim(), ignoreCase = true) }

    /** Novo login: a fila volta a andar com o token novo. */
    fun sessaoRenovada() {
        _sessaoExpirada.value = false
    }

    /**
     * Quem está logado agora (null no logout). Só as leituras dele — e as
     * antigas, sem dono — são enviadas; as dos outros esperam.
     */
    fun definirUsuario(id: Long?) {
        usuarioAtual = id
        escopo.launch {
            trava.withLock { atualizarContagens() }
            if (id != null) drenar()
        }
    }

    private fun enviavel(l: LeituraPendente): Boolean = l.usuarioId == 0L || l.usuarioId == usuarioAtual

    /**
     * Envia uma de cada vez e em ordem. Paralelizar traria recusa falsa de
     * "quantidade completa" quando duas leituras do mesmo item chegassem fora
     * de ordem no fim da contagem.
     */
    private fun drenar() {
        escopo.launch {
            if (trava.withLock { if (trabalhando) return@withLock true else { trabalhando = true; false } }) {
                return@launch
            }
            try {
                var espera = ESPERA_INICIAL_MS
                while (true) {
                    val proxima = trava.withLock { fila.firstOrNull(::enviavel) } ?: break
                    val api = obterApi()
                    if (api == null) {
                        _online.value = false
                        delay(espera)
                        continue
                    }

                    try {
                        val r = api.registrarLeitura(
                            proxima.idempotencyKey, proxima.palletCodigo,
                            proxima.itemId, proxima.codigoLido, proxima.lidoEm,
                        )
                        remover(proxima)
                        _online.value = true
                        espera = ESPERA_INICIAL_MS
                        aoAceitar(proxima, r)
                    } catch (e: ErroApi) {
                        // Sessão expirada não é recusa da leitura: mantém na
                        // fila e espera o separador logar de novo.
                        if (e.codigo == "SESSAO_EXPIRADA") {
                            _online.value = false
                            _sessaoExpirada.value = true
                            delay(espera)
                            espera = (espera * 2).coerceAtMost(ESPERA_MAXIMA_MS)
                            continue
                        }
                        remover(proxima)
                        _online.value = true
                        aoRecusar(proxima, e)
                    } catch (e: Exception) {
                        // Rede: mantém na fila e tenta de novo, com espera
                        // crescente para não martelar o servidor caído.
                        _online.value = false
                        delay(espera)
                        espera = (espera * 2).coerceAtMost(ESPERA_MAXIMA_MS)
                    }
                }
            } finally {
                trava.withLock { trabalhando = false }
            }
        }
    }

    private suspend fun remover(leitura: LeituraPendente) = trava.withLock {
        fila.remove(leitura)
        versao++
        atualizarContagens()
        persistir()
    }

    private fun atualizarContagens() {
        _pendentes.value = fila.count(::enviavel)
        val outras = fila.filterNot(::enviavel)
        _retidas.value = outras.size
        _donosRetidas.value = outras.map { it.usuarioNome.ifBlank { "outro usuário" } }.distinct()
    }

    private fun persistir() {
        runCatching {
            prefs.edit().putString("pendentes", json.writeValueAsString(fila.toList())).apply()
        }
    }

    companion object {
        private const val ESPERA_INICIAL_MS = 1_000L
        private const val ESPERA_MAXIMA_MS = 15_000L
    }
}
