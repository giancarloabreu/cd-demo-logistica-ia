package br.com.cddemo.redespacho.importacao

import org.w3c.dom.Element
import org.w3c.dom.Node
import java.io.ByteArrayInputStream
import java.math.BigDecimal
import javax.xml.parsers.DocumentBuilderFactory

private const val NS_NFE = "http://www.portalfiscal.inf.br/nfe"

data class ItemXml(
    val nItem: Int,
    val cProd: String,
    val xProd: String,
    val cEan: String?,
    val cEanTrib: String?,
    val uCom: String,
    val qCom: BigDecimal,
    val uTrib: String?,
    val qTrib: BigDecimal,
    val cfop: String?,
)

data class NotaFiscalXml(
    val chave: String,
    val numero: String,
    val serie: String,
    val emissao: String,
    val emitCnpj: String?,
    val emitNome: String?,
    val destCnpj: String?,
    val destNome: String,
    val destLogradouro: String?,
    val destNumero: String?,
    val destComplemento: String?,
    val destBairro: String?,
    val destMunicipio: String?,
    val destUf: String?,
    val destCep: String?,
    val qtdVolumes: Int?,
    val natOp: String?,
    val itens: List<ItemXml>,
    /** Chaves das NF-e referenciadas (ide/NFref/refNFe): na devolução, a de venda. */
    val referencias: List<String> = emptyList(),
    /** Endereço do emitente (enderEmit): na devolução parcial, o do cliente. */
    val emitBairro: String? = null,
    val emitMunicipio: String? = null,
    val emitUf: String? = null,
) {
    val somaCaixas: BigDecimal get() = itens.fold(BigDecimal.ZERO) { a, i -> a + i.qCom }

    /**
     * Bonificação: CFOP 5910/6910 ("remessa em bonificação, doação ou brinde")
     * ou a natureza da operação dizendo isso em texto. Basta um item — nota
     * mista continua sendo tratada como bonificação, que é o caso conservador.
     *
     * O texto é abreviado pelo emissor ("REMESSA EM BONIF  DOACAO OU BRINDE"),
     * então o teste é por "BONIF" e não por "BONIFICAÇÃO".
     */
    val bonificacao: Boolean
        get() = itens.any { it.cfop in CFOPS_BONIFICACAO } ||
            natOp?.uppercase()?.contains("BONIF") == true

    companion object {
        val CFOPS_BONIFICACAO = setOf("5910", "6910", "5911", "6911")
    }
}

class NfeInvalidoException(val codigo: String, message: String) : Exception(message)

/**
 * Leitor de NF-e 4.00 (`nfeProc`). Puro: não toca banco, para poder ser
 * testado contra os XMLs reais em `src/test/resources/nfe`.
 */
object NfeParser {

    fun parse(bytes: ByteArray): NotaFiscalXml {
        val doc = try {
            DocumentBuilderFactory.newInstance().apply {
                isNamespaceAware = true
                setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
                isXIncludeAware = false
                setAttribute(javax.xml.XMLConstants.ACCESS_EXTERNAL_DTD, "")
                setAttribute(javax.xml.XMLConstants.ACCESS_EXTERNAL_SCHEMA, "")
            }.newDocumentBuilder().parse(ByteArrayInputStream(bytes))
        } catch (e: Exception) {
            throw NfeInvalidoException("XML_INVALIDO", "Arquivo não é um XML válido: ${e.message}")
        }

        val infNFe = doc.getElementsByTagNameNS(NS_NFE, "infNFe").item(0) as? Element
            ?: throw NfeInvalidoException("XML_INVALIDO", "Não é uma NF-e (elemento infNFe ausente)")

        val versao = infNFe.getAttribute("versao")
        if (!versao.startsWith("4.")) {
            throw NfeInvalidoException("XML_INVALIDO", "Versão de NF-e não suportada: $versao")
        }

        val chave = infNFe.getAttribute("Id").removePrefix("NFe")
        if (chave.length != 44) {
            throw NfeInvalidoException("XML_INVALIDO", "Chave de acesso inválida: $chave")
        }

        val ide = infNFe.filho("ide")
            ?: throw NfeInvalidoException("XML_INVALIDO", "Bloco ide ausente")
        val emit = infNFe.filho("emit")
        val dest = infNFe.filho("dest")
            ?: throw NfeInvalidoException("XML_INVALIDO", "Bloco dest ausente")
        val ender = dest.filho("enderDest")

        val itens = infNFe.filhos("det").map { det ->
            val prod = det.filho("prod")
                ?: throw NfeInvalidoException("XML_INVALIDO", "Item sem bloco prod")
            ItemXml(
                nItem = det.getAttribute("nItem").toIntOrNull()
                    ?: throw NfeInvalidoException("XML_INVALIDO", "Item sem nItem"),
                cProd = prod.texto("cProd").orEmpty(),
                xProd = prod.texto("xProd").orEmpty(),
                cEan = prod.texto("cEAN")?.takeIf { it.isNotBlank() && !it.equals("SEM GTIN", true) },
                cEanTrib = prod.texto("cEANTrib")?.takeIf { it.isNotBlank() },
                uCom = prod.texto("uCom").orEmpty(),
                qCom = prod.texto("qCom")?.toBigDecimalOrNull() ?: BigDecimal.ZERO,
                uTrib = prod.texto("uTrib"),
                qTrib = prod.texto("qTrib")?.toBigDecimalOrNull() ?: BigDecimal.ZERO,
                cfop = prod.texto("CFOP"),
            )
        }

        if (itens.isEmpty()) {
            throw NfeInvalidoException("XML_INVALIDO", "NF-e sem itens")
        }

        return NotaFiscalXml(
            chave = chave,
            numero = ide.texto("nNF").orEmpty(),
            serie = ide.texto("serie").orEmpty(),
            emissao = ide.texto("dhEmi") ?: ide.texto("dEmi").orEmpty(),
            emitCnpj = emit?.texto("CNPJ"),
            emitNome = emit?.texto("xNome"),
            destCnpj = dest.texto("CNPJ") ?: dest.texto("CPF"),
            destNome = dest.texto("xNome").orEmpty(),
            destLogradouro = ender?.texto("xLgr"),
            destNumero = ender?.texto("nro"),
            destComplemento = ender?.texto("xCpl"),
            destBairro = ender?.texto("xBairro"),
            destMunicipio = ender?.texto("xMun"),
            destUf = ender?.texto("UF"),
            destCep = ender?.texto("CEP"),
            qtdVolumes = infNFe.filho("transp")?.filho("vol")?.texto("qVol")?.toIntOrNull(),
            natOp = ide.texto("natOp"),
            itens = itens,
            referencias = ide.filhos("NFref").mapNotNull { ref -> ref.texto("refNFe")?.takeIf { it.length == 44 } },
            emitBairro = emit?.filho("enderEmit")?.texto("xBairro"),
            emitMunicipio = emit?.filho("enderEmit")?.texto("xMun"),
            emitUf = emit?.filho("enderEmit")?.texto("UF"),
        )
    }
}

// --- navegação no DOM, restrita a filhos diretos -----------------------------
// getElementsByTagNameNS varre a árvore inteira e traria, por exemplo, o CNPJ
// do autXML ou do transportador ao procurar o do destinatário.

private fun Element.filhos(nome: String): List<Element> =
    (0 until childNodes.length)
        .mapNotNull { childNodes.item(it) as? Element }
        .filter { it.nodeType == Node.ELEMENT_NODE && it.localName == nome && it.namespaceURI == NS_NFE }

private fun Element.filho(nome: String): Element? = filhos(nome).firstOrNull()

private fun Element.texto(nome: String): String? = filho(nome)?.textContent?.trim()
