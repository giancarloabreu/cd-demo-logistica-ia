package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.importacao.Importador
import br.com.cddemo.redespacho.importacao.PreviaArquivo
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import java.sql.Connection
import java.time.Instant

data class ArquivoXml(val nome: String, val bytes: ByteArray)

data class ItemDivergente(
    val notaNumero: String,
    val produtoCodigo: String,
    val produtoDescricao: String,
    val qtdPrevista: Int,
    val qtdSeparada: Int,
    val justificativa: String?,
    val fechadoPor: String?,
    val fechadoEm: String?,
)

data class ResumoInstrucao(
    val id: Long,
    val numero: String,
    val status: String,
    val criadoEm: String,
    val criadoPor: String,
    val qtdNotas: Int,
    val qtdCaixasPrevistas: Int,
    val qtdCaixasSeparadas: Int,
    /**
     * Todos os cadeados fechados, esperando a retaguarda confirmar.
     *
     * A instrução continua no coletor até alguém clicar em concluir — o
     * separador precisa enxergar que a parte dele acabou sem que ela suma da
     * tela. Ver docs/TELAS-COLETOR.md §4.
     */
    val itensEmAberto: Int = 0,
) {
    val prontaParaConclusao: Boolean get() = status != "CONCLUIDA" && itensEmAberto == 0
}

class InstrucaoServico(private val db: Db) {

    fun criar(usuarioId: Long): ResumoInstrucao = db.transacao { c ->
        val numero = Sequencias.proximo(c, Sequencias.INSTRUCAO)
        val agora = Instant.now().toString()
        val id = c.inserir(
            "INSERT INTO instrucao (numero, status, criado_em, criado_por) VALUES (?,?,?,?)",
            numero, "ABERTA", agora, usuarioId,
        )
        buscarResumo(c, id)!!
    }

    fun listar(status: List<String>? = null): List<ResumoInstrucao> = db.leitura { c ->
        val filtro = status?.takeIf { it.isNotEmpty() }
            ?.joinToString(",") { "'$it'" }
            ?.let { "WHERE i.status IN ($it)" } ?: ""
        c.buscarTodos(sqlResumo(filtro)) { mapaResumo(it) }
    }

    fun buscar(id: Long): ResumoInstrucao? = db.leitura { c -> buscarResumo(c, id) }

    /**
     * Exclui uma NF. A trava é por nota, não pela instrução: dá para remover
     * uma nota intocada mesmo que outras já estejam em separação.
     * Ver docs/TELAS-RETAGUARDA.md §1.3.
     */
    fun excluirNota(notaId: Long) = db.transacao { c ->
        val nota = c.buscarUm(
            "SELECT numero, instrucao_id FROM nota_fiscal WHERE id = ?", notaId,
        ) { it.getString(1) to it.getLong(2) } ?: throw Erros.naoEncontrado("Nota")

        exigirSemSeparacao(c, notaId, "NF ${nota.first}")

        c.atualizar("DELETE FROM item_em_separacao WHERE item_nota_id IN " +
            "(SELECT id FROM item_nota WHERE nota_fiscal_id = ?)", notaId)
        c.atualizar("DELETE FROM item_nota WHERE nota_fiscal_id = ?", notaId)
        c.atualizar("DELETE FROM nota_fiscal WHERE id = ?", notaId)
    }

    fun excluirInstrucao(id: Long) = db.transacao { c ->
        val status = c.buscarUm("SELECT status FROM instrucao WHERE id = ?", id) { it.getString(1) }
            ?: throw Erros.naoEncontrado("Instrução")
        if (status == "CONCLUIDA") {
            throw Erros.conflito("INSTRUCAO_CONCLUIDA", "Instrução concluída não pode ser excluída")
        }

        val notas = c.buscarTodos(
            "SELECT id, numero FROM nota_fiscal WHERE instrucao_id = ?", id,
        ) { it.getLong(1) to it.getString(2) }

        notas.forEach { (notaId, numero) -> exigirSemSeparacao(c, notaId, "NF $numero") }

        notas.forEach { (notaId, _) ->
            c.atualizar("DELETE FROM item_em_separacao WHERE item_nota_id IN " +
                "(SELECT id FROM item_nota WHERE nota_fiscal_id = ?)", notaId)
            c.atualizar("DELETE FROM item_nota WHERE nota_fiscal_id = ?", notaId)
        }
        c.atualizar("DELETE FROM nota_fiscal WHERE instrucao_id = ?", id)
        c.atualizar("DELETE FROM instrucao WHERE id = ?", id)
        // O sequencial não volta: buraco na numeração é inofensivo, número
        // repetido apontando para duas instruções não.
    }

    /** Itens fechados com divergência, para a confirmação da conclusão. */
    fun divergencias(instrucaoId: Long): List<ItemDivergente> = db.leitura { c ->
        c.buscarTodos(
            """SELECT n.numero, p.codigo, p.descricao, i.qtd_prevista, i.qtd_separada,
                      i.justificativa, u.nome, i.fechado_em
                 FROM item_nota i
                 JOIN nota_fiscal n ON n.id = i.nota_fiscal_id
                 JOIN produto p ON p.id = i.produto_id
                 LEFT JOIN usuario u ON u.id = i.fechado_por
                WHERE n.instrucao_id = ? AND i.status = 'FECHADO_DIVERGENCIA'
                ORDER BY n.numero, i.n_item""",
            instrucaoId,
        ) {
            ItemDivergente(it.getString(1), it.getString(2), it.getString(3),
                it.getInt(4), it.getInt(5), it.getString(6), it.getString(7), it.getString(8))
        }
    }

    fun concluir(id: Long, usuarioId: Long, confirmarDivergencias: Boolean): ResumoInstrucao =
        db.transacao { c ->
            val status = c.buscarUm("SELECT status FROM instrucao WHERE id = ?", id) { it.getString(1) }
                ?: throw Erros.naoEncontrado("Instrução")
            if (status == "CONCLUIDA") {
                throw Erros.conflito("INSTRUCAO_CONCLUIDA", "Instrução já está concluída")
            }

            val abertos = c.buscarUm(
                """SELECT COUNT(*) FROM item_nota i
                     JOIN nota_fiscal n ON n.id = i.nota_fiscal_id
                    WHERE n.instrucao_id = ?
                      AND i.status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
                id,
            ) { it.getInt(1) } ?: 0
            if (abertos > 0) {
                throw Erros.conflito(
                    "ITENS_EM_ABERTO",
                    "$abertos item(ns) ainda com cadeado aberto. Feche todos antes de concluir.",
                    mapOf("itensEmAberto" to abertos),
                )
            }

            val comDivergencia = c.buscarUm(
                """SELECT COUNT(*) FROM item_nota i
                     JOIN nota_fiscal n ON n.id = i.nota_fiscal_id
                    WHERE n.instrucao_id = ? AND i.status = 'FECHADO_DIVERGENCIA'""",
                id,
            ) { it.getInt(1) } ?: 0

            // As justificativas já foram dadas item a item; aqui só se confirma.
            if (comDivergencia > 0 && !confirmarDivergencias) {
                throw Erros.conflito(
                    "INSTRUCAO_COM_DIVERGENCIA",
                    "$comDivergencia item(ns) com divergência. Confirme para concluir mesmo assim.",
                    mapOf("qtdDivergencias" to comDivergencia),
                )
            }

            c.atualizar(
                "UPDATE instrucao SET status='CONCLUIDA', concluido_em=?, concluido_por=? WHERE id=?",
                Instant.now().toString(), usuarioId, id,
            )
            buscarResumo(c, id)!!
        }

    private fun exigirSemSeparacao(c: Connection, notaId: Long, rotulo: String) {
        val leituras = c.buscarUm(
            """SELECT COUNT(*) FROM leitura l
                 JOIN item_nota i ON i.id = l.item_nota_id
                WHERE i.nota_fiscal_id = ? AND l.cancelada = 0""",
            notaId,
        ) { it.getInt(1) } ?: 0
        if (leituras > 0) {
            throw Erros.conflito(
                "SEPARACAO_INICIADA",
                "$rotulo tem $leituras caixa(s) separada(s) e não pode ser excluída",
                mapOf("qtdSeparada" to leituras),
            )
        }
    }

    /**
     * Analisa os XMLs sem gravar nada. É o que a tela mostra antes de o
     * operador confirmar. Ver docs/TELAS-RETAGUARDA.md §1.1.
     */
    fun previa(instrucaoId: Long, arquivos: List<ArquivoXml>): List<PreviaArquivo> = db.leitura { c ->
        exigirInstrucaoEditavel(c, instrucaoId)
        arquivos.map { arq ->
            when (val p = Importador.analisar(arq.nome, arq.bytes)) {
                is PreviaArquivo.Rejeitado -> p
                is PreviaArquivo.Ok -> {
                    val dona = c.buscarUm(
                        """SELECT i.numero FROM nota_fiscal n
                           JOIN instrucao i ON i.id = n.instrucao_id
                          WHERE n.chave = ?""",
                        p.nota.chave,
                    ) { it.getString(1) }
                    if (dona != null) {
                        PreviaArquivo.Rejeitado(
                            arq.nome, "NOTA_JA_IMPORTADA",
                            "NF ${p.nota.numero} já pertence à instrução $dona",
                        )
                    } else {
                        p
                    }
                }
            }
        }
    }

    /**
     * Grava as NFs. Reanalisa os arquivos em vez de confiar na prévia: entre
     * uma chamada e outra outro operador pode ter importado a mesma nota.
     */
    fun importar(instrucaoId: Long, usuarioId: Long, arquivos: List<ArquivoXml>): List<PreviaArquivo> =
        db.transacao { c ->
            exigirInstrucaoEditavel(c, instrucaoId)
            val agora = Instant.now().toString()

            arquivos.map { arq ->
                when (val p = Importador.analisar(arq.nome, arq.bytes)) {
                    is PreviaArquivo.Rejeitado -> p
                    is PreviaArquivo.Ok -> {
                        val jaExiste = c.buscarUm(
                            """SELECT i.numero FROM nota_fiscal n
                               JOIN instrucao i ON i.id = n.instrucao_id
                              WHERE n.chave = ?""",
                            p.nota.chave,
                        ) { it.getString(1) }

                        if (jaExiste != null) {
                            PreviaArquivo.Rejeitado(
                                arq.nome, "NOTA_JA_IMPORTADA",
                                "NF ${p.nota.numero} já pertence à instrução $jaExiste",
                            )
                        } else {
                            gravarNota(c, instrucaoId, usuarioId, agora, p, arq.bytes)
                            p
                        }
                    }
                }
            }
        }

    private fun gravarNota(
        c: Connection,
        instrucaoId: Long,
        usuarioId: Long,
        agora: String,
        previa: PreviaArquivo.Ok,
        xml: ByteArray,
    ) {
        val n = previa.nota
        val notaId = c.inserir(
            """INSERT INTO nota_fiscal
               (instrucao_id, chave, numero, serie, emissao, dest_cnpj, dest_nome,
                dest_logradouro, dest_numero, dest_complemento, dest_bairro,
                dest_municipio, dest_uf, dest_cep, qtd_volumes_xml, bonificacao, status,
                xml_original, importado_em, importado_por)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            instrucaoId, n.chave, n.numero, n.serie, n.emissao, n.destCnpj, n.destNome,
            n.destLogradouro, n.destNumero, n.destComplemento, n.destBairro,
            n.destMunicipio, n.destUf, n.destCep, n.qtdVolumes, n.bonificacao, "PENDENTE",
            xml, agora, usuarioId,
        )

        previa.itens.forEach { pi ->
            val produtoId = obterOuCriarProduto(c, pi, agora, usuarioId)
            c.inserir(
                """INSERT INTO item_nota
                   (nota_fiscal_id, n_item, produto_id, qtd_prevista, qtd_separada, status)
                   VALUES (?,?,?,?,0,'ABERTO')""",
                notaId, pi.item.nItem, produtoId, pi.item.qCom.toBigInteger().toInt(),
            )
        }
    }

    /**
     * O produto vem do XML. O `cEAN` entra como código de barras não
     * confirmado — a primeira leitura bem-sucedida no coletor o promove.
     * Ver docs/MODELO-DE-DADOS.md §5.
     */
    private fun obterOuCriarProduto(
        c: Connection,
        pi: br.com.cddemo.redespacho.importacao.PreviaItem,
        agora: String,
        usuarioId: Long,
    ): Long {
        val codigo = pi.item.cProd
        val existente = c.buscarUm("SELECT id FROM produto WHERE codigo = ?", codigo) { it.getLong(1) }

        val produtoId = existente ?: c.inserir(
            """INSERT INTO produto
               (codigo, descricao, unidades_por_caixa, gramas_por_unidade, un_cx_origem, origem, criado_em)
               VALUES (?,?,?,?,?,'XML',?)""",
            codigo, pi.item.xProd,
            pi.embalagem?.unidadesPorCaixa, pi.embalagem?.gramasPorUnidade,
            pi.embalagem?.let { "XML_VALIDADO" }, agora,
        )

        // Produto que já existia sem embalagem determinada é completado quando
        // um XML posterior traz dado suficiente para validar.
        if (existente != null && pi.embalagem != null) {
            c.atualizar(
                """UPDATE produto
                      SET unidades_por_caixa = ?, gramas_por_unidade = ?, un_cx_origem = 'XML_VALIDADO'
                    WHERE id = ? AND unidades_por_caixa IS NULL""",
                pi.embalagem.unidadesPorCaixa, pi.embalagem.gramasPorUnidade, produtoId,
            )
        }

        val gtin = pi.gtinCaixa
        if (gtin != null) {
            val dono = c.buscarUm(
                "SELECT produto_id FROM produto_codigo_barras WHERE codigo = ?", gtin,
            ) { it.getLong(1) }
            // Um código jamais aponta para dois produtos (invariante 10). Se já
            // pertence a outro, não sobrescreve: o conflito vai para o aviso da
            // prévia e o supervisor decide.
            if (dono == null) {
                c.inserir(
                    """INSERT INTO produto_codigo_barras
                       (produto_id, codigo, simbologia, origem, confirmado, criado_em, criado_por)
                       VALUES (?,?,'CODE128','XML',0,?,?)""",
                    produtoId, gtin, agora, usuarioId,
                )
            }
        }
        return produtoId
    }

    private fun exigirInstrucaoEditavel(c: Connection, id: Long) {
        val status = c.buscarUm("SELECT status FROM instrucao WHERE id = ?", id) { it.getString(1) }
            ?: throw Erros.naoEncontrado("Instrução")
        if (status !in setOf("ABERTA", "EM_SEPARACAO")) {
            throw Erros.conflito(
                "INSTRUCAO_CONCLUIDA",
                "Instrução $status não aceita novas notas",
                mapOf("status" to status),
            )
        }
    }

    private fun buscarResumo(c: Connection, id: Long): ResumoInstrucao? =
        c.buscarUm(sqlResumo("WHERE i.id = ?"), id) { mapaResumo(it) }

    private fun sqlResumo(filtro: String) = """
        SELECT i.id, i.numero, i.status, i.criado_em, u.nome,
               (SELECT COUNT(*) FROM nota_fiscal n WHERE n.instrucao_id = i.id),
               COALESCE((SELECT SUM(it.qtd_prevista) FROM item_nota it
                          JOIN nota_fiscal n ON n.id = it.nota_fiscal_id
                         WHERE n.instrucao_id = i.id), 0),
               COALESCE((SELECT SUM(it.qtd_separada) FROM item_nota it
                          JOIN nota_fiscal n ON n.id = it.nota_fiscal_id
                         WHERE n.instrucao_id = i.id), 0),
               (SELECT COUNT(*) FROM item_nota it
                  JOIN nota_fiscal n ON n.id = it.nota_fiscal_id
                 WHERE n.instrucao_id = i.id
                   AND it.status NOT IN ('FECHADO','FECHADO_DIVERGENCIA'))
          FROM instrucao i
          JOIN usuario u ON u.id = i.criado_por
        $filtro
        ORDER BY i.id DESC
    """.trimIndent()

    private fun mapaResumo(rs: java.sql.ResultSet) = ResumoInstrucao(
        id = rs.getLong(1),
        numero = rs.getString(2),
        status = rs.getString(3),
        criadoEm = rs.getString(4),
        criadoPor = rs.getString(5),
        qtdNotas = rs.getInt(6),
        qtdCaixasPrevistas = rs.getInt(7),
        qtdCaixasSeparadas = rs.getInt(8),
        itensEmAberto = rs.getInt(9),
    )
}
