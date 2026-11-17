package br.com.cddemo.redespacho.persistencia

import java.nio.file.Path
import java.sql.Connection
import java.sql.DriverManager

/**
 * Abertura do banco e aplicação do schema.
 *
 * SQLite em arquivo local do servidor, acessado por um único processo — é o
 * que torna seguro usá-lo aqui, e a razão de o arquivo não ficar na unidade de
 * rede. Ver docs/OPERACAO.md §2.
 */
object Banco {

    private val schema: String by lazy {
        Banco::class.java.getResourceAsStream("/schema.sql")
            ?.bufferedReader()?.readText()
            ?: error("schema.sql não encontrado no classpath")
    }

    fun abrir(arquivo: Path): Connection {
        val c = DriverManager.getConnection("jdbc:sqlite:${arquivo.toAbsolutePath()}")
        // Arquivo que não é banco só falha no primeiro PRAGMA. Sem fechar
        // aqui, a conexão ficava aberta e, no Windows, prendia o arquivo: a
        // restauração não conseguia nem apagar a cópia ruim para tentar a próxima.
        return try {
            c.configurar()
        } catch (e: Exception) {
            runCatching { c.close() }
            throw e
        }
    }

    fun emMemoria(): Connection =
        DriverManager.getConnection("jdbc:sqlite::memory:").configurar().also { aplicarSchema(it) }

    fun aplicarSchema(c: Connection) {
        c.createStatement().use { st ->
            // Sem foreign_keys durante a criação: `pallet` referencia
            // `nota_devolucao`, declarada depois.
            st.executeUpdate("PRAGMA foreign_keys = OFF")
            st.executeUpdate(schema)
            st.executeUpdate("PRAGMA foreign_keys = ON")
        }
        Migracoes.aplicar(c)
    }

    private fun Connection.configurar(): Connection = apply {
        createStatement().use { st ->
            st.executeUpdate("PRAGMA foreign_keys = ON")
            st.execute("PRAGMA journal_mode = WAL")
            // FULL em vez de NORMAL: cada bipe vira gravação confirmada em
            // disco. Com NORMAL, uma queda de energia pode levar os últimos
            // segundos — e isso é caixa contada que some. O custo é irrelevante
            // no volume desta operação (poucos bipes por segundo no pico).
            st.executeUpdate("PRAGMA synchronous = FULL")
            st.executeUpdate("PRAGMA busy_timeout = 5000")
        }
    }
}
