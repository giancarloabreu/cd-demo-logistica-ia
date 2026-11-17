package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import java.sql.Connection
import java.time.Instant

data class NotaParaExpedir(
    val id: Long,
    val numero: String,
    val cliente: String,
    val instrucao: String,
    val status: String,
    val bonificacao: Boolean,
    val qtdPallets: Int,
    val qtdCaixas: Int,
    val semEndereco: Int,
)

data class PalletExpedido(val codigo: String, val endereco: String?, val qtdCaixas: Int)

data class ResultadoExpedicao(
    val expedicaoId: Long,
    val notaNumero: String,
    val pallets: List<PalletExpedido>,
    val posicoesLiberadas: Int,
)

data class ExpedicaoRegistro(
    val id: Long,
    val notaNumero: String,
    val cliente: String,
    val expedidoEm: String,
    val expedidoPor: String,
    val qtdPallets: Int,
    val estornadoEm: String?,
    val estornoMotivo: String?,
)

data class PalletSemEndereco(
    val palletCodigo: String,
    val posicaoOriginal: String,
    val ocupadaPor: String,
)

data class PalletRetornavel(
    val expedicaoPalletId: Long,
    val palletCodigo: String,
    val qtdCaixas: Int,
    val expedidoEm: String,
    val retornadoEm: String?,
)

data class NotaExpedidaResumo(
    val notaId: Long,
    val notaNumero: String,
    val cliente: String,
    val status: String,
    val pallets: List<PalletRetornavel>,
)

data class ResultadoReentrega(
    val notaNumero: String,
    val notaStatus: String,
    val palletsRetornados: List<String>,
    val qtdCaixas: Int,
    val totalPendentes: Int,
)

data class ResultadoEstorno(
    val notaNumero: String,
    val restaurados: Int,
    val semEndereco: List<PalletSemEndereco>,
)

/**
 * Saída da mercadoria e o desfazer dela.
 *
 * Expedir libera os endereços; estornar tenta devolvê-los. Ver
 * docs/MODELO-DE-DADOS.md §9 e docs/TELAS-RETAGUARDA.md §9.
 */
class ExpedicaoServico(private val db: Db) {

    private val log = org.slf4j.LoggerFactory.getLogger(ExpedicaoServico::class.java)

    /** NFs prontas para sair: concluídas ou com parte retornada. */
    fun pendentes(): List<NotaParaExpedir> = db.leitura { c ->
        c.buscarTodos(
            """SELECT n.id, n.numero, n.dest_nome, i.numero, n.status, n.bonificacao,
                      (SELECT COUNT(*) FROM pallet p
                        WHERE p.nota_fiscal_id = n.id
                          AND p.status IN ('EM_USO','ENCERRADO','ENDERECADO')),
                      -- Conta pelo pallet, não pelo item: em RETORNO_PARCIAL
                      -- parte das caixas ainda está com o cliente, e somar o
                      -- item inteiro faria a tela oferecer carga que não está aqui.
                      COALESCE((SELECT COUNT(*) FROM leitura l
                                 JOIN pallet p ON p.id = l.pallet_id
                                WHERE p.nota_fiscal_id = n.id AND l.cancelada = 0
                                  AND p.status IN ('EM_USO','ENCERRADO','ENDERECADO')), 0),
                      (SELECT COUNT(*) FROM pallet p
                        WHERE p.nota_fiscal_id = n.id
                          AND p.status IN ('EM_USO','ENCERRADO')
                          AND NOT EXISTS (SELECT 1 FROM enderecamento e
                                           WHERE e.pallet_id = p.id AND e.liberado_em IS NULL))
                 FROM nota_fiscal n
                 JOIN instrucao i ON i.id = n.instrucao_id
                WHERE n.status IN ('CONCLUIDA','RETORNO_PARCIAL')
                  -- Retorno em conferência ou esperando tratativa: a mercadoria
                  -- voltou, mas ninguém decidiu ainda se ela sai de novo.
                  AND NOT EXISTS (SELECT 1 FROM retorno r
                                   WHERE r.nota_fiscal_id = n.id
                                     AND r.status IN ('EM_CONFERENCIA','AGUARDANDO_TRATATIVA'))
                ORDER BY i.numero, n.numero""",
        ) {
            NotaParaExpedir(
                it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                it.getString(5), it.getInt(6) == 1, it.getInt(7), it.getInt(8), it.getInt(9),
            )
        }
    }

    fun historico(limite: Int = 50): List<ExpedicaoRegistro> = db.leitura { c ->
        c.buscarTodos(
            """SELECT e.id, n.numero, n.dest_nome, e.expedido_em, u.nome,
                      (SELECT COUNT(*) FROM expedicao_pallet ep WHERE ep.expedicao_id = e.id),
                      e.estornado_em, e.estorno_motivo
                 FROM expedicao e
                 JOIN nota_fiscal n ON n.id = e.nota_fiscal_id
                 JOIN usuario u ON u.id = e.expedido_por
                ORDER BY e.id DESC LIMIT ?""",
            limite,
        ) {
            ExpedicaoRegistro(
                it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                it.getString(5), it.getInt(6), it.getString(7), it.getString(8),
            )
        }
    }

    /** Prévia do efeito, para a tela mostrar antes de aplicar. */
    fun previa(notaIds: List<Long>): List<ResultadoExpedicao> = db.leitura { c ->
        notaIds.map { id ->
            val numero = c.buscarUm("SELECT numero FROM nota_fiscal WHERE id = ?", id) { it.getString(1) }
                ?: throw Erros.naoEncontrado("Nota")
            val pallets = palletsDaNota(c, id)
            ResultadoExpedicao(0, numero, pallets, pallets.count { it.endereco != null })
        }
    }

    fun expedir(usuarioId: Long, notaIds: List<Long>, observacao: String?): List<ResultadoExpedicao> =
        db.transacao { c ->
            if (notaIds.isEmpty()) {
                throw Erros.invalido("NENHUMA_NOTA", "Selecione ao menos uma nota")
            }
            val agora = Instant.now().toString()

            notaIds.map { notaId ->
                val nota = c.buscarUm(
                    "SELECT numero, status FROM nota_fiscal WHERE id = ?", notaId,
                ) { it.getString(1) to it.getString(2) } ?: throw Erros.naoEncontrado("Nota")

                // Invariante 12: só expede nota concluída (ou com retorno parcial).
                if (nota.second !in setOf("CONCLUIDA", "RETORNO_PARCIAL")) {
                    throw Erros.conflito(
                        "NOTA_NAO_CONCLUIDA",
                        "NF ${nota.first} está ${nota.second.replace('_', ' ').lowercase()} " +
                            "e não pode ser expedida",
                        mapOf("notaNumero" to nota.first, "status" to nota.second),
                    )
                }
                exigirSemRetornoAberto(c, notaId, nota.first)

                // Fecha o que ficou EM_USO nesta nota antes de listar.
                //
                // Endereçar passou a ser permitido com o pallet ainda em
                // separação, então um palete cheio pode chegar aqui sem ninguém
                // ter tocado em "encerrar". `palletsDaNota` não o veria, e a
                // carga sairia sem ele — perda silenciosa, descoberta pelo
                // cliente. Expedir é ato explícito sobre nota já concluída:
                // nada mais será bipado, então fechar aqui é correto.
                val fechadosAgora = c.atualizar(
                    """UPDATE pallet SET status='ENCERRADO', usuario_atual_id=NULL,
                             encerrado_em=?, encerrado_por=?
                        WHERE nota_fiscal_id=? AND status='EM_USO'""",
                    agora, usuarioId, notaId,
                )
                if (fechadosAgora > 0) {
                    log.info(
                        "NF {}: {} pallet(s) encerrados na expedição por ainda estarem em separação",
                        nota.first, fechadosAgora,
                    )
                }

                val pallets = palletsDaNota(c, notaId)
                if (pallets.isEmpty()) {
                    throw Erros.conflito(
                        "NOTA_SEM_PALLET", "NF ${nota.first} não tem pallet para expedir",
                    )
                }

                val expedicaoId = c.inserir(
                    """INSERT INTO expedicao (nota_fiscal_id, expedido_em, expedido_por, observacao)
                       VALUES (?,?,?,?)""",
                    notaId, agora, usuarioId, observacao,
                )

                var liberadas = 0
                pallets.forEach { p ->
                    val palletId = c.buscarUm(
                        "SELECT id FROM pallet WHERE codigo = ?", p.codigo,
                    ) { it.getLong(1) }!!

                    c.inserir(
                        "INSERT INTO expedicao_pallet (expedicao_id, pallet_id) VALUES (?,?)",
                        expedicaoId, palletId,
                    )
                    liberadas += c.atualizar(
                        """UPDATE enderecamento
                              SET liberado_em=?, liberado_por=?, motivo_liberacao='EXPEDICAO'
                            WHERE pallet_id=? AND liberado_em IS NULL""",
                        agora, usuarioId, palletId,
                    )
                    c.atualizar("UPDATE pallet SET status='EXPEDIDO' WHERE id=?", palletId)
                }

                c.atualizar("UPDATE nota_fiscal SET status='EXPEDIDA' WHERE id=?", notaId)
                ResultadoExpedicao(expedicaoId, nota.first, pallets, liberadas)
            }
        }

    /**
     * Desfaz uma expedição lançada por engano — os pallets nunca saíram.
     *
     * Posição já ocupada por outro pallet **não** é tomada de volta: o pallet
     * volta sem endereço e entra na fila de endereçamento. Dois pallets no
     * mesmo lugar seria pior que um pallet pendente na tela.
     */
    fun estornar(usuarioId: Long, expedicaoId: Long, motivo: String): ResultadoEstorno =
        db.transacao { c ->
            if (motivo.isBlank()) {
                throw Erros.invalido("MOTIVO_OBRIGATORIO", "Informe o motivo do estorno")
            }
            val exp = c.buscarUm(
                """SELECT e.nota_fiscal_id, n.numero, e.estornado_em
                     FROM expedicao e JOIN nota_fiscal n ON n.id = e.nota_fiscal_id
                    WHERE e.id = ?""",
                expedicaoId,
            ) { Triple(it.getLong(1), it.getString(2), it.getString(3)) }
                ?: throw Erros.naoEncontrado("Expedição")

            if (exp.third != null) {
                throw Erros.conflito("EXPEDICAO_JA_ESTORNADA", "Esta expedição já foi estornada")
            }
            exigirSemRetornoAberto(c, exp.first, exp.second)
            // Pallet desta saída que já voltou ao CD (retorno ou reentrega) tem
            // outra história desde então: pode ter sido refaturado, descartado
            // ou saído de novo. Estornar a saída o poria de volta no estoque
            // sem olhar para nada disso.
            val voltaram = c.buscarTodos(
                """SELECT p.codigo FROM expedicao_pallet ep JOIN pallet p ON p.id = ep.pallet_id
                    WHERE ep.expedicao_id = ? AND (ep.retornado_em IS NOT NULL OR p.status <> 'EXPEDIDO')
                    ORDER BY p.codigo""",
                expedicaoId,
            ) { it.getString(1) }
            if (voltaram.isNotEmpty()) {
                throw Erros.conflito(
                    "EXPEDICAO_COM_RETORNO",
                    "Pallet desta expedição já voltou ao CD (${voltaram.joinToString()}): estornar criaria " +
                        "estoque que não existe. Se o retorno foi lançado por engano, cancele-o antes.",
                    mapOf("pallets" to voltaram),
                )
            }

            val agora = Instant.now().toString()
            val pendentes = mutableListOf<PalletSemEndereco>()
            var restaurados = 0

            val pallets = c.buscarTodos(
                """SELECT p.id, p.codigo FROM expedicao_pallet ep
                     JOIN pallet p ON p.id = ep.pallet_id
                    WHERE ep.expedicao_id = ?""",
                expedicaoId,
            ) { it.getLong(1) to it.getString(2) }

            pallets.forEach { (palletId, codigo) ->
                // O endereço que ESTA expedição liberou (mesmo instante). Pallet
                // que voltou e saiu de novo sem ser endereçado não tem nenhum:
                // o de uma saída anterior não é mais dele.
                val anterior = c.buscarUm(
                    """SELECT e.posicao_id, b.codigo, po.nivel
                         FROM enderecamento e
                         JOIN posicao po ON po.id = e.posicao_id
                         JOIN box b ON b.id = po.box_id
                        WHERE e.pallet_id = ? AND e.motivo_liberacao = 'EXPEDICAO'
                          AND e.liberado_em = (SELECT expedido_em FROM expedicao WHERE id = ?)
                        ORDER BY e.id DESC LIMIT 1""",
                    palletId, expedicaoId,
                ) { Triple(it.getLong(1), it.getString(2), it.getInt(3)) }

                if (anterior == null) {
                    c.atualizar("UPDATE pallet SET status='ENCERRADO' WHERE id=?", palletId)
                    return@forEach
                }

                val ocupante = c.buscarUm(
                    """SELECT p.codigo FROM enderecamento e JOIN pallet p ON p.id = e.pallet_id
                        WHERE e.posicao_id = ? AND e.liberado_em IS NULL""",
                    anterior.first,
                ) { it.getString(1) }

                if (ocupante != null) {
                    pendentes += PalletSemEndereco(
                        codigo, "${anterior.second} nível ${anterior.third}", ocupante,
                    )
                    c.atualizar("UPDATE pallet SET status='ENCERRADO' WHERE id=?", palletId)
                } else {
                    c.inserir(
                        """INSERT INTO enderecamento (pallet_id, posicao_id, enderecado_em, enderecado_por)
                           VALUES (?,?,?,?)""",
                        palletId, anterior.first, agora, usuarioId,
                    )
                    c.atualizar("UPDATE pallet SET status='ENDERECADO' WHERE id=?", palletId)
                    restaurados++
                }
            }

            c.atualizar(
                """UPDATE expedicao
                      SET estornado_em=?, estornado_por=?, estorno_motivo=?,
                          estorno_tipo='ERRO_OPERACIONAL'
                    WHERE id=?""",
                agora, usuarioId, motivo, expedicaoId,
            )
            // Parte da NF pode continuar na rua: saída anterior que não voltou.
            c.atualizar("UPDATE nota_fiscal SET status=? WHERE id=?", situacaoDaNota(c, exp.first), exp.first)

            ResultadoEstorno(exp.second, restaurados, pendentes)
        }

    // -------------------------------------------------------------- reentrega

    /** Os pallets de uma NF expedida, com o que já voltou marcado. */
    fun expedidos(notaId: Long): NotaExpedidaResumo = db.leitura { c ->
        val nota = c.buscarUm(
            "SELECT numero, dest_nome, status FROM nota_fiscal WHERE id = ?", notaId,
        ) { Triple(it.getString(1), it.getString(2), it.getString(3)) }
            ?: throw Erros.naoEncontrado("Nota fiscal")

        if (nota.third !in setOf("EXPEDIDA", "RETORNO_PARCIAL")) {
            throw Erros.conflito(
                "NOTA_NAO_EXPEDIDA",
                "NF ${nota.first} não está expedida (situação: ${nota.third.lowercase()})",
            )
        }

        NotaExpedidaResumo(notaId, nota.first, nota.second, nota.third, retornaveis(c, notaId))
    }

    /**
     * A carga saiu, o cliente não recebeu e ela voltou inteira.
     *
     * Diferente do estorno: os endereços originais não são restaurados. O
     * pallet esteve fora do CD por horas e a posição já foi reaproveitada —
     * tentar retomá-la brigaria com o que está lá fisicamente. Volta sem
     * endereço, para o coletor endereçar de novo.
     * Ver docs/MODELO-DE-DADOS.md §9.2.
     */
    fun reentregar(
        usuarioId: Long,
        notaId: Long,
        palletCodigos: List<String>,
        motivo: String,
    ): ResultadoReentrega = db.transacao { c ->
        if (motivo.isBlank()) {
            throw Erros.invalido("MOTIVO_OBRIGATORIO", "Informe o motivo do retorno")
        }
        if (palletCodigos.isEmpty()) {
            throw Erros.invalido("NENHUM_PALLET", "Selecione ao menos um pallet retornado")
        }

        val nota = c.buscarUm(
            "SELECT numero, status FROM nota_fiscal WHERE id = ?", notaId,
        ) { it.getString(1) to it.getString(2) } ?: throw Erros.naoEncontrado("Nota fiscal")

        if (nota.second !in setOf("EXPEDIDA", "RETORNO_PARCIAL")) {
            throw Erros.conflito(
                "NOTA_NAO_EXPEDIDA",
                "NF ${nota.first} não está expedida (situação: ${nota.second.lowercase()})",
            )
        }
        exigirSemRetornoAberto(c, notaId, nota.first)

        val pendentes = retornaveis(c, notaId).filter { it.retornadoEm == null }
            .associateBy { it.palletCodigo }
        val pedidos = palletCodigos.map { it.trim().uppercase() }.distinct()

        val desconhecidos = pedidos.filterNot { it in pendentes }
        if (desconhecidos.isNotEmpty()) {
            throw Erros.conflito(
                "PALLET_NAO_RETORNAVEL",
                "Fora da expedição desta NF ou já retornado: ${desconhecidos.joinToString()}",
                mapOf("pallets" to desconhecidos),
            )
        }

        val agora = Instant.now().toString()
        var caixas = 0
        pedidos.forEach { codigo ->
            val p = pendentes.getValue(codigo)
            caixas += p.qtdCaixas
            c.atualizar(
                "UPDATE expedicao_pallet SET retornado_em=?, retornado_por=? WHERE id=?",
                agora, usuarioId, p.expedicaoPalletId,
            )
            // Sem endereço: entra na fila de endereçamento do coletor.
            c.atualizar(
                "UPDATE pallet SET status='ENCERRADO' WHERE codigo=?", codigo,
            )
        }

        val faltam = retornaveis(c, notaId).count { it.retornadoEm == null }
        val novoStatus = if (faltam == 0) "CONCLUIDA" else "RETORNO_PARCIAL"
        c.atualizar("UPDATE nota_fiscal SET status=? WHERE id=?", novoStatus, notaId)

        c.inserir(
            """INSERT INTO log_auditoria
               (entidade, entidade_id, acao, usuario_id, em, detalhe_json)
               VALUES ('nota_fiscal', ?, 'REENTREGA', ?, ?, ?)""",
            notaId, usuarioId, agora,
            "${pedidos.size} pallet(s), $caixas caixa(s): $motivo",
        )

        ResultadoReentrega(nota.first, novoStatus, pedidos, caixas, faltam)
    }

    /**
     * Situação atual de cada pallet da NF: uma linha por pallet, a da **última**
     * vez que ele saiu.
     *
     * Um pallet pode ter saído mais de uma vez — retorno parcial seguido de nova
     * expedição. Somar todas as saídas mostraria o mesmo pallet duas vezes, uma
     * como retornado e outra como pendente. Só a saída mais recente diz onde ele
     * está agora.
     */
    private fun retornaveis(c: Connection, notaId: Long): List<PalletRetornavel> = c.buscarTodos(
        """SELECT ep.id, p.codigo,
                  (SELECT COUNT(*) FROM leitura l WHERE l.pallet_id = p.id AND l.cancelada = 0),
                  e.expedido_em, ep.retornado_em
             FROM expedicao e
             JOIN expedicao_pallet ep ON ep.expedicao_id = e.id
             JOIN pallet p ON p.id = ep.pallet_id
            WHERE e.nota_fiscal_id = ? AND e.estornado_em IS NULL
              AND ep.id = (SELECT MAX(ep2.id)
                             FROM expedicao_pallet ep2
                             JOIN expedicao e2 ON e2.id = ep2.expedicao_id
                            WHERE ep2.pallet_id = ep.pallet_id AND e2.estornado_em IS NULL)
            ORDER BY p.codigo""",
        notaId,
    ) {
        PalletRetornavel(
            it.getLong(1), it.getString(2), it.getInt(3), it.getString(4), it.getString(5),
        )
    }

    /** Retorno em conferência ou esperando tratativa trava a NF: ninguém decidiu o destino ainda. */
    private fun exigirSemRetornoAberto(c: Connection, notaId: Long, numero: String) {
        c.buscarUm(
            "SELECT 1 FROM retorno WHERE nota_fiscal_id = ? AND status IN ('EM_CONFERENCIA','AGUARDANDO_TRATATIVA')",
            notaId,
        ) { it.getInt(1) }?.let {
            throw Erros.conflito(
                "RETORNO_EM_ABERTO",
                "NF $numero tem retorno em conferência ou esperando tratativa: resolva-o antes",
                mapOf("notaNumero" to numero),
            )
        }
    }

    private fun palletsDaNota(c: Connection, notaId: Long): List<PalletExpedido> = c.buscarTodos(
        """SELECT p.codigo, b.codigo, po.nivel,
                  (SELECT COUNT(*) FROM leitura l WHERE l.pallet_id = p.id AND l.cancelada = 0)
             FROM pallet p
             LEFT JOIN enderecamento e ON e.pallet_id = p.id AND e.liberado_em IS NULL
             LEFT JOIN posicao po ON po.id = e.posicao_id
             LEFT JOIN box b ON b.id = po.box_id
            WHERE p.nota_fiscal_id = ? AND p.status IN ('EM_USO','ENCERRADO','ENDERECADO')
            ORDER BY p.codigo""",
        notaId,
    ) {
        val box = it.getString(2)
        PalletExpedido(
            codigo = it.getString(1),
            endereco = box?.let { b -> "$b nível ${it.getInt(3)}" },
            qtdCaixas = it.getInt(4),
        )
    }
}
