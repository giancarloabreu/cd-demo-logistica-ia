package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import com.fasterxml.jackson.annotation.JsonProperty
import java.sql.Connection
import java.time.Instant
import java.util.UUID

/** Quantas caixas de um item vão neste pallet — o total, não o acréscimo. */
data class CaixasDoItem(val itemId: Long, val caixas: Int)

/** Um item da NF visto pelo desembarque: o que a NF pede, o que já veio, o que está neste pallet. */
data class ItemDesembarque(
    val itemId: Long,
    @get:JsonProperty("nItem") val nItem: Int,
    val produtoCodigo: String,
    val produtoDescricao: String,
    val qtdPrevista: Int,
    val qtdSeparada: Int,
    val nestePallet: Int,
    val status: String,
) {
    /** O teto deste item neste pallet: o que falta na NF mais o que já está aqui. */
    val limite: Int get() = (qtdPrevista - qtdSeparada) + nestePallet
}

data class DesembarqueResumo(
    val id: Long,
    val notaId: Long,
    val notaNumero: String,
    val cliente: String,
    val palletCodigo: String,
    val responsavelId: Long,
    val responsavelNome: String,
    val status: String,
    val qtdCaixas: Int,
    val abertoEm: String,
    val itens: List<ItemDesembarque>,
)

/**
 * Desembarque na retaguarda: o pallet montado por quantidade.
 *
 * A carreta chega e é descarregada direto no pallet — produto por produto,
 * caixa contada na mão, sem bipe. Antes isso só existia no coletor, bipe a
 * bipe; quem desembarca de uma vez lançava depois, ou não lançava. Aqui o
 * admin abre o desembarque, escolhe no cadastro **quem desembarcou** (é o nome
 * que fica no pallet, em "Montado por") e diz quantas caixas de cada item vão
 * naquele pallet. Nunca mais do que a NF pede: o teto é o que falta.
 *
 * Cada caixa lançada vira uma `leitura` normal, igual à do bipe — é o que faz
 * o estoque, o cartão do pallet, a expedição e o relatório enxergarem o pallet
 * sem saber que ele não foi bipado. O que as distingue é `desembarque_id`.
 *
 * Ver docs/TELAS-RETAGUARDA.md §2.1.
 */
class DesembarqueServico(private val db: Db) {

    /** O desembarque aberto desta NF, se houver — a tela retoma por ele. */
    fun emAberto(notaId: Long): DesembarqueResumo? = db.leitura { c ->
        c.buscarUm(
            "SELECT id FROM desembarque WHERE nota_fiscal_id = ? AND status = 'ABERTO' ORDER BY id DESC LIMIT 1",
            notaId,
        ) { it.getLong(1) }?.let { resumo(c, it) }
    }

    /** Abre o desembarque: cria o pallet da NF e amarra o responsável. */
    fun abrir(usuarioId: Long, notaId: Long, responsavelId: Long): DesembarqueResumo = db.transacao { c ->
        val nota = c.buscarUm(
            "SELECT numero, status FROM nota_fiscal WHERE id = ?", notaId,
        ) { it.getString(1) to it.getString(2) } ?: throw Erros.naoEncontrado("Nota fiscal")
        if (nota.second in setOf("EXPEDIDA", "RETORNO_PARCIAL")) {
            throw Erros.conflito(
                "NOTA_JA_EXPEDIDA",
                "A NF ${nota.first} já foi expedida: não há o que desembarcar",
            )
        }
        c.buscarUm(
            """SELECT p.codigo FROM desembarque d JOIN pallet p ON p.id = d.pallet_id
                WHERE d.nota_fiscal_id = ? AND d.status = 'ABERTO'""",
            notaId,
        ) { it.getString(1) }?.let {
            throw Erros.conflito(
                "DESEMBARQUE_EM_ABERTO",
                "Esta NF já tem um desembarque aberto no pallet $it. Encerre-o antes de abrir outro.",
                mapOf("palletCodigo" to it),
            )
        }

        val responsavel = c.buscarUm(
            "SELECT nome, ativo FROM usuario WHERE id = ?", responsavelId,
        ) { it.getString(1) to (it.getInt(2) == 1) } ?: throw Erros.naoEncontrado("Usuário")
        if (!responsavel.second) {
            throw Erros.conflito("USUARIO_INATIVO", "${responsavel.first} está inativo: escolha outro responsável")
        }

        val agora = Instant.now().toString()
        val codigo = Sequencias.proximo(c, Sequencias.PALLET)
        // Já nasce da NF e em uso pelo responsável: é a mesma trava
        // anti-mistura do bipe, só que decidida na abertura.
        val palletId = c.inserir(
            """INSERT INTO pallet (codigo, tipo, status, nota_fiscal_id, usuario_atual_id,
                                   criado_em, criado_por, impresso_em)
               VALUES (?, 'SEPARACAO', 'EM_USO', ?, ?, ?, ?, ?)""",
            codigo, notaId, responsavelId, agora, usuarioId, agora,
        )
        val id = c.inserir(
            """INSERT INTO desembarque
               (nota_fiscal_id, pallet_id, usuario_id, status, aberto_em, aberto_por)
               VALUES (?,?,?, 'ABERTO', ?, ?)""",
            notaId, palletId, responsavelId, agora, usuarioId,
        )
        auditar(c, notaId, "DESEMBARQUE_ABERTO", usuarioId, "Pallet $codigo, desembarcado por ${responsavel.first}")
        resumo(c, id)
    }

    /**
     * Quantas caixas de cada item vão neste pallet. O número é o total, não o
     * acréscimo: digitar 8 onde havia 10 tira duas — é como o operador corrige
     * o que contou errado, sem precisar de outra tela.
     */
    fun lancar(usuarioId: Long, desembarqueId: Long, itens: List<CaixasDoItem>): DesembarqueResumo =
        db.transacao { c ->
            val d = exigirAberto(c, desembarqueId)
            val pedidos = itens.filter { it.caixas >= 0 }.groupBy { it.itemId }
                .map { (itemId, lista) -> CaixasDoItem(itemId, lista.last().caixas) }
            if (pedidos.isEmpty()) {
                throw Erros.invalido("NADA_A_LANCAR", "Informe as caixas de ao menos um item")
            }

            val agora = Instant.now().toString()
            pedidos.forEach { pedido ->
                val item = c.buscarUm(
                    """SELECT i.produto_id, i.n_item, i.qtd_prevista, i.qtd_separada, i.status,
                              (SELECT COUNT(*) FROM leitura l
                                WHERE l.desembarque_id = ? AND l.item_nota_id = i.id AND l.cancelada = 0)
                         FROM item_nota i WHERE i.id = ? AND i.nota_fiscal_id = ?""",
                    desembarqueId, pedido.itemId, d.notaId,
                ) {
                    ItemAlvo(it.getLong(1), it.getInt(2), it.getInt(3), it.getInt(4), it.getString(5), it.getInt(6))
                } ?: throw Erros.naoEncontrado("Item da NF")

                val diferenca = pedido.caixas - item.nestePallet
                if (diferenca == 0) return@forEach
                if (diferenca > 0) {
                    if (item.status == "FECHADO_DIVERGENCIA") {
                        throw Erros.conflito(
                            "ITEM_FECHADO",
                            "O item ${item.nItem} foi fechado com divergência. Reabra antes de lançar caixas.",
                            mapOf("nItem" to item.nItem),
                        )
                    }
                    val falta = item.qtdPrevista - item.qtdSeparada
                    if (diferenca > falta) {
                        throw Erros.conflito(
                            "QUANTIDADE_MAIOR_QUE_A_NF",
                            "O item ${item.nItem} tem ${item.qtdPrevista} caixa(s) na NF e " +
                                "${item.qtdSeparada} já lançada(s): cabem ${item.nestePallet + falta} neste pallet",
                            mapOf("limite" to item.nestePallet + falta, "falta" to falta),
                        )
                    }
                    repeat(diferenca) {
                        c.inserir(
                            """INSERT INTO leitura
                               (idempotency_key, pallet_id, item_nota_id, produto_id, codigo_lido,
                                usuario_id, coletor_id, lido_em, registrado_em, desembarque_id)
                               VALUES (?,?,?,?,?,?,NULL,NULL,?,?)""",
                            UUID.randomUUID().toString(), d.palletId, pedido.itemId, item.produtoId,
                            codigoDoProduto(c, item.produtoId), d.responsavelId, agora, desembarqueId,
                        )
                    }
                } else {
                    // Tirar só o que este desembarque pôs, da última para a
                    // primeira: caixa bipada no coletor não sai por aqui.
                    c.buscarTodos(
                        """SELECT id FROM leitura
                            WHERE desembarque_id = ? AND item_nota_id = ? AND cancelada = 0
                            ORDER BY id DESC LIMIT ?""",
                        desembarqueId, pedido.itemId, -diferenca,
                    ) { it.getLong(1) }.forEach { leituraId ->
                        c.atualizar(
                            """UPDATE leitura SET cancelada = 1, cancelada_em = ?, cancelada_por = ?,
                                      cancelamento_motivo = 'Correção no desembarque'
                                WHERE id = ?""",
                            agora, usuarioId, leituraId,
                        )
                    }
                }
                recalcularItem(c, pedido.itemId)
                acertarCadeado(c, pedido.itemId, d.responsavelId, agora)
            }
            atualizarStatusDaNota(c, d.notaId)
            resumo(c, desembarqueId)
        }

    /**
     * Fecha o pallet: ele vai para a fila de endereçamento como qualquer
     * outro. Quem encerra, para o estoque, é o responsável pelo desembarque —
     * é o nome que o supervisor procura em "Montado por".
     */
    fun encerrar(usuarioId: Long, desembarqueId: Long): DesembarqueResumo = db.transacao { c ->
        val d = exigirAberto(c, desembarqueId)
        val caixas = caixasDe(c, desembarqueId)
        if (caixas == 0) {
            throw Erros.conflito(
                "DESEMBARQUE_VAZIO",
                "Nenhuma caixa foi lançada neste pallet. Lance as caixas ou cancele o desembarque.",
            )
        }
        val agora = Instant.now().toString()
        c.atualizar(
            """UPDATE pallet SET status = 'ENCERRADO', usuario_atual_id = NULL,
                      encerrado_em = ?, encerrado_por = ? WHERE id = ?""",
            agora, d.responsavelId, d.palletId,
        )
        c.atualizar(
            "UPDATE desembarque SET status = 'ENCERRADO', encerrado_em = ?, encerrado_por = ? WHERE id = ?",
            agora, usuarioId, desembarqueId,
        )
        auditar(
            c, d.notaId, "DESEMBARQUE_ENCERRADO", usuarioId,
            "Pallet ${d.palletCodigo} com $caixas caixa(s), desembarcado por ${d.responsavelNome}",
        )
        resumo(c, desembarqueId)
    }

    /**
     * Desembarque aberto por engano. As caixas lançadas saem (ficam marcadas
     * como excluídas, não apagadas) e o pallet sai de circulação — ninguém
     * precisa procurar na rua um pallet que nunca foi montado.
     */
    fun cancelar(usuarioId: Long, desembarqueId: Long, justificativa: String): DesembarqueResumo =
        db.transacao { c ->
            val d = exigirAberto(c, desembarqueId)
            if (justificativa.isBlank()) {
                throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe por que o desembarque foi cancelado")
            }
            val agora = Instant.now().toString()
            val itens = c.buscarTodos(
                "SELECT DISTINCT item_nota_id FROM leitura WHERE desembarque_id = ? AND cancelada = 0",
                desembarqueId,
            ) { it.getLong(1) }
            c.atualizar(
                """UPDATE leitura SET cancelada = 1, cancelada_em = ?, cancelada_por = ?,
                          cancelamento_motivo = ?
                    WHERE desembarque_id = ? AND cancelada = 0""",
                agora, usuarioId, "Desembarque cancelado: $justificativa", desembarqueId,
            )
            itens.forEach {
                recalcularItem(c, it)
                acertarCadeado(c, it, d.responsavelId, agora)
            }
            atualizarStatusDaNota(c, d.notaId)
            c.atualizar(
                "UPDATE pallet SET status = 'ESVAZIADO', usuario_atual_id = NULL WHERE id = ?", d.palletId,
            )
            c.atualizar(
                """UPDATE desembarque SET status = 'CANCELADO', encerrado_em = ?, encerrado_por = ?,
                          justificativa = ? WHERE id = ?""",
                agora, usuarioId, justificativa, desembarqueId,
            )
            auditar(
                c, d.notaId, "DESEMBARQUE_CANCELADO", usuarioId,
                "Pallet ${d.palletCodigo}: $justificativa",
            )
            resumo(c, desembarqueId)
        }

    // ------------------------------------------------------------------ apoio

    private data class ItemAlvo(
        val produtoId: Long,
        val nItem: Int,
        val qtdPrevista: Int,
        val qtdSeparada: Int,
        val status: String,
        val nestePallet: Int,
    )

    private data class Aberto(
        val notaId: Long,
        val palletId: Long,
        val palletCodigo: String,
        val responsavelId: Long,
        val responsavelNome: String,
    )

    private fun exigirAberto(c: Connection, desembarqueId: Long): Aberto = c.buscarUm(
        """SELECT d.nota_fiscal_id, d.pallet_id, p.codigo, d.usuario_id, u.nome, d.status
             FROM desembarque d
             JOIN pallet p ON p.id = d.pallet_id
             JOIN usuario u ON u.id = d.usuario_id
            WHERE d.id = ?""",
        desembarqueId,
    ) {
        Aberto(it.getLong(1), it.getLong(2), it.getString(3), it.getLong(4), it.getString(5)) to it.getString(6)
    }?.let { (aberto, status) ->
        if (status != "ABERTO") {
            throw Erros.conflito(
                "DESEMBARQUE_ENCERRADO",
                "Este desembarque já foi ${status.lowercase()}. Abra outro para continuar.",
            )
        }
        aberto
    } ?: throw Erros.naoEncontrado("Desembarque")

    /**
     * A leitura exige um código. Vale o GTIN que o produto já tem — o mesmo
     * que o bipe gravaria —, e o código interno dele quando não há nenhum.
     */
    private fun codigoDoProduto(c: Connection, produtoId: Long): String = c.buscarUm(
        """SELECT codigo FROM produto_codigo_barras
            WHERE produto_id = ? AND ativo = 1 ORDER BY confirmado DESC, id LIMIT 1""",
        produtoId,
    ) { it.getString(1) } ?: c.buscarUm("SELECT codigo FROM produto WHERE id = ?", produtoId) { it.getString(1) }
        .orEmpty()

    private fun caixasDe(c: Connection, desembarqueId: Long): Int = c.buscarUm(
        "SELECT COUNT(*) FROM leitura WHERE desembarque_id = ? AND cancelada = 0", desembarqueId,
    ) { it.getInt(1) } ?: 0

    /**
     * Lançou tudo que a NF pede, o cadeado fecha sozinho — o desembarque é a
     * conferência. Tirou caixa depois, ele reabre: fechado com menos do que a
     * NF pede seria divergência, e não é disso que se trata.
     */
    private fun acertarCadeado(c: Connection, itemId: Long, responsavelId: Long, agora: String) {
        c.atualizar(
            """UPDATE item_nota SET status = 'FECHADO', fechado_em = ?, fechado_por = ?
                WHERE id = ? AND qtd_separada >= qtd_prevista
                  AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
            agora, responsavelId, itemId,
        )
        c.atualizar(
            """UPDATE item_nota SET status = 'EM_SEPARACAO', fechado_em = NULL, fechado_por = NULL
                WHERE id = ? AND qtd_separada < qtd_prevista AND status = 'FECHADO'""",
            itemId,
        )
        // Item fechado some do coletor: a marca de "separando agora" ficaria
        // presa e travaria a próxima leitura de quem a tivesse.
        c.atualizar(
            """DELETE FROM item_em_separacao WHERE item_nota_id = ?
                AND (SELECT status FROM item_nota WHERE id = ?) IN ('FECHADO','FECHADO_DIVERGENCIA')""",
            itemId, itemId,
        )
    }

    private fun auditar(c: Connection, notaId: Long, acao: String, usuarioId: Long, detalhe: String) {
        c.inserir(
            """INSERT INTO log_auditoria (entidade, entidade_id, acao, usuario_id, em, detalhe_json)
               VALUES ('nota_fiscal', ?, ?, ?, ?, ?)""",
            notaId, acao, usuarioId, Instant.now().toString(), detalhe,
        )
    }

    private fun resumo(c: Connection, desembarqueId: Long): DesembarqueResumo {
        val d = c.buscarUm(
            """SELECT d.id, d.nota_fiscal_id, n.numero, n.dest_nome, p.codigo,
                      d.usuario_id, u.nome, d.status, d.aberto_em
                 FROM desembarque d
                 JOIN nota_fiscal n ON n.id = d.nota_fiscal_id
                 JOIN pallet p ON p.id = d.pallet_id
                 JOIN usuario u ON u.id = d.usuario_id
                WHERE d.id = ?""",
            desembarqueId,
        ) {
            DesembarqueResumo(
                it.getLong(1), it.getLong(2), it.getString(3), it.getString(4), it.getString(5),
                it.getLong(6), it.getString(7), it.getString(8), 0, it.getString(9), emptyList(),
            )
        } ?: throw Erros.naoEncontrado("Desembarque")

        val itens = c.buscarTodos(
            """SELECT i.id, i.n_item, pr.codigo, pr.descricao, i.qtd_prevista, i.qtd_separada, i.status,
                      (SELECT COUNT(*) FROM leitura l
                        WHERE l.desembarque_id = ? AND l.item_nota_id = i.id AND l.cancelada = 0)
                 FROM item_nota i JOIN produto pr ON pr.id = i.produto_id
                WHERE i.nota_fiscal_id = ? ORDER BY i.n_item""",
            desembarqueId, d.notaId,
        ) {
            ItemDesembarque(
                itemId = it.getLong(1), nItem = it.getInt(2),
                produtoCodigo = it.getString(3), produtoDescricao = it.getString(4),
                qtdPrevista = it.getInt(5), qtdSeparada = it.getInt(6),
                status = it.getString(7), nestePallet = it.getInt(8),
            )
        }
        return d.copy(itens = itens, qtdCaixas = itens.sumOf { it.nestePallet })
    }
}
