package br.com.cddemo.redespacho

import br.com.cddemo.redespacho.api.CorpoErro
import br.com.cddemo.redespacho.api.DetalheErro
import br.com.cddemo.redespacho.api.ErroNegocio
import br.com.cddemo.redespacho.api.rotas
import br.com.cddemo.redespacho.persistencia.Backup
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.Presenca
import br.com.cddemo.redespacho.seguranca.Perfil
import br.com.cddemo.redespacho.seguranca.Sessoes
import br.com.cddemo.redespacho.servico.EnderecamentoServico
import br.com.cddemo.redespacho.servico.DesembarqueServico
import br.com.cddemo.redespacho.servico.DevolucaoServico
import br.com.cddemo.redespacho.servico.EstoqueServico
import br.com.cddemo.redespacho.servico.ExpedicaoServico
import br.com.cddemo.redespacho.servico.ImpressoraServico
import br.com.cddemo.redespacho.servico.InstrucaoServico
import br.com.cddemo.redespacho.servico.MovimentacaoServico
import br.com.cddemo.redespacho.servico.AtualizacaoServico
import br.com.cddemo.redespacho.servico.ProdutoServico
import br.com.cddemo.redespacho.servico.RelatorioServico
import br.com.cddemo.redespacho.servico.RetornoServico
import br.com.cddemo.redespacho.servico.SeparacaoServico
import br.com.cddemo.redespacho.servico.UsuarioServico
import com.fasterxml.jackson.databind.SerializationFeature
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule
import com.fasterxml.jackson.module.kotlin.KotlinModule
import io.ktor.http.CacheControl
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.serialization.jackson.jackson
import io.ktor.server.application.Application
import io.ktor.server.application.install
import io.ktor.server.application.log
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty
import io.ktor.server.plugins.BadRequestException
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.plugins.cors.routing.CORS
import io.ktor.server.http.content.staticResources
import io.ktor.server.plugins.statuspages.StatusPages
import io.ktor.server.response.respond
import io.ktor.server.routing.post
import io.ktor.server.routing.routing
import br.com.cddemo.redespacho.persistencia.Restauracao
import br.com.cddemo.redespacho.persistencia.Subida
import org.slf4j.LoggerFactory
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.StandardOpenOption
import io.ktor.server.engine.EmbeddedServer
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import kotlin.system.exitProcess

data class Configuracao(
    val porta: Int = System.getenv("REDESPACHO_PORTA")?.toIntOrNull() ?: 8080,
    val banco: Path = Paths.get(System.getenv("REDESPACHO_BANCO") ?: "dados/redespacho.db"),
)

/** Presa enquanto o processo viver; soltá-la para o GC liberaria a trava. */
@Volatile private var travaDeInstancia: FileLock? = null

/**
 * Um servidor por pasta de dados.
 *
 * A restauração troca o arquivo do banco na subida; com outro processo usando
 * esse arquivo, isso o corromperia. O iniciar.bat já confere pela porta, mas
 * dois cliques rápidos passam os dois pela conferência antes de qualquer um
 * abrir a porta — a trava de arquivo não deixa.
 */
private fun travarInstancia(pastaDados: Path): FileLock? {
    Files.createDirectories(pastaDados)
    val canal = FileChannel.open(
        pastaDados.resolve("servidor.trava"), StandardOpenOption.CREATE, StandardOpenOption.WRITE,
    )
    return canal.tryLock() ?: run { canal.close(); null }
}

fun main() {
    val log = LoggerFactory.getLogger("br.com.cddemo.redespacho.Aplicacao")
    val cfg = Configuracao()
    val pastaDados = cfg.banco.toAbsolutePath().parent ?: Paths.get("dados").toAbsolutePath()

    travaDeInstancia = travarInstancia(pastaDados) ?: run {
        log.error("Já existe um servidor rodando com a pasta {}. Esta subida foi cancelada.", pastaDados)
        exitProcess(2)
    }

    // Antes de abrir o banco: pode ser que ele tenha de vir do backup.
    val inacessivel = System.getenv("REDESPACHO_BACKUP_INACESSIVEL") == "1"
    try {
        val r = Restauracao(cfg.banco, Backup.pastaConfigurada(), inacessivel).executar()
        log.info("Restauração: {}", r.mensagem)
        if (r.avisar) Subida.aviso = r.mensagem
    } catch (e: Exception) {
        log.error("Restauração automática falhou; seguindo com o banco local", e)
        Subida.aviso = "A restauração automática falhou (${e.javaClass.simpleName}: ${e.message}). " +
            "Confira se os dados estão completos antes de operar."
    }

    val db = Db.arquivo(cfg.banco)
    val backup = Backup.de(cfg.banco).also { it.iniciar() }
    val presenca = Presenca.de(cfg.porta).also { it.iniciar() }
    val desligamento = Desligamento(backup, presenca, db)
    // Também quando o Windows encerra a sessão. Sem nada da bandeja aqui: com
    // a JVM saindo, o AWT trava e o gancho nunca chegava ao backup final.
    Runtime.getRuntime().addShutdownHook(Thread { desligamento.executar() })
    val sair: () -> Unit = { desligamento.executar(); exitProcess(0) }

    val servidor = embeddedServer(Netty, port = cfg.porta, host = "0.0.0.0") {
        modulo(db, pastaDados, aoPedirParada = {
            // Responde antes de sair: quem pediu (parar.bat) precisa do OK.
            thread(name = "parada") { Thread.sleep(300); sair() }
        })
    }
    desligamento.servidor = servidor
    try {
        servidor.start(wait = false)
    } catch (e: Exception) {
        log.error("Não foi possível abrir a porta {}: {}", cfg.porta, e.message)
        exitProcess(3)
    }
    // O processo segue vivo pelas threads do Netty; main pode terminar aqui.
    val avisos = listOfNotNull(Subida.aviso, Subida.avisoBackup).joinToString("\n").ifEmpty { null }
    Bandeja.mostrar(cfg.porta, Paths.get(System.getenv("REDESPACHO_LOGS") ?: "logs"), avisos, aoParar = sair)
}

/**
 * Desligamento em ordem, uma vez só, venha de onde vier: bandeja, parar.bat
 * ou o Windows encerrando a sessão.
 *
 * A ordem é o que protege os dados: primeiro para de aceitar pedidos (bipe
 * que chegasse depois da cópia final ficaria fora dela), depois a cópia final,
 * e só no fim apaga o sinal de presença. Apagá-lo antes deixava outra máquina
 * subir e restaurar a cópia anterior enquanto a final ainda viajava para a rede.
 */
private class Desligamento(
    private val backup: Backup,
    private val presenca: Presenca,
    private val db: Db,
) {
    private val log = LoggerFactory.getLogger(Desligamento::class.java)
    private val feito = AtomicBoolean(false)

    @Volatile var servidor: EmbeddedServer<*, *>? = null

    fun executar() {
        if (!feito.compareAndSet(false, true)) return
        log.info("Desligando: parando de aceitar pedidos, cópia final do backup, sinal de presença")
        runCatching { servidor?.stop(1_000, 3_000) }
        runCatching { backup.encerrar() }
        runCatching { db.close() }
        runCatching { presenca.encerrar() }
        log.info("Servidor desligado.")
    }
}

private fun ehDestaMaquina(endereco: String): Boolean =
    endereco == "127.0.0.1" || endereco == "::1" || endereco == "0:0:0:0:0:0:0:1" || endereco == "localhost"

fun Application.modulo(db: Db, pastaDados: Path = Paths.get("dados"), aoPedirParada: () -> Unit = {}) {
    val usuarios = UsuarioServico(db)
    val produtos = ProdutoServico(db)
    val atualizacao = AtualizacaoServico(pastaDados)
    // A pasta `coletor` fica ao lado do executável, não dentro de dados/: ela
    // vem no ZIP e dados/ nasce vazia na primeira subida.
    atualizacao.adotarApkDoPacote(Paths.get("coletor"))
    val instrucoes = InstrucaoServico(db)
    val separacao = SeparacaoServico(db)
    val desembarques = DesembarqueServico(db)
    val movimentacao = MovimentacaoServico(db)
    val devolucoes = DevolucaoServico(db)
    val estoque = EstoqueServico(db)
    val impressoras = ImpressoraServico(db)
    val enderecamento = EnderecamentoServico(db)
    val expedicao = ExpedicaoServico(db)
    val retornos = RetornoServico(db)
    val relatorios = RelatorioServico(db)
    val sessoes = Sessoes()

    // Sem nenhum usuário o sistema fica inacessível e não há como cadastrar o
    // primeiro. Criamos um admin de implantação, cuja senha deve ser trocada.
    // Malha de boxes na primeira subida: 740 boxes e 4.440 posições saem do
    // BOXES DEMO.xls convertido para CSV. Idempotente — não duplica.
    if (enderecamento.totalBoxes() == 0) {
        val csv = {}.javaClass.getResourceAsStream("/boxes-demo.csv")?.bufferedReader()?.readText()
        if (csv != null) {
            val r = enderecamento.importarBoxes(csv)
            log.info("Boxes importados: ${r.boxes} boxes, ${r.posicoes} posições, ${r.depositos} depósitos")
        }
    }

    if (!usuarios.existeAlgum()) {
        usuarios.criar("admin", "ADMINISTRADOR", "0000", Perfil.ADMIN)
        log.warn("Nenhum usuário cadastrado: criado 'admin' com senha 0000. TROQUE na primeira entrada.")
    }

    install(ContentNegotiation) {
        jackson {
            registerModule(KotlinModule.Builder().build())
            registerModule(JavaTimeModule())
            disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
        }
    }

    install(CORS) {
        anyHost() // rede local fechada, sem internet — ver docs/OPERACAO.md §7
        allowHeader(HttpHeaders.Authorization)
        allowHeader(HttpHeaders.ContentType)
        allowMethod(HttpMethod.Put)
        allowMethod(HttpMethod.Delete)
    }

    install(StatusPages) {
        exception<ErroNegocio> { call, e ->
            call.respond(
                e.status,
                CorpoErro(DetalheErro(e.codigo, e.mensagemUsuario, e.detalhe.ifEmpty { null })),
            )
        }
        exception<BadRequestException> { call, e ->
            call.respond(
                HttpStatusCode.BadRequest,
                CorpoErro(DetalheErro("PAYLOAD_INVALIDO", e.message ?: "Requisição malformada")),
            )
        }
        exception<Throwable> { call, e ->
            call.application.log.error("Erro não tratado", e)
            call.respond(
                HttpStatusCode.InternalServerError,
                CorpoErro(DetalheErro("ERRO_INTERNO", "Erro interno do servidor")),
            )
        }
    }

    routing {
        // Retaguarda servida pelo próprio backend: nada a instalar na estação,
        // e atualizar é reiniciar o serviço. Ver docs/TELAS-RETAGUARDA.md.
        staticResources("/", "web") {
            default("index.html")
            // A tela muda a cada versão, mas o pacote grava os arquivos com a
            // mesma data fixa: o navegador guardava a tela antiga e a versão
            // nova não aparecia (botão sumido, coluna faltando). Só a fonte,
            // que não muda, fica guardada.
            cacheControl { url ->
                if (url.path.endsWith(".woff2")) listOf(CacheControl.MaxAge(maxAgeSeconds = 86_400))
                else listOf(CacheControl.NoStore(null))
            }
        }

        // parar.bat pede por aqui. Desligar pelo caminho normal faz o último
        // backup e apaga o sinal de presença — o taskkill /F de antes pulava
        // os dois. Só da própria máquina: de fora, ninguém derruba o servidor.
        post("/api/v1/interno/parar") {
            if (!ehDestaMaquina(call.request.local.remoteAddress)) {
                call.respond(
                    HttpStatusCode.Forbidden,
                    CorpoErro(DetalheErro("SO_NA_MAQUINA", "Só pode ser pedido na máquina do servidor")),
                )
                return@post
            }
            call.respond(HttpStatusCode.Accepted, mapOf("parando" to true))
            aoPedirParada()
        }

        rotas(usuarios, produtos, atualizacao, movimentacao, expedicao, devolucoes, estoque, relatorios, instrucoes, separacao, desembarques, impressoras, enderecamento, sessoes, retornos, bancoOk = {
            // `use` nos dois: statement e ResultSet deixados abertos ficam presos
            // à conexão, e o SQLite recusa VACUUM enquanto houver statement em
            // andamento. Uma chamada a /saude bastava para matar o backup.
            runCatching {
                db.leitura { c ->
                    c.createStatement().use { st -> st.executeQuery("SELECT 1").use { it.next() } }
                }
            }.isSuccess
        })
    }
}
