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

data class RetornoResumo(
    val id: Long,
    val notaId: Long,
    val notaNumero: String,
    val cliente: String,
    val motivo: String,
    /** EM_CONFERENCIA, AGUARDANDO_TRATATIVA, TRATADO ou CANCELADO. */
    val status: String,
    val registradoEm: String,
    val registradoPor: String,
    val qtdPallets: Int = 0,
    val palletsFechados: Int = 0,
    val caixasEsperadas: Int = 0,
    val caixasConferidas: Int = 0,
    /** Das caixas dos pallets já fechados com falta. */
    val caixasFaltando: Int = 0,
    val conferenciaManual: Boolean = false,
    val conferidoEm: String? = null,
    val conferidoPor: String? = null,
    /** REENTREGA, REFATURAMENTO ou DESCARTE. */
    val tratativa: String? = null,
    val nfsRefaturamento: String? = null,
    val tratativaObs: String? = null,
    val tratadoEm: String? = null,
    val tratadoPor: String? = null,
)

data class ProdutoRetorno(
    val produtoCodigo: String,
    val produtoDescricao: String,
    val esperada: Int,
    val conferida: Int,
)

data class RetornoPallet(
    val id: Long,
    val palletCodigo: String,
    /** ABERTO, CONFERIDO ou CONFERIDO_FALTA. */
    val status: String,
    val qtdEsperada: Int,
    val qtdConferida: Int,
    /** Fechado pelo "retorno completo" da retaguarda, sem bipar. */
    val manual: Boolean,
    val justificativa: String?,
    val produtos: List<ProdutoRetorno>,
)

data class RetornoDetalhe(val retorno: RetornoResumo, val pallets: List<RetornoPallet>)

data class ResultadoLeituraRetorno(
    val retornoPalletId: Long,
    val palletCodigo: String,
    val qtdConferida: Int,
    val qtdEsperada: Int,
    val produtoCodigo: String,
    val produtoDescricao: String,
    val produtoConferida: Int,
    val produtoEsperada: Int,
    val palletCompleto: Boolean,
)

/**
 * Retorno de NF expedida: a mercadoria saiu e voltou ao CD.
 *
 * Recusa total (o cliente não quis mais — atraso na entrega, por exemplo) ou
 * pallets que voltaram para reentregar. Nos dois casos a carga pode voltar
 * faltando: extravio na volta, caixa avariada que ficou pelo caminho. Por isso
 * o retorno passa por três etapas antes de a mercadoria ter destino:
 *
 * 1. **Registro** (retaguarda): quais pallets voltaram e por quê. A foto do que
 *    cada pallet levou, por produto, é tirada aqui — a conferência compara com
 *    ela, e não com o pallet como estiver depois.
 * 2. **Conferência** caixa a caixa no coletor; pallet que voltou faltando
 *    fecha com justificativa. Ou "retorno completo" na retaguarda, por
 *    supervisor, registrado como manual.
 * 3. **Tratativa**: reentregar (a NF pode sair de novo), refaturar para outras
 *    NFs (separação nova; os pallets devolvidos saem do estoque) ou descartar.
 *
 * Tratativa e registro podem ser desfeitos, com justificativa. Nada aqui apaga
 * leitura: o que foi conferido fica, inclusive num retorno cancelado.
 *
 * Distinto da devolução (DevolucaoServico): lá o cliente recebeu e emitiu NF
 * dele; aqui a NF é a nossa e o pallet volta como saiu.
 */
class RetornoServico(private val db: Db) {

    // ------------------------------------------------------------- consulta

    fun listar(status: List<String>?): List<RetornoResumo> = db.leitura { c ->
        val filtro = (status?.takeIf { it.isNotEmpty() } ?: ABERTOS).filter { it in TODOS }
        if (filtro.isEmpty()) return@leitura emptyList()
        c.buscarTodos(
            "SELECT id FROM retorno WHERE status IN (${filtro.joinToString(",") { "?" }}) ORDER BY id DESC LIMIT 200",
            *filtro.toTypedArray(),
        ) { it.getLong(1) }.map { resumo(c, it) }
    }

    fun detalhe(id: Long): RetornoDetalhe = db.leitura { c -> RetornoDetalhe(resumo(c, id), pallets(c, id)) }

    // ------------------------------------------------------------- registro

    fun registrar(usuarioId: Long, notaId: Long, palletCodigos: List<String>, motivo: String): RetornoResumo =
        db.transacao { c ->
            if (motivo.isBlank()) throw Erros.invalido("MOTIVO_OBRIGATORIO", "Informe o motivo do retorno")
            val pedidos = palletCodigos.map { it.trim().uppercase() }.filter { it.isNotEmpty() }.distinct()
            if (pedidos.isEmpty()) {
                throw Erros.invalido("NENHUM_PALLET", "Selecione ao menos um pallet que voltou")
            }

            val (numero, status) = c.buscarUm(
                "SELECT numero, status FROM nota_fiscal WHERE id = ?", notaId,
            ) { it.getString(1) to it.getString(2) } ?: throw Erros.naoEncontrado("Nota fiscal")
            if (status !in setOf("EXPEDIDA", "RETORNO_PARCIAL")) {
                throw Erros.conflito(
                    "NOTA_NAO_EXPEDIDA", "NF $numero não está expedida (situação: ${rotulo(status)})",
                )
            }
            // Um retorno aberto por NF: dois ao mesmo tempo disputariam os
            // mesmos pallets e a NF não saberia para qual estado voltar.
            if (retornoAberto(c, notaId) != null) {
                throw Erros.conflito(
                    "RETORNO_EM_ABERTO",
                    "NF $numero já tem retorno em aberto: conclua a conferência e a tratativa dele antes",
                )
            }

            val fora = foraDoCd(c, notaId).associateBy { it.codigo }
            val desconhecidos = pedidos.filterNot { it in fora }
            if (desconhecidos.isNotEmpty()) {
                throw Erros.conflito(
                    "PALLET_NAO_RETORNAVEL",
                    "Fora da expedição desta NF ou já retornado: ${desconhecidos.joinToString()}",
                    mapOf("pallets" to desconhecidos),
                )
            }

            val agora = Instant.now().toString()
            val retornoId = c.inserir(
                """INSERT INTO retorno (nota_fiscal_id, motivo, status, nota_status_anterior, registrado_em, registrado_por)
                   VALUES (?,?, 'EM_CONFERENCIA', ?,?,?)""",
                notaId, motivo.trim(), status, agora, usuarioId,
            )
            pedidos.forEach { codigo ->
                val p = fora.getValue(codigo)
                c.atualizar(
                    "UPDATE expedicao_pallet SET retornado_em=?, retornado_por=? WHERE id=?",
                    agora, usuarioId, p.expedicaoPalletId,
                )
                // Sem endereço: a posição antiga já foi reaproveitada. Entra na
                // fila de endereçamento do coletor, como na reentrega.
                c.atualizar("UPDATE pallet SET status='ENCERRADO' WHERE id=?", p.palletId)

                val conteudo = c.buscarTodos(
                    "SELECT produto_id, COUNT(*) FROM leitura WHERE pallet_id = ? AND cancelada = 0 GROUP BY produto_id",
                    p.palletId,
                ) { it.getLong(1) to it.getInt(2) }
                val retornoPalletId = c.inserir(
                    """INSERT INTO retorno_pallet (retorno_id, pallet_id, expedicao_pallet_id, qtd_esperada, status)
                       VALUES (?,?,?,?, 'ABERTO')""",
                    retornoId, p.palletId, p.expedicaoPalletId, conteudo.sumOf { it.second },
                )
                conteudo.forEach { (produtoId, qtd) ->
                    c.inserir(
                        "INSERT INTO retorno_pallet_produto (retorno_pallet_id, produto_id, esperada) VALUES (?,?,?)",
                        retornoPalletId, produtoId, qtd,
                    )
                }
            }
            auditar(c, retornoId, "RETORNO_REGISTRADO", usuarioId, "${pedidos.size} pallet(s): ${motivo.trim()}")
            resumo(c, retornoId)
        }

    // ---------------------------------------------------------- conferência

    fun registrarLeitura(
        usuarioId: Long,
        idempotencyKey: String,
        retornoId: Long,
        palletCodigo: String,
        codigoLido: String,
        lidoEm: String?,
    ): ResultadoLeituraRetorno = db.transacao { c ->
        if (idempotencyKey.isBlank()) {
            throw Erros.invalido("IDEMPOTENCY_KEY_OBRIGATORIA", "Leitura sem chave de idempotência")
        }
        // Reenvio depois de a rede cair devolve o resultado original, sem
        // contar a caixa duas vezes.
        c.buscarUm(
            "SELECT retorno_pallet_id, produto_id FROM retorno_leitura WHERE idempotency_key = ?", idempotencyKey,
        ) { it.getLong(1) to it.getLong(2) }?.let { (rp, produto) -> return@transacao situacao(c, rp, produto) }

        exigirStatus(c, retornoId, "EM_CONFERENCIA")
        val cod = palletCodigo.trim().uppercase()
        val (retornoPalletId, statusPallet) = c.buscarUm(
            """SELECT rp.id, rp.status FROM retorno_pallet rp JOIN pallet p ON p.id = rp.pallet_id
                WHERE rp.retorno_id = ? AND p.codigo = ?""",
            retornoId, cod,
        ) { it.getLong(1) to it.getString(2) }
            ?: throw Erros.conflito("PALLET_FORA_DO_RETORNO", "O pallet $cod não faz parte deste retorno")
        if (statusPallet != "ABERTO") {
            throw Erros.conflito("PALLET_JA_CONFERIDO", "O pallet $cod já foi fechado. Reabrir é na retaguarda.")
        }

        val gtin = when (val r = Gtin.normalizar(codigoLido)) {
            is ResultadoGtin.Ok -> r.gtin14
            is ResultadoGtin.Invalido -> null
        }
        val produtoId = gtin?.let { g ->
            c.buscarUm("SELECT produto_id FROM produto_codigo_barras WHERE codigo = ? AND ativo = 1", g) { it.getLong(1) }
        } ?: throw Erros.conflito("CODIGO_NAO_CADASTRADO", "Código não cadastrado", mapOf("codigoLido" to codigoLido))

        val esperada = c.buscarUm(
            "SELECT esperada FROM retorno_pallet_produto WHERE retorno_pallet_id = ? AND produto_id = ?",
            retornoPalletId, produtoId,
        ) { it.getInt(1) }
        if (esperada == null) {
            val desc = c.buscarUm("SELECT descricao FROM produto WHERE id = ?", produtoId) { it.getString(1) }
            throw Erros.conflito(
                "PRODUTO_FORA_DO_PALLET",
                "${desc ?: "Este produto"} não saiu no pallet $cod. Confira se a caixa é de outro pallet.",
                mapOf("produto" to desc),
            )
        }
        val conferida = conferidas(c, retornoPalletId, produtoId)
        if (conferida >= esperada) {
            throw Erros.conflito(
                "QUANTIDADE_COMPLETA",
                "As $esperada caixa(s) deste produto já foram conferidas no pallet $cod",
                mapOf("conferida" to conferida, "esperada" to esperada),
            )
        }

        c.inserir(
            """INSERT INTO retorno_leitura
               (idempotency_key, retorno_pallet_id, produto_id, codigo_lido, usuario_id, lido_em, registrado_em)
               VALUES (?,?,?,?,?,?,?)""",
            idempotencyKey, retornoPalletId, produtoId, codigoLido, usuarioId, lidoEm, Instant.now().toString(),
        )
        situacao(c, retornoPalletId, produtoId)
    }

    /**
     * Fecha a conferência de um pallet. Faltando caixa, exige justificativa:
     * extravio na volta é informação que precisa sobreviver até a tratativa.
     * Pallet que nem voltou fecha com zero e a justificativa.
     */
    fun fecharPallet(usuarioId: Long, retornoPalletId: Long, justificativa: String?): RetornoPallet =
        db.transacao { c ->
            val (retornoId, status) = palletDoRetorno(c, retornoPalletId)
            exigirStatus(c, retornoId, "EM_CONFERENCIA")
            if (status != "ABERTO") throw Erros.conflito("PALLET_JA_CONFERIDO", "Este pallet já foi fechado")

            val p = pallet(c, retornoPalletId)
            val falta = p.qtdConferida < p.qtdEsperada
            val just = justificativa?.trim()?.takeIf { it.isNotEmpty() }
            if (falta && just == null) {
                throw Erros.conflito(
                    "CONFERENCIA_INCOMPLETA",
                    "Conferido ${p.qtdConferida} de ${p.qtdEsperada} caixa(s). Informe a justificativa da falta.",
                    mapOf("conferida" to p.qtdConferida, "esperada" to p.qtdEsperada),
                )
            }
            c.atualizar(
                "UPDATE retorno_pallet SET status=?, justificativa=?, fechado_em=?, fechado_por=? WHERE id=?",
                if (falta) "CONFERIDO_FALTA" else "CONFERIDO", just, Instant.now().toString(), usuarioId,
                retornoPalletId,
            )
            concluirSeTudoFechado(c, retornoId, usuarioId)
            pallet(c, retornoPalletId)
        }

    /** Caixa bipada por engano: cancela a última leitura do pallet (fica no histórico). */
    fun desfazerUltima(usuarioId: Long, retornoPalletId: Long): RetornoPallet = db.transacao { c ->
        val (retornoId, status) = palletDoRetorno(c, retornoPalletId)
        exigirStatus(c, retornoId, "EM_CONFERENCIA")
        if (status != "ABERTO") throw Erros.conflito("PALLET_JA_CONFERIDO", "Este pallet já foi fechado")
        val ultima = c.buscarUm(
            "SELECT id FROM retorno_leitura WHERE retorno_pallet_id = ? AND cancelada = 0 ORDER BY id DESC LIMIT 1",
            retornoPalletId,
        ) { it.getLong(1) } ?: throw Erros.conflito("NADA_A_DESFAZER", "Nenhuma caixa conferida neste pallet")
        c.atualizar(
            "UPDATE retorno_leitura SET cancelada=1, cancelada_em=?, cancelada_por=? WHERE id=?",
            Instant.now().toString(), usuarioId, ultima,
        )
        pallet(c, retornoPalletId)
    }

    fun reabrirPallet(usuarioId: Long, retornoPalletId: Long): RetornoPallet = db.transacao { c ->
        val (retornoId, status) = palletDoRetorno(c, retornoPalletId)
        when (cabecalho(c, retornoId).status) {
            "TRATADO" -> throw Erros.conflito(
                "RETORNO_JA_TRATADO", "Desfaça a tratativa antes de reabrir a conferência",
            )
            "CANCELADO" -> throw Erros.conflito("RETORNO_CANCELADO", "Este retorno foi cancelado")
        }
        if (status == "ABERTO") return@transacao pallet(c, retornoPalletId)
        c.atualizar(
            """UPDATE retorno_pallet SET status='ABERTO', manual=0, justificativa=NULL, fechado_em=NULL, fechado_por=NULL
                WHERE id=?""",
            retornoPalletId,
        )
        c.atualizar(
            """UPDATE retorno SET status='EM_CONFERENCIA', conferido_em=NULL, conferido_por=NULL,
                      conferencia_manual = (SELECT COUNT(*) > 0 FROM retorno_pallet WHERE retorno_id = ? AND manual = 1)
                WHERE id=?""",
            retornoId, retornoId,
        )
        auditar(c, retornoId, "PALLET_REABERTO", usuarioId, pallet(c, retornoPalletId).palletCodigo)
        pallet(c, retornoPalletId)
    }

    /**
     * Retorno completo sem bipar, para pallet que volta lacrado. Só supervisor
     * (a rota confere) e registrado como manual, com quem e quando.
     */
    fun marcarCompleto(usuarioId: Long, retornoId: Long, observacao: String?): RetornoResumo = db.transacao { c ->
        exigirStatus(c, retornoId, "EM_CONFERENCIA")
        val agora = Instant.now().toString()
        val obs = observacao?.trim()?.takeIf { it.isNotEmpty() } ?: "Retorno completo marcado na retaguarda"
        c.atualizar(
            """UPDATE retorno_pallet SET status='CONFERIDO', manual=1, justificativa=?, fechado_em=?, fechado_por=?
                WHERE retorno_id=? AND status='ABERTO'""",
            obs, agora, usuarioId, retornoId,
        )
        c.atualizar(
            """UPDATE retorno SET status='AGUARDANDO_TRATATIVA', conferencia_manual=1, conferido_em=?, conferido_por=?
                WHERE id=?""",
            agora, usuarioId, retornoId,
        )
        auditar(c, retornoId, "RETORNO_COMPLETO_MANUAL", usuarioId, obs)
        resumo(c, retornoId)
    }

    // ------------------------------------------------------------ tratativa

    fun tratar(usuarioId: Long, retornoId: Long, tipo: String, nfs: List<String>?, observacao: String?): RetornoResumo =
        db.transacao { c ->
            val t = tipo.trim().uppercase()
            if (t !in setOf("REENTREGA", "REFATURAMENTO", "DESCARTE")) {
                throw Erros.invalido("TIPO_INVALIDO", "Tratativa deve ser REENTREGA, REFATURAMENTO ou DESCARTE")
            }
            val r = cabecalho(c, retornoId)
            when (r.status) {
                "AGUARDANDO_TRATATIVA" -> Unit
                "EM_CONFERENCIA" -> throw Erros.conflito(
                    "RETORNO_NAO_CONFERIDO", "Termine a conferência (ou marque o retorno completo) antes da tratativa",
                )
                "TRATADO" -> throw Erros.conflito("RETORNO_JA_TRATADO", "Este retorno já tem tratativa")
                else -> throw Erros.conflito("RETORNO_CANCELADO", "Este retorno foi cancelado")
            }
            val listaNfs = nfs.orEmpty().flatMap { it.split(',', ';', ' ', '\n', '\t') }
                .map { it.trim() }.filter { it.isNotEmpty() }.distinct()
            val obs = observacao?.trim()?.takeIf { it.isNotEmpty() }
            if (t == "REFATURAMENTO" && listaNfs.isEmpty()) {
                throw Erros.invalido("NFS_OBRIGATORIAS", "Informe as NFs do refaturamento")
            }
            if (t == "DESCARTE" && obs == null) {
                throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe a justificativa do descarte")
            }
            val conferencia = pallets(c, retornoId)
            if (t == "REENTREGA" && conferencia.all { it.qtdConferida == 0 }) {
                throw Erros.conflito(
                    "NADA_A_REENTREGAR",
                    "Nenhuma caixa voltou: não há o que reentregar. Descarte (a falta toda vai por NF de " +
                        "devolução do cliente) ou cancele o retorno, se foi lançado por engano.",
                )
            }
            val doRetorno = palletsDoRetorno(c, retornoId)
            exigirIntactos(c, doRetorno) { NO_CD }

            val agora = Instant.now().toString()
            var faltaram = 0
            if (t == "REENTREGA") {
                // A NF sai de novo com o que voltou; a falta conferida vai por
                // NF de devolução do cliente. As caixas que faltaram saem do
                // pallet — senão expedição, cartão e estoque diriam que estão
                // ali. Pallet que não voltou nada sai de circulação.
                val numero = c.buscarUm("SELECT numero FROM nota_fiscal WHERE id = ?", r.notaId) { it.getString(1) }
                doRetorno.forEach { p ->
                    val rp = conferencia.first { it.id == p.id }
                    faltaram += tirarFalta(c, retornoId, p.palletId, rp, usuarioId, agora, numero)
                    val esvaziou = rp.qtdConferida == 0
                    c.atualizar(
                        "UPDATE retorno_pallet SET status_pallet_tratativa = ? WHERE id = ?",
                        if (esvaziou) p.statusPallet else null, p.id,
                    )
                    if (esvaziou) {
                        c.atualizar(
                            """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao='CORRECAO'
                                WHERE pallet_id=? AND liberado_em IS NULL""",
                            agora, usuarioId, p.palletId,
                        )
                        c.atualizar("UPDATE pallet SET status='ESVAZIADO' WHERE id=?", p.palletId)
                    }
                }
            } else {
                // A mercadoria sai do estoque como estes pallets: no refaturamento
                // as caixas são bipadas de novo em pallets novos para as NFs novas;
                // no descarte, vão embora. Os endereços ficam livres.
                val motivoLiberacao = if (t == "REFATURAMENTO") "TRANSFERENCIA" else "EXPEDICAO"
                palletsDoRetorno(c, retornoId).forEach { p ->
                    c.atualizar(
                        "UPDATE retorno_pallet SET status_pallet_tratativa = ? WHERE id = ?", p.statusPallet, p.id,
                    )
                    c.atualizar(
                        """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao=?
                            WHERE pallet_id=? AND liberado_em IS NULL""",
                        agora, usuarioId, motivoLiberacao, p.palletId,
                    )
                    c.atualizar("UPDATE pallet SET status='ESVAZIADO' WHERE id=?", p.palletId)
                }
            }
            c.atualizar("UPDATE nota_fiscal SET status=? WHERE id=?", situacaoDaNota(c, r.notaId), r.notaId)
            c.atualizar(
                """UPDATE retorno SET status='TRATADO', tratativa=?, nfs_refaturamento=?, tratativa_obs=?,
                          tratado_em=?, tratado_por=?
                    WHERE id=?""",
                t, listaNfs.joinToString(", ").ifEmpty { null }, obs, agora, usuarioId, retornoId,
            )
            auditar(
                c, retornoId, "TRATATIVA", usuarioId,
                t + (listaNfs.takeIf { it.isNotEmpty() }?.let { " → ${it.joinToString(", ")}" } ?: "") +
                    (if (faltaram > 0) " · $faltaram caixa(s) faltaram (NF de devolução do cliente)" else "") +
                    (obs?.let { ": $it" } ?: ""),
            )
            resumo(c, retornoId)
        }

    fun desfazerTratativa(usuarioId: Long, retornoId: Long, justificativa: String): RetornoResumo =
        db.transacao { c ->
            val just = justificativa.trim().ifEmpty {
                throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe por que a tratativa está sendo desfeita")
            }
            val r = cabecalho(c, retornoId)
            if (r.status != "TRATADO") {
                throw Erros.conflito("RETORNO_NAO_TRATADO", "Este retorno não tem tratativa para desfazer")
            }
            // Voltar a "aguardando tratativa" com outro retorno da NF aberto
            // deixaria dois disputando a mesma NF.
            if (retornoAberto(c, r.notaId) != null) {
                throw Erros.conflito(
                    "RETORNO_EM_ABERTO",
                    "A NF tem outro retorno em aberto: conclua ou cancele aquele antes de desfazer esta tratativa",
                )
            }
            val pallets = palletsDoRetorno(c, retornoId)
            if (r.tratativa == "REENTREGA") {
                // Saiu de novo? Então a mercadoria já foi: não há o que desfazer.
                val reexpedidos = pallets.filter { ultimaSaida(c, it.palletId) != it.expedicaoPalletId }
                if (reexpedidos.isNotEmpty()) {
                    throw Erros.conflito(
                        "NOTA_JA_REEXPEDIDA",
                        "A NF já saiu de novo (${reexpedidos.joinToString { it.codigo }}): não dá para desfazer a reentrega",
                    )
                }
                // O conteúdo a conferir é a foto: o que ficou no pallet mais o
                // que a reentrega tirou por falta. O que ficou vazio pela falta
                // está ESVAZIADO.
                exigirIntactos(c, pallets, faltaDoRetorno = retornoId) { p ->
                    if (p.statusPalletTratativa != null) setOf("ESVAZIADO") else NO_CD
                }
                devolverFalta(c, retornoId)
                pallets.filter { it.statusPalletTratativa != null }
                    .forEach { c.atualizar("UPDATE pallet SET status='ENCERRADO' WHERE id=?", it.palletId) }
            } else {
                // Pallet que deixou de estar "esvaziado", ou cujas caixas mudaram,
                // foi usado para outra coisa depois: voltá-lo criaria estoque que
                // não existe.
                exigirIntactos(c, pallets) { setOf("ESVAZIADO") }
                if (r.tratativa == "REFATURAMENTO") exigirRefaturamentoSemSeparacao(c, retornoId, r.nfsRefaturamento)
                // Sem endereço: volta para a fila de endereçamento do coletor.
                pallets.forEach { c.atualizar("UPDATE pallet SET status='ENCERRADO' WHERE id=?", it.palletId) }
            }
            c.atualizar(
                "UPDATE nota_fiscal SET status=? WHERE id=?",
                situacaoDaNota(c, r.notaId, pallets.map { it.palletId }), r.notaId,
            )
            c.atualizar("UPDATE retorno_pallet SET status_pallet_tratativa = NULL WHERE retorno_id = ?", retornoId)
            c.atualizar(
                """UPDATE retorno SET status='AGUARDANDO_TRATATIVA', tratativa=NULL, nfs_refaturamento=NULL,
                          tratativa_obs=NULL, tratado_em=NULL, tratado_por=NULL
                    WHERE id=?""",
                retornoId,
            )
            auditar(c, retornoId, "TRATATIVA_DESFEITA", usuarioId, "${r.tratativa}: $just")
            resumo(c, retornoId)
        }

    /**
     * Retorno registrado por engano: os pallets voltam a constar como fora do
     * CD. O que foi conferido fica no histórico.
     */
    fun cancelar(usuarioId: Long, retornoId: Long, justificativa: String): RetornoResumo = db.transacao { c ->
        val just = justificativa.trim().ifEmpty {
            throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe por que o retorno está sendo cancelado")
        }
        val r = cabecalho(c, retornoId)
        when (r.status) {
            "TRATADO" -> throw Erros.conflito("RETORNO_JA_TRATADO", "Desfaça a tratativa antes de cancelar o retorno")
            "CANCELADO" -> throw Erros.conflito("RETORNO_CANCELADO", "Este retorno já foi cancelado")
        }
        val pallets = palletsDoRetorno(c, retornoId)
        exigirIntactos(c, pallets) { NO_CD }
        val agora = Instant.now().toString()
        pallets.forEach { p ->
            c.atualizar(
                """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao='CORRECAO'
                    WHERE pallet_id=? AND liberado_em IS NULL""",
                agora, usuarioId, p.palletId,
            )
            c.atualizar("UPDATE pallet SET status='EXPEDIDO' WHERE id=?", p.palletId)
            c.atualizar(
                "UPDATE expedicao_pallet SET retornado_em=NULL, retornado_por=NULL WHERE id=?", p.expedicaoPalletId,
            )
        }
        c.atualizar("UPDATE nota_fiscal SET status=? WHERE id=?", situacaoDaNota(c, r.notaId), r.notaId)
        c.atualizar(
            "UPDATE retorno SET status='CANCELADO', cancelado_em=?, cancelado_por=?, cancelamento_motivo=? WHERE id=?",
            agora, usuarioId, just, retornoId,
        )
        auditar(c, retornoId, "RETORNO_CANCELADO", usuarioId, just)
        resumo(c, retornoId)
    }

    // ---------------------------------------------------------------- internos

    private data class Cabecalho(val notaId: Long, val status: String, val tratativa: String?, val nfsRefaturamento: String?)
    private data class PalletFora(val palletId: Long, val codigo: String, val expedicaoPalletId: Long)
    private data class PalletDoRetorno(
        val id: Long, val palletId: Long, val codigo: String, val statusPallet: String, val expedicaoPalletId: Long,
        /** Na reentrega: preenchido só no pallet que ficou vazio pela falta. */
        val statusPalletTratativa: String?,
    )

    private fun cabecalho(c: Connection, retornoId: Long): Cabecalho = c.buscarUm(
        "SELECT nota_fiscal_id, status, tratativa, nfs_refaturamento FROM retorno WHERE id = ?", retornoId,
    ) { Cabecalho(it.getLong(1), it.getString(2), it.getString(3), it.getString(4)) }
        ?: throw Erros.naoEncontrado("Retorno")

    private fun exigirStatus(c: Connection, retornoId: Long, esperado: String) {
        val atual = cabecalho(c, retornoId).status
        if (atual != esperado) {
            throw Erros.conflito(
                "RETORNO_NAO_EM_CONFERENCIA",
                "Este retorno não está em conferência (situação: ${rotulo(atual)})",
            )
        }
    }

    private fun palletDoRetorno(c: Connection, retornoPalletId: Long): Pair<Long, String> = c.buscarUm(
        "SELECT retorno_id, status FROM retorno_pallet WHERE id = ?", retornoPalletId,
    ) { it.getLong(1) to it.getString(2) } ?: throw Erros.naoEncontrado("Pallet do retorno")

    private fun palletsDoRetorno(c: Connection, retornoId: Long): List<PalletDoRetorno> = c.buscarTodos(
        """SELECT rp.id, p.id, p.codigo, p.status, rp.expedicao_pallet_id, rp.status_pallet_tratativa
             FROM retorno_pallet rp JOIN pallet p ON p.id = rp.pallet_id
            WHERE rp.retorno_id = ? ORDER BY p.codigo""",
        retornoId,
    ) {
        PalletDoRetorno(
            it.getLong(1), it.getLong(2), it.getString(3), it.getString(4), it.getLong(5), it.getString(6),
        )
    }

    private fun retornoAberto(c: Connection, notaId: Long): Long? = c.buscarUm(
        "SELECT id FROM retorno WHERE nota_fiscal_id = ? AND status IN ('EM_CONFERENCIA','AGUARDANDO_TRATATIVA')",
        notaId,
    ) { it.getLong(1) }

    private fun foraDoCd(c: Connection, notaId: Long): List<PalletFora> =
        c.buscarTodos(SQL_NA_RUA, notaId) { PalletFora(it.getLong(1), it.getString(2), it.getLong(3)) }

    private fun ultimaSaida(c: Connection, palletId: Long): Long? = c.buscarUm(
        """SELECT MAX(ep.id) FROM expedicao_pallet ep JOIN expedicao e ON e.id = ep.expedicao_id
            WHERE ep.pallet_id = ? AND e.estornado_em IS NULL""",
        palletId,
    ) { it.getLong(1).takeIf { _ -> !it.wasNull() } }

    /**
     * O pallet ainda é o que voltou: na situação esperada e com o mesmo
     * conteúdo da foto do registro. Senão a tratativa (ou o cancelamento)
     * levaria caixa que não é do retorno, ou deixaria para trás caixa que é.
     */
    private fun exigirIntactos(
        c: Connection,
        pallets: List<PalletDoRetorno>,
        faltaDoRetorno: Long? = null,
        situacoes: (PalletDoRetorno) -> Set<String>,
    ) {
        val alterados = pallets.mapNotNull { p ->
            when {
                p.statusPallet !in situacoes(p) -> "${p.codigo} (${rotulo(p.statusPallet)})"
                conteudoAtual(c, p.palletId, faltaDoRetorno) != foto(c, p.id) -> "${p.codigo} (caixas mudaram)"
                else -> null
            }
        }
        if (alterados.isNotEmpty()) {
            throw Erros.conflito(
                "PALLET_ALTERADO",
                "Pallet mudou desde o registro do retorno: ${alterados.joinToString()}",
                mapOf("pallets" to alterados),
            )
        }
    }

    /** O que está no pallet; com [faltaDoRetorno], mais o que a reentrega dele tirou por falta. */
    private fun conteudoAtual(c: Connection, palletId: Long, faltaDoRetorno: Long? = null): Map<Long, Int> =
        c.buscarTodos(
            """SELECT produto_id, COUNT(*) FROM leitura
                WHERE pallet_id = ? AND (cancelada = 0 OR (cancelada = 2 AND retorno_falta_id = ?))
                GROUP BY produto_id""",
            palletId, faltaDoRetorno ?: -1L,
        ) { it.getLong(1) to it.getInt(2) }.toMap()

    /**
     * Tira do pallet as caixas que não voltaram, produto a produto: a leitura
     * fica com cancelada = 2 ("faltou no retorno"), ligada a este retorno. Não
     * é exclusão — foram separadas e saíram —, então não reabre o item nem
     * conta como exclusão no relatório do separador. Desfazer a tratativa as
     * devolve ([devolverFalta]).
     */
    private fun tirarFalta(
        c: Connection, retornoId: Long, palletId: Long, rp: RetornoPallet,
        usuarioId: Long, agora: String, notaNumero: String?,
    ): Int {
        if (rp.status != "CONFERIDO_FALTA" || rp.manual) return 0
        val motivo = "Faltou no retorno da NF $notaNumero: ${rp.justificativa.orEmpty()}"
        var tiradas = 0
        c.buscarTodos(
            """SELECT rpp.produto_id,
                      rpp.esperada - (SELECT COUNT(*) FROM retorno_leitura rl
                                       WHERE rl.retorno_pallet_id = rpp.retorno_pallet_id
                                         AND rl.produto_id = rpp.produto_id AND rl.cancelada = 0)
                 FROM retorno_pallet_produto rpp WHERE rpp.retorno_pallet_id = ?""",
            rp.id,
        ) { it.getLong(1) to it.getInt(2) }.filter { it.second > 0 }.forEach { (produtoId, falta) ->
            c.buscarTodos(
                """SELECT id FROM leitura WHERE pallet_id = ? AND produto_id = ? AND cancelada = 0
                    ORDER BY id DESC LIMIT ?""",
                palletId, produtoId, falta,
            ) { it.getLong(1) }.forEach { leituraId ->
                c.atualizar(
                    """UPDATE leitura SET cancelada = 2, cancelada_em = ?, cancelada_por = ?,
                              cancelamento_motivo = ?, retorno_falta_id = ?
                        WHERE id = ?""",
                    agora, usuarioId, motivo, retornoId, leituraId,
                )
                tiradas++
            }
        }
        return tiradas
    }

    /** Devolve ao pallet as caixas que a reentrega deste retorno tirou por falta. */
    private fun devolverFalta(c: Connection, retornoId: Long) {
        c.atualizar(
            """UPDATE leitura SET cancelada = 0, cancelada_em = NULL, cancelada_por = NULL,
                      cancelamento_motivo = NULL, retorno_falta_id = NULL
                WHERE cancelada = 2 AND retorno_falta_id = ?""",
            retornoId,
        )
    }

    private fun foto(c: Connection, retornoPalletId: Long): Map<Long, Int> = c.buscarTodos(
        "SELECT produto_id, esperada FROM retorno_pallet_produto WHERE retorno_pallet_id = ?", retornoPalletId,
    ) { it.getLong(1) to it.getInt(2) }.toMap()

    /**
     * Refaturado e já separado nas NFs novas, a mercadoria existiria duas
     * vezes se o pallet devolvido voltasse ao estoque.
     */
    private fun exigirRefaturamentoSemSeparacao(c: Connection, retornoId: Long, nfs: String?) {
        val separadas = nfs.orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() }.filter { nf ->
            (c.buscarUm(
                """SELECT COUNT(*) FROM leitura l
                     JOIN item_nota i ON i.id = l.item_nota_id
                     JOIN nota_fiscal n ON n.id = i.nota_fiscal_id
                    WHERE l.cancelada = 0 AND LTRIM(n.numero, '0') = LTRIM(?, '0')
                      AND l.pallet_id NOT IN (SELECT pallet_id FROM retorno_pallet WHERE retorno_id = ?)""",
                nf, retornoId,
            ) { it.getInt(1) } ?: 0) > 0
        }
        if (separadas.isNotEmpty()) {
            throw Erros.conflito(
                "REFATURAMENTO_EM_SEPARACAO",
                "A NF ${separadas.joinToString()} do refaturamento já tem caixa separada: desfazer agora " +
                    "duplicaria o estoque. Exclua a separação dela antes.",
                mapOf("nfs" to separadas),
            )
        }
    }

    private fun concluirSeTudoFechado(c: Connection, retornoId: Long, usuarioId: Long) {
        val abertos = c.buscarUm(
            "SELECT COUNT(*) FROM retorno_pallet WHERE retorno_id = ? AND status = 'ABERTO'", retornoId,
        ) { it.getInt(1) } ?: 0
        if (abertos == 0) {
            c.atualizar(
                """UPDATE retorno SET status='AGUARDANDO_TRATATIVA', conferido_em=?, conferido_por=?
                    WHERE id=? AND status='EM_CONFERENCIA'""",
                Instant.now().toString(), usuarioId, retornoId,
            )
        }
    }

    private fun conferidas(c: Connection, retornoPalletId: Long, produtoId: Long): Int = c.buscarUm(
        """SELECT COUNT(*) FROM retorno_leitura
            WHERE retorno_pallet_id = ? AND produto_id = ? AND cancelada = 0""",
        retornoPalletId, produtoId,
    ) { it.getInt(1) } ?: 0

    private fun situacao(c: Connection, retornoPalletId: Long, produtoId: Long): ResultadoLeituraRetorno {
        val p = pallet(c, retornoPalletId)
        val (codigo, descricao) = c.buscarUm(
            "SELECT codigo, descricao FROM produto WHERE id = ?", produtoId,
        ) { it.getString(1) to it.getString(2) } ?: ("" to "")
        val produto = p.produtos.firstOrNull { it.produtoCodigo == codigo }
        return ResultadoLeituraRetorno(
            retornoPalletId = p.id,
            palletCodigo = p.palletCodigo,
            qtdConferida = p.qtdConferida,
            qtdEsperada = p.qtdEsperada,
            produtoCodigo = codigo,
            produtoDescricao = descricao,
            produtoConferida = produto?.conferida ?: 0,
            produtoEsperada = produto?.esperada ?: 0,
            palletCompleto = p.qtdConferida >= p.qtdEsperada,
        )
    }

    private fun resumo(c: Connection, retornoId: Long): RetornoResumo {
        val base = c.buscarUm(
            """SELECT r.id, r.nota_fiscal_id, n.numero, n.dest_nome, r.motivo, r.status,
                      r.registrado_em, ur.nome, r.conferencia_manual, r.conferido_em, uc.nome,
                      r.tratativa, r.nfs_refaturamento, r.tratativa_obs, r.tratado_em, ut.nome
                 FROM retorno r
                 JOIN nota_fiscal n ON n.id = r.nota_fiscal_id
                 JOIN usuario ur ON ur.id = r.registrado_por
                 LEFT JOIN usuario uc ON uc.id = r.conferido_por
                 LEFT JOIN usuario ut ON ut.id = r.tratado_por
                WHERE r.id = ?""",
            retornoId,
        ) {
            RetornoResumo(
                id = it.getLong(1), notaId = it.getLong(2), notaNumero = it.getString(3), cliente = it.getString(4),
                motivo = it.getString(5), status = it.getString(6), registradoEm = it.getString(7),
                registradoPor = it.getString(8), conferenciaManual = it.getInt(9) == 1,
                conferidoEm = it.getString(10), conferidoPor = it.getString(11), tratativa = it.getString(12),
                nfsRefaturamento = it.getString(13), tratativaObs = it.getString(14), tratadoEm = it.getString(15),
                tratadoPor = it.getString(16),
            )
        } ?: throw Erros.naoEncontrado("Retorno")

        val ps = pallets(c, retornoId)
        return base.copy(
            qtdPallets = ps.size,
            palletsFechados = ps.count { it.status != "ABERTO" },
            caixasEsperadas = ps.sumOf { it.qtdEsperada },
            caixasConferidas = ps.sumOf { it.qtdConferida },
            caixasFaltando = ps.filter { it.status == "CONFERIDO_FALTA" }.sumOf { it.qtdEsperada - it.qtdConferida },
        )
    }

    private fun pallets(c: Connection, retornoId: Long): List<RetornoPallet> = c.buscarTodos(
        "SELECT rp.id FROM retorno_pallet rp JOIN pallet p ON p.id = rp.pallet_id WHERE rp.retorno_id = ? ORDER BY p.codigo",
        retornoId,
    ) { it.getLong(1) }.map { pallet(c, it) }

    private fun pallet(c: Connection, retornoPalletId: Long): RetornoPallet {
        data class Base(val codigo: String, val status: String, val esperada: Int, val manual: Boolean, val just: String?)
        val b = c.buscarUm(
            """SELECT p.codigo, rp.status, rp.qtd_esperada, rp.manual, rp.justificativa
                 FROM retorno_pallet rp JOIN pallet p ON p.id = rp.pallet_id WHERE rp.id = ?""",
            retornoPalletId,
        ) { Base(it.getString(1), it.getString(2), it.getInt(3), it.getInt(4) == 1, it.getString(5)) }
            ?: throw Erros.naoEncontrado("Pallet do retorno")

        val produtos = c.buscarTodos(
            """SELECT pr.codigo, pr.descricao, rpp.esperada,
                      (SELECT COUNT(*) FROM retorno_leitura rl
                        WHERE rl.retorno_pallet_id = rpp.retorno_pallet_id
                          AND rl.produto_id = rpp.produto_id AND rl.cancelada = 0)
                 FROM retorno_pallet_produto rpp JOIN produto pr ON pr.id = rpp.produto_id
                WHERE rpp.retorno_pallet_id = ? ORDER BY pr.codigo""",
            retornoPalletId,
        ) {
            val esperada = it.getInt(3)
            // "Retorno completo" da retaguarda vale como conferido inteiro.
            ProdutoRetorno(it.getString(1), it.getString(2), esperada, if (b.manual) esperada else it.getInt(4))
        }
        return RetornoPallet(
            id = retornoPalletId,
            palletCodigo = b.codigo,
            status = b.status,
            qtdEsperada = b.esperada,
            qtdConferida = if (b.manual) b.esperada else produtos.sumOf { it.conferida },
            manual = b.manual,
            justificativa = b.just,
            produtos = produtos,
        )
    }

    private fun auditar(c: Connection, retornoId: Long, acao: String, usuarioId: Long, detalhe: String) {
        c.inserir(
            """INSERT INTO log_auditoria (entidade, entidade_id, acao, usuario_id, em, detalhe_json)
               VALUES ('retorno', ?, ?, ?, ?, ?)""",
            retornoId, acao, usuarioId, Instant.now().toString(), detalhe,
        )
    }

    private fun rotulo(status: String) = status.replace('_', ' ').lowercase()

    private companion object {
        val ABERTOS = listOf("EM_CONFERENCIA", "AGUARDANDO_TRATATIVA")
        val TODOS = setOf("EM_CONFERENCIA", "AGUARDANDO_TRATATIVA", "TRATADO", "CANCELADO")
        /** Pallet que voltou e ainda está no CD, fechado. */
        val NO_CD = setOf("ENCERRADO", "ENDERECADO")
    }
}

/** Pallets da NF que estão na rua: a última saída de cada um, não estornada nem retornada. */
private const val SQL_NA_RUA = """SELECT p.id, p.codigo, ep.id
     FROM expedicao e
     JOIN expedicao_pallet ep ON ep.expedicao_id = e.id
     JOIN pallet p ON p.id = ep.pallet_id
    WHERE e.nota_fiscal_id = ? AND e.estornado_em IS NULL AND ep.retornado_em IS NULL
      AND p.status = 'EXPEDIDO'
      AND ep.id = (SELECT MAX(ep2.id) FROM expedicao_pallet ep2
                     JOIN expedicao e2 ON e2.id = ep2.expedicao_id
                    WHERE ep2.pallet_id = ep.pallet_id AND e2.estornado_em IS NULL)
    ORDER BY p.codigo"""

/**
 * Situação de uma NF que já saiu, lida dos pallets dela: com pallet no CD,
 * pode sair de novo (CONCLUIDA se nada ficou na rua, RETORNO_PARCIAL se
 * ficou); sem nenhum, EXPEDIDA — foi faturada e saiu, o destino da mercadoria
 * está no retorno.
 *
 * Calculada na hora, e não guardada de antes: tratativa, cancelamento e
 * estorno de retornos diferentes da mesma NF se cruzam, e "a situação de
 * antes" de um deles pode já não valer.
 *
 * [emRetornoAberto]: pallets de um retorno que volta a ficar aberto. Contam
 * como na rua — até a tratativa, a NF fica como estava no registro dele.
 */
internal fun situacaoDaNota(c: Connection, notaId: Long, emRetornoAberto: Collection<Long> = emptyList()): String {
    val noCd = c.buscarTodos(
        "SELECT id FROM pallet WHERE nota_fiscal_id = ? AND status IN ('EM_USO','ENCERRADO','ENDERECADO')",
        notaId,
    ) { it.getLong(1) }.count { it !in emRetornoAberto }
    val naRua = c.buscarTodos(SQL_NA_RUA, notaId) { it.getLong(1) }.size + emRetornoAberto.size
    return when {
        noCd == 0 -> "EXPEDIDA"
        naRua == 0 -> "CONCLUIDA"
        else -> "RETORNO_PARCIAL"
    }
}

/**
 * Pallet de retorno em conferência ou esperando tratativa não se mexe: a
 * conferência compara com a foto do que ele levou, e a tratativa age sobre
 * ele inteiro. Caixa tirada escaparia da tratativa; caixa posta iria junto.
 */
internal fun exigirForaDeRetornoAberto(c: Connection, palletId: Long, codigo: String) {
    val nota = c.buscarUm(
        """SELECT n.numero FROM retorno_pallet rp
             JOIN retorno r ON r.id = rp.retorno_id
             JOIN nota_fiscal n ON n.id = r.nota_fiscal_id
            WHERE rp.pallet_id = ? AND r.status IN ('EM_CONFERENCIA','AGUARDANDO_TRATATIVA')
            LIMIT 1""",
        palletId,
    ) { it.getString(1) } ?: return
    throw Erros.conflito(
        "PALLET_EM_RETORNO",
        "Pallet $codigo está no retorno da NF $nota: só pode ser mexido depois da tratativa",
        mapOf("palletCodigo" to codigo, "notaNumero" to nota),
    )
}
