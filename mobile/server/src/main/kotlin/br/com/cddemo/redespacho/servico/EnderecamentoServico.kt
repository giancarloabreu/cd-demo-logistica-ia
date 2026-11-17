package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.importacao.LinhaPlanilha
import br.com.cddemo.redespacho.importacao.Planilha
import br.com.cddemo.redespacho.importacao.PlanilhaInvalidaException
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import java.sql.Connection
import java.time.Instant

data class ResultadoImportacaoBoxes(
    val depositos: Int,
    val ruas: Int,
    val boxes: Int,
    val posicoes: Int,
    val jaExistiam: Int,
)

/** Um box como aparece no cadastro de endereços e na planilha. */
data class BoxCadastro(
    val codigo: String,
    val codigoBarras: String,
    val depositoCodigo: String,
    val depositoNome: String,
    val rua: String,
    val tipo: String,
    val niveis: Int,
    val ativo: Boolean,
    /** Níveis com pallet agora. */
    val ocupadas: Int,
)

data class LinhaPreviaBoxes(
    /** A linha no Excel (1 é o cabeçalho). */
    val linha: Int,
    val box: String,
    /** NOVO, ALTERADO ou ERRO. Linha igual ao cadastro só entra na contagem. */
    val acao: String,
    val detalhe: String,
)

data class PreviaBoxes(
    val novos: Int,
    val alterados: Int,
    val iguais: Int,
    val erros: Int,
    val linhas: List<LinhaPreviaBoxes>,
)

data class PosicaoResumo(
    val id: Long,
    val nivel: Int,
    val ocupadaPor: String?,
    val notaNumero: String?,
    val qtdCaixas: Int?,
    /** Ocupada, mas pode receber este pallet mesmo assim (bonificação). */
    val aceitaCoabitacao: Boolean = false,
    /** Cliente da NF do pallet (emitente, no de devolução). */
    val cliente: String? = null,
    /** SEPARACAO ou DEVOLUCAO: o de devolução conta em unidades. */
    val tipoPallet: String? = null,
) {
    val livre: Boolean get() = ocupadaPor == null
}

data class BoxResumo(
    val id: Long,
    val codigo: String,
    val deposito: String,
    val rua: String,
    val tipo: String,
    val posicoes: List<PosicaoResumo>,
)

data class LinhaRoteiro(
    val palletCodigo: String,
    val box: String?,
    val nivel: Int?,
    val deposito: String?,
    val rua: String?,
    val qtdCaixas: Int,
    val status: String,
)

data class Roteiro(
    val notaNumero: String,
    val cliente: String,
    val qtdPallets: Int,
    val semEndereco: Int,
    val linhas: List<LinhaRoteiro>,
)

data class PalletParaEnderecar(
    val codigo: String,
    val tipo: String,
    val status: String,
    val notaNumero: String?,
    val cliente: String?,
    val qtdCaixas: Int,
    val enderecoAtual: String?,
    val bonificacao: Boolean = false,
)

/** Colunas da planilha de endereços, na ordem do modelo. */
private val COLUNAS_BOXES = listOf(
    "Depósito (código)", "Depósito (nome)", "Rua", "Box", "Código de barras",
    "Tipo (ESTOCAGEM ou PICKING)", "Níveis", "Ativo (SIM ou NÃO)",
)
private val ROTULO_CAMPO = mapOf(
    "deposito" to "Depósito (código)", "rua" to "Rua", "box" to "Box", "tipo" to "Tipo", "niveis" to "Níveis",
)

class EnderecamentoServico(private val db: Db) {

    /**
     * Importa a malha de boxes a partir do CSV exportado do BOXES DEMO.xls.
     * Idempotente: rodar de novo não duplica nada, só acrescenta o que falta.
     */
    fun importarBoxes(csv: String): ResultadoImportacaoBoxes = db.transacao { c ->
        val linhas = csv.lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .toList()
        if (linhas.size < 2) {
            throw Erros.invalido("CSV_VAZIO", "O arquivo não tem linhas de dados")
        }

        val cabecalho = linhas.first().split(';').map { it.trim().lowercase() }
        val col = { nome: String ->
            cabecalho.indexOf(nome).takeIf { it >= 0 }
                ?: throw Erros.invalido("COLUNA_AUSENTE", "Coluna \"$nome\" não encontrada no CSV")
        }
        val cDepCod = col("deposito_codigo"); val cDepNome = col("deposito_nome")
        val cRua = col("rua"); val cBox = col("box_codigo")
        val cBarras = col("box_barras"); val cTipo = col("tipo"); val cNiveis = col("niveis")

        var novosDep = 0; var novasRuas = 0; var novosBoxes = 0; var novasPos = 0; var repetidos = 0

        linhas.drop(1).forEach { linha ->
            val p = linha.split(';').map { it.trim() }
            if (p.size <= maxOf(cDepCod, cDepNome, cRua, cBox, cBarras, cTipo, cNiveis)) return@forEach

            val depositoId = c.buscarUm(
                "SELECT id FROM deposito WHERE codigo = ?", p[cDepCod],
            ) { it.getLong(1) } ?: c.inserir(
                "INSERT INTO deposito (codigo, nome) VALUES (?,?)", p[cDepCod], p[cDepNome],
            ).also { novosDep++ }

            val ruaId = c.buscarUm(
                "SELECT id FROM rua WHERE deposito_id = ? AND sigla = ?", depositoId, p[cRua],
            ) { it.getLong(1) } ?: c.inserir(
                "INSERT INTO rua (deposito_id, sigla) VALUES (?,?)", depositoId, p[cRua],
            ).also { novasRuas++ }

            val existente = c.buscarUm("SELECT id FROM box WHERE codigo = ?", p[cBox]) { it.getLong(1) }
            if (existente != null) { repetidos++; return@forEach }

            val niveis = p[cNiveis].toIntOrNull() ?: 6
            val boxId = c.inserir(
                """INSERT INTO box (codigo, codigo_barras, deposito_id, rua_id, tipo, qtd_niveis)
                   VALUES (?,?,?,?,?,?)""",
                p[cBox], p[cBarras], depositoId, ruaId, p[cTipo], niveis,
            )
            novosBoxes++

            // Posições materializadas: dão FK ao endereçamento e permitem
            // consultar ocupação numa query só.
            (1..niveis).forEach { nivel ->
                c.inserir("INSERT INTO posicao (box_id, nivel) VALUES (?,?)", boxId, nivel)
                novasPos++
            }
        }

        ResultadoImportacaoBoxes(novosDep, novasRuas, novosBoxes, novasPos, repetidos)
    }

    fun totalBoxes(): Int = db.leitura { c ->
        c.buscarUm("SELECT COUNT(*) FROM box WHERE ativo = 1") { it.getInt(1) } ?: 0
    }

    // --------------------------------------------- cadastro de endereços em lote

    fun listarBoxes(): List<BoxCadastro> = db.leitura { c ->
        c.buscarTodos(
            """SELECT b.codigo, b.codigo_barras, d.codigo, d.nome, r.sigla, b.tipo, b.qtd_niveis, b.ativo,
                      (SELECT COUNT(*) FROM enderecamento e JOIN posicao po ON po.id = e.posicao_id
                        WHERE po.box_id = b.id AND e.liberado_em IS NULL)
                 FROM box b JOIN deposito d ON d.id = b.deposito_id JOIN rua r ON r.id = b.rua_id
                ORDER BY d.codigo, r.sigla, b.codigo""",
        ) {
            BoxCadastro(
                it.getString(1), it.getString(2), it.getString(3), it.getString(4), it.getString(5),
                it.getString(6), it.getInt(7), it.getInt(8) == 1, it.getInt(9),
            )
        }
    }

    /**
     * A planilha para preencher. Vazia, é o modelo (só o cabeçalho — linha de
     * exemplo esquecida viraria box de verdade); com [comAtuais], já traz o
     * cadastro de hoje para editar e subir de volta.
     */
    fun planilhaDeBoxes(comAtuais: Boolean): ByteArray {
        val linhas = if (!comAtuais) emptyList() else listarBoxes().map { b ->
            listOf(
                b.depositoCodigo, b.depositoNome, b.rua, b.codigo, b.codigoBarras, b.tipo,
                b.niveis.toString(), if (b.ativo) "SIM" else "NÃO",
            )
        }
        return Planilha.xlsx("Enderecos", COLUNAS_BOXES, linhas, listOf(12, 28, 8, 12, 18, 28, 8, 18))
    }

    /** O que a planilha faria, linha a linha, sem gravar nada. */
    fun previaPlanilhaBoxes(bytes: ByteArray): PreviaBoxes = db.leitura { c ->
        planejar(c, lerPlanilhaDeBoxes(bytes)).previa
    }

    /**
     * Grava a planilha: tudo ou nada. Com qualquer linha com erro, nada muda —
     * meio cadastro aplicado é pior do que nenhum. Box que não está na planilha
     * fica como está: a planilha acrescenta e corrige, nunca apaga.
     */
    fun aplicarPlanilhaBoxes(bytes: ByteArray): PreviaBoxes = db.transacao { c ->
        val plano = planejar(c, lerPlanilhaDeBoxes(bytes))
        if (plano.previa.erros > 0) {
            throw Erros.conflito(
                "PLANILHA_COM_ERROS",
                "A planilha tem ${plano.previa.erros} linha(s) com erro. Nada foi gravado: corrija e envie de novo.",
                mapOf("linhas" to plano.previa.linhas.filter { it.acao == "ERRO" }),
            )
        }
        plano.itens.forEach { aplicar(c, it) }
        plano.previa
    }

    private class ItemPlano(
        val deposito: String, val depositoNome: String, val rua: String, val box: String, val barras: String,
        val tipo: String, val niveis: Int, val ativo: Boolean, val existente: BoxAtual?,
    )

    private data class BoxAtual(
        val id: Long, val barras: String, val deposito: String, val rua: String,
        val tipo: String, val niveis: Int, val ativo: Boolean,
    )

    private class Plano(val previa: PreviaBoxes, val itens: List<ItemPlano>)

    private class PlanilhaDeBoxes(val colunas: Map<String, Int>, val linhas: List<LinhaPlanilha>)

    private fun lerPlanilhaDeBoxes(bytes: ByteArray): PlanilhaDeBoxes {
        val linhas = try {
            Planilha.ler(bytes)
        } catch (e: PlanilhaInvalidaException) {
            throw Erros.invalido("PLANILHA_INVALIDA", e.message.orEmpty())
        }
        if (linhas.isEmpty()) throw Erros.invalido("PLANILHA_VAZIA", "A planilha não tem linhas")
        val colunas = mutableMapOf<String, Int>()
        linhas.first().celulas.forEachIndexed { i, nome -> campoDaColuna(nome)?.let { colunas.putIfAbsent(it, i) } }
        val faltando = ROTULO_CAMPO.keys.filter { it !in colunas }
        if (faltando.isNotEmpty()) {
            throw Erros.invalido(
                "COLUNA_AUSENTE",
                "Faltam colunas na planilha: ${faltando.joinToString { ROTULO_CAMPO.getValue(it) }}. Use o modelo.",
            )
        }
        return PlanilhaDeBoxes(colunas, linhas.drop(1))
    }

    /** Aceita o cabeçalho do modelo e o do CSV antigo (deposito_codigo, box_barras...). */
    private fun campoDaColuna(nome: String): String? {
        val n = java.text.Normalizer.normalize(nome, java.text.Normalizer.Form.NFD)
            .replace(Regex("\\p{M}"), "").lowercase().filter { it.isLetterOrDigit() }
        return when {
            n == "depositocodigo" || n == "deposito" || n == "codigodeposito" -> "deposito"
            n == "depositonome" || n == "nomedeposito" -> "depositoNome"
            n == "rua" -> "rua"
            n == "box" || n == "boxcodigo" || n == "codigobox" -> "box"
            n.contains("barras") -> "barras"
            n.startsWith("tipo") -> "tipo"
            n.startsWith("niveis") || n == "qtdniveis" -> "niveis"
            n.startsWith("ativo") -> "ativo"
            else -> null
        }
    }

    private fun planejar(c: Connection, p: PlanilhaDeBoxes): Plano {
        fun LinhaPlanilha.campo(nome: String) = p.colunas[nome]?.let { celulas.getOrNull(it) }.orEmpty().trim()

        val depositos = c.buscarTodos("SELECT codigo, nome FROM deposito") { it.getString(1) to it.getString(2) }.toMap()
        val nomesPedidos = mutableMapOf<String, String>()
        val boxesVistos = mutableMapOf<String, Int>()
        val barrasVistas = mutableMapOf<String, Int>()
        val resultado = mutableListOf<LinhaPreviaBoxes>()
        val itens = mutableListOf<ItemPlano>()
        var iguais = 0

        p.linhas.forEach { l ->
            if (l.celulas.all { it.isBlank() }) return@forEach
            val erros = mutableListOf<String>()
            val box = l.campo("box").uppercase()
            val deposito = l.campo("deposito")
            val depositoNome = l.campo("depositoNome")
            val rua = l.campo("rua").uppercase()
            val barras = l.campo("barras").uppercase().ifEmpty { box }
            val tipo = l.campo("tipo").uppercase()
            val niveis = l.campo("niveis").toIntOrNull()
            if (box.isEmpty()) erros += "box em branco"
            if (deposito.isEmpty()) erros += "código do depósito em branco"
            if (rua.isEmpty()) erros += "rua em branco"
            if (tipo !in setOf("ESTOCAGEM", "PICKING")) erros += "tipo \"${l.campo("tipo")}\" (use ESTOCAGEM ou PICKING)"
            if (niveis == null || niveis !in 1..20) erros += "níveis \"${l.campo("niveis")}\" (de 1 a 20)"
            val ativo = when (l.campo("ativo").uppercase()) {
                "", "SIM", "S", "1", "VERDADEIRO", "TRUE" -> true
                "NÃO", "NAO", "N", "0", "FALSO", "FALSE" -> false
                else -> { erros += "ativo \"${l.campo("ativo")}\" (use SIM ou NÃO)"; true }
            }
            if (box.isNotEmpty()) {
                boxesVistos[box]?.let { erros += "box repetido (linha $it)" } ?: boxesVistos.put(box, l.numero)
            }
            barrasVistas[barras]?.let { erros += "código de barras repetido (linha $it)" } ?: barrasVistas.put(barras, l.numero)
            c.buscarUm("SELECT codigo FROM box WHERE codigo_barras = ? AND codigo <> ?", barras, box) { it.getString(1) }
                ?.let { erros += "código de barras $barras já é do box $it" }
            if (deposito.isNotEmpty() && deposito !in depositos && depositoNome.isEmpty() && deposito !in nomesPedidos) {
                erros += "depósito $deposito não existe: informe o nome dele"
            }
            if (deposito.isNotEmpty() && depositoNome.isNotEmpty()) nomesPedidos.putIfAbsent(deposito, depositoNome)

            val existente = box.takeIf { it.isNotEmpty() }?.let { boxAtual(c, it) }
            val mudancas = mutableListOf<String>()
            if (existente != null && niveis != null) {
                if (existente.barras != barras) mudancas += "barras ${existente.barras} → $barras"
                if (existente.deposito != deposito) mudancas += "depósito ${existente.deposito} → $deposito"
                if (existente.rua != rua) mudancas += "rua ${existente.rua} → $rua"
                if (existente.tipo != tipo) mudancas += "tipo ${existente.tipo} → $tipo"
                if (existente.niveis != niveis) {
                    mudancas += "níveis ${existente.niveis} → $niveis"
                    if (niveis < existente.niveis) {
                        // Posição que já teve pallet tem histórico: não pode sumir.
                        val usados = c.buscarTodos(
                            """SELECT DISTINCT po.nivel FROM posicao po JOIN enderecamento e ON e.posicao_id = po.id
                                WHERE po.box_id = ? AND po.nivel > ? ORDER BY po.nivel""",
                            existente.id, niveis,
                        ) { it.getInt(1) }
                        if (usados.isNotEmpty()) {
                            erros += "não dá para reduzir para $niveis níveis: o nível ${usados.joinToString()} já teve pallet"
                        }
                    }
                }
                if (existente.ativo != ativo) {
                    mudancas += if (ativo) "reativa" else "desativa"
                    if (!ativo && ocupadas(c, existente.id) > 0) erros += "tem pallet endereçado: tire antes de desativar"
                }
            }
            if (deposito in depositos && depositoNome.isNotEmpty() && depositos[deposito] != depositoNome) {
                mudancas += "depósito $deposito passa a se chamar \"$depositoNome\""
            }

            when {
                erros.isNotEmpty() -> resultado += LinhaPreviaBoxes(l.numero, box, "ERRO", erros.joinToString("; "))
                existente != null && mudancas.isEmpty() -> iguais++
                else -> {
                    val novo = existente == null
                    resultado += LinhaPreviaBoxes(
                        l.numero, box, if (novo) "NOVO" else "ALTERADO",
                        if (novo) "depósito $deposito · rua $rua · $tipo · $niveis níveis" + (if (!ativo) " · inativo" else "")
                        else mudancas.joinToString("; "),
                    )
                    itens += ItemPlano(
                        deposito, depositoNome.ifEmpty { nomesPedidos[deposito].orEmpty() }, rua, box, barras,
                        tipo, niveis!!, ativo, existente,
                    )
                }
            }
        }
        return Plano(
            PreviaBoxes(
                novos = resultado.count { it.acao == "NOVO" },
                alterados = resultado.count { it.acao == "ALTERADO" },
                iguais = iguais,
                erros = resultado.count { it.acao == "ERRO" },
                linhas = resultado,
            ),
            itens,
        )
    }

    private fun aplicar(c: Connection, i: ItemPlano) {
        val depositoId = c.buscarUm("SELECT id FROM deposito WHERE codigo = ?", i.deposito) { it.getLong(1) }
            ?.also { id ->
                if (i.depositoNome.isNotEmpty()) {
                    c.atualizar("UPDATE deposito SET nome = ? WHERE id = ?", i.depositoNome, id)
                }
            }
            ?: c.inserir("INSERT INTO deposito (codigo, nome) VALUES (?,?)", i.deposito, i.depositoNome)
        val ruaId = c.buscarUm("SELECT id FROM rua WHERE deposito_id = ? AND sigla = ?", depositoId, i.rua) { it.getLong(1) }
            ?: c.inserir("INSERT INTO rua (deposito_id, sigla) VALUES (?,?)", depositoId, i.rua)
        val e = i.existente
        if (e == null) {
            val boxId = c.inserir(
                """INSERT INTO box (codigo, codigo_barras, deposito_id, rua_id, tipo, qtd_niveis, ativo)
                   VALUES (?,?,?,?,?,?,?)""",
                i.box, i.barras, depositoId, ruaId, i.tipo, i.niveis, if (i.ativo) 1 else 0,
            )
            (1..i.niveis).forEach { c.inserir("INSERT INTO posicao (box_id, nivel) VALUES (?,?)", boxId, it) }
        } else {
            c.atualizar(
                "UPDATE box SET codigo_barras=?, deposito_id=?, rua_id=?, tipo=?, qtd_niveis=?, ativo=? WHERE id=?",
                i.barras, depositoId, ruaId, i.tipo, i.niveis, if (i.ativo) 1 else 0, e.id,
            )
            if (i.niveis > e.niveis) {
                (e.niveis + 1..i.niveis).forEach { n ->
                    c.atualizar("INSERT OR IGNORE INTO posicao (box_id, nivel) VALUES (?,?)", e.id, n)
                }
            }
            // Só chega aqui sem histórico nos níveis que saem (conferido no plano).
            if (i.niveis < e.niveis) c.atualizar("DELETE FROM posicao WHERE box_id = ? AND nivel > ?", e.id, i.niveis)
        }
    }

    private fun boxAtual(c: Connection, codigo: String): BoxAtual? = c.buscarUm(
        """SELECT b.id, b.codigo_barras, d.codigo, r.sigla, b.tipo, b.qtd_niveis, b.ativo
             FROM box b JOIN deposito d ON d.id = b.deposito_id JOIN rua r ON r.id = b.rua_id
            WHERE b.codigo = ?""",
        codigo,
    ) {
        BoxAtual(
            it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
            it.getString(5), it.getInt(6), it.getInt(7) == 1,
        )
    }

    private fun ocupadas(c: Connection, boxId: Long): Int = c.buscarUm(
        """SELECT COUNT(*) FROM enderecamento e JOIN posicao po ON po.id = e.posicao_id
            WHERE po.box_id = ? AND e.liberado_em IS NULL""",
        boxId,
    ) { it.getInt(1) } ?: 0

    /** Valida o pallet e devolve o que o separador precisa conferir antes de andar até o box. */
    /**
     * O que pode receber endereço. EM_USO passa: o endereço é onde o palete ESTÁ,
     * e ele já está na rua quando o separador o leva para a câmara — muitas
     * vezes antes de lembrar de encerrar. Ver docs/TELAS-COLETOR.md §6.
     */
    private fun exigirEnderecavel(status: String, codigo: String) {
        when (status) {
            "CRIADO" -> throw Erros.conflito(
                "PALLET_VAZIO",
                "Pallet ${codigo.trim().uppercase()} ainda não recebeu nenhuma caixa.",
                mapOf("status" to status),
            )
            "EXPEDIDO" -> throw Erros.conflito("PALLET_JA_EXPEDIDO", "Pallet já foi expedido")
            "FUNDIDO", "ESVAZIADO" -> throw Erros.conflito(
                "PALLET_ENCERRADO", "Pallet saiu de circulação",
            )
        }
    }

    fun palletParaEnderecar(codigo: String): PalletParaEnderecar = db.leitura { c ->
        val p = c.buscarUm(
            """SELECT pa.id, pa.codigo, pa.tipo, pa.status,
                      COALESCE(n.numero, nd.numero), COALESCE(n.dest_nome, nd.emitente_nome), n.bonificacao
                 FROM pallet pa
                 LEFT JOIN nota_fiscal n ON n.id = pa.nota_fiscal_id
                 LEFT JOIN nota_devolucao nd ON nd.id = pa.nota_devolucao_id
                WHERE pa.codigo = ?""",
            codigo.trim().uppercase(),
        ) {
            Quinteto(it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                it.getString(5), it.getString(6), it.getInt(7) == 1)
        } ?: throw Erros.conflito("PALLET_NAO_ENCONTRADO", "Pallet $codigo não encontrado")

        exigirEnderecavel(p.status, codigo)

        // O pallet de devolução conta as unidades conferidas, não as caixas.
        val qtd = c.buscarUm(
            if (p.tipo == "DEVOLUCAO") {
                "SELECT COALESCE(SUM(qtd_un), 0) FROM leitura_devolucao WHERE pallet_id = ? AND cancelada = 0"
            } else {
                "SELECT COUNT(*) FROM leitura WHERE pallet_id = ? AND cancelada = 0"
            },
            p.id,
        ) { it.getInt(1) } ?: 0

        val atual = c.buscarUm(
            """SELECT b.codigo, po.nivel FROM enderecamento e
                 JOIN posicao po ON po.id = e.posicao_id
                 JOIN box b ON b.id = po.box_id
                WHERE e.pallet_id = ? AND e.liberado_em IS NULL""",
            p.id,
        ) { "${it.getString(1)} nível ${it.getInt(2)}" }

        PalletParaEnderecar(
            p.codigo, p.tipo, p.status, p.notaNumero, p.cliente, qtd, atual, p.bonificacao,
        )
    }

    /**
     * Os níveis do box, com o que ocupa cada um. Azul = ocupado, branco = livre.
     *
     * Recebendo o pallet que se quer endereçar, marca também as posições
     * ocupadas que ele pode dividir — bonificação do mesmo CNPJ.
     */
    fun box(codigoBarras: String, palletCodigo: String? = null): BoxResumo = db.leitura { c ->
        val b = c.buscarUm(
            """SELECT b.id, b.codigo, d.nome, r.sigla, b.tipo
                 FROM box b JOIN deposito d ON d.id = b.deposito_id
                 JOIN rua r ON r.id = b.rua_id
                WHERE (b.codigo_barras = ? OR b.codigo = ?) AND b.ativo = 1""",
            codigoBarras.trim().uppercase(), codigoBarras.trim().uppercase(),
        ) {
            DadosBox(it.getLong(1), it.getString(2), it.getString(3), it.getString(4), it.getString(5))
        } ?: throw Erros.conflito(
            "BOX_NAO_ENCONTRADO", "Box $codigoBarras não encontrado",
            mapOf("codigo" to codigoBarras),
        )

        val candidato = palletCodigo?.let { dadosDaNotaDoPallet(c, it) }

        val posicoes = c.buscarTodos(
            // Pallet de devolução não tem NF de venda: mostra a NF de devolução
            // e conta em unidades, como a conferência dela.
            """SELECT po.id, po.nivel, pa.codigo, COALESCE(n.numero, nd.numero),
                      CASE WHEN pa.tipo = 'DEVOLUCAO'
                           THEN (SELECT COALESCE(SUM(ld.qtd_un), 0) FROM leitura_devolucao ld
                                  WHERE ld.pallet_id = pa.id AND ld.cancelada = 0)
                           ELSE (SELECT COUNT(*) FROM leitura l WHERE l.pallet_id = pa.id AND l.cancelada = 0)
                      END,
                      n.dest_cnpj, n.bonificacao, COALESCE(n.dest_nome, nd.emitente_nome), pa.tipo
                 FROM posicao po
                 LEFT JOIN enderecamento e ON e.posicao_id = po.id AND e.liberado_em IS NULL
                 LEFT JOIN pallet pa ON pa.id = e.pallet_id
                 LEFT JOIN nota_fiscal n ON n.id = pa.nota_fiscal_id
                 LEFT JOIN nota_devolucao nd ON nd.id = pa.nota_devolucao_id
                WHERE po.box_id = ? ORDER BY po.nivel DESC""",
            b.id,
        ) {
            val ocupante = it.getString(3)
            PosicaoResumo(
                id = it.getLong(1),
                nivel = it.getInt(2),
                ocupadaPor = ocupante,
                notaNumero = it.getString(4),
                qtdCaixas = it.getInt(5).takeIf { _ -> ocupante != null },
                aceitaCoabitacao = ocupante != null && candidato != null &&
                    podeCoabitar(candidato, NotaDoPallet(it.getString(6), it.getInt(7) == 1)),
                cliente = it.getString(8),
                tipoPallet = it.getString(9),
            )
        }

        BoxResumo(b.id, b.codigo, b.deposito, b.rua, b.tipo, posicoes)
    }

    fun enderecar(usuarioId: Long, palletCodigo: String, posicaoId: Long): PalletParaEnderecar =
        db.transacao { c ->
            val (palletId, status) = c.buscarUm(
                "SELECT id, status FROM pallet WHERE codigo = ?", palletCodigo.trim().uppercase(),
            ) { it.getLong(1) to it.getString(2) }
                ?: throw Erros.conflito("PALLET_NAO_ENCONTRADO", "Pallet $palletCodigo não encontrado")

            // A mesma trava da leitura do pallet, de novo aqui dentro da transação.
            // Entre o coletor ler o pallet e confirmar o nível passa tempo — o
            // operador anda até o box —, e nesse meio o pallet pode ter sido
            // expedido ou juntado em outro. Sem repetir a checagem, o UPDATE
            // abaixo trazia um pallet EXPEDIDO de volta ao estoque como
            // ENDERECADO, ocupando um nível com carga que já saiu do CD.
            exigirEnderecavel(status, palletCodigo)

            val ocupantes = c.buscarTodos(
                """SELECT pa.codigo, n.dest_cnpj, n.bonificacao, n.numero
                     FROM enderecamento e
                     JOIN pallet pa ON pa.id = e.pallet_id
                     LEFT JOIN nota_fiscal n ON n.id = pa.nota_fiscal_id
                    WHERE e.posicao_id = ? AND e.liberado_em IS NULL""",
                posicaoId,
            ) { Ocupante(it.getString(1), it.getString(2), it.getInt(3) == 1, it.getString(4)) }
                .filter { it.palletCodigo != palletCodigo.trim().uppercase() }

            if (ocupantes.isNotEmpty()) {
                val candidato = dadosDaNotaDoPallet(c, palletCodigo)
                    ?: throw posicaoOcupada(ocupantes.first())
                ocupantes.forEach { o ->
                    if (!podeCoabitar(candidato, NotaDoPallet(o.cnpj, o.bonificacao))) {
                        throw posicaoOcupada(o)
                    }
                }
            }

            val agora = Instant.now().toString()
            // Re-endereçar é permitido: libera o endereço anterior e grava o novo.
            c.atualizar(
                """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao='CORRECAO'
                    WHERE pallet_id=? AND liberado_em IS NULL""",
                agora, usuarioId, palletId,
            )
            c.inserir(
                """INSERT INTO enderecamento (pallet_id, posicao_id, enderecado_em, enderecado_por)
                   VALUES (?,?,?,?)""",
                palletId, posicaoId, agora, usuarioId,
            )
            // Pallet ainda em separação continua EM_USO: virar ENDERECADO o
            // tiraria das mãos do separador, que não conseguiria mais bipar nele.
            // Endereço e situação da separação são fatos independentes — um diz
            // onde está, o outro diz se acabou.
            c.atualizar(
                "UPDATE pallet SET status='ENDERECADO' WHERE id=? AND status <> 'EM_USO'",
                palletId,
            )
        }.let { palletParaEnderecar(palletCodigo) }

    /**
     * Roteiro de uma NF: onde está cada pallet, ordenado por câmara e rua.
     * É a lista que alguém leva na mão para buscar os pallets, então a ordem
     * segue o caminho a percorrer, não a de criação. Ver docs/TELAS-RETAGUARDA.md §8.
     */
    fun roteiroDaNota(numero: String): Roteiro = db.leitura { c ->
        val nota = c.buscarUm(
            "SELECT id, numero, dest_nome FROM nota_fiscal WHERE numero = ? ORDER BY id DESC LIMIT 1",
            numero.trim(),
        ) { Triple(it.getLong(1), it.getString(2), it.getString(3)) }
            ?: throw Erros.naoEncontrado("NF $numero")

        val linhas = c.buscarTodos(
            """SELECT pa.codigo, b.codigo, po.nivel, d.nome, r.sigla, pa.status,
                      (SELECT COUNT(*) FROM leitura l WHERE l.pallet_id = pa.id AND l.cancelada = 0)
                 FROM pallet pa
                 LEFT JOIN enderecamento e ON e.pallet_id = pa.id AND e.liberado_em IS NULL
                 LEFT JOIN posicao po ON po.id = e.posicao_id
                 LEFT JOIN box b ON b.id = po.box_id
                 LEFT JOIN deposito d ON d.id = b.deposito_id
                 LEFT JOIN rua r ON r.id = b.rua_id
                WHERE pa.nota_fiscal_id = ? AND pa.status NOT IN ('FUNDIDO','ESVAZIADO')
                ORDER BY d.nome, r.sigla, b.codigo, po.nivel""",
            nota.first,
        ) {
            LinhaRoteiro(
                palletCodigo = it.getString(1),
                box = it.getString(2),
                nivel = it.getInt(3).takeIf { _ -> it.getString(2) != null },
                deposito = it.getString(4),
                rua = it.getString(5),
                status = it.getString(6),
                qtdCaixas = it.getInt(7),
            )
        }

        Roteiro(
            notaNumero = nota.second,
            cliente = nota.third,
            qtdPallets = linhas.size,
            // O pallet sem endereço aparece em destaque: senão só se descobre
            // na hora do carregamento.
            semEndereco = linhas.count { it.box == null },
            linhas = linhas,
        )
    }

    /** Pallets encerrados aguardando endereço — a fila do coletor. */
    fun pendentesDeEnderecamento(): List<PalletParaEnderecar> = db.leitura { c ->
        c.buscarTodos(
            // Em separação também entra, desde que já tenha caixa: endereçar
            // passou a valer a qualquer momento, e a fila é onde o operador vê
            // o que ainda está no chão. Encerrados primeiro; os em uso depois,
            // marcados como tal na tela — é assim que se sabe que não acabou.
            """SELECT pa.codigo FROM pallet pa
                WHERE (pa.status = 'ENCERRADO'
                       OR (pa.status = 'EM_USO'
                           AND (EXISTS (SELECT 1 FROM leitura l
                                         WHERE l.pallet_id = pa.id AND l.cancelada = 0)
                                OR EXISTS (SELECT 1 FROM leitura_devolucao ld
                                            WHERE ld.pallet_id = pa.id AND ld.cancelada = 0))))
                  AND NOT EXISTS (SELECT 1 FROM enderecamento e
                                   WHERE e.pallet_id = pa.id AND e.liberado_em IS NULL)
                ORDER BY CASE pa.status WHEN 'ENCERRADO' THEN 0 ELSE 1 END, pa.encerrado_em""",
        ) { it.getString(1) }
    }.map { palletParaEnderecar(it) }

    /**
     * Bonificação é pouca caixa e vai no mesmo pallet da venda. Só coabitam se
     * ao menos uma das notas for bonificação **e** o CNPJ do destinatário for o
     * mesmo — juntar clientes diferentes no mesmo endereço seria exatamente o
     * erro que o sistema existe para evitar.
     */
    private fun podeCoabitar(candidato: NotaDoPallet, ocupante: NotaDoPallet): Boolean {
        if (!candidato.bonificacao && !ocupante.bonificacao) return false
        val a = candidato.cnpj?.filter(Char::isDigit)
        val b = ocupante.cnpj?.filter(Char::isDigit)
        return !a.isNullOrBlank() && a == b
    }

    private fun dadosDaNotaDoPallet(c: Connection, palletCodigo: String): NotaDoPallet? = c.buscarUm(
        """SELECT n.dest_cnpj, n.bonificacao FROM pallet pa
             JOIN nota_fiscal n ON n.id = pa.nota_fiscal_id
            WHERE pa.codigo = ?""",
        palletCodigo.trim().uppercase(),
    ) { NotaDoPallet(it.getString(1), it.getInt(2) == 1) }

    private fun posicaoOcupada(o: Ocupante) = Erros.conflito(
        "POSICAO_OCUPADA",
        "Posição ocupada pelo pallet " + o.palletCodigo +
            (o.notaNumero?.let { " (NF " + it + ")" } ?: "") +
            ". Só dá para dividir com bonificação do mesmo CNPJ.",
        mapOf("ocupadaPor" to o.palletCodigo, "notaNumero" to o.notaNumero),
    )

    private data class NotaDoPallet(val cnpj: String?, val bonificacao: Boolean)

    private data class Ocupante(
        val palletCodigo: String, val cnpj: String?,
        val bonificacao: Boolean, val notaNumero: String?,
    )

    private data class DadosBox(
        val id: Long, val codigo: String, val deposito: String,
        val rua: String, val tipo: String,
    )

    private data class Quinteto(
        val id: Long, val codigo: String, val tipo: String, val status: String,
        val notaNumero: String?, val cliente: String?, val bonificacao: Boolean = false,
    )
}
