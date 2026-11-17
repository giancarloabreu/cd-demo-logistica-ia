package br.com.cddemo.redespacho.rede

import com.fasterxml.jackson.annotation.JsonIgnoreProperties
import com.fasterxml.jackson.annotation.JsonProperty
import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.module.kotlin.KotlinModule
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.jackson.jackson

// --- modelos do contrato (docs/API.md) --------------------------------------

@JsonIgnoreProperties(ignoreUnknown = true)
data class Saude(
    val versao: String = "",
    val horaServidor: String = "",
    val bancoOk: Boolean = false,
    val versaoMinimaApp: String = "",
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class Usuario(
    val id: Long = 0,
    val login: String = "",
    val nome: String = "",
    val perfil: String = "",
    val multiItem: Boolean = false,
    val impressoraId: Long? = null,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class RespostaLogin(val token: String = "", val usuario: Usuario = Usuario())

@JsonIgnoreProperties(ignoreUnknown = true)
data class Instrucao(
    val id: Long = 0,
    val numero: String = "",
    val status: String = "",
    val criadoEm: String = "",
    val criadoPor: String = "",
    val qtdNotas: Int = 0,
    val qtdCaixasPrevistas: Int = 0,
    val qtdCaixasSeparadas: Int = 0,
    val itensEmAberto: Int = 0,
    /** Cadeados todos fechados, esperando a retaguarda concluir. */
    val prontaParaConclusao: Boolean = false,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class Nota(
    val id: Long = 0,
    val numero: String = "",
    val cliente: String = "",
    val status: String = "",
    val qtdPrevista: Int = 0,
    val qtdSeparada: Int = 0,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class Item(
    val id: Long = 0,
    @JsonProperty("nItem") val nItem: Int = 0,
    val produtoCodigo: String = "",
    val produtoDescricao: String = "",
    val qtdPrevista: Int = 0,
    val qtdSeparada: Int = 0,
    val status: String = "",
    val separadores: List<String> = emptyList(),
) {
    val fechado: Boolean get() = status == "FECHADO" || status == "FECHADO_DIVERGENCIA"
    val comDivergencia: Boolean get() = status == "FECHADO_DIVERGENCIA"
}

@JsonIgnoreProperties(ignoreUnknown = true)
data class ResultadoLeitura(
    val resultado: String = "",
    val qtdSeparada: Int = 0,
    val qtdPrevista: Int = 0,
    val itemCompleto: Boolean = false,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class PalletsCriados(val pallets: List<String> = emptyList(), val impressao: String = "")

@JsonIgnoreProperties(ignoreUnknown = true)
data class Devolucao(
    val id: Long = 0,
    val numero: String = "",
    val emitenteNome: String = "",
    val notaOrigemNumero: String? = null,
    val status: String = "",
    val qtdEsperadaUn: Int = 0,
    val qtdConferidaUn: Int = 0,
    /** Conferência de estoque: sobra sem NF, em que o separador bipa qualquer caixa. */
    val sobra: Boolean = false,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class ItemDevolucao(
    val id: Long = 0,
    val produtoCodigo: String? = null,
    val produtoDescricao: String? = null,
    val cprodCliente: String? = null,
    val xprodCliente: String? = null,
    val unidadesPorCaixa: Int? = null,
    val qtdEsperadaUn: Int = 0,
    val qtdConferidaUn: Int = 0,
    val status: String = "ABERTO",
    val justificativa: String? = null,
) {
    val fechado: Boolean get() = status == "FECHADO" || status == "FECHADO_DIVERGENCIA"
}

@JsonIgnoreProperties(ignoreUnknown = true)
data class ResultadoConferencia(
    val itemId: Long = 0,
    val qtdConferidaUn: Int = 0,
    val qtdEsperadaUn: Int = 0,
    val completo: Boolean = false,
)

/**
 * Retorno: NF já expedida cujos pallets voltaram ao armazém (recusa total ou
 * reentrega). Registrado na retaguarda; o coletor só confere caixa a caixa.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
data class RetornoResumo(
    val id: Long = 0,
    val notaId: Long = 0,
    val notaNumero: String = "",
    val cliente: String = "",
    val motivo: String = "",
    /** EM_CONFERENCIA, AGUARDANDO_TRATATIVA, TRATADO ou CANCELADO. */
    val status: String = "",
    val registradoEm: String = "",
    val registradoPor: String = "",
    val qtdPallets: Int = 0,
    val palletsFechados: Int = 0,
    val caixasEsperadas: Int = 0,
    val caixasConferidas: Int = 0,
    val caixasFaltando: Int = 0,
    val conferenciaManual: Boolean = false,
    val conferidoEm: String? = null,
    val conferidoPor: String? = null,
    val tratativa: String? = null,
    val nfsRefaturamento: String? = null,
    val tratativaObs: String? = null,
    val tratadoEm: String? = null,
    val tratadoPor: String? = null,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class ProdutoRetorno(
    val produtoCodigo: String = "",
    val produtoDescricao: String = "",
    val esperada: Int = 0,
    val conferida: Int = 0,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class RetornoPallet(
    /** Id do pallet dentro do retorno (retornoPalletId), não do pallet. */
    val id: Long = 0,
    val palletCodigo: String = "",
    /** ABERTO, CONFERIDO ou CONFERIDO_FALTA. */
    val status: String = "ABERTO",
    val qtdEsperada: Int = 0,
    val qtdConferida: Int = 0,
    val manual: Boolean = false,
    val justificativa: String? = null,
    val produtos: List<ProdutoRetorno> = emptyList(),
) {
    val fechado: Boolean get() = status == "CONFERIDO" || status == "CONFERIDO_FALTA"
    val comFalta: Boolean get() = status == "CONFERIDO_FALTA"
}

@JsonIgnoreProperties(ignoreUnknown = true)
data class RetornoDetalhe(
    val retorno: RetornoResumo = RetornoResumo(),
    val pallets: List<RetornoPallet> = emptyList(),
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class ResultadoLeituraRetorno(
    val retornoPalletId: Long = 0,
    val palletCodigo: String = "",
    val qtdConferida: Int = 0,
    val qtdEsperada: Int = 0,
    val produtoCodigo: String = "",
    val produtoDescricao: String = "",
    val produtoConferida: Int = 0,
    val produtoEsperada: Int = 0,
    val palletCompleto: Boolean = false,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class Pallet(
    val id: Long = 0,
    val codigo: String = "",
    val tipo: String = "",
    val status: String = "",
    val notaFiscalId: Long? = null,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class PalletParaEnderecar(
    val codigo: String = "",
    val tipo: String = "",
    val status: String = "",
    val notaNumero: String? = null,
    val cliente: String? = null,
    val qtdCaixas: Int = 0,
    val enderecoAtual: String? = null,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class Posicao(
    val id: Long = 0,
    val nivel: Int = 0,
    val ocupadaPor: String? = null,
    val notaNumero: String? = null,
    val qtdCaixas: Int? = null,
    /** Cliente da NF do pallet (emitente, no de devolução). Servidor 0.7.2+. */
    val cliente: String? = null,
    /** SEPARACAO ou DEVOLUCAO: o de devolução conta em unidades. Servidor 0.7.2+. */
    val tipoPallet: String? = null,
) {
    val livre: Boolean get() = ocupadaPor == null
}

@JsonIgnoreProperties(ignoreUnknown = true)
data class Box(
    val id: Long = 0,
    val codigo: String = "",
    val deposito: String = "",
    val rua: String = "",
    val tipo: String = "",
    val posicoes: List<Posicao> = emptyList(),
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class ResultadoFusao(
    val palletOrigem: String = "",
    val palletDestino: String = "",
    val qtdCaixas: Int = 0,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class ItemDoPallet(
    val itemId: Long = 0,
    val produtoCodigo: String = "",
    val produtoDescricao: String = "",
    val quantidade: Int = 0,
)

@JsonIgnoreProperties(ignoreUnknown = true)
data class ConteudoPallet(
    val codigo: String = "",
    val status: String = "",
    val notaNumero: String? = null,
    val cliente: String? = null,
    val endereco: String? = null,
    val totalCaixas: Int = 0,
    val itens: List<ItemDoPallet> = emptyList(),
)

/**
 * Os dois pallets de uma repaletização, como estão agora no servidor.
 *
 * Repaletizar é passar caixa a caixa de um pallet para outro da mesma NF (ou
 * para um pallet novo, que vira da NF da origem na primeira caixa). Toda
 * resposta traz os dois conteúdos inteiros: a tela mostra o que o servidor
 * diz, nunca uma conta feita no aparelho.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
data class Repaletizacao(
    val notaNumero: String? = null,
    val cliente: String? = null,
    val origem: ConteudoPallet = ConteudoPallet(),
    val destino: ConteudoPallet = ConteudoPallet(),
    /** Produto da caixa desta leitura (ou da que o desfazer devolveu); nulo ao abrir. */
    val produtoCodigo: String? = null,
    val produtoDescricao: String? = null,
    /** A origem ficou sem caixa: saiu de circulação e liberou o endereço. */
    val origemEsvaziada: Boolean = false,
    /** Caixas que este usuário passou da origem para o destino e ainda não desfez. */
    val movidas: Int = 0,
)

/**
 * Fim da repaletização. `impressao` é código do pallet → situação do cartão:
 * "ENVIADA" saiu para a impressora; qualquer outra coisa não saiu.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
data class ConclusaoRepaletizacao(
    val repaletizacao: Repaletizacao = Repaletizacao(),
    val impressao: Map<String, String> = emptyMap(),
)

/** Um pallet na consulta: onde está e o que tem. */
@JsonIgnoreProperties(ignoreUnknown = true)
data class PalletConsultado(
    val codigo: String = "",
    val status: String = "",
    /** "C8-D28 nível 3"; nulo quando não está endereçado (ou já foi expedido). */
    val endereco: String? = null,
    val deposito: String? = null,
    val quantidade: Int = 0,
    val itens: List<ItemDoPallet> = emptyList(),
    /** É o pallet que foi bipado. */
    val consultado: Boolean = false,
    /** Box e nível do endereço, separados (servidor 0.7.2+): a conferência de box abre por eles. */
    val box: String? = null,
    val nivel: Int? = null,
)

/**
 * Consulta de pallet: de qual NF ele é e onde estão os outros pallets dela.
 * O bipado vem primeiro em `pallets`. Pallet de devolução mostra a NF de
 * devolução e conta em unidades ("un"), como a conferência dela.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
data class ConsultaPallet(
    val codigo: String = "",
    val tipo: String = "",
    val status: String = "",
    /** Nulo no pallet novo, que ainda não recebeu caixa. */
    val notaNumero: String? = null,
    val cliente: String? = null,
    val notaStatus: String? = null,
    val unidade: String = "cx",
    val pallets: List<PalletConsultado> = emptyList(),
)

/**
 * Código de erro do próprio app: o servidor não tem a rota pedida. Ver
 * [Api.consultarPallet] — num GET, servidor antigo não responde 404.
 */
const val ROTA_AUSENTE = "ROTA_AUSENTE"

@JsonIgnoreProperties(ignoreUnknown = true)
data class VersaoApp(
    val versao: String = "",
    val obrigatoria: Boolean = false,
    val tamanho: Long = 0,
    val url: String = "/app/apk",
) {
    val disponivel: Boolean get() = versao.isNotBlank()
}

@JsonIgnoreProperties(ignoreUnknown = true)
data class DetalheErro(val codigo: String = "", val mensagem: String = "")

@JsonIgnoreProperties(ignoreUnknown = true)
data class CorpoErro(val erro: DetalheErro = DetalheErro())

/**
 * Erro vindo do servidor. O `codigo` é o que decide comportamento no app; a
 * mensagem é só para exibir. Ver docs/API.md §5.
 */
class ErroApi(val codigo: String, val mensagem: String) : Exception(mensagem)

// --- cliente ----------------------------------------------------------------

class Api(private val urlBase: String) {

    var token: String? = null

    private val http = HttpClient(OkHttp) {
        expectSuccess = false
        install(ContentNegotiation) {
            jackson {
                registerModule(KotlinModule.Builder().build())
                configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
            }
        }
        install(HttpTimeout) {
            // Curto de propósito: na câmara, esperar 30s por um servidor
            // inalcançável é pior do que avisar rápido que caiu a rede.
            connectTimeoutMillis = 4_000
            requestTimeoutMillis = 10_000
        }
    }

    suspend fun saude(): Saude = http.get("$urlBase/api/v1/saude").exigirOk()

    suspend fun entrar(login: String, senha: String, dispositivoId: String): RespostaLogin {
        val r: RespostaLogin = http.post("$urlBase/api/v1/auth/login") {
            contentType(ContentType.Application.Json)
            setBody(mapOf("login" to login, "senha" to senha, "dispositivoId" to dispositivoId))
        }.exigirOk()
        token = r.token
        return r
    }

    suspend fun instrucoesEmAberto(): List<Instrucao> =
        http.get("$urlBase/api/v1/instrucoes") { autenticado() }.exigirOk()

    suspend fun notas(instrucaoId: Long): List<Nota> =
        http.get("$urlBase/api/v1/instrucoes/$instrucaoId/notas") { autenticado() }.exigirOk()

    suspend fun itens(notaId: Long): List<Item> =
        http.get("$urlBase/api/v1/notas/$notaId/itens") { autenticado() }.exigirOk()

    suspend fun criarPallets(quantidade: Int, tipo: String = "SEPARACAO"): PalletsCriados =
        http.post("$urlBase/api/v1/pallets") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("tipo" to tipo, "quantidade" to quantidade))
        }.exigirOk()

    suspend fun selecionarPallet(codigo: String, notaId: Long): Pallet =
        http.post("$urlBase/api/v1/pallets/$codigo/selecionar") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("notaId" to notaId))
        }.exigirOk()

    suspend fun encerrarPallet(codigo: String): Pallet =
        http.post("$urlBase/api/v1/pallets/$codigo/encerrar") { autenticado() }.exigirOk()

    suspend fun registrarLeitura(
        idempotencyKey: String,
        palletCodigo: String?,
        itemId: Long,
        codigoLido: String,
        lidoEm: String,
    ): ResultadoLeitura = http.post("$urlBase/api/v1/leituras") {
        autenticado()
        contentType(ContentType.Application.Json)
        setBody(
            mapOf(
                "idempotencyKey" to idempotencyKey,
                "palletCodigo" to palletCodigo,
                "itemId" to itemId,
                "codigoLido" to codigoLido,
                "lidoEm" to lidoEm,
            )
        )
    }.exigirOk()

    suspend fun fecharItem(itemId: Long): Item =
        http.post("$urlBase/api/v1/itens/$itemId/fechar") { autenticado() }.exigirOk()

    suspend fun excluirSeparacao(palletCodigo: String, itemId: Long, quantidade: Int): Item =
        http.post("$urlBase/api/v1/separacao/excluir") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(
                mapOf(
                    "palletCodigo" to palletCodigo,
                    "itemId" to itemId,
                    "quantidade" to quantidade,
                )
            )
        }.exigirOk()

    // ---------------------------------------------------------------- devolução

    suspend fun devolucoesParaConferir(): List<Devolucao> =
        http.get("$urlBase/api/v1/devolucoes") {
            autenticado()
            url.parameters.append("status", "AGUARDANDO_CONFERENCIA,EM_CONFERENCIA")
        }.exigirOk()

    suspend fun itensDaDevolucao(devolucaoId: Long): List<ItemDevolucao> =
        http.get("$urlBase/api/v1/devolucoes/$devolucaoId/itens") { autenticado() }.exigirOk()

    suspend fun conferirDevolucao(
        idempotencyKey: String,
        itemDevolucaoId: Long,
        modo: String,
        codigoLido: String?,
        qtdUn: Int?,
        palletCodigo: String?,
        lidoEm: String,
    ): ResultadoConferencia = http.post("$urlBase/api/v1/devolucoes/conferencia") {
        autenticado()
        contentType(ContentType.Application.Json)
        setBody(
            mapOf(
                "idempotencyKey" to idempotencyKey,
                "itemDevolucaoId" to itemDevolucaoId,
                "modo" to modo,
                "codigoLido" to codigoLido,
                "qtdUn" to qtdUn,
                "palletCodigo" to palletCodigo,
                "lidoEm" to lidoEm,
            )
        )
    }.exigirOk()

    /**
     * Conferência de estoque: a caixa entra como sobra, sem NF. [codigoLido]
     * vem do bipe (uma caixa); [itemId] vem do digitar, com [caixas] caixas
     * do mesmo produto. Devolve os itens da conferência já recontados.
     */
    suspend fun biparSobra(
        devolucaoId: Long,
        idempotencyKey: String,
        codigoLido: String?,
        itemId: Long?,
        caixas: Int,
        palletCodigo: String?,
    ): List<ItemDevolucao> = http.post("$urlBase/api/v1/devolucoes/$devolucaoId/sobra/leitura") {
        autenticado()
        contentType(ContentType.Application.Json)
        setBody(
            mapOf(
                "idempotencyKey" to idempotencyKey,
                "codigoLido" to codigoLido,
                "itemId" to itemId,
                "caixas" to caixas,
                "palletCodigo" to palletCodigo,
            )
        )
    }.exigirOk()

    suspend fun fecharItemDevolucao(itemId: Long, justificativa: String?): ItemDevolucao =
        http.post("$urlBase/api/v1/devolucoes/itens/$itemId/fechar") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("justificativa" to justificativa))
        }.exigirOk()

    // ------------------------------------------------------------------ retorno

    /** Só os em conferência: aguardando tratativa já não tem o que bipar. */
    suspend fun retornosEmConferencia(): List<RetornoResumo> =
        http.get("$urlBase/api/v1/retornos") {
            autenticado()
            url.parameters.append("status", "EM_CONFERENCIA")
        }.exigirOk()

    suspend fun retorno(id: Long): RetornoDetalhe =
        http.get("$urlBase/api/v1/retornos/$id") { autenticado() }.exigirOk()

    /**
     * A mesma `idempotencyKey` enviada de novo devolve o resultado original
     * sem contar a caixa duas vezes — é o que permite reenviar depois de
     * ficar sem resposta.
     */
    suspend fun registrarLeituraRetorno(
        idempotencyKey: String,
        retornoId: Long,
        palletCodigo: String,
        codigoLido: String,
        lidoEm: String,
    ): ResultadoLeituraRetorno = http.post("$urlBase/api/v1/retornos/leituras") {
        autenticado()
        contentType(ContentType.Application.Json)
        setBody(
            mapOf(
                "idempotencyKey" to idempotencyKey,
                "retornoId" to retornoId,
                "palletCodigo" to palletCodigo,
                "codigoLido" to codigoLido,
                "lidoEm" to lidoEm,
            )
        )
    }.exigirOk()

    suspend fun fecharPalletRetorno(retornoPalletId: Long, justificativa: String?): RetornoPallet =
        http.post("$urlBase/api/v1/retornos/pallets/$retornoPalletId/fechar") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("justificativa" to justificativa))
        }.exigirOk()

    suspend fun desfazerUltimaRetorno(retornoPalletId: Long): RetornoPallet =
        http.post("$urlBase/api/v1/retornos/pallets/$retornoPalletId/desfazer-ultima") {
            autenticado()
        }.exigirOk()

    suspend fun palletParaEnderecar(codigo: String): PalletParaEnderecar =
        http.get("$urlBase/api/v1/pallets/$codigo/enderecamento") { autenticado() }.exigirOk()

    suspend fun box(codigo: String): Box =
        http.get("$urlBase/api/v1/boxes/$codigo/posicoes") { autenticado() }.exigirOk()

    suspend fun pendentesDeEnderecamento(): List<PalletParaEnderecar> =
        http.get("$urlBase/api/v1/enderecamentos/pendentes") { autenticado() }.exigirOk()

    suspend fun enderecar(palletCodigo: String, posicaoId: Long): PalletParaEnderecar =
        http.post("$urlBase/api/v1/enderecamentos") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("palletCodigo" to palletCodigo, "posicaoId" to posicaoId))
        }.exigirOk()

    suspend fun fundirPallets(origem: String, destino: String): ResultadoFusao =
        http.post("$urlBase/api/v1/pallets/fusao") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("origem" to origem, "destino" to destino))
        }.exigirOk()

    // ------------------------------------------------------------ repaletização

    /** Confere o par — mesma NF, ou destino novo — sem mover caixa nenhuma. */
    suspend fun abrirRepaletizacao(origem: String, destino: String): Repaletizacao =
        http.post("$urlBase/api/v1/pallets/repaletizacao/abrir") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("origem" to origem, "destino" to destino))
        }.exigirOk()

    /**
     * Passa UMA caixa do produto lido da origem para o destino. A mesma
     * `idempotencyKey` enviada de novo devolve a situação atual sem mover
     * outra caixa — é o que permite reenviar depois de ficar sem resposta.
     */
    suspend fun repaletizarCaixa(
        idempotencyKey: String,
        origem: String,
        destino: String,
        codigoLido: String,
        lidoEm: String,
    ): Repaletizacao = http.post("$urlBase/api/v1/pallets/repaletizacao/caixa") {
        autenticado()
        contentType(ContentType.Application.Json)
        setBody(
            mapOf(
                "idempotencyKey" to idempotencyKey,
                "origem" to origem,
                "destino" to destino,
                "codigoLido" to codigoLido,
                "lidoEm" to lidoEm,
            )
        )
    }.exigirOk()

    /** Devolve à origem a última caixa que este usuário passou entre os dois. */
    suspend fun desfazerRepaletizacao(origem: String, destino: String): Repaletizacao =
        http.post("$urlBase/api/v1/pallets/repaletizacao/desfazer") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("origem" to origem, "destino" to destino))
        }.exigirOk()

    /** Imprime o cartão do destino e o da origem (se ainda tiver caixa). */
    suspend fun concluirRepaletizacao(origem: String, destino: String): ConclusaoRepaletizacao =
        http.post("$urlBase/api/v1/pallets/repaletizacao/concluir") {
            autenticado()
            contentType(ContentType.Application.Json)
            setBody(mapOf("origem" to origem, "destino" to destino))
        }.exigirOk()

    suspend fun conteudoDoPallet(codigo: String): ConteudoPallet =
        http.get("$urlBase/api/v1/pallets/$codigo/conteudo-detalhado") { autenticado() }.exigirOk()

    /**
     * De qual NF é o pallet, o que tem e onde estão os outros da mesma NF.
     *
     * Servidor sem esta rota quase nunca responde 404 aqui: a retaguarda é
     * servida na raiz com o index.html de reserva, e um GET desconhecido volta
     * 200 em HTML. Sem esta checagem o erro de conversão caía no "verifique a
     * rede" — o operador ia atrás do wifi quando o que falta é atualizar o
     * servidor.
     */
    suspend fun consultarPallet(codigo: String): ConsultaPallet {
        val r = http.get("$urlBase/api/v1/pallets/$codigo/consulta") { autenticado() }
        val json = r.contentType()?.match(ContentType.Application.Json) == true
        if (!json && (r.status.isSuccess() || r.status == HttpStatusCode.NotFound)) {
            throw ErroApi(ROTA_AUSENTE, "Este servidor não tem a consulta de pallet")
        }
        return r.exigirOk()
    }

    suspend fun reimprimirPallet(codigo: String): Map<String, String> =
        http.post("$urlBase/api/v1/pallets/$codigo/reimprimir") { autenticado() }.exigirOk()

    /** Cartão 80×220 com os itens do pallet — o papel que vai no palete. */
    suspend fun imprimirCartao(codigo: String): Map<String, String> =
        http.post("$urlBase/api/v1/pallets/$codigo/cartao") { autenticado() }.exigirOk()

    suspend fun versaoDisponivel(): VersaoApp =
        http.get("$urlBase/api/v1/app/versao").exigirOk()

    suspend fun sair() {
        runCatching { http.post("$urlBase/api/v1/auth/logout") { autenticado() } }
        token = null
    }

    private fun io.ktor.client.request.HttpRequestBuilder.autenticado() {
        token?.let { header(HttpHeaders.Authorization, "Bearer $it") }
    }

    private suspend inline fun <reified T> HttpResponse.exigirOk(): T {
        if (status.isSuccess()) return body()
        val erro = runCatching { body<CorpoErro>().erro }.getOrNull()
        throw ErroApi(
            erro?.codigo ?: "ERRO_HTTP_${status.value}",
            erro?.mensagem?.takeIf { it.isNotBlank() }
                ?: "Falha na comunicação (HTTP ${status.value})",
        ).also { if (erro == null) bodyAsText() }
    }
}
