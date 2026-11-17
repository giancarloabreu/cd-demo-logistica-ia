package br.com.cddemo.redespacho.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.Box
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.Tinta2
import br.com.cddemo.redespacho.ui.theme.TintaLegenda
import br.com.cddemo.redespacho.ui.theme.Verde

/** O mesmo azul de "ocupado" do endereçamento, mais claro: aqui é fundo de leitura, não de escolha. */
private val AZUL_NIVEL_OCUPADO = Color(0xFF1565C0)

/**
 * Conferência de box: bipa a etiqueta do box e vê o que está em cada nível
 * daquele prédio — pallet, caixas, NF e cliente —, de cima para baixo. Nível
 * vazio aparece em branco. Etiqueta de pallet abre o box onde ele está, com
 * ele marcado. Só leitura: nada aqui move pallet. Ver docs/TELAS-COLETOR.md §6D.
 */
@Composable
fun TelaConferenciaBox(estado: EstadoApp, vm: AppViewModel) {
    val box = estado.conferenciaBox
    // Rolagem nova a cada box: quem desceu até o nível 1 e bipou outro box
    // precisa ver o novo desde o topo.
    val rolagem = remember(box?.codigo, estado.conferenciaBoxPallet) { ScrollState(0) }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Cabecalho("Conferência de box", "o que está em cada nível")
        FaixaEnvio(estado)

        // Tudo rola: seis níveis, com nível dividido por bonificação, passam
        // da tela de 4" — nada pode ficar escondido embaixo.
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rolagem)) {
            estado.mensagem?.let { AvisoDaConferenciaDeBox(it) }
            if (box == null) {
                Text(
                    "Bipe a etiqueta do box (ou de um pallet endereçado): aparece cada nível do prédio, com " +
                        "o pallet, as caixas e a NF. Nível vazio aparece em branco.",
                    fontSize = 15.sp,
                    lineHeight = 20.sp,
                    color = Tinta2,
                    modifier = Modifier.padding(vertical = 10.dp),
                )
            } else {
                val niveis = niveisDoBox(box)
                CabecalhoDoBox(box, niveis)
                niveis.forEach { LinhaDoNivel(it, estado.conferenciaBoxPallet) }
            }
        }

        Spacer(Modifier.height(6.dp))
        EntradaManual(
            rotulo = "Box ou pallet",
            textoBotao = "Conferir",
            habilitado = !estado.carregando,
        ) { vm.conferirBox(it) }
    }
}

@Composable
private fun AvisoDaConferenciaDeBox(texto: String) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        shape = FORMA_AVISO,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
    ) {
        Text(
            texto,
            Modifier.padding(12.dp),
            color = MaterialTheme.colorScheme.onErrorContainer,
            fontSize = 15.sp,
            lineHeight = 20.sp,
        )
    }
}

@Composable
private fun CabecalhoDoBox(box: Box, niveis: List<NivelDoBox>) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        border = BorderStroke(1.dp, Verde.copy(alpha = 0.3f)),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Text("Box", fontSize = 12.sp, lineHeight = 16.sp, color = TintaLegenda)
            Text(
                box.codigo,
                fontSize = 24.sp,
                lineHeight = 28.sp,
                fontWeight = FontWeight.ExtraBold,
                fontFamily = FontFamily.Monospace,
                color = Tinta,
            )
            // Sem limite de linhas: nome de depósito longo quebra, não some.
            Text("${box.deposito} · Rua ${box.rua}", fontSize = 14.sp, lineHeight = 19.sp, color = Tinta)
            Text(
                ocupacaoDoBox(niveis),
                fontSize = 13.sp,
                lineHeight = 17.sp,
                fontWeight = FontWeight.SemiBold,
                color = Tinta2,
            )
        }
    }
}

/**
 * Um nível: o número à esquerda, o que está nele à direita. Vazio fica em
 * branco, só com o número — é o que se procura com os olhos no prédio. O
 * pallet cuja etiqueta abriu a tela vem com borda verde.
 */
@Composable
private fun LinhaDoNivel(n: NivelDoBox, palletMarcado: String?) {
    val marcado = palletMarcado != null && n.pallets.any { it.ocupadaPor == palletMarcado }
    Card(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        border = if (marcado) BorderStroke(2.dp, Verde) else BORDA_CARTAO,
        colors = CardDefaults.cardColors(
            containerColor = if (n.vazio) Color.White else AZUL_NIVEL_OCUPADO.copy(alpha = 0.10f),
        ),
    ) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.widthIn(min = 56.dp)) {
                Text("Nível", fontSize = 11.sp, lineHeight = 14.sp, color = TintaLegenda)
                Text(
                    "${n.nivel}",
                    fontSize = 22.sp,
                    lineHeight = 26.sp,
                    fontWeight = FontWeight.ExtraBold,
                    color = Tinta,
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                n.pallets.forEachIndexed { i, p ->
                    if (i > 0) Spacer(Modifier.height(8.dp))
                    Text(
                        p.ocupadaPor.orEmpty(),
                        fontSize = 16.sp,
                        lineHeight = 20.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        color = if (p.ocupadaPor == palletMarcado) Verde else Tinta,
                    )
                    Text(
                        "${quantidadeDaPosicao(p)} · NF ${p.notaNumero ?: "—"}",
                        fontSize = 15.sp,
                        lineHeight = 19.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = Tinta,
                    )
                    p.cliente?.takeIf { it.isNotBlank() }?.let {
                        Text(it, fontSize = 12.sp, lineHeight = 16.sp, color = Tinta2)
                    }
                }
            }
        }
    }
}
