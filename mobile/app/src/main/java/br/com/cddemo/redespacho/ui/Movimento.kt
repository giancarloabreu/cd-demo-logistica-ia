package br.com.cddemo.redespacho.ui

import android.content.Context
import android.provider.Settings
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.LinearOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleResumeEffect
import br.com.cddemo.redespacho.ui.theme.Ambar
import br.com.cddemo.redespacho.ui.theme.AmbarBorda
import br.com.cddemo.redespacho.ui.theme.AmbarClaro
import br.com.cddemo.redespacho.ui.theme.NUMEROS_TABULARES
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.TintaLegenda
import br.com.cddemo.redespacho.ui.theme.Trilho
import br.com.cddemo.redespacho.ui.theme.Verde
import br.com.cddemo.redespacho.ui.theme.VerdeBorda
import br.com.cddemo.redespacho.ui.theme.VerdeClaro
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.PI
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Animações do sistema ligadas?
 *
 * Quem desliga as animações nas opções do desenvolvedor (ou por
 * acessibilidade) não quer nada se mexendo — nem a onda da barra, nem o salto
 * do contador, nem o deslize entre telas. Lido do sistema e não do Compose
 * porque animação infinita não "termina" com a escala zerada.
 */
fun animacoesLigadas(contexto: Context): Boolean =
    Settings.Global.getFloat(contexto.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f

@Composable
fun lembrarAnimacoes(): Boolean {
    val contexto = LocalContext.current
    var ligadas by remember(contexto) { mutableStateOf(animacoesLigadas(contexto)) }
    // Relido a cada volta ao app: para mexer nas opções do desenvolvedor o
    // operador sai do app, e não deveria precisar fechá-lo para a onda parar.
    LifecycleResumeEffect(contexto) {
        ligadas = animacoesLigadas(contexto)
        onPauseOrDispose { }
    }
    return ligadas
}

// --- contador ----------------------------------------------------------------

/**
 * Contador de bipes: número grande, dígitos tabulares, e um salto com mola a
 * cada aumento.
 *
 * O salto é a confirmação que o olho pega de relance, de longe, com a tela
 * embaçada — antes mesmo de ler o número. Só no aumento: exclusão de caixa não
 * pula, para não parecer um bipe que entrou.
 *
 * @param chave identifica o item; trocar de item não conta como bipe.
 */
@Composable
fun ContadorMola(
    atual: Int,
    previsto: Int,
    unidade: String,
    chave: Any?,
    modifier: Modifier = Modifier,
    tamanho: TextUnit = 62.sp,
) {
    val animar = lembrarAnimacoes()
    val escala = remember(chave) { Animatable(1f) }
    val brilho = remember(chave) { Animatable(0f) }
    // O maior valor já visto, e não o anterior: o bipe soma na hora e a
    // confirmação do servidor depois sobrescreve com a contagem dele. Com bipes
    // rápidos a tela vai 14 → 15 → 16, a confirmação do primeiro traz 15 e a do
    // segundo 16 de novo — comparando com o anterior, o mesmo bipe saltava e
    // piscava duas vezes. O preço: depois de excluir caixas (ou de uma leitura
    // recusada), os bipes que só repõem a contagem até o máximo não saltam.
    var maximo by remember(chave) { mutableIntStateOf(atual) }

    LaunchedEffect(chave, atual) {
        val subiu = atual > maximo
        maximo = max(maximo, atual)
        if (subiu && animar) {
            launch {
                brilho.snapTo(1f)
                brilho.animateTo(0f, tween(500, easing = LinearOutSlowInEasing))
            }
            // Mola com amortecimento baixo: sai de 0,86, passa de 1 e assenta —
            // o mesmo `.86 → 1.08 → 1` da prévia.
            escala.snapTo(0.86f)
            escala.animateTo(1f, spring(dampingRatio = 0.38f, stiffness = Spring.StiffnessMedium))
        }
    }

    val completo = atual >= previsto
    val cor by animateColorAsState(
        if (completo) Verde else Tinta,
        if (animar) tween(300) else snap(),
        label = "corContador",
    )

    val texto = buildAnnotatedString {
        withStyle(SpanStyle(color = cor)) { append(atual.toString()) }
        // O previsto em TintaLegenda (~4,9:1 sobre a tela sólida): mais claro
        // que o número, para não competir com ele, mas ainda legível embaçado.
        withStyle(SpanStyle(color = TintaLegenda, fontWeight = FontWeight.Medium)) {
            append(" / $previsto")
        }
    }

    BoxWithConstraints(modifier, contentAlignment = Alignment.Center) {
        // Encolhe até caber, medindo com a fonte do próprio aparelho: do
        // Android 8 ao 11 a Roboto não tem o peso 800 e cai no Black (900),
        // mais largo, e "1200 / 2400" da conferência cortava na borda. Medir
        // acerta nos dois casos; uma tabela por número de dígitos não.
        val medidor = rememberTextMeasurer()
        val limite = constraints.maxWidth
        val limitado = constraints.hasBoundedWidth
        val tamanhoQueCabe = remember(texto.text, tamanho, limite) {
            if (!limitado) {
                tamanho
            } else {
                val largura = medidor.measure(
                    texto,
                    estiloContador(tamanho),
                    maxLines = 1,
                    softWrap = false,
                ).size.width
                // 3% de folga: a largura não escala exatamente em linha reta
                // com o tamanho (arredondamento de pixel por glifo).
                if (largura <= limite) tamanho else (tamanho.value * limite / largura * 0.97f).sp
            }
        }

        Column(
            Modifier.drawBehind {
                // Clarão verde atrás do número a cada bipe aceito.
                val a = brilho.value
                if (a > 0f) {
                    drawCircle(
                        Brush.radialGradient(
                            0f to Verde.copy(alpha = 0.22f * a),
                            1f to Color.Transparent,
                            center = center,
                            radius = size.maxDimension * 0.6f,
                        ),
                        radius = size.maxDimension * 0.6f,
                    )
                }
            },
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                texto,
                style = estiloContador(tamanhoQueCabe),
                maxLines = 1,
                // Sem quebra: se ainda sobrar um fio, corta a ponta em vez de
                // mandar o " / previsto" inteiro para uma segunda linha oculta.
                softWrap = false,
                modifier = Modifier.graphicsLayer {
                    scaleX = escala.value
                    scaleY = escala.value
                },
            )
            Text(unidade, fontSize = 14.sp, color = TintaLegenda)
        }
    }
}

private fun estiloContador(tamanho: TextUnit) = TextStyle(
    fontSize = tamanho,
    lineHeight = tamanho,
    fontWeight = FontWeight.ExtraBold,
    fontFeatureSettings = NUMEROS_TABULARES,
    letterSpacing = (tamanho.value * -0.03f).sp,
)

// --- barra ondulada ----------------------------------------------------------

/**
 * Progresso como uma onda: verde até a fração separada, trilho cinza no resto.
 *
 * A onda andando diz "este item ainda está em curso" sem texto. Para quando o
 * item fecha (não há mais nada em curso) e quando o sistema está com as
 * animações desligadas. Desenhada em Canvas: a fase é lida só no desenho, então
 * cada quadro redesenha a barra sem recompor a tela.
 */
@Composable
fun BarraOndulada(fracao: Float, ondular: Boolean, modifier: Modifier = Modifier) {
    val animar = lembrarAnimacoes()
    val alvo = fracao.coerceIn(0f, 1f)
    val progresso by animateFloatAsState(
        alvo,
        if (animar) spring(dampingRatio = 0.8f, stiffness = Spring.StiffnessLow) else snap(),
        label = "progresso",
    )

    // A onda para ~5 s depois da última mudança da contagem e volta no próximo
    // bipe. A tela fica sempre ligada (FLAG_KEEP_SCREEN_ON), e uma animação
    // infinita num item parado redesenharia a 60 quadros por segundo o turno
    // inteiro, gastando bateria para dizer o que o operador já sabe.
    var recente by remember { mutableStateOf(true) }
    LaunchedEffect(alvo) {
        recente = true
        delay(5_000)
        recente = false
    }
    // Sem nada verde desenhado (contagem zero) não há onda para mostrar.
    val ondulando = animar && ondular && alvo > 0f && recente

    // Animatable e não transição infinita: ao parar, a onda congela onde está
    // em vez de saltar para a fase zero, e retoma do mesmo ponto.
    val fase = remember { Animatable(0f) }
    LaunchedEffect(ondulando) {
        if (ondulando) {
            while (true) {
                val resto = ((1f - fase.value) * 1400).toInt().coerceAtLeast(1)
                fase.animateTo(1f, tween(resto, easing = LinearEasing))
                // 1 e 0 são a mesma fase do seno: o recomeço não aparece.
                fase.snapTo(0f)
            }
        }
    }
    val caminho = remember { Path() }

    Canvas(modifier.fillMaxWidth().height(18.dp)) {
        val traco = 5.dp.toPx()
        val amplitude = 3.2.dp.toPx()
        val onda = 16.dp.toPx()
        val meio = size.height / 2
        val inicio = traco / 2 + 1f
        val fim = size.width - inicio
        val x1 = size.width * progresso
        val deslocamento = fase.value * onda

        if (progresso < 1f) {
            val xTrilho = min(fim, x1 + 9.dp.toPx())
            if (xTrilho < fim) {
                drawLine(Trilho, Offset(xTrilho, meio), Offset(fim, meio), traco, StrokeCap.Round)
            }
        }
        val xFeito = min(fim, x1 - inicio)
        if (xFeito > inicio) {
            caminho.reset()
            var x = inicio
            caminho.moveTo(x, meio + amplitude * sin(((x + deslocamento) / onda) * 2 * PI).toFloat())
            val passo = 2.dp.toPx()
            while (x < xFeito) {
                x = min(xFeito, x + passo)
                caminho.lineTo(x, meio + amplitude * sin(((x + deslocamento) / onda) * 2 * PI).toFloat())
            }
            drawPath(caminho, Verde, style = Stroke(traco, cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
    }
}

// --- cadeado -----------------------------------------------------------------

enum class EstadoCadeado { ABERTO, PRONTO, FECHADO, DIVERGENCIA }

/**
 * O cadeado como forma, não como emoji.
 *
 * Aberto é um quadrado arredondado âmbar; quando a quantidade bate e o item
 * ainda não foi fechado, vira um círculo verde que pulsa — "pode fechar", lido
 * de relance e de luva. Fechado é o círculo cheio. O emoji mudava de desenho a
 * cada versão do Android da frota; a forma é igual em todas.
 *
 * @param aoTocar nulo nas listas: ali o cadeado só informa.
 */
@Composable
fun Cadeado(
    estado: EstadoCadeado,
    modifier: Modifier = Modifier,
    tamanho: Dp = 56.dp,
    habilitado: Boolean = true,
    aoTocar: (() -> Unit)? = null,
) {
    val animar = lembrarAnimacoes()
    val aberto = estado == EstadoCadeado.ABERTO
    val raio by animateDpAsState(
        if (aberto) tamanho * (18f / 56f) else tamanho / 2,
        if (animar) spring(dampingRatio = 0.5f, stiffness = Spring.StiffnessMediumLow) else snap(),
        label = "raioCadeado",
    )
    val (fundoAlvo, bordaAlvo, tintaAlvo) = when (estado) {
        EstadoCadeado.ABERTO -> Triple(AmbarClaro, AmbarBorda, Ambar)
        EstadoCadeado.PRONTO -> Triple(VerdeClaro, VerdeBorda, Verde)
        EstadoCadeado.FECHADO -> Triple(Verde, Verde, Color.White)
        EstadoCadeado.DIVERGENCIA -> Triple(Ambar, Ambar, Color.White)
    }
    val especCor = if (animar) tween<Color>(300) else snap()
    val fundo by animateColorAsState(fundoAlvo, especCor, label = "fundoCadeado")
    val borda by animateColorAsState(bordaAlvo, especCor, label = "bordaCadeado")
    val tinta by animateColorAsState(tintaAlvo, especCor, label = "tintaCadeado")

    // Pulso só no controle da tela do item, nunca nas listas: dezenas de
    // animações infinitas numa lista custam bateria sem dizer nada a mais.
    val pulsar = animar && aoTocar != null && estado == EstadoCadeado.PRONTO
    val pulso: State<Float> = if (pulsar) {
        rememberInfiniteTransition(label = "pulso").animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(800), RepeatMode.Reverse),
            label = "anel",
        )
    } else {
        remember { mutableFloatStateOf(0f) }
    }

    val forma = RoundedCornerShape(raio)
    val descricao = when (estado) {
        EstadoCadeado.ABERTO -> "Cadeado aberto"
        EstadoCadeado.PRONTO -> "Completo, toque para fechar o item"
        EstadoCadeado.FECHADO -> "Item fechado"
        EstadoCadeado.DIVERGENCIA -> "Item fechado com divergência"
    }

    Box(
        modifier
            .size(tamanho)
            .drawBehind {
                val p = pulso.value
                if (p > 0f) {
                    drawCircle(Verde.copy(alpha = 0.14f), radius = size.minDimension / 2 + 7.dp.toPx() * p)
                }
            }
            .clip(forma)
            .background(fundo)
            .border(1.5.dp, borda, forma)
            .then(
                if (aoTocar != null) {
                    Modifier.clickable(enabled = habilitado, role = Role.Button, onClick = aoTocar)
                } else {
                    Modifier
                },
            )
            .semantics { contentDescription = descricao },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size(tamanho * (26f / 56f))) {
            desenharCadeado(
                cor = tinta,
                fechado = estado == EstadoCadeado.FECHADO || estado == EstadoCadeado.DIVERGENCIA,
                divergencia = estado == EstadoCadeado.DIVERGENCIA,
            )
        }
    }
}

// --- ícones ------------------------------------------------------------------

/**
 * Ícones de traço, desenhados a partir dos SVG da prévia (caixa de 24).
 *
 * Desenhados aqui e não tirados de uma biblioteca de ícones para não somar
 * dependência ao APK por nove desenhos.
 */
enum class Icone { SEPARAR, CRIAR_PALLET, ENDERECAR, DEVOLUCOES, TRANSFERIR, REIMPRIMIR, CONSULTAR, VOLTAR, MAIS }

@Composable
fun IconeTraco(icone: Icone, cor: Color, modifier: Modifier = Modifier.size(22.dp)) {
    Canvas(modifier) { desenharIcone(icone, cor) }
}

/** Caneta numa grade de 24 unidades, como o `viewBox="0 0 24 24"` da prévia. */
private class Traco(private val u: Float) {
    val caminho = Path()
    fun m(x: Float, y: Float) = caminho.moveTo(x * u, y * u)
    fun l(x: Float, y: Float) = caminho.lineTo(x * u, y * u)
    fun c(x1: Float, y1: Float, x2: Float, y2: Float, x3: Float, y3: Float) =
        caminho.cubicTo(x1 * u, y1 * u, x2 * u, y2 * u, x3 * u, y3 * u)
    fun arco(esq: Float, topo: Float, dir: Float, base: Float, inicio: Float, varredura: Float) =
        caminho.arcTo(Rect(esq * u, topo * u, dir * u, base * u), inicio, varredura, false)
    fun retangulo(esq: Float, topo: Float, dir: Float, base: Float, raio: Float) =
        caminho.addRoundRect(RoundRect(esq * u, topo * u, dir * u, base * u, CornerRadius(raio * u)))
    fun circulo(cx: Float, cy: Float, r: Float) =
        caminho.addOval(Rect((cx - r) * u, (cy - r) * u, (cx + r) * u, (cy + r) * u))
    fun fechar() = caminho.close()
}

private fun DrawScope.tracar(cor: Color, espessura: Float, desenho: Traco.() -> Unit) {
    val u = size.minDimension / 24f
    val t = Traco(u).apply(desenho)
    drawPath(t.caminho, cor, style = Stroke(espessura * u, cap = StrokeCap.Round, join = StrokeJoin.Round))
}

private fun DrawScope.desenharIcone(icone: Icone, cor: Color) {
    when (icone) {
        Icone.SEPARAR -> tracar(cor, 1.9f) {
            m(5f, 7f); l(19f, 7f)
            m(5f, 12f); l(19f, 12f)
            m(5f, 17f); l(14f, 17f)
        }
        Icone.CRIAR_PALLET -> tracar(cor, 1.9f) {
            retangulo(4f, 4f, 20f, 20f, 4f)
            m(12f, 8f); l(12f, 16f)
            m(8f, 12f); l(16f, 12f)
        }
        Icone.ENDERECAR -> tracar(cor, 1.9f) {
            m(12f, 21f)
            c(15f, 18.2f, 18f, 15f, 18f, 10f)
            arco(6f, 4f, 18f, 16f, 0f, -180f)
            c(6f, 15f, 9f, 18.2f, 12f, 21f)
            fechar()
            circulo(12f, 10f, 2f)
        }
        Icone.DEVOLUCOES -> tracar(cor, 1.9f) {
            m(9f, 14f); l(5f, 10f); l(9f, 6f)
            m(5f, 10f); l(14f, 10f)
            arco(9f, 10f, 19f, 20f, -90f, 180f)
            l(11f, 20f)
        }
        Icone.TRANSFERIR -> tracar(cor, 1.9f) {
            m(6f, 4f); l(6f, 9f)
            arco(6f, 5f, 14f, 13f, 180f, -90f)
            l(18f, 13f)
            m(15f, 10f); l(18f, 13f); l(15f, 16f)
            m(6f, 20f); l(6f, 17f)
        }
        Icone.REIMPRIMIR -> tracar(cor, 1.9f) {
            m(7f, 9f); l(7f, 4f); l(17f, 4f); l(17f, 9f)
            retangulo(3f, 9f, 21f, 17f, 2f)
            m(7f, 14f); l(17f, 14f); l(17f, 20f); l(7f, 20f); fechar()
        }
        // Lupa: a lente e o cabo a 45°.
        Icone.CONSULTAR -> tracar(cor, 1.9f) {
            circulo(10.5f, 10.5f, 6f)
            m(15f, 15f); l(20f, 20f)
        }
        Icone.VOLTAR -> tracar(cor, 2.2f) {
            m(15f, 5f); l(8f, 12f); l(15f, 19f)
        }
        Icone.MAIS -> {
            val u = size.minDimension / 24f
            listOf(5.5f, 12f, 18.5f).forEach { y ->
                drawCircle(cor, radius = 1.7f * u, center = Offset(12f * u, y * u))
            }
        }
    }
}

private fun DrawScope.desenharCadeado(cor: Color, fechado: Boolean, divergencia: Boolean) {
    tracar(cor, 2.2f) {
        retangulo(5f, 11f, 19f, 21f, 2.5f)
        m(8f, 11f); l(8f, 7f)
        if (fechado) {
            arco(8f, 3f, 16f, 11f, 180f, 180f)
            l(16f, 11f)
        } else {
            // A haste solta de um lado: o `a4 4 0 0 1 7.6-1.7` da prévia.
            arco(8f, 3f, 16f, 11f, 180f, 155f)
        }
        if (divergencia) {
            m(12f, 13.8f); l(12f, 16.4f)
            m(12f, 18.6f); l(12f, 18.7f)
        }
    }
}

// --- chip do pallet ----------------------------------------------------------

/**
 * Pallet ativo em verde claro, com o código em fonte de largura fixa — a mono
 * do sistema, que separa 0 de O e 1 de I num código lido de relance.
 */
@Composable
fun ChipPallet(codigo: String, modifier: Modifier = Modifier) {
    Row(
        modifier
            .clip(CircleShape)
            .background(VerdeClaro)
            .padding(horizontal = 14.dp, vertical = 9.dp)
            .semantics(mergeDescendants = true) { contentDescription = "Pallet ativo $codigo" },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(Verde))
        Spacer(Modifier.width(8.dp))
        Text(
            codigo,
            fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
            color = Verde,
            maxLines = 1,
        )
    }
}
