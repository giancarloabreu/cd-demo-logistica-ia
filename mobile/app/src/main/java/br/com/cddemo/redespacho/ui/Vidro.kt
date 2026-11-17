package br.com.cddemo.redespacho.ui

import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ClipOp
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.addOutline
import androidx.compose.ui.graphics.drawOutline
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.unit.dp
import br.com.cddemo.redespacho.ui.theme.BordaDigitar
import br.com.cddemo.redespacho.ui.theme.GeloCamara
import br.com.cddemo.redespacho.ui.theme.LuzAmbar
import br.com.cddemo.redespacho.ui.theme.LuzGelo
import br.com.cddemo.redespacho.ui.theme.LuzVerde
import br.com.cddemo.redespacho.ui.theme.Sombra
import br.com.cddemo.redespacho.ui.theme.Verde
import kotlin.math.abs
import kotlin.math.max

/**
 * Vidro só no menu e no login — as telas que se olham, não as que se bipam.
 *
 * Do Android 12 (API 31) em diante o sistema desfoca com RenderEffect, e o
 * cartão pode ser bem translúcido (alfa 0,42). Do 8 ao 11 não há desfoque
 * barato: imitar pesaria no processador e na bateria do coletor, então o
 * cartão fica quase opaco (0,90) com o mesmo desenho. É o que a prévia aprovada
 * mostra lado a lado.
 */
val VIDRO_REAL: Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S

val ALFA_VIDRO: Float = if (VIDRO_REAL) 0.42f else 0.90f

/**
 * Fundo de câmara fria: três luzes (gelo, verde, âmbar) sobre gelo.
 *
 * Gradientes radiais, não imagem: saem macios em qualquer versão, não pesam no
 * APK e não dependem de nada baixado. No 12+ a camada das luzes ainda passa por
 * um desfoque; abaixo disso `Modifier.blur` não faz nada, e o radial já basta.
 */
@Composable
fun FundoCamara(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize().background(GeloCamara)) {
        Box(
            Modifier
                .fillMaxSize()
                .then(if (VIDRO_REAL) Modifier.blur(28.dp) else Modifier)
                .drawBehind {
                    val w = size.width
                    val h = size.height
                    // Posições tiradas da prévia (tela de 320 × 533 dp), em fração.
                    luz(LuzGelo, Offset(0.14f * w, 0.29f * h), 0.62f * w)
                    luz(LuzVerde, Offset(0.875f * w, 0.545f * h), 0.56f * w)
                    luz(LuzAmbar, Offset(0.44f * w, 0.80f * h), 0.58f * w)
                },
        )
    }
}

private fun DrawScope.luz(cor: Color, centro: Offset, raio: Float) {
    drawCircle(
        brush = Brush.radialGradient(
            0f to cor.copy(alpha = 0.9f),
            0.5f to cor.copy(alpha = 0.55f),
            1f to cor.copy(alpha = 0f),
            center = centro,
            radius = raio,
        ),
        radius = raio,
        center = centro,
    )
}

/**
 * Superfície de vidro: branco translúcido, borda clara, brilho no topo e uma
 * sombra suave tingida de azul-petróleo.
 *
 * A sombra é desenhada à mão e recortada fora do cartão: a sombra de elevação
 * do Android aparece *através* de um cartão translúcido e deixa o vidro cinza.
 */
fun Modifier.vidro(shape: Shape, alfa: Float = ALFA_VIDRO): Modifier = this
    .drawWithCache {
        val contorno = shape.createOutline(size, layoutDirection, this)
        val recorte = Path().apply { addOutline(contorno) }
        // Cinco camadas crescendo a partir de um contorno menor que o cartão
        // imitam o `0 10px 24px -10px` da prévia sem RenderEffect.
        val camadas = (0 until 5).map { i ->
            val espalha = (-3 + i * 2.2f).dp.toPx()
            espalha to shape.createOutline(
                Size(size.width + 2 * espalha, size.height + 2 * espalha),
                layoutDirection,
                this,
            )
        }
        val desce = 6.dp.toPx()
        onDrawBehind {
            clipPath(recorte, ClipOp.Difference) {
                camadas.forEach { (espalha, forma) ->
                    translate(-espalha, desce - espalha) {
                        drawOutline(forma, Sombra.copy(alpha = if (VIDRO_REAL) 0.035f else 0.045f))
                    }
                }
            }
        }
    }
    .clip(shape)
    .background(Color.White.copy(alpha = alfa))
    .drawWithCache {
        // Brilho em 150°, como o `linear-gradient(150deg, …)` da prévia.
        val dx = 0.5f
        val dy = 0.866f
        val comprimento = abs(size.width * dx) + abs(size.height * dy)
        val centro = Offset(size.width / 2, size.height / 2)
        val brilho = Brush.linearGradient(
            0f to Color.White.copy(alpha = if (VIDRO_REAL) 0.40f else 0.25f),
            0.42f to Color.White.copy(alpha = 0f),
            start = centro - Offset(dx, dy) * (comprimento / 2),
            end = centro + Offset(dx, dy) * (comprimento / 2),
        )
        onDrawBehind { drawRect(brilho) }
    }
    .border(
        width = 1.dp,
        brush = if (VIDRO_REAL) {
            // Topo mais claro que a base: é o fio de luz que faz a borda ler
            // como vidro, e não como contorno.
            Brush.verticalGradient(
                0f to Color.White.copy(alpha = 0.95f),
                0.5f to Color.White.copy(alpha = 0.72f),
                1f to Color.White.copy(alpha = 0.40f),
            )
        } else {
            Brush.verticalGradient(listOf(Color.White.copy(alpha = 0.95f), Color.White.copy(alpha = 0.95f)))
        },
        shape = shape,
    )

/**
 * Campo de texto sobre o vidro do login. O campo é o próprio vidro — um cartão
 * em volta custaria altura que a tela de 4" não tem.
 */
@Composable
fun coresCampoVidro(): TextFieldColors = OutlinedTextFieldDefaults.colors(
    unfocusedContainerColor = Color.White.copy(alpha = ALFA_VIDRO),
    focusedContainerColor = Color.White.copy(alpha = max(ALFA_VIDRO, 0.85f)),
    // Borda cheia, sem alfa: a 35% ela sumia sobre as luzes (~1,5:1) e o campo
    // sem foco não se distinguia do cartão com a tela embaçada.
    unfocusedBorderColor = BordaDigitar,
    focusedBorderColor = Verde,
)
