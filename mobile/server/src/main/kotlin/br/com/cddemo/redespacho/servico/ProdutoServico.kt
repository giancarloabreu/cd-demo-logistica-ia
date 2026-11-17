package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.dominio.Gtin
import br.com.cddemo.redespacho.dominio.ResultadoGtin
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import java.sql.Connection
import java.time.Instant

data class CodigoBarras(
    val id: Long,
    val codigo: String,
    val origem: String,
    val confirmado: Boolean,
    val ativo: Boolean,
)

data class ProdutoResumo(
    val id: Long,
    val codigo: String,
    val descricao: String,
    val unidadesPorCaixa: Int?,
    val unCxOrigem: String?,
    val ativo: Boolean,
    val codigosBarras: List<CodigoBarras>,
)

data class CodigoPendente(
    val id: Long,
    val codigo: String,
    val qtdLeituras: Int,
    val primeiraLeituraEm: String,
    val ultimaLeituraEm: String,
    val ultimoUsuario: String?,
)

class ProdutoServico(private val db: Db) {

    fun listar(busca: String?, limite: Int = 50): List<ProdutoResumo> = db.leitura { c ->
        val termo = busca?.trim()?.takeIf { it.isNotEmpty() }
        val produtos = if (termo == null) {
            c.buscarTodos(
                """SELECT id, codigo, descricao, unidades_por_caixa, un_cx_origem, ativo
                     FROM produto ORDER BY codigo LIMIT ?""",
                limite,
            ) { linhaProduto(it) }
        } else {
            // Busca por código, descrição ou código de barras — o campo aceita
            // bipe direto de um leitor USB na retaguarda.
            val curinga = "%${termo.uppercase()}%"
            c.buscarTodos(
                """SELECT DISTINCT p.id, p.codigo, p.descricao, p.unidades_por_caixa,
                          p.un_cx_origem, p.ativo
                     FROM produto p
                     LEFT JOIN produto_codigo_barras b ON b.produto_id = p.id
                    WHERE p.codigo LIKE ? OR UPPER(p.descricao) LIKE ? OR b.codigo LIKE ?
                    ORDER BY p.codigo LIMIT ?""",
                curinga, curinga, curinga, limite,
            ) { linhaProduto(it) }
        }
        produtos.map { it.copy(codigosBarras = codigosDe(c, it.id)) }
    }

    /**
     * Fila alimentada por toda leitura recusada no coletor. A contagem é o que
     * diz a urgência: 7 tentativas significa separador parado agora.
     * Ver docs/TELAS-RETAGUARDA.md §4.
     */
    fun pendentes(): List<CodigoPendente> = db.leitura { c ->
        c.buscarTodos(
            """SELECT k.id, k.codigo, k.qtd_leituras, k.primeira_leitura_em,
                      k.ultima_leitura_em, u.nome
                 FROM codigo_nao_identificado k
                 LEFT JOIN usuario u ON u.id = k.ultimo_usuario_id
                WHERE k.resolvido_em IS NULL
                ORDER BY k.qtd_leituras DESC, k.ultima_leitura_em DESC""",
        ) {
            CodigoPendente(
                it.getLong(1), it.getString(2), it.getInt(3),
                it.getString(4), it.getString(5), it.getString(6),
            )
        }
    }

    fun quantidadePendente(): Int = db.leitura { c ->
        c.buscarUm(
            "SELECT COUNT(*) FROM codigo_nao_identificado WHERE resolvido_em IS NULL",
        ) { it.getInt(1) } ?: 0
    }

    /** Vincula um código pendente a um produto e tira da fila. */
    fun vincular(pendenteId: Long, produtoId: Long, usuarioId: Long): ProdutoResumo = db.transacao { c ->
        val codigo = c.buscarUm(
            "SELECT codigo FROM codigo_nao_identificado WHERE id = ? AND resolvido_em IS NULL",
            pendenteId,
        ) { it.getString(1) } ?: throw Erros.naoEncontrado("Código pendente")

        adicionarCodigo(c, produtoId, codigo, usuarioId, origem = "VINCULO_COLETOR")

        c.atualizar(
            """UPDATE codigo_nao_identificado
                  SET resolvido_em = ?, resolvido_produto_id = ?, resolvido_por = ?
                WHERE id = ?""",
            Instant.now().toString(), produtoId, usuarioId, pendenteId,
        )
        buscarProduto(c, produtoId)!!
    }

    /** Descarta um código que não é de produto — etiqueta de outra coisa. */
    fun descartarPendente(pendenteId: Long, usuarioId: Long) = db.transacao { c ->
        val alterou = c.atualizar(
            "UPDATE codigo_nao_identificado SET resolvido_em = ?, resolvido_por = ? WHERE id = ?",
            Instant.now().toString(), usuarioId, pendenteId,
        )
        if (alterou == 0) throw Erros.naoEncontrado("Código pendente")
    }

    fun adicionarCodigoBarras(produtoId: Long, codigo: String, usuarioId: Long): ProdutoResumo =
        db.transacao { c ->
            adicionarCodigo(c, produtoId, codigo, usuarioId, origem = "MANUAL")
            buscarProduto(c, produtoId)!!
        }

    fun desativarCodigoBarras(codigoId: Long): Unit = db.transacao { c ->
        c.atualizar("UPDATE produto_codigo_barras SET ativo = 0 WHERE id = ?", codigoId)
    }

    /** Unidades por caixa que o XML não permitiu validar. Ver docs §5. */
    fun definirUnidadesPorCaixa(produtoId: Long, unidades: Int): ProdutoResumo = db.transacao { c ->
        if (unidades !in 1..999) {
            throw Erros.invalido("VALOR_INVALIDO", "Unidades por caixa deve estar entre 1 e 999")
        }
        c.buscarUm("SELECT 1 FROM produto WHERE id = ?", produtoId) { it.getInt(1) }
            ?: throw Erros.naoEncontrado("Produto")
        c.atualizar(
            "UPDATE produto SET unidades_por_caixa = ?, un_cx_origem = 'MANUAL' WHERE id = ?",
            unidades, produtoId,
        )
        buscarProduto(c, produtoId)!!
    }

    // ---------------------------------------------------------------- internos

    private fun adicionarCodigo(
        c: Connection,
        produtoId: Long,
        bruto: String,
        usuarioId: Long,
        origem: String,
    ) {
        val normalizado = when (val r = Gtin.normalizar(bruto)) {
            is ResultadoGtin.Ok -> r.gtin14
            // Código fora do padrão GTIN ainda pode ser válido na prática:
            // guarda como veio, só limpando separadores.
            is ResultadoGtin.Invalido -> bruto.trim().uppercase()
        }
        if (normalizado.isBlank()) {
            throw Erros.invalido("CODIGO_VAZIO", "Informe o código de barras")
        }

        c.buscarUm("SELECT 1 FROM produto WHERE id = ?", produtoId) { it.getInt(1) }
            ?: throw Erros.naoEncontrado("Produto")

        // Invariante 10: um código aponta para no máximo um produto.
        val dono = c.buscarUm(
            """SELECT p.codigo, p.descricao, b.produto_id
                 FROM produto_codigo_barras b JOIN produto p ON p.id = b.produto_id
                WHERE b.codigo = ?""",
            normalizado,
        ) { Triple(it.getString(1), it.getString(2), it.getLong(3)) }

        if (dono != null) {
            if (dono.third == produtoId) {
                c.atualizar(
                    "UPDATE produto_codigo_barras SET ativo = 1 WHERE codigo = ?", normalizado,
                )
                return
            }
            throw Erros.conflito(
                "GTIN_DUPLICADO",
                "O código $normalizado já pertence ao produto ${dono.first} — ${dono.second}",
                mapOf("produtoCodigo" to dono.first),
            )
        }

        val agora = Instant.now().toString()
        c.inserir(
            """INSERT INTO produto_codigo_barras
               (produto_id, codigo, simbologia, origem, confirmado, confirmado_em, criado_em, criado_por)
               VALUES (?,?,'CODE128',?,1,?,?,?)""",
            produtoId, normalizado, origem, agora, agora, usuarioId,
        )
    }

    private fun buscarProduto(c: Connection, id: Long): ProdutoResumo? = c.buscarUm(
        """SELECT id, codigo, descricao, unidades_por_caixa, un_cx_origem, ativo
             FROM produto WHERE id = ?""",
        id,
    ) { linhaProduto(it) }?.let { it.copy(codigosBarras = codigosDe(c, it.id)) }

    private fun codigosDe(c: Connection, produtoId: Long): List<CodigoBarras> = c.buscarTodos(
        """SELECT id, codigo, origem, confirmado, ativo
             FROM produto_codigo_barras WHERE produto_id = ? ORDER BY ativo DESC, id""",
        produtoId,
    ) {
        CodigoBarras(
            it.getLong(1), it.getString(2), it.getString(3),
            it.getInt(4) == 1, it.getInt(5) == 1,
        )
    }

    private fun linhaProduto(rs: java.sql.ResultSet) = ProdutoResumo(
        id = rs.getLong(1),
        codigo = rs.getString(2),
        descricao = rs.getString(3),
        unidadesPorCaixa = rs.getInt(4).takeIf { !rs.wasNull() },
        unCxOrigem = rs.getString(5),
        ativo = rs.getInt(6) == 1,
        codigosBarras = emptyList(),
    )
}
