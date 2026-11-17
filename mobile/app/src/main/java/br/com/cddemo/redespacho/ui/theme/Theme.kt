package br.com.cddemo.redespacho.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * Esquema único, sempre claro e sem cor dinâmica.
 *
 * O template vinha com `dynamicColor = true`: no Android 12+ o app pegava a cor
 * do papel de parede e, abaixo disso, ficava roxo. Numa frota com Android 8 a
 * 14 isso dava um coletor de cada cor. Tema escuro também fica de fora: na
 * câmara escura, com a tela embaçada, o que se lê de relance é tinta escura
 * sobre fundo claro.
 */
private val EsquemaCamara = lightColorScheme(
    primary = Verde,
    onPrimary = Color.White,
    primaryContainer = VerdeClaro,
    onPrimaryContainer = Verde,
    inversePrimary = VerdeBorda,
    secondary = Tinta2,
    onSecondary = Color.White,
    secondaryContainer = GeloVariante,
    onSecondaryContainer = Tinta,
    tertiary = Ambar,
    onTertiary = Color.White,
    tertiaryContainer = AmbarClaro,
    onTertiaryContainer = Color(0xFF5C4000),
    error = Vermelho,
    onError = Color.White,
    errorContainer = VermelhoClaro,
    onErrorContainer = Color(0xFF410E0B),
    background = TelaSolida,
    onBackground = Tinta,
    surface = TelaSolida,
    onSurface = Tinta,
    surfaceVariant = Color(0xFFE3EBED),
    onSurfaceVariant = Tinta2,
    surfaceTint = Verde,
    outline = BordaDigitar,
    outlineVariant = Linha,
    // Cartões e diálogos em papel branco sobre o fundo quase branco; a borda
    // de cada cartão faz a separação que a sombra faria.
    surfaceContainerLowest = Papel,
    surfaceContainerLow = Papel,
    surfaceContainer = Papel,
    surfaceContainerHigh = Papel,
    surfaceContainerHighest = Papel,
    surfaceBright = Papel,
    surfaceDim = Gelo,
)

/**
 * Formas da prévia: botões pílula (padrão do Material 3), cartões com raio de
 * 22 dp, diálogos com 28 dp.
 */
private val FormasCamara = Shapes(
    extraSmall = RoundedCornerShape(10.dp),
    small = RoundedCornerShape(14.dp),
    medium = RoundedCornerShape(22.dp),
    large = RoundedCornerShape(26.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

@Composable
fun RedespachoTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = EsquemaCamara,
        typography = Typography,
        shapes = FormasCamara,
        content = content,
    )
}
