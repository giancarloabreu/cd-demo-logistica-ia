package br.com.cddemo.redespacho.persistencia

import org.slf4j.LoggerFactory
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.StandardCopyOption
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.io.path.name

/** Uma cópia na pasta de backup, pelo que o nome diz dela. */
data class CopiaDeBackup(
    val arquivo: Path,
    /** `yyyy-MM-dd_HHmm`: ordena por texto. */
    val carimbo: String,
    /** Null em cópia anterior à 0.5.1, sem identidade no nome. */
    val linhagem: String?,
    val ramo: String?,
    val geracao: Long?,
)

/**
 * Cópia periódica do banco para a pasta de rede.
 *
 * É **backup**, não sincronização: o banco continua vivendo num lugar só. Ter
 * duas máquinas gravando o mesmo arquivo por SMB produziria bancos divergentes
 * e sequenciais repetidos, sem conserto possível. O que a pasta de rede resolve
 * é a máquina morrer, ou o servidor mudar de máquina — ver [Restauracao].
 *
 * O nome da cópia leva a identidade do banco (`-L<linhagem>-G<geração>`), para
 * a restauração escolher sem abrir arquivo nenhum pela rede.
 *
 * Ver docs/OPERACAO.md §2.
 */
class Backup(
    private val banco: Path,
    private val destino: Path?,
    private val intervalo: Duration = Duration.ofMinutes(15),
) {

    private val log = LoggerFactory.getLogger(Backup::class.java)
    private val agenda = Executors.newSingleThreadScheduledExecutor { r ->
        Thread(r, "backup").apply { isDaemon = true }
    }

    private val zona = ZoneId.of("America/Sao_Paulo")

    /** Identidade da última cópia gravada por este processo. */
    @Volatile private var ultimaGravada: Identidade? = null

    /** Quando as cópias começaram a falhar; null enquanto estão chegando. */
    @Volatile private var falhandoDesde: LocalDateTime? = null

    fun iniciar() {
        if (destino == null) {
            log.warn("Backup desativado: nenhuma pasta configurada (BACKUP em configuracao.txt).")
            Subida.avisoBackup = "Backup desativado: nenhuma pasta configurada em configuracao.txt. " +
                "Se esta máquina parar, os dados vão junto."
            return
        }
        log.info("Backup a cada {} min para {}", intervalo.toMinutes(), destino)
        agenda.scheduleAtFixedRate(
            { executarComSeguranca() },
            1, intervalo.toMinutes(), TimeUnit.MINUTES,
        )
    }

    fun encerrar() {
        // Espera a cópia agendada que estiver em andamento: as duas usam o
        // mesmo temporário, e no Windows a segunda não consegue nem apagá-lo.
        agenda.shutdown()
        runCatching { agenda.awaitTermination(2, TimeUnit.MINUTES) }
        // Uma última cópia ao desligar: é o momento em que o banco está mais
        // completo e mais perto de ficar indisponível.
        runCatching { executarComSeguranca() }
    }

    /** Falha de backup nunca derruba o servidor: a rede cai, a operação segue. */
    private fun executarComSeguranca() {
        try {
            executar()
            if (destino != null) {
                falhandoDesde = null
                Subida.avisoBackup = null
            }
        } catch (e: Exception) {
            // Diz o destino e o tipo do erro: "backup falhou" sozinho não deixa
            // ninguém agir, e quem lê o log não tem o código na frente.
            log.warn(
                "Backup falhou para {} ({}: {}) — a operação continua normalmente",
                destino, e.javaClass.simpleName, e.message,
            )
            val desde = falhandoDesde ?: LocalDateTime.now(zona).also { falhandoDesde = it }
            // Na retaguarda: sem janela nenhuma, o log era o único lugar onde
            // se via que as cópias pararam de chegar.
            Subida.avisoBackup = "O backup não está chegando à pasta de rede desde " +
                "${DateTimeFormatter.ofPattern("dd/MM HH:mm").format(desde)} " +
                "(${e.javaClass.simpleName}). Confira a rede; ele tenta de novo sozinho."
        }
    }

    /** Interno para o teste poder ver a exceção que `executarComSeguranca` engole. */
    @Synchronized
    internal fun executar() {
        val pasta = destino ?: return
        Files.createDirectories(pasta)

        // VACUUM INTO produz uma cópia consistente com o banco em uso, sem
        // parar o serviço nem travar quem está bipando.
        // Escreve ao lado do próprio banco, não em java.io.tmpdir: em máquina
        // corporativa o TEMP pode estar redirecionado, cheio ou bloqueado por
        // política, e isso apareceria como "backup falhou" sem explicação. Esta
        // pasta já se sabe gravável — o banco vive nela.
        val temporario = (banco.toAbsolutePath().parent ?: Paths.get("."))
            .resolve("redespacho-backup.tmp")
        Files.deleteIfExists(temporario)   // VACUUM INTO recusa destino existente

        try {
            // Conexão própria, de vida curta, em vez da conexão viva do servidor.
            // O SQLite recusa VACUUM enquanto houver qualquer statement em
            // andamento naquela conexão, então um único statement esquecido em
            // qualquer canto do sistema desligaria o backup — em silêncio, e a
            // conta só chegaria no dia em que a máquina morresse. Em WAL, um
            // segundo leitor enxerga tudo que já foi confirmado.
            val identidade = Banco.abrir(banco).use { c ->
                // Lida antes da cópia: se alguém gravar no meio, a cópia tem
                // no mínimo esta geração — o nome nunca promete mais do que ela tem.
                val id = Identidade.ler(c)
                if (id != null && id == ultimaGravada) {
                    // Nada gravado desde a última cópia: outra igual só ocuparia
                    // a pasta de rede (à noite seriam dezenas).
                    log.debug("Backup dispensado: nada mudou desde {}", id)
                    return
                }
                c.createStatement().use {
                    val alvo = temporario.toAbsolutePath().toString().replace("'", "''")
                    it.executeUpdate("VACUUM INTO '$alvo'")
                }
                id
            }

            val nome = nomeDaCopia(LocalDateTime.now(zona), identidade)
            // Publica em dois passos: copiar para a rede leva tempo, e no meio
            // dela quem listasse a pasta — a restauração de outra máquina —
            // veria um .db pela metade. O .parcial não conta como cópia; o
            // rename final, na mesma pasta, é instantâneo.
            val parcial = pasta.resolve("$nome.parcial")
            Files.move(temporario, parcial, StandardCopyOption.REPLACE_EXISTING)
            publicar(parcial, pasta.resolve(nome))
            ultimaGravada = identidade
            log.info("Backup gravado: {}", nome)
        } finally {
            // Falhando no meio, não deixa meio arquivo ocupando disco.
            runCatching { Files.deleteIfExists(temporario) }
        }

        limpar(pasta)
    }

    private fun publicar(parcial: Path, final: Path) {
        try {
            Files.move(parcial, final, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } catch (e: AtomicMoveNotSupportedException) {
            Files.move(parcial, final, StandardCopyOption.REPLACE_EXISTING)
        }
    }

    /**
     * Retenção: tudo das últimas 48 h, mais a última cópia de cada dia por 30
     * dias. Sem isso a pasta cresce sem parar e um dia enche o compartilhamento.
     */
    private fun limpar(pasta: Path) {
        val todos = Files.list(pasta).use { it.toList() }
        val limite48h = Instant.now().minus(Duration.ofHours(48))

        // .parcial de cópia interrompida (rede caiu no meio) não é cópia.
        todos.filter { it.name.endsWith(".parcial") }
            .filter { Files.getLastModifiedTime(it).toInstant().isBefore(Instant.now().minus(Duration.ofDays(1))) }
            .forEach { runCatching { Files.deleteIfExists(it) } }

        val arquivos = todos.filter { it.name.startsWith("redespacho-") && it.name.endsWith(".db") }
        val hoje = LocalDate.now(zona)
        val recentes = arquivos.filter {
            Files.getLastModifiedTime(it).toInstant().isAfter(limite48h)
        }.toSet()

        val ultimaDoDia = arquivos
            .filterNot { it in recentes }
            .groupBy { it.name.substringAfter("redespacho-").take(10) }
            .mapValues { (_, lista) -> lista.maxByOrNull { it.name }!! }
            .values.toSet()

        arquivos.forEach { arquivo ->
            if (arquivo in recentes || arquivo in ultimaDoDia) return@forEach
            val dia = runCatching {
                LocalDate.parse(arquivo.name.substringAfter("redespacho-").take(10))
            }.getOrNull()
            val expirado = dia == null || dia.isBefore(hoje.minusDays(30))
            if (expirado || arquivo !in ultimaDoDia) {
                runCatching { Files.deleteIfExists(arquivo) }
            }
        }
    }

    companion object {
        private val CARIMBO = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmm")
        private val NOME = Regex(
            """redespacho-(\d{4}-\d{2}-\d{2}_\d{4})(?:-L([0-9a-f]{8})-R([0-9a-f]{8})-G(\d+))?\.db""",
        )

        /** `redespacho-2026-09-12_0742-L3f2a9c1d-R7b01e4aa-G1234.db` */
        internal fun nomeDaCopia(momento: LocalDateTime, identidade: Identidade?): String =
            "redespacho-${CARIMBO.format(momento)}" +
                (identidade?.let { "-L${it.linhagemCurta}-R${it.ramoCurto}-G${it.geracao}" } ?: "") + ".db"

        /** Carimbo de nome de cópia para um Instant — mesma régua do nome. */
        internal fun carimboDe(instante: String): String? = runCatching {
            CARIMBO.format(java.time.Instant.parse(instante).atZone(ZoneId.of("America/Sao_Paulo")))
        }.getOrNull()

        /** Null para o que não é cópia de backup (inclusive `.parcial`). */
        fun lerNome(arquivo: Path): CopiaDeBackup? {
            val m = NOME.matchEntire(arquivo.name) ?: return null
            return CopiaDeBackup(
                arquivo = arquivo,
                carimbo = m.groupValues[1],
                linhagem = m.groupValues[2].ifEmpty { null },
                ramo = m.groupValues[3].ifEmpty { null },
                geracao = m.groupValues[4].toLongOrNull(),
            )
        }

        fun pastaConfigurada(): Path? = System.getenv("REDESPACHO_BACKUP")
            ?.takeIf { it.isNotBlank() }
            ?.let { runCatching { Paths.get(it) }.getOrNull() }

        fun de(banco: Path): Backup {
            val minutos = System.getenv("REDESPACHO_BACKUP_MIN")?.toLongOrNull() ?: 15
            return Backup(banco, pastaConfigurada(), Duration.ofMinutes(minutos))
        }
    }
}
