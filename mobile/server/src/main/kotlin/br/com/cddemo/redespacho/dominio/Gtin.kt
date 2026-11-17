package br.com.cddemo.redespacho.dominio

/**
 * Normalização de código de barras lido no coletor ou vindo de XML.
 *
 * As caixas usam Code 128 simples com os 14 dígitos do GTIN. A normalização
 * existe para que uma eventual mudança de etiqueta no fornecedor — GS1-128 com
 * AI (01), lote e validade — não quebre a operação. Ver docs/MODELO-DE-DADOS.md §5.1.
 */
object Gtin {

    private val SEPARADORES = Regex("[()\\s\\u001D]")

    /**
     * Valida o dígito verificador de qualquer GTIN (8, 12, 13 ou 14 dígitos).
     * Pesos 3 e 1 alternados a partir do dígito de dados mais à direita.
     */
    fun digitoVerificadorValido(gtin: String): Boolean {
        if (gtin.length < 8 || !gtin.all { it.isDigit() }) return false
        val soma = gtin.dropLast(1).reversed()
            .mapIndexed { i, c -> (c - '0') * if (i % 2 == 0) 3 else 1 }
            .sum()
        return (10 - soma % 10) % 10 == gtin.last() - '0'
    }

    fun normalizar(bruto: String?): ResultadoGtin {
        val limpo = bruto?.replace(SEPARADORES, "").orEmpty()
        if (limpo.isEmpty()) return ResultadoGtin.Invalido(MotivoGtin.VAZIO, bruto.orEmpty())

        // GS1-128: AI (01) seguido do GTIN-14. O que vier depois é lote,
        // validade ou outro AI — descartado, pois varia entre caixas.
        // A extração vem ANTES da checagem de dígitos: lote é alfanumérico
        // (ex.: "L2340") e reprovaria a string inteira.
        val candidato = when {
            limpo.length >= 16 && limpo.startsWith("01") -> limpo.substring(2, 16)
            else -> limpo
        }

        if (!candidato.all { it.isDigit() }) {
            return ResultadoGtin.Invalido(MotivoGtin.FORMATO, bruto.orEmpty())
        }

        return when {
            candidato.length == 14 && digitoVerificadorValido(candidato) ->
                ResultadoGtin.Ok(candidato)

            candidato.length == 14 ->
                ResultadoGtin.Invalido(MotivoGtin.DIGITO_VERIFICADOR, bruto.orEmpty())

            // 13 dígitos é o GTIN da unidade (cEANTrib), não o da caixa.
            candidato.length == 13 ->
                ResultadoGtin.Invalido(MotivoGtin.GTIN_DA_UNIDADE, bruto.orEmpty())

            else -> ResultadoGtin.Invalido(MotivoGtin.FORMATO, bruto.orEmpty())
        }
    }
}

sealed interface ResultadoGtin {
    data class Ok(val gtin14: String) : ResultadoGtin
    data class Invalido(val motivo: MotivoGtin, val bruto: String) : ResultadoGtin
}

enum class MotivoGtin { VAZIO, FORMATO, DIGITO_VERIFICADOR, GTIN_DA_UNIDADE }
