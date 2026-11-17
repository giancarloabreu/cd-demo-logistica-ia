package br.com.cddemo.redespacho

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.EnterExitState
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.IntOffset
import androidx.lifecycle.viewmodel.compose.viewModel
import br.com.cddemo.redespacho.coletor.DataWedge
import br.com.cddemo.redespacho.ui.AppViewModel
import br.com.cddemo.redespacho.ui.BarraCarregando
import br.com.cddemo.redespacho.ui.DialogoAlerta
import br.com.cddemo.redespacho.ui.DialogoAtualizacao
import br.com.cddemo.redespacho.ui.FundoCamara
import br.com.cddemo.redespacho.ui.Tela
import br.com.cddemo.redespacho.ui.TelaConferencia
import br.com.cddemo.redespacho.ui.TelaConferenciaBox
import br.com.cddemo.redespacho.ui.TelaConferenciaRetorno
import br.com.cddemo.redespacho.ui.TelaConfiguracao
import br.com.cddemo.redespacho.ui.TelaConsultaPallet
import br.com.cddemo.redespacho.ui.TelaCriarPallet
import br.com.cddemo.redespacho.ui.TelaDevolucoes
import br.com.cddemo.redespacho.ui.TelaEnderecamento
import br.com.cddemo.redespacho.ui.TelaInstrucoes
import br.com.cddemo.redespacho.ui.TelaItens
import br.com.cddemo.redespacho.ui.TelaItensDevolucao
import br.com.cddemo.redespacho.ui.TelaLogin
import br.com.cddemo.redespacho.ui.TelaMenu
import br.com.cddemo.redespacho.ui.TelaNotas
import br.com.cddemo.redespacho.ui.TelaReimpressao
import br.com.cddemo.redespacho.ui.TelaRetorno
import br.com.cddemo.redespacho.ui.TelaSeparacao
import br.com.cddemo.redespacho.ui.TelaTransferencia
import br.com.cddemo.redespacho.ui.lembrarAnimacoes
import br.com.cddemo.redespacho.ui.theme.RedespachoTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Ícones escuros nas barras do sistema: o app é sempre claro, e com o
        // aparelho em modo escuro o padrão pintaria ícones brancos sobre gelo.
        // Barra de navegação transparente também: o escrim branco a 90% virava
        // uma faixa leitosa sobre o fundo de câmara fria do menu e do login.
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.light(android.graphics.Color.TRANSPARENT, ESCRIM_ESCURO),
        )
        // O separador não fica tocando a tela entre um bipe e outro; deixar
        // apagar no meio da separação custa tempo. Ver docs/TELAS-COLETOR.md §0.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        // Configura o perfil do DataWedge sozinho, para o gatilho funcionar sem
        // ninguém abrir o DataWedge em cada coletor. Sem efeito fora da Zebra.
        DataWedge.provisionar(this)

        setContent {
            RedespachoTheme {
                val vm: AppViewModel = viewModel()
                val estado by vm.estado.collectAsState()
                val animar = lembrarAnimacoes()

                // A leitura do DataWedge chega por broadcast; registrar uma vez
                // e deixar o ViewModel decidir o que fazer com o código.
                DisposableEffect(Unit) {
                    vm.scanner.registrar { codigo -> vm.aoLerCodigo(codigo) }
                    onDispose { vm.scanner.cancelar() }
                }

                BackHandler { if (!vm.voltar()) moveTaskToBack(true) }

                Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
                    // O fundo de câmara fria fica atrás de tudo, até das barras do
                    // sistema, e aparece só no login e no menu — as telas de vidro.
                    FundoDaTela(vidro = estado.tela == Tela.LOGIN || estado.tela == Tela.MENU, animar = animar)

                    Scaffold(
                        modifier = Modifier.fillMaxSize(),
                        containerColor = Color.Transparent,
                        contentColor = MaterialTheme.colorScheme.onBackground,
                    ) { padding ->
                        Box(Modifier.padding(padding)) {
                            // Troca de tela com mola: desliza pouco (um quinto da
                            // largura) e some rápido — o bastante para dizer "fui
                            // para frente" ou "voltei", curto para não atrasar o bipe.
                            AnimatedContent(
                                targetState = estado.tela,
                                transitionSpec = {
                                    if (!animar) {
                                        EnterTransition.None togetherWith ExitTransition.None
                                    } else {
                                        val sentido =
                                            if (profundidade(targetState) >= profundidade(initialState)) 1 else -1
                                        val mola = spring<IntOffset>(
                                            dampingRatio = 0.82f,
                                            stiffness = Spring.StiffnessMediumLow,
                                        )
                                        (slideInHorizontally(mola) { sentido * it / 5 } + fadeIn(tween(200))) togetherWith
                                            (slideOutHorizontally(mola) { -sentido * it / 5 } + fadeOut(tween(140)))
                                    }
                                },
                                label = "tela",
                            ) { tela ->
                                // A tela que está saindo não aceita toque: um segundo
                                // toque durante o deslize repetiria a ação dela.
                                val saindo = transition.targetState == EnterExitState.PostExit
                                // As telas sólidas pintam o próprio fundo: o fundo de
                                // câmara leva 280 ms para sumir, e durante o deslize a
                                // tela de bipe ficava transparente sobre as luzes e por
                                // cima da tela de vidro que saía — as duas misturadas.
                                val solida = tela != Tela.LOGIN && tela != Tela.MENU
                                Box(
                                    Modifier
                                        .fillMaxSize()
                                        .then(
                                            if (solida) Modifier.background(MaterialTheme.colorScheme.background)
                                            else Modifier,
                                        )
                                        .then(if (saindo) Modifier.pointerInput(Unit) { engolirToques() } else Modifier),
                                ) {
                                    when (tela) {
                                        Tela.CONFIGURACAO -> TelaConfiguracao(estado, vm)
                                        Tela.LOGIN -> TelaLogin(estado, vm)
                                        Tela.MENU -> TelaMenu(estado, vm)
                                        Tela.INSTRUCOES -> TelaInstrucoes(estado, vm)
                                        Tela.NOTAS -> TelaNotas(estado, vm)
                                        Tela.ITENS -> TelaItens(estado, vm)
                                        Tela.SEPARACAO -> TelaSeparacao(estado, vm)
                                        Tela.CRIAR_PALLET -> TelaCriarPallet(estado, vm)
                                        Tela.ENDERECAMENTO -> TelaEnderecamento(estado, vm)
                                        Tela.DEVOLUCOES -> TelaDevolucoes(estado, vm)
                                        Tela.ITENS_DEVOLUCAO -> TelaItensDevolucao(estado, vm)
                                        Tela.CONFERENCIA -> TelaConferencia(estado, vm)
                                        Tela.RETORNO -> TelaRetorno(estado, vm)
                                        Tela.CONFERENCIA_RETORNO -> TelaConferenciaRetorno(estado, vm)
                                        Tela.TRANSFERENCIA -> TelaTransferencia(estado, vm)
                                        Tela.REIMPRESSAO -> TelaReimpressao(estado, vm)
                                        Tela.CONSULTA_PALLET -> TelaConsultaPallet(estado, vm)
                                        Tela.CONFERENCIA_BOX -> TelaConferenciaBox(estado, vm)
                                    }
                                }
                            }
                            BarraCarregando(estado.carregando)
                            DialogoAlerta(estado, vm)
                            DialogoAtualizacao(estado, vm)
                        }
                    }
                }
            }
        }
    }

    private companion object {
        // Escrim escuro do padrão do androidx.activity. Só entra onde a barra
        // de navegação não aceita ícones escuros — no androidx 1.13 isso é o
        // Android 6 e 7, abaixo do minSdk 26 da frota. Fica porque a API pede e
        // porque, se valer em algum aparelho, ícone branco precisa de fundo escuro.
        val ESCRIM_ESCURO = android.graphics.Color.argb(0x80, 0x1B, 0x1B, 0x1B)
    }
}

/**
 * Profundidade de cada tela na navegação, para o sentido do deslize: ir mais
 * fundo desliza para a esquerda, voltar para a direita.
 *
 * Explícito, e não pela ordem do enum: pelo ordinal, LOGIN → CONFIGURACAO lia
 * como "voltei" (a configuração vem antes no enum) e qualquer reordenação do
 * enum trocaria o sentido sem ninguém notar. O `when` sem `else` obriga a dar
 * lugar a toda tela nova.
 */
private fun profundidade(tela: Tela): Int = when (tela) {
    Tela.LOGIN -> 0
    Tela.CONFIGURACAO, Tela.MENU -> 1
    Tela.INSTRUCOES, Tela.CRIAR_PALLET, Tela.ENDERECAMENTO, Tela.DEVOLUCOES,
    Tela.TRANSFERENCIA, Tela.REIMPRESSAO, Tela.CONSULTA_PALLET, Tela.CONFERENCIA_BOX -> 2
    Tela.NOTAS, Tela.ITENS_DEVOLUCAO, Tela.RETORNO -> 3
    Tela.ITENS, Tela.CONFERENCIA, Tela.CONFERENCIA_RETORNO -> 4
    Tela.SEPARACAO -> 5
}

/**
 * Fundo de câmara fria que entra e sai em fade. Isolado num composable próprio
 * para que só ele recomponha a cada quadro do fade, não a tela inteira.
 */
@Composable
private fun FundoDaTela(vidro: Boolean, animar: Boolean) {
    val alfa by animateFloatAsState(
        if (vidro) 1f else 0f,
        if (animar) tween(280) else snap(),
        label = "fundoCamara",
    )
    if (alfa > 0f) {
        FundoCamara(Modifier.graphicsLayer { this.alpha = alfa })
    }
}

private suspend fun androidx.compose.ui.input.pointer.PointerInputScope.engolirToques() {
    awaitPointerEventScope {
        while (true) {
            awaitPointerEvent(PointerEventPass.Initial).changes.forEach { it.consume() }
        }
    }
}
