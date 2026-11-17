package br.com.cddemo.redespacho.ui

import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.Devolucao
import br.com.cddemo.redespacho.rede.ItemDevolucao
import br.com.cddemo.redespacho.ui.theme.BordaDigitar
import br.com.cddemo.redespacho.ui.theme.Linha
import br.com.cddemo.redespacho.ui.theme.NUMEROS_TABULARES
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.TintaLegenda
import br.com.cddemo.redespacho.ui.theme.Verde
import br.com.cddemo.redespacho.ui.theme.Vermelho

private val VERDE = Verde
private val VERMELHO_SUTIL = Vermelho

/**
 * Conferência de devolução no coletor.
 *
 * Espelha a separação — quantidade esperada, cadeado, divergência com
 * justificativa — com uma diferença que vem da natureza da carga: o cliente
 * pode devolver uma lata avulsa, então a contagem é em **unidades** e há dois
 * jeitos de somar. Ver docs/TELAS-RETAGUARDA.md §10.3.
 */
/**
 * "Conferir" do menu: retornos e devoluções numa lista só, em duas seções.
 *
 * Retorno primeiro: é NF nossa parada fora da expedição até ser conferida,
 * com carga na doca esperando destino. Servidor anterior à 0.6.0 não tem
 * retorno, e aí a tela volta a ser só a de devoluções.
 */
@Composable
fun TelaDevolucoes(estado: EstadoApp, vm: AppViewModel) {
    val comRetornos = !estado.retornosIndisponiveis

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Cabecalho(
            "Conferir",
            if (comRetornos) "${estado.retornos.size} retorno(s) · ${estado.devolucoes.size} devolução(ões)"
            else "${estado.devolucoes.size} devolução(ões) aguardando conferência",
        )

        estado.mensagem?.let { AvisoErro(it) }

        // Uma rolagem só para as duas seções: duas listas empilhadas dividiam
        // a tela de 4" ao meio mesmo com uma delas vazia.
        LazyColumn(Modifier.weight(1f)) {
            if (comRetornos) {
                item { TituloSecao("Retornos", "NF expedida que voltou") }
                if (estado.retornos.isEmpty()) {
                    item { TextoVazio("Nenhum retorno em conferência. O retorno é registrado na retaguarda.") }
                }
                items(estado.retornos) { r -> CartaoRetorno(r) { vm.abrirRetorno(r) } }
                item { TituloSecao("Devoluções", "NF do cliente") }
            }
            if (estado.devolucoes.isEmpty()) {
                item { TextoVazio("Nenhuma devolução para conferir. A NF do cliente é importada na retaguarda.") }
            }
            items(estado.devolucoes) { d -> CartaoDevolucao(d) { vm.abrirDevolucao(d) } }
        }
    }
}

@Composable
private fun TituloSecao(titulo: String, detalhe: String) {
    Row(
        Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 2.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        Text(titulo, fontSize = 15.sp, fontWeight = FontWeight.ExtraBold, color = Tinta)
        Spacer(Modifier.width(8.dp))
        Text(detalhe, fontSize = 12.sp, color = TintaLegenda, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun TextoVazio(texto: String) {
    Text(
        texto,
        fontSize = 14.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(vertical = 6.dp),
    )
}

@Composable
private fun CartaoDevolucao(d: Devolucao, aoClicar: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 5.dp).clickable(onClick = aoClicar),
        border = BORDA_CARTAO,
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(if (d.sobra) d.numero else "NF ${d.numero}", fontSize = 19.sp, fontWeight = FontWeight.Bold)
            Text(if (d.sobra) "Conferência de estoque" else d.emitenteNome, fontSize = 15.sp)
            Text(
                if (d.sobra) "bipe qualquer caixa · ${d.qtdConferidaUn} un já na sobra"
                else "origem NF ${d.notaOrigemNumero ?: "—"} · ${d.qtdConferidaUn} de ${d.qtdEsperadaUn} un",
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
fun TelaItensDevolucao(estado: EstadoApp, vm: AppViewModel) {
    val d = estado.devolucao ?: return
    val faltam = estado.itensDevolucao.count { !it.fechado }
    // Sobra: tocar no produto soma caixas, no lugar da tela de conferência.
    var somandoEm by remember { mutableStateOf<ItemDevolucao?>(null) }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        if (d.sobra) {
            Cabecalho(d.numero, "Conferência de estoque · ${estado.itensDevolucao.size} produto(s)")
        } else {
            Cabecalho("NF ${d.numero}", "${d.emitenteNome} · $faltam item(ns) em aberto")
        }

        estado.sucesso?.let { AvisoOk(it) }
        estado.mensagem?.let { AvisoErro(it) }

        estado.palletDevolucao?.let {
            Text(
                "Pallet $it",
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(vertical = 4.dp),
            )
        }

        if (d.sobra) {
            Text(
                if (estado.palletDevolucao == null)
                    "Bipe primeiro a etiqueta do pallet de devolução (P-DEV-)."
                else "Bipe a caixa: cada bipe conta 1 caixa. Para várias do mesmo produto, toque nele e digite.",
                fontSize = 13.sp,
                color = TintaLegenda,
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }

        LazyColumn {
            items(estado.itensDevolucao) { i ->
                if (d.sobra) CartaoItemSobra(i) { somandoEm = i }
                else CartaoItemDevolucao(i) { vm.abrirItemDevolucao(i) }
            }
        }
    }

    somandoEm?.let { i ->
        DialogoNumero(
            titulo = "Somar caixas",
            detalhe = "${i.produtoCodigo ?: ""} ${i.produtoDescricao ?: ""}" +
                (i.unidadesPorCaixa?.let { un -> " · $un un/caixa" } ?: ""),
            rotulo = "Quantidade em caixas",
            confirmar = "Somar",
            aoConfirmar = { n -> somandoEm = null; vm.somarCaixasSobra(i, n) },
            aoCancelar = { somandoEm = null },
        )
    }
}

/**
 * Item da sobra: não há quantidade esperada para comparar — o que vale é
 * quantas caixas já entraram.
 */
@Composable
private fun CartaoItemSobra(i: ItemDevolucao, aoClicar: () -> Unit) {
    val caixas = i.unidadesPorCaixa?.takeIf { it > 0 }?.let { i.qtdConferidaUn / it }
    Card(
        Modifier.fillMaxWidth().padding(vertical = 5.dp).clickable(onClick = aoClicar),
        border = BORDA_CARTAO,
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).height(44.dp).background(VERDE, CircleShape))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(i.produtoCodigo ?: "—", fontSize = 17.sp, fontWeight = FontWeight.Bold)
                Text(i.produtoDescricao ?: "", fontSize = 14.sp)
            }
            Spacer(Modifier.width(10.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    caixas?.toString() ?: "—",
                    fontSize = 19.sp,
                    fontWeight = FontWeight.ExtraBold,
                    style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                    color = VERDE,
                )
                Text("caixas", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("${i.qtdConferidaUn} un", fontSize = 12.sp, color = TintaLegenda)
            }
        }
    }
}

@Composable
private fun CartaoItemDevolucao(i: ItemDevolucao, aoClicar: () -> Unit) {
    val completo = i.qtdConferidaUn >= i.qtdEsperadaUn
    val cor = when {
        i.status == "FECHADO_DIVERGENCIA" -> VERMELHO_SUTIL
        i.fechado -> VERDE
        completo -> VERDE
        else -> VERMELHO_SUTIL
    }

    Card(
        Modifier.fillMaxWidth().padding(vertical = 5.dp).clickable(onClick = aoClicar),
        border = BORDA_CARTAO,
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).height(44.dp).background(cor, CircleShape))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(i.produtoCodigo ?: "—", fontSize = 17.sp, fontWeight = FontWeight.Bold)
                Text(i.produtoDescricao ?: "", fontSize = 14.sp)
                i.justificativa?.let {
                    Text(
                        "“$it”",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.width(10.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    "${i.qtdConferidaUn}/${i.qtdEsperadaUn}",
                    fontSize = 19.sp,
                    fontWeight = FontWeight.ExtraBold,
                    style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                    color = cor,
                )
                Text("un", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Cadeado(
                    when {
                        i.status == "FECHADO_DIVERGENCIA" -> EstadoCadeado.DIVERGENCIA
                        i.fechado -> EstadoCadeado.FECHADO
                        else -> EstadoCadeado.ABERTO
                    },
                    tamanho = 28.dp,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
        }
    }
}

@Composable
fun TelaConferencia(estado: EstadoApp, vm: AppViewModel) {
    val item = estado.itemDevolucao ?: return
    var pedindoJustificativa by remember { mutableStateOf(false) }
    var pedindoUnidades by remember { mutableStateOf(false) }

    val completo = item.qtdConferidaUn >= item.qtdEsperadaUn

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            BotaoIcone(Icone.VOLTAR, "Voltar aos itens da devolução") { vm.irPara(Tela.ITENS_DEVOLUCAO) }
            Text(
                "Devolução ${estado.devolucao?.numero ?: ""}",
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            // Mesmo controle da separação: o cadeado é o botão.
            Cadeado(
                when {
                    item.status == "FECHADO_DIVERGENCIA" -> EstadoCadeado.DIVERGENCIA
                    item.fechado -> EstadoCadeado.FECHADO
                    completo -> EstadoCadeado.PRONTO
                    else -> EstadoCadeado.ABERTO
                },
                habilitado = !estado.carregando && !item.fechado,
                aoTocar = {
                    if (completo) vm.fecharItemDevolucao() else pedindoJustificativa = true
                },
            )
        }

        // Rola: mesma razão da tela de separação — na tela de 4" um aviso
        // empurrava o campo e o botão de unidades para fora. Quando cabe,
        // o contador fica no meio da altura, como na separação.
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .heightIn(min = maxHeight),
                verticalArrangement = Arrangement.SpaceBetween,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Column(Modifier.fillMaxWidth()) {
                    Text(
                        item.produtoCodigo ?: "—",
                        fontSize = 21.sp,
                        fontWeight = FontWeight.ExtraBold,
                        letterSpacing = (-0.2).sp,
                        color = Tinta,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        item.produtoDescricao ?: "",
                        fontSize = 14.sp,
                        lineHeight = 18.sp,
                        color = Color(0xFF3A4C53),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        "cliente: ${item.cprodCliente ?: "—"} ${item.xprodCliente ?: ""}",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    estado.mensagem?.let { AvisoErro(it) }
                    estado.sucesso?.let { AvisoOk(it) }
                }

                Column(
                    Modifier.fillMaxWidth().padding(vertical = 10.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    ContadorMola(
                        atual = item.qtdConferidaUn,
                        previsto = item.qtdEsperadaUn,
                        unidade = "unidades",
                        chave = item.id,
                        tamanho = 54.sp,
                    )
                    item.unidadesPorCaixa?.let {
                        Text(
                            "cada bipe soma $it un (1 caixa)",
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Spacer(Modifier.height(10.dp))
                    BarraOndulada(
                        fracao = if (item.qtdEsperadaUn > 0) {
                            item.qtdConferidaUn.toFloat() / item.qtdEsperadaUn
                        } else {
                            0f
                        },
                        ondular = !item.fechado,
                        modifier = Modifier.fillMaxWidth(0.92f),
                    )
                }

                // Só pallet de devolução: o de separação que ficou ativo não vai aqui.
                if (estado.palletDevolucao != null) {
                    ChipPallet(estado.palletDevolucao)
                } else {
                    Text(
                        "Bipe primeiro a etiqueta do pallet de devolução (P-DEV-)",
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = VERMELHO_SUTIL,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }

        Spacer(Modifier.height(6.dp))
        // Lado a lado: empilhados custavam uma linha inteira da tela de 4".
        // Sem pallet, o campo só serve para a etiqueta dele e as unidades
        // soltas esperam: caixa sem pallet não aparece no estoque.
        val semPallet = estado.palletDevolucao == null
        val podeUnidades = !item.fechado && !estado.carregando && !semPallet
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            EntradaManual(
                rotulo = if (semPallet) "Etiqueta do pallet" else "Código da caixa ou pallet",
                textoBotao = "Bipar",
                habilitado = !item.fechado && !estado.carregando,
                // Sempre texto: com teclado numérico não dava para digitar o
                // "P-DEV-" do segundo pallet quando a etiqueta dele não lê.
                teclado = KeyboardType.Text,
                modifier = Modifier.weight(1f),
            ) { vm.biparDevolucao(it) }
            OutlinedButton(
                onClick = { pedindoUnidades = true },
                enabled = podeUnidades,
                shape = CircleShape,
                border = BorderStroke(1.5.dp, if (podeUnidades) BordaDigitar else Linha),
                colors = ButtonDefaults.outlinedButtonColors(containerColor = Color.White, contentColor = Tinta),
                modifier = Modifier.weight(1f).heightIn(min = 52.dp),
            ) {
                Text(
                    "Unidades soltas",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    textAlign = TextAlign.Center,
                )
            }
        }
    }

    if (pedindoUnidades) {
        DialogoNumero(
            titulo = "Unidades soltas",
            detalhe = "Para a sobra que não fecha caixa — o cliente pode devolver uma lata avulsa.",
            aoConfirmar = { vm.digitarUnidades(it); pedindoUnidades = false },
            aoCancelar = { pedindoUnidades = false },
        )
    }

    if (pedindoJustificativa) {
        DialogoJustificativaDev(
            detalhe = "Conferido ${item.qtdConferidaUn} de ${item.qtdEsperadaUn} un. " +
                "Cliente devolveu menos do que a NF declara.",
            aoConfirmar = { vm.fecharItemDevolucao(it); pedindoJustificativa = false },
            aoCancelar = { pedindoJustificativa = false },
        )
    }
}

// --- apoio -------------------------------------------------------------------

// Internos, e não privados: a conferência de retorno (TelasRetorno.kt) usa os
// mesmos avisos, para as duas conferências falarem igual.
@Composable
internal fun AvisoErro(texto: String) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        shape = FORMA_AVISO,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
    ) {
        Text(texto, Modifier.padding(12.dp), color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 15.sp)
    }
}

@Composable
internal fun AvisoOk(texto: String) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        shape = FORMA_AVISO,
        colors = CardDefaults.cardColors(containerColor = VERDE),
    ) { Text(texto, Modifier.padding(12.dp), color = Color.White, fontSize = 15.sp) }
}

@Composable
private fun DialogoNumero(
    titulo: String,
    detalhe: String,
    aoConfirmar: (Int) -> Unit,
    aoCancelar: () -> Unit,
    rotulo: String = "Quantidade em unidades",
    confirmar: String = "Somar",
) {
    var valor by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = aoCancelar,
        title = { Text(titulo, fontSize = 20.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column {
                Text(detalhe, fontSize = 14.sp)
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = valor,
                    onValueChange = { valor = it.filter(Char::isDigit) },
                    label = { Text(rotulo) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { aoConfirmar(valor.toIntOrNull() ?: 0) },
                enabled = (valor.toIntOrNull() ?: 0) > 0,
            ) { Text(confirmar, fontSize = 17.sp) }
        },
        dismissButton = { TextButton(onClick = aoCancelar) { Text("Cancelar", fontSize = 17.sp) } },
    )
}

@Composable
private fun DialogoJustificativaDev(
    detalhe: String,
    aoConfirmar: (String) -> Unit,
    aoCancelar: () -> Unit,
) {
    var valor by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = aoCancelar,
        title = { Text("Fechar com divergência", fontSize = 20.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column {
                Text(detalhe, fontSize = 15.sp)
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = valor,
                    onValueChange = { valor = it },
                    label = { Text("Justificativa") },
                    singleLine = true,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { aoConfirmar(valor) }, enabled = valor.isNotBlank()) {
                Text("Fechar item", fontSize = 17.sp)
            }
        },
        dismissButton = { TextButton(onClick = aoCancelar) { Text("Cancelar", fontSize = 17.sp) } },
    )
}
