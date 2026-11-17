package br.com.cddemo.redespacho.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * Roboto do sistema, sem fonte baixável: a Wi-Fi dos coletores não tem
 * internet, e uma fonte que não chega vira texto em fonte trocada no meio do
 * turno. Os pesos ExtraBold e Black saem da Roboto que já vem no Android.
 */
val Typography = Typography(
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 24.sp,
        letterSpacing = 0.5.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 15.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.1.sp,
    ),
)

/**
 * Números tabulares: com dígitos de largura igual, o contador não "anda" para
 * o lado a cada bipe — de 9 para 10 o número cresce, mas não treme.
 */
const val NUMEROS_TABULARES = "tnum"
