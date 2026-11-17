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

data class ItemDoPallet(
    val itemId: Long,
    val produtoCodigo: String,
    val produtoDescricao: String,
    val quantidade: Int,
)

data class ConteudoPallet(
    val codigo: String,
    val status: String,
    val notaNumero: String?,
    val cliente: String?,
    val endereco: String?,
    val totalCaixas: Int,
    val itens: List<ItemDoPallet>,
)

data class ResultadoMovimentacao(
    val palletOrigem: String,
    val palletDestino: String,
    val qtdCaixas: Int,
    val origemEsvaziada: Boolean,
    val cartoesParaImprimir: List<String>,
)

data class TransferenciaItem(val itemId: Long, val quantidade: Int)

/** Um pallet na consulta: onde está e o que tem. */
data class PalletConsultado(
    val codigo: String,
    val status: String,
    val endereco: String?,
    val deposito: String?,
    val quantidade: Int,
    val itens: List<ItemDoPallet>,
    /** É o pallet que foi bipado. */
    val consultado: Boolean,
    /** Box e nível do endereço, separados: a conferência de box abre por eles. */
    val box: String? = null,
    val nivel: Int? = null,
)

/** Consulta de pallet: de qual NF é e onde estão os outros pallets dela (o bipado vem primeiro). */
data class ConsultaPallet(
    val codigo: String,
    val tipo: String,
    val status: String,
    val notaNumero: String?,
    val cliente: String?,
    val notaStatus: String?,
    /** "cx" nos pallets de separação; "un" nos de devolução, contados em unidades. */
    val unidade: String,
    val pallets: List<PalletConsultado>,
)

/** Os dois pallets de uma repaletização, como estão agora. */
data class Repaletizacao(
    val notaNumero: String?,
    val cliente: String?,
    val origem: ConteudoPallet,
    val destino: ConteudoPallet,
    /** A caixa desta leitura (ou a que o desfazer devolveu); nula ao abrir. */
    val produtoCodigo: String? = null,
    val produtoDescricao: String? = null,
    /** A origem ficou sem caixa: saiu de circulação e liberou o endereço. */
    val origemEsvaziada: Boolean = false,
    /** Caixas que este usuário passou da origem para o destino, ainda não desfeitas. */
    val movidas: Int = 0,
)

/**
 * Movimentação de caixas entre pallets.
 *
 * Fusão (coletor) junta dois pallets parciais; transferência (retaguarda) tira
 * N caixas para um pallet novo. Mesmo mecanismo por baixo: as leituras passam
 * a apontar para o destino e `leitura.pallet_origem_id` registra onde a caixa
 * foi bipada, então a consulta de conteúdo segue sendo um FK só.
 *
 * **Movimentar não é excluir.** A quantidade separada do item não muda; o
 * cadeado não reabre e o líquido do relatório continua o mesmo.
 * Ver docs/MODELO-DE-DADOS.md §7.
 */
class MovimentacaoServico(private val db: Db) {

    fun conteudo(codigo: String): ConteudoPallet = db.leitura { c ->
        conteudoDe(
            c, pallet(c, codigo) ?: throw Erros.conflito("PALLET_NAO_ENCONTRADO", "Pallet $codigo não encontrado"),
        )
    }

    /**
     * Junta o pallet de origem no de destino. Dois separadores que terminaram
     * com meio pallet cada põem tudo num só.
     */
    fun fundir(usuarioId: Long, origemCodigo: String, destinoCodigo: String): ResultadoMovimentacao =
        db.transacao { c ->
            val origem = exigirPallet(c, origemCodigo)
            val destino = exigirPallet(c, destinoCodigo)

            if (origem.id == destino.id) {
                throw Erros.invalido("MESMO_PALLET", "Origem e destino são o mesmo pallet")
            }
            exigirMovimentavel(c, origem, "Origem")
            exigirMovimentavel(c, destino, "Destino")
            if (origem.tipo != destino.tipo) {
                throw Erros.conflito(
                    "TIPOS_INCOMPATIVEIS",
                    "Não é possível juntar pallet de ${origem.tipo.lowercase()} com " +
                        "pallet de ${destino.tipo.lowercase()}",
                )
            }
            // A mesma trava anti-mistura da separação: pallet é de uma NF só.
            if (origem.notaFiscalId != null && destino.notaFiscalId != null &&
                origem.notaFiscalId != destino.notaFiscalId
            ) {
                throw Erros.conflito(
                    "PALLETS_DE_NOTAS_DIFERENTES",
                    "Pallet ${origem.codigo} é da NF ${origem.notaNumero} e " +
                        "${destino.codigo} é da NF ${destino.notaNumero}",
                )
            }

            // A mesma trava, pela NF do cliente, nos pallets de devolução.
            if (origem.notaDevolucaoId != null && destino.notaDevolucaoId != null &&
                origem.notaDevolucaoId != destino.notaDevolucaoId
            ) {
                throw Erros.conflito(
                    "PALLETS_DE_NOTAS_DIFERENTES",
                    "Pallet ${origem.codigo} é da devolução NF ${origem.notaDevolucaoNumero} e " +
                        "${destino.codigo} é da NF ${destino.notaDevolucaoNumero}",
                )
            }

            val agora = Instant.now().toString()
            // A devolução conta em unidades, na tabela dela: sem isto, juntar
            // dois P-DEV movia zero caixa e recusava com "pallet vazio".
            val devolucao = origem.tipo == "DEVOLUCAO"
            val movidas = if (devolucao) {
                c.atualizar(
                    "UPDATE leitura_devolucao SET pallet_id = ? WHERE pallet_id = ? AND cancelada = 0",
                    destino.id, origem.id,
                )
            } else {
                c.atualizar(
                    """UPDATE leitura
                          SET pallet_id = ?, pallet_origem_id = COALESCE(pallet_origem_id, pallet_id)
                        WHERE pallet_id = ? AND cancelada = 0""",
                    destino.id, origem.id,
                )
            }
            if (movidas == 0) {
                throw Erros.conflito("PALLET_VAZIO", "Pallet ${origem.codigo} não tem caixas")
            }

            // Destino sem NF assume a da origem (pallet ainda não usado).
            if (devolucao) {
                if (destino.notaDevolucaoId == null && origem.notaDevolucaoId != null) {
                    c.atualizar(
                        """UPDATE pallet SET nota_devolucao_id = ?,
                                  status = CASE WHEN status = 'CRIADO' THEN 'EM_USO' ELSE status END
                            WHERE id = ?""",
                        origem.notaDevolucaoId, destino.id,
                    )
                }
            } else {
                if (destino.notaFiscalId == null && origem.notaFiscalId != null) {
                    c.atualizar(
                        "UPDATE pallet SET nota_fiscal_id = ? WHERE id = ?",
                        origem.notaFiscalId, destino.id,
                    )
                }
                // Pallet novo que recebe a fusão sai de CRIADO: ficava fora do
                // estoque e da expedição, e a NF aparecia com 0 pallets e 0
                // caixas. Encerrado, vai para a fila de endereçamento, como o
                // destino novo da repaletização caixa a caixa.
                c.atualizar(
                    """UPDATE pallet SET status = 'ENCERRADO', encerrado_em = ?, encerrado_por = ?
                        WHERE id = ? AND status = 'CRIADO'""",
                    agora, usuarioId, destino.id,
                )
            }

            liberarEndereco(c, origem.id, usuarioId, "FUSAO", agora)
            c.atualizar(
                "UPDATE pallet SET status='FUNDIDO', fundido_em_pallet_id=?, usuario_atual_id=NULL WHERE id=?",
                destino.id, origem.id,
            )
            c.inserir(
                """INSERT INTO pallet_movimentacao
                   (tipo, pallet_origem_id, pallet_destino_id, qtd_caixas, movido_em, movido_por)
                   VALUES ('FUSAO',?,?,?,?,?)""",
                origem.id, destino.id, movidas, agora, usuarioId,
            )

            ResultadoMovimentacao(
                palletOrigem = origem.codigo,
                palletDestino = destino.codigo,
                qtdCaixas = movidas,
                origemEsvaziada = true,
                // O cartão do destino muda de conteúdo: um cartão dizendo 45
                // caixas num pallet de 67 é pior que cartão nenhum. Devolução
                // não tem cartão: ele lista os itens da NF de venda.
                cartoesParaImprimir = if (devolucao) emptyList() else listOf(destino.codigo),
            )
        }

    /** Tira N caixas de itens escolhidos para um pallet novo, sem endereço. */
    fun transferir(
        usuarioId: Long,
        origemCodigo: String,
        itens: List<TransferenciaItem>,
        justificativa: String?,
    ): ResultadoMovimentacao = db.transacao { c ->
        val origem = exigirPallet(c, origemCodigo)
        exigirMovimentavel(c, origem, "Pallet")

        val pedidos = itens.filter { it.quantidade > 0 }
        if (pedidos.isEmpty()) {
            throw Erros.invalido("NADA_A_TRANSFERIR", "Informe ao menos uma caixa para transferir")
        }

        val agora = Instant.now().toString()
        val sequencia = if (origem.tipo == "DEVOLUCAO") Sequencias.PALLET_DEVOLUCAO else Sequencias.PALLET
        val destinoCodigo = Sequencias.proximo(c, sequencia)
        val destinoId = c.inserir(
            """INSERT INTO pallet (codigo, tipo, status, nota_fiscal_id, criado_em, criado_por, encerrado_em, encerrado_por)
               VALUES (?,?, 'ENCERRADO', ?,?,?,?,?)""",
            destinoCodigo, origem.tipo, origem.notaFiscalId, agora, usuarioId, agora, usuarioId,
        )

        var total = 0
        pedidos.forEach { pedido ->
            // As mais recentes primeiro, mesma regra da exclusão.
            val alvos = c.buscarTodos(
                """SELECT id FROM leitura
                    WHERE pallet_id = ? AND item_nota_id = ? AND cancelada = 0
                    ORDER BY id DESC LIMIT ?""",
                origem.id, pedido.itemId, pedido.quantidade,
            ) { it.getLong(1) }

            if (alvos.size < pedido.quantidade) {
                throw Erros.conflito(
                    "QUANTIDADE_INSUFICIENTE",
                    "O pallet tem apenas ${alvos.size} caixa(s) deste item",
                    mapOf("disponivel" to alvos.size, "solicitado" to pedido.quantidade),
                )
            }
            alvos.forEach { id ->
                c.atualizar(
                    """UPDATE leitura
                          SET pallet_id = ?, pallet_origem_id = COALESCE(pallet_origem_id, pallet_id)
                        WHERE id = ?""",
                    destinoId, id,
                )
            }
            total += alvos.size
            c.inserir(
                """INSERT INTO pallet_movimentacao
                   (tipo, pallet_origem_id, pallet_destino_id, item_nota_id, qtd_caixas,
                    justificativa, movido_em, movido_por)
                   VALUES ('TRANSFERENCIA',?,?,?,?,?,?,?)""",
                origem.id, destinoId, pedido.itemId, alvos.size, justificativa, agora, usuarioId,
            )
        }

        val sobrou = c.buscarUm(
            "SELECT COUNT(*) FROM leitura WHERE pallet_id = ? AND cancelada = 0", origem.id,
        ) { it.getInt(1) } ?: 0

        val esvaziou = sobrou == 0
        if (esvaziou) {
            liberarEndereco(c, origem.id, usuarioId, "TRANSFERENCIA", agora)
            c.atualizar("UPDATE pallet SET status='ESVAZIADO', usuario_atual_id=NULL WHERE id=?", origem.id)
        }

        ResultadoMovimentacao(
            palletOrigem = origem.codigo,
            palletDestino = destinoCodigo,
            qtdCaixas = total,
            origemEsvaziada = esvaziou,
            cartoesParaImprimir = if (esvaziou) listOf(destinoCodigo)
            else listOf(destinoCodigo, origem.codigo),
        )
    }

    // ---------------------------------------------------------- repaletização

    /**
     * Confere o par antes de bipar caixa. Repaletizar é passar caixa a caixa
     * de um pallet para outro **da mesma NF** — a trava anti-mistura da
     * separação vale aqui também. O destino pode ser um pallet novo, nunca
     * usado: ele passa a ser da NF da origem na primeira caixa.
     */
    fun abrirRepaletizacao(usuarioId: Long, origemCodigo: String, destinoCodigo: String): Repaletizacao =
        db.leitura { c ->
            val (origem, destino) = parRepaletizavel(c, origemCodigo, destinoCodigo)
            repaletizacao(c, usuarioId, origem.id, destino.id)
        }

    /**
     * Fecha a sessão, sem validar o par (a origem pode ter esvaziado): as
     * caixas passadas saem da conta do "passadas" e do desfazer, e voltar ao
     * mesmo par depois começa do zero. [Repaletizacao.movidas] na resposta é
     * quanto passou nesta sessão.
     */
    fun concluirRepaletizacao(usuarioId: Long, origemCodigo: String, destinoCodigo: String): Repaletizacao =
        db.transacao { c ->
            val origem = exigirPallet(c, origemCodigo)
            val destino = exigirPallet(c, destinoCodigo)
            val nestaSessao = repaletizacao(c, usuarioId, origem.id, destino.id).movidas
            c.atualizar(
                """UPDATE repaletizacao_caixa SET concluida_em = ?
                    WHERE usuario_id = ? AND pallet_origem_id = ? AND pallet_destino_id = ?
                      AND desfeita_em IS NULL AND concluida_em IS NULL""",
                Instant.now().toString(), usuarioId, origem.id, destino.id,
            )
            repaletizacao(c, usuarioId, origem.id, destino.id).copy(movidas = nestaSessao)
        }

    /**
     * Pallet bipado no coletor: de qual NF é, o que tem, e os outros pallets
     * da mesma NF com endereço e conteúdo — a tela abre cada um sem nova
     * consulta. Pallet de devolução mostra a NF de devolução e conta em
     * unidades, como a conferência dela.
     */
    fun consultar(codigo: String): ConsultaPallet = db.leitura { c ->
        data class Base(
            val id: Long, val codigo: String, val tipo: String, val status: String,
            val notaId: Long?, val devolucaoId: Long?,
        )
        val p = c.buscarUm(
            "SELECT id, codigo, tipo, status, nota_fiscal_id, nota_devolucao_id FROM pallet WHERE codigo = ?",
            codigo.trim().uppercase(),
        ) {
            Base(
                it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                it.getLong(5).takeIf { _ -> !it.wasNull() }, it.getLong(6).takeIf { _ -> !it.wasNull() },
            )
        } ?: throw Erros.conflito(
            "PALLET_NAO_ENCONTRADO", "Pallet $codigo não encontrado", mapOf("palletCodigo" to codigo),
        )

        val devolucao = p.tipo == "DEVOLUCAO"
        val nota = when {
            devolucao && p.devolucaoId != null -> c.buscarUm(
                "SELECT numero, emitente_nome, status FROM nota_devolucao WHERE id = ?", p.devolucaoId,
            ) { Triple(it.getString(1), it.getString(2), it.getString(3)) }
            !devolucao && p.notaId != null -> c.buscarUm(
                "SELECT numero, dest_nome, status FROM nota_fiscal WHERE id = ?", p.notaId,
            ) { Triple(it.getString(1), it.getString(2), it.getString(3)) }
            else -> null
        }
        // Os outros da mesma NF. Fundido e esvaziado não existem mais como palete.
        val outros = when {
            devolucao && p.devolucaoId != null -> c.buscarTodos(
                """SELECT id FROM pallet WHERE nota_devolucao_id = ? AND status NOT IN ('FUNDIDO','ESVAZIADO')
                    ORDER BY codigo""",
                p.devolucaoId,
            ) { it.getLong(1) }
            !devolucao && p.notaId != null -> c.buscarTodos(
                """SELECT id FROM pallet WHERE nota_fiscal_id = ? AND tipo = 'SEPARACAO'
                      AND status NOT IN ('FUNDIDO','ESVAZIADO')
                    ORDER BY codigo""",
                p.notaId,
            ) { it.getLong(1) }
            else -> emptyList()
        }.filter { it != p.id }

        ConsultaPallet(
            codigo = p.codigo,
            tipo = p.tipo,
            status = p.status,
            notaNumero = nota?.first,
            cliente = nota?.second,
            notaStatus = nota?.third,
            unidade = if (devolucao) "un" else "cx",
            pallets = (listOf(p.id) + outros).map { palletConsultado(c, it, devolucao, it == p.id) },
        )
    }

    /**
     * Uma caixa bipada passa da origem para o destino. Movimentar não é
     * excluir: a quantidade separada do item não muda. A caixa que sai é a
     * leitura mais recente daquele produto na origem — as caixas do mesmo
     * produto são iguais.
     */
    fun repaletizarCaixa(
        usuarioId: Long,
        idempotencyKey: String,
        origemCodigo: String,
        destinoCodigo: String,
        codigoLido: String,
    ): Repaletizacao = db.transacao { c ->
        if (idempotencyKey.isBlank()) {
            throw Erros.invalido("IDEMPOTENCY_KEY_OBRIGATORIA", "Leitura sem chave de idempotência")
        }
        // Reenvio depois de a rede cair: devolve a situação, sem mover outra caixa.
        c.buscarUm(
            "SELECT pallet_origem_id, pallet_destino_id, produto_id FROM repaletizacao_caixa WHERE idempotency_key = ?",
            idempotencyKey,
        ) { Triple(it.getLong(1), it.getLong(2), it.getLong(3)) }?.let { (o, d, produto) ->
            return@transacao repaletizacao(c, usuarioId, o, d, produto)
        }

        val (origem, destino) = parRepaletizavel(c, origemCodigo, destinoCodigo)
        val produtoId = produtoDoCodigo(c, codigoLido)
        val (leituraId, itemId) = c.buscarUm(
            """SELECT id, item_nota_id FROM leitura
                WHERE pallet_id = ? AND produto_id = ? AND cancelada = 0
                ORDER BY id DESC LIMIT 1""",
            origem.id, produtoId,
        ) { it.getLong(1) to it.getLong(2) } ?: run {
            val desc = descricaoDoProduto(c, produtoId)
            throw Erros.conflito(
                "PRODUTO_FORA_DO_PALLET",
                "$desc não está no pallet ${origem.codigo}",
                mapOf("produto" to desc, "palletCodigo" to origem.codigo),
            )
        }

        val agora = Instant.now().toString()
        c.atualizar(
            "UPDATE leitura SET pallet_id = ?, pallet_origem_id = COALESCE(pallet_origem_id, pallet_id) WHERE id = ?",
            destino.id, leituraId,
        )
        // Pallet novo passa a ser da NF da origem e fica encerrado: entra na
        // fila de endereçamento como o pallet novo da transferência.
        if (destino.notaFiscalId == null) {
            c.atualizar(
                """UPDATE pallet SET nota_fiscal_id = ?,
                          status = CASE WHEN status = 'CRIADO' THEN 'ENCERRADO' ELSE status END,
                          encerrado_em = COALESCE(encerrado_em, ?), encerrado_por = COALESCE(encerrado_por, ?)
                    WHERE id = ?""",
                origem.notaFiscalId, agora, usuarioId, destino.id,
            )
        }
        val movimentacaoId = c.inserir(
            """INSERT INTO pallet_movimentacao
               (tipo, pallet_origem_id, pallet_destino_id, item_nota_id, qtd_caixas, justificativa, movido_em, movido_por)
               VALUES ('TRANSFERENCIA',?,?,?,1,'Repaletização',?,?)""",
            origem.id, destino.id, itemId, agora, usuarioId,
        )
        val esvaziou = caixasVivas(c, origem.id) == 0
        if (esvaziou) {
            liberarEndereco(c, origem.id, usuarioId, "TRANSFERENCIA", agora)
            c.atualizar("UPDATE pallet SET status='ESVAZIADO', usuario_atual_id=NULL WHERE id=?", origem.id)
        }
        c.inserir(
            """INSERT INTO repaletizacao_caixa
               (idempotency_key, leitura_id, produto_id, pallet_origem_id, pallet_destino_id,
                origem_status_anterior, destino_status_anterior, origem_esvaziou,
                pallet_movimentacao_id, codigo_lido, usuario_id, movido_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            idempotencyKey, leituraId, produtoId, origem.id, destino.id,
            origem.status, destino.status, if (esvaziou) 1 else 0,
            movimentacaoId, codigoLido, usuarioId, agora,
        )
        repaletizacao(c, usuarioId, origem.id, destino.id, produtoId, esvaziou)
    }

    /**
     * Caixa bipada por engano: volta para a origem a última que **este**
     * usuário passou entre os dois. Origem que tinha esvaziado com ela volta a
     * circular, sem o endereço (já foi liberado): entra na fila de
     * endereçamento. Pallet novo que fica vazio de novo volta a ser novo.
     */
    fun desfazerRepaletizacao(usuarioId: Long, origemCodigo: String, destinoCodigo: String): Repaletizacao =
        db.transacao { c ->
            val origem = exigirPallet(c, origemCodigo)
            val destino = exigirPallet(c, destinoCodigo)
            data class Ultima(
                val id: Long, val leituraId: Long, val produtoId: Long,
                val origemAnterior: String, val destinoAnterior: String, val origemEsvaziou: Boolean,
            )
            val u = c.buscarUm(
                """SELECT id, leitura_id, produto_id, origem_status_anterior, destino_status_anterior, origem_esvaziou
                     FROM repaletizacao_caixa
                    WHERE usuario_id = ? AND pallet_origem_id = ? AND pallet_destino_id = ?
                      AND desfeita_em IS NULL AND concluida_em IS NULL
                    ORDER BY id DESC LIMIT 1""",
                usuarioId, origem.id, destino.id,
            ) { Ultima(it.getLong(1), it.getLong(2), it.getLong(3), it.getString(4), it.getString(5), it.getInt(6) == 1) }
                ?: throw Erros.conflito(
                    "NADA_A_DESFAZER", "Nenhuma caixa sua para desfazer de ${origem.codigo} para ${destino.codigo}",
                )

            exigirMovimentavel(c, destino, "Destino")
            val (noDestino, itemId) = c.buscarUm(
                "SELECT pallet_id = ? AND cancelada = 0, item_nota_id FROM leitura WHERE id = ?",
                destino.id, u.leituraId,
            ) { (it.getInt(1) == 1) to it.getLong(2) } ?: (false to 0L)
            if (!noDestino) {
                throw Erros.conflito(
                    "CAIXA_JA_MOVIDA", "Essa caixa já não está no pallet ${destino.codigo}: não dá para desfazer",
                )
            }
            if (origem.status == "ESVAZIADO" && u.origemEsvaziou) {
                c.atualizar(
                    "UPDATE pallet SET status = ? WHERE id = ?",
                    if (u.origemAnterior == "EM_USO") "EM_USO" else "ENCERRADO", origem.id,
                )
            } else {
                exigirMovimentavel(c, origem, "Origem")
            }

            val agora = Instant.now().toString()
            c.atualizar("UPDATE leitura SET pallet_id = ? WHERE id = ?", origem.id, u.leituraId)
            c.inserir(
                """INSERT INTO pallet_movimentacao
                   (tipo, pallet_origem_id, pallet_destino_id, item_nota_id, qtd_caixas, justificativa, movido_em, movido_por)
                   VALUES ('TRANSFERENCIA',?,?,?,1,'Repaletização desfeita',?,?)""",
                destino.id, origem.id, itemId, agora, usuarioId,
            )
            c.atualizar(
                "UPDATE repaletizacao_caixa SET desfeita_em = ?, desfeita_por = ? WHERE id = ?", agora, usuarioId, u.id,
            )
            // Vazio de novo, o pallet novo não pode ficar encerrado: entraria
            // sem caixa na fila de endereçamento.
            if (u.destinoAnterior == "CRIADO" && caixasVivas(c, destino.id) == 0) {
                liberarEndereco(c, destino.id, usuarioId, "CORRECAO", agora)
                c.atualizar(
                    """UPDATE pallet SET status='CRIADO', nota_fiscal_id=NULL, encerrado_em=NULL, encerrado_por=NULL
                        WHERE id=?""",
                    destino.id,
                )
            }
            repaletizacao(c, usuarioId, origem.id, destino.id, u.produtoId)
        }

    // ---------------------------------------------------------------- internos

    private fun parRepaletizavel(c: Connection, origemCodigo: String, destinoCodigo: String): Pair<DadosPallet, DadosPallet> {
        val origem = exigirPallet(c, origemCodigo)
        val destino = exigirPallet(c, destinoCodigo)
        if (origem.id == destino.id) throw Erros.invalido("MESMO_PALLET", "Origem e destino são o mesmo pallet")
        exigirMovimentavel(c, origem, "Origem")
        exigirMovimentavel(c, destino, "Destino")
        listOf(origem, destino).firstOrNull { it.tipo != "SEPARACAO" }?.let {
            throw Erros.conflito(
                "PALLET_DE_DEVOLUCAO", "Pallet ${it.codigo} é de devolução: repaletização é entre pallets de separação",
            )
        }
        if (origem.notaFiscalId == null || caixasVivas(c, origem.id) == 0) {
            throw Erros.conflito("PALLET_VAZIO", "Pallet ${origem.codigo} não tem caixas")
        }
        if (destino.notaFiscalId == null) {
            // Sem NF e sem caixa, mas na mão de um separador: a próxima caixa
            // dele é que diria de qual NF o pallet é.
            if (destino.status == "EM_USO") {
                throw Erros.conflito(
                    "PALLET_EM_USO_POR_OUTRO", "Pallet ${destino.codigo} está em uso na separação",
                )
            }
        } else if (destino.notaFiscalId != origem.notaFiscalId) {
            throw Erros.conflito(
                "PALLETS_DE_NOTAS_DIFERENTES",
                "Pallet ${origem.codigo} é da NF ${origem.notaNumero} e ${destino.codigo} é da NF " +
                    "${destino.notaNumero}: repaletização só entre pallets da mesma NF",
                mapOf("origem" to origem.notaNumero, "destino" to destino.notaNumero),
            )
        }
        return origem to destino
    }

    private fun repaletizacao(
        c: Connection, usuarioId: Long, origemId: Long, destinoId: Long,
        produtoId: Long? = null, origemEsvaziada: Boolean = false,
    ): Repaletizacao {
        val origem = conteudoDe(c, palletPorId(c, origemId))
        val destino = conteudoDe(c, palletPorId(c, destinoId))
        val (codigo, descricao) = produtoId?.let { id ->
            c.buscarUm("SELECT codigo, descricao FROM produto WHERE id = ?", id) { it.getString(1) to it.getString(2) }
        } ?: (null to null)
        val movidas = c.buscarUm(
            """SELECT COUNT(*) FROM repaletizacao_caixa
                WHERE usuario_id = ? AND pallet_origem_id = ? AND pallet_destino_id = ?
                  AND desfeita_em IS NULL AND concluida_em IS NULL""",
            usuarioId, origemId, destinoId,
        ) { it.getInt(1) } ?: 0
        return Repaletizacao(
            notaNumero = origem.notaNumero ?: destino.notaNumero,
            cliente = origem.cliente ?: destino.cliente,
            origem = origem,
            destino = destino,
            produtoCodigo = codigo,
            produtoDescricao = descricao,
            origemEsvaziada = origemEsvaziada || origem.status == "ESVAZIADO",
            movidas = movidas,
        )
    }

    private fun palletConsultado(c: Connection, id: Long, devolucao: Boolean, consultado: Boolean): PalletConsultado {
        val (codigo, status) = c.buscarUm("SELECT codigo, status FROM pallet WHERE id = ?", id) {
            it.getString(1) to it.getString(2)
        }!!
        val itens = if (devolucao) {
            c.buscarTodos(
                """SELECT i.id, pr.codigo, pr.descricao, SUM(l.qtd_un)
                     FROM leitura_devolucao l
                     JOIN item_devolucao i ON i.id = l.item_devolucao_id
                     JOIN produto pr ON pr.id = l.produto_id
                    WHERE l.pallet_id = ? AND l.cancelada = 0
                    GROUP BY i.id ORDER BY pr.codigo""",
                id,
            ) { ItemDoPallet(it.getLong(1), it.getString(2), it.getString(3), it.getInt(4)) }
        } else {
            itensDoPallet(c, id)
        }
        val local = c.buscarUm(
            """SELECT b.codigo, po.nivel, d.nome FROM enderecamento e
                 JOIN posicao po ON po.id = e.posicao_id
                 JOIN box b ON b.id = po.box_id
                 JOIN deposito d ON d.id = b.deposito_id
                WHERE e.pallet_id = ? AND e.liberado_em IS NULL""",
            id,
        ) { Triple(it.getString(1), it.getInt(2), it.getString(3)) }
        return PalletConsultado(
            codigo = codigo,
            status = status,
            endereco = local?.let { "${it.first} nível ${it.second}" },
            deposito = local?.third,
            quantidade = itens.sumOf { it.quantidade },
            itens = itens,
            consultado = consultado,
            box = local?.first,
            nivel = local?.second,
        )
    }

    private fun produtoDoCodigo(c: Connection, codigoLido: String): Long {
        val gtin = when (val r = Gtin.normalizar(codigoLido)) {
            is ResultadoGtin.Ok -> r.gtin14
            is ResultadoGtin.Invalido -> null
        }
        return gtin?.let { g ->
            c.buscarUm("SELECT produto_id FROM produto_codigo_barras WHERE codigo = ? AND ativo = 1", g) { it.getLong(1) }
        } ?: throw Erros.conflito("CODIGO_NAO_CADASTRADO", "Código não cadastrado", mapOf("codigoLido" to codigoLido))
    }

    private fun descricaoDoProduto(c: Connection, produtoId: Long): String =
        c.buscarUm("SELECT descricao FROM produto WHERE id = ?", produtoId) { it.getString(1) } ?: "Este produto"

    private fun caixasVivas(c: Connection, palletId: Long): Int =
        c.buscarUm("SELECT COUNT(*) FROM leitura WHERE pallet_id = ? AND cancelada = 0", palletId) { it.getInt(1) } ?: 0

    private fun conteudoDe(c: Connection, p: DadosPallet): ConteudoPallet {
        val itens = itensDoPallet(c, p.id, p.tipo)
        return ConteudoPallet(
            codigo = p.codigo,
            status = p.status,
            notaNumero = p.notaNumero,
            cliente = p.cliente,
            endereco = enderecoAtual(c, p.id),
            totalCaixas = itens.sumOf { it.quantidade },
            itens = itens,
        )
    }

    private fun palletPorId(c: Connection, id: Long): DadosPallet {
        val codigo = c.buscarUm("SELECT codigo FROM pallet WHERE id = ?", id) { it.getString(1) }!!
        return pallet(c, codigo)!!
    }

    private fun exigirPallet(c: Connection, codigo: String): DadosPallet =
        pallet(c, codigo) ?: throw Erros.conflito(
            "PALLET_NAO_ENCONTRADO", "Pallet $codigo não encontrado",
            mapOf("palletCodigo" to codigo),
        )

    private fun exigirMovimentavel(c: Connection, p: DadosPallet, rotulo: String) {
        when (p.status) {
            "EXPEDIDO" -> throw Erros.conflito(
                "PALLET_JA_EXPEDIDO", "$rotulo ${p.codigo} já foi expedido",
            )
            "FUNDIDO" -> throw Erros.conflito(
                "PALLET_JA_FUNDIDO", "$rotulo ${p.codigo} já foi juntado a outro pallet",
            )
            "ESVAZIADO" -> throw Erros.conflito(
                "PALLET_ENCERRADO", "$rotulo ${p.codigo} está vazio",
            )
        }
        exigirForaDeRetornoAberto(c, p.id, p.codigo)
    }

    private fun liberarEndereco(c: Connection, palletId: Long, usuarioId: Long, motivo: String, agora: String) {
        c.atualizar(
            """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao=?
                WHERE pallet_id=? AND liberado_em IS NULL""",
            agora, usuarioId, motivo, palletId,
        )
    }

    private fun enderecoAtual(c: Connection, palletId: Long): String? = c.buscarUm(
        """SELECT b.codigo, po.nivel FROM enderecamento e
             JOIN posicao po ON po.id = e.posicao_id
             JOIN box b ON b.id = po.box_id
            WHERE e.pallet_id = ? AND e.liberado_em IS NULL""",
        palletId,
    ) { "${it.getString(1)} nível ${it.getInt(2)}" }

    private fun itensDoPallet(c: Connection, palletId: Long, tipo: String = "SEPARACAO"): List<ItemDoPallet> =
        c.buscarTodos(
            if (tipo == "DEVOLUCAO") {
                // A devolução conta unidades na tabela dela: sem isto o P-DEV
                // cheio aparecia vazio na repaletização e na consulta.
                """SELECT i.id, p.codigo, p.descricao, SUM(l.qtd_un)
                     FROM leitura_devolucao l
                     JOIN item_devolucao i ON i.id = l.item_devolucao_id
                     JOIN produto p ON p.id = l.produto_id
                    WHERE l.pallet_id = ? AND l.cancelada = 0
                    GROUP BY i.id ORDER BY p.codigo"""
            } else {
                """SELECT i.id, p.codigo, p.descricao, COUNT(l.id)
                     FROM leitura l
                     JOIN item_nota i ON i.id = l.item_nota_id
                     JOIN produto p ON p.id = i.produto_id
                    WHERE l.pallet_id = ? AND l.cancelada = 0
                    GROUP BY i.id ORDER BY p.codigo"""
            },
            palletId,
        ) { ItemDoPallet(it.getLong(1), it.getString(2), it.getString(3), it.getInt(4)) }

    private fun pallet(c: Connection, codigo: String): DadosPallet? = c.buscarUm(
        """SELECT pa.id, pa.codigo, pa.tipo, pa.status, pa.nota_fiscal_id, n.numero, n.dest_nome,
                  pa.nota_devolucao_id, nd.numero
             FROM pallet pa
             LEFT JOIN nota_fiscal n ON n.id = pa.nota_fiscal_id
             LEFT JOIN nota_devolucao nd ON nd.id = pa.nota_devolucao_id
            WHERE pa.codigo = ?""",
        codigo.trim().uppercase(),
    ) {
        DadosPallet(
            id = it.getLong(1), codigo = it.getString(2), tipo = it.getString(3),
            status = it.getString(4),
            notaFiscalId = it.getLong(5).takeIf { _ -> !it.wasNull() },
            notaNumero = it.getString(6), cliente = it.getString(7),
            notaDevolucaoId = it.getLong(8).takeIf { _ -> !it.wasNull() },
            notaDevolucaoNumero = it.getString(9),
        )
    }

    private data class DadosPallet(
        val id: Long, val codigo: String, val tipo: String, val status: String,
        val notaFiscalId: Long?, val notaNumero: String?, val cliente: String?,
        /** NF de devolução do pallet P-DEV, e o número dela. */
        val notaDevolucaoId: Long? = null, val notaDevolucaoNumero: String? = null,
    )
}
