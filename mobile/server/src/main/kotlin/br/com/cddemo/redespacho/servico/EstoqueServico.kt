package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm

data class LinhaEstoque(
    val palletCodigo: String,
    val tipo: String,
    val status: String,
    /** EM_ESTOQUE, EXPEDIDO ou FORA_DE_CIRCULACAO. */
    val situacao: String,
    val notaNumero: String?,
    val cliente: String?,
    val bonificacao: Boolean,
    val qtdCaixas: Int,
    val endereco: String?,
    val montadoEm: String?,
    val montadoPor: String?,
    val saidaEm: String?,
    val saidaPor: String?,
    /** EXPEDICAO, BAIXA_DEVOLUCAO, REFATURAMENTO ou DESCARTE — o que tirou o pallet do estoque. */
    val saidaTipo: String?,
    /** Situação do retorno, quando o pallet voltou de uma NF expedida. */
    val retorno: String? = null,
)

data class TotaisEstoque(
    val pallets: Int,
    val caixas: Int,
    val semEndereco: Int,
)

data class RelatorioEstoque(
    val linhas: List<LinhaEstoque>,
    val totais: TotaisEstoque,
)

data class FiltroEstoque(
    val situacao: String = "EM_ESTOQUE",
    val nota: String? = null,
    val cliente: String? = null,
    val box: String? = null,
    val tipo: String? = null,
)

/**
 * O que está no CD e o que já saiu, na granularidade de **pallet**.
 *
 * Pallet é a unidade que ocupa posição e sobe no caminhão — perguntar "o que
 * tem em estoque" é perguntar quais pallets ainda estão aqui. A contagem de
 * caixas vem das leituras vivas, não do previsto da NF: é o que foi bipado que
 * está no palete.
 *
 * Ver docs/TELAS-RETAGUARDA.md §11.
 */
class EstoqueServico(private val db: Db) {

    fun consultar(f: FiltroEstoque): RelatorioEstoque = db.leitura { c ->
        val condicoes = mutableListOf<String>()
        val args = mutableListOf<Any?>()

        when (f.situacao.uppercase()) {
            // EM_USO entra: o pallet está sendo montado, mas as caixas já estão
            // fisicamente em cima dele, dentro do CD. Deixar de fora faria o
            // estoque mentir para menos durante toda a separação — e o pallet de
            // devolução em conferência nunca passa por ENCERRADO.
            "EM_ESTOQUE" -> condicoes += "p.status IN ('EM_USO','ENCERRADO','ENDERECADO')"
            "EXPEDIDO" -> condicoes += "p.status = 'EXPEDIDO'"
            "TODOS" -> Unit
            // Pallet fundido ou esvaziado deixou de existir como volume: as
            // caixas estão em outro. Só aparece quando pedido de propósito,
            // senão a soma do estoque contaria a mesma caixa duas vezes.
            "FORA_DE_CIRCULACAO" -> condicoes += "p.status IN ('FUNDIDO','ESVAZIADO')"
            else -> condicoes += "p.status IN ('EM_USO','ENCERRADO','ENDERECADO')"
        }
        if (f.situacao.uppercase() == "TODOS") {
            condicoes += "p.status <> 'CRIADO'"
        }

        f.tipo?.trim()?.takeIf { it.isNotEmpty() }?.let {
            condicoes += "p.tipo = ?"
            args.add(it.uppercase())
        }
        f.nota?.trim()?.takeIf { it.isNotEmpty() }?.let {
            condicoes += "(nf.numero LIKE ? OR nd.numero LIKE ?)"
            args.add("%$it%")
            args.add("%$it%")
        }
        f.cliente?.trim()?.takeIf { it.isNotEmpty() }?.let {
            condicoes += "(UPPER(nf.dest_nome) LIKE ? OR UPPER(nd.emitente_nome) LIKE ?)"
            args.add("%${it.uppercase()}%")
            args.add("%${it.uppercase()}%")
        }
        f.box?.trim()?.takeIf { it.isNotEmpty() }?.let {
            condicoes += "b.codigo LIKE ?"
            args.add("%${it.uppercase()}%")
        }

        val onde = if (condicoes.isEmpty()) "" else "WHERE " + condicoes.joinToString(" AND ")

        val linhas = c.buscarTodos(
            """SELECT p.codigo, p.tipo, p.status,
                      COALESCE(nf.numero, nd.numero),
                      COALESCE(nf.dest_nome, nd.emitente_nome),
                      COALESCE(nf.bonificacao, 0),
                      (SELECT COUNT(*) FROM leitura l
                        WHERE l.pallet_id = p.id AND l.cancelada = 0)
                      + (SELECT COUNT(*) FROM leitura_devolucao ld
                          WHERE ld.pallet_id = p.id AND ld.cancelada = 0),
                      b.codigo, po.nivel,
                      -- Montado por: quem encerrou; sem encerramento (pallet que foi
                      -- direto para o endereço), quem fez a última leitura nele.
                      COALESCE(p.encerrado_em,
                               (SELECT MAX(COALESCE(l.lido_em, l.registrado_em)) FROM leitura l
                                 WHERE l.pallet_id = p.id AND l.cancelada = 0),
                               (SELECT MAX(COALESCE(ld.lido_em, ld.registrado_em)) FROM leitura_devolucao ld
                                 WHERE ld.pallet_id = p.id AND ld.cancelada = 0)),
                      COALESCE(ue.nome,
                               (SELECT u.nome FROM leitura l JOIN usuario u ON u.id = l.usuario_id
                                 WHERE l.pallet_id = p.id AND l.cancelada = 0 ORDER BY l.id DESC LIMIT 1),
                               (SELECT u.nome FROM leitura_devolucao ld JOIN usuario u ON u.id = ld.usuario_id
                                 WHERE ld.pallet_id = p.id AND ld.cancelada = 0 ORDER BY ld.id DESC LIMIT 1)),
                      exp.expedido_em, uexp.nome,
                      bx.baixado_em, ubx.nome, bx.tipo,
                      -- Último retorno do pallet (não cancelado): situação e o
                      -- que foi conferido, quando voltou faltando.
                      (SELECT r.status || '|' || COALESCE(r.tratativa, '') || '|' || n2.numero || '|'
                              || COALESCE(r.nfs_refaturamento, '') || '|' || COALESCE(r.tratado_em, '')
                         FROM retorno_pallet rp
                         JOIN retorno r ON r.id = rp.retorno_id
                         JOIN nota_fiscal n2 ON n2.id = r.nota_fiscal_id
                        WHERE rp.pallet_id = p.id AND r.status <> 'CANCELADO'
                        ORDER BY rp.id DESC LIMIT 1),
                      -- Só com o retorno aberto: tratado, a falta já saiu do
                      -- pallet (reentrega) ou o pallet saiu (refaturamento,
                      -- descarte), e as leituras voltam a ser a verdade.
                      (SELECT CASE WHEN rp.status = 'CONFERIDO_FALTA' AND rp.manual = 0
                                        AND r.status IN ('EM_CONFERENCIA','AGUARDANDO_TRATATIVA')
                                   THEN (SELECT COUNT(*) FROM retorno_leitura rl
                                          WHERE rl.retorno_pallet_id = rp.id AND rl.cancelada = 0) END
                         FROM retorno_pallet rp
                         JOIN retorno r ON r.id = rp.retorno_id
                        WHERE rp.pallet_id = p.id AND r.status <> 'CANCELADO'
                        ORDER BY rp.id DESC LIMIT 1)
                 FROM pallet p
                 LEFT JOIN nota_fiscal nf ON nf.id = p.nota_fiscal_id
                 LEFT JOIN nota_devolucao nd ON nd.id = p.nota_devolucao_id
                 LEFT JOIN enderecamento e ON e.pallet_id = p.id AND e.liberado_em IS NULL
                 LEFT JOIN posicao po ON po.id = e.posicao_id
                 LEFT JOIN box b ON b.id = po.box_id
                 LEFT JOIN usuario ue ON ue.id = p.encerrado_por
                 -- Última saída viva do pallet: expedição não estornada e sem retorno.
                 LEFT JOIN expedicao_pallet ep ON ep.id = (
                     SELECT MAX(ep2.id) FROM expedicao_pallet ep2
                       JOIN expedicao e2 ON e2.id = ep2.expedicao_id
                      WHERE ep2.pallet_id = p.id
                        AND e2.estornado_em IS NULL AND ep2.retornado_em IS NULL)
                 LEFT JOIN expedicao exp ON exp.id = ep.expedicao_id
                 LEFT JOIN usuario uexp ON uexp.id = exp.expedido_por
                 LEFT JOIN baixa_devolucao bx ON bx.nota_devolucao_id = p.nota_devolucao_id
                 LEFT JOIN usuario ubx ON ubx.id = bx.baixado_por
                 $onde
                ORDER BY p.codigo DESC""",
            *args.toTypedArray(),
        ) {
            val box = it.getString(8)
            val nivel = it.getInt(9)
            val status = it.getString(3)
            val expedidoEm = it.getString(12)
            val baixadoEm = it.getString(14)
            val noCd = status in setOf("EM_USO", "ENCERRADO", "ENDERECADO")
            // status|tratativa|NF|nfs|tratado_em do último retorno do pallet.
            val retorno = it.getString(17)?.split('|')
            val conferidasNoRetorno = it.getInt(18).takeIf { _ -> !it.wasNull() }
            val (retStatus, retTratativa, retNota) = retorno ?: listOf("", "", "")
            val retNfs = retorno?.getOrNull(3).orEmpty()
            val retTratadoEm = retorno?.getOrNull(4)?.ifEmpty { null }
            val saiuPorRetorno = status == "ESVAZIADO" && retTratativa in setOf("REFATURAMENTO", "DESCARTE")
            val textoRetorno = when {
                retorno == null -> null
                noCd && retStatus == "EM_CONFERENCIA" -> "Retorno NF $retNota · em conferência"
                noCd && retStatus == "AGUARDANDO_TRATATIVA" -> "Retorno NF $retNota · aguardando tratativa"
                noCd && retTratativa == "REENTREGA" -> "Retorno NF $retNota · para reentrega"
                saiuPorRetorno && retTratativa == "REFATURAMENTO" -> "Retorno NF $retNota · refaturado ($retNfs)"
                saiuPorRetorno && retTratativa == "DESCARTE" -> "Retorno NF $retNota · descartado"
                status == "ESVAZIADO" && retTratativa == "REENTREGA" ->
                    "Retorno NF $retNota · não voltou (falta na reentrega)"
                else -> null
            }

            LinhaEstoque(
                palletCodigo = it.getString(1),
                tipo = it.getString(2),
                status = status,
                situacao = when (status) {
                    "EM_USO", "ENCERRADO", "ENDERECADO" -> "EM_ESTOQUE"
                    "EXPEDIDO" -> "EXPEDIDO"
                    else -> "FORA_DE_CIRCULACAO"
                },
                notaNumero = it.getString(4),
                cliente = it.getString(5),
                bonificacao = it.getInt(6) == 1,
                // Voltou faltando: o estoque é o que foi conferido na volta, não o
                // que saiu no pallet.
                qtdCaixas = if (noCd && conferidasNoRetorno != null) conferidasNoRetorno else it.getInt(7),
                endereco = box?.let { b -> "$b nível $nivel" },
                montadoEm = it.getString(10),
                montadoPor = it.getString(11),
                // Pallet de venda sai por expedição; o de devolução, pela baixa;
                // o que voltou de uma NF recusada, pela tratativa do retorno.
                saidaEm = when {
                    saiuPorRetorno -> retTratadoEm
                    else -> expedidoEm ?: baixadoEm
                },
                saidaPor = if (saiuPorRetorno) null else it.getString(13) ?: it.getString(15),
                saidaTipo = when {
                    saiuPorRetorno -> retTratativa
                    expedidoEm != null -> "EXPEDICAO"
                    baixadoEm != null -> "BAIXA_DEVOLUCAO"
                    else -> null
                },
                retorno = textoRetorno,
            )
        }

        // Pallet encerrado sem nenhuma caixa não é estoque: é um estrado vazio.
        // Contá-lo infla a quantidade de pallets e não representa mercadoria
        // nenhuma. Em FORA_DE_CIRCULACAO ele é justamente o que se quer ver.
        val visiveis =
            if (f.situacao.uppercase() in setOf("EM_ESTOQUE", "EXPEDIDO")) {
                linhas.filter { it.qtdCaixas > 0 }
            } else {
                linhas
            }

        RelatorioEstoque(
            linhas = visiveis,
            totais = TotaisEstoque(
                pallets = visiveis.size,
                caixas = visiveis.sumOf { it.qtdCaixas },
                semEndereco = visiveis.count { it.situacao == "EM_ESTOQUE" && it.endereco == null },
            ),
        )
    }

    /** Conteúdo de um pallet, para a linha abrir sem sair da tela. */
    fun itens(palletCodigo: String): List<ItemDoPallet> = db.leitura { c ->
        val (id, status, tipo) = c.buscarUm(
            "SELECT id, status, tipo FROM pallet WHERE codigo = ?", palletCodigo.trim().uppercase(),
        ) { Triple(it.getLong(1), it.getString(2), it.getString(3)) } ?: return@leitura emptyList()

        // Devolução conta em unidades, na tabela dela: sem este desvio o P-DEV
        // cheio abria escrito "sem caixas neste pallet".
        if (tipo == "DEVOLUCAO") {
            return@leitura c.buscarTodos(
                """SELECT i.id, pr.codigo, pr.descricao, SUM(l.qtd_un)
                     FROM leitura_devolucao l
                     JOIN item_devolucao i ON i.id = l.item_devolucao_id
                     JOIN produto pr ON pr.id = l.produto_id
                    WHERE l.pallet_id = ? AND l.cancelada = 0
                    GROUP BY i.id ORDER BY pr.codigo""",
                id,
            ) { ItemDoPallet(it.getLong(1), it.getString(2), it.getString(3), it.getInt(4)) }
        }

        // Voltou faltando: no pallet está o que foi conferido na volta, a mesma
        // regra da quantidade na linha do estoque.
        val conferencia = if (status !in setOf("EM_USO", "ENCERRADO", "ENDERECADO")) null else c.buscarUm(
            """SELECT CASE WHEN rp.status = 'CONFERIDO_FALTA' AND rp.manual = 0
                                AND r.status IN ('EM_CONFERENCIA','AGUARDANDO_TRATATIVA') THEN rp.id END
                 FROM retorno_pallet rp JOIN retorno r ON r.id = rp.retorno_id
                WHERE rp.pallet_id = ? AND r.status <> 'CANCELADO'
                ORDER BY rp.id DESC LIMIT 1""",
            id,
        ) { it.getLong(1).takeIf { _ -> !it.wasNull() } }
        if (conferencia != null) {
            return@leitura c.buscarTodos(
                """SELECT (SELECT MIN(l.item_nota_id) FROM leitura l
                            WHERE l.pallet_id = ? AND l.produto_id = rpp.produto_id AND l.cancelada = 0),
                          pr.codigo, pr.descricao,
                          (SELECT COUNT(*) FROM retorno_leitura rl
                            WHERE rl.retorno_pallet_id = rpp.retorno_pallet_id
                              AND rl.produto_id = rpp.produto_id AND rl.cancelada = 0)
                     FROM retorno_pallet_produto rpp JOIN produto pr ON pr.id = rpp.produto_id
                    WHERE rpp.retorno_pallet_id = ? ORDER BY pr.codigo""",
                id, conferencia,
            ) { ItemDoPallet(it.getLong(1), it.getString(2), it.getString(3), it.getInt(4)) }
                .filter { it.quantidade > 0 }
        }

        c.buscarTodos(
            """SELECT i.id, pr.codigo, pr.descricao, COUNT(*)
                 FROM leitura l
                 JOIN item_nota i ON i.id = l.item_nota_id
                 JOIN produto pr ON pr.id = i.produto_id
                WHERE l.pallet_id = ? AND l.cancelada = 0
                GROUP BY i.id ORDER BY pr.codigo""",
            id,
        ) { ItemDoPallet(it.getLong(1), it.getString(2), it.getString(3), it.getInt(4)) }
    }
}
