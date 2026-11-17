package br.com.cddemo.redespacho.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.ConsultaPallet
import br.com.cddemo.redespacho.rede.ItemDoPallet
import br.com.cddemo.redespacho.rede.PalletConsultado
import br.com.cddemo.redespacho.ui.theme.NUMEROS_TABULARES
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.Tinta2
import br.com.cddemo.redespacho.ui.theme.TintaLegenda
import br.com.cddemo.redespacho.ui.theme.Verde

/**
 * Itens do pallet bipado à vista antes do "ver todos": o bastante para
 * reconhecer o palete sem empurrar os outros pallets da NF para longe.
 */
private const val ITENS_A_VISTA = 6

/**
 * Consultar pallet: bipa a etiqueta e vê de qual NF o pallet é, o que tem e
 * onde estão os outros pallets da mesma NF — para achar o resto da carga sem
 * ir à retaguarda. Só leitura: nada aqui move caixa.
 *
 * Os outros pallets vêm recolhidos: o operador bipou para ver aquele, e numa
 * NF grande a lista empurraria o conteúdo do bipado para fora da tela de 4".
 */
@Composable
fun TelaConsultaPallet(estado: EstadoApp, vm: AppViewModel) {
    val consulta = estado.consultaPallet
    // Rolagem nova a cada pallet: quem desceu até os outros da NF e bipou
    // outra etiqueta precisa ver o pallet novo desde o topo.
    val rolagem = remember(consulta?.codigo) { ScrollState(0) }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Cabecalho("Consultar pallet", "NF, conteúdo e onde estão os outros")
        FaixaEnvio(estado)

        // Tudo rola: NF com muitos pallets, ou pallet com muitos itens, passa
        // da tela de 4", e nada pode ficar escondido.
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rolagem)) {
            estado.mensagem?.let { AvisoErroMov(it) }
            estado.sucesso?.let { AvisoOkMov(it) }
            if (consulta == null) {
                TextoPasso(
                    "Bipe a etiqueta de um pallet (P-RED- ou P-DEV-): aparece de qual NF ele é, o que tem " +
                        "e onde estão os outros pallets da mesma NF.",
                )
            } else {
                ResultadoConsulta(consulta)
            }
        }

        Spacer(Modifier.height(6.dp))
        EntradaManual(
            rotulo = "Código do pallet",
            textoBotao = "Consultar",
            habilitado = !estado.carregando,
            valorInicial = "P-RED-",
        ) { vm.consultarPallet(it) }
    }
}

@Composable
private fun ResultadoConsulta(c: ConsultaPallet) {
    // Por pallet consultado: bipar outra etiqueta começa tudo recolhido.
    var outrosAbertos by remember(c.codigo) { mutableStateOf(false) }
    var abertos by remember(c.codigo) { mutableStateOf(emptySet<String>()) }
    val outros = c.outros

    c.bipado?.let { CartaoBipado(c, it) }

    when {
        c.notaNumero == null -> TextoApoio("Sem NF, não há outros pallets para mostrar.")
        outros.isEmpty() -> TextoApoio("É o único pallet da NF ${c.notaNumero}.")
        else -> {
            CabecalhoOutros(outros, aberto = outrosAbertos) { outrosAbertos = !outrosAbertos }
            if (outrosAbertos) {
                outros.forEach { p ->
                    val aberto = p.codigo in abertos
                    CartaoOutroPallet(p, c.unidade, aberto) {
                        abertos = if (aberto) abertos - p.codigo else abertos + p.codigo
                    }
                }
            }
        }
    }
}

/**
 * O pallet bipado: código grande, NF, total e lugar de relance; os itens
 * logo abaixo, já abertos — é o que o operador bipou para ver.
 */
@Composable
private fun CartaoBipado(c: ConsultaPallet, p: PalletConsultado) {
    var todos by remember(p.codigo) { mutableStateOf(false) }
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        border = BorderStroke(1.dp, Verde.copy(alpha = 0.3f)),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Text(
                if (c.tipo == "DEVOLUCAO") "Pallet de devolução" else "Pallet de separação",
                fontSize = 12.sp,
                lineHeight = 16.sp,
                color = TintaLegenda,
            )
            Text(
                c.codigo,
                fontSize = 24.sp,
                lineHeight = 28.sp,
                fontWeight = FontWeight.ExtraBold,
                fontFamily = FontFamily.Monospace,
                color = Tinta,
            )
            // Sem limite de linhas: nome de cliente longo quebra, não some.
            Text(textoDaNota(c), fontSize = 15.sp, lineHeight = 19.sp, fontWeight = FontWeight.SemiBold, color = Tinta)
            c.notaStatus?.let {
                Text("NF ${situacaoDaNota(it)}", fontSize = 12.sp, lineHeight = 16.sp, color = Tinta2)
            }

            Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.widthIn(min = 64.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        "${p.quantidade}",
                        fontSize = 26.sp,
                        lineHeight = 30.sp,
                        fontWeight = FontWeight.ExtraBold,
                        color = Verde,
                        style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                    )
                    Text(nomeDaUnidade(c.unidade), fontSize = 12.sp, lineHeight = 16.sp, color = TintaLegenda)
                }
                Spacer(Modifier.width(12.dp))
                LugarDoPallet(p, Modifier.weight(1f), tamanho = 17)
            }

            Spacer(Modifier.height(10.dp))
            if (p.itens.isEmpty()) {
                Text(
                    "Pallet vazio: nenhuma ${if (c.unidade == "un") "unidade" else "caixa"} nele.",
                    fontSize = 14.sp,
                    lineHeight = 19.sp,
                    color = Tinta2,
                )
            } else {
                Text(
                    "O que tem · ${p.itens.size} item(ns)",
                    fontSize = 12.sp,
                    lineHeight = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = TintaLegenda,
                )
                // Lista simples, não LazyColumn: o cartão mora numa tela que
                // rola, e lista preguiçosa dentro de rolagem não se mede.
                (if (todos) p.itens else p.itens.take(ITENS_A_VISTA)).forEach { LinhaItem(it, c.unidade) }
                if (p.itens.size > ITENS_A_VISTA) {
                    AlternarTodos(if (todos) "Mostrar menos" else "Ver todos os ${p.itens.size} itens") { todos = !todos }
                }
            }
        }
    }
}

/**
 * Onde o pallet está, em destaque, e a linha miúda com depósito e situação.
 * Cinza quando não há onde procurar: expedido ou sem endereço.
 */
@Composable
private fun LugarDoPallet(p: PalletConsultado, modifier: Modifier, tamanho: Int) {
    val temLugar = p.endereco != null && p.status != "EXPEDIDO"
    Column(modifier) {
        Text(
            localDoPallet(p),
            fontSize = tamanho.sp,
            lineHeight = (tamanho + 4).sp,
            fontWeight = FontWeight.Bold,
            color = if (temLugar) Tinta else Tinta2,
        )
        detalheDoPallet(p).takeIf { it.isNotEmpty() }?.let {
            Text(it, fontSize = 12.sp, lineHeight = 16.sp, color = Tinta2)
        }
    }
}

/** Quantidade à esquerda, alinhada pelos números; código e descrição quebram em quantas linhas precisarem. */
@Composable
private fun LinhaItem(i: ItemDoPallet, unidade: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text(
            quantidadeComUnidade(i.quantidade, unidade),
            fontSize = 14.sp,
            lineHeight = 18.sp,
            fontWeight = FontWeight.Bold,
            color = Tinta,
            textAlign = TextAlign.End,
            style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
            modifier = Modifier.widthIn(min = 52.dp),
        )
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(i.produtoCodigo, fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold, color = Tinta)
            if (i.produtoDescricao.isNotBlank()) {
                Text(i.produtoDescricao, fontSize = 13.sp, lineHeight = 17.sp, color = Tinta2)
            }
        }
    }
}

@Composable
private fun AlternarTodos(texto: String, aoTocar: () -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 52.dp)
            .clip(RoundedCornerShape(12.dp))
            .clickable(role = Role.Button, onClick = aoTocar)
            .padding(horizontal = 4.dp),
        contentAlignment = Alignment.CenterStart,
    ) { Text(texto, fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold, color = Verde) }
}

/** Faixa que abre e recolhe a lista, com o resumo de onde está o resto da NF. */
@Composable
private fun CabecalhoOutros(outros: List<PalletConsultado>, aberto: Boolean, aoTocar: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(top = 8.dp, bottom = 4.dp)
            .heightIn(min = 56.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .clickable(role = Role.Button, onClick = aoTocar)
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                "Outros pallets da NF (${outros.size})",
                fontSize = 16.sp,
                lineHeight = 20.sp,
                fontWeight = FontWeight.Bold,
                color = Tinta,
            )
            Text(resumoDosOutros(outros), fontSize = 12.sp, lineHeight = 16.sp, color = Tinta2)
        }
        Spacer(Modifier.width(8.dp))
        Seta(aberto)
    }
}

/**
 * Outro pallet da NF: código, lugar e total; tocar abre o conteúdo. Só a
 * faixa de cima abre e fecha — tocar nos itens ao rolar não recolhe o cartão.
 */
@Composable
private fun CartaoOutroPallet(p: PalletConsultado, unidade: String, aberto: Boolean, aoTocar: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        border = BORDA_CARTAO,
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 52.dp)
                .clickable(role = Role.Button, onClick = aoTocar)
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    p.codigo,
                    fontSize = 17.sp,
                    lineHeight = 21.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = Tinta,
                )
                LugarDoPallet(p, Modifier, tamanho = 15)
            }
            Spacer(Modifier.width(8.dp))
            Text(
                quantidadeComUnidade(p.quantidade, unidade),
                fontSize = 17.sp,
                lineHeight = 21.sp,
                fontWeight = FontWeight.ExtraBold,
                color = Tinta,
                style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
            )
            Spacer(Modifier.width(6.dp))
            Seta(aberto)
        }
        if (aberto) {
            Column(Modifier.padding(start = 12.dp, end = 12.dp, bottom = 10.dp)) {
                if (p.itens.isEmpty()) {
                    Text("Pallet vazio.", fontSize = 13.sp, lineHeight = 17.sp, color = Tinta2)
                } else {
                    p.itens.forEach { LinhaItem(it, unidade) }
                }
            }
        }
    }
}

/** A seta do "voltar", girada: para a direita recolhido, para baixo aberto. */
@Composable
private fun Seta(aberto: Boolean) {
    IconeTraco(Icone.VOLTAR, Tinta2, Modifier.size(20.dp).rotate(if (aberto) -90f else 180f))
}

@Composable
private fun TextoApoio(texto: String) {
    Text(texto, fontSize = 14.sp, lineHeight = 19.sp, color = Tinta2, modifier = Modifier.padding(vertical = 10.dp))
}
