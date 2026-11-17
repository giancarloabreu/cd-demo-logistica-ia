package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.impressao.Impressora
import br.com.cddemo.redespacho.impressao.Zpl
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import java.sql.Connection
import java.time.Instant

data class ImpressoraResumo(
    val id: Long,
    val nome: String,
    val ip: String,
    val porta: Int,
    val dpi: Int?,
    val ultimoContatoEm: String?,
    val ativo: Boolean,
)

data class ResultadoTeste(
    val alcancada: Boolean,
    val dpi: Int?,
    val impressao: String,
    val motivo: String? = null,
)

class ImpressoraServico(private val db: Db) {

    /** Sem dpi detectado, assume 203 — o modelo mais comum da ZT231. */
    private val DPI_PADRAO = 203

    fun listar(): List<ImpressoraResumo> = db.leitura { c ->
        c.buscarTodos(
            """SELECT id, nome, ip, porta, dpi, ultimo_contato_em, ativo
                 FROM impressora ORDER BY ativo DESC, nome""",
        ) { mapear(it) }
    }

    fun criar(nome: String, ip: String, porta: Int): ImpressoraResumo = db.transacao { c ->
        validar(nome, ip, porta)
        if (c.buscarUm("SELECT 1 FROM impressora WHERE nome = ?", nome.trim()) { it.getInt(1) } != null) {
            throw Erros.conflito("NOME_EM_USO", "Já existe uma impressora chamada \"${nome.trim()}\"")
        }
        val id = c.inserir(
            "INSERT INTO impressora (nome, ip, porta) VALUES (?,?,?)",
            nome.trim(), ip.trim(), porta,
        )
        buscar(c, id)!!
    }

    fun atualizar(id: Long, nome: String, ip: String, porta: Int, ativo: Boolean): ImpressoraResumo =
        db.transacao { c ->
            validar(nome, ip, porta)
            buscar(c, id) ?: throw Erros.naoEncontrado("Impressora")
            c.atualizar(
                "UPDATE impressora SET nome=?, ip=?, porta=?, ativo=? WHERE id=?",
                nome.trim(), ip.trim(), porta, ativo, id,
            )
            buscar(c, id)!!
        }

    /**
     * Detecta o dpi e imprime uma etiqueta de conferência. É o primeiro lugar
     * a olhar quando o coletor não imprime — separa problema de rede de
     * problema de app.
     */
    fun testar(id: Long): ResultadoTeste {
        val imp = db.leitura { c -> buscar(c, id) } ?: throw Erros.naoEncontrado("Impressora")

        val dpi = Impressora.detectarDpi(imp.ip, imp.porta)
        if (dpi != null) {
            db.transacao { c ->
                c.atualizar(
                    "UPDATE impressora SET dpi=?, ultimo_contato_em=? WHERE id=?",
                    dpi, Instant.now().toString(), id,
                )
            }
        }

        val dpiUsado = dpi ?: imp.dpi ?: DPI_PADRAO
        return when (val r = Impressora.enviar(imp.ip, imp.porta, Zpl.etiquetaTeste(dpiUsado, imp.nome))) {
            is Impressora.Resultado.Enviada -> {
                db.transacao { c ->
                    c.atualizar("UPDATE impressora SET ultimo_contato_em=? WHERE id=?",
                        Instant.now().toString(), id)
                }
                ResultadoTeste(true, dpi ?: imp.dpi, "ENVIADA")
            }
            is Impressora.Resultado.Falhou ->
                ResultadoTeste(false, dpi, "FALHOU", r.motivo)
        }
    }

    /**
     * Imprime as etiquetas dos pallets recém-criados. Falha de impressão não
     * derruba a criação: o pallet existe no sistema e pode ser reimpresso.
     */
    fun imprimirPallets(impressoraId: Long?, codigos: List<String>): String {
        val imp = impressoraId?.let { id -> db.leitura { c -> buscar(c, id) } }
            ?: db.leitura { c -> primeiraAtiva(c) }
            ?: return "SEM_IMPRESSORA"

        val dpi = imp.dpi ?: DPI_PADRAO
        // O rodapé vem do tipo gravado, não de um padrão: etiqueta de devolução
        // saindo escrita "Pallet Separação" manda a carga para o lugar errado.
        val zpl = db.leitura { c ->
            codigos.joinToString("") { codigo ->
                Zpl.etiquetaPallet(codigo, dpi, rodapeDoPallet(c, codigo))
            }
        }
        return when (Impressora.enviar(imp.ip, imp.porta, zpl)) {
            is Impressora.Resultado.Enviada -> "ENVIADA"
            is Impressora.Resultado.Falhou -> "FALHOU"
        }
    }

    private fun rodapeDoPallet(c: java.sql.Connection, codigo: String): String {
        val tipo = c.buscarUm(
            "SELECT tipo FROM pallet WHERE codigo = ?", codigo.trim().uppercase(),
        ) { it.getString(1) }
        return when (tipo) {
            "DEVOLUCAO" -> "Pallet Devolução"
            else -> "Pallet Separação"
        }
    }

    /**
     * [copias] etiquetas 2ª via iguais, uma por caixa. Mesma escolha de
     * impressora das outras etiquetas; falha de impressão só volta no texto.
     */
    fun imprimirEtiquetasProduto(
        impressoraId: Long?,
        codigoBarras: String,
        produtoCodigo: String,
        descricao: String,
        copias: Int,
    ): String {
        val imp = impressoraId?.let { id -> db.leitura { c -> buscar(c, id) } }
            ?: db.leitura { c -> primeiraAtiva(c) }
            ?: return "SEM_IMPRESSORA"
        val zpl = Zpl.etiquetaProduto(codigoBarras, produtoCodigo, descricao, imp.dpi ?: DPI_PADRAO).repeat(copias)
        return when (Impressora.enviar(imp.ip, imp.porta, zpl)) {
            is Impressora.Resultado.Enviada -> "ENVIADA"
            is Impressora.Resultado.Falhou -> "FALHOU"
        }
    }

    fun imprimirCartao(impressoraId: Long?, cartao: Zpl.Cartao): String {
        val imp = impressoraId?.let { id -> db.leitura { c -> buscar(c, id) } }
            ?: db.leitura { c -> primeiraAtiva(c) }
            ?: return "SEM_IMPRESSORA"
        val zpl = Zpl.cartaoPallet(cartao, imp.dpi ?: DPI_PADRAO)
        return when (Impressora.enviar(imp.ip, imp.porta, zpl)) {
            is Impressora.Resultado.Enviada -> "ENVIADA"
            is Impressora.Resultado.Falhou -> "FALHOU"
        }
    }

    // ---------------------------------------------------------------- internos

    private fun validar(nome: String, ip: String, porta: Int) {
        if (nome.isBlank()) throw Erros.invalido("NOME_OBRIGATORIO", "Informe o nome da impressora")
        if (ip.isBlank()) throw Erros.invalido("IP_OBRIGATORIO", "Informe o IP da impressora")
        if (porta !in 1..65535) throw Erros.invalido("PORTA_INVALIDA", "Porta inválida: $porta")
    }

    private fun primeiraAtiva(c: Connection): ImpressoraResumo? = c.buscarUm(
        """SELECT id, nome, ip, porta, dpi, ultimo_contato_em, ativo
             FROM impressora WHERE ativo = 1 ORDER BY id LIMIT 1""",
    ) { mapear(it) }

    private fun buscar(c: Connection, id: Long): ImpressoraResumo? = c.buscarUm(
        """SELECT id, nome, ip, porta, dpi, ultimo_contato_em, ativo
             FROM impressora WHERE id = ?""",
        id,
    ) { mapear(it) }

    private fun mapear(rs: java.sql.ResultSet) = ImpressoraResumo(
        id = rs.getLong(1),
        nome = rs.getString(2),
        ip = rs.getString(3),
        porta = rs.getInt(4),
        dpi = rs.getInt(5).takeIf { !rs.wasNull() },
        ultimoContatoEm = rs.getString(6),
        ativo = rs.getInt(7) == 1,
    )
}
