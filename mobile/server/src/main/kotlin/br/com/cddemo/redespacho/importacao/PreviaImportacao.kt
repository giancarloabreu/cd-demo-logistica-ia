package br.com.cddemo.redespacho.importacao

import br.com.cddemo.redespacho.dominio.Embalagem
import br.com.cddemo.redespacho.dominio.Embalagens
import br.com.cddemo.redespacho.dominio.Gtin
import br.com.cddemo.redespacho.dominio.MotivoGtin
import br.com.cddemo.redespacho.dominio.ResultadoGtin
import java.math.BigDecimal

/**
 * Prévia da importação de XML: tudo que a tela precisa mostrar ao operador
 * antes de gravar qualquer coisa. Ver docs/TELAS-RETAGUARDA.md §1.1.
 */
sealed interface PreviaArquivo {
    val arquivo: String

    data class Ok(
        override val arquivo: String,
        val nota: NotaFiscalXml,
        val itens: List<PreviaItem>,
        val avisos: List<Aviso>,
    ) : PreviaArquivo {
        val qtdCaixas: BigDecimal get() = nota.somaCaixas
        val qVolConfere: Boolean
            get() = nota.qtdVolumes != null &&
                BigDecimal(nota.qtdVolumes).compareTo(nota.somaCaixas) == 0
    }

    data class Rejeitado(
        override val arquivo: String,
        val codigo: String,
        val mensagem: String,
    ) : PreviaArquivo
}

data class PreviaItem(
    val item: ItemXml,
    /** GTIN-14 da caixa, pronto para virar código de barras do produto. */
    val gtinCaixa: String?,
    val embalagem: Embalagem?,
)

data class Aviso(val codigo: String, val mensagem: String)

object Importador {

    fun analisar(arquivo: String, bytes: ByteArray): PreviaArquivo {
        val nota = try {
            NfeParser.parse(bytes)
        } catch (e: NfeInvalidoException) {
            return PreviaArquivo.Rejeitado(arquivo, e.codigo, e.message.orEmpty())
        }

        val avisos = mutableListOf<Aviso>()

        // Validação cruzada: a soma das quantidades tem de bater com o total de
        // volumes declarado. Avisa, mas não bloqueia — erro de emissão não pode
        // travar a operação.
        val qVol = nota.qtdVolumes
        if (qVol == null) {
            avisos += Aviso("QVOL_AUSENTE", "NF-e não declara total de volumes")
        } else if (BigDecimal(qVol).compareTo(nota.somaCaixas) != 0) {
            avisos += Aviso(
                "QVOL_DIVERGENTE",
                "Soma dos itens é ${nota.somaCaixas.toPlainString()} caixas, " +
                    "mas a NF declara $qVol volumes",
            )
        }

        val itens = nota.itens.map { item ->
            if (!item.uCom.equals("CX", ignoreCase = true)) {
                avisos += Aviso(
                    "UNIDADE_NAO_CX",
                    "Item ${item.nItem} (${item.cProd}) está em ${item.uCom}, não em CX",
                )
            }

            val gtin = when (val r = Gtin.normalizar(item.cEan)) {
                is ResultadoGtin.Ok -> r.gtin14
                is ResultadoGtin.Invalido -> {
                    if (r.motivo != MotivoGtin.VAZIO) {
                        avisos += Aviso(
                            "GTIN_INVALIDO",
                            "Item ${item.nItem} (${item.cProd}) tem cEAN inválido " +
                                "(${r.motivo}): ${item.cEan}",
                        )
                    } else {
                        avisos += Aviso(
                            "SEM_GTIN",
                            "Item ${item.nItem} (${item.cProd}) sem cEAN — " +
                                "produto entra sem código de barras",
                        )
                    }
                    null
                }
            }

            val embalagem = Embalagens.extrair(item.xProd, item.qCom, item.qTrib, item.uTrib)
            if (embalagem == null) {
                avisos += Aviso(
                    "EMBALAGEM_NAO_DETERMINADA",
                    "Item ${item.nItem} (${item.cProd}): unidades por caixa não " +
                        "puderam ser confirmadas — cadastrar à mão",
                )
            }

            PreviaItem(item, gtin, embalagem)
        }

        // Um código de barras jamais pode apontar para dois produtos.
        itens.filter { it.gtinCaixa != null }
            .groupBy { it.gtinCaixa }
            .filter { it.value.size > 1 }
            .forEach { (gtin, dup) ->
                avisos += Aviso(
                    "GTIN_DUPLICADO",
                    "GTIN $gtin aparece em mais de um item: " +
                        dup.joinToString { it.item.cProd },
                )
            }

        return PreviaArquivo.Ok(arquivo, nota, itens, avisos)
    }
}
