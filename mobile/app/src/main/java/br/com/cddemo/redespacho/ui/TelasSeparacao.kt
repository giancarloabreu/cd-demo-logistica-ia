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
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.Item
import br.com.cddemo.redespacho.rede.Nota
import br.com.cddemo.redespacho.ui.theme.Ambar
import br.com.cddemo.redespacho.ui.theme.NUMEROS_TABULARES
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.Tinta2
import br.com.cddemo.redespacho.ui.theme.TintaLegenda
import br.com.cddemo.redespacho.ui.theme.Verde
import br.com.cddemo.redespacho.ui.theme.Vermelho

private val VERDE = Verde

/** Vermelho de sinalização, não de erro: marca pendência sem gritar. */
private val VERMELHO_SUTIL = Vermelho
private val AMBAR = Ambar

/**
 * Estado do envio, visível sem tirar o olho do contador.
 *
 * Sem isto o separador não tem como saber que perdeu o servidor — continuaria
 * bipando achando que está tudo certo. Ver docs/TELAS-COLETOR.md §7.
 */
@Composable
fun FaixaEnvio(estado: EstadoApp) {
    val pendentes = estado.leiturasPendentes
    if (pendentes == 0 && estado.online) return

    val (cor, texto) = when {
        !estado.online && pendentes > 0 ->
            VERMELHO_SUTIL to "Sem servidor · $pendentes leitura(s) aguardando envio"
        !estado.online -> VERMELHO_SUTIL to "Sem servidor — reconectando"
        else -> AMBAR to "Enviando $pendentes leitura(s)…"
    }

    Card(
        Modifier.fillMaxWidth().padding(top = 6.dp),
        shape = FORMA_AVISO,
        colors = CardDefaults.cardColors(containerColor = cor.copy(alpha = 0.13f)),
    ) {
        Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(10.dp).background(cor, CircleShape))
            Spacer(Modifier.width(10.dp))
            Text(texto, fontSize = 14.sp, color = cor, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
fun DialogoAlerta(estado: EstadoApp, vm: AppViewModel) {
    val a = estado.alerta ?: return
    AlertDialog(
        onDismissRequest = { vm.fecharAlerta() },
        title = {
            Text(
                a.titulo,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                color = if (a.grave) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
            )
        },
        text = { Text(a.detalhe, fontSize = 17.sp) },
        confirmButton = {
            // Confirmação obrigatória: é o que impede o separador de continuar
            // bipando no automático depois de um erro.
            TextButton(onClick = { vm.fecharAlerta() }) { Text("Entendi", fontSize = 18.sp) }
        },
    )
}

@Composable
fun TelaNotas(estado: EstadoApp, vm: AppViewModel) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Cabecalho(estado.instrucao?.numero ?: "Notas", "${estado.notas.size} nota(s)")

        LazyColumn {
            items(estado.notas) { n -> CartaoNota(n) { vm.abrirNota(n) } }
        }
    }
}

@Composable
private fun CartaoNota(n: Nota, aoClicar: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp).clickable(onClick = aoClicar),
        border = BORDA_CARTAO,
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text("NF ${n.numero}", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Text(
                    "${n.qtdSeparada}/${n.qtdPrevista}",
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                    color = if (n.qtdSeparada >= n.qtdPrevista) VERDE
                    else MaterialTheme.colorScheme.onSurface,
                )
            }
            Text(n.cliente, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(n.status.replace('_', ' '), fontSize = 12.sp)
        }
    }
}

@Composable
fun TelaItens(estado: EstadoApp, vm: AppViewModel) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Cabecalho("NF ${estado.nota?.numero}", estado.nota?.cliente)

        FaixaEnvio(estado)

        // O gatilho na lista só seleciona pallet; bipar caixa aqui avisa.
        estado.mensagem?.let {
            Card(
                Modifier.fillMaxWidth().padding(vertical = 6.dp),
                shape = FORMA_AVISO,
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
            ) {
                Text(it, Modifier.padding(12.dp), color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 15.sp)
            }
        }

        estado.palletAtivo?.takeIf { !it.startsWith("P-DEV-") }?.let {
            Text(
                "Pallet ativo: $it",
                fontSize = 14.sp,
                color = VERDE,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }

        LazyColumn {
            items(estado.itens) { i -> CartaoItem(i) { vm.abrirItem(i) } }
        }
    }
}

/**
 * Item fechado é verde e negrito; item em aberto leva um vermelho discreto.
 * O critério é o cadeado, não a quantidade: 20/20 sem cadeado fechado ainda é
 * pendência, e é justamente esse o caso que passa despercebido numa lista longa.
 */
@Composable
private fun CartaoItem(i: Item, aoClicar: () -> Unit) {
    val cor = if (i.fechado) VERDE else VERMELHO_SUTIL
    val peso = if (i.fechado) FontWeight.Bold else FontWeight.Medium

    Card(
        Modifier.fillMaxWidth().padding(vertical = 5.dp).clickable(onClick = aoClicar),
        colors = CardDefaults.cardColors(
            containerColor = cor.copy(alpha = if (i.fechado) 0.10f else 0.07f),
        ),
        border = BorderStroke(1.dp, cor.copy(alpha = 0.22f)),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            // Na lista o cadeado só informa, e sem o estado "pronto": 20/20 sem
            // cadeado fechado continua pendência (ver acima).
            Cadeado(
                when {
                    i.comDivergencia -> EstadoCadeado.DIVERGENCIA
                    i.fechado -> EstadoCadeado.FECHADO
                    else -> EstadoCadeado.ABERTO
                },
                tamanho = 36.dp,
                modifier = Modifier.padding(end = 12.dp),
            )
            Column(Modifier.weight(1f)) {
                Text(i.produtoCodigo, fontSize = 17.sp, fontWeight = peso, color = cor)
                Text(i.produtoDescricao, fontSize = 13.sp, maxLines = 2)
                if (i.separadores.isNotEmpty()) {
                    Text("● ${i.separadores.joinToString()}", fontSize = 12.sp, color = VERDE)
                }
            }
            Text(
                "${i.qtdSeparada}/${i.qtdPrevista}",
                fontSize = 20.sp,
                fontWeight = FontWeight.ExtraBold,
                style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                color = cor,
            )
        }
    }
}

/** A tela crítica: tudo que importa cabe sem rolagem. Ver docs/TELAS-COLETOR.md §5. */
@Composable
fun TelaSeparacao(estado: EstadoApp, vm: AppViewModel) {
    val item = estado.item ?: return
    var menuAberto by remember { mutableStateOf(false) }
    var pedindoPallet by remember { mutableStateOf(false) }
    var pedindoExclusao by remember { mutableStateOf(false) }
    var pedindoFusao by remember { mutableStateOf(false) }

    val completo = item.qtdSeparada >= item.qtdPrevista

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            // voltar(), e não irPara: é ele que recarrega a lista de itens.
            BotaoIcone(Icone.VOLTAR, "Voltar aos itens") { vm.voltar() }
            Text(
                "NF ${estado.nota?.numero} · Item ${item.nItem}",
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            // O cadeado é o controle: tocar fecha o item, se permitido.
            // Alvo grande porque é operado com luva.
            Cadeado(
                when {
                    item.comDivergencia -> EstadoCadeado.DIVERGENCIA
                    item.fechado -> EstadoCadeado.FECHADO
                    completo -> EstadoCadeado.PRONTO
                    else -> EstadoCadeado.ABERTO
                },
                habilitado = !estado.carregando,
                aoTocar = { vm.alternarCadeado() },
            )
            Box {
                BotaoIcone(Icone.MAIS, "Mais opções") { menuAberto = true }
                DropdownMenu(expanded = menuAberto, onDismissRequest = { menuAberto = false }) {
                    DropdownMenuItem(
                        text = { Text("Encerrar pallet") },
                        onClick = { menuAberto = false; vm.encerrarPallet() },
                    )
                    DropdownMenuItem(
                        text = { Text("Trocar pallet") },
                        onClick = { menuAberto = false; vm.encerrarPallet(); pedindoPallet = true },
                    )
                    DropdownMenuItem(
                        text = { Text("Juntar pallets") },
                        onClick = { menuAberto = false; pedindoFusao = true },
                    )
                    DropdownMenuItem(
                        text = { Text("Reimprimir etiqueta") },
                        onClick = { menuAberto = false; vm.reimprimirPalletAtivo() },
                    )
                    DropdownMenuItem(
                        text = { Text("Excluir caixas") },
                        onClick = { menuAberto = false; pedindoExclusao = true },
                    )
                }
            }
        }

        FaixaEnvio(estado)

        // Rola: na tela de 4" um aviso a mais empurrava o pallet e o campo
        // para fora. O que precisa estar sempre à mão fica fixo embaixo.
        // Quando tudo cabe, o conteúdo ocupa a altura toda: produto no alto,
        // contador no meio, pallet embaixo — o número fica onde o olho procura.
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
                        item.produtoCodigo,
                        fontSize = 21.sp,
                        fontWeight = FontWeight.ExtraBold,
                        letterSpacing = (-0.2).sp,
                        color = Tinta,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        item.produtoDescricao,
                        fontSize = 14.sp,
                        lineHeight = 18.sp,
                        color = Color(0xFF3A4C53),
                        maxLines = 2,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    estado.mensagem?.let {
                        Card(
                            Modifier.fillMaxWidth().padding(top = 8.dp),
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
                    estado.sucesso?.let {
                        Card(
                            Modifier.fillMaxWidth().padding(top = 8.dp),
                            shape = FORMA_AVISO,
                            colors = CardDefaults.cardColors(containerColor = VERDE),
                        ) { Text(it, Modifier.padding(10.dp), color = Color.White, fontSize = 14.sp) }
                    }
                }

                Column(
                    Modifier.fillMaxWidth().padding(vertical = 10.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    ContadorMola(
                        atual = item.qtdSeparada,
                        previsto = item.qtdPrevista,
                        unidade = "caixas",
                        chave = item.id,
                    )
                    Spacer(Modifier.height(10.dp))
                    BarraOndulada(
                        fracao = if (item.qtdPrevista > 0) item.qtdSeparada.toFloat() / item.qtdPrevista else 0f,
                        ondular = !item.fechado,
                        modifier = Modifier.fillMaxWidth(0.92f),
                    )
                }

                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    // Pallet de devolução que ficou ativo da conferência não conta aqui.
                    if (estado.palletAtivo == null || estado.palletAtivo.startsWith("P-DEV-")) {
                        Card(
                            Modifier.fillMaxWidth().clickable { pedindoPallet = true },
                            shape = FORMA_AVISO,
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                        ) {
                            Column(Modifier.padding(12.dp)) {
                                Text(
                                    "Selecione um pallet",
                                    color = MaterialTheme.colorScheme.onErrorContainer,
                                    fontSize = 16.sp,
                                    fontWeight = FontWeight.Bold,
                                )
                                Text(
                                    "Bipe a etiqueta do pallet, ou toque aqui para digitar",
                                    color = MaterialTheme.colorScheme.onErrorContainer,
                                    fontSize = 12.sp,
                                )
                            }
                        }
                    } else {
                        ChipPallet(estado.palletAtivo)
                    }

                    Spacer(Modifier.height(6.dp))
                    Text(
                        when {
                            item.comDivergencia -> "Item fechado com divergência"
                            item.fechado -> "Item fechado"
                            completo -> "Completo — toque no cadeado para fechar"
                            else -> "Toque no cadeado acima para fechar o item"
                        },
                        fontSize = 12.sp,
                        color = TintaLegenda,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }

        Spacer(Modifier.height(6.dp))
        EntradaManual(
            rotulo = "Código da caixa",
            textoBotao = "Bipar",
            habilitado = !item.fechado,
            teclado = KeyboardType.Number,
        ) { vm.bipar(it) }
    }

    if (pedindoPallet) {
        DialogoTexto(
            titulo = "Selecionar pallet",
            rotulo = "Código do pallet",
            valorInicial = "P-RED-",
            aoConfirmar = { vm.selecionarPallet(it); pedindoPallet = false },
            aoCancelar = { pedindoPallet = false },
        )
    }

    if (pedindoFusao) {
        DialogoTexto(
            titulo = "Juntar no pallet ${estado.palletAtivo ?: ""}",
            rotulo = "Pallet de origem (será esvaziado)",
            valorInicial = "P-RED-",
            aoConfirmar = { vm.fundirNoPalletAtivo(it); pedindoFusao = false },
            aoCancelar = { pedindoFusao = false },
        )
    }

    if (pedindoExclusao) {
        DialogoExclusao(
            item = item,
            palletAtivo = estado.palletAtivo,
            aoConfirmar = { qtd, pallet ->
                vm.excluirDaSeparacao(qtd, pallet)
                pedindoExclusao = false
            },
            aoCancelar = { pedindoExclusao = false },
        )
    }
}

/**
 * Excluir caixas bipadas por engano.
 *
 * Pede o pallet quando o aparelho não tem um aberto — o vínculo com o pallet
 * mora só na memória do app e se perde ao encerrar ou ao reabrir o aplicativo,
 * e é justamente aí que o operador percebe o erro.
 */
@Composable
private fun DialogoExclusao(
    item: Item,
    palletAtivo: String?,
    aoConfirmar: (Int, String?) -> Unit,
    aoCancelar: () -> Unit,
) {
    var quantidade by remember { mutableStateOf("1") }
    var pallet by remember { mutableStateOf(palletAtivo ?: "P-RED-") }

    AlertDialog(
        onDismissRequest = aoCancelar,
        title = { Text("Excluir caixas", fontSize = 20.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column {
                Text(
                    "${item.produtoCodigo} — ${item.qtdSeparada} de ${item.qtdPrevista} caixas.",
                    fontSize = 15.sp,
                )
                Text(
                    "Reduz a quantidade separada. Cadeado fechado reabre.",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = quantidade,
                    onValueChange = { quantidade = it.filter(Char::isDigit) },
                    label = { Text("Quantidade") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                if (palletAtivo == null) {
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = pallet,
                        onValueChange = { pallet = it.trim().uppercase() },
                        label = { Text("De qual pallet") },
                        singleLine = true,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { aoConfirmar(quantidade.toInt(), pallet) },
                enabled = (quantidade.toIntOrNull() ?: 0) > 0 && pallet.length > 6,
            ) { Text("Excluir", fontSize = 17.sp) }
        },
        dismissButton = { TextButton(onClick = aoCancelar) { Text("Cancelar", fontSize = 17.sp) } },
    )
}

@Composable
private fun DialogoTexto(
    titulo: String,
    rotulo: String,
    valorInicial: String,
    somenteNumero: Boolean = false,
    aoConfirmar: (String) -> Unit,
    aoCancelar: () -> Unit,
) {
    var valor by remember { mutableStateOf(valorInicial) }
    AlertDialog(
        onDismissRequest = aoCancelar,
        title = { Text(titulo, fontSize = 20.sp, fontWeight = FontWeight.Bold) },
        text = {
            OutlinedTextField(
                value = valor,
                onValueChange = { valor = if (somenteNumero) it.filter(Char::isDigit) else it.trim() },
                label = { Text(rotulo) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = if (somenteNumero) KeyboardType.Number else KeyboardType.Text,
                ),
            )
        },
        confirmButton = {
            TextButton(onClick = { aoConfirmar(valor) }, enabled = valor.isNotBlank()) {
                Text("Confirmar", fontSize = 17.sp)
            }
        },
        dismissButton = { TextButton(onClick = aoCancelar) { Text("Cancelar", fontSize = 17.sp) } },
    )
}

/**
 * Criar pallet.
 *
 * Quantidade por botões, sem campo de texto: o campo abria o teclado, e com o
 * teclado aberto a tela de 4" encolhia até o aviso de "criado" ficar espremido
 * por baixo dos botões. Tudo que pode crescer rola; "Criar" fica fixo embaixo.
 */
@Composable
fun TelaCriarPallet(estado: EstadoApp, vm: AppViewModel) {
    var quantidade by remember { mutableStateOf(1) }
    val devolucao = estado.tipoPallet == "DEVOLUCAO"

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Cabecalho(
            "Criar pallet",
            if (devolucao) "Pallet de devolução (P-DEV-)" else "Pallet de separação (P-RED-)",
        )

        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())) {
            // O tipo escolhe a sequência do código. Pallet de devolução não
            // aceita leitura de separação e vice-versa — o servidor recusa.
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                listOf("SEPARACAO" to "Separação", "DEVOLUCAO" to "Devolução").forEach { (tipo, rotulo) ->
                    val ativo = estado.tipoPallet == tipo
                    if (ativo) {
                        Button(
                            onClick = { vm.escolherTipoPallet(tipo) },
                            modifier = Modifier.weight(1f).heightIn(min = 52.dp),
                        ) { Text(rotulo, fontSize = 16.sp, fontWeight = FontWeight.Bold) }
                    } else {
                        OutlinedButton(
                            onClick = { vm.escolherTipoPallet(tipo) },
                            modifier = Modifier.weight(1f).heightIn(min = 52.dp),
                        ) { Text(rotulo, fontSize = 16.sp) }
                    }
                }
            }

            Spacer(Modifier.height(12.dp))
            Text("Quantidade", fontSize = 13.sp, color = Tinta2)
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(
                    onClick = { quantidade = (quantidade - 1).coerceAtLeast(1) },
                    enabled = quantidade > 1,
                    shape = CircleShape,
                    contentPadding = PaddingValues(0.dp),
                    modifier = Modifier.size(56.dp),
                ) { Text("−", fontSize = 26.sp, fontWeight = FontWeight.Bold) }
                Text(
                    "$quantidade",
                    fontSize = 34.sp,
                    fontWeight = FontWeight.ExtraBold,
                    textAlign = TextAlign.Center,
                    style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                    modifier = Modifier.weight(1f),
                )
                OutlinedButton(
                    onClick = { quantidade = (quantidade + 1).coerceAtMost(MAXIMO_PALLETS) },
                    enabled = quantidade < MAXIMO_PALLETS,
                    shape = CircleShape,
                    contentPadding = PaddingValues(0.dp),
                    modifier = Modifier.size(56.dp),
                ) { Text("+", fontSize = 26.sp, fontWeight = FontWeight.Bold) }
            }
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(1, 5, 10, 20).forEach { q ->
                    val ativo = quantidade == q
                    OutlinedButton(
                        onClick = { quantidade = q },
                        border = BorderStroke(if (ativo) 2.dp else 1.dp, if (ativo) VERDE else MaterialTheme.colorScheme.outline),
                        contentPadding = PaddingValues(0.dp),
                        modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    ) {
                        Text("$q", fontSize = 17.sp, fontWeight = if (ativo) FontWeight.ExtraBold else FontWeight.SemiBold)
                    }
                }
            }

            estado.sucesso?.let {
                Card(
                    Modifier.fillMaxWidth().padding(top = 12.dp),
                    shape = FORMA_AVISO,
                    colors = CardDefaults.cardColors(containerColor = VERDE),
                ) { Text(it, Modifier.padding(14.dp), color = Color.White, fontSize = 15.sp) }
            }
            estado.mensagem?.let {
                Card(
                    Modifier.fillMaxWidth().padding(top = 12.dp),
                    shape = FORMA_AVISO,
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                ) {
                    Text(it, Modifier.padding(14.dp), color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 15.sp)
                }
            }

            // Lista simples, não LazyColumn: está dentro da rolagem da tela.
            if (estado.palletsCriados.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                Text("Últimos códigos criados:", fontSize = 13.sp, color = Tinta2)
                estado.palletsCriados.take(10).forEach {
                    Text(
                        it,
                        fontSize = 19.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.padding(vertical = 2.dp),
                    )
                }
                if (estado.palletsCriados.size > 10) {
                    Text("+ ${estado.palletsCriados.size - 10}", fontSize = 13.sp, color = Tinta2)
                }
            }
        }

        Spacer(Modifier.height(8.dp))
        BotaoGrande(
            if (quantidade == 1) "Criar 1 pallet" else "Criar $quantidade pallets",
            habilitado = !estado.carregando,
        ) { vm.criarPallets(quantidade) }
    }
}

/** Limite do servidor por pedido (SeparacaoServico.criarPallets). */
private const val MAXIMO_PALLETS = 50


/**
 * Atualização disponível no servidor. Obrigatória não dispensa: coletor
 * esquecido com versão antiga é fonte de comportamento divergente.
 */
@Composable
fun DialogoAtualizacao(estado: EstadoApp, vm: AppViewModel) {
    val v = estado.atualizacao ?: return
    val baixando = estado.baixandoAtualizacao

    AlertDialog(
        onDismissRequest = { if (!v.obrigatoria && baixando == null) vm.dispensarAtualizacao() },
        title = { Text("Atualização disponível", fontSize = 21.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column {
                Text("Versão ${v.versao} · ${v.tamanho / 1024 / 1024} MB", fontSize = 16.sp)
                if (v.obrigatoria) {
                    Text(
                        "Esta atualização é obrigatória.",
                        fontSize = 14.sp,
                        color = MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                if (baixando != null) {
                    Spacer(Modifier.height(12.dp))
                    Text("Baixando… $baixando%", fontSize = 15.sp)
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { vm.baixarEInstalarAtualizacao() },
                enabled = baixando == null,
            ) { Text(if (baixando == null) "Atualizar" else "Baixando…", fontSize = 17.sp) }
        },
        dismissButton = {
            if (!v.obrigatoria && baixando == null) {
                TextButton(onClick = { vm.dispensarAtualizacao() }) { Text("Depois", fontSize = 17.sp) }
            }
        },
    )
}