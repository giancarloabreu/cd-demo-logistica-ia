package br.com.cddemo.redespacho.ui

import android.app.Application
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import br.com.cddemo.redespacho.coletor.Evento
import br.com.cddemo.redespacho.coletor.Atualizador
import br.com.cddemo.redespacho.coletor.Feedback
import br.com.cddemo.redespacho.coletor.FilaLeituras
import br.com.cddemo.redespacho.coletor.LeituraPendente
import br.com.cddemo.redespacho.coletor.Scanner
import br.com.cddemo.redespacho.dados.Preferencias
import br.com.cddemo.redespacho.rede.Api
import br.com.cddemo.redespacho.rede.ErroApi
import br.com.cddemo.redespacho.rede.Instrucao
import br.com.cddemo.redespacho.rede.Item
import br.com.cddemo.redespacho.rede.Box
import br.com.cddemo.redespacho.rede.ConsultaPallet
import br.com.cddemo.redespacho.rede.ConteudoPallet
import br.com.cddemo.redespacho.rede.Devolucao
import br.com.cddemo.redespacho.rede.ItemDevolucao
import br.com.cddemo.redespacho.rede.Nota
import br.com.cddemo.redespacho.rede.PalletParaEnderecar
import br.com.cddemo.redespacho.rede.ProdutoRetorno
import br.com.cddemo.redespacho.rede.Repaletizacao
import br.com.cddemo.redespacho.rede.ResultadoFusao
import br.com.cddemo.redespacho.rede.ResultadoLeituraRetorno
import br.com.cddemo.redespacho.rede.RetornoDetalhe
import br.com.cddemo.redespacho.rede.RetornoPallet
import br.com.cddemo.redespacho.rede.RetornoResumo
import br.com.cddemo.redespacho.rede.Usuario
import br.com.cddemo.redespacho.rede.VersaoApp
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.OffsetDateTime
import java.util.UUID

enum class Tela {
    CONFIGURACAO, LOGIN, MENU, INSTRUCOES, NOTAS, ITENS, SEPARACAO, CRIAR_PALLET,
    ENDERECAMENTO, DEVOLUCOES, ITENS_DEVOLUCAO, CONFERENCIA,
    RETORNO, CONFERENCIA_RETORNO,
    TRANSFERENCIA, REIMPRESSAO, CONSULTA_PALLET, CONFERENCIA_BOX,
}

/** Diálogo bloqueante: o separador precisa confirmar antes de seguir bipando. */
data class Alerta(val titulo: String, val detalhe: String, val grave: Boolean)

/**
 * Uma leitura de retorno, com tudo para reenviá-la. Guardada quando fica sem
 * resposta: com a mesma chave, se a primeira chegou, o servidor devolve o
 * resultado dela em vez de contar a caixa de novo. Leva o pallet e o retorno
 * dela — o reenvio vai para onde a caixa foi bipada, não para o pallet que
 * estiver aberto na hora.
 */
private data class LeituraRetornoSemResposta(
    val retornoId: Long,
    val retornoPalletId: Long,
    val palletCodigo: String,
    val codigoLido: String,
    val idempotencyKey: String,
    val lidoEm: String,
)

private const val MSG_PALLET_FECHADO = "Pallet fechado: não aceita leitura. Reabrir é na retaguarda."

data class EstadoApp(
    val tela: Tela = Tela.LOGIN,
    val servidor: String = "",
    val porta: String = "8080",
    val login: String = "",
    val usuario: Usuario? = null,

    val instrucoes: List<Instrucao> = emptyList(),
    val instrucao: Instrucao? = null,
    val notas: List<Nota> = emptyList(),
    val nota: Nota? = null,
    val itens: List<Item> = emptyList(),
    val item: Item? = null,

    /** Pallet de separação (P-RED-) aberto no aparelho. */
    val palletAtivo: String? = null,
    /**
     * Pallet de devolução (P-DEV-) da conferência. Campo próprio: num campo só,
     * o P-DEV da conferência virava o pallet dos menus da separação.
     */
    val palletDevolucao: String? = null,
    val palletsCriados: List<String> = emptyList(),
    /** Tipo escolhido na tela de criar pallet: SEPARACAO ou DEVOLUCAO. */
    val tipoPallet: String = "SEPARACAO",

    // Conferência de devolução: NF do cliente, itens e o item em conferência.
    val devolucoes: List<Devolucao> = emptyList(),
    val devolucao: Devolucao? = null,
    val itensDevolucao: List<ItemDevolucao> = emptyList(),
    val itemDevolucao: ItemDevolucao? = null,

    // Conferência de retorno: NF expedida que voltou, seus pallets e o pallet
    // em conferência.
    val retornos: List<RetornoResumo> = emptyList(),
    /** Servidor anterior à 0.6.0 não tem retorno: a seção some da lista. */
    val retornosIndisponiveis: Boolean = false,
    val retorno: RetornoResumo? = null,
    val palletsRetorno: List<RetornoPallet> = emptyList(),
    val palletRetorno: RetornoPallet? = null,
    /** Produto da última caixa aceita, destacado na lista do pallet. */
    val produtoLidoRetorno: String? = null,

    // Repaletização (Tela.TRANSFERENCIA): pallet de origem lido e o interruptor
    // "Juntar o pallet inteiro". Desligado, a tela passa caixa a caixa.
    val palletOrigem: ConteudoPallet? = null,
    val unirArmado: Boolean = false,
    /** Par aberto na repaletização caixa a caixa: origem e destino já conferidos. */
    val repaletizacao: Repaletizacao? = null,
    /** Alguma caixa passou ou voltou neste par desde que foi aberto. */
    val cartoesDesatualizados: Boolean = false,
    /** Caixa bipada que ficou sem resposta: reenviada antes de qualquer outra coisa. */
    val caixaRepaletizacaoSemResposta: CaixaRepaletizacaoSemResposta? = null,
    /** Pedido de confirmação para sair com os cartões por imprimir. */
    val saidaRepaletizacao: SaidaRepaletizacao? = null,
    /** O que a tela de reimpressão manda imprimir: ETIQUETA ou CARTAO. */
    val tipoImpressao: String = "ETIQUETA",
    /** Pallet bipado em "Consultar pallet", com os outros da mesma NF. */
    val consultaPallet: ConsultaPallet? = null,
    /** Box bipado em "Conferência de box", com o que está em cada nível. */
    val conferenciaBox: Box? = null,
    /** Pallet cuja etiqueta abriu a conferência de box: aparece marcado no nível dele. */
    val conferenciaBoxPallet: String? = null,

    // Endereçamento: pallet lido, box lido e nível escolhido.
    val palletEndereco: PalletParaEnderecar? = null,
    val box: Box? = null,
    val posicaoEscolhida: Long? = null,
    val pendentes: List<PalletParaEnderecar> = emptyList(),

    val carregando: Boolean = false,
    /** Leituras bipadas e ainda não confirmadas pelo servidor. */
    val leiturasPendentes: Int = 0,
    /** Leituras de outro usuário guardadas no aparelho até ele entrar. */
    val leiturasRetidas: Int = 0,
    val donosRetidas: List<String> = emptyList(),
    val online: Boolean = true,
    val mensagem: String? = null,
    val sucesso: String? = null,
    val alerta: Alerta? = null,

    /** Versão publicada no servidor, quando for mais nova que a instalada. */
    val atualizacao: VersaoApp? = null,
    val baixandoAtualizacao: Int? = null,
)

/**
 * Leva a contagem nova de um item a todo lugar da tela onde ele aparece: o
 * item aberto, a lista de itens e o total da nota.
 *
 * Antes só o item aberto era atualizado. A lista guardava o número de quando
 * a nota foi aberta, então voltar para ela mostrava 0 caixas, reabrir o item
 * trazia esse 0 junto, e só o bipe seguinte mostrava o número verdadeiro.
 */
internal fun EstadoApp.comQtdDoItem(itemId: Long, nova: (Int) -> Int): EstadoApp {
    fun ajustar(i: Item) =
        if (i.id == itemId) i.copy(qtdSeparada = nova(i.qtdSeparada).coerceAtLeast(0)) else i

    val novosItens = itens.map(::ajustar)
    val daNotaAberta = itens.any { it.id == itemId }
    val totalDaNota = novosItens.sumOf { it.qtdSeparada }
    fun ajustarNota(n: Nota) =
        if (daNotaAberta && n.id == nota?.id) n.copy(qtdSeparada = totalDaNota) else n

    return copy(
        item = item?.let(::ajustar),
        itens = novosItens,
        nota = nota?.let(::ajustarNota),
        notas = notas.map(::ajustarNota),
    )
}

class AppViewModel(app: Application) : AndroidViewModel(app) {

    private val prefs = Preferencias(app)
    private val feedback = Feedback(app)
    val scanner = Scanner(app)
    private var api: Api? = null

    private val fila = FilaLeituras(app, viewModelScope) { api }

    private val _estado = MutableStateFlow(
        EstadoApp(
            tela = if (prefs.configurado) Tela.LOGIN else Tela.CONFIGURACAO,
            servidor = prefs.servidor,
            porta = prefs.porta.toString(),
            login = prefs.ultimoLogin,
        )
    )
    val estado: StateFlow<EstadoApp> = _estado.asStateFlow()

    private val dispositivoId: String = "${Build.MANUFACTURER}-${Build.MODEL}".take(40)

    init {
        // O servidor é a autoridade da contagem: aceito, o número dele vale.
        fila.aoAceitar = { pendente, r ->
            // O número do servidor ainda não conta as leituras que estão atrás
            // desta na fila; somá-las evita o contador voltar no meio de uma
            // rajada de bipes.
            val naFila = fila.pendentesDoItem(pendente.itemId)
            _estado.update { it.comQtdDoItem(pendente.itemId) { _ -> r.qtdSeparada + naFila } }
            feedback.sinalizar(if (r.itemCompleto) Evento.ITEM_COMPLETO else Evento.ACEITA)
        }

        // Recusa de negócio é definitiva: desfaz o que a tela tinha somado.
        fila.aoRecusar = { pendente, erro ->
            _estado.update { it.comQtdDoItem(pendente.itemId) { qtd -> qtd - 1 } }
            tratarRecusaDeLeitura(erro, pendente.codigoLido)
        }

        viewModelScope.launch {
            fila.pendentes.collect { n -> _estado.update { it.copy(leiturasPendentes = n) } }
        }
        viewModelScope.launch {
            fila.online.collect { on -> _estado.update { it.copy(online = on) } }
        }
        viewModelScope.launch {
            fila.retidas.collect { n -> _estado.update { it.copy(leiturasRetidas = n) } }
        }
        viewModelScope.launch {
            fila.donosRetidas.collect { d -> _estado.update { it.copy(donosRetidas = d) } }
        }
        // Servidor reiniciado derruba as sessões. Antes o coletor ficava em
        // "Sem servidor" para sempre, bipando numa fila que não andava, sem
        // nada que levasse ao login. A fila fica: vai depois do login.
        viewModelScope.launch {
            fila.sessaoExpirada.collect { expirou ->
                val tela = _estado.value.tela
                if (expirou && tela != Tela.LOGIN && tela != Tela.CONFIGURACAO) {
                    api = null
                    _estado.update {
                        it.copy(
                            tela = Tela.LOGIN,
                            alerta = null,
                            mensagem = "O servidor foi reiniciado — entre de novo. " +
                                "As leituras que estavam na fila são enviadas depois do login.",
                        )
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------- configuração

    /**
     * Limpa o campo do servidor sozinho: `http://ip:8080` vira `ip`, e a porta
     * vai para o campo dela.
     *
     * O texto pode chegar por dois caminhos — o QR lido pelo app, ou o mesmo QR
     * digitado no campo pelo DataWedge em modo teclado, que é como o coletor
     * vem de fábrica. Tratar aqui cobre os dois sem depender de qual perfil o
     * aparelho está usando.
     */
    fun aoMudarServidor(v: String) {
        val limpo = v.trim().removePrefix("https://").removePrefix("http://").substringBefore('/')
        val host = limpo.substringBefore(':').trim()
        val porta = limpo.substringAfter(':', "").filter(Char::isDigit)
        _estado.update {
            it.copy(
                servidor = host,
                porta = porta.ifBlank { it.porta },
                mensagem = null,
            )
        }
    }
    fun aoMudarPorta(v: String) = _estado.update { it.copy(porta = v.filter(Char::isDigit), mensagem = null) }
    fun aoMudarLogin(v: String) = _estado.update { it.copy(login = v, mensagem = null) }
    fun limparAviso() = _estado.update { it.copy(mensagem = null, sucesso = null) }
    fun fecharAlerta() = _estado.update { it.copy(alerta = null) }

    fun irPara(tela: Tela) = _estado.update { it.copy(tela = tela, mensagem = null, sucesso = null) }

    /**
     * Botão físico voltar. Devolve `false` quando não há para onde voltar — aí
     * a Activity manda o app para segundo plano em vez de fechar, preservando
     * a sessão: reabrir volta direto ao menu, sem novo login.
     */
    fun voltar(): Boolean {
        val e = _estado.value
        if (e.alerta != null) { fecharAlerta(); return true }
        // Diálogo de saída da repaletização aberto: voltar é "continuar aqui".
        if (e.saidaRepaletizacao != null) { fecharSaidaRepaletizacao(); return true }
        if (e.tela == Tela.TRANSFERENCIA) {
            // Com caixa passada e cartões por imprimir, sair calado deixaria no
            // palete um cartão que mente sobre a quantidade: pergunta antes.
            if (e.repaletizacaoPorImprimir) {
                _estado.update { it.copy(saidaRepaletizacao = SaidaRepaletizacao.VOLTAR) }
                return true
            }
            // Par sem mudança não tem o que imprimir: não sobrevive à tela.
            _estado.update { it.copy(repaletizacao = null) }
        }

        val destino = when (e.tela) {
            Tela.SEPARACAO -> Tela.ITENS
            Tela.ITENS -> Tela.NOTAS
            Tela.NOTAS -> Tela.INSTRUCOES
            Tela.INSTRUCOES -> Tela.MENU
            Tela.CRIAR_PALLET -> Tela.MENU
            Tela.ENDERECAMENTO -> Tela.MENU
            Tela.TRANSFERENCIA -> Tela.MENU
            Tela.REIMPRESSAO -> Tela.MENU
            Tela.CONSULTA_PALLET -> Tela.MENU
            Tela.CONFERENCIA_BOX -> Tela.MENU
            Tela.CONFERENCIA -> Tela.ITENS_DEVOLUCAO
            Tela.ITENS_DEVOLUCAO -> Tela.DEVOLUCOES
            Tela.CONFERENCIA_RETORNO -> Tela.RETORNO
            Tela.RETORNO -> Tela.DEVOLUCOES
            Tela.DEVOLUCOES -> Tela.MENU
            // Configuração só volta ao login se já houver servidor gravado.
            Tela.CONFIGURACAO -> if (prefs.configurado) Tela.LOGIN else null
            // No menu e no login, sair é ação explícita — voltar sem querer no
            // meio do turno derrubaria a sessão do separador.
            Tela.MENU, Tela.LOGIN -> null
        } ?: return false

        irPara(destino)
        // Voltar para uma lista mostra o que o servidor sabe agora — outro
        // separador pode ter bipado no mesmo item enquanto isso.
        if (destino == Tela.ITENS || destino == Tela.NOTAS) atualizarListasEmSilencio()
        // Idem no retorno: outro coletor pode estar conferindo outro pallet da
        // mesma NF, e o retorno que acabou de encerrar sai da lista.
        if (destino == Tela.RETORNO) atualizarRetornoEmSilencio()
        if (destino == Tela.DEVOLUCOES) atualizarRetornosEmSilencio()
        return true
    }

    /**
     * Configura o aparelho bipando o QR da retaguarda.
     *
     * Digitar IP à mão em cada coletor é lento e erra — e vira problema real no
     * dia em que o endereço do servidor muda, porque são todos os aparelhos de
     * uma vez. O QR carrega `http://ip:porta`; aqui só se separa host de porta.
     */
    private fun lerEnderecoDoServidor(codigo: String) {
        val limpo = codigo.trim()
        if (!scanner.aceitar(limpo)) return

        val semEsquema = limpo
            .removePrefix("https://")
            .removePrefix("http://")
            .substringBefore('/')
            .trim()
        val host = semEsquema.substringBefore(':').trim()

        if (host.isBlank() || host.any { it.isWhitespace() }) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update {
                it.copy(mensagem = "Isto não parece o QR do servidor. Bipe o código da aba Coletores.")
            }
            return
        }

        feedback.sinalizar(Evento.ACEITA)
        aoMudarServidor(semEsquema)
        _estado.update { it.copy(sucesso = null) }
        // Testa na hora: bipar e não saber se funcionou obrigaria a descobrir o
        // erro só no login, longe do PC onde está o QR.
        testarConexao()
    }

    fun testarConexao() = comCarregamento {
        val e = _estado.value
        val porta = e.porta.toIntOrNull() ?: 8080
        val saude = Api("http://${e.servidor.trim()}:$porta").saude()
        prefs.servidor = e.servidor
        prefs.porta = porta
        _estado.update { it.copy(sucesso = "Servidor respondeu — versão ${saude.versao}, banco OK") }
    }

    fun salvarConfiguracao() {
        val e = _estado.value
        prefs.servidor = e.servidor
        prefs.porta = e.porta.toIntOrNull() ?: 8080
        irPara(Tela.LOGIN)
    }

    fun entrar(senha: String) = comCarregamento {
        if (!prefs.configurado) {
            _estado.update { it.copy(tela = Tela.CONFIGURACAO, mensagem = "Configure o servidor primeiro") }
            return@comCarregamento
        }
        val cliente = Api(prefs.urlBase)
        val r = cliente.entrar(_estado.value.login.trim(), senha, dispositivoId)
        api = cliente
        fila.sessaoRenovada()
        fila.definirUsuario(r.usuario.id)
        prefs.ultimoLogin = _estado.value.login.trim()
        _estado.update {
            // Outro usuário no aparelho (a sessão caiu e outro entrou): nada do
            // anterior serve para ele — nem o pallet, nem a nota aberta.
            val outro = it.usuario != null && it.usuario.id != r.usuario.id
            if (outro) {
                it.copy(
                    usuario = r.usuario, tela = Tela.MENU,
                    palletAtivo = null, palletDevolucao = null, palletOrigem = null,
                    instrucao = null, notas = emptyList(), nota = null, itens = emptyList(), item = null,
                    devolucao = null, itensDevolucao = emptyList(), itemDevolucao = null,
                    retorno = null, palletsRetorno = emptyList(), palletRetorno = null,
                    repaletizacao = null, cartoesDesatualizados = false,
                    caixaRepaletizacaoSemResposta = null, saidaRepaletizacao = null,
                )
            } else {
                it.copy(
                    usuario = r.usuario, tela = Tela.MENU,
                    // A sessão caiu no meio de uma repaletização: o par e a caixa
                    // sem resposta continuam no aparelho, esperando o operador.
                    mensagem = it.repaletizacao?.let { p ->
                        "A repaletização de ${p.origem.codigo} para ${p.destino.codigo} ficou aberta. " +
                            "Toque em Repaletização para continuar ou concluir."
                    },
                )
            }
        }
        verificarAtualizacao()
    }

    /** Checa no login: é o momento em que o separador está parado mesmo. */
    private fun verificarAtualizacao() {
        viewModelScope.launch {
            runCatching {
                val v = exigirApi().versaoDisponivel()
                val instalada = getApplication<Application>().packageManager
                    .getPackageInfo(getApplication<Application>().packageName, 0).versionName.orEmpty()
                if (v.disponivel && Atualizador.ehMaisNova(v.versao, instalada)) {
                    _estado.update { it.copy(atualizacao = v) }
                }
            }
        }
    }

    fun dispensarAtualizacao() = _estado.update { it.copy(atualizacao = null) }

    fun baixarEInstalarAtualizacao() {
        val v = _estado.value.atualizacao ?: return
        viewModelScope.launch {
            _estado.update { it.copy(baixandoAtualizacao = 0) }
            try {
                val app = getApplication<Application>()
                val arquivo = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    Atualizador.baixar(app, prefs.urlBase) { pct ->
                        _estado.update { it.copy(baixandoAtualizacao = pct) }
                    }
                }
                _estado.update { it.copy(baixandoAtualizacao = null, atualizacao = null) }
                Atualizador.instalar(app, arquivo)
            } catch (e: Exception) {
                _estado.update {
                    it.copy(
                        baixandoAtualizacao = null,
                        alerta = Alerta(
                            "Não foi possível baixar",
                            "Verifique a rede e tente de novo pelo menu.",
                            true,
                        ),
                    )
                }
            }
        }
    }

    /**
     * Sai sem nunca apagar leitura: a fila fica guardada no aparelho, com o
     * dono de cada leitura, e vai quando ele entrar de novo.
     *
     * Antes o logout apagava a fila — caixas no pallet sem registro. E
     * bloquear o logout enquanto houvesse fila prendia o coletor quando o
     * servidor mudava de IP: sem sair, não se chega à configuração.
     */
    fun sair() = comCarregamento {
        val pendentes = _estado.value.leiturasPendentes
        // A caixa de repaletização sem resposta mora só na memória: sair a
        // perde. Não se esconde isso — o operador precisa conferir os pallets.
        val semResposta = _estado.value.caixaRepaletizacaoSemResposta
        api?.sair()
        api = null
        fila.definirUsuario(null)
        _estado.update {
            EstadoApp(
                tela = Tela.LOGIN, servidor = prefs.servidor,
                porta = prefs.porta.toString(), login = prefs.ultimoLogin,
                mensagem = listOfNotNull(
                    if (pendentes > 0) {
                        "$pendentes leitura(s) sua(s) ainda não chegaram ao servidor. Ficaram guardadas neste " +
                            "coletor e são enviadas quando você entrar de novo."
                    } else {
                        null
                    },
                    semResposta?.let { c ->
                        "A caixa ${c.codigoLido} da repaletização de ${c.origem} para ${c.destino} ficou sem " +
                            "confirmação do servidor. Confira os dois pallets na retaguarda."
                    },
                ).joinToString("\n\n").ifEmpty { null },
            )
        }
    }

    // ---------------------------------------------------------------- navegação

    fun carregarInstrucoes() = comCarregamento {
        val lista = exigirApi().instrucoesEmAberto()
        _estado.update { it.copy(instrucoes = lista, tela = Tela.INSTRUCOES) }
    }

    fun abrirInstrucao(i: Instrucao) = comCarregamento {
        val notas = exigirApi().notas(i.id)
        _estado.update { it.copy(instrucao = i, notas = notasComPendentes(notas, it), tela = Tela.NOTAS) }
    }

    fun abrirNota(n: Nota) = comCarregamento {
        val itens = comPendentes(exigirApi().itens(n.id))
        _estado.update { it.copy(nota = n, itens = itens, tela = Tela.ITENS) }
    }

    fun recarregarItens() = comCarregamento { recarregarItensAgora() }

    /**
     * Lista do servidor mais o que ainda está na fila. Descartada se alguma
     * leitura entrou ou saiu da fila durante a consulta: o retrato veio de
     * antes dela, e aplicá-lo faria o contador recuar.
     *
     * Sem [comCarregamento] de propósito: chamada de dentro de outra ação, um
     * segundo comCarregamento apagava o aviso que ela acabara de dar.
     */
    private suspend fun recarregarItensAgora() {
        val nota = _estado.value.nota ?: return
        val versao = fila.versao
        val itens = exigirApi().itens(nota.id)
        if (fila.versao != versao) return
        val ajustados = comPendentes(itens)
        _estado.update { e ->
            if (e.nota?.id != nota.id) e
            else e.copy(itens = ajustados, item = e.item?.let { atual -> ajustados.firstOrNull { it.id == atual.id } ?: atual })
        }
    }

    /**
     * A contagem da tela é o número do servidor mais as leituras ainda na
     * fila — o servidor ainda não as conta. Sem somar, cada lista recarregada
     * "desbipava" caixas, e a recusa de uma delas descontava do que o
     * servidor já tinha.
     */
    private fun comPendentes(itens: List<Item>): List<Item> =
        itens.map { it.copy(qtdSeparada = it.qtdSeparada + fila.pendentesDoItem(it.id)) }

    private fun notasComPendentes(notas: List<Nota>, st: EstadoApp): List<Nota> {
        val aberta = st.nota?.id ?: return notas
        val naFila = st.itens.sumOf { fila.pendentesDoItem(it.id) }
        return notas.map { if (it.id == aberta) it.copy(qtdSeparada = it.qtdSeparada + naFila) else it }
    }

    /**
     * Encerrar ou juntar um pallet com leitura dele ainda na fila deixaria
     * essas caixas fora do cartão — e o servidor recusaria as leituras depois,
     * com o pallet já encerrado. Avisa e devolve false.
     */
    private fun palletLivreDaFila(codigo: String?): Boolean {
        val n = codigo?.let { fila.pendentesDoPallet(it) } ?: 0
        if (n == 0) return true
        feedback.sinalizar(Evento.ITEM_ERRADO)
        _estado.update {
            it.copy(
                alerta = Alerta(
                    "Aguarde o envio",
                    "$n leitura(s) do pallet $codigo ainda não chegaram ao servidor. Espere a faixa de envio sumir.",
                    true,
                )
            )
        }
        return false
    }

    /**
     * Abre o item mesmo fechado.
     *
     * Recusar aqui trancava o operador para fora justamente quando ele mais
     * precisa entrar: é depois de fechar o cadeado que se percebe a caixa
     * bipada por engano, e o menu de excluir só existe dentro desta tela.
     * A leitura continua barrada — pelo servidor, que é quem decide.
     */
    fun abrirItem(i: Item) {
        _estado.update {
            it.copy(
                item = i,
                tela = Tela.SEPARACAO,
                mensagem = if (i.fechado) "Item fechado: não aceita leitura. Dá para excluir pelo menu ⋮." else null,
                sucesso = null,
            )
        }
        atualizarListasEmSilencio()
    }

    /**
     * Recarrega itens e notas sem travar a tela nem mexer nos avisos.
     *
     * Soma o que está na fila ([comPendentes]) e descarta a resposta se uma
     * leitura entrou ou saiu da fila durante a consulta ([FilaLeituras.versao]):
     * aplicar um retrato de antes dela faria o contador voltar.
     */
    private fun atualizarListasEmSilencio() {
        val cliente = api ?: return
        val nota = _estado.value.nota
        val instrucao = _estado.value.instrucao
        viewModelScope.launch {
            runCatching {
                if (nota != null) {
                    val versao = fila.versao
                    val itens = cliente.itens(nota.id)
                    if (fila.versao == versao) {
                        val ajustados = comPendentes(itens)
                        _estado.update { e ->
                            if (e.nota?.id != nota.id) e
                            else e.copy(
                                itens = ajustados,
                                item = e.item?.let { a -> ajustados.firstOrNull { it.id == a.id } ?: a },
                            )
                        }
                    }
                }
                if (instrucao != null) {
                    val versao = fila.versao
                    val notas = cliente.notas(instrucao.id)
                    if (fila.versao == versao) {
                        _estado.update { e ->
                            if (e.instrucao?.id != instrucao.id) e
                            else {
                                val ajustadas = notasComPendentes(notas, e)
                                e.copy(notas = ajustadas, nota = e.nota?.let { a -> ajustadas.firstOrNull { it.id == a.id } ?: a })
                            }
                        }
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ pallets

    fun escolherTipoPallet(tipo: String) =
        _estado.update { it.copy(tipoPallet = tipo, palletsCriados = emptyList(), sucesso = null) }

    fun criarPallets(quantidade: Int) = comCarregamento {
        val r = exigirApi().criarPallets(quantidade, _estado.value.tipoPallet)
        val faixa = r.pallets.first() + if (r.pallets.size > 1) " a ${r.pallets.last()}" else ""
        // O resultado da impressão era ignorado: sem impressora, a tela dizia
        // "criado" e o operador ficava esperando uma etiqueta que não vinha.
        _estado.update {
            it.copy(
                palletsCriados = r.pallets,
                sucesso = when (r.impressao) {
                    "ENVIADA" -> "Criado e enviado à impressora: $faixa"
                    "SEM_IMPRESSORA", "FALHOU" -> null
                    else -> "Criado: $faixa"
                },
                mensagem = when (r.impressao) {
                    "SEM_IMPRESSORA" -> "Criado: $faixa — mas seu usuário não tem impressora. " +
                        "Cadastre na retaguarda e use Reimprimir."
                    "FALHOU" -> "Criado: $faixa — a impressora não respondeu. " +
                        "Veja se está ligada e use Reimprimir."
                    else -> null
                },
            )
        }
    }

    fun selecionarPallet(codigo: String) {
        val nota = _estado.value.nota ?: return
        // Pallet de devolução não recebe separação (o servidor também recusa).
        if (codigo.trim().uppercase().startsWith("P-DEV-")) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update {
                it.copy(alerta = Alerta("Pallet de devolução", "Este pallet não recebe separação. Bipe um pallet P-RED-.", true))
            }
            return
        }
        viewModelScope.launch {
            _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
            try {
                val p = exigirApi().selecionarPallet(codigo.trim().uppercase(), nota.id)
                feedback.sinalizar(Evento.ACEITA)
                _estado.update { it.copy(palletAtivo = p.codigo, sucesso = "Pallet ${p.codigo} selecionado") }
            } catch (e: ErroApi) {
                // Recusa de pallet usa o mesmo diálogo bloqueante das leituras
                // erradas: o separador precisa confirmar antes de seguir.
                feedback.sinalizar(Evento.ITEM_ERRADO)
                _estado.update {
                    it.copy(
                        alerta = Alerta(
                            when (e.codigo) {
                                "PALLET_DE_OUTRA_NOTA" -> "Pallet de outra nota"
                                "PALLET_EM_USO_POR_OUTRO" -> "Pallet em uso"
                                "PALLET_ENCERRADO" -> "Pallet encerrado"
                                "PALLET_NAO_ENCONTRADO" -> "Pallet não encontrado"
                                else -> "Não foi possível selecionar"
                            },
                            e.mensagem, true,
                        )
                    )
                }
            } catch (e: Exception) {
                feedback.sinalizar(Evento.ITEM_ERRADO)
                _estado.update {
                    it.copy(alerta = Alerta("Sem resposta do servidor", "Pallet não selecionado.", true))
                }
            } finally {
                _estado.update { it.copy(carregando = false) }
            }
        }
    }

    /**
     * Junta o pallet lido no pallet ativo. Dois separadores que terminaram com
     * meio pallet põem tudo num só. Ver docs/TELAS-COLETOR.md §5.5.
     */
    fun fundirNoPalletAtivo(origem: String) = comCarregamento {
        val destino = _estado.value.palletAtivo ?: return@comCarregamento
        if (!palletLivreDaFila(destino) || !palletLivreDaFila(origem.trim().uppercase())) return@comCarregamento
        val r = exigirApi().fundirPallets(origem.trim().uppercase(), destino)
        feedback.sinalizar(Evento.ITEM_COMPLETO)
        _estado.update {
            it.copy(
                sucesso = "${r.qtdCaixas} caixa(s) de ${r.palletOrigem} passaram para " +
                    "${r.palletDestino}. Descarte o cartão do pallet de origem.",
            )
        }
        recarregarItensAgora()
    }

    fun reimprimirPalletAtivo() = comCarregamento {
        val p = _estado.value.palletAtivo ?: return@comCarregamento
        exigirApi().reimprimirPallet(p)
        _estado.update { it.copy(sucesso = "Etiqueta de $p reenviada à impressora") }
    }

    fun encerrarPallet() = comCarregamento {
        // Sem pallet aberto a função saía em silêncio, e "Encerrar pallet" no
        // menu parecia não fazer nada — foi o que travou o operador no teste.
        val p = _estado.value.palletAtivo ?: run {
            _estado.update {
                it.copy(
                    mensagem = "Nenhum pallet aberto neste aparelho. Para encerrar outro, " +
                        "use Repaletização e bipe o pallet.",
                )
            }
            return@comCarregamento
        }
        if (!palletLivreDaFila(p)) return@comCarregamento
        exigirApi().encerrarPallet(p)
        // Só zera se ainda for o mesmo: em "Trocar pallet" o novo pode ter sido
        // bipado antes de esta resposta chegar, e zerar apagaria o novo.
        _estado.update { it.copy(palletAtivo = it.palletAtivo.takeIf { a -> a != p }, sucesso = "Pallet $p encerrado") }
    }

    /**
     * Encerra qualquer pallet lido, não só o aberto no aparelho.
     *
     * O vínculo com o pallet mora na memória do app e some ao reiniciá-lo;
     * sem isto um pallet ficava EM_USO para sempre se o separador trocasse de
     * aparelho ou o app fosse fechado no meio do turno.
     */
    fun encerrarPalletPorCodigo(codigo: String) = comCarregamento {
        val alvo = codigo.trim().uppercase()
        if (!palletLivreDaFila(alvo)) return@comCarregamento
        exigirApi().encerrarPallet(alvo)
        feedback.sinalizar(Evento.ACEITA)
        _estado.update { st ->
            st.copy(
                palletAtivo = st.palletAtivo.takeIf { it != alvo },
                palletDevolucao = st.palletDevolucao.takeIf { it != alvo },
                palletOrigem = st.palletOrigem?.let { o ->
                    if (o.codigo == alvo) o.copy(status = "ENCERRADO") else o
                },
                sucesso = "Pallet $alvo encerrado",
            )
        }
    }

    // ------------------------------------------------------------ endereçamento

    fun abrirEnderecamento() = comCarregamento {
        val fila = exigirApi().pendentesDeEnderecamento()
        _estado.update {
            it.copy(
                tela = Tela.ENDERECAMENTO,
                pendentes = fila,
                palletEndereco = null, box = null, posicaoEscolhida = null,
            )
        }
    }

    /**
     * Uma leitura só para as duas etapas: o prefixo diz se veio pallet ou box,
     * então o separador não escolhe o tipo antes de bipar.
     * Ver docs/MODELO-DE-DADOS.md §2.1.
     */
    fun lerNoEnderecamento(codigo: String) {
        val limpo = codigo.trim().uppercase()
        if (!scanner.aceitar(limpo)) return
        if (ehCodigoDePallet(limpo)) lerPallet(limpo) else lerBox(limpo)
    }

    // ehCodigoDePallet mora em RegrasTransferencia.kt: a regra do bipe da
    // repaletização usa o mesmo prefixo e é testada sem Android.

    private fun lerPallet(codigo: String) = comCarregamento {
        val p = exigirApi().palletParaEnderecar(codigo)
        feedback.sinalizar(Evento.ACEITA)
        _estado.update { it.copy(palletEndereco = p, box = null, posicaoEscolhida = null) }
    }

    private fun lerBox(codigo: String) = comCarregamento {
        if (_estado.value.palletEndereco == null) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update {
                it.copy(alerta = Alerta("Leia o pallet primeiro", "Bipe a etiqueta do pallet antes do box.", true))
            }
            return@comCarregamento
        }
        val b = exigirApi().box(codigo)
        feedback.sinalizar(Evento.ACEITA)
        _estado.update { it.copy(box = b, posicaoEscolhida = null) }
    }

    fun escolherPosicao(id: Long) = _estado.update { it.copy(posicaoEscolhida = id) }

    fun confirmarEnderecamento() = comCarregamento {
        val e = _estado.value
        val pallet = e.palletEndereco ?: return@comCarregamento
        val posicao = e.posicaoEscolhida ?: return@comCarregamento
        exigirApi().enderecar(pallet.codigo, posicao)
        feedback.sinalizar(Evento.ITEM_COMPLETO)
        val fila = exigirApi().pendentesDeEnderecamento()
        _estado.update {
            it.copy(
                palletEndereco = null, box = null, posicaoEscolhida = null,
                pendentes = fila,
                sucesso = "${pallet.codigo} endereçado",
            )
        }
    }

    fun limparEnderecamento() = _estado.update {
        it.copy(palletEndereco = null, box = null, posicaoEscolhida = null)
    }

    // ------------------------------------------------------------------ leitura

    /**
     * O contador sobe na tela imediatamente e o servidor confirma depois. Não
     * confirmando, a tela desfaz — é o que sustenta a operação durante o salto
     * entre APs sem inventar contagem. Ver docs/API.md §6.
     */
    /** Ponto único de entrada do scanner: a tela ativa decide o que fazer. */
    fun aoLerCodigo(codigo: String) {
        when (_estado.value.tela) {
            Tela.CONFIGURACAO -> lerEnderecoDoServidor(codigo)
            Tela.TRANSFERENCIA -> lerNaTransferencia(codigo)
            Tela.REIMPRESSAO -> reimprimir(codigo)
            Tela.CONSULTA_PALLET -> consultarPallet(codigo)
            Tela.CONFERENCIA_BOX -> conferirBox(codigo)
            Tela.ENDERECAMENTO -> lerNoEnderecamento(codigo)
            Tela.CONFERENCIA -> biparDevolucao(codigo)
            Tela.RETORNO -> lerNoRetorno(codigo)
            Tela.CONFERENCIA_RETORNO -> biparRetorno(codigo)
            Tela.SEPARACAO -> bipar(codigo)
            Tela.ITENS -> lerNaListaDeItens(codigo)
            Tela.ITENS_DEVOLUCAO -> lerNaListaDeItensDevolucao(codigo)
            // Nas demais telas o gatilho não tem o que fazer. Antes tudo caía
            // em `bipar`, que contava a caixa no último item aberto — em
            // silêncio, para quem estava na lista de itens ou no menu.
            else -> Unit
        }
    }

    /** Na lista de itens o gatilho só seleciona pallet; caixa pede o item aberto. */
    private fun lerNaListaDeItens(codigo: String) {
        if (!scanner.aceitar(codigo)) return
        if (ehCodigoDePallet(codigo)) {
            selecionarPallet(codigo)
            return
        }
        feedback.sinalizar(Evento.ITEM_ERRADO)
        _estado.update { it.copy(mensagem = "Abra o item na lista antes de bipar a caixa.", sucesso = null) }
    }

    fun bipar(codigo: String) {
        val e = _estado.value
        val item = e.item ?: return
        if (!scanner.aceitar(codigo)) return

        // Etiqueta de pallet seleciona o pallet em vez de contar como caixa —
        // o separador bipa o pallet e já começa, sem tocar em botão nenhum.
        // Ver docs/TELAS-COLETOR.md §5.1.
        if (ehCodigoDePallet(codigo)) {
            selecionarPallet(codigo)
            return
        }

        // Pallet de devolução que ficou ativo da conferência não serve aqui.
        if (e.palletAtivo == null || e.palletAtivo.startsWith("P-DEV-")) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update {
                it.copy(alerta = Alerta("Sem pallet", "Nenhum pallet de separação selecionado. Bipe um pallet P-RED-.", true))
            }
            return
        }

        // Soma na hora e envia em segundo plano. Falha de rede não desfaz: a
        // leitura fica na fila e vai quando o wifi voltar.
        _estado.update { it.comQtdDoItem(item.id) { qtd -> qtd + 1 } }

        fila.enfileirar(
            LeituraPendente(
                idempotencyKey = UUID.randomUUID().toString(),
                palletCodigo = e.palletAtivo,
                itemId = item.id,
                codigoLido = codigo,
                lidoEm = OffsetDateTime.now().toString(),
                usuarioId = e.usuario?.id ?: 0,
                usuarioNome = e.usuario?.nome.orEmpty(),
            )
        )
    }

    // ------------------------------------------------ repaletizar (transferência)

    /**
     * Abre a tela no passo 1 — a não ser que uma repaletização tenha ficado
     * aberta (a sessão caiu no meio, ou o operador saiu para voltar depois).
     * Zerar aqui perderia a caixa sem resposta e deixaria os cartões mentindo.
     */
    fun abrirTransferencia() = _estado.update {
        val par = it.repaletizacao
        if (par != null) {
            it.copy(
                tela = Tela.TRANSFERENCIA, saidaRepaletizacao = null, mensagem = null,
                sucesso = "Repaletização de ${par.origem.codigo} para ${par.destino.codigo} retomada. " +
                    "Continue bipando as caixas ou toque em Concluir.",
            )
        } else {
            it.copy(
                tela = Tela.TRANSFERENCIA, palletOrigem = null, unirArmado = false,
                cartoesDesatualizados = false, saidaRepaletizacao = null,
                mensagem = null, sucesso = null,
            )
        }
    }

    /**
     * Liga ou desliga "Juntar o pallet inteiro". Com caixa já passada no par
     * aberto não troca: juntar agora deixaria os dois cartões sem imprimir.
     */
    fun armarUniao(armado: Boolean) {
        if (_estado.value.repaletizacaoPorImprimir) {
            _estado.update {
                it.copy(
                    mensagem = "Toque em Concluir antes de trocar o modo: os cartões dos dois pallets precisam sair.",
                    sucesso = null,
                )
            }
            return
        }
        _estado.update {
            it.copy(unirArmado = armado, repaletizacao = null, cartoesDesatualizados = false, mensagem = null, sucesso = null)
        }
    }

    /** "Trocar origem". Com cartões por imprimir, pergunta antes. */
    fun limparOrigem() {
        if (_estado.value.repaletizacaoPorImprimir) {
            _estado.update { it.copy(saidaRepaletizacao = SaidaRepaletizacao.TROCAR_ORIGEM) }
            return
        }
        _estado.update {
            it.copy(palletOrigem = null, repaletizacao = null, cartoesDesatualizados = false, mensagem = null, sucesso = null)
        }
    }

    /**
     * "Trocar destino": só com o par ainda intocado — o destino bipado errado
     * é o engano mais comum, e voltar à origem por ele custava um bipe a mais.
     */
    fun trocarDestino() {
        if (_estado.value.repaletizacaoPorImprimir) return
        _estado.update { it.copy(repaletizacao = null, cartoesDesatualizados = false, mensagem = null, sucesso = null) }
    }

    fun fecharSaidaRepaletizacao() = _estado.update { it.copy(saidaRepaletizacao = null) }

    /**
     * "Sair sem imprimir" do diálogo de saída. Com caixa sem resposta o par não
     * é descartado: ela é reenviada quando o operador voltar à tela, e
     * descartar agora deixaria o sistema sem saber em qual pallet ela está.
     */
    fun sairSemImprimir() {
        val e = _estado.value
        val par = e.repaletizacao ?: return fecharSaidaRepaletizacao()
        val sair = e.saidaRepaletizacao == SaidaRepaletizacao.VOLTAR
        val o = par.origem.codigo
        val d = par.destino.codigo
        if (e.caixaRepaletizacaoSemResposta != null) {
            _estado.update {
                it.copy(
                    saidaRepaletizacao = null,
                    tela = if (sair) Tela.MENU else it.tela,
                    sucesso = null,
                    mensagem = "A repaletização de $o para $d ficou aberta, com uma caixa ainda sem confirmação. " +
                        "Volte em Repaletização quando a rede voltar e toque em Concluir.",
                )
            }
            return
        }
        _estado.update {
            it.copy(
                saidaRepaletizacao = null,
                tela = if (sair) Tela.MENU else it.tela,
                palletOrigem = null, repaletizacao = null, cartoesDesatualizados = false,
                sucesso = null,
                mensagem = if (par.origemEsvaziada) {
                    "$o ficou vazio: descarte o cartão dele. O cartão de $d NÃO foi impresso e mostra a " +
                        "quantidade antiga — use Reimprimir › Cartão."
                } else {
                    "Os cartões de $o e $d NÃO foram impressos e mostram as quantidades antigas. " +
                        "Use Reimprimir › Cartão nos dois."
                },
            )
        }
    }

    /**
     * Um bipe só, vários significados, decididos pelo que já está na tela
     * ([acaoNaTransferencia]).
     *
     * Ligado, "Juntar o pallet inteiro" é a fusão de sempre: origem, destino,
     * todas as caixas passam. Desligado, é caixa a caixa: origem, destino e
     * cada caixa bipada passa uma. O interruptor existe para que dois bipes
     * distraídos não movam carga: juntar pallet é irreversível pelo coletor.
     */
    fun lerNaTransferencia(codigo: String) {
        if (!scanner.aceitar(codigo)) return
        val lido = codigo.trim()
        val e = _estado.value
        val origem = e.palletOrigem

        when (acaoNaTransferencia(e, lido)) {
            AcaoTransferencia.CARREGAR_ORIGEM -> carregarOrigem(lido)
            AcaoTransferencia.PEDIR_PALLET -> avisarNaTransferencia(
                if (origem == null) "Bipe a etiqueta do pallet de ORIGEM (P-RED-)."
                else "Bipe a etiqueta do pallet de DESTINO (P-RED-).",
            )
            AcaoTransferencia.MESMO_PALLET ->
                avisarNaTransferencia("${origem?.codigo} já é a origem. Bipe o pallet de DESTINO.")
            AcaoTransferencia.FUNDIR -> origem?.let { unirNoDestino(it.codigo, lido) }
            AcaoTransferencia.ABRIR_REPALETIZACAO -> origem?.let { abrirRepaletizacao(it.codigo, lido.uppercase()) }
            AcaoTransferencia.PASSAR_CAIXA -> passarCaixa(lido)
            AcaoTransferencia.ORIGEM_ESVAZIADA -> avisarNaTransferencia(
                "${e.repaletizacao?.origem?.codigo} ficou vazio: não há mais caixa a passar. " +
                    "Toque em Concluir para imprimir os cartões.",
            )
            AcaoTransferencia.CONCLUIR_ANTES -> avisarNaTransferencia(
                "Agora só vale caixa. Para trocar de pallet, toque em Concluir antes.",
            )
        }
    }

    private fun avisarNaTransferencia(texto: String) {
        feedback.sinalizar(Evento.ITEM_ERRADO)
        _estado.update { it.copy(mensagem = texto, sucesso = null) }
    }

    fun carregarOrigem(codigo: String) = comCarregamento {
        val p = exigirApi().conteudoDoPallet(codigo.trim().uppercase())
        feedback.sinalizar(Evento.ACEITA)
        _estado.update { it.copy(palletOrigem = p, repaletizacao = null, cartoesDesatualizados = false) }
    }

    /**
     * Segundo bipe da repaletização caixa a caixa: confere o par no servidor
     * sem mover nada. A trava de NF é dele — pallet de outra nota volta
     * recusado, e a tela continua esperando o destino.
     */
    private fun abrirRepaletizacao(origem: String, destino: String) = comCarregamento {
        // A mesma checagem da fusão: leitura de separação ainda na fila deste
        // coletor ficaria fora da conta dos dois pallets.
        if (!palletLivreDaFila(origem) || !palletLivreDaFila(destino)) return@comCarregamento
        val r = try {
            exigirApi().abrirRepaletizacao(origem, destino)
        } catch (e: ErroApi) {
            if (e.codigo == "SESSAO_EXPIRADA") throw e
            recusarPar(e)
            return@comCarregamento
        }
        feedback.sinalizar(Evento.ACEITA)
        _estado.update {
            // Trocou a origem enquanto a resposta vinha: este par não vale mais.
            if (it.palletOrigem?.codigo?.equals(origem, ignoreCase = true) != true || it.unirArmado) {
                it
            } else {
                it.copy(
                    palletOrigem = r.origem, repaletizacao = r, cartoesDesatualizados = false,
                    // Par deixado aberto antes ("Sair sem imprimir", app fechado):
                    // o servidor devolve as caixas daquela vez e a tela abre presa
                    // ao Concluir. Sem dizer o porquê, o interruptor apagado e o
                    // "passadas" já cheio pareciam defeito.
                    sucesso = if (r.movidas > 0) {
                        "Você já tinha passado ${r.movidas} caixa(s) de ${r.origem.codigo} para " +
                            "${r.destino.codigo} sem concluir: os cartões ainda não saíram. " +
                            "Continue bipando ou toque em Concluir."
                    } else {
                        "Bipe as caixas que passam de ${r.origem.codigo} para ${r.destino.codigo}."
                    },
                )
            }
        }
    }

    /** Recusa do par no diálogo bloqueante, como a recusa de pallet na separação. */
    private fun recusarPar(e: ErroApi) {
        feedback.sinalizar(Evento.ITEM_ERRADO)
        val titulo = when (e.codigo) {
            "PALLETS_DE_NOTAS_DIFERENTES" -> "Pallets de notas diferentes"
            "PALLET_EM_USO_POR_OUTRO" -> "Pallet em uso"
            "PALLET_VAZIO", "PALLET_ENCERRADO" -> "Pallet vazio"
            "PALLET_NAO_ENCONTRADO" -> "Pallet não encontrado"
            "PALLET_JA_EXPEDIDO" -> "Pallet já expedido"
            "PALLET_JA_FUNDIDO" -> "Pallet já juntado"
            "PALLET_EM_RETORNO" -> "Pallet em retorno"
            "PALLET_DE_DEVOLUCAO" -> "Pallet de devolução"
            "MESMO_PALLET" -> "Mesmo pallet"
            else -> "Não foi possível abrir"
        }
        // Servidor anterior à repaletização não tem a rota e responde 404.
        val detalhe = if (e.codigo == "ERRO_HTTP_404") {
            "Este servidor ainda não tem a repaletização caixa a caixa. Ligue \"Juntar o pallet inteiro\" " +
                "ou peça a atualização do servidor."
        } else {
            e.mensagem
        }
        _estado.update { it.copy(alerta = Alerta(titulo, detalhe, true)) }
    }

    /**
     * Uma operação de repaletização por vez, na ordem do bipe — o mesmo motivo
     * do retorno: sem soma otimista, respostas fora de ordem fariam os totais
     * recuarem, e desfazer ou concluir com caixa no ar mediria um par que
     * ainda ia mudar.
     */
    private val envioRepaletizacao = Mutex()

    /**
     * Uma chave por caixa, criada no bipe. O reenvio é sempre da própria caixa
     * sem resposta, com a chave dela — nunca de um bipe novo que "parece" o
     * mesmo: todas as caixas de um produto têm o mesmo código, e reaproveitar a
     * chave faria a caixa seguinte não passar.
     */
    private fun passarCaixa(codigo: String) {
        val par = _estado.value.repaletizacao ?: return
        val caixa = CaixaRepaletizacaoSemResposta(
            origem = par.origem.codigo,
            destino = par.destino.codigo,
            codigoLido = codigo,
            idempotencyKey = UUID.randomUUID().toString(),
            lidoEm = OffsetDateTime.now().toString(),
        )

        viewModelScope.launch {
            envioRepaletizacao.withLock {
                _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
                try {
                    // A caixa anterior sem resposta vai primeiro. Enquanto ela
                    // não se resolve, este bipe não é enviado: se fosse, e o
                    // operador rebipasse a anterior, ela passaria duas vezes.
                    if (!resolverCaixaSemResposta()) {
                        if (api != null) avisarCaixaSemResposta(naoPassou = codigo)
                        return@withLock
                    }
                    // Bipe que esperou na fila enquanto o par era concluído:
                    // enviá-lo passaria a caixa depois dos cartões impressos.
                    if (_estado.value.repaletizacao?.mesmoPar(caixa.origem, caixa.destino) != true) {
                        feedback.sinalizar(Evento.ITEM_ERRADO)
                        _estado.update {
                            it.copy(
                                mensagem = "A leitura $codigo NÃO passou: a repaletização de ${caixa.origem} " +
                                    "para ${caixa.destino} já tinha sido concluída.",
                            )
                        }
                        return@withLock
                    }
                    if (!enviarCaixa(caixa) && api != null) avisarCaixaSemResposta(naoPassou = null)
                } finally {
                    _estado.update { it.copy(carregando = false) }
                }
            }
        }
    }

    /** Reenvia a caixa sem resposta, se houver. true: não há mais pendência. */
    private suspend fun resolverCaixaSemResposta(): Boolean {
        val pendente = _estado.value.caixaRepaletizacaoSemResposta ?: return true
        return enviarCaixa(pendente)
    }

    /**
     * Envia uma caixa e aplica a resposta. false quando a resposta não veio
     * (rede, erro do servidor, sessão): a caixa fica guardada para o reenvio,
     * com a mesma chave.
     */
    private suspend fun enviarCaixa(caixa: CaixaRepaletizacaoSemResposta): Boolean {
        try {
            val r = exigirApi().repaletizarCaixa(
                idempotencyKey = caixa.idempotencyKey,
                origem = caixa.origem,
                destino = caixa.destino,
                codigoLido = caixa.codigoLido,
                lidoEm = caixa.lidoEm,
            )
            // Origem esvaziada tem o som de "acabou": não há mais o que bipar.
            feedback.sinalizar(if (r.origemEsvaziada) Evento.ITEM_COMPLETO else Evento.ACEITA)
            _estado.update { st ->
                val aberto = st.repaletizacao?.mesmoPar(r) == true
                st.copy(
                    caixaRepaletizacaoSemResposta = null,
                    repaletizacao = if (aberto) r else st.repaletizacao,
                    palletOrigem = if (aberto) r.origem else st.palletOrigem,
                    cartoesDesatualizados = st.cartoesDesatualizados || aberto,
                )
            }
            return true
        } catch (e: ErroApi) {
            when {
                // Erro do próprio servidor não diz se a caixa passou: guarda a
                // chave, como na falta de rede.
                e.codigo == "ERRO_INTERNO" || e.codigo.startsWith("ERRO_HTTP_5") -> {
                    _estado.update { it.copy(caixaRepaletizacaoSemResposta = caixa) }
                    return false
                }
                e.codigo == "SESSAO_EXPIRADA" -> {
                    // Guardada: o reenvio sai no primeiro bipe depois do login.
                    api = null
                    _estado.update {
                        it.copy(caixaRepaletizacaoSemResposta = caixa, tela = Tela.LOGIN, mensagem = e.mensagem)
                    }
                    return false
                }
                else -> {
                    // Recusa de negócio é resposta: a caixa não passou, e a
                    // chave não serve mais para reenvio.
                    _estado.update { it.copy(caixaRepaletizacaoSemResposta = null) }
                    tratarRecusaDeLeitura(e, caixa.codigoLido)
                    // Pallet que mudou de situação por outro caminho (esvaziado,
                    // expedido, em retorno): a tela passa a mostrar o que vale.
                    if (e.codigo.startsWith("PALLET_")) recarregarPar(caixa.origem, caixa.destino)
                    return true
                }
            }
        } catch (e: Exception) {
            _estado.update { it.copy(caixaRepaletizacaoSemResposta = caixa) }
            return false
        }
    }

    private fun avisarCaixaSemResposta(naoPassou: String?) {
        val p = _estado.value.caixaRepaletizacaoSemResposta ?: return
        feedback.sinalizar(Evento.ITEM_ERRADO)
        _estado.update {
            it.copy(
                alerta = Alerta(
                    "Sem resposta do servidor",
                    "A caixa ${p.codigoLido} (de ${p.origem} para ${p.destino}) ainda não foi confirmada. Ela é " +
                        "reenviada sozinha antes do próximo bipe, sem passar duas vezes: NÃO bipe ela de novo." +
                        (naoPassou?.let { "\n\nEsta leitura ($it) NÃO passou: bipe a caixa de novo quando a rede voltar." } ?: ""),
                    true,
                )
            )
        }
    }

    /** "Reenviar agora", na faixa da caixa sem resposta. */
    fun reenviarCaixaSemResposta() {
        viewModelScope.launch {
            envioRepaletizacao.withLock {
                _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
                try {
                    if (!resolverCaixaSemResposta() && api != null) avisarCaixaSemResposta(naoPassou = null)
                } finally {
                    _estado.update { it.copy(carregando = false) }
                }
            }
        }
    }

    /**
     * "Desfazer última": a caixa bipada por engano volta para a origem.
     *
     * Desfazer não tem chave: repetido depois de ficar sem resposta, desfaria
     * outra caixa. Por isso a falta de resposta aqui não reenvia nada — avisa,
     * e a tela recarrega os totais para o operador ver se a caixa voltou antes
     * de tocar de novo.
     */
    fun desfazerRepaletizacao() {
        val par = _estado.value.repaletizacao ?: return
        val o = par.origem.codigo
        val d = par.destino.codigo
        comCarregamento {
            envioRepaletizacao.withLock {
                // A caixa sem resposta vai antes: desfazer "a última" com ela no
                // ar desfaria a anterior, e o reenvio depois traria de volta
                // justamente a que devia sair.
                if (!resolverCaixaSemResposta()) {
                    if (api != null) avisarCaixaSemResposta(naoPassou = null)
                    return@withLock
                }
                val r = try {
                    exigirApi().desfazerRepaletizacao(o, d)
                } catch (e: ErroApi) {
                    if (e.codigo == "SESSAO_EXPIRADA") throw e
                    feedback.sinalizar(Evento.ITEM_ERRADO)
                    val titulo = when (e.codigo) {
                        "NADA_A_DESFAZER" -> "Nada a desfazer"
                        "CAIXA_JA_MOVIDA" -> "Caixa já movida"
                        else -> "Não foi possível desfazer"
                    }
                    _estado.update { it.copy(alerta = Alerta(titulo, e.mensagem, true)) }
                    return@withLock
                } catch (e: Exception) {
                    feedback.sinalizar(Evento.ITEM_ERRADO)
                    _estado.update {
                        it.copy(
                            alerta = Alerta(
                                "Sem resposta do servidor",
                                "A última caixa pode ter voltado ou não para $o. Confira os totais na tela " +
                                    "antes de tocar em Desfazer de novo.",
                                true,
                            )
                        )
                    }
                    recarregarPar(o, d)
                    return@withLock
                }
                feedback.sinalizar(Evento.ACEITA)
                _estado.update { st ->
                    if (st.repaletizacao?.mesmoPar(r) != true) {
                        st
                    } else {
                        st.copy(
                            // Sem o destaque de "última passada": esta voltou.
                            repaletizacao = r.copy(produtoCodigo = null, produtoDescricao = null),
                            palletOrigem = r.origem,
                            cartoesDesatualizados = true,
                            sucesso = "Desfeito: 1 caixa de ${r.produtoDescricao ?: r.produtoCodigo ?: "produto"} " +
                                "voltou para $o.",
                        )
                    }
                }
            }
        }
    }

    /**
     * Retrato novo do par depois de uma operação sem resposta ou recusada.
     * /abrir devolve os dois e o total passado; se ele recusar (a origem
     * esvaziou, por exemplo), os dois conteúdos avulsos bastam para os totais.
     */
    private suspend fun recarregarPar(o: String, d: String) {
        val cliente = api ?: return
        val r = runCatching { cliente.abrirRepaletizacao(o, d) }.getOrNull()
        if (r != null) {
            _estado.update { st ->
                if (st.repaletizacao?.mesmoPar(r) != true) st
                else st.copy(repaletizacao = r.copy(produtoCodigo = null, produtoDescricao = null), palletOrigem = r.origem)
            }
            return
        }
        val origem = runCatching { cliente.conteudoDoPallet(o) }.getOrNull() ?: return
        val destino = runCatching { cliente.conteudoDoPallet(d) }.getOrNull() ?: return
        _estado.update { st ->
            val par = st.repaletizacao
            if (par == null || !par.mesmoPar(o, d)) {
                st
            } else {
                st.copy(
                    repaletizacao = par.copy(
                        origem = origem, destino = destino,
                        origemEsvaziada = origem.status == "ESVAZIADO",
                        produtoCodigo = null, produtoDescricao = null,
                    ),
                    palletOrigem = origem,
                )
            }
        }
    }

    /**
     * "Concluir": imprime o cartão do destino e o da origem (se ainda tiver
     * caixa) e volta ao passo 1, com o interruptor como estava. Os dois
     * cartões mudaram de conteúdo — cartão velho mente sobre a quantidade.
     */
    fun concluirRepaletizacao() {
        val e = _estado.value
        val par = e.repaletizacao ?: return
        _estado.update { it.copy(saidaRepaletizacao = null) }
        // Nada passou nem voltou: os cartões continuam certos, e imprimir de
        // novo só gastaria etiqueta.
        if (!e.repaletizacaoPorImprimir) {
            _estado.update {
                it.copy(
                    palletOrigem = null, repaletizacao = null, cartoesDesatualizados = false, mensagem = null,
                    sucesso = "Nenhuma caixa passada de ${par.origem.codigo} para ${par.destino.codigo}: " +
                        "nada a imprimir.",
                )
            }
            return
        }
        comCarregamento {
            envioRepaletizacao.withLock {
                // A caixa sem resposta vai antes: concluir sem ela imprimiria
                // cartões com a conta errada.
                if (!resolverCaixaSemResposta()) {
                    if (api != null) avisarCaixaSemResposta(naoPassou = null)
                    return@withLock
                }
                val fim = try {
                    exigirApi().concluirRepaletizacao(par.origem.codigo, par.destino.codigo)
                } catch (e: ErroApi) {
                    throw e
                } catch (e: Exception) {
                    // Pode ter impresso sem a resposta chegar. Tocar de novo é
                    // seguro: no máximo sai um cartão repetido.
                    feedback.sinalizar(Evento.ITEM_ERRADO)
                    _estado.update {
                        it.copy(
                            mensagem = "Sem resposta do servidor: os cartões podem ter saído ou não. Confira a " +
                                "impressora e, se não saíram, toque em Concluir de novo.",
                        )
                    }
                    return@withLock
                }
                val (ok, falhas) = textosDaConclusao(fim)
                feedback.sinalizar(if (falhas == null) Evento.ITEM_COMPLETO else Evento.ITEM_ERRADO)
                _estado.update {
                    it.copy(
                        palletOrigem = null, repaletizacao = null, cartoesDesatualizados = false,
                        sucesso = ok, mensagem = falhas,
                    )
                }
            }
        }
    }

    fun unirNoDestino(origem: String, destino: String) = comCarregamento {
        if (origem.equals(destino.trim(), ignoreCase = true)) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update { it.copy(mensagem = "Origem e destino são o mesmo pallet.") }
            return@comCarregamento
        }
        if (!palletLivreDaFila(origem) || !palletLivreDaFila(destino.trim().uppercase())) return@comCarregamento
        val r = exigirApi().fundirPallets(origem, destino.trim().uppercase())
        feedback.sinalizar(Evento.ITEM_COMPLETO)
        _estado.update {
            it.copy(
                palletOrigem = null,
                unirArmado = false,
                sucesso = "${r.qtdCaixas} caixa(s) de ${r.palletOrigem} passaram para " +
                    "${r.palletDestino}. Descarte o cartão de ${r.palletOrigem}.",
            )
        }
    }

    // ----------------------------------------------------------- reimpressão

    fun abrirReimpressao() = _estado.update {
        it.copy(tela = Tela.REIMPRESSAO, mensagem = null, sucesso = null)
    }

    fun escolherTipoImpressao(tipo: String) =
        _estado.update { it.copy(tipoImpressao = tipo, mensagem = null, sucesso = null) }

    fun reimprimir(codigo: String) = comCarregamento {
        if (!scanner.aceitar(codigo)) return@comCarregamento
        val alvo = codigo.trim().uppercase()
        if (!ehCodigoDePallet(alvo)) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update { it.copy(mensagem = "Informe um código de pallet (P-RED- ou P-DEV-).") }
            return@comCarregamento
        }
        // Confirma que existe antes de mandar imprimir: a porta 9100 aceita o
        // trabalho sem conferir nada, e etiqueta de pallet inexistente sai igual.
        val p = exigirApi().conteudoDoPallet(alvo)
        val cartao = _estado.value.tipoImpressao == "CARTAO"
        val r = if (cartao) exigirApi().imprimirCartao(alvo) else exigirApi().reimprimirPallet(alvo)
        val situacao = r["impressao"]
        val oQue = if (cartao) "Cartão" else "Etiqueta"
        feedback.sinalizar(if (situacao == "ENVIADA") Evento.ACEITA else Evento.ITEM_ERRADO)
        _estado.update {
            it.copy(
                sucesso = if (situacao == "ENVIADA") "$oQue de ${p.codigo} enviado à impressora"
                          else null,
                mensagem = when (situacao) {
                    "ENVIADA" -> null
                    "SEM_IMPRESSORA" -> "Nenhuma impressora cadastrada para o seu usuário."
                    // O cartão lista os itens da NF: pallet sem nota vinculada
                    // ainda não tem o que imprimir nele.
                    "SEM_CONTEUDO" -> "${p.codigo} ainda não tem nota vinculada — " +
                        "o cartão só sai depois da primeira caixa bipada."
                    else -> "A impressora não aceitou o trabalho. Verifique se está ligada."
                },
            )
        }
    }

    // --------------------------------------------------------- consultar pallet

    /**
     * Abre vazia. A consulta anterior podia estar velha — o pallet endereçado,
     * juntado ou expedido depois dela — e mostrá-la como atual mandaria o
     * operador ao lugar errado.
     */
    fun abrirConsultaPallet() = _estado.update {
        it.copy(tela = Tela.CONSULTA_PALLET, consultaPallet = null, mensagem = null, sucesso = null)
    }

    /** Última etiqueta pedida: a resposta de um bipe anterior que chegue depois não troca a tela. */
    private var consultaPedida: String? = null

    /**
     * Cada etiqueta bipada troca a consulta. Falha apaga a anterior: com ela na
     * tela, o aviso de "não encontrado" ficaria em cima dos dados de outro
     * pallet, e o operador leria um pelo outro.
     */
    fun consultarPallet(codigo: String) {
        if (!scanner.aceitar(codigo)) return
        val alvo = codigo.trim().uppercase()
        if (!ehCodigoDePallet(alvo)) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update { it.copy(mensagem = "Bipe a etiqueta de um pallet (P-RED- ou P-DEV-).", sucesso = null) }
            return
        }
        consultaPedida = alvo
        // Quem já saiu da tela, ou bipou outra etiqueta, não quer esta resposta.
        fun vale() = consultaPedida == alvo && _estado.value.tela == Tela.CONSULTA_PALLET
        comCarregamento {
            val c = try {
                exigirApi().consultarPallet(alvo)
            } catch (e: ErroApi) {
                if (e.codigo == "SESSAO_EXPIRADA") throw e
                if (vale()) {
                    feedback.sinalizar(Evento.ITEM_ERRADO)
                    _estado.update {
                        it.copy(consultaPallet = null, mensagem = mensagemDaConsultaRecusada(e.codigo, e.mensagem))
                    }
                }
                return@comCarregamento
            } catch (e: Exception) {
                if (!vale()) return@comCarregamento
                feedback.sinalizar(Evento.ITEM_ERRADO)
                _estado.update { it.copy(consultaPallet = null) }
                // O aviso de rede é o de sempre, do comCarregamento.
                throw e
            }
            if (!vale()) return@comCarregamento
            feedback.sinalizar(Evento.ACEITA)
            _estado.update { it.copy(consultaPallet = c) }
        }
    }

    // ------------------------------------------------------ conferência de box

    /** Abre vazia, pelo mesmo motivo da consulta de pallet: um box de antes pode estar velho. */
    fun abrirConferenciaBox() = _estado.update {
        it.copy(
            tela = Tela.CONFERENCIA_BOX, conferenciaBox = null, conferenciaBoxPallet = null,
            mensagem = null, sucesso = null,
        )
    }

    /** Última etiqueta pedida: a resposta de um bipe anterior que chegue depois não troca a tela. */
    private var boxPedido: String? = null

    /**
     * Etiqueta do box mostra o que está em cada nível; etiqueta de pallet abre
     * o box onde ele está, com ele marcado. Só leitura. Falha apaga o box
     * anterior: o aviso em cima dos níveis de outro box seria lido por eles.
     */
    fun conferirBox(codigo: String) {
        if (!scanner.aceitar(codigo)) return
        val lido = codigo.trim().uppercase()
        if (lido.isEmpty()) return
        boxPedido = lido
        fun vale() = boxPedido == lido && _estado.value.tela == Tela.CONFERENCIA_BOX
        fun recusar(texto: String) {
            if (!vale()) return
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update { it.copy(conferenciaBox = null, conferenciaBoxPallet = null, mensagem = texto) }
        }
        comCarregamento {
            try {
                var pallet: String? = null
                val codigoBox = if (ehCodigoDePallet(lido)) {
                    val c = exigirApi().consultarPallet(lido)
                    val p = c.bipado
                    pallet = c.codigo
                    // Servidor anterior à 0.7.2 não manda o box separado; o endereço tem o mesmo dado.
                    p?.box ?: boxDoEndereco(p?.endereco) ?: run {
                        recusar("Pallet ${c.codigo} não está em box nenhum: ${p?.let { localDoPallet(it) } ?: "sem endereço"}.")
                        return@comCarregamento
                    }
                } else {
                    lido
                }
                val box = exigirApi().box(codigoBox)
                if (!vale()) return@comCarregamento
                feedback.sinalizar(Evento.ACEITA)
                _estado.update { it.copy(conferenciaBox = box, conferenciaBoxPallet = pallet, mensagem = null) }
            } catch (e: ErroApi) {
                if (e.codigo == "SESSAO_EXPIRADA") throw e
                recusar(mensagemDaConsultaRecusada(e.codigo, e.mensagem))
            } catch (e: Exception) {
                if (!vale()) return@comCarregamento
                feedback.sinalizar(Evento.ITEM_ERRADO)
                _estado.update { it.copy(conferenciaBox = null, conferenciaBoxPallet = null) }
                // O aviso de rede é o de sempre, do comCarregamento.
                throw e
            }
        }
    }

    // -------------------------------------------------------------- devolução

    /**
     * "Conferir" do menu: retornos e devoluções na mesma lista.
     *
     * Retorno é da 0.6.0. Falha nele não derruba a devolução — servidor mais
     * antigo responde 404, e o operador precisa continuar conferindo a NF do
     * cliente como antes; nesse caso a seção de retornos só não aparece.
     */
    fun abrirDevolucoes() = comCarregamento {
        val lista = exigirApi().devolucoesParaConferir()
        val retornos = runCatching { exigirApi().retornosEmConferencia() }
        // Só o 404 é servidor antigo. Rede ou tempo esgotado não pode sumir
        // com a seção como se não houvesse retorno para conferir.
        val semRota = (retornos.exceptionOrNull() as? ErroApi)?.codigo == "ERRO_HTTP_404"
        val falhou = retornos.isFailure && !semRota
        _estado.update {
            it.copy(
                tela = Tela.DEVOLUCOES, devolucoes = lista,
                devolucao = null, itensDevolucao = emptyList(), itemDevolucao = null,
                retornos = retornos.getOrNull() ?: if (semRota) emptyList() else it.retornos,
                retornosIndisponiveis = semRota,
                mensagem = if (falhou) "Não foi possível carregar os retornos. Volte a esta tela para tentar de novo." else null,
                retorno = null, palletsRetorno = emptyList(), palletRetorno = null,
            )
        }
    }

    fun abrirDevolucao(d: Devolucao) = comCarregamento {
        val itens = exigirApi().itensDaDevolucao(d.id)
        _estado.update {
            it.copy(
                tela = Tela.ITENS_DEVOLUCAO, devolucao = d, itensDevolucao = itens, itemDevolucao = null,
                // Outra NF, outro pallet: o P-DEV da anterior não segue aberto.
                palletDevolucao = it.palletDevolucao.takeIf { _ -> it.devolucao?.id == d.id },
            )
        }
    }

    fun abrirItemDevolucao(item: ItemDevolucao) =
        // Na sobra não há tela de conferência: o item é só o que já entrou, e
        // somar mais caixas é o diálogo da própria lista.
        if (_estado.value.devolucao?.sobra == true) Unit
        else _estado.update {
            it.copy(tela = Tela.CONFERENCIA, itemDevolucao = item, mensagem = null, sucesso = null)
        }

    /**
     * Bipe conta caixa fechada; digitação conta unidade avulsa.
     *
     * Diferente da separação, aqui não há soma otimista na tela: a devolução
     * chega em unidades e uma contagem provisória errada viraria divergência
     * contra a NF do cliente. O número que aparece é sempre o do servidor.
     */
    fun biparDevolucao(codigo: String) {
        val item = _estado.value.itemDevolucao ?: return
        if (!scanner.aceitar(codigo)) return
        if (escolherPalletDevolucao(codigo)) return
        if (!exigirPalletDevolucao()) return
        registrarConferencia(item.id, "BIPE", codigo, null)
    }

    /**
     * Na devolução o gatilho, aqui na lista, só serve para escolher o P-DEV:
     * a caixa é bipada dentro do item, contra a quantidade da NF. Na
     * conferência de estoque não há NF nem item esperado — o separador bipa
     * qualquer caixa e ela entra como sobra, uma caixa por bipe.
     */
    private fun lerNaListaDeItensDevolucao(codigo: String) {
        if (!scanner.aceitar(codigo)) return
        if (escolherPalletDevolucao(codigo)) return
        if (_estado.value.devolucao?.sobra == true) {
            registrarSobra(codigoLido = codigo, itemId = null, caixas = 1)
            return
        }
        feedback.sinalizar(Evento.ITEM_ERRADO)
        _estado.update { it.copy(mensagem = "Abra o item na lista antes de bipar a caixa.", sucesso = null) }
    }

    /** Várias caixas do mesmo produto: digita em vez de bipar uma a uma. */
    fun somarCaixasSobra(item: ItemDevolucao, caixas: Int) {
        if (caixas < 1) return
        registrarSobra(codigoLido = null, itemId = item.id, caixas = caixas)
    }

    private fun registrarSobra(codigoLido: String?, itemId: Long?, caixas: Int) = viewModelScope.launch {
        val d = _estado.value.devolucao ?: return@launch
        if (!exigirPalletDevolucao()) return@launch
        _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
        try {
            val itens = exigirApi().biparSobra(
                devolucaoId = d.id,
                idempotencyKey = UUID.randomUUID().toString(),
                codigoLido = codigoLido,
                itemId = itemId,
                caixas = caixas,
                palletCodigo = _estado.value.palletDevolucao,
            )
            feedback.sinalizar(Evento.ACEITA)
            _estado.update {
                it.copy(
                    itensDevolucao = itens,
                    sucesso = if (caixas == 1) "1 caixa na sobra" else "$caixas caixas na sobra",
                )
            }
        } catch (e: ErroApi) {
            // Mesma regra da conferência: pallet recusado não segue escolhido.
            if (e.codigo in ERROS_DE_PALLET) {
                _estado.update { it.copy(palletDevolucao = null) }
                tratarRecusaDeLeitura(e, "")
            } else {
                tratarRecusaDeLeitura(e, codigoLido.orEmpty())
            }
        } catch (e: Exception) {
            _estado.update { it.copy(mensagem = e.message ?: "Falha ao registrar") }
        } finally {
            _estado.update { it.copy(carregando = false) }
        }
    }

    /**
     * Etiqueta de pallet na devolução; devolve true se era pallet. Apaga o
     * alerta junto: o modal "Pallet primeiro" ficava por cima depois de o
     * operador fazer exatamente o que ele mandou.
     */
    private fun escolherPalletDevolucao(codigo: String): Boolean {
        if (!ehCodigoDePallet(codigo)) return false
        // Só pallet de devolução: o de separação ficaria ativo e seria
        // mandado junto das unidades devolvidas.
        if (!codigo.trim().uppercase().startsWith("P-DEV-")) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update {
                it.copy(alerta = Alerta("Pallet de separação", "A devolução vai em pallet P-DEV-. Bipe um pallet de devolução.", true))
            }
            return true
        }
        _estado.update { it.copy(palletDevolucao = codigo.trim().uppercase(), alerta = null, mensagem = null) }
        feedback.sinalizar(Evento.ACEITA)
        return true
    }

    fun digitarUnidades(quantidade: Int) {
        val item = _estado.value.itemDevolucao ?: return
        if (quantidade < 1) return
        if (!exigirPalletDevolucao()) return
        registrarConferencia(item.id, "DIGITADO", null, quantidade)
    }

    /**
     * Caixa sem pallet não aparece no estoque: a leitura ficava sem pallet e o
     * P-DEV endereçado depois mostrava zero caixas. O pallet vem antes.
     */
    private fun exigirPalletDevolucao(): Boolean {
        if (_estado.value.palletDevolucao != null) return true
        feedback.sinalizar(Evento.ITEM_ERRADO)
        _estado.update {
            it.copy(alerta = Alerta("Pallet primeiro", "Bipe a etiqueta do pallet de devolução (P-DEV-) antes das caixas.", true))
        }
        return false
    }

    private fun registrarConferencia(
        itemId: Long,
        modo: String,
        codigo: String?,
        qtdUn: Int?,
    ) = viewModelScope.launch {
        _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
        try {
            val r = exigirApi().conferirDevolucao(
                idempotencyKey = UUID.randomUUID().toString(),
                itemDevolucaoId = itemId,
                modo = modo,
                codigoLido = codigo,
                qtdUn = qtdUn,
                palletCodigo = _estado.value.palletDevolucao,
                lidoEm = OffsetDateTime.now().toString(),
            )
            feedback.sinalizar(if (r.completo) Evento.ITEM_COMPLETO else Evento.ACEITA)
            aplicarConferencia(itemId, r.qtdConferidaUn)
        } catch (e: ErroApi) {
            // O pallet recusado continuaria selecionado e recusaria toda caixa
            // seguinte; e o alerta tem de falar do pallet, não da caixa lida.
            if (e.codigo in ERROS_DE_PALLET) {
                _estado.update { it.copy(palletDevolucao = null) }
                tratarRecusaDeLeitura(e, "")
            } else {
                tratarRecusaDeLeitura(e, codigo.orEmpty())
            }
        } catch (e: Exception) {
            _estado.update { it.copy(mensagem = e.message ?: "Falha ao registrar") }
        } finally {
            _estado.update { it.copy(carregando = false) }
        }
    }

    private fun aplicarConferencia(itemId: Long, conferida: Int) = _estado.update { st ->
        val novos = st.itensDevolucao.map {
            if (it.id == itemId) it.copy(qtdConferidaUn = conferida, status = "EM_CONFERENCIA") else it
        }
        st.copy(
            itensDevolucao = novos,
            itemDevolucao = novos.firstOrNull { it.id == itemId } ?: st.itemDevolucao,
        )
    }

    /**
     * Cadeado da conferência. Fecha direto quando bate com a NF; faltando
     * unidade, exige justificativa — cliente que devolve menos do que declarou
     * é informação que precisa sobreviver até o relatório.
     */
    fun fecharItemDevolucao(justificativa: String? = null) = comCarregamento {
        val item = _estado.value.itemDevolucao ?: return@comCarregamento
        val r = exigirApi().fecharItemDevolucao(item.id, justificativa)
        feedback.sinalizar(Evento.ITEM_COMPLETO)

        val itens = _estado.value.itensDevolucao.map { if (it.id == r.id) r else it }
        val faltam = itens.count { !it.fechado }
        _estado.update {
            it.copy(
                itensDevolucao = itens,
                itemDevolucao = r,
                tela = Tela.ITENS_DEVOLUCAO,
                sucesso = if (faltam == 0) "Conferência encerrada — baixe a NF na retaguarda"
                          else "Item conferido · faltam $faltam",
            )
        }
    }

    // ---------------------------------------------------------------- retorno

    /**
     * Leitura de retorno que foi e não voltou resposta: pode ter contado ou
     * não. Uma só — enquanto ela existe, bipe novo não é enviado — e é
     * reenviada com a mesma chave antes da próxima operação.
     */
    private var leituraRetornoSemResposta: LeituraRetornoSemResposta? = null

    /**
     * Cresce ao fim de cada operação de retorno: um retrato do servidor tirado
     * antes dela está velho e não pode ser aplicado depois.
     */
    private var versaoRetorno = 0L

    /**
     * Uma operação de retorno por vez, na ordem do bipe.
     *
     * Sem soma otimista na tela, duas leituras no ar ao mesmo tempo podiam
     * voltar fora de ordem — a resposta do 1º bipe chegando depois da do 2º
     * fazia o contador recuar. E fechar ou desfazer com leitura ainda no ar
     * mediria um pallet que ainda ia mudar.
     */
    private val envioRetorno = Mutex()

    fun abrirRetorno(r: RetornoResumo) = comCarregamento {
        val d = exigirApi().retorno(r.id)
        _estado.update {
            it.copy(
                tela = Tela.RETORNO,
                retorno = d.retorno,
                palletsRetorno = d.pallets,
                palletRetorno = null,
                produtoLidoRetorno = null,
            )
        }
    }

    /**
     * Abre o pallet mesmo fechado, só para ver: reabrir é na retaguarda, e a
     * leitura continua barrada — aqui e no servidor.
     *
     * Pallet que nem voltou também passa por aqui: toca na lista, toca no
     * cadeado e fecha com 0 caixas e a justificativa, sem bipe nenhum.
     */
    fun abrirPalletRetorno(p: RetornoPallet, aviso: String? = null) {
        _estado.update {
            it.copy(
                tela = Tela.CONFERENCIA_RETORNO,
                palletRetorno = p,
                produtoLidoRetorno = null,
                mensagem = if (p.fechado) MSG_PALLET_FECHADO else null,
                sucesso = if (p.fechado) null else aviso,
            )
        }
        atualizarRetornoEmSilencio()
    }

    /** Na lista de pallets do retorno o gatilho abre o pallet bipado. */
    private fun lerNoRetorno(codigo: String) {
        if (!scanner.aceitar(codigo)) return
        if (!ehCodigoDePallet(codigo)) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update {
                it.copy(mensagem = "Bipe a etiqueta do pallet (ou toque nele) antes das caixas.", sucesso = null)
            }
            return
        }
        val p = palletDoRetorno(codigo) ?: run { avisarPalletForaDoRetorno(codigo); return }
        feedback.sinalizar(Evento.ACEITA)
        abrirPalletRetorno(p)
    }

    /**
     * Bipe na conferência do pallet.
     *
     * Etiqueta de outro pallet do mesmo retorno troca de pallet sem voltar à
     * lista — o operador desce um palete e já bipa o próximo. Caixa vai ao
     * servidor, e o número da tela é sempre o dele: como na devolução, sem
     * soma otimista, porque a falta aqui vira extravio na tratativa.
     */
    fun biparRetorno(codigo: String) {
        val e = _estado.value
        val retorno = e.retorno ?: return
        val pallet = e.palletRetorno ?: return
        if (!scanner.aceitar(codigo)) return
        val lido = codigo.trim()

        if (ehCodigoDePallet(lido)) {
            val outro = palletDoRetorno(lido) ?: run { avisarPalletForaDoRetorno(lido); return }
            feedback.sinalizar(Evento.ACEITA)
            if (outro.id != pallet.id) abrirPalletRetorno(outro, aviso = "Conferindo ${outro.palletCodigo}")
            return
        }

        if (pallet.fechado) {
            feedback.sinalizar(Evento.ITEM_ERRADO)
            _estado.update {
                it.copy(
                    alerta = Alerta(
                        "Pallet já conferido",
                        "${pallet.palletCodigo} está fechado e não aceita leitura. Reabrir é na retaguarda.",
                        true,
                    )
                )
            }
            return
        }
        registrarLeituraRetorno(retorno.id, pallet, lido)
    }

    private fun palletDoRetorno(codigo: String): RetornoPallet? {
        val alvo = codigo.trim()
        return _estado.value.palletsRetorno.firstOrNull { it.palletCodigo.equals(alvo, ignoreCase = true) }
    }

    private fun avisarPalletForaDoRetorno(codigo: String) {
        feedback.sinalizar(Evento.ITEM_ERRADO)
        val nf = _estado.value.retorno?.notaNumero.orEmpty()
        _estado.update {
            it.copy(
                alerta = Alerta(
                    "Pallet fora deste retorno",
                    "${codigo.trim().uppercase()} não está entre os pallets que voltaram da NF $nf. Confira a etiqueta.",
                    true,
                )
            )
        }
    }

    /**
     * Uma chave por caixa, criada no bipe. O reenvio é sempre da própria
     * leitura sem resposta, com a chave dela — nunca de um bipe novo que
     * "parece" o mesmo: todas as caixas de um produto têm o mesmo código, e
     * reaproveitar a chave faria a caixa seguinte não contar.
     */
    private fun registrarLeituraRetorno(retornoId: Long, pallet: RetornoPallet, codigo: String) {
        val leitura = LeituraRetornoSemResposta(
            retornoId = retornoId,
            retornoPalletId = pallet.id,
            palletCodigo = pallet.palletCodigo,
            codigoLido = codigo,
            idempotencyKey = UUID.randomUUID().toString(),
            lidoEm = OffsetDateTime.now().toString(),
        )

        viewModelScope.launch {
            envioRetorno.withLock {
                _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
                try {
                    // A caixa anterior sem resposta vai primeiro. Enquanto ela
                    // não se resolve, este bipe não é enviado: se fosse, e o
                    // operador rebipasse a caixa anterior, ela contaria duas
                    // vezes e esconderia um extravio.
                    if (!resolverLeituraSemResposta()) {
                        if (api != null) avisarSemResposta(naoContou = codigo)
                        return@withLock
                    }
                    if (!enviarLeituraRetorno(leitura) && api != null) avisarSemResposta(naoContou = null)
                } finally {
                    versaoRetorno++
                    _estado.update { it.copy(carregando = false) }
                }
            }
        }
    }

    /** Reenvia a leitura sem resposta, se houver. true: não há mais pendência. */
    private suspend fun resolverLeituraSemResposta(): Boolean {
        val pendente = leituraRetornoSemResposta ?: return true
        return enviarLeituraRetorno(pendente)
    }

    /**
     * Envia uma leitura e aplica a resposta. false quando a resposta não veio
     * (rede, erro do servidor, sessão): a leitura fica guardada para o reenvio.
     */
    private suspend fun enviarLeituraRetorno(leitura: LeituraRetornoSemResposta): Boolean {
        try {
            val r = exigirApi().registrarLeituraRetorno(
                idempotencyKey = leitura.idempotencyKey,
                retornoId = leitura.retornoId,
                palletCodigo = leitura.palletCodigo,
                codigoLido = leitura.codigoLido,
                lidoEm = leitura.lidoEm,
            )
            leituraRetornoSemResposta = null
            feedback.sinalizar(if (r.palletCompleto) Evento.ITEM_COMPLETO else Evento.ACEITA)
            aplicarLeituraRetorno(r)
            return true
        } catch (e: ErroApi) {
            when {
                // Erro do próprio servidor não diz se a leitura ficou
                // gravada: guarda a chave, como na falta de rede.
                e.codigo == "ERRO_INTERNO" || e.codigo.startsWith("ERRO_HTTP_5") -> {
                    leituraRetornoSemResposta = leitura
                    return false
                }
                e.codigo == "SESSAO_EXPIRADA" -> {
                    // Guardada: o reenvio sai no primeiro bipe depois do login.
                    leituraRetornoSemResposta = leitura
                    api = null
                    _estado.update { it.copy(tela = Tela.LOGIN, mensagem = e.mensagem) }
                    return false
                }
                else -> {
                    // Recusa de negócio é resposta: a caixa não contou, e a
                    // chave não serve mais para reenvio.
                    leituraRetornoSemResposta = null
                    tratarRecusaDeLeitura(e, leitura.codigoLido)
                    // Fechado por outro coletor, ou "Retorno completo" na
                    // retaguarda: a tela passa a mostrar o que vale.
                    if (e.codigo == "PALLET_JA_CONFERIDO" || e.codigo == "RETORNO_NAO_EM_CONFERENCIA") {
                        runCatching { aplicarDetalheRetorno(exigirApi().retorno(leitura.retornoId)) }
                    }
                    return true
                }
            }
        } catch (e: Exception) {
            leituraRetornoSemResposta = leitura
            return false
        }
    }

    private fun avisarSemResposta(naoContou: String?) {
        val p = leituraRetornoSemResposta ?: return
        feedback.sinalizar(Evento.ITEM_ERRADO)
        _estado.update {
            it.copy(
                alerta = Alerta(
                    "Sem resposta do servidor",
                    "A caixa ${p.codigoLido} do pallet ${p.palletCodigo} ainda não foi confirmada. Ela é " +
                        "reenviada sozinha na próxima leitura, sem contar duas vezes: NÃO bipe ela de novo." +
                        (naoContou?.let { "\n\nEsta leitura ($it) NÃO contou: bipe a caixa de novo quando a rede voltar." } ?: ""),
                    true,
                )
            )
        }
    }

    private fun aplicarLeituraRetorno(r: ResultadoLeituraRetorno) = _estado.update { st ->
        val pallets = st.palletsRetorno.map { p ->
            if (p.id != r.retornoPalletId) {
                p
            } else {
                val produtos = if (p.produtos.any { it.produtoCodigo == r.produtoCodigo }) {
                    p.produtos.map {
                        if (it.produtoCodigo == r.produtoCodigo) {
                            it.copy(conferida = r.produtoConferida, esperada = r.produtoEsperada)
                        } else {
                            it
                        }
                    }
                } else {
                    p.produtos + ProdutoRetorno(r.produtoCodigo, r.produtoDescricao, r.produtoEsperada, r.produtoConferida)
                }
                p.copy(qtdConferida = r.qtdConferida, qtdEsperada = r.qtdEsperada, produtos = produtos)
            }
        }
        val desteAberto = st.palletRetorno?.id == r.retornoPalletId
        st.comPalletsRetorno(pallets).copy(
            produtoLidoRetorno = if (desteAberto) r.produtoCodigo else st.produtoLidoRetorno,
            sucesso = if (desteAberto && r.palletCompleto) "Pallet completo — toque no cadeado para fechar" else null,
        )
    }

    private fun aplicarPalletRetorno(p: RetornoPallet) = _estado.update { st ->
        st.comPalletsRetorno(st.palletsRetorno.map { if (it.id == p.id) p else it })
    }

    private fun aplicarDetalheRetorno(d: RetornoDetalhe) = _estado.update { st ->
        if (st.retorno?.id != d.retorno.id) {
            st
        } else {
            val aberto = st.palletRetorno?.let { a -> d.pallets.firstOrNull { it.id == a.id } ?: a }
            st.copy(
                retorno = d.retorno,
                palletsRetorno = d.pallets,
                palletRetorno = aberto,
                retornos = st.retornos.map { if (it.id == d.retorno.id) d.retorno else it },
                // Reaberto na retaguarda enquanto a lista mostrava fechado: o
                // aviso de "fechado" deixou de valer.
                mensagem = if (st.mensagem == MSG_PALLET_FECHADO && aberto?.fechado == false) null else st.mensagem,
            )
        }
    }

    /** Leva o pallet novo à lista, ao pallet aberto e ao resumo do retorno. */
    private fun EstadoApp.comPalletsRetorno(pallets: List<RetornoPallet>): EstadoApp = copy(
        palletsRetorno = pallets,
        palletRetorno = palletRetorno?.let { a -> pallets.firstOrNull { it.id == a.id } ?: a },
        retorno = retorno?.copy(
            palletsFechados = pallets.count { it.fechado },
            caixasConferidas = pallets.sumOf { it.qtdConferida },
        ),
    )

    /**
     * Recarrega o retorno aberto sem travar a tela nem mexer nos avisos.
     *
     * A consulta fica fora da trava — dentro, segurava bipe e cadeado por até
     * 10 s sem nada na tela. Só o aplicar entra, e só se nenhuma operação
     * terminou no meio: um retrato tirado antes de uma leitura e aplicado
     * depois dela faria o contador recuar.
     */
    private fun atualizarRetornoEmSilencio() {
        val cliente = api ?: return
        val id = _estado.value.retorno?.id ?: return
        viewModelScope.launch {
            val antes = versaoRetorno
            val d = runCatching { cliente.retorno(id) }.getOrNull() ?: return@launch
            envioRetorno.withLock { if (versaoRetorno == antes) aplicarDetalheRetorno(d) }
        }
    }

    private fun atualizarRetornosEmSilencio() {
        val cliente = api ?: return
        if (_estado.value.retornosIndisponiveis) return
        viewModelScope.launch {
            runCatching { cliente.retornosEmConferencia() }
                .onSuccess { lista -> _estado.update { it.copy(retornos = lista) } }
        }
    }

    /**
     * Cadeado do pallet. Completo, fecha direto; faltando caixa, a tela pede a
     * justificativa antes — caixa que não voltou é extravio, e o motivo
     * precisa chegar à tratativa. Fechado o último pallet, o retorno vai para
     * a tratativa na retaguarda e sai da lista do coletor.
     */
    fun fecharPalletRetorno(retornoPalletId: Long, justificativa: String? = null) {
        // Pelo id que a tela tinha ao tocar no cadeado, e não pelo pallet
        // aberto agora: um bipe de etiqueta com o diálogo aberto troca o pallet.
        val pallet = _estado.value.palletsRetorno.firstOrNull { it.id == retornoPalletId } ?: return
        val retornoId = _estado.value.retorno?.id ?: return
        // Já no toque: o cadeado apaga e um segundo toque não entra na fila.
        _estado.update { it.copy(carregando = true) }
        viewModelScope.launch {
            // Na trava: espera as leituras deste pallet ainda no ar, senão o
            // servidor fecharia com uma contagem que ainda ia mudar.
            envioRetorno.withLock {
                _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
                try {
                    // A leitura sem resposta vai antes, pelo mesmo motivo.
                    if (!resolverLeituraSemResposta()) {
                        if (api != null) avisarSemResposta(naoContou = null)
                        return@withLock
                    }
                    val r = exigirApi().fecharPalletRetorno(pallet.id, justificativa?.trim()?.takeIf { it.isNotEmpty() })
                    feedback.sinalizar(Evento.ITEM_COMPLETO)
                    aplicarPalletRetorno(r)
                    // O servidor é quem diz se o retorno acabou; falhando a
                    // consulta, a conta local dos pallets fechados basta.
                    runCatching { aplicarDetalheRetorno(exigirApi().retorno(retornoId)) }
                    val faltam = _estado.value.palletsRetorno.count { !it.fechado }
                    _estado.update {
                        // Volta à lista só quem ainda está neste pallet: quem já
                        // bipou a etiqueta de outro continua conferindo o outro.
                        val aqui = it.tela == Tela.CONFERENCIA_RETORNO && it.palletRetorno?.id == pallet.id
                        it.copy(
                            tela = if (aqui) Tela.RETORNO else it.tela,
                            produtoLidoRetorno = if (aqui) null else it.produtoLidoRetorno,
                            sucesso = if (faltam == 0) "Conferência encerrada — a tratativa é na retaguarda"
                                      else "${pallet.palletCodigo} fechado · faltam $faltam pallet(s)",
                        )
                    }
                } catch (e: ErroApi) {
                    feedback.sinalizar(Evento.ITEM_ERRADO)
                    if (e.codigo == "SESSAO_EXPIRADA") {
                        api = null
                        _estado.update { it.copy(tela = Tela.LOGIN, mensagem = e.mensagem) }
                    } else {
                        _estado.update {
                            it.copy(
                                alerta = Alerta(
                                    when (e.codigo) {
                                        "CONFERENCIA_INCOMPLETA" -> "Pallet incompleto"
                                        "PALLET_JA_CONFERIDO" -> "Pallet já conferido"
                                        "RETORNO_NAO_EM_CONFERENCIA" -> "Retorno fora de conferência"
                                        else -> "Não foi possível fechar"
                                    },
                                    e.mensagem,
                                    true,
                                )
                            )
                        }
                        if (e.codigo == "PALLET_JA_CONFERIDO" || e.codigo == "RETORNO_NAO_EM_CONFERENCIA") {
                            runCatching { aplicarDetalheRetorno(exigirApi().retorno(retornoId)) }
                        }
                    }
                } catch (e: Exception) {
                    feedback.sinalizar(Evento.ITEM_ERRADO)
                    // Pode ter fechado sem a resposta chegar: tocar de novo no
                    // cadeado é seguro, o servidor responde "já conferido".
                    _estado.update {
                        it.copy(
                            alerta = Alerta(
                                "Sem resposta do servidor",
                                "O pallet pode não ter sido fechado. Verifique a rede e toque no cadeado de novo.",
                                true,
                            )
                        )
                    }
                } finally {
                    versaoRetorno++
                    _estado.update { it.copy(carregando = false) }
                }
            }
        }
    }

    fun avisarPalletRetornoFechado() = _estado.update {
        it.copy(
            alerta = Alerta(
                "Pallet já fechado",
                "Reabrir um pallet do retorno é ação do supervisor, pela retaguarda.",
                false,
            )
        )
    }

    /** Desfaz a última caixa lida neste pallet — a bipada duas vezes por engano. */
    fun desfazerUltimaLeituraRetorno() = comCarregamento {
        val pallet = _estado.value.palletRetorno ?: return@comCarregamento
        envioRetorno.withLock {
            try {
                // A leitura sem resposta vai antes: desfazer "a última" com ela
                // no ar desfaria a caixa anterior, e o reenvio depois traria de
                // volta justamente a que devia sair.
                if (!resolverLeituraSemResposta()) {
                    if (api != null) avisarSemResposta(naoContou = null)
                    return@withLock
                }
                val r = exigirApi().desfazerUltimaRetorno(pallet.id)
                feedback.sinalizar(Evento.ACEITA)
                aplicarPalletRetorno(r)
                _estado.update {
                    it.copy(
                        produtoLidoRetorno = null,
                        sucesso = "Última leitura desfeita · ${r.qtdConferida} de ${r.qtdEsperada} caixas",
                    )
                }
            } finally {
                versaoRetorno++
            }
        }
    }

    /** Pallet que voltou pode ter perdido o cartão no caminho. */
    fun reimprimirCartaoRetorno() = comCarregamento {
        val p = _estado.value.palletRetorno ?: return@comCarregamento
        val situacao = exigirApi().imprimirCartao(p.palletCodigo)["impressao"]
        feedback.sinalizar(if (situacao == "ENVIADA") Evento.ACEITA else Evento.ITEM_ERRADO)
        _estado.update {
            it.copy(
                sucesso = if (situacao == "ENVIADA") "Cartão de ${p.palletCodigo} enviado à impressora" else null,
                mensagem = when (situacao) {
                    "ENVIADA" -> null
                    "SEM_IMPRESSORA" -> "Nenhuma impressora cadastrada para o seu usuário."
                    "SEM_CONTEUDO" -> "${p.palletCodigo} não tem conteúdo para imprimir no cartão."
                    else -> "A impressora não aceitou o trabalho. Verifique se está ligada."
                },
            )
        }
    }

    /** Recusas que são do pallet selecionado, não da caixa lida. */
    private val ERROS_DE_PALLET = setOf(
        "PALLET_OBRIGATORIO", "PALLET_NAO_ENCONTRADO", "PALLET_TIPO_INVALIDO",
        "PALLET_ENCERRADO", "PALLET_DE_OUTRA_NOTA",
    )

    /** O código do erro decide som e texto, nunca a mensagem. Ver docs/API.md §5. */
    private fun tratarRecusaDeLeitura(erro: ErroApi?, codigo: String) {
        val evento = when (erro?.codigo) {
            "PRODUTO_DIVERGENTE", "PRODUTO_FORA_DO_PALLET" -> Evento.ITEM_ERRADO
            "CODIGO_NAO_CADASTRADO" -> Evento.CODIGO_DESCONHECIDO
            else -> Evento.ITEM_ERRADO
        }
        feedback.sinalizar(evento)

        val titulo = when (erro?.codigo) {
            "PRODUTO_DIVERGENTE" -> "Item errado"
            "CODIGO_NAO_CADASTRADO" -> "Código não cadastrado"
            "QUANTIDADE_COMPLETA" -> "Quantidade completa"
            "ITEM_FECHADO" -> "Item fechado"
            "PALLET_DE_OUTRA_NOTA" -> "Pallet de outra nota"
            "PALLET_NAO_SELECIONADO" -> "Sem pallet"
            // Conferência de retorno.
            "PRODUTO_FORA_DO_PALLET" -> "Produto fora do pallet"
            "PALLET_JA_CONFERIDO" -> "Pallet já conferido"
            "PALLET_FORA_DO_RETORNO" -> "Pallet fora deste retorno"
            "RETORNO_NAO_EM_CONFERENCIA" -> "Retorno fora de conferência"
            // Repaletização.
            "PALLETS_DE_NOTAS_DIFERENTES" -> "Pallets de notas diferentes"
            "PALLET_JA_EXPEDIDO" -> "Pallet já expedido"
            "PALLET_JA_FUNDIDO" -> "Pallet já juntado"
            "PALLET_EM_RETORNO" -> "Pallet em retorno"
            // Conferência de devolução.
            "PALLET_OBRIGATORIO" -> "Pallet primeiro"
            "PALLET_NAO_ENCONTRADO" -> "Pallet não encontrado"
            "PALLET_TIPO_INVALIDO" -> "Pallet de separação"
            "PALLET_ENCERRADO" -> "Pallet fora de circulação"
            "UNIDADES_POR_CAIXA_NAO_CADASTRADA" -> "Sem unidades por caixa"
            null -> "Sem resposta do servidor"
            else -> "Leitura recusada"
        }
        val detalhe = erro?.mensagem ?: "Leitura recusada pelo servidor."
        // Unidades soltas não têm código lido: a linha vazia só confundia.
        val lido = if (codigo.isBlank()) "" else "\n\nCódigo lido: $codigo"
        _estado.update {
            it.copy(alerta = Alerta(titulo, "$detalhe$lido", true))
        }
    }

    /**
     * O cadeado é o próprio controle: tocar nele fecha o item, se permitido.
     * Fechar incompleto é ação de supervisor, na retaguarda — aqui o motivo
     * aparece em diálogo, nunca em silêncio.
     */
    fun alternarCadeado() {
        val item = _estado.value.item ?: return
        if (item.fechado) {
            _estado.update {
                it.copy(
                    alerta = Alerta(
                        "Item já fechado",
                        "Reabrir um item é ação do supervisor, pela retaguarda.",
                        false,
                    )
                )
            }
            return
        }
        fecharItem()
    }

    private fun fecharItem() {
        val item = _estado.value.item ?: return
        viewModelScope.launch {
            _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
            try {
                val atualizado = exigirApi().fecharItem(item.id)
                feedback.sinalizar(Evento.ITEM_COMPLETO)
                val nota = _estado.value.nota
                val itens = if (nota != null) comPendentes(exigirApi().itens(nota.id)) else _estado.value.itens
                _estado.update {
                    it.copy(
                        item = atualizado.copy(qtdSeparada = atualizado.qtdSeparada + fila.pendentesDoItem(atualizado.id)),
                        itens = itens, tela = Tela.ITENS, sucesso = "Item fechado",
                    )
                }
            } catch (e: ErroApi) {
                feedback.sinalizar(Evento.ITEM_ERRADO)
                _estado.update {
                    it.copy(
                        alerta = Alerta(
                            if (e.codigo == "ITEM_INCOMPLETO") "Item incompleto" else "Não foi possível fechar",
                            e.mensagem,
                            true,
                        )
                    )
                }
            } catch (e: Exception) {
                _estado.update {
                    it.copy(
                        alerta = Alerta(
                            "Sem resposta do servidor",
                            "O cadeado não foi fechado. Verifique a rede e tente de novo.",
                            true,
                        )
                    )
                }
            } finally {
                _estado.update { it.copy(carregando = false) }
            }
        }
    }

    /**
     * Exclui caixas bipadas por engano.
     *
     * `palletCodigo` vem da tela quando não há pallet ativo: o vínculo com o
     * pallet mora só na memória do app e se perde ao encerrar o pallet ou ao
     * reabrir o aplicativo. Antes a função saía em silêncio nesse caso, e o
     * botão Confirmar parecia morto.
     */
    fun excluirDaSeparacao(quantidade: Int, palletCodigo: String? = null) = comCarregamento {
        val e = _estado.value
        val item = e.item ?: return@comCarregamento
        val pallet = palletCodigo?.trim()?.uppercase()?.takeIf { it.isNotBlank() }
            ?: e.palletAtivo
            ?: run {
                _estado.update {
                    it.copy(
                        mensagem = "Informe de qual pallet excluir — não há pallet aberto neste aparelho.",
                    )
                }
                return@comCarregamento
            }
        val atualizado = exigirApi().excluirSeparacao(pallet, item.id, quantidade)
        feedback.sinalizar(Evento.ACEITA)
        _estado.update {
            it.copy(
                item = atualizado.copy(qtdSeparada = atualizado.qtdSeparada + fila.pendentesDoItem(atualizado.id)),
                sucesso = "$quantidade caixa(s) excluída(s) de $pallet",
            )
        }
        recarregarItensAgora()
    }

    // ----------------------------------------------------------------- internos

    private fun exigirApi(): Api = api ?: throw ErroApi("SESSAO_EXPIRADA", "Faça login novamente")

    private fun comCarregamento(bloco: suspend () -> Unit) {
        viewModelScope.launch {
            _estado.update { it.copy(carregando = true, mensagem = null, sucesso = null) }
            try {
                bloco()
            } catch (e: ErroApi) {
                if (e.codigo == "SESSAO_EXPIRADA") {
                    api = null
                    _estado.update { it.copy(tela = Tela.LOGIN, mensagem = e.mensagem) }
                } else {
                    _estado.update { it.copy(mensagem = e.mensagem) }
                }
            } catch (e: Exception) {
                _estado.update {
                    it.copy(
                        mensagem = "Sem resposta de ${prefs.servidor.ifBlank { "servidor não configurado" }}. " +
                            "Verifique a rede."
                    )
                }
            } finally {
                _estado.update { it.copy(carregando = false) }
            }
        }
    }

    override fun onCleared() {
        scanner.cancelar()
        super.onCleared()
    }
}
