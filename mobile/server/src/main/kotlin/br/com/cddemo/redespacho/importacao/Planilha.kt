package br.com.cddemo.redespacho.importacao

import org.w3c.dom.Element
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.math.BigDecimal
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.Charset
import java.nio.charset.CodingErrorAction
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream
import javax.xml.parsers.DocumentBuilderFactory

class PlanilhaInvalidaException(message: String) : Exception(message)

/** Uma linha da planilha, com o número que ela tem no Excel (1 = cabeçalho). */
data class LinhaPlanilha(val numero: Int, val celulas: List<String>)

/**
 * Planilha sem biblioteca: escreve um .xlsx de uma aba e lê .xlsx ou CSV.
 *
 * É o que o cadastro em lote precisa — baixar o modelo, preencher no Excel,
 * subir — sem levar ao pacote um jar do tamanho do servidor. Tudo é texto: o
 * número vira o texto que aparece na célula ("6", não "6.0").
 */
object Planilha {

    private const val NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    private const val NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    private const val LIMITE_ENTRADA = 30L * 1024 * 1024

    // ----------------------------------------------------------------- escrita

    /**
     * Uma aba, cabeçalho em negrito e congelado. As colunas saem em formato
     * texto: o que se digitar nelas depois continua texto — sem o Excel comer
     * zero à esquerda de código.
     */
    fun xlsx(aba: String, cabecalho: List<String>, linhas: List<List<String>>, larguras: List<Int> = emptyList()): ByteArray {
        val saida = ByteArrayOutputStream()
        ZipOutputStream(saida).use { zip ->
            fun arquivo(nome: String, conteudo: String) {
                zip.putNextEntry(ZipEntry(nome))
                zip.write(conteudo.toByteArray(Charsets.UTF_8))
                zip.closeEntry()
            }
            arquivo("[Content_Types].xml", TIPOS)
            arquivo("_rels/.rels", RELS)
            arquivo(
                "xl/workbook.xml",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="$NS" xmlns:r="$NS_REL"><sheets><sheet name="${escapar(aba.take(31))}" sheetId="1" r:id="rId1"/></sheets></workbook>""",
            )
            arquivo("xl/_rels/workbook.xml.rels", RELS_WORKBOOK)
            arquivo("xl/styles.xml", ESTILOS)
            arquivo("xl/worksheets/sheet1.xml", aba(cabecalho, linhas, larguras))
        }
        return saida.toByteArray()
    }

    private fun aba(cabecalho: List<String>, linhas: List<List<String>>, larguras: List<Int>): String = buildString {
        append("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>""")
        append("""<worksheet xmlns="$NS"><sheetViews><sheetView workbookViewId="0">""")
        append("""<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>""")
        append("<cols>")
        cabecalho.indices.forEach { i ->
            val largura = larguras.getOrNull(i) ?: maxOf(12, cabecalho[i].length + 2)
            append("""<col min="${i + 1}" max="${i + 1}" width="$largura" style="2" customWidth="1"/>""")
        }
        append("</cols><sheetData>")
        (listOf(cabecalho) + linhas).forEachIndexed { r, linha ->
            append("""<row r="${r + 1}">""")
            linha.forEachIndexed { i, valor ->
                val estilo = if (r == 0) 1 else 2
                append("""<c r="${coluna(i)}${r + 1}" t="inlineStr" s="$estilo"><is><t xml:space="preserve">""")
                append(escapar(valor))
                append("</t></is></c>")
            }
            append("</row>")
        }
        append("</sheetData></worksheet>")
    }

    /** 0 → A, 25 → Z, 26 → AA. */
    private fun coluna(indice: Int): String {
        var n = indice + 1
        val sb = StringBuilder()
        while (n > 0) {
            val resto = (n - 1) % 26
            sb.insert(0, 'A' + resto)
            n = (n - 1) / 26
        }
        return sb.toString()
    }

    private fun escapar(s: String): String = buildString {
        s.forEach { ch ->
            when {
                ch == '&' -> append("&amp;")
                ch == '<' -> append("&lt;")
                ch == '>' -> append("&gt;")
                ch == '"' -> append("&quot;")
                // Controle não cabe em XML 1.0 e deixaria o arquivo ilegível.
                ch < ' ' && ch != '\t' && ch != '\n' && ch != '\r' -> Unit
                else -> append(ch)
            }
        }
    }

    // ----------------------------------------------------------------- leitura

    /** .xlsx (reconhecido pela assinatura de ZIP) ou CSV com `;`, `,` ou tabulação. */
    fun ler(bytes: ByteArray): List<LinhaPlanilha> {
        if (bytes.isEmpty()) throw PlanilhaInvalidaException("Arquivo vazio")
        val ehZip = bytes.size > 4 && bytes[0] == 'P'.code.toByte() && bytes[1] == 'K'.code.toByte()
        return if (ehZip) lerXlsx(bytes) else lerCsv(bytes)
    }

    private fun lerXlsx(bytes: ByteArray): List<LinhaPlanilha> {
        val partes = mutableMapOf<String, ByteArray>()
        var total = 0L
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            generateSequence { zip.nextEntry }.forEach { e ->
                if (e.isDirectory) return@forEach
                val nome = e.name.removePrefix("/")
                // Só o que interessa, e com teto: ZIP que se expande a gigas
                // derrubaria o servidor.
                if (nome == "xl/workbook.xml" || nome == "xl/_rels/workbook.xml.rels" ||
                    nome == "xl/sharedStrings.xml" || nome.startsWith("xl/worksheets/")
                ) {
                    val conteudo = lerLimitado(zip)
                    total += conteudo.size
                    if (total > LIMITE_ENTRADA) throw PlanilhaInvalidaException("Planilha grande demais")
                    partes[nome] = conteudo
                }
            }
        }
        val caminho = primeiraAba(partes)
        val aba = partes[caminho] ?: partes.keys.filter { it.startsWith("xl/worksheets/sheet") }.minOrNull()?.let { partes[it] }
            ?: throw PlanilhaInvalidaException("A planilha não tem aba de dados (é mesmo um .xlsx?)")
        val compartilhadas = partes["xl/sharedStrings.xml"]?.let { textosCompartilhados(it) }.orEmpty()

        val doc = xml(aba)
        val linhas = mutableListOf<LinhaPlanilha>()
        val rows = doc.getElementsByTagNameNS(NS, "row")
        var numeroAnterior = 0
        for (i in 0 until rows.length) {
            val row = rows.item(i) as Element
            val numero = row.getAttribute("r").toIntOrNull() ?: (numeroAnterior + 1)
            numeroAnterior = numero
            val celulas = mutableMapOf<Int, String>()
            var proxima = 0
            val cs = row.getElementsByTagNameNS(NS, "c")
            for (j in 0 until cs.length) {
                val c = cs.item(j) as Element
                val ref = c.getAttribute("r")
                val col = if (ref.isNotEmpty()) indiceDaColuna(ref) else proxima
                proxima = col + 1
                celulas[col] = valorDaCelula(c, compartilhadas)
            }
            if (celulas.isEmpty()) continue
            val largura = (celulas.keys.maxOrNull() ?: -1) + 1
            linhas += LinhaPlanilha(numero, (0 until largura).map { celulas[it].orEmpty().trim() })
        }
        return linhas
    }

    private fun lerLimitado(zip: ZipInputStream): ByteArray {
        val saida = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        var lidos = 0L
        while (true) {
            val n = zip.read(buffer)
            if (n < 0) break
            lidos += n
            if (lidos > LIMITE_ENTRADA) throw PlanilhaInvalidaException("Planilha grande demais")
            saida.write(buffer, 0, n)
        }
        return saida.toByteArray()
    }

    /** O caminho da primeira aba, pelo workbook e suas relações. */
    private fun primeiraAba(partes: Map<String, ByteArray>): String {
        val padrao = "xl/worksheets/sheet1.xml"
        val workbook = partes["xl/workbook.xml"] ?: return padrao
        val rels = partes["xl/_rels/workbook.xml.rels"] ?: return padrao
        val sheet = xml(workbook).getElementsByTagNameNS(NS, "sheet").item(0) as? Element ?: return padrao
        val id = sheet.getAttributeNS(NS_REL, "id")
        val relacoes = xml(rels).getElementsByTagName("Relationship")
        for (i in 0 until relacoes.length) {
            val r = relacoes.item(i) as Element
            if (r.getAttribute("Id") == id) {
                val alvo = r.getAttribute("Target")
                return if (alvo.startsWith("/")) alvo.removePrefix("/") else "xl/$alvo"
            }
        }
        return padrao
    }

    private fun textosCompartilhados(bytes: ByteArray): List<String> {
        val sis = xml(bytes).getElementsByTagNameNS(NS, "si")
        return (0 until sis.length).map { i -> textoRico(sis.item(i) as Element) }
    }

    /** Texto de <si> ou <is>, inclusive com formatação por trecho; ignora a fonética. */
    private fun textoRico(e: Element): String {
        val ts = e.getElementsByTagNameNS(NS, "t")
        return (0 until ts.length).map { ts.item(it) as Element }
            .filter { (it.parentNode as? Element)?.localName != "rPh" }
            .joinToString("") { it.textContent }
    }

    private fun valorDaCelula(c: Element, compartilhadas: List<String>): String {
        val v = (c.getElementsByTagNameNS(NS, "v").item(0) as? Element)?.textContent.orEmpty()
        return when (c.getAttribute("t")) {
            "s" -> v.toIntOrNull()?.let { compartilhadas.getOrNull(it) }.orEmpty()
            "inlineStr" -> (c.getElementsByTagNameNS(NS, "is").item(0) as? Element)?.let { textoRico(it) }.orEmpty()
            "str" -> v
            "b" -> if (v == "1") "VERDADEIRO" else "FALSO"
            "e" -> ""
            else -> numero(v)
        }
    }

    /** "6" e não "6.0"; "1E3" vira "1000". */
    private fun numero(v: String): String =
        if (v.isEmpty()) "" else runCatching { BigDecimal(v).stripTrailingZeros().toPlainString() }.getOrDefault(v)

    /** "B7" → 1. */
    private fun indiceDaColuna(ref: String): Int {
        var n = 0
        ref.takeWhile { it.isLetter() }.uppercase().forEach { n = n * 26 + (it - 'A' + 1) }
        return n - 1
    }

    private fun xml(bytes: ByteArray) = try {
        DocumentBuilderFactory.newInstance().apply {
            isNamespaceAware = true
            setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
            isXIncludeAware = false
            setAttribute(javax.xml.XMLConstants.ACCESS_EXTERNAL_DTD, "")
            setAttribute(javax.xml.XMLConstants.ACCESS_EXTERNAL_SCHEMA, "")
        }.newDocumentBuilder().parse(ByteArrayInputStream(bytes))
    } catch (e: Exception) {
        throw PlanilhaInvalidaException("Não consegui ler a planilha: ${e.message}")
    }

    // --------------------------------------------------------------------- CSV

    private fun lerCsv(bytes: ByteArray): List<LinhaPlanilha> {
        val texto = decodificar(bytes).removePrefix("﻿")
        val primeira = texto.lineSequence().firstOrNull { it.isNotBlank() }.orEmpty()
        val separador = listOf(';', ',', '\t').maxByOrNull { s -> primeira.count { it == s } } ?: ';'

        val linhas = mutableListOf<LinhaPlanilha>()
        var numero = 1
        var celula = StringBuilder()
        var atual = mutableListOf<String>()
        var aspas = false
        var i = 0
        fun fecharLinha() {
            atual += celula.toString().trim()
            celula = StringBuilder()
            if (atual.any { it.isNotEmpty() }) linhas += LinhaPlanilha(numero, atual)
            atual = mutableListOf()
            numero++
        }
        while (i < texto.length) {
            val ch = texto[i]
            when {
                aspas && ch == '"' && i + 1 < texto.length && texto[i + 1] == '"' -> { celula.append('"'); i++ }
                ch == '"' -> aspas = !aspas
                !aspas && ch == separador -> { atual += celula.toString().trim(); celula = StringBuilder() }
                !aspas && ch == '\r' -> Unit
                !aspas && ch == '\n' -> fecharLinha()
                else -> celula.append(ch)
            }
            i++
        }
        if (celula.isNotEmpty() || atual.isNotEmpty()) fecharLinha()
        return linhas
    }

    /** UTF-8 quando é UTF-8 válido; senão o ANSI do Excel em português. */
    private fun decodificar(bytes: ByteArray): String = try {
        Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString()
    } catch (e: CharacterCodingException) {
        String(bytes, Charset.forName("windows-1252"))
    }

    // ----------------------------------------------------------- partes fixas

    private const val TIPOS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>"""

    private const val RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"""

    private const val RELS_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""

    // Estilo 1: cabeçalho em negrito; 1 e 2 em formato texto (@).
    private const val ESTILOS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="49" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>"""
}
