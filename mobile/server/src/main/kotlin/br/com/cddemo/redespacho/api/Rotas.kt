package br.com.cddemo.redespacho.api

import br.com.cddemo.redespacho.importacao.PreviaArquivo
import com.fasterxml.jackson.annotation.JsonProperty
import com.fasterxml.jackson.core.type.TypeReference
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import br.com.cddemo.redespacho.seguranca.Perfil
import br.com.cddemo.redespacho.seguranca.Sessao
import br.com.cddemo.redespacho.seguranca.Sessoes
import br.com.cddemo.redespacho.Rede
import br.com.cddemo.redespacho.servico.ArquivoXml
import br.com.cddemo.redespacho.servico.CaixasDoItem
import br.com.cddemo.redespacho.servico.DesembarqueServico
import br.com.cddemo.redespacho.servico.DevolucaoServico
import br.com.cddemo.redespacho.servico.VinculoItem
import br.com.cddemo.redespacho.servico.AtualizacaoServico
import br.com.cddemo.redespacho.impressao.Zpl
import br.com.cddemo.redespacho.servico.EnderecamentoServico
import br.com.cddemo.redespacho.servico.EstoqueServico
import br.com.cddemo.redespacho.servico.FiltroEstoque
import br.com.cddemo.redespacho.servico.ExpedicaoServico
import br.com.cddemo.redespacho.servico.ImpressoraServico
import br.com.cddemo.redespacho.servico.InstrucaoServico
import br.com.cddemo.redespacho.servico.MovimentacaoServico
import br.com.cddemo.redespacho.servico.OrigemDevolucao
import br.com.cddemo.redespacho.servico.TransferenciaItem
import br.com.cddemo.redespacho.servico.FiltroRelatorio
import br.com.cddemo.redespacho.servico.ProdutoServico
import br.com.cddemo.redespacho.servico.RelatorioServico
import br.com.cddemo.redespacho.servico.RetornoServico
import br.com.cddemo.redespacho.servico.SeparacaoServico
import br.com.cddemo.redespacho.servico.UsuarioServico
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.http.content.PartData
import io.ktor.http.content.forEachPart
import io.ktor.server.application.ApplicationCall
import io.ktor.server.request.receive
import io.ktor.server.request.receiveText
import io.ktor.server.request.receiveMultipart
import io.ktor.server.response.header
import io.ktor.server.response.respond
import io.ktor.server.response.respondBytes
import io.ktor.server.response.respondFile
import io.ktor.server.routing.Route
import io.ktor.server.routing.delete
import io.ktor.server.routing.get
import io.ktor.server.routing.post
import io.ktor.server.routing.put
import io.ktor.server.routing.RoutingContext
import io.ktor.server.routing.route
import io.ktor.utils.io.readRemaining
import kotlinx.io.readByteArray
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

const val VERSAO = "0.8.4"
const val VERSAO_MINIMA_APP = "0.1.0"

data class Saude(
    val versao: String,
    val horaServidor: String,
    val bancoOk: Boolean,
    val versaoMinimaApp: String,
    val senhaPadraoPendente: Boolean = false,
    /**
     * Endereço desta máquina na rede, como o coletor a alcança.
     *
     * Quem abre a retaguarda o faz na própria máquina do servidor, onde a
     * página se vê como `localhost`. Sem este campo, o QR de configuração sairia
     * apontando para localhost e nenhum coletor chegaria.
     */
    val enderecoLocal: String = "",
    val porta: Int = 0,
    /** Aviso da subida (banco restaurado do backup, bifurcação, dois bancos na pasta). */
    val restauracao: String? = null,
    /** Backup desativado ou sem chegar à rede. */
    val backup: String? = null,
)

data class PedidoLogin(val login: String = "", val senha: String = "", val dispositivoId: String? = null)

data class UsuarioResposta(
    val id: Long,
    val login: String,
    val nome: String,
    val perfil: String,
    val multiItem: Boolean,
    val impressoraId: Long?,
)

data class RespostaLogin(val token: String, val usuario: UsuarioResposta)

/**
 * Campos cujo nome tem uma única letra minúscula antes de maiúscula (qVol,
 * cProd, nItem) precisam de @JsonProperty: o Jackson não os deriva
 * corretamente do getter — publica "qvolConfere" ou "QVolConfere" conforme a
 * configuração. Ver docs/API.md.
 */
data class NotaPrevia(
    val numero: String,
    val serie: String,
    val cliente: String,
    val qtdItens: Int,
    val qtdCaixas: Int,
    @get:JsonProperty("qVolConfere") val qVolConfere: Boolean,
)

data class ArquivoPreviaResposta(
    val arquivo: String,
    val situacao: String,
    val nota: NotaPrevia? = null,
    val avisos: List<DetalheErro> = emptyList(),
    val erro: DetalheErro? = null,
)

data class PedidoPallets(
    val tipo: String = "SEPARACAO",
    val quantidade: Int = 1,
    val impressoraId: Long? = null,
)
data class PedidoSelecionarPallet(val notaId: Long? = null)
data class PedidoLeitura(
    val idempotencyKey: String = "",
    val palletCodigo: String? = null,
    val itemId: Long = 0,
    val codigoLido: String = "",
    val lidoEm: String? = null,
)
data class PedidoJustificativa(val justificativa: String = "")
data class PedidoConclusao(val confirmarDivergencias: Boolean = false)
data class PedidoUsuario(
    val login: String = "",
    val nome: String = "",
    val senha: String = "",
    val perfil: String = "SEPARADOR",
    val multiItem: Boolean = false,
    val ativo: Boolean = true,
)
data class PedidoSenha(val senha: String = "")
data class PedidoVincular(val produtoId: Long = 0)
data class PedidoCodigoBarras(val codigo: String = "")
data class PedidoUnidades(val unidadesPorCaixa: Int = 0)
data class PedidoExpedicao(val notaIds: List<Long> = emptyList(), val observacao: String? = null)
data class PedidoEstorno(val motivo: String = "")
data class PedidoReentrega(
    val notaId: Long = 0,
    val palletCodigos: List<String> = emptyList(),
    val motivo: String = "",
)
data class PedidoRetorno(
    val notaId: Long = 0,
    val palletCodigos: List<String> = emptyList(),
    val motivo: String = "",
)
data class PedidoLeituraRetorno(
    val idempotencyKey: String = "",
    val retornoId: Long = 0,
    val palletCodigo: String = "",
    val codigoLido: String = "",
    val lidoEm: String? = null,
)
data class PedidoFecharRetorno(val justificativa: String? = null)
data class PedidoObservacaoRetorno(val observacao: String? = null)
data class PedidoTratativa(val tipo: String = "", val nfs: List<String>? = null, val observacao: String? = null)
data class PedidoBaixaDevolucao(
    val tipo: String = "",
    val observacao: String? = null,
    val nfRevenda: String? = null,
)
data class PedidoConferencia(
    val idempotencyKey: String = "",
    val itemDevolucaoId: Long = 0,
    val modo: String = "BIPE",
    val codigoLido: String? = null,
    val qtdUn: Int? = null,
    val palletCodigo: String? = null,
    val lidoEm: String? = null,
)
data class PedidoFecharItemDevolucao(val justificativa: String? = null)
data class PedidoFusao(val origem: String = "", val destino: String = "")
data class PedidoTransferenciaItem(val itemId: Long = 0, val quantidade: Int = 0)
data class PedidoTransferencia(
    val palletOrigem: String = "",
    val itens: List<PedidoTransferenciaItem> = emptyList(),
    val justificativa: String? = null,
    val impressoraId: Long? = null,
)
data class PedidoEnderecar(val palletCodigo: String = "", val posicaoId: Long = 0)
data class PedidoImpressora(
    val nome: String = "",
    val ip: String = "",
    val porta: Int = 9100,
    val ativo: Boolean = true,
)
data class PedidoExclusao(
    val palletCodigo: String = "",
    val itemId: Long = 0,
    val quantidade: Int = 0,
    val justificativa: String? = null,
)

/** Só para os campos que chegam como texto dentro de multipart. */
private val JSON = jacksonObjectMapper()

fun Route.rotas(
    usuarios: UsuarioServico,
    produtos: ProdutoServico,
    atualizacao: AtualizacaoServico,
    movimentacao: MovimentacaoServico,
    expedicao: ExpedicaoServico,
    devolucoes: DevolucaoServico,
    estoque: EstoqueServico,
    relatorios: RelatorioServico,
    instrucoes: InstrucaoServico,
    separacao: SeparacaoServico,
    desembarques: DesembarqueServico,
    impressoras: ImpressoraServico,
    enderecamento: EnderecamentoServico,
    sessoes: Sessoes,
    retornos: RetornoServico,
    bancoOk: () -> Boolean,
) {
    val zona = ZoneId.of("America/Sao_Paulo")

    route("/api/v1") {

        get("/saude") {
            call.respond(
                Saude(
                    versao = VERSAO,
                    horaServidor = DateTimeFormatter.ISO_OFFSET_DATE_TIME
                        .format(Instant.now().atZone(zona)),
                    bancoOk = bancoOk(),
                    versaoMinimaApp = VERSAO_MINIMA_APP,
                    senhaPadraoPendente = usuarios.adminComSenhaPadrao(),
                    enderecoLocal = Rede.enderecoLocal(),
                    porta = call.request.local.localPort,
                    restauracao = br.com.cddemo.redespacho.persistencia.Subida.aviso,
                    backup = br.com.cddemo.redespacho.persistencia.Subida.avisoBackup,
                )
            )
        }

        post("/auth/login") {
            val p = call.receive<PedidoLogin>()
            if (p.login.isBlank() || p.senha.isBlank()) throw Erros.credenciaisInvalidas()
            val u = usuarios.autenticar(p.login, p.senha)
            val token = sessoes.abrir(u, p.dispositivoId)
            call.respond(
                RespostaLogin(
                    token = token,
                    usuario = UsuarioResposta(u.id, u.login, u.nome, u.perfil.name, u.multiItem, u.impressoraId),
                )
            )
        }

        post("/auth/logout") {
            sessoes.encerrar(call.tokenDoCabecalho())
            call.respond(HttpStatusCode.NoContent)
        }

        // ------------------------------------------------------------ instruções

        get("/instrucoes") {
            autenticar(sessoes)
            val status = call.request.queryParameters["status"]
                ?.split(",")?.map { it.trim().uppercase() }?.filter { it.isNotEmpty() }
                ?: listOf("ABERTA", "EM_SEPARACAO")
            call.respond(instrucoes.listar(status))
        }

        post("/instrucoes") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(HttpStatusCode.Created, instrucoes.criar(s.usuario.id))
        }

        get("/instrucoes/{id}") {
            autenticar(sessoes)
            val id = call.idDaRota()
            call.respond(instrucoes.buscar(id) ?: throw Erros.naoEncontrado("Instrução"))
        }

        post("/instrucoes/{id}/xmls/previa") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val arquivos = call.receberXmls()
            call.respond(instrucoes.previa(call.idDaRota(), arquivos).map { it.paraResposta() })
        }

        post("/instrucoes/{id}/xmls") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val arquivos = call.receberXmls()
            val r = instrucoes.importar(call.idDaRota(), s.usuario.id, arquivos)
            call.respond(HttpStatusCode.Created, r.map { it.paraResposta() })
        }

        post("/instrucoes/{id}/concluir") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = runCatching { call.receive<PedidoConclusao>() }.getOrElse { PedidoConclusao() }
            call.respond(instrucoes.concluir(call.idDaRota(), s.usuario.id, p.confirmarDivergencias))
        }

        get("/instrucoes/{id}/divergencias") {
            autenticar(sessoes)
            call.respond(instrucoes.divergencias(call.idDaRota()))
        }

        delete("/instrucoes/{id}") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            instrucoes.excluirInstrucao(call.idDaRota())
            call.respond(HttpStatusCode.NoContent)
        }

        delete("/notas/{id}") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            instrucoes.excluirNota(call.idDaRota())
            call.respond(HttpStatusCode.NoContent)
        }

        get("/instrucoes/{id}/notas") {
            autenticar(sessoes)
            call.respond(separacao.notasDaInstrucao(call.idDaRota()))
        }

        get("/notas") {
            autenticar(sessoes)
            val status = call.request.queryParameters["status"]
                ?.split(",")?.map { it.trim().uppercase() }?.filter { it.isNotEmpty() }
            call.respond(separacao.buscarNotas(status, call.request.queryParameters["numero"]))
        }

        get("/notas/{id}/itens") {
            autenticar(sessoes)
            call.respond(separacao.itensDaNota(call.idDaRota()))
        }

        // --------------------------------------------------------------- produtos

        get("/produtos") {
            autenticar(sessoes)
            val limite = call.request.queryParameters["limite"]?.toIntOrNull()?.coerceIn(1, 2000)
            call.respond(
                produtos.listar(call.request.queryParameters["busca"], limite ?: 50),
            )
        }

        post("/produtos/{id}/codigos-barras") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoCodigoBarras>()
            call.respond(produtos.adicionarCodigoBarras(call.idDaRota(), p.codigo, s.usuario.id))
        }

        // Reimpressão da etiqueta 2ª via (80 × 50 mm) de qualquer produto: só o
        // usuário admin — o login, não o perfil.
        post("/produtos/{id}/etiqueta") {
            val s = autenticar(sessoes)
            if (!s.usuario.login.equals("admin", ignoreCase = true)) throw Erros.semPermissao()
            val p = runCatching { call.receive<PedidoEtiquetaProduto>() }.getOrElse { PedidoEtiquetaProduto() }
            if (p.copias !in 1..200) throw Erros.invalido("COPIAS_INVALIDAS", "Imprima de 1 a 200 etiquetas")
            val e = devolucoes.etiquetaDoProduto(s.usuario.id, call.idDaRota())
            val impressao = impressoras.imprimirEtiquetasProduto(
                p.impressoraId ?: s.usuario.impressoraId, e.codigoBarras, e.produtoCodigo, e.produtoDescricao, p.copias,
            )
            call.respond(mapOf("etiqueta" to e, "copias" to p.copias, "impressao" to impressao))
        }

        delete("/codigos-barras/{id}") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            produtos.desativarCodigoBarras(call.idDaRota())
            call.respond(HttpStatusCode.NoContent)
        }

        put("/produtos/{id}/unidades-por-caixa") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoUnidades>()
            call.respond(produtos.definirUnidadesPorCaixa(call.idDaRota(), p.unidadesPorCaixa))
        }

        get("/codigos-nao-identificados") {
            autenticar(sessoes)
            call.respond(produtos.pendentes())
        }

        get("/codigos-nao-identificados/total") {
            autenticar(sessoes)
            call.respond(mapOf("total" to produtos.quantidadePendente()))
        }

        post("/codigos-nao-identificados/{id}/vincular") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoVincular>()
            call.respond(produtos.vincular(call.idDaRota(), p.produtoId, s.usuario.id))
        }

        post("/codigos-nao-identificados/{id}/descartar") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            produtos.descartarPendente(call.idDaRota(), s.usuario.id)
            call.respond(HttpStatusCode.NoContent)
        }

        // --------------------------------------------------------------- usuários

        get("/usuarios") {
            autenticar(sessoes, Perfil.ADMIN)
            call.respond(usuarios.listar())
        }

        post("/usuarios") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoUsuario>()
            usuarios.criar(p.login, p.nome, p.senha, perfilDe(p.perfil), p.multiItem, s.usuario.id)
            call.respond(HttpStatusCode.Created, usuarios.listar().first { it.login == p.login.trim().lowercase() })
        }

        put("/usuarios/{id}") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoUsuario>()
            val alvo = call.idDaRota()
            val r = usuarios.atualizar(alvo, p.nome, perfilDe(p.perfil), p.multiItem, p.ativo, s.usuario.id)
            // Inativado, o usuário não pode continuar operando com a sessão aberta.
            if (!p.ativo) sessoes.encerrarDoUsuario(alvo)
            call.respond(r)
        }

        post("/usuarios/{id}/senha") {
            autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoSenha>()
            usuarios.redefinirSenha(call.idDaRota(), p.senha)
            call.respond(HttpStatusCode.NoContent)
        }

        // ----------------------------------------------------- atualização do app

        // Sem autenticação: o StageNow da Zebra baixa o APK antes de existir
        // usuário configurado no aparelho, e não sabe mandar token. A rede é
        // local e fechada, e o APK não é segredo. Ver docs/OPERACAO.md §3.
        // QR com o endereço do servidor: o coletor lê na tela de configuração
        // em vez de digitar IP à mão em cada aparelho.
        get("/app/qrcode") {
            val destino = call.request.queryParameters["url"]
                ?: "${call.request.local.scheme}://${call.request.local.serverHost}:" +
                   "${call.request.local.serverPort}"
            call.respondBytes(QrCode.png(destino), ContentType.Image.PNG)
        }

        get("/app/versao") {
            call.respond(atualizacao.versao())
        }

        get("/app/apk") {
            val arquivo = atualizacao.apk().toFile()
            call.response.header(
                HttpHeaders.ContentDisposition,
                "attachment; filename=\"separacao-redespacho.apk\"",
            )
            call.respondFile(arquivo)
        }

        post("/app/apk") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            var bytes: ByteArray? = null
            var versao = ""
            var obrigatoria = false
            call.receiveMultipart().forEachPart { parte ->
                when (parte) {
                    is PartData.FileItem ->
                        bytes = parte.provider().readRemaining().readByteArray()
                    is PartData.FormItem -> when (parte.name) {
                        "versao" -> versao = parte.value
                        "obrigatoria" -> obrigatoria = parte.value == "true"
                    }
                    else -> {}
                }
                parte.dispose()
            }
            val conteudo = bytes
                ?: throw Erros.invalido("NENHUM_ARQUIVO", "Envie o arquivo .apk")
            call.respond(atualizacao.publicar(conteudo, versao, obrigatoria, s.usuario.nome))
        }

        // ------------------------------------------------------------ relatórios

        get("/relatorios/separacao") {
            autenticar(sessoes)
            val f = call.filtroDeRelatorio()
            if (call.request.queryParameters["formato"] == "csv") {
                call.respondCsv("separacao", relatorios.separacaoCsv(f))
            } else {
                call.respond(relatorios.separacao(f))
            }
        }

        get("/relatorios/exclusoes") {
            autenticar(sessoes)
            val f = call.filtroDeRelatorio()
            if (call.request.queryParameters["formato"] == "csv") {
                call.respondCsv("exclusoes", relatorios.exclusoesCsv(f))
            } else {
                call.respond(relatorios.exclusoes(f))
            }
        }

        get("/relatorios/produtividade") {
            autenticar(sessoes)
            call.respond(relatorios.produtividade(call.filtroDeRelatorio()))
        }

        // ------------------------------------------------------------- expedição

        get("/expedicoes/pendentes") {
            autenticar(sessoes)
            call.respond(expedicao.pendentes())
        }

        get("/expedicoes") {
            autenticar(sessoes)
            call.respond(expedicao.historico())
        }

        post("/expedicoes/previa") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoExpedicao>()
            call.respond(expedicao.previa(p.notaIds))
        }

        post("/expedicoes") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoExpedicao>()
            call.respond(expedicao.expedir(s.usuario.id, p.notaIds, p.observacao))
        }

        post("/expedicoes/{id}/estornar") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoEstorno>()
            call.respond(expedicao.estornar(s.usuario.id, call.idDaRota(), p.motivo))
        }

        // -------------------------------------------------------------- estoque

        get("/estoque") {
            autenticar(sessoes)
            val q = call.request.queryParameters
            call.respond(
                estoque.consultar(
                    FiltroEstoque(
                        situacao = q["situacao"]?.uppercase() ?: "EM_ESTOQUE",
                        nota = q["nota"],
                        cliente = q["cliente"],
                        box = q["box"],
                        tipo = q["tipo"],
                    ),
                ),
            )
        }

        get("/estoque/{codigo}/itens") {
            autenticar(sessoes)
            call.respond(estoque.itens(call.codigoDaRota()))
        }

        // ------------------------------------------------------------ reentrega

        get("/notas/{id}/expedidos") {
            autenticar(sessoes)
            call.respond(expedicao.expedidos(call.idDaRota()))
        }

        post("/reentregas") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoReentrega>()
            call.respond(expedicao.reentregar(s.usuario.id, p.notaId, p.palletCodigos, p.motivo))
        }

        // -------------------------------------------------------------- retorno
        //
        // NF expedida que voltou: registro, conferência caixa a caixa,
        // tratativa. Ver RetornoServico e docs/TELAS-RETAGUARDA.md §9.2.

        get("/retornos") {
            autenticar(sessoes)
            val status = call.request.queryParameters["status"]
                ?.split(",")?.map { it.trim().uppercase() }?.filter { it.isNotEmpty() }
            call.respond(retornos.listar(status))
        }

        get("/retornos/{id}") {
            autenticar(sessoes)
            call.respond(retornos.detalhe(call.idDaRota()))
        }

        post("/retornos") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoRetorno>()
            call.respond(HttpStatusCode.Created, retornos.registrar(s.usuario.id, p.notaId, p.palletCodigos, p.motivo))
        }

        post("/retornos/leituras") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoLeituraRetorno>()
            call.respond(
                retornos.registrarLeitura(s.usuario.id, p.idempotencyKey, p.retornoId, p.palletCodigo, p.codigoLido, p.lidoEm),
            )
        }

        post("/retornos/pallets/{id}/fechar") {
            val s = autenticar(sessoes)
            val p = runCatching { call.receive<PedidoFecharRetorno>() }.getOrElse { PedidoFecharRetorno() }
            call.respond(retornos.fecharPallet(s.usuario.id, call.idDaRota(), p.justificativa))
        }

        post("/retornos/pallets/{id}/desfazer-ultima") {
            val s = autenticar(sessoes)
            call.respond(retornos.desfazerUltima(s.usuario.id, call.idDaRota()))
        }

        post("/retornos/pallets/{id}/reabrir") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(retornos.reabrirPallet(s.usuario.id, call.idDaRota()))
        }

        post("/retornos/{id}/completo") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = runCatching { call.receive<PedidoObservacaoRetorno>() }.getOrElse { PedidoObservacaoRetorno() }
            call.respond(retornos.marcarCompleto(s.usuario.id, call.idDaRota(), p.observacao))
        }

        post("/retornos/{id}/tratativa") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoTratativa>()
            call.respond(retornos.tratar(s.usuario.id, call.idDaRota(), p.tipo, p.nfs, p.observacao))
        }

        post("/retornos/{id}/desfazer-tratativa") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = runCatching { call.receive<PedidoFecharRetorno>() }.getOrElse { PedidoFecharRetorno() }
            call.respond(retornos.desfazerTratativa(s.usuario.id, call.idDaRota(), p.justificativa.orEmpty()))
        }

        post("/retornos/{id}/cancelar") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = runCatching { call.receive<PedidoFecharRetorno>() }.getOrElse { PedidoFecharRetorno() }
            call.respond(retornos.cancelar(s.usuario.id, call.idDaRota(), p.justificativa.orEmpty()))
        }

        // ------------------------------------------------------------ devolução

        get("/devolucoes") {
            autenticar(sessoes)
            val status = call.request.queryParameters["status"]
                ?.split(",")?.map { it.trim().uppercase() }?.filter { it.isNotEmpty() }
            call.respond(devolucoes.listar(status))
        }

        get("/devolucoes/{id}/itens") {
            autenticar(sessoes)
            call.respond(devolucoes.itens(call.idDaRota()))
        }

        // A NF de origem vem de um de três jeitos: notaOrigemId (NF do sistema),
        // arquivoOrigem (XML dela) ou origemNumero (só digitado).
        post("/devolucoes/previa") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val (arquivos, campos) = call.receberArquivosComCampos()
            call.respond(devolucoes.previa(arquivos.xmlDaDevolucao(), origemDoPedido(arquivos, campos)))
        }

        post("/devolucoes") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val (arquivos, campos) = call.receberArquivosComCampos()
            val vinculos = campos["vinculos"]?.let { json ->
                JSON.readValue(json, object : TypeReference<List<VinculoItem>>() {})
            } ?: emptyList()
            call.respond(
                HttpStatusCode.Created,
                devolucoes.importar(
                    s.usuario.id, arquivos.xmlDaDevolucao(),
                    origemDoPedido(arquivos, campos) ?: OrigemDevolucao(), vinculos,
                    motivo = campos["motivo"],
                ),
            )
        }

        post("/devolucoes/conferencia") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoConferencia>()
            if (p.idempotencyKey.isBlank()) {
                throw Erros.invalido("IDEMPOTENCY_KEY_OBRIGATORIA", "Leitura sem chave de idempotência")
            }
            call.respond(
                devolucoes.conferir(
                    s.usuario.id, p.idempotencyKey, p.itemDevolucaoId, p.modo,
                    p.codigoLido, p.qtdUn, p.palletCodigo, p.lidoEm,
                ),
            )
        }

        post("/devolucoes/itens/{id}/fechar") {
            val s = autenticar(sessoes)
            val p = runCatching { call.receive<PedidoFecharItemDevolucao>() }
                .getOrElse { PedidoFecharItemDevolucao() }
            call.respond(devolucoes.fecharItem(s.usuario.id, call.idDaRota(), p.justificativa))
        }

        post("/devolucoes/{id}/baixar") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoBaixaDevolucao>()
            call.respond(
                devolucoes.baixar(s.usuario.id, call.idDaRota(), p.tipo, p.observacao, p.nfRevenda),
            )
        }

        // Devolução lançada por engano. Baixada não se apaga; com conferência,
        // só com comConferencia = true (as leituras vão junto).
        post("/devolucoes/{id}/apagar") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoApagarDevolucao>()
            call.respond(devolucoes.apagar(s.usuario.id, call.idDaRota(), p.justificativa, p.comConferencia))
        }

        // Produto trocado: veio outro produto no lugar do declarado na NF do cliente.
        post("/devolucoes/itens/{id}/troca") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoTrocaDevolucao>()
            call.respond(
                devolucoes.trocarProduto(
                    s.usuario.id, call.idDaRota(), p.produtoId, p.quantidade, p.unidade, p.itemTodo, p.justificativa,
                ),
            )
        }

        post("/devolucoes/itens/{id}/desfazer-troca") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(devolucoes.desfazerTroca(s.usuario.id, call.idDaRota()))
        }

        // Etiqueta 2ª via do produto do item: o GTIN que ele já tem ou um
        // código interno gravado nele, para a caixa sem código poder ser bipada.
        post("/devolucoes/itens/{id}/etiqueta") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = runCatching { call.receive<PedidoEtiquetaProduto>() }.getOrElse { PedidoEtiquetaProduto() }
            if (p.copias !in 1..200) throw Erros.invalido("COPIAS_INVALIDAS", "Imprima de 1 a 200 etiquetas")
            val e = devolucoes.etiquetaDoItem(s.usuario.id, call.idDaRota())
            val impressao = impressoras.imprimirEtiquetasProduto(
                p.impressoraId ?: s.usuario.impressoraId, e.codigoBarras, e.produtoCodigo, e.produtoDescricao, p.copias,
            )
            call.respond(mapOf("etiqueta" to e, "copias" to p.copias, "impressao" to impressao))
        }

        // Conferência de estoque: a sobra entra como devolução sem NF.
        post("/devolucoes/sobra") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(HttpStatusCode.Created, devolucoes.criarSobra(s.usuario.id))
        }

        post("/devolucoes/{id}/sobra/leitura") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoSobra>()
            if (p.idempotencyKey.isBlank()) {
                throw Erros.invalido("IDEMPOTENCY_KEY_OBRIGATORIA", "Leitura sem chave de idempotência")
            }
            call.respond(
                devolucoes.biparSobra(
                    s.usuario.id, call.idDaRota(), p.idempotencyKey, p.codigoLido, p.itemId, p.caixas, p.palletCodigo,
                ),
            )
        }

        // Fecha a conferência de estoque sem baixar: a sobra continua no
        // estoque, mas sai do coletor e da fila de pendências.
        post("/devolucoes/{id}/conferir") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(devolucoes.fecharConferencia(s.usuario.id, call.idDaRota()))
        }

        post("/devolucoes/{id}/reabrir") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(devolucoes.reabrirConferencia(s.usuario.id, call.idDaRota()))
        }

        // Estorno da baixa: a devolução volta ao estoque. Só o admin.
        post("/devolucoes/{id}/estornar-baixa") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoJustificativa>()
            call.respond(devolucoes.estornarBaixa(s.usuario.id, call.idDaRota(), p.justificativa))
        }

        // Baixa de parte da sobra: N caixas de um produto saem; o resto fica.
        post("/devolucoes/itens/{id}/baixa-sobra") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoBaixaSobra>()
            call.respond(
                devolucoes.baixarSobraParcial(
                    s.usuario.id, call.idDaRota(), p.caixas, p.tipo, p.observacao, p.nfRevenda,
                ),
            )
        }

        // Cadastro de motivos de devolução: a lista para escolher, padronizada.
        get("/motivos-devolucao") {
            autenticar(sessoes)
            call.respond(devolucoes.motivos(todos = call.request.queryParameters["todos"] == "1"))
        }

        post("/motivos-devolucao") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoMotivoCadastro>()
            call.respond(HttpStatusCode.Created, devolucoes.criarMotivo(s.usuario.id, p.descricao))
        }

        put("/motivos-devolucao/{id}") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoMotivoCadastro>()
            call.respond(devolucoes.atualizarMotivo(call.idDaRota(), p.descricao, p.ativo))
        }

        // Motivo da devolução: na NF (vale para todos os itens) e item a item.
        post("/devolucoes/{id}/motivo") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoMotivo>()
            call.respond(devolucoes.definirMotivo(s.usuario.id, call.idDaRota(), p.motivo))
        }

        post("/devolucoes/itens/{id}/motivo") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoMotivo>()
            call.respond(devolucoes.definirMotivoDoItem(s.usuario.id, call.idDaRota(), p.motivo))
        }

        // Todas as devoluções numa planilha, um item por linha.
        get("/devolucoes/planilha") {
            autenticar(sessoes)
            val (nome, bytes) = devolucoes.planilhaGeral()
            call.response.header(HttpHeaders.ContentDisposition, "attachment; filename=\"$nome\"")
            call.respondBytes(bytes, ContentType.parse("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        }

        // Planilha da devolução, um item por linha, para mandar por e-mail.
        get("/devolucoes/{id}/planilha") {
            autenticar(sessoes)
            val (nome, bytes) = devolucoes.planilha(call.idDaRota())
            call.response.header(HttpHeaders.ContentDisposition, "attachment; filename=\"$nome\"")
            call.respondBytes(bytes, ContentType.parse("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        }

        // Caixas conferidas: a lista, a exclusão e a troca de pallet.
        get("/devolucoes/{id}/leituras") {
            autenticar(sessoes)
            call.respond(devolucoes.leituras(call.idDaRota()))
        }

        post("/devolucoes/{id}/leituras/excluir") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoCaixasDevolucao>()
            call.respond(devolucoes.excluirLeituras(s.usuario.id, call.idDaRota(), p.leituraIds, p.justificativa))
        }

        post("/devolucoes/{id}/leituras/pallet") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoCaixasDevolucao>()
            call.respond(devolucoes.atribuirPallet(s.usuario.id, call.idDaRota(), p.leituraIds, p.palletCodigo))
        }

        // -------------------------------------------------------- endereçamento

        get("/pallets/{codigo}/enderecamento") {
            autenticar(sessoes)
            call.respond(enderecamento.palletParaEnderecar(call.codigoDaRota()))
        }

        get("/boxes/{codigo}/posicoes") {
            autenticar(sessoes)
            // O pallet vem por query para o box já dizer quais posições
            // ocupadas ele pode dividir.
            val pallet = call.request.queryParameters["pallet"]
            call.respond(enderecamento.box(call.codigoDaRota(), pallet))
        }

        get("/enderecamentos/nota/{numero}") {
            autenticar(sessoes)
            val numero = call.parameters["numero"]?.takeIf { it.isNotBlank() }
                ?: throw Erros.naoEncontrado("NF")
            call.respond(enderecamento.roteiroDaNota(numero))
        }

        get("/enderecamentos/pendentes") {
            autenticar(sessoes)
            call.respond(enderecamento.pendentesDeEnderecamento())
        }

        post("/enderecamentos") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoEnderecar>()
            call.respond(enderecamento.enderecar(s.usuario.id, p.palletCodigo, p.posicaoId))
        }

        get("/boxes/total") {
            autenticar(sessoes)
            call.respond(mapOf("total" to enderecamento.totalBoxes()))
        }

        post("/boxes/importar") {
            autenticar(sessoes, Perfil.ADMIN)
            val csv = call.receberTexto()
            call.respond(enderecamento.importarBoxes(csv))
        }

        // Cadastro de endereços em lote: baixa a planilha, preenche no Excel e sobe.
        get("/boxes") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(enderecamento.listarBoxes())
        }

        get("/boxes/planilha") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val comAtuais = call.request.queryParameters["atuais"] == "1"
            val nome = if (comAtuais) "enderecos-cadastrados.xlsx" else "modelo-enderecos.xlsx"
            call.response.header(HttpHeaders.ContentDisposition, "attachment; filename=\"$nome\"")
            call.respondBytes(
                enderecamento.planilhaDeBoxes(comAtuais),
                ContentType.parse("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            )
        }

        post("/boxes/planilha/previa") {
            autenticar(sessoes, Perfil.ADMIN)
            val (arquivos, _) = call.receberArquivosComCampos()
            call.respond(enderecamento.previaPlanilhaBoxes(arquivos.planilha()))
        }

        post("/boxes/planilha") {
            autenticar(sessoes, Perfil.ADMIN)
            val (arquivos, _) = call.receberArquivosComCampos()
            call.respond(enderecamento.aplicarPlanilhaBoxes(arquivos.planilha()))
        }

        // ------------------------------------------------------------ impressoras

        get("/impressoras") {
            autenticar(sessoes)
            call.respond(impressoras.listar())
        }

        post("/impressoras") {
            autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoImpressora>()
            call.respond(HttpStatusCode.Created, impressoras.criar(p.nome, p.ip, p.porta))
        }

        put("/impressoras/{id}") {
            autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoImpressora>()
            call.respond(impressoras.atualizar(call.idDaRota(), p.nome, p.ip, p.porta, p.ativo))
        }

        post("/impressoras/{id}/testar") {
            autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(impressoras.testar(call.idDaRota()))
        }

        // ---------------------------------------------------------------- pallets

        post("/pallets") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoPallets>()
            val codigos = separacao.criarPallets(s.usuario.id, p.tipo, p.quantidade).map { it.codigo }
            // Falha de impressão não derruba a criação: o pallet existe e pode
            // ser reimpresso. A porta 9100 nunca confirma que o papel saiu.
            val impressao = impressoras.imprimirPallets(p.impressoraId ?: s.usuario.impressoraId, codigos)
            call.respond(HttpStatusCode.Created, mapOf("pallets" to codigos, "impressao" to impressao))
        }

        post("/pallets/{codigo}/reimprimir") {
            val s = autenticar(sessoes)
            val codigo = call.codigoDaRota()
            call.respond(
                mapOf("impressao" to impressoras.imprimirPallets(s.usuario.impressoraId, listOf(codigo)))
            )
        }

        post("/pallets/{codigo}/cartao") {
            val s = autenticar(sessoes)
            call.respond(mapOf("impressao" to imprimirCartao(separacao, impressoras, s, call.codigoDaRota())))
        }

        post("/pallets/{codigo}/selecionar") {
            val s = autenticar(sessoes)
            val p = runCatching { call.receive<PedidoSelecionarPallet>() }.getOrElse { PedidoSelecionarPallet() }
            call.respond(separacao.selecionarPallet(s.usuario.id, call.codigoDaRota(), p.notaId))
        }

        post("/pallets/{codigo}/encerrar") {
            val s = autenticar(sessoes)
            val codigo = call.codigoDaRota()
            // O cartão é montado antes de encerrar: depois o pallet sai do
            // vínculo do usuário, mas o conteúdo continua o mesmo.
            val impressao = imprimirCartao(separacao, impressoras, s, codigo)
            val p = separacao.encerrarPallet(s.usuario.id, codigo)
            call.respond(mapOf("pallet" to p, "impressao" to impressao))
        }

        get("/pallets/{codigo}/conteudo") {
            autenticar(sessoes)
            call.respond(separacao.conteudoDoPallet(call.codigoDaRota()))
        }

        // ------------------------------------------------- fusão e repaletização

        get("/pallets/{codigo}/conteudo-detalhado") {
            autenticar(sessoes)
            call.respond(movimentacao.conteudo(call.codigoDaRota()))
        }

        // Consultar pallet (coletor): de qual NF é e onde estão os outros dela.
        get("/pallets/{codigo}/consulta") {
            autenticar(sessoes)
            call.respond(movimentacao.consultar(call.codigoDaRota()))
        }

        post("/pallets/fusao") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoFusao>()
            val r = movimentacao.fundir(s.usuario.id, p.origem, p.destino)
            // O conteúdo mudou; sem reimprimir, o cartão do destino passa a
            // mentir sobre a quantidade.
            r.cartoesParaImprimir.forEach {
                imprimirCartao(separacao, impressoras, s, it)
            }
            call.respond(r)
        }

        post("/pallets/transferencia") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoTransferencia>()
            val r = movimentacao.transferir(
                s.usuario.id, p.palletOrigem,
                p.itens.map { TransferenciaItem(it.itemId, it.quantidade) },
                p.justificativa,
            )
            r.cartoesParaImprimir.forEach {
                imprimirCartao(separacao, impressoras, s, it)
            }
            call.respond(r)
        }

        // Repaletização: bipa a origem, o destino (mesma NF, ou pallet novo) e
        // as caixas, uma a uma. Qualquer usuário, como a fusão: é trabalho de
        // chão, no coletor, e também da retaguarda.
        post("/pallets/repaletizacao/abrir") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoRepaletizacao>()
            call.respond(movimentacao.abrirRepaletizacao(s.usuario.id, p.origem, p.destino))
        }

        post("/pallets/repaletizacao/caixa") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoCaixaRepaletizacao>()
            val chave = p.idempotencyKey.ifBlank { call.request.headers["Idempotency-Key"].orEmpty() }
            call.respond(movimentacao.repaletizarCaixa(s.usuario.id, chave, p.origem, p.destino, p.codigoLido))
        }

        post("/pallets/repaletizacao/desfazer") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoRepaletizacao>()
            call.respond(movimentacao.desfazerRepaletizacao(s.usuario.id, p.origem, p.destino))
        }

        post("/pallets/repaletizacao/concluir") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoRepaletizacao>()
            val r = movimentacao.concluirRepaletizacao(s.usuario.id, p.origem, p.destino)
            // Os dois mudaram de conteúdo: cartão velho mente sobre a
            // quantidade. Origem que esvaziou não tem o que imprimir.
            val cartoes = listOfNotNull(r.destino.codigo, r.origem.codigo.takeIf { r.origem.totalCaixas > 0 })
            val impressao = cartoes.associateWith { imprimirCartao(separacao, impressoras, s, it) }
            call.respond(mapOf("repaletizacao" to r, "impressao" to impressao))
        }

        // ---------------------------------------------------------------- leitura

        post("/leituras") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoLeitura>()
            val chave = p.idempotencyKey.ifBlank {
                call.request.headers["Idempotency-Key"].orEmpty()
            }
            if (chave.isBlank()) {
                throw Erros.invalido("IDEMPOTENCY_KEY_AUSENTE", "Leitura exige chave de idempotência")
            }
            call.respond(
                separacao.registrarLeitura(
                    usuarioId = s.usuario.id,
                    multiItem = s.usuario.multiItem,
                    coletorId = s.dispositivoId,
                    idempotencyKey = chave,
                    palletCodigo = p.palletCodigo,
                    itemId = p.itemId,
                    codigoLido = p.codigoLido,
                    lidoEm = p.lidoEm,
                )
            )
        }

        // ------------------------------------------------------------ desembarque
        //
        // O pallet montado por quantidade, na retaguarda, quando a carreta é
        // descarregada de uma vez. Só o admin: é lançamento de caixa sem bipe.

        get("/notas/{id}/desembarque") {
            autenticar(sessoes, Perfil.ADMIN)
            val aberto = desembarques.emAberto(call.idDaRota())
            if (aberto == null) call.respond(HttpStatusCode.NoContent) else call.respond(aberto)
        }

        post("/notas/{id}/desembarque") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoDesembarque>()
            val d = desembarques.abrir(s.usuario.id, call.idDaRota(), p.responsavelId)
            // O pallet é novo e físico: sem a etiqueta ninguém o acha depois.
            val impressao = impressoras.imprimirPallets(
                p.impressoraId ?: s.usuario.impressoraId, listOf(d.palletCodigo),
            )
            call.respond(HttpStatusCode.Created, mapOf("desembarque" to d, "impressao" to impressao))
        }

        post("/desembarques/{id}/itens") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoCaixasDesembarque>()
            call.respond(
                desembarques.lancar(
                    s.usuario.id, call.idDaRota(), p.itens.map { CaixasDoItem(it.itemId, it.caixas) },
                ),
            )
        }

        post("/desembarques/{id}/encerrar") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            val d = desembarques.encerrar(s.usuario.id, call.idDaRota())
            val impressao = imprimirCartao(separacao, impressoras, s, d.palletCodigo)
            call.respond(mapOf("desembarque" to d, "impressao" to impressao))
        }

        post("/desembarques/{id}/cancelar") {
            val s = autenticar(sessoes, Perfil.ADMIN)
            val p = call.receive<PedidoJustificativa>()
            call.respond(desembarques.cancelar(s.usuario.id, call.idDaRota(), p.justificativa))
        }

        post("/itens/{id}/fechar") {
            val s = autenticar(sessoes)
            call.respond(separacao.fecharItem(s.usuario.id, call.idDaRota()))
        }

        post("/itens/{id}/fechar-divergencia") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            val p = call.receive<PedidoJustificativa>()
            call.respond(separacao.fecharComDivergencia(s.usuario.id, call.idDaRota(), p.justificativa))
        }

        post("/itens/{id}/reabrir") {
            val s = autenticar(sessoes, Perfil.SUPERVISOR, Perfil.ADMIN)
            call.respond(separacao.reabrirItem(s.usuario.id, call.idDaRota()))
        }

        get("/itens/{id}/pallets") {
            autenticar(sessoes)
            call.respond(separacao.palletsDoItem(call.idDaRota()))
        }

        post("/separacao/excluir") {
            val s = autenticar(sessoes)
            val p = call.receive<PedidoExclusao>()
            call.respond(
                separacao.excluirSeparacao(
                    s.usuario.id, p.palletCodigo, p.itemId, p.quantidade, p.justificativa,
                )
            )
        }
    }
}

// --- apoio ------------------------------------------------------------------

private fun ApplicationCall.tokenDoCabecalho(): String? =
    request.headers[HttpHeaders.Authorization]
        ?.takeIf { it.startsWith("Bearer ", ignoreCase = true) }
        ?.substring(7)?.trim()

/**
 * Perfil é verificado no servidor, não apenas escondendo botão na tela.
 * Ver docs/API.md §6.
 */
private fun RoutingContext.autenticar(
    sessoes: Sessoes,
    vararg perfisPermitidos: Perfil,
): Sessao {
    val s = sessoes.validar(call.tokenDoCabecalho()) ?: throw Erros.naoAutenticado()

    // Trava central do perfil CONSULTA: nada que não seja leitura passa, sem
    // depender de cada rota nova lembrar de se proteger.
    if (s.usuario.perfil == Perfil.CONSULTA && call.request.local.method != HttpMethod.Get) {
        throw Erros.semPermissao()
    }

    if (perfisPermitidos.isNotEmpty() && s.usuario.perfil !in perfisPermitidos) {
        throw Erros.semPermissao()
    }
    return s
}

private fun imprimirCartao(
    separacao: SeparacaoServico,
    impressoras: ImpressoraServico,
    s: Sessao,
    codigo: String,
): String {
    val d = separacao.dadosDoCartao(codigo) ?: return "SEM_CONTEUDO"
    return impressoras.imprimirCartao(
        s.usuario.impressoraId,
        Zpl.Cartao(
            palletCodigo = d.palletCodigo,
            instrucao = d.instrucao,
            notaNumero = d.notaNumero,
            cliente = d.cliente,
            endereco = d.endereco,
            complemento = d.complemento,
            totalCaixas = d.totalCaixas,
            itens = d.itens.map { Zpl.LinhaCartao(it.codigo, it.descricao, it.quantidade) },
            // Quem separou e quando: numa segunda via, o cartão continua
            // dizendo quem encerrou o pallet, não quem pediu a reimpressão.
            // Pallet ainda aberto (o cartão do encerrar sai antes do UPDATE) é agora e é quem pediu.
            usuario = d.encerradoPor ?: s.usuario.nome,
            em = d.encerradoEm?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: Instant.now(),
            bonificacao = d.bonificacao,
            bairro = d.bairro,
            cidade = d.cidade,
            cep = d.cep,
        ),
    )
}

data class PedidoApagarDevolucao(val justificativa: String = "", val comConferencia: Boolean = false)

data class PedidoDesembarque(
    val responsavelId: Long = 0,
    val impressoraId: Long? = null,
)

data class ItemDesembarquePedido(val itemId: Long = 0, val caixas: Int = 0)

data class PedidoCaixasDesembarque(val itens: List<ItemDesembarquePedido> = emptyList())

data class PedidoBaixaSobra(
    val caixas: Int = 0,
    val tipo: String = "",
    val observacao: String? = null,
    val nfRevenda: String? = null,
)

data class PedidoSobra(
    val idempotencyKey: String = "",
    val codigoLido: String? = null,
    /** Vem do "digitar quantidade": várias caixas do produto que já está na lista. */
    val itemId: Long? = null,
    val caixas: Int = 1,
    val palletCodigo: String? = null,
)

data class PedidoMotivo(val motivo: String? = null)

data class PedidoMotivoCadastro(val descricao: String = "", val ativo: Boolean = true)

data class PedidoEtiquetaProduto(val copias: Int = 1, val impressoraId: Long? = null)

data class PedidoCaixasDevolucao(
    val leituraIds: List<Long> = emptyList(),
    val justificativa: String = "",
    val palletCodigo: String = "",
)

data class PedidoTrocaDevolucao(
    val produtoId: Long = 0,
    val quantidade: Int = 0,
    val unidade: String = "CX",
    val itemTodo: Boolean = true,
    val justificativa: String = "",
)

data class PedidoRepaletizacao(val origem: String, val destino: String)

data class PedidoCaixaRepaletizacao(
    val idempotencyKey: String = "",
    val origem: String,
    val destino: String,
    val codigoLido: String,
    val lidoEm: String? = null,
)

private fun perfilDe(valor: String): Perfil =
    runCatching { Perfil.valueOf(valor.trim().uppercase()) }
        .getOrElse { throw Erros.invalido("PERFIL_INVALIDO", "Perfil inválido: $valor") }

private fun ApplicationCall.filtroDeRelatorio(): FiltroRelatorio {
    val q = request.queryParameters
    return FiltroRelatorio(
        de = q["de"],
        ate = q["ate"],
        usuarioId = q["usuarioId"]?.toLongOrNull(),
        instrucaoId = q["instrucaoId"]?.toLongOrNull(),
        notaNumero = q["nota"],
        produtoCodigo = q["produto"],
    )
}

/** BOM no início: sem ele o Excel abre o CSV com os acentos quebrados. */
private suspend fun ApplicationCall.respondCsv(nome: String, conteudo: String) {
    response.header(
        HttpHeaders.ContentDisposition,
        "attachment; filename=\"$nome-${java.time.LocalDate.now()}.csv\"",
    )
    respondBytes(
        "\uFEFF$conteudo".toByteArray(Charsets.UTF_8),
        ContentType.Text.CSV.withParameter("charset", "utf-8"),
    )
}

private fun ApplicationCall.idDaRota(): Long =
    parameters["id"]?.toLongOrNull() ?: throw Erros.naoEncontrado("Recurso")

private fun ApplicationCall.codigoDaRota(): String =
    parameters["codigo"]?.takeIf { it.isNotBlank() } ?: throw Erros.naoEncontrado("Pallet")

/** Aceita CSV por multipart ou como corpo cru. */
private suspend fun ApplicationCall.receberTexto(): String {
    val tipo = request.headers[HttpHeaders.ContentType].orEmpty()
    if (!tipo.startsWith("multipart/")) return receiveText()

    var conteudo: String? = null
    receiveMultipart().forEachPart { parte ->
        if (parte is PartData.FileItem && conteudo == null) {
            conteudo = parte.provider().readRemaining().readByteArray().toString(Charsets.UTF_8)
        }
        parte.dispose()
    }
    return conteudo ?: throw Erros.invalido("NENHUM_ARQUIVO", "Nenhum arquivo enviado")
}

/**
 * Arquivos pelo nome do campo ("arquivo", "arquivoOrigem") e os campos de
 * texto do formulário (multipart). Arquivo sem nome de campo vale como "arquivo".
 */
private suspend fun ApplicationCall.receberArquivosComCampos(): Pair<Map<String, ByteArray>, Map<String, String>> {
    val arquivos = mutableMapOf<String, ByteArray>()
    val campos = mutableMapOf<String, String>()
    receiveMultipart().forEachPart { parte ->
        when (parte) {
            is PartData.FileItem -> {
                val nome = parte.name?.takeIf { it.isNotBlank() } ?: "arquivo"
                if (nome !in arquivos) arquivos[nome] = parte.provider().readRemaining().readByteArray()
            }
            is PartData.FormItem -> campos[parte.name.orEmpty()] = parte.value
            else -> Unit
        }
        parte.dispose()
    }
    return arquivos to campos
}

private fun Map<String, ByteArray>.planilha(): ByteArray =
    this["arquivo"] ?: values.firstOrNull() ?: throw Erros.invalido("NENHUM_ARQUIVO", "Nenhuma planilha foi enviada")

private fun Map<String, ByteArray>.xmlDaDevolucao(): ByteArray =
    this["arquivo"] ?: throw Erros.invalido("NENHUM_ARQUIVO", "Nenhum arquivo XML foi enviado")

/** A NF de origem como a tela mandou; nula se não veio nenhuma das três. */
private fun origemDoPedido(arquivos: Map<String, ByteArray>, campos: Map<String, String>): OrigemDevolucao? {
    val o = OrigemDevolucao(
        notaId = campos["notaOrigemId"]?.toLongOrNull(),
        xml = arquivos["arquivoOrigem"],
        numero = campos["origemNumero"]?.trim()?.takeIf { it.isNotEmpty() },
    )
    return o.takeIf { it.notaId != null || it.xml != null || it.numero != null }
}

/** Um XML mais os campos de formulário que o acompanham (multipart). */
private suspend fun ApplicationCall.receberXmlComCampos(): Pair<ByteArray, Map<String, String>> {
    var bytes: ByteArray? = null
    val campos = mutableMapOf<String, String>()
    receiveMultipart().forEachPart { parte ->
        when (parte) {
            is PartData.FileItem ->
                if (bytes == null) bytes = parte.provider().readRemaining().readByteArray()
            is PartData.FormItem -> campos[parte.name.orEmpty()] = parte.value
            else -> Unit
        }
        parte.dispose()
    }
    return (bytes ?: throw Erros.invalido("NENHUM_ARQUIVO", "Nenhum arquivo XML foi enviado")) to campos
}

private suspend fun ApplicationCall.receberXmls(): List<ArquivoXml> {
    val arquivos = mutableListOf<ArquivoXml>()
    receiveMultipart().forEachPart { parte ->
        if (parte is PartData.FileItem) {
            arquivos += ArquivoXml(
                nome = parte.originalFileName ?: "sem-nome.xml",
                bytes = parte.provider().readRemaining().readByteArray(),
            )
        }
        parte.dispose()
    }
    if (arquivos.isEmpty()) {
        throw Erros.invalido("NENHUM_ARQUIVO", "Nenhum arquivo XML foi enviado")
    }
    return arquivos
}

private fun PreviaArquivo.paraResposta(): ArquivoPreviaResposta = when (this) {
    is PreviaArquivo.Rejeitado -> ArquivoPreviaResposta(
        arquivo = arquivo,
        situacao = "REJEITADO",
        erro = DetalheErro(codigo, mensagem),
    )

    is PreviaArquivo.Ok -> ArquivoPreviaResposta(
        arquivo = arquivo,
        situacao = if (avisos.isEmpty()) "OK" else "OK_COM_AVISOS",
        nota = NotaPrevia(
            numero = nota.numero,
            serie = nota.serie,
            cliente = nota.destNome,
            qtdItens = itens.size,
            qtdCaixas = qtdCaixas.toBigInteger().toInt(),
            qVolConfere = qVolConfere,
        ),
        avisos = avisos.map { DetalheErro(it.codigo, it.mensagem) },
    )
}
