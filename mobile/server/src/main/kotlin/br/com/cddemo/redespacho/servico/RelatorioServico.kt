package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.buscarTodos
import java.sql.Connection

data class FiltroRelatorio(
    val de: String? = null,
    val ate: String? = null,
    val usuarioId: Long? = null,
    val instrucaoId: Long? = null,
    val notaNumero: String? = null,
    val produtoCodigo: String? = null,
)

data class LinhaSeparacao(
    val usuario: String,
    val instrucao: String,
    val notaNumero: String,
    val produtoCodigo: String,
    val produtoDescricao: String,
    val primeiraEm: String,
    val ultimaEm: String,
    /** Tudo que foi bipado, inclusive o que depois saiu. */
    val separado: Int,
    val excluido: Int,
) {
    /** O número que vale: bipado menos excluído. */
    val liquido: Int get() = separado - excluido
}

data class TotaisSeparacao(val separado: Int, val excluido: Int, val liquido: Int)

data class RelatorioSeparacao(val linhas: List<LinhaSeparacao>, val totais: TotaisSeparacao)

data class LinhaExclusao(
    val usuario: String,
    val em: String,
    val instrucao: String,
    val notaNumero: String,
    val produtoCodigo: String,
    val produtoDescricao: String,
    val quantidade: Int,
    val palletCodigo: String,
    val motivo: String?,
)

data class LinhaProdutividade(
    val usuario: String,
    val dia: String,
    val hora: String,
    val liquido: Int,
)

/**
 * Relatórios de separação.
 *
 * Tudo sai da tabela `leitura`, que guarda uma linha por caixa bipada. Foi
 * para isso que ela não é um contador: com o movimento preservado dá para
 * responder quem, quando e o que foi excluído — o contador só diria o saldo.
 * Ver docs/TELAS-RETAGUARDA.md §11.
 */
class RelatorioServico(private val db: Db) {

    fun separacao(f: FiltroRelatorio): RelatorioSeparacao = db.leitura { c ->
        val (onde, args) = filtro(f)
        val linhas = c.buscarTodos(
            """SELECT u.nome, i.numero, n.numero, p.codigo, p.descricao,
                      MIN(l.registrado_em), MAX(l.registrado_em),
                      COUNT(*),
                      -- Só exclusão (1). Caixa que faltou num retorno (2) foi
                      -- separada e saiu: continua no líquido do separador.
                      COALESCE(SUM(CASE WHEN l.cancelada = 1 THEN 1 ELSE 0 END), 0)
                 FROM leitura l
                 JOIN usuario u ON u.id = l.usuario_id
                 JOIN item_nota it ON it.id = l.item_nota_id
                 JOIN nota_fiscal n ON n.id = it.nota_fiscal_id
                 JOIN instrucao i ON i.id = n.instrucao_id
                 JOIN produto p ON p.id = l.produto_id
                $onde
                GROUP BY u.id, i.id, n.id, p.id
                ORDER BY MIN(l.registrado_em)""",
            *args,
        ) {
            LinhaSeparacao(
                it.getString(1), it.getString(2), it.getString(3), it.getString(4),
                it.getString(5), it.getString(6), it.getString(7),
                it.getInt(8), it.getInt(9),
            )
        }

        RelatorioSeparacao(
            linhas = linhas,
            totais = TotaisSeparacao(
                separado = linhas.sumOf { it.separado },
                excluido = linhas.sumOf { it.excluido },
                liquido = linhas.sumOf { it.liquido },
            ),
        )
    }

    /**
     * Exclusões isoladas. Fica separado de propósito: diluída no volume total,
     * a exclusão não aparece — e frequência de exclusão por separador é o
     * padrão que costuma anteceder falta no cliente.
     */
    fun exclusoes(f: FiltroRelatorio): List<LinhaExclusao> = db.leitura { c ->
        val (onde, args) = filtro(f, apenasCanceladas = true)
        c.buscarTodos(
            """SELECT COALESCE(uc.nome, u.nome), l.cancelada_em, i.numero, n.numero,
                      p.codigo, p.descricao, COUNT(*), pa.codigo, l.cancelamento_motivo
                 FROM leitura l
                 JOIN usuario u ON u.id = l.usuario_id
                 LEFT JOIN usuario uc ON uc.id = l.cancelada_por
                 JOIN pallet pa ON pa.id = l.pallet_id
                 JOIN item_nota it ON it.id = l.item_nota_id
                 JOIN nota_fiscal n ON n.id = it.nota_fiscal_id
                 JOIN instrucao i ON i.id = n.instrucao_id
                 JOIN produto p ON p.id = l.produto_id
                $onde
                GROUP BY l.cancelada_em, l.cancelada_por, pa.id, p.id
                ORDER BY l.cancelada_em DESC""",
            *args,
        ) {
            LinhaExclusao(
                it.getString(1), it.getString(2).orEmpty(), it.getString(3), it.getString(4),
                it.getString(5), it.getString(6), it.getInt(7), it.getString(8), it.getString(9),
            )
        }
    }

    /** Caixas líquidas por usuário e por hora, para dimensionar turno. */
    fun produtividade(f: FiltroRelatorio): List<LinhaProdutividade> = db.leitura { c ->
        val (onde, args) = filtro(f, apenasValidas = true)
        c.buscarTodos(
            """SELECT u.nome,
                      substr(l.registrado_em, 1, 10),
                      substr(l.registrado_em, 12, 2),
                      COUNT(*)
                 FROM leitura l
                 JOIN usuario u ON u.id = l.usuario_id
                 JOIN item_nota it ON it.id = l.item_nota_id
                 JOIN nota_fiscal n ON n.id = it.nota_fiscal_id
                 JOIN instrucao i ON i.id = n.instrucao_id
                 JOIN produto p ON p.id = l.produto_id
                $onde
                GROUP BY u.id, substr(l.registrado_em, 1, 13)
                ORDER BY substr(l.registrado_em, 1, 13), u.nome""",
            *args,
        ) {
            LinhaProdutividade(it.getString(1), it.getString(2), it.getString(3), it.getInt(4))
        }
    }

    fun separacaoCsv(f: FiltroRelatorio): String {
        val r = separacao(f)
        return buildString {
            appendLine("Usuario;Instrucao;NF;Produto;Descricao;Primeira;Ultima;Separado;Excluido;Liquido")
            r.linhas.forEach {
                appendLine(
                    listOf(
                        it.usuario, it.instrucao, it.notaNumero, it.produtoCodigo,
                        it.produtoDescricao, it.primeiraEm, it.ultimaEm,
                        it.separado, it.excluido, it.liquido,
                    ).joinToString(";") { campo -> csv(campo.toString()) }
                )
            }
            appendLine(";;;;;;;${r.totais.separado};${r.totais.excluido};${r.totais.liquido}")
        }
    }

    fun exclusoesCsv(f: FiltroRelatorio): String = buildString {
        appendLine("Usuario;Quando;Instrucao;NF;Produto;Descricao;Quantidade;Pallet;Motivo")
        exclusoes(f).forEach {
            appendLine(
                listOf(
                    it.usuario, it.em, it.instrucao, it.notaNumero, it.produtoCodigo,
                    it.produtoDescricao, it.quantidade, it.palletCodigo, it.motivo.orEmpty(),
                ).joinToString(";") { campo -> csv(campo.toString()) }
            )
        }
    }

    // ---------------------------------------------------------------- internos

    /** `;` e quebra de linha quebrariam o CSV que o Excel abre. */
    private fun csv(valor: String): String =
        if (valor.contains(';') || valor.contains('"') || valor.contains('\n')) {
            "\"${valor.replace("\"", "\"\"").replace("\n", " ")}\""
        } else {
            valor
        }

    private fun filtro(
        f: FiltroRelatorio,
        apenasCanceladas: Boolean = false,
        apenasValidas: Boolean = false,
    ): Pair<String, Array<Any?>> {
        val condicoes = mutableListOf<String>()
        val args = mutableListOf<Any?>()

        if (apenasCanceladas) condicoes += "l.cancelada = 1"
        // Válida é o que não foi excluído: a caixa que faltou num retorno
        // (cancelada = 2) foi separada e conta na produtividade.
        if (apenasValidas) condicoes += "l.cancelada <> 1"

        f.de?.takeIf { it.isNotBlank() }?.let { condicoes += "l.registrado_em >= ?"; args += it }
        // O filtro é por dia inteiro: sem o sufixo, "até 06/09" excluiria o
        // próprio dia 6.
        f.ate?.takeIf { it.isNotBlank() }?.let { condicoes += "l.registrado_em <= ?"; args += "${it}T23:59:59Z" }
        f.usuarioId?.let { condicoes += "l.usuario_id = ?"; args += it }
        f.instrucaoId?.let { condicoes += "i.id = ?"; args += it }
        f.notaNumero?.takeIf { it.isNotBlank() }?.let { condicoes += "n.numero = ?"; args += it }
        f.produtoCodigo?.takeIf { it.isNotBlank() }?.let { condicoes += "p.codigo = ?"; args += it }

        val onde = if (condicoes.isEmpty()) "" else "WHERE " + condicoes.joinToString(" AND ")
        return onde to args.toTypedArray()
    }
}
