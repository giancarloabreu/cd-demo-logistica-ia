package br.com.cddemo.redespacho.ui

import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.PalletParaEnderecar
import br.com.cddemo.redespacho.rede.Posicao
import br.com.cddemo.redespacho.ui.theme.Verde
import br.com.cddemo.redespacho.ui.theme.VerdeClaro

private val VERDE = Verde
private val AZUL_OCUPADO = Color(0xFF1565C0)

/**
 * Endereçamento: lê o pallet, lê o box, escolhe o nível.
 *
 * Uma leitura só para as duas etapas — o prefixo do código diz se veio pallet
 * (`P-RED-…`) ou box (`C8-D28`). Ver docs/TELAS-COLETOR.md §6.
 */
@Composable
fun TelaEnderecamento(estado: EstadoApp, vm: AppViewModel) {

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Cabecalho("Endereçamento")

        // Rola: um box tem seis níveis, e seis cartões mais o pallet não cabem
        // na tela de 4" do TC8300 — o botão Endereçar sumia embaixo.
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())) {
            estado.sucesso?.let {
                Card(
                    Modifier.fillMaxWidth().padding(vertical = 6.dp),
                    shape = FORMA_AVISO,
                    colors = CardDefaults.cardColors(containerColor = VERDE),
                ) { Text(it, Modifier.padding(10.dp), color = Color.White, fontSize = 14.sp) }
            }
            estado.mensagem?.let {
                Card(
                    Modifier.fillMaxWidth().padding(vertical = 6.dp),
                    shape = FORMA_AVISO,
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                ) {
                    Text(
                        it,
                        Modifier.padding(10.dp),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        fontSize = 14.sp,
                    )
                }
            }

            Spacer(Modifier.height(6.dp))
            when {
                estado.palletEndereco == null -> PassoLerPallet(estado)
                estado.box == null -> PassoLerBox(estado.palletEndereco, vm)
                else -> PassoEscolherNivel(estado, vm)
            }
        }

        Spacer(Modifier.height(6.dp))
        // No passo do nível o que importa é confirmar, e o gatilho continua lendo
        // outro box se precisar — o campo de digitação cede o lugar ao botão.
        if (estado.palletEndereco != null && estado.box != null) {
            BotaoGrande(
                "Endereçar",
                habilitado = estado.posicaoEscolhida != null && !estado.carregando,
            ) { vm.confirmarEnderecamento() }
        } else {
            EntradaManual(
                rotulo = "Pallet ou box",
                textoBotao = "Ler",
                habilitado = !estado.carregando,
            ) { vm.lerNoEnderecamento(it) }
        }
    }
}

@Composable
private fun PassoLerPallet(estado: EstadoApp) {
    Text("1. Leia a etiqueta do pallet", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(10.dp))

    if (estado.pendentes.isEmpty()) {
        Text(
            "Nenhum pallet aguardando endereço.",
            fontSize = 15.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }

    Text(
        "${estado.pendentes.size} pallet(s) na fila:",
        fontSize = 13.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    // Lista simples: a tela inteira já rola, e lista preguiçosa dentro de
    // rolagem não se mede. A altura fixa de 280 dp comia metade da tela de 4".
    estado.pendentes.take(40).forEach { p ->
        Card(Modifier.fillMaxWidth().padding(vertical = 3.dp), border = BORDA_CARTAO) {
            Column(Modifier.padding(10.dp)) {
                Text(p.codigo, fontSize = 16.sp, fontWeight = FontWeight.Bold, fontFamily = FontFamily.Monospace)
                Text(
                    "NF ${p.notaNumero ?: "—"} · ${p.qtdCaixas} caixas" +
                        if (p.status == "EM_USO") " · em separação" else "",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
    if (estado.pendentes.size > 40) {
        Text(
            "+ ${estado.pendentes.size - 40} pallet(s)",
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun PassoLerBox(p: PalletParaEnderecar, vm: AppViewModel) {
    // Confirmar o conteúdo antes de andar até o box evita levar o pallet errado.
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = VerdeClaro),
    ) {
        Column(Modifier.padding(14.dp)) {
            Text(
                p.codigo,
                fontSize = 20.sp,
                fontWeight = FontWeight.ExtraBold,
                fontFamily = FontFamily.Monospace,
                color = VERDE,
            )
            Text("NF ${p.notaNumero ?: "—"}", fontSize = 15.sp)
            p.cliente?.let { Text(it, fontSize = 14.sp) }
            Text("${p.qtdCaixas} caixas", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            p.enderecoAtual?.let {
                Text(
                    "Já endereçado em $it — ler outro box move o pallet.",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
    Spacer(Modifier.height(14.dp))
    Text("2. Leia a etiqueta do box", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(8.dp))
    TextButton(onClick = { vm.limparEnderecamento() }) { Text("Trocar pallet") }
}

@Composable
private fun PassoEscolherNivel(estado: EstadoApp, vm: AppViewModel) {
    val box = estado.box ?: return

    Text(
        "${estado.palletEndereco?.codigo} → ${box.codigo}",
        fontSize = 17.sp,
        fontWeight = FontWeight.Bold,
    )
    Text(
        "${box.deposito} · Rua ${box.rua}",
        fontSize = 13.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(10.dp))

    // Duas colunas: um box tem seis níveis, e seis linhas mais o pallet e o
    // botão passavam de 560 dp — a tela de 4" tem ~460. Em grade cabe.
    // Azul = ocupado, branco = livre. Nível ocupado não é selecionável.
    box.posicoes.chunked(2).forEach { par ->
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            par.forEach { p ->
                LinhaNivel(p, estado.posicaoEscolhida == p.id, vm, Modifier.weight(1f))
            }
            if (par.size == 1) Spacer(Modifier.weight(1f))
        }
    }

    Spacer(Modifier.height(4.dp))
    TextButton(onClick = { vm.limparEnderecamento() }) { Text("Recomeçar") }
}

@Composable
private fun LinhaNivel(p: Posicao, escolhida: Boolean, vm: AppViewModel, modifier: Modifier) {
    val fundo = when {
        !p.livre -> AZUL_OCUPADO.copy(alpha = 0.20f)
        escolhida -> VerdeClaro
        else -> Color.White
    }
    Card(
        modifier
            .padding(vertical = 3.dp)
            .clickable(enabled = p.livre) { vm.escolherPosicao(p.id) },
        colors = CardDefaults.cardColors(containerColor = fundo),
        border = when {
            escolhida -> BorderStroke(2.dp, VERDE)
            p.livre -> BORDA_CARTAO
            else -> null
        },
    ) {
        Column(Modifier.padding(10.dp)) {
            Text("Nível ${p.nivel}", fontSize = 16.sp, fontWeight = FontWeight.Bold)
            if (p.livre) {
                Text(
                    if (escolhida) "selecionado" else "livre",
                    fontSize = 13.sp,
                    color = if (escolhida) VERDE else MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = if (escolhida) FontWeight.Bold else FontWeight.Normal,
                )
            } else {
                Text(
                    p.ocupadaPor.orEmpty(),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                )
                Text(
                    "NF ${p.notaNumero ?: "—"} · ${p.qtdCaixas ?: 0} cx",
                    fontSize = 11.sp,
                    maxLines = 1,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
