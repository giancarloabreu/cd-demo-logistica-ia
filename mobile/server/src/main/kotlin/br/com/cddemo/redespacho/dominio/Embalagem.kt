package br.com.cddemo.redespacho.dominio

import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Unidades por caixa, necessária para a conferência de devolução — que pode
 * vir de uma lata avulsa, enquanto a separação só conta caixa fechada.
 *
 * O dado sai da descrição no padrão `24X420G`, mas descrição é texto livre e
 * não se confia nela sozinha: o valor é conferido contra `qTrib / qCom`, que
 * dá o peso real por caixa. Ver docs/MODELO-DE-DADOS.md §5.
 */
data class Embalagem(
    val unidadesPorCaixa: Int,
    val gramasPorUnidade: Int,
)

object Embalagens {

    private val PADRAO = Regex("""(\d{1,3})\s*[Xx]\s*(\d{2,5})\s*[Gg]""")

    /** Tolerância de 10 g por caixa, para absorver arredondamento do emissor. */
    private val TOLERANCIA_KG = BigDecimal("0.010")

    /**
     * Extrai e valida. Devolve `null` quando a descrição não traz o padrão ou
     * quando o peso declarado não confere — nesses casos o produto entra sem a
     * informação e a retaguarda pede o valor à mão, em vez de arriscar contar
     * unidade errada.
     */
    fun extrair(
        descricao: String,
        qCom: BigDecimal,
        qTrib: BigDecimal,
        uTrib: String?,
    ): Embalagem? {
        val m = PADRAO.find(descricao) ?: return null
        val unidades = m.groupValues[1].toInt()
        val gramas = m.groupValues[2].toInt()
        if (unidades <= 0 || gramas <= 0) return null

        // Sem peso em KG não há como conferir; não aceitamos o valor sem prova.
        if (!uTrib.equals("KG", ignoreCase = true)) return null
        if (qCom <= BigDecimal.ZERO) return null

        val kgPorCaixaReal = qTrib.divide(qCom, 6, RoundingMode.HALF_UP)
        val kgPorCaixaCalc = BigDecimal(unidades * gramas).divide(BigDecimal(1000))

        return if ((kgPorCaixaReal - kgPorCaixaCalc).abs() <= TOLERANCIA_KG) {
            Embalagem(unidades, gramas)
        } else {
            null
        }
    }
}
