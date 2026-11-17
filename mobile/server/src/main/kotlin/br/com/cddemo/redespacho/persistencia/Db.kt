package br.com.cddemo.redespacho.persistencia

import java.nio.file.Path
import java.sql.Connection
import java.sql.PreparedStatement
import java.sql.ResultSet

/**
 * Acesso ao banco.
 *
 * Conexão única serializada por lock. SQLite aceita um escritor por vez, e o
 * volume aqui — algumas dezenas de leituras por minuto na pior hora — não
 * justifica pool. Simplifica também a transação: uma por vez, sem risco de
 * duas rotas dividindo a mesma conexão no meio de um commit.
 */
class Db(private val conexao: Connection) : AutoCloseable {

    private val lock = Any()

    /** Banco com identidade (migração 4); montado à mão num teste, pode não ter. */
    private val contaGeracao: Boolean = runCatching {
        conexao.buscarUm(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'banco_identidade'",
        ) { true } ?: false
    }.getOrDefault(false)

    /** Coluna do ramo pendente (restauração); banco de teste antigo pode não ter. */
    private val temRamoPendente: Boolean = contaGeracao && runCatching {
        conexao.buscarTodos("PRAGMA table_info(banco_identidade)") { it.getString("name") }
            .contains("ramo_pendente")
    }.getOrDefault(false)

    private val maquina: String? = System.getenv("COMPUTERNAME")

    fun <T> transacao(bloco: (Connection) -> T): T = synchronized(lock) {
        val autoCommitAnterior = conexao.autoCommit
        conexao.autoCommit = false
        try {
            val antes = if (contaGeracao) mudancas() else 0L
            val r = bloco(conexao)
            // Transação que gravou algo avança a geração, na mesma transação:
            // é o que diz, entre duas cópias do banco, qual está mais adiante.
            // Ver Identidade.
            if (contaGeracao && mudancas() != antes) {
                // Primeiro trabalho depois de uma restauração: nasce o ramo desta máquina.
                if (temRamoPendente) Identidade.abrirRamoSePendente(conexao, maquina)
                conexao.atualizar(
                    "UPDATE banco_identidade SET geracao = geracao + 1, alterado_em = ? WHERE id = 1",
                    java.time.Instant.now().toString(),
                )
            }
            conexao.commit()
            r
        } catch (e: Throwable) {
            runCatching { conexao.rollback() }
            throw e
        } finally {
            conexao.autoCommit = autoCommitAnterior
        }
    }

    fun <T> leitura(bloco: (Connection) -> T): T = synchronized(lock) { bloco(conexao) }

    /** Linhas alteradas pela conexão desde que abriu. */
    private fun mudancas(): Long = conexao.buscarUm("SELECT total_changes()") { it.getLong(1) } ?: 0L

    override fun close() = conexao.close()

    companion object {
        fun arquivo(caminho: Path): Db {
            val novo = !caminho.toFile().exists()
            caminho.toAbsolutePath().parent?.toFile()?.mkdirs()
            val c = Banco.abrir(caminho)
            if (novo) Banco.aplicarSchema(c) else Migracoes.aplicar(c)
            return Db(c)
        }

        fun emMemoria(): Db = Db(Banco.emMemoria())
    }
}

// --- açúcar para JDBC -------------------------------------------------------

fun Connection.consulta(sql: String, vararg args: Any?): PreparedStatement =
    prepareStatement(sql).apply { aplicar(args) }

fun Connection.atualizar(sql: String, vararg args: Any?): Int =
    prepareStatement(sql).use { it.aplicar(args); it.executeUpdate() }

/** Executa um INSERT e devolve a chave gerada. */
fun Connection.inserir(sql: String, vararg args: Any?): Long =
    prepareStatement(sql, java.sql.Statement.RETURN_GENERATED_KEYS).use {
        it.aplicar(args)
        it.executeUpdate()
        it.generatedKeys.use { k -> if (k.next()) k.getLong(1) else 0L }
    }

fun <T> Connection.buscarUm(sql: String, vararg args: Any?, mapa: (ResultSet) -> T): T? =
    consulta(sql, *args).use { st -> st.executeQuery().use { if (it.next()) mapa(it) else null } }

fun <T> Connection.buscarTodos(sql: String, vararg args: Any?, mapa: (ResultSet) -> T): List<T> =
    consulta(sql, *args).use { st ->
        st.executeQuery().use {
            buildList { while (it.next()) add(mapa(it)) }
        }
    }

private fun PreparedStatement.aplicar(args: Array<out Any?>) {
    args.forEachIndexed { i, v ->
        when (v) {
            null -> setObject(i + 1, null)
            is Int -> setInt(i + 1, v)
            is Long -> setLong(i + 1, v)
            is Boolean -> setInt(i + 1, if (v) 1 else 0)
            is ByteArray -> setBytes(i + 1, v)
            else -> setString(i + 1, v.toString())
        }
    }
}
