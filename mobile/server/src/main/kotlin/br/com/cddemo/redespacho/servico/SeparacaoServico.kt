package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.ErroNegocio
import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.dominio.Gtin
import br.com.cddemo.redespacho.dominio.ResultadoGtin
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import com.fasterxml.jackson.annotation.JsonProperty
import java.sql.Connection
import java.time.Instant

data class PalletCriado(val codigo: String)

data class ResultadoLeitura(
    val resultado: String,
    val qtdSeparada: Int,
    val qtdPrevista: Int,
    val itemCompleto: Boolean,
)

data class PalletDoItem(val codigo: String, val status: String, val quantidade: Int)

data class ItemResumo(
    val id: Long,
    // Sem a anotação o Jackson publica "nitem": ele não deriva camelCase de
    // letra única inicial. Ver docs/API.md.
    @get:JsonProperty("nItem") val nItem: Int,
    val produtoCodigo: String,
    val produtoDescricao: String,
    val qtdPrevista: Int,
    val qtdSeparada: Int,
    val status: String,
    val separadores: List<String>,
    /** Motivo do fechamento com divergência — precisa voltar para a tela. */
    val justificativa: String? = null,
    val fechadoPor: String? = null,
)

/** Tudo que o cartão do pallet precisa, numa consulta só. */
data class DadosCartao(
    val palletCodigo: String,
    val instrucao: String,
    val notaNumero: String,
    val cliente: String,
    /** Logradouro e número. O resto do endereço vem em partes: o cartão quebra linha por parte. */
    val endereco: String,
    val complemento: String?,
    val totalCaixas: Int,
    val itens: List<TrioItem>,
    val bonificacao: Boolean,
    val bairro: String? = null,
    /** Município/UF. */
    val cidade: String? = null,
    val cep: String? = null,
    /** Null enquanto o pallet está aberto. */
    val encerradoEm: String? = null,
    val encerradoPor: String? = null,
)

data class TrioItem(val codigo: String, val descricao: String, val quantidade: Int)

data class NotaResumo(
    val id: Long,
    val numero: String,
    val cliente: String,
    val status: String,
    val qtdPrevista: Int,
    val qtdSeparada: Int,
    val bonificacao: Boolean = false,
)

/**
 * Núcleo da separação: pallets, leitura e cadeado.
 *
 * O servidor é a autoridade sobre contagem, estado de cadeado e vínculo de
 * pallet. O coletor antecipa o incremento na tela por fluidez, mas desfaz se a
 * resposta recusar. Ver docs/API.md §6.
 */
class SeparacaoServico(private val db: Db) {

    // ------------------------------------------------------------------ consulta

    fun notasDaInstrucao(instrucaoId: Long): List<NotaResumo> = db.leitura { c ->
        c.buscarTodos(
            """SELECT n.id, n.numero, n.dest_nome, n.status,
                      COALESCE(SUM(i.qtd_prevista),0), COALESCE(SUM(i.qtd_separada),0),
                      n.bonificacao
                 FROM nota_fiscal n
                 LEFT JOIN item_nota i ON i.nota_fiscal_id = n.id
                WHERE n.instrucao_id = ?
                GROUP BY n.id ORDER BY n.numero""",
            instrucaoId,
        ) {
            NotaResumo(it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                it.getInt(5), it.getInt(6), it.getInt(7) == 1)
        }
    }

    /**
     * Busca notas por situação e número, atravessando instruções.
     *
     * A devolução e a reentrega partem da NF, não da instrução: quem procura
     * sabe o número que veio no papel, não em qual romaneio ela entrou.
     */
    fun buscarNotas(status: List<String>?, numero: String?): List<NotaResumo> = db.leitura { c ->
        val condicoes = mutableListOf<String>()
        val args = mutableListOf<Any?>()
        status?.takeIf { it.isNotEmpty() }?.let {
            condicoes += "n.status IN (${it.joinToString(",") { _ -> "?" }})"
            args.addAll(it)
        }
        numero?.trim()?.takeIf { it.isNotEmpty() }?.let {
            condicoes += "n.numero LIKE ?"
            args.add("%$it%")
        }
        val onde = if (condicoes.isEmpty()) "" else "WHERE " + condicoes.joinToString(" AND ")

        c.buscarTodos(
            """SELECT n.id, n.numero, n.dest_nome, n.status,
                      COALESCE(SUM(i.qtd_prevista),0), COALESCE(SUM(i.qtd_separada),0),
                      n.bonificacao
                 FROM nota_fiscal n
                 LEFT JOIN item_nota i ON i.nota_fiscal_id = n.id
                 $onde
                GROUP BY n.id ORDER BY n.numero DESC LIMIT 200""",
            *args.toTypedArray(),
        ) {
            NotaResumo(it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                it.getInt(5), it.getInt(6), it.getInt(7) == 1)
        }
    }

    fun itensDaNota(notaId: Long): List<ItemResumo> = db.leitura { c ->
        c.buscarTodos(
            """SELECT i.id, i.n_item, p.codigo, p.descricao, i.qtd_prevista, i.qtd_separada,
                      i.status, i.justificativa, u.nome
                 FROM item_nota i
                 JOIN produto p ON p.id = i.produto_id
                 LEFT JOIN usuario u ON u.id = i.fechado_por
                WHERE i.nota_fiscal_id = ? ORDER BY i.n_item""",
            notaId,
        ) {
            ItemResumo(it.getLong(1), it.getInt(2), it.getString(3), it.getString(4),
                it.getInt(5), it.getInt(6), it.getString(7), emptyList(),
                it.getString(8), it.getString(9))
        }.map { item ->
            // Quem está separando este item agora — resolve o "quem pegou esse
            // item?" da tela de acompanhamento.
            item.copy(
                separadores = c.buscarTodos(
                    """SELECT u.nome FROM item_em_separacao s
                         JOIN usuario u ON u.id = s.usuario_id
                        WHERE s.item_nota_id = ?""",
                    item.id,
                ) { it.getString(1) }
            )
        }
    }

    // ------------------------------------------------------------------- pallets

    fun criarPallets(usuarioId: Long, tipo: String, quantidade: Int): List<PalletCriado> {
        if (quantidade !in 1..50) {
            throw Erros.invalido("QUANTIDADE_INVALIDA", "Quantidade deve estar entre 1 e 50")
        }
        val sequencia = when (tipo) {
            "SEPARACAO" -> Sequencias.PALLET
            "DEVOLUCAO" -> Sequencias.PALLET_DEVOLUCAO
            else -> throw Erros.invalido("TIPO_INVALIDO", "Tipo de pallet inválido: $tipo")
        }
        return db.transacao { c ->
            val agora = Instant.now().toString()
            Sequencias.proximos(c, sequencia, quantidade).map { codigo ->
                c.inserir(
                    """INSERT INTO pallet (codigo, tipo, status, criado_em, criado_por, impresso_em)
                       VALUES (?,?, 'CRIADO', ?, ?, ?)""",
                    codigo, tipo, agora, usuarioId, agora,
                )
                PalletCriado(codigo)
            }
        }
    }

    /** Vincula o pallet ao separador. Um pallet, um separador. */
    fun selecionarPallet(usuarioId: Long, codigo: String, notaId: Long?): Pallet = db.transacao { c ->
        val p = buscarPallet(c, codigo) ?: throw Erros.conflito(
            "PALLET_NAO_ENCONTRADO", "Pallet $codigo não encontrado", mapOf("palletCodigo" to codigo),
        )
        when (p.status) {
            "ENCERRADO" -> throw Erros.conflito("PALLET_ENCERRADO", "Pallet $codigo já foi encerrado")
            "FUNDIDO", "ESVAZIADO" -> throw Erros.conflito("PALLET_ENCERRADO", "Pallet $codigo saiu de circulação")
            "EXPEDIDO" -> throw Erros.conflito("PALLET_JA_EXPEDIDO", "Pallet $codigo já foi expedido")
            "ENDERECADO" -> throw Erros.conflito("PALLET_ENCERRADO", "Pallet $codigo já está endereçado")
        }
        // Pallet de devolução não recebe separação: a mercadoria sairia depois
        // no descarte da devolução. O coletor usa o mesmo campo para os dois.
        if (p.tipo != "SEPARACAO") {
            throw Erros.conflito("PALLET_DE_DEVOLUCAO", "Pallet $codigo é de devolução: não recebe separação")
        }
        if (p.usuarioAtualId != null && p.usuarioAtualId != usuarioId) {
            val nome = c.buscarUm("SELECT nome FROM usuario WHERE id = ?", p.usuarioAtualId) { it.getString(1) }
            throw Erros.conflito(
                "PALLET_EM_USO_POR_OUTRO", "Pallet em uso por ${nome ?: "outro separador"}",
                mapOf("palletCodigo" to codigo, "usuario" to nome),
            )
        }
        // Trava anti-mistura: o pallet se amarra à NF na primeira leitura e não
        // muda mais.
        if (notaId != null && p.notaFiscalId != null && p.notaFiscalId != notaId) {
            throw palletDeOutraNota(c, p)
        }
        c.atualizar("UPDATE pallet SET usuario_atual_id = ? WHERE id = ?", usuarioId, p.id)
        p.copy(usuarioAtualId = usuarioId)
    }

    fun encerrarPallet(usuarioId: Long, codigo: String): Pallet = db.transacao { c ->
        val p = buscarPallet(c, codigo) ?: throw Erros.conflito(
            "PALLET_NAO_ENCONTRADO", "Pallet $codigo não encontrado",
        )
        // Encerrar um pallet que já saiu de circulação o traria de volta ao
        // estoque com as caixas que tinha: as de um refaturado ou descartado,
        // por exemplo, que já foram embora.
        when (p.status) {
            "ENCERRADO", "ENDERECADO" -> throw Erros.conflito("PALLET_ENCERRADO", "Pallet $codigo já foi encerrado")
            "EXPEDIDO" -> throw Erros.conflito("PALLET_JA_EXPEDIDO", "Pallet $codigo já foi expedido")
            "FUNDIDO", "ESVAZIADO" -> throw Erros.conflito("PALLET_ENCERRADO", "Pallet $codigo saiu de circulação")
        }
        if (p.usuarioAtualId != null && p.usuarioAtualId != usuarioId) {
            throw Erros.conflito("PALLET_NAO_E_SEU", "Este pallet está em uso por outro separador")
        }
        val agora = Instant.now().toString()
        c.atualizar(
            """UPDATE pallet SET status='ENCERRADO', usuario_atual_id=NULL,
                     encerrado_em=?, encerrado_por=? WHERE id=?""",
            agora, usuarioId, p.id,
        )
        c.atualizar("DELETE FROM item_em_separacao WHERE pallet_id = ? AND usuario_id = ?", p.id, usuarioId)
        p.copy(status = "ENCERRADO", usuarioAtualId = null)
    }

    fun dadosDoCartao(codigo: String): DadosCartao? = db.leitura { c ->
        val p = buscarPallet(c, codigo) ?: return@leitura null
        val notaId = p.notaFiscalId ?: return@leitura null

        val cab = c.buscarUm(
            """SELECT n.numero, n.dest_nome, n.dest_logradouro, n.dest_numero, n.dest_bairro,
                      n.dest_municipio, n.dest_uf, n.dest_cep, n.dest_complemento, i.numero,
                      n.bonificacao
                 FROM nota_fiscal n JOIN instrucao i ON i.id = n.instrucao_id
                WHERE n.id = ?""",
            notaId,
        ) { rs ->
            val endereco = listOfNotNull(rs.getString(3), rs.getString(4))
                .filter { it.isNotBlank() }.joinToString(", ")
            val cidade = listOfNotNull(rs.getString(6), rs.getString(7))
                .filter { it.isNotBlank() }.joinToString("/")
            Cabecalho(rs.getString(1), rs.getString(2), endereco, rs.getString(9),
                rs.getString(10), rs.getInt(11) == 1,
                bairro = rs.getString(5), cidade = cidade.ifBlank { null }, cep = rs.getString(8))
        } ?: return@leitura null

        val itens = c.buscarTodos(
            """SELECT pr.codigo, pr.descricao, COUNT(l.id)
                 FROM leitura l
                 JOIN item_nota i ON i.id = l.item_nota_id
                 JOIN produto pr ON pr.id = i.produto_id
                WHERE l.pallet_id = ? AND l.cancelada = 0
                GROUP BY pr.id ORDER BY pr.codigo""",
            p.id,
        ) { TrioItem(it.getString(1), it.getString(2), it.getInt(3)) }

        val (encerradoEm, encerradoPor) = c.buscarUm(
            """SELECT pa.encerrado_em, u.nome FROM pallet pa
                 LEFT JOIN usuario u ON u.id = pa.encerrado_por
                WHERE pa.id = ?""",
            p.id,
        ) { it.getString(1) to it.getString(2) } ?: (null to null)

        DadosCartao(
            encerradoEm = encerradoEm,
            encerradoPor = encerradoPor,
            palletCodigo = p.codigo,
            instrucao = cab.instrucao,
            notaNumero = cab.notaNumero,
            cliente = cab.cliente,
            endereco = cab.endereco,
            complemento = cab.complemento,
            totalCaixas = itens.sumOf { it.quantidade },
            itens = itens,
            bonificacao = cab.bonificacao,
            bairro = cab.bairro,
            cidade = cab.cidade,
            cep = cab.cep,
        )
    }

    private data class Cabecalho(
        val notaNumero: String, val cliente: String, val endereco: String,
        val complemento: String?, val instrucao: String, val bonificacao: Boolean,
        val bairro: String?, val cidade: String?, val cep: String?,
    )

    fun conteudoDoPallet(codigo: String): List<ItemResumo> = db.leitura { c ->
        val p = buscarPallet(c, codigo) ?: throw Erros.conflito(
            "PALLET_NAO_ENCONTRADO", "Pallet $codigo não encontrado",
        )
        c.buscarTodos(
            """SELECT i.id, i.n_item, pr.codigo, pr.descricao, i.qtd_prevista,
                      COUNT(l.id), i.status
                 FROM leitura l
                 JOIN item_nota i ON i.id = l.item_nota_id
                 JOIN produto pr ON pr.id = i.produto_id
                WHERE l.pallet_id = ? AND l.cancelada = 0
                GROUP BY i.id ORDER BY i.n_item""",
            p.id,
        ) {
            ItemResumo(it.getLong(1), it.getInt(2), it.getString(3), it.getString(4),
                it.getInt(5), it.getInt(6), it.getString(7), emptyList())
        }
    }

    // ------------------------------------------------------------------- leitura

    /**
     * A transação devolve uma decisão; a conversão em exceção acontece depois
     * do commit. Sem isso, a recusa por código não cadastrado faria rollback
     * do próprio registro na fila de pendências — que é justamente o que
     * precisa sobreviver à recusa.
     */
    private sealed interface Decisao {
        data class Aceita(val resultado: ResultadoLeitura) : Decisao
        data class Recusada(
            val codigo: String,
            val mensagem: String,
            val detalhe: Map<String, Any?> = emptyMap(),
        ) : Decisao
    }

    fun registrarLeitura(
        usuarioId: Long,
        multiItem: Boolean,
        coletorId: String?,
        idempotencyKey: String,
        palletCodigo: String?,
        itemId: Long,
        codigoLido: String,
        lidoEm: String?,
    ): ResultadoLeitura {
        val decisao = db.transacao { c ->
            avaliarLeitura(
                c, usuarioId, multiItem, coletorId, idempotencyKey,
                palletCodigo, itemId, codigoLido, lidoEm,
            )
        }
        return when (decisao) {
            is Decisao.Aceita -> decisao.resultado
            is Decisao.Recusada -> throw Erros.conflito(decisao.codigo, decisao.mensagem, decisao.detalhe)
        }
    }

    private fun avaliarLeitura(
        c: Connection,
        usuarioId: Long,
        multiItem: Boolean,
        coletorId: String?,
        idempotencyKey: String,
        palletCodigo: String?,
        itemId: Long,
        codigoLido: String,
        lidoEm: String?,
    ): Decisao {

        // Reenvio após oscilação de wifi devolve o resultado original, sem
        // contar a caixa duas vezes.
        c.buscarUm("SELECT item_nota_id FROM leitura WHERE idempotency_key = ?", idempotencyKey) {
            it.getLong(1)
        }?.let { return Decisao.Aceita(situacaoAtual(c, it, "ACEITA")) }

        val item = buscarItem(c, itemId) ?: throw Erros.naoEncontrado("Item")

        // 1. pallet selecionado
        if (palletCodigo.isNullOrBlank()) {
            return Decisao.Recusada("PALLET_NAO_SELECIONADO", "Nenhum pallet de separação selecionado")
        }
        val pallet = buscarPallet(c, palletCodigo)
            ?: return Decisao.Recusada("PALLET_NAO_ENCONTRADO", "Pallet $palletCodigo não encontrado")
        if (pallet.tipo != "SEPARACAO") {
            return Decisao.Recusada(
                "PALLET_DE_DEVOLUCAO", "Pallet $palletCodigo é de devolução: não recebe separação",
            )
        }
        if (pallet.status !in setOf("CRIADO", "EM_USO")) {
            return Decisao.Recusada(
                "PALLET_ENCERRADO", "Pallet $palletCodigo não aceita mais leitura",
                mapOf("status" to pallet.status),
            )
        }
        if (pallet.usuarioAtualId != null && pallet.usuarioAtualId != usuarioId) {
            return Decisao.Recusada("PALLET_NAO_E_SEU", "Este pallet está em uso por outro separador")
        }
        if (pallet.notaFiscalId != null && pallet.notaFiscalId != item.notaFiscalId) {
            val numero = c.buscarUm("SELECT numero FROM nota_fiscal WHERE id = ?", pallet.notaFiscalId) {
                it.getString(1)
            }
            return Decisao.Recusada(
                "PALLET_DE_OUTRA_NOTA", "Este pallet pertence à NF $numero",
                mapOf("palletCodigo" to pallet.codigo, "notaNumero" to numero),
            )
        }

        // 2. cadeado
        if (item.status in setOf("FECHADO", "FECHADO_DIVERGENCIA")) {
            return Decisao.Recusada(
                "ITEM_FECHADO",
                "Item fechado" + (item.fechadoEm?.let { " em $it" } ?: ""),
                mapOf("fechadoEm" to item.fechadoEm, "status" to item.status),
            )
        }

        // 3. código conhecido
        val normalizado = when (val r = Gtin.normalizar(codigoLido)) {
            is ResultadoGtin.Ok -> r.gtin14
            is ResultadoGtin.Invalido -> null
        }
        val produtoLidoId = normalizado?.let {
            c.buscarUm(
                "SELECT produto_id FROM produto_codigo_barras WHERE codigo = ? AND ativo = 1", it,
            ) { rs -> rs.getLong(1) }
        }
        if (produtoLidoId == null) {
            // Gravado e mantido: a decisão de recusa é commitada junto com ele.
            registrarCodigoDesconhecido(c, normalizado ?: codigoLido, usuarioId)
            return Decisao.Recusada(
                "CODIGO_NAO_CADASTRADO", "Código não cadastrado",
                mapOf("codigoLido" to codigoLido),
            )
        }

        // 4. é o produto pedido?
        if (produtoLidoId != item.produtoId) {
            val desc = c.buscarUm("SELECT descricao FROM produto WHERE id = ?", produtoLidoId) {
                it.getString(1)
            }
            return Decisao.Recusada(
                "PRODUTO_DIVERGENTE", "Item errado: ${desc ?: "produto desconhecido"}",
                mapOf("produtoLido" to desc, "codigoLido" to codigoLido),
            )
        }

        // 5. já completou
        if (item.qtdSeparada >= item.qtdPrevista) {
            return Decisao.Recusada(
                "QUANTIDADE_COMPLETA",
                "Quantidade já completa (${item.qtdSeparada}/${item.qtdPrevista})",
                mapOf("qtdSeparada" to item.qtdSeparada, "qtdPrevista" to item.qtdPrevista),
            )
        }

        limiteDeItens(c, usuarioId, multiItem, itemId)?.let { return it }

        // 6. aceita
        val agora = Instant.now().toString()
        if (pallet.notaFiscalId == null) {
            c.atualizar(
                "UPDATE pallet SET nota_fiscal_id = ?, status = 'EM_USO', usuario_atual_id = ? WHERE id = ?",
                item.notaFiscalId, usuarioId, pallet.id,
            )
        } else if (pallet.status == "CRIADO") {
            c.atualizar(
                "UPDATE pallet SET status = 'EM_USO', usuario_atual_id = ? WHERE id = ?",
                usuarioId, pallet.id,
            )
        }

        c.inserir(
            """INSERT INTO leitura
               (idempotency_key, pallet_id, item_nota_id, produto_id, codigo_lido,
                usuario_id, coletor_id, lido_em, registrado_em)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            idempotencyKey, pallet.id, itemId, item.produtoId, codigoLido,
            usuarioId, coletorId, lidoEm, agora,
        )

        // A primeira leitura bem-sucedida confirma o código que veio do XML.
        c.atualizar(
            "UPDATE produto_codigo_barras SET confirmado = 1, confirmado_em = ? WHERE codigo = ? AND confirmado = 0",
            agora, normalizado,
        )

        c.atualizar(
            """INSERT OR IGNORE INTO item_em_separacao (item_nota_id, usuario_id, pallet_id, iniciado_em)
               VALUES (?,?,?,?)""",
            itemId, usuarioId, pallet.id, agora,
        )

        recalcularItem(c, itemId)
        // Sem isto a nota e a instrução ficariam paradas em ABERTA enquanto a
        // separação já acontece.
        atualizarStatusDaNota(c, item.notaFiscalId)
        return Decisao.Aceita(situacaoAtual(c, itemId, "ACEITA"))
    }

    // -------------------------------------------------------------------- cadeado

    /** Separador só fecha com a quantidade completa. Incompleto é do supervisor. */
    fun fecharItem(usuarioId: Long, itemId: Long): ItemResumo = db.transacao { c ->
        val item = buscarItem(c, itemId) ?: throw Erros.naoEncontrado("Item")
        if (item.status in setOf("FECHADO", "FECHADO_DIVERGENCIA")) {
            throw Erros.conflito("ITEM_FECHADO", "Item já está fechado")
        }
        if (item.qtdSeparada != item.qtdPrevista) {
            throw Erros.conflito(
                "ITEM_INCOMPLETO",
                "Item não coletado por completo (${item.qtdSeparada}/${item.qtdPrevista})",
                mapOf("qtdSeparada" to item.qtdSeparada, "qtdPrevista" to item.qtdPrevista),
            )
        }
        fechar(c, itemId, usuarioId, "FECHADO", null)
        atualizarStatusDaNota(c, item.notaFiscalId)
        itemResumo(c, itemId)
    }

    /** Fechamento incompleto, com justificativa obrigatória — só supervisor. */
    fun fecharComDivergencia(usuarioId: Long, itemId: Long, justificativa: String): ItemResumo =
        db.transacao { c ->
            if (justificativa.isBlank()) {
                throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe a justificativa da divergência")
            }
            val item = buscarItem(c, itemId) ?: throw Erros.naoEncontrado("Item")
            if (item.status in setOf("FECHADO", "FECHADO_DIVERGENCIA")) {
                throw Erros.conflito("ITEM_FECHADO", "Item já está fechado")
            }
            fechar(c, itemId, usuarioId, "FECHADO_DIVERGENCIA", justificativa)
            atualizarStatusDaNota(c, item.notaFiscalId)
            itemResumo(c, itemId)
        }

    fun reabrirItem(usuarioId: Long, itemId: Long): ItemResumo = db.transacao { c ->
        val item = buscarItem(c, itemId) ?: throw Erros.naoEncontrado("Item")
        if (item.status !in setOf("FECHADO", "FECHADO_DIVERGENCIA")) {
            throw Erros.conflito("ITEM_NAO_FECHADO", "Item não está fechado")
        }
        c.atualizar(
            """UPDATE item_nota SET status='EM_SEPARACAO', reaberto_em=?, reaberto_por=?
                WHERE id=?""",
            Instant.now().toString(), usuarioId, itemId,
        )
        atualizarStatusDaNota(c, item.notaFiscalId)
        itemResumo(c, itemId)
    }

    /**
     * Em quais pallets estão as caixas deste item, e quantas em cada.
     *
     * A exclusão é por pallet — é de um palete físico que a caixa sai — e sem
     * esta lista a retaguarda teria de pedir o código do pallet de cabeça.
     */
    fun palletsDoItem(itemId: Long): List<PalletDoItem> = db.leitura { c ->
        c.buscarTodos(
            """SELECT p.codigo, p.status, COUNT(*)
                 FROM leitura l JOIN pallet p ON p.id = l.pallet_id
                WHERE l.item_nota_id = ? AND l.cancelada = 0
                GROUP BY p.id ORDER BY p.codigo""",
            itemId,
        ) { PalletDoItem(it.getString(1), it.getString(2), it.getInt(3)) }
    }

    // ------------------------------------------------------------------ exclusão

    fun excluirSeparacao(
        usuarioId: Long,
        palletCodigo: String,
        itemId: Long,
        quantidade: Int,
        justificativa: String?,
    ): ItemResumo = db.transacao { c ->
        if (quantidade < 1) {
            throw Erros.invalido("QUANTIDADE_INVALIDA", "Quantidade deve ser maior que zero")
        }
        val pallet = buscarPallet(c, palletCodigo)
            ?: throw Erros.conflito("PALLET_NAO_ENCONTRADO", "Pallet $palletCodigo não encontrado")
        when (pallet.status) {
            "EXPEDIDO" -> throw Erros.conflito("PALLET_JA_EXPEDIDO", "Pallet já foi expedido")
            "FUNDIDO", "ESVAZIADO" -> throw Erros.conflito("PALLET_ENCERRADO", "Pallet $palletCodigo saiu de circulação")
        }
        exigirForaDeRetornoAberto(c, pallet.id, pallet.codigo)

        val alvos = c.buscarTodos(
            """SELECT id FROM leitura
                WHERE pallet_id = ? AND item_nota_id = ? AND cancelada = 0
                ORDER BY id DESC LIMIT ?""",
            pallet.id, itemId, quantidade,
        ) { it.getLong(1) }

        if (alvos.size < quantidade) {
            throw Erros.conflito(
                "QUANTIDADE_INSUFICIENTE",
                "Pallet tem apenas ${alvos.size} caixa(s) deste item",
                mapOf("disponivel" to alvos.size, "solicitado" to quantidade),
            )
        }

        val agora = Instant.now().toString()
        alvos.forEach { id ->
            c.atualizar(
                """UPDATE leitura SET cancelada=1, cancelada_em=?, cancelada_por=?, cancelamento_motivo=?
                    WHERE id=?""",
                agora, usuarioId, justificativa, id,
            )
        }

        // Exclusão reabre o cadeado: a quantidade deixou de atender o pedido.
        val item = buscarItem(c, itemId)!!
        if (item.status in setOf("FECHADO", "FECHADO_DIVERGENCIA")) {
            c.atualizar(
                "UPDATE item_nota SET status='EM_SEPARACAO', reaberto_em=?, reaberto_por=? WHERE id=?",
                agora, usuarioId, itemId,
            )
        }
        recalcularItem(c, itemId)
        atualizarStatusDaNota(c, item.notaFiscalId)
        itemResumo(c, itemId)
    }

    // ---------------------------------------------------------------- internos

    private fun limiteDeItens(
        c: Connection, usuarioId: Long, multiItem: Boolean, itemId: Long,
    ): Decisao.Recusada? {
        if (multiItem) return null
        val outro = c.buscarUm(
            """SELECT p.codigo FROM item_em_separacao s
                 JOIN item_nota i ON i.id = s.item_nota_id
                 JOIN produto p ON p.id = i.produto_id
                WHERE s.usuario_id = ? AND s.item_nota_id <> ?
                  AND i.status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')
                LIMIT 1""",
            usuarioId, itemId,
        ) { it.getString(1) }
        return outro?.let {
            Decisao.Recusada(
                "ITEM_ANTERIOR_EM_ABERTO",
                "Finalize o item $it antes de iniciar outro",
                mapOf("itemEmAberto" to it),
            )
        }
    }

    private fun registrarCodigoDesconhecido(c: Connection, codigo: String, usuarioId: Long) {
        val agora = Instant.now().toString()
        val alterou = c.atualizar(
            """UPDATE codigo_nao_identificado
                  SET qtd_leituras = qtd_leituras + 1, ultima_leitura_em = ?, ultimo_usuario_id = ?
                WHERE codigo = ? AND resolvido_em IS NULL""",
            agora, usuarioId, codigo,
        )
        if (alterou == 0) {
            c.atualizar(
                """INSERT OR IGNORE INTO codigo_nao_identificado
                   (codigo, qtd_leituras, primeira_leitura_em, ultima_leitura_em, ultimo_usuario_id)
                   VALUES (?,1,?,?,?)""",
                codigo, agora, agora, usuarioId,
            )
        }
    }

    private fun fechar(c: Connection, itemId: Long, usuarioId: Long, status: String, justificativa: String?) {
        c.atualizar(
            "UPDATE item_nota SET status=?, fechado_em=?, fechado_por=?, justificativa=? WHERE id=?",
            status, Instant.now().toString(), usuarioId, justificativa, itemId,
        )
        c.atualizar("DELETE FROM item_em_separacao WHERE item_nota_id = ?", itemId)
    }

    private fun situacaoAtual(c: Connection, itemId: Long, resultado: String): ResultadoLeitura {
        val i = buscarItem(c, itemId)!!
        return ResultadoLeitura(resultado, i.qtdSeparada, i.qtdPrevista, i.qtdSeparada >= i.qtdPrevista)
    }

    private fun itemResumo(c: Connection, itemId: Long): ItemResumo = c.buscarUm(
        """SELECT i.id, i.n_item, p.codigo, p.descricao, i.qtd_prevista, i.qtd_separada,
                  i.status, i.justificativa, u.nome
             FROM item_nota i
             JOIN produto p ON p.id = i.produto_id
             LEFT JOIN usuario u ON u.id = i.fechado_por
            WHERE i.id = ?""",
        itemId,
    ) {
        ItemResumo(it.getLong(1), it.getInt(2), it.getString(3), it.getString(4),
            it.getInt(5), it.getInt(6), it.getString(7), emptyList(),
            it.getString(8), it.getString(9))
    }!!

    private fun palletDeOutraNota(c: Connection, p: Pallet): ErroNegocio {
        val numero = c.buscarUm("SELECT numero FROM nota_fiscal WHERE id = ?", p.notaFiscalId) {
            it.getString(1)
        }
        return Erros.conflito(
            "PALLET_DE_OUTRA_NOTA", "Este pallet pertence à NF $numero",
            mapOf("palletCodigo" to p.codigo, "notaNumero" to numero),
        )
    }

    private fun buscarPallet(c: Connection, codigo: String): Pallet? = c.buscarUm(
        """SELECT id, codigo, tipo, status, nota_fiscal_id, usuario_atual_id
             FROM pallet WHERE codigo = ?""",
        codigo.trim().uppercase(),
    ) {
        Pallet(
            id = it.getLong(1), codigo = it.getString(2), tipo = it.getString(3),
            status = it.getString(4),
            notaFiscalId = it.getLong(5).takeIf { _ -> !it.wasNull() },
            usuarioAtualId = it.getLong(6).takeIf { _ -> !it.wasNull() },
        )
    }

    private fun buscarItem(c: Connection, id: Long): Item? = c.buscarUm(
        """SELECT id, nota_fiscal_id, produto_id, qtd_prevista, qtd_separada, status, fechado_em
             FROM item_nota WHERE id = ?""",
        id,
    ) {
        Item(it.getLong(1), it.getLong(2), it.getLong(3), it.getInt(4), it.getInt(5),
            it.getString(6), it.getString(7))
    }

    data class Pallet(
        val id: Long, val codigo: String, val tipo: String, val status: String,
        val notaFiscalId: Long?, val usuarioAtualId: Long?,
    )

    private data class Item(
        val id: Long, val notaFiscalId: Long, val produtoId: Long,
        val qtdPrevista: Int, val qtdSeparada: Int, val status: String, val fechadoEm: String?,
    )
}

// --- contagem e situação, compartilhadas com o desembarque -------------------

/**
 * Contagem é sempre derivada das leituras, nunca incrementada às cegas.
 * Só a exclusão (cancelada = 1) desconta: a caixa que faltou num retorno
 * (cancelada = 2) foi separada, e a falta vai por NF de devolução do cliente.
 *
 * Fica fora da classe porque o desembarque pela retaguarda
 * (`DesembarqueServico`) grava as mesmas leituras e precisa da mesma regra —
 * duas cópias divergiriam no primeiro ajuste.
 */
internal fun recalcularItem(c: Connection, itemId: Long) {
    c.atualizar(
        """UPDATE item_nota SET qtd_separada =
             (SELECT COUNT(*) FROM leitura WHERE item_nota_id = ? AND cancelada <> 1),
             status = CASE WHEN status = 'ABERTO' THEN 'EM_SEPARACAO' ELSE status END
           WHERE id = ?""",
        itemId, itemId,
    )
}

internal fun atualizarStatusDaNota(c: Connection, notaId: Long) {
    val abertos = c.buscarUm(
        """SELECT COUNT(*) FROM item_nota
            WHERE nota_fiscal_id = ? AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
        notaId,
    ) { it.getInt(1) } ?: 0
    val novo = if (abertos == 0) "CONCLUIDA" else "EM_SEPARACAO"
    c.atualizar(
        "UPDATE nota_fiscal SET status = ? WHERE id = ? AND status NOT IN ('EXPEDIDA','RETORNO_PARCIAL')",
        novo, notaId,
    )
    // Sobe de ABERTA e **desce de CONCLUIDA**. Sem o segundo caso, reabrir um
    // item deixava a instrução concluída: o coletor filtra por
    // ABERTA/EM_SEPARACAO, então ela sumia do aparelho e o separador não
    // tinha como terminar o que o supervisor acabara de reabrir.
    c.atualizar(
        """UPDATE instrucao SET status='EM_SEPARACAO'
            WHERE id = (SELECT instrucao_id FROM nota_fiscal WHERE id = ?)
              AND status IN ('ABERTA','CONCLUIDA')""",
        notaId,
    )
    if (abertos > 0) {
        // A conclusão deixou de valer: guardar quem concluiu e quando
        // descreveria um fato que não é mais verdade.
        c.atualizar(
            """UPDATE instrucao SET concluido_em=NULL, concluido_por=NULL
                WHERE id = (SELECT instrucao_id FROM nota_fiscal WHERE id = ?)
                  AND status='EM_SEPARACAO'""",
            notaId,
        )
    }
}
