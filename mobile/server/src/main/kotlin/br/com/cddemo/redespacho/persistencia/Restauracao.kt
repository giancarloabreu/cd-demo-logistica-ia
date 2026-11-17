package br.com.cddemo.redespacho.persistencia

import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.StandardCopyOption
import java.sql.Connection
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.io.path.name

/** O que a subida e o backup têm a dizer a quem abre a retaguarda e a bandeja. */
object Subida {
    /** Restauração: banco restaurado, bifurcação, outro banco na pasta, pasta inacessível. */
    @Volatile var aviso: String? = null

    /** Backup desativado ou sem chegar à rede. Limpo na primeira cópia que chegar. */
    @Volatile var avisoBackup: String? = null
}

/**
 * Restauração automática do banco, na subida, a partir da pasta de backup.
 *
 * O banco mora no disco local, então um servidor que muda de máquina nascia
 * vazio na máquina nova — foi o que aconteceu no teste de campo, com o backup
 * inteiro esperando na rede. Agora, antes de abrir o banco:
 *
 * - **sem banco local, ou banco sem nenhuma operação**: restaura a cópia mais
 *   adiantada que tenha operação;
 * - **com banco local**: restaura só a cópia do mesmo banco que **descende do
 *   estado local** — outra máquina continuou este banco depois que esta parou;
 * - cópia do mesmo banco que seguiu **por outro caminho** (bifurcação: outra
 *   máquina trabalhou a partir de um ponto anterior ao deste) ou de **outro
 *   banco**: nunca substitui; avisa alto, porque cada lado tem o que o outro
 *   não tem e só alguém olhando decide;
 * - pasta configurada mas inacessível na subida: não decide nada e avisa que a
 *   restauração não foi conferida.
 *
 * O banco substituído nunca é apagado: vai inteiro (com -wal e -shm) para
 * `dados/substituidos/<data-hora>/`. Toda restauração abre um ramo novo no
 * banco restaurado ([Identidade.abrirRamo]).
 *
 * A pré-seleção é pelo nome das cópias ([Backup.lerNome]); a escolhida é
 * copiada, passa pelo `quick_check` e, quando o local tem dados, tem a árvore
 * de ramos conferida por dentro antes de entrar no lugar. Quem garante que
 * nenhum outro processo está com o banco aberto é a trava de instância da
 * subida (Aplicacao.kt).
 */
class Restauracao(
    banco: Path,
    private val pastaBackup: Path?,
    private val pastaInacessivel: Boolean = false,
) {

    private val banco: Path = banco.toAbsolutePath()
    private val log = LoggerFactory.getLogger(Restauracao::class.java)
    private val zona = ZoneId.of("America/Sao_Paulo")

    /** [avisar]: vale mostrar na bandeja e na retaguarda. */
    data class Resultado(val restaurado: Boolean, val mensagem: String, val avisar: Boolean = restaurado)

    private data class Local(
        val identidade: Identidade?,
        val historico: Map<String, Pair<String?, Long>>,
        val vazio: Boolean,
    )

    private sealed interface Tentativa {
        data class Restaurada(val resultado: Resultado) : Tentativa
        data object Bifurcada : Tentativa
        data object Inutil : Tentativa
    }

    fun executar(): Resultado {
        if (pastaBackup == null) return Resultado(false, "Backup não configurado; seguindo com o banco local.")
        if (pastaInacessivel) {
            val msg = "A pasta de backup não respondeu na subida: a restauração automática NÃO foi conferida. " +
                "O backup tenta de novo sozinho; confira a rede."
            log.warn(msg)
            return Resultado(false, msg, avisar = true)
        }
        if (!runCatching { Files.isDirectory(pastaBackup) }.getOrDefault(false)) {
            return Resultado(false, "A pasta de backup ainda não existe; nasce na primeira cópia.")
        }
        val copias = Files.list(pastaBackup).use { f -> f.toList().mapNotNull { Backup.lerNome(it) } }
        if (copias.isEmpty()) return Resultado(false, "Nenhuma cópia na pasta de backup.")

        val local = lerLocal()
        return when {
            local == null || local.vazio -> restaurarMelhor(copias, local)
            local.identidade == null -> conferirLegado(copias)
            else -> conferirMesmoBanco(copias, local.identidade, local.historico)
        }
    }

    // ------------------------------------------------ sem banco, ou banco vazio

    private fun restaurarMelhor(copias: List<CopiaDeBackup>, local: Local?): Resultado {
        val minha = local?.identidade?.geracao ?: -1
        val comIdentidade = copias.filter { it.geracao != null && it.geracao > minha }
            .sortedWith(compareByDescending<CopiaDeBackup> { it.geracao }.thenByDescending { it.carimbo })
        // Cópias de antes da 0.5.1 não dizem o que têm: vale a mais recente
        // com operação.
        val legadas = copias.filter { it.geracao == null }.sortedByDescending { it.carimbo }.take(10)

        // Com operação primeiro — com identidade, depois as antigas. Cópia de
        // banco vazio (máquina que subiu uma vez sem restaurar) só em último
        // caso, e só sem banco local nenhum: senão ela passaria na frente de
        // uma cópia antiga cheia de dados.
        val fila = comIdentidade.map { it to true } + legadas.map { it to true } +
            (if (local == null) comIdentidade.map { it to false } else emptyList())

        for ((copia, exigeOperacao) in fila.distinct()) {
            val t = tentar(copia, exigeOperacao, precisaConter = null)
            if (t is Tentativa.Restaurada) return comAvisoDeOutroBanco(t.resultado, copia, copias)
        }
        val msg = "ATENÇÃO: a pasta de backup tem ${copias.size} cópia(s), mas nenhuma pôde ser restaurada, e o " +
            "servidor subiu com um banco ${if (local == null) "NOVO, vazio" else "sem operação"}. " +
            "Não opere antes de conferir — veja logs\\servidor.log."
        log.error(msg)
        return Resultado(false, msg, avisar = true)
    }

    /** Restaurou, mas a pasta tem cópia mais recente de outro banco: alguém precisa saber. */
    private fun comAvisoDeOutroBanco(r: Resultado, escolhida: CopiaDeBackup, copias: List<CopiaDeBackup>): Resultado {
        val outra = copias
            .filter { it.linhagem != null && escolhida.linhagem != null && it.linhagem != escolhida.linhagem }
            .filter { it.carimbo > escolhida.carimbo }
            .maxByOrNull { it.carimbo } ?: return r
        return r.copy(
            mensagem = r.mensagem + " Atenção: a pasta também tem cópia mais recente de OUTRO banco " +
                "(${outra.arquivo.name}); confira qual vale.",
            avisar = true,
        )
    }

    // --------------------------------------------------- banco local com dados

    private fun conferirMesmoBanco(
        copias: List<CopiaDeBackup>,
        minha: Identidade,
        historico: Map<String, Pair<String?, Long>>,
    ): Resultado {
        val doMesmoBanco = copias.filter {
            it.linhagem == minha.linhagemCurta && it.ramo != null && it.geracao != null
        }
        val adiante = doMesmoBanco.filter { it.geracao!! > minha.geracao }
            .sortedWith(compareByDescending<CopiaDeBackup> { it.geracao }.thenByDescending { it.carimbo })

        val bifurcadas = mutableListOf<CopiaDeBackup>()
        val ilegiveis = mutableListOf<CopiaDeBackup>()
        // Por ramo, da mais adiantada para trás. Todas as cópias de um ramo têm
        // a mesma árvore acima dele: uma bifurcada condena o ramo inteiro, e
        // copiar as outras pela rede só atrasaria a subida.
        for ((_, doRamo) in adiante.groupBy { it.ramo }) {
            for (copia in doRamo) {
                when (val t = tentar(copia, exigeOperacao = false, precisaConter = minha)) {
                    is Tentativa.Restaurada -> return t.resultado
                    Tentativa.Bifurcada -> { bifurcadas += copia; break }
                    Tentativa.Inutil -> ilegiveis += copia
                }
            }
        }

        // As que não estão adiante são estados anteriores deste banco — o
        // normal — ou vieram de outro caminho. Estas só contam se forem mais
        // recentes que a última gravação local: depois que o servidor seguiu
        // trabalhando, a bifurcação antiga já foi vista (ou resolvida).
        val desde = minha.alteradoEm?.let { Backup.carimboDe(it) }
        bifurcadas += doMesmoBanco
            .filter { it.geracao!! <= minha.geracao }
            .filterNot { Identidade.contem(historico, minha.ramo, minha.geracao, it.ramo!!, it.geracao!!) }
            .filter { desde == null || it.carimbo >= desde }

        bifurcadas.maxByOrNull { it.carimbo }?.let { b ->
            val msg = "BIFURCAÇÃO: a pasta de backup tem a cópia ${b.arquivo.name}, deste mesmo banco, que seguiu " +
                "por outro caminho — outra máquina trabalhou a partir de um ponto anterior ao deste servidor. " +
                "Cada lado tem operações que o outro não tem. Nada foi restaurado: chame o suporte antes de seguir."
            log.warn(msg)
            return Resultado(false, msg, avisar = true)
        }
        // Cópia mais adiantada que não se deixou ler (rede caiu no meio,
        // arquivo ruim): não dá para dizer "em dia" — pode ser trabalho de outra
        // máquina, e na próxima subida ele já não estaria mais "adiante".
        ilegiveis.firstOrNull()?.let { c ->
            val msg = "A pasta de backup tem cópia mais adiantada deste banco (${c.arquivo.name}) que não pôde " +
                "ser lida: a restauração NÃO foi conferida. Confira a rede e a pasta antes de operar."
            log.warn(msg)
            return Resultado(false, msg, avisar = true)
        }
        return outraLinhagem(copias, minha) ?: emDia()
    }

    private fun emDia() = Resultado(false, "Banco local em dia com o backup.")

    /**
     * A pasta tem cópias de outro banco mais recentes que as deste: dois
     * servidores rodaram. Não há como juntar; só avisar alto.
     */
    private fun outraLinhagem(copias: List<CopiaDeBackup>, minha: Identidade): Resultado? {
        val minhaMaisRecente = copias.filter { it.linhagem == minha.linhagemCurta }.maxOfOrNull { it.carimbo }
        val outra = copias
            .filter { it.linhagem != null && it.linhagem != minha.linhagemCurta }
            .filter { minhaMaisRecente == null || it.carimbo > minhaMaisRecente }
            .maxByOrNull { it.carimbo }
            ?: return null
        val msg = "A pasta de backup tem cópia de OUTRO banco mais recente que este (${outra.arquivo.name}). " +
            "Parece que o servidor rodou em duas máquinas. Nada foi restaurado: confira qual banco vale antes de seguir."
        log.warn(msg)
        return Resultado(false, msg, avisar = true)
    }

    /**
     * Banco da 0.5.0 com operação: nunca é substituído — as cópias antigas não
     * dizem de que banco vieram. Mas, se a cópia antiga mais recente tiver
     * atividade mais nova que a dele, outra máquina trabalhou: avisa, em vez
     * de dizer "em dia".
     */
    private fun conferirLegado(copias: List<CopiaDeBackup>): Resultado {
        val minha = Banco.abrir(banco).use { marcaDeAtividade(it) }

        copias.filter { it.geracao == null }.maxByOrNull { it.carimbo }?.let { legada ->
            val dela = comCopia(legada) { marcaDeAtividade(it) }
            if (dela != null && (minha == null || dela > minha)) {
                val msg = "A pasta de backup tem cópia de antes da 0.5.1 (${legada.arquivo.name}) com atividade mais " +
                    "nova que este banco. Outra máquina pode ter trabalhado nele. Nada foi restaurado: confira antes de seguir."
                log.warn(msg)
                return Resultado(false, msg, avisar = true)
            }
        }

        // Cópias da 0.5.1 mais recentes que a última atividade deste banco:
        // outra máquina já roda a versão nova. Este banco antigo não é "o
        // banco" — sem este aviso ele subiria em silêncio como um segundo.
        val desde = minha?.let { Backup.carimboDe(it) }
        // ">=": o carimbo tem resolução de minuto; no mesmo minuto, avisar.
        copias.filter { it.geracao != null && (desde == null || it.carimbo >= desde) }
            .maxByOrNull { it.carimbo }
            ?.let { nova ->
                val msg = "Este banco é de antes da 0.5.1 e a pasta de backup tem cópia mais recente de outro " +
                    "banco (${nova.arquivo.name}). Nada foi restaurado: confira qual banco vale antes de operar."
                log.warn(msg)
                return Resultado(false, msg, avisar = true)
            }
        return emDia()
    }

    // ---------------------------------------------------------------- internos

    private var tentativas = 0

    /** Copia a cópia para o lado do banco, abre e entrega a conexão. Null se não abrir. */
    private fun <T> comCopia(copia: CopiaDeBackup, bloco: (Connection) -> T?): T? {
        val temporario = banco.resolveSibling("redespacho.restaurando-${++tentativas}.tmp")
        return try {
            Files.createDirectories(banco.parent)
            Files.copy(copia.arquivo, temporario, StandardCopyOption.REPLACE_EXISTING)
            Banco.abrir(temporario).use(bloco)
        } catch (e: Exception) {
            log.warn("Cópia {} não pôde ser lida ({}: {})", copia.arquivo.name, e.javaClass.simpleName, e.message)
            null
        } finally {
            runCatching { apagarComSatelites(temporario) }
        }
    }

    private fun tentar(copia: CopiaDeBackup, exigeOperacao: Boolean, precisaConter: Identidade?): Tentativa {
        // Um arquivo por tentativa: se a anterior deixou o dela preso por
        // qualquer motivo, isso não derruba as candidatas seguintes.
        val temporario = banco.resolveSibling("redespacho.restaurando-${++tentativas}.tmp")
        try {
            apagarComSatelites(temporario)
            Files.createDirectories(banco.parent)
            Files.copy(copia.arquivo, temporario, StandardCopyOption.REPLACE_EXISTING)

            var bifurcada = false
            val utilizavel = Banco.abrir(temporario).use { c ->
                val integra = c.buscarUm("PRAGMA quick_check") { it.getString(1) } == "ok"
                when {
                    !integra -> false
                    exigeOperacao && semOperacao(c) -> false
                    precisaConter == null -> true
                    else -> {
                        // Pela árvore de ramos de dentro da cópia, não pelo nome.
                        val id = Identidade.ler(c)
                        val descende = id != null && Identidade.contem(
                            Identidade.historico(c), id.ramo, id.geracao,
                            precisaConter.ramo, precisaConter.geracao,
                        )
                        bifurcada = !descende
                        descende
                    }
                }
            }
            if (bifurcada) {
                log.warn("Cópia {} é deste banco, mas seguiu por outro caminho (bifurcação)", copia.arquivo.name)
                return Tentativa.Bifurcada
            }
            if (!utilizavel) {
                log.warn("Cópia {} descartada: corrompida ou sem operação", copia.arquivo.name)
                return Tentativa.Inutil
            }

            val guardado = guardarLocal()
            // Só depois de guardado: o -wal pode ter transações que ainda não
            // foram para o arquivo principal.
            listOf("-wal", "-shm").forEach { Files.deleteIfExists(Paths.get("$banco$it")) }
            Files.move(temporario, banco, StandardCopyOption.REPLACE_EXISTING)
            // O primeiro trabalho daqui em diante abre um ramo novo (Db.transacao).
            // Se a máquina de onde a cópia veio continuar trabalhando no dela, a
            // diferença aparece como bifurcação em vez de sumir em silêncio.
            // Restaurar sem trabalhar não cria ramo: seria bifurcação falsa.
            runCatching { Banco.abrir(banco).use { Identidade.marcarRamoPendente(it) } }
                .onFailure { log.warn("Não foi possível marcar ramo novo no banco restaurado: {}", it.message) }

            val msg = buildString {
                append("Banco restaurado do backup ${copia.arquivo.name}.")
                guardado?.let { append(" O banco que estava nesta máquina foi guardado em $it.") }
            }
            log.warn(msg)
            return Tentativa.Restaurada(Resultado(true, msg))
        } catch (e: Exception) {
            log.warn("Cópia {} não pôde ser restaurada ({}: {})", copia.arquivo.name, e.javaClass.simpleName, e.message)
            return Tentativa.Inutil
        } finally {
            runCatching { apagarComSatelites(temporario) }
        }
    }

    private fun lerLocal(): Local? {
        if (!Files.exists(banco)) return null
        return Banco.abrir(banco).use { c -> Local(Identidade.ler(c), Identidade.historico(c), semOperacao(c)) }
    }

    /** Nenhuma instrução, pallet ou devolução: usuários e impressoras não contam. */
    private fun semOperacao(c: Connection): Boolean =
        listOf("instrucao", "pallet", "nota_devolucao").none { tabela ->
            runCatching {
                c.buscarUm("SELECT EXISTS(SELECT 1 FROM $tabela)") { it.getInt(1) == 1 } ?: false
            }.getOrDefault(false)
        }

    /** Momento da operação mais recente registrada no banco (texto ISO; comparável entre bancos). */
    private fun marcaDeAtividade(c: Connection): String? =
        listOf(
            "SELECT MAX(registrado_em) FROM leitura",
            "SELECT MAX(criado_em) FROM pallet",
            "SELECT MAX(criado_em) FROM instrucao",
            "SELECT MAX(registrado_em) FROM leitura_devolucao",
        ).mapNotNull { sql -> runCatching { c.buscarUm(sql) { it.getString(1) } }.getOrNull() }
            .maxOrNull()

    /** Cópia inteira do banco local, antes de substituí-lo. Nunca apagada. */
    private fun guardarLocal(): Path? {
        if (!Files.exists(banco)) return null
        val destino = banco.resolveSibling("substituidos").resolve(LocalDateTime.now(zona).format(CARIMBO))
        Files.createDirectories(destino)
        satelites(banco).filter { Files.exists(it) }.forEach {
            Files.copy(it, destino.resolve(it.name), StandardCopyOption.REPLACE_EXISTING)
        }
        return destino
    }

    private fun satelites(p: Path) = listOf(p, Paths.get("$p-wal"), Paths.get("$p-shm"))

    private fun apagarComSatelites(p: Path) = satelites(p).forEach { Files.deleteIfExists(it) }

    private companion object {
        val CARIMBO: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmmss")
    }
}
