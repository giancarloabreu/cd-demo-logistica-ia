package br.com.cddemo.redespacho.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.ProdutoRetorno
import br.com.cddemo.redespacho.rede.RetornoPallet
import br.com.cddemo.redespacho.rede.RetornoResumo
import br.com.cddemo.redespacho.ui.theme.Ambar
import br.com.cddemo.redespacho.ui.theme.Linha
import br.com.cddemo.redespacho.ui.theme.NUMEROS_TABULARES
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.Tinta2
import br.com.cddemo.redespacho.ui.theme.TintaLegenda
import br.com.cddemo.redespacho.ui.theme.Verde
import br.com.cddemo.redespacho.ui.theme.VerdeClaro
import br.com.cddemo.redespacho.ui.theme.Vermelho

/**
 * Conferência de retorno no coletor.
 *
 * Retorno é NF nossa, já expedida, que voltou ao armazém — o cliente recusou
 * a entrega inteira ou alguns pallets voltaram para reentrega. A retaguarda
 * registra quais pallets voltaram; aqui se confere caixa a caixa contra o que
 * cada pallet levou na expedição. Espelha a devolução — lista → pallet →
 * conferência, cadeado, falta com justificativa — e a tratativa (reentrega,
 * refaturamento, descarte) fica na retaguarda.
 */

/** Cartão do retorno na lista "Conferir", acima das devoluções. */
@Composable
internal fun CartaoRetorno(r: RetornoResumo, aoClicar: () -> Unit) {
    val todosFechados = r.qtdPallets > 0 && r.palletsFechados >= r.qtdPallets

    Card(
        Modifier.fillMaxWidth().padding(vertical = 5.dp).clickable(onClick = aoClicar),
        border = BORDA_CARTAO,
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("NF ${r.notaNumero}", fontSize = 19.sp, fontWeight = FontWeight.Bold)
                Text(r.cliente, fontSize = 15.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    r.motivo,
                    fontSize = 13.sp,
                    lineHeight = 17.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    "${r.palletsFechados}/${r.qtdPallets}",
                    fontSize = 19.sp,
                    fontWeight = FontWeight.ExtraBold,
                    style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                    color = if (todosFechados) Verde else Tinta,
                )
                Text("pallets", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/**
 * Pallets de um retorno. Bipar a etiqueta abre o pallet direto; tocar também
 * — é por aqui que se fecha o pallet que nem voltou, sem bipe nenhum.
 */
@Composable
fun TelaRetorno(estado: EstadoApp, vm: AppViewModel) {
    val r = estado.retorno ?: return
    val pallets = estado.palletsRetorno
    val faltam = pallets.count { !it.fechado }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Cabecalho("Retorno NF ${r.notaNumero}", r.cliente)
        Text(
            "Motivo: ${r.motivo}",
            fontSize = 13.sp,
            lineHeight = 17.sp,
            color = Tinta2,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            when (r.status) {
                "AGUARDANDO_TRATATIVA" -> "Conferência encerrada — a tratativa é na retaguarda"
                "TRATADO" -> "Retorno já tratado na retaguarda"
                "CANCELADO" -> "Retorno cancelado na retaguarda"
                else -> if (faltam == 0) {
                    "Todos os pallets fechados"
                } else {
                    "$faltam de ${pallets.size} pallet(s) em aberto · bipe a etiqueta ou toque no pallet"
                }
            },
            fontSize = 12.sp,
            lineHeight = 16.sp,
            fontWeight = if (r.status == "EM_CONFERENCIA") FontWeight.Normal else FontWeight.SemiBold,
            color = TintaLegenda,
            modifier = Modifier.padding(top = 2.dp),
        )

        estado.sucesso?.let { AvisoOk(it) }
        estado.mensagem?.let { AvisoErro(it) }

        LazyColumn(Modifier.weight(1f)) {
            items(pallets, key = { it.id }) { p -> CartaoPalletRetorno(p) { vm.abrirPalletRetorno(p) } }
        }
    }
}

/**
 * Pallet em aberto leva o vermelho discreto das pendências; conferido é verde;
 * com falta é âmbar, a cor do cadeado com divergência.
 */
@Composable
private fun CartaoPalletRetorno(p: RetornoPallet, aoClicar: () -> Unit) {
    val cor = when {
        p.comFalta -> Ambar
        p.fechado -> Verde
        else -> Vermelho
    }
    val situacao = when {
        p.comFalta -> "com falta de ${(p.qtdEsperada - p.qtdConferida).coerceAtLeast(0)} cx"
        p.fechado -> if (p.manual) "conferido · manual" else "conferido"
        p.qtdConferida > 0 -> "em conferência"
        else -> "em aberto"
    }

    Card(
        Modifier.fillMaxWidth().padding(vertical = 5.dp).clickable(onClick = aoClicar),
        colors = CardDefaults.cardColors(containerColor = cor.copy(alpha = if (p.fechado) 0.10f else 0.07f)),
        border = BorderStroke(1.dp, cor.copy(alpha = 0.22f)),
    ) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            // Na lista o cadeado só informa, e sem o estado "pronto": completo
            // sem cadeado fechado ainda é pallet em aberto.
            Cadeado(estadoCadeado(p, mostrarPronto = false), tamanho = 36.dp, modifier = Modifier.padding(end = 12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    p.palletCodigo,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = Tinta,
                    maxLines = 1,
                )
                Text(situacao, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = cor)
                p.justificativa?.let {
                    Text(
                        "“$it”",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Spacer(Modifier.width(8.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    "${p.qtdConferida}/${p.qtdEsperada}",
                    fontSize = 19.sp,
                    fontWeight = FontWeight.ExtraBold,
                    style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                    color = if (p.fechado) cor else Tinta,
                )
                Text("caixas", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

private fun estadoCadeado(p: RetornoPallet, mostrarPronto: Boolean): EstadoCadeado = when {
    p.comFalta -> EstadoCadeado.DIVERGENCIA
    p.fechado -> EstadoCadeado.FECHADO
    mostrarPronto && p.qtdConferida >= p.qtdEsperada -> EstadoCadeado.PRONTO
    else -> EstadoCadeado.ABERTO
}

/**
 * Conferência de um pallet que voltou. Mesmo desenho da conferência de
 * devolução: cadeado como controle, contador no meio, digitação como plano B.
 * O número é sempre o do servidor — a tela não soma antes da resposta.
 */
@Composable
fun TelaConferenciaRetorno(estado: EstadoApp, vm: AppViewModel) {
    val p = estado.palletRetorno ?: return
    var menuAberto by remember { mutableStateOf(false) }
    // Preso ao pallet: bipar a etiqueta de outro com o diálogo aberto troca o
    // pallet da tela, e o diálogo não pode seguir aberto com os números dele.
    var pedindoJustificativa by remember(p.id) { mutableStateOf(false) }

    val completo = p.qtdConferida >= p.qtdEsperada
    // Cancelado ou já fechado na retaguarda: a tela não finge que ainda confere.
    val situacao = estado.retorno?.status
    val emConferencia = situacao == null || situacao == "EM_CONFERENCIA"

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            // voltar(), e não irPara: é ele que recarrega os pallets do retorno.
            BotaoIcone(Icone.VOLTAR, "Voltar aos pallets do retorno") { vm.voltar() }
            Text(
                "Retorno NF ${estado.retorno?.notaNumero.orEmpty()}",
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            // O cadeado é o controle, como na separação e na devolução.
            // Fechado, continua tocável: explica que reabrir é na retaguarda
            // em vez de parecer um botão morto.
            Cadeado(
                estadoCadeado(p, mostrarPronto = true),
                habilitado = !estado.carregando && (emConferencia || p.fechado),
                aoTocar = {
                    when {
                        p.fechado -> vm.avisarPalletRetornoFechado()
                        completo -> vm.fecharPalletRetorno(p.id)
                        else -> pedindoJustificativa = true
                    }
                },
            )
            Box {
                BotaoIcone(Icone.MAIS, "Mais opções") { menuAberto = true }
                DropdownMenu(expanded = menuAberto, onDismissRequest = { menuAberto = false }) {
                    DropdownMenuItem(
                        text = { Text("Desfazer última leitura") },
                        enabled = !p.fechado && p.qtdConferida > 0,
                        onClick = { menuAberto = false; vm.desfazerUltimaLeituraRetorno() },
                    )
                    DropdownMenuItem(
                        text = { Text("Reimprimir cartão") },
                        onClick = { menuAberto = false; vm.reimprimirCartaoRetorno() },
                    )
                }
            }
        }

        // Rola: mesma razão das outras telas de bipe — na tela de 4" um aviso
        // ou um pallet com muitos produtos empurrava o "Digitar" para fora.
        // Quando cabe, o contador fica no meio da altura.
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
                        p.palletCodigo,
                        fontSize = 21.sp,
                        fontWeight = FontWeight.ExtraBold,
                        fontFamily = FontFamily.Monospace,
                        color = Tinta,
                        maxLines = 1,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        estado.retorno?.cliente.orEmpty(),
                        fontSize = 13.sp,
                        lineHeight = 17.sp,
                        color = Color(0xFF3A4C53),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    estado.mensagem?.let { AvisoErro(it) }
                    estado.sucesso?.let { AvisoOk(it) }
                }

                Column(
                    Modifier.fillMaxWidth().padding(vertical = 8.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    ContadorMola(
                        atual = p.qtdConferida,
                        previsto = p.qtdEsperada,
                        unidade = "caixas",
                        chave = p.id,
                        tamanho = 54.sp,
                    )
                    Spacer(Modifier.height(8.dp))
                    BarraOndulada(
                        fracao = if (p.qtdEsperada > 0) p.qtdConferida.toFloat() / p.qtdEsperada else 0f,
                        ondular = !p.fechado,
                        modifier = Modifier.fillMaxWidth(0.92f),
                    )
                }

                Column(Modifier.fillMaxWidth()) {
                    // Só os produtos deste pallet: é contra eles que o servidor
                    // confere, e a falta aparece produto a produto.
                    p.produtos.forEach { pr ->
                        LinhaProdutoRetorno(
                            pr,
                            destacado = pr.produtoCodigo == estado.produtoLidoRetorno,
                            palletFechado = p.fechado,
                        )
                    }
                    Text(
                        when {
                            !emConferencia ->
                                "Retorno ${situacao.orEmpty().replace('_', ' ').lowercase()} — não aceita mais leitura"
                            p.comFalta -> "Pallet fechado com falta — reabrir é na retaguarda"
                            p.fechado -> "Pallet fechado — reabrir é na retaguarda"
                            completo -> "Completo — toque no cadeado para fechar"
                            p.qtdConferida == 0 -> "Bipe as caixas. Pallet não voltou? Toque no cadeado"
                            else -> "Faltando caixa? Toque no cadeado e justifique"
                        },
                        fontSize = 12.sp,
                        color = TintaLegenda,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    )
                }
            }
        }

        Spacer(Modifier.height(6.dp))
        // Habilitado mesmo com leitura no ar: o envio é em fila, na ordem.
        EntradaManual(
            rotulo = "Código da caixa",
            textoBotao = "Bipar",
            habilitado = !p.fechado && emConferencia,
            teclado = KeyboardType.Number,
        ) { vm.biparRetorno(it) }
    }

    if (pedindoJustificativa) {
        DialogoFaltaRetorno(
            conferidas = p.qtdConferida,
            esperadas = p.qtdEsperada,
            aoConfirmar = { vm.fecharPalletRetorno(p.id, it); pedindoJustificativa = false },
            aoCancelar = { pedindoJustificativa = false },
        )
    }
}

/** Linha compacta do produto: cabe três ou quatro sem rolar na tela de 4". */
@Composable
private fun LinhaProdutoRetorno(pr: ProdutoRetorno, destacado: Boolean, palletFechado: Boolean) {
    val falta = pr.esperada - pr.conferida
    val cor = when {
        falta <= 0 -> Verde
        palletFechado -> Ambar
        else -> Tinta
    }

    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp)
            .clip(RoundedCornerShape(12.dp))
            // A linha do último produto bipado acende: diz de relance em qual
            // produto a caixa contou, sem ler o código.
            .background(if (destacado) VerdeClaro else Color.Transparent)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(pr.produtoCodigo, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Tinta, maxLines = 1)
            Text(
                pr.produtoDescricao,
                fontSize = 12.sp,
                lineHeight = 15.sp,
                color = Tinta2,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(8.dp))
        Column(horizontalAlignment = Alignment.End) {
            Text(
                "${pr.conferida}/${pr.esperada}",
                fontSize = 16.sp,
                fontWeight = FontWeight.ExtraBold,
                style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
                color = cor,
            )
            if (falta > 0) {
                Text(
                    "falta $falta",
                    fontSize = 11.sp,
                    fontWeight = if (palletFechado) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (palletFechado) Ambar else TintaLegenda,
                )
            }
        }
    }
}

/**
 * Fechar pallet com falta. Motivos prontos a um toque: digitar de luva, na
 * câmara, é o último recurso. Com 0 caixas o primeiro motivo é o pallet que
 * nem voltou.
 */
@Composable
private fun DialogoFaltaRetorno(
    conferidas: Int,
    esperadas: Int,
    aoConfirmar: (String) -> Unit,
    aoCancelar: () -> Unit,
) {
    var valor by remember { mutableStateOf("") }
    val sugestoes = if (conferidas == 0) {
        listOf("Pallet não voltou", "Extravio no transporte", "Caixa avariada")
    } else {
        listOf("Extravio no transporte", "Caixa avariada")
    }

    AlertDialog(
        onDismissRequest = aoCancelar,
        title = { Text("Fechar com falta", fontSize = 20.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(
                    "Conferido $conferidas de $esperadas caixas. " +
                        "Faltam ${(esperadas - conferidas).coerceAtLeast(0)} — informe o motivo.",
                    fontSize = 15.sp,
                )
                Spacer(Modifier.height(10.dp))
                sugestoes.forEach { s ->
                    val escolhida = valor == s
                    OutlinedButton(
                        onClick = { valor = s },
                        shape = CircleShape,
                        border = BorderStroke(if (escolhida) 2.dp else 1.dp, if (escolhida) Verde else Linha),
                        colors = ButtonDefaults.outlinedButtonColors(
                            containerColor = if (escolhida) VerdeClaro else Color.White,
                            contentColor = Tinta,
                        ),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                    ) {
                        Text(
                            s,
                            fontSize = 15.sp,
                            fontWeight = if (escolhida) FontWeight.Bold else FontWeight.Medium,
                            textAlign = TextAlign.Center,
                        )
                    }
                    Spacer(Modifier.height(6.dp))
                }
                OutlinedTextField(
                    value = valor,
                    onValueChange = { valor = it },
                    label = { Text("Justificativa") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { aoConfirmar(valor.trim()) }, enabled = valor.isNotBlank()) {
                Text("Fechar pallet", fontSize = 17.sp)
            }
        },
        dismissButton = { TextButton(onClick = aoCancelar) { Text("Cancelar", fontSize = 17.sp) } },
    )
}
