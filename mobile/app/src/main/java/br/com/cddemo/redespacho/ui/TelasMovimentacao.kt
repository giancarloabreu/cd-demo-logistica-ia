package br.com.cddemo.redespacho.ui

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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.ConteudoPallet
import br.com.cddemo.redespacho.rede.Repaletizacao
import br.com.cddemo.redespacho.ui.theme.Ambar
import br.com.cddemo.redespacho.ui.theme.AmbarBorda
import br.com.cddemo.redespacho.ui.theme.AmbarClaro
import br.com.cddemo.redespacho.ui.theme.BordaDigitar
import br.com.cddemo.redespacho.ui.theme.Linha
import br.com.cddemo.redespacho.ui.theme.NUMEROS_TABULARES
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.Tinta2
import br.com.cddemo.redespacho.ui.theme.TintaLegenda
import br.com.cddemo.redespacho.ui.theme.Verde
import br.com.cddemo.redespacho.ui.theme.VerdeClaro
import br.com.cddemo.redespacho.ui.theme.Vermelho

private val VERDE = Verde
private val AMBAR = Ambar

/**
 * Repaletização: passar caixas de um pallet para outro, sem item aberto.
 *
 * Um interruptor, dois jeitos. Ligado ("Juntar o pallet inteiro") é a fusão:
 * bipa a origem, bipa o destino, e todas as caixas passam. Desligado é caixa a
 * caixa: bipa a origem, o destino, e cada caixa bipada passa uma. A trava de
 * NF é do servidor nos dois — pallets de notas diferentes no mesmo palete é o
 * erro que o sistema existe para evitar. Ver docs/TELAS-COLETOR.md §5.5.
 */
@Composable
fun TelaTransferencia(estado: EstadoApp, vm: AppViewModel) {
    val passo = estado.passoTransferencia
    val par = estado.repaletizacao
    val origem = par?.origem ?: estado.palletOrigem
    // Caixa já passada no par: trocar de modo ou de destino deixaria os
    // cartões dos dois pallets sem imprimir.
    val porImprimir = estado.repaletizacaoPorImprimir

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Cabecalho(
            "Repaletização",
            if (estado.unirArmado) "junta o pallet inteiro · mesma NF" else "caixa a caixa · mesma NF",
        )
        FaixaEnvio(estado)

        // Tudo rola: na tela de 4" os dois cartões, os avisos e a faixa da
        // caixa sem resposta passam da altura, e nada pode ficar escondido.
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())) {
            estado.mensagem?.let { AvisoErroMov(it) }
            estado.sucesso?.let { AvisoOkMov(it) }
            estado.caixaRepaletizacaoSemResposta?.let { c ->
                FaixaCaixaSemResposta(c, habilitado = !estado.carregando) { vm.reenviarCaixaSemResposta() }
            }

            InterruptorJuntar(armado = estado.unirArmado, travado = porImprimir) { vm.armarUniao(it) }

            if (par != null) {
                // Passo 3: o par está aberto e só caixa conta.
                PainelRepaletizacao(par)
                if (par.origemEsvaziada) {
                    AvisoAtencao(
                        "${par.origem.codigo} ficou vazio e saiu de circulação: descarte o cartão dele. " +
                            "Não há mais caixa a passar — toque em Concluir.",
                    )
                }
                CartaoPallet("Origem", par.origem, maxItens = 3, destacado = par.produtoCodigo)
                CartaoPallet("Destino", par.destino, maxItens = 3, destacado = par.produtoCodigo)
                Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    BotaoSecundario("Trocar origem", Modifier.weight(1f), !estado.carregando) { vm.limparOrigem() }
                    // Destino bipado errado é o engano mais comum; trocar só
                    // enquanto nenhuma caixa passou.
                    if (!porImprimir) {
                        BotaoSecundario("Trocar destino", Modifier.weight(1f), !estado.carregando) { vm.trocarDestino() }
                    }
                }
            } else if (origem != null) {
                // Passo 2: origem na tela, esperando o destino.
                CartaoPallet("Origem", origem)
                TextoPasso(
                    when {
                        estado.unirArmado ->
                            "Agora bipe o pallet de DESTINO. TODAS as caixas de ${origem.codigo} " +
                                "passam para ele e ${origem.codigo} fica vazio."
                        origem.totalCaixas == 0 ->
                            "${origem.codigo} está vazio: não tem caixa para passar. " +
                                "Bipe o pallet de ORIGEM — o que tem as caixas."
                        else ->
                            "Agora bipe o pallet de DESTINO: um da mesma NF" +
                                (origem.notaNumero?.let { " ($it)" } ?: "") +
                                " ou um pallet novo, sem uso. Depois, bipe as caixas uma a uma."
                    },
                    // Âmbar quando o próximo bipe move carga.
                    cor = if (estado.unirArmado) AMBAR else Tinta,
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    BotaoSecundario("Trocar origem", Modifier.weight(1f), !estado.carregando) { vm.limparOrigem() }
                    // Encerrar daqui porque é aqui que se lê qualquer pallet:
                    // o menu da separação só alcança o pallet aberto no aparelho.
                    if (origem.status == "EM_USO") {
                        BotaoSecundario("Encerrar este pallet", Modifier.weight(1f), !estado.carregando) {
                            vm.encerrarPalletPorCodigo(origem.codigo)
                        }
                    }
                }
            } else {
                // Passo 1.
                TextoPasso("Bipe a etiqueta do pallet de ORIGEM — o que vai ceder as caixas.")
            }
        }

        Spacer(Modifier.height(6.dp))
        // Fora da rolagem: desfazer e concluir precisam estar sempre à mão,
        // sem o operador rolar a tela de luva a cada caixa.
        if (par != null) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                BotaoSecundario(
                    "Desfazer última",
                    Modifier.weight(1f),
                    habilitado = !estado.carregando && par.movidas > 0,
                    alturaMinima = 64.dp,
                ) { vm.desfazerRepaletizacao() }
                BotaoGrande("Concluir", Modifier.weight(1f), habilitado = !estado.carregando) {
                    vm.concluirRepaletizacao()
                }
            }
            Spacer(Modifier.height(6.dp))
        }
        EntradaManual(
            rotulo = when (passo) {
                PassoTransferencia.ORIGEM -> "Pallet de origem"
                PassoTransferencia.DESTINO -> "Pallet de destino"
                PassoTransferencia.CAIXAS -> "Código da caixa"
            },
            textoBotao = when (passo) {
                PassoTransferencia.ORIGEM -> "Ler"
                PassoTransferencia.DESTINO -> if (estado.unirArmado) "Juntar" else "Ler"
                PassoTransferencia.CAIXAS -> "Passar"
            },
            // No passo das caixas, habilitado mesmo com leitura no ar: o envio
            // é em fila, na ordem do bipe — como no retorno.
            habilitado = passo == PassoTransferencia.CAIXAS || !estado.carregando,
            teclado = if (passo == PassoTransferencia.CAIXAS) KeyboardType.Number else KeyboardType.Text,
            valorInicial = if (passo == PassoTransferencia.CAIXAS) "" else "P-RED-",
        ) { vm.lerNaTransferencia(it) }
    }

    val saida = estado.saidaRepaletizacao
    if (saida != null && par != null) {
        DialogoSaidaRepaletizacao(
            saida = saida,
            par = par,
            semResposta = estado.caixaRepaletizacaoSemResposta,
            carregando = estado.carregando,
            aoConcluir = { vm.concluirRepaletizacao() },
            aoSairSemImprimir = { vm.sairSemImprimir() },
            aoContinuar = { vm.fecharSaidaRepaletizacao() },
        )
    }
}

/**
 * O interruptor arma a fusão. Ligado, o segundo bipe move TODAS as caixas —
 * por isso âmbar, não verde: é atenção ("o próximo bipe move carga"), não
 * "tudo certo". Desligado, a tela passa caixa a caixa.
 */
@Composable
private fun InterruptorJuntar(armado: Boolean, travado: Boolean, aoMudar: (Boolean) -> Unit) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        border = if (armado) BorderStroke(1.5.dp, AMBAR.copy(alpha = 0.45f)) else BORDA_CARTAO,
        colors = CardDefaults.cardColors(
            containerColor = if (armado) AMBAR.copy(alpha = 0.15f) else MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Juntar o pallet inteiro", fontSize = 16.sp, lineHeight = 20.sp, fontWeight = FontWeight.Bold)
                Text(
                    when {
                        travado -> "conclua a repaletização para trocar"
                        armado -> "o destino recebe TODAS as caixas da origem"
                        else -> "desligado: passa caixa a caixa"
                    },
                    fontSize = 12.sp,
                    lineHeight = 16.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(8.dp))
            Switch(
                checked = armado,
                onCheckedChange = aoMudar,
                enabled = !travado,
                colors = SwitchDefaults.colors(
                    checkedTrackColor = AMBAR,
                    checkedThumbColor = Color.White,
                    checkedBorderColor = AMBAR,
                ),
            )
        }
    }
}

/**
 * O que importa a cada bipe, no alto: a caixa que acabou de passar e os três
 * números. O operador confere de relance, sem ler os cartões dos pallets.
 */
@Composable
private fun PainelRepaletizacao(r: Repaletizacao) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        border = BorderStroke(1.dp, VERDE.copy(alpha = 0.3f)),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text("Bipe as caixas que passam", fontSize = 13.sp, color = Tinta2)
            Text(
                "de ${r.origem.codigo}",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
                color = Tinta,
            )
            Text(
                "para ${r.destino.codigo}",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
                color = Tinta,
            )
            r.produtoCodigo?.let { codigo ->
                Column(
                    Modifier
                        .fillMaxWidth()
                        .padding(top = 8.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(VerdeClaro)
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                ) {
                    Text("Última caixa passada", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = VERDE)
                    Text(codigo, fontSize = 17.sp, fontWeight = FontWeight.ExtraBold, color = Tinta)
                    r.produtoDescricao?.let {
                        Text(
                            it,
                            fontSize = 13.sp,
                            lineHeight = 17.sp,
                            color = Tinta2,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
            Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Numero("origem", "${r.origem.totalCaixas} cx", Modifier.weight(1f))
                Numero("destino", "${r.destino.totalCaixas} cx", Modifier.weight(1f))
                Numero("passadas", "${r.movidas}", Modifier.weight(1f), cor = VERDE)
            }
        }
    }
}

@Composable
private fun Numero(rotulo: String, valor: String, modifier: Modifier, cor: Color = Tinta) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            valor,
            fontSize = 22.sp,
            lineHeight = 26.sp,
            fontWeight = FontWeight.ExtraBold,
            color = cor,
            textAlign = TextAlign.Center,
            style = LocalTextStyle.current.copy(fontFeatureSettings = NUMEROS_TABULARES),
        )
        Text(rotulo, fontSize = 12.sp, color = TintaLegenda, textAlign = TextAlign.Center)
    }
}

/**
 * Cartão do pallet: código grande, NF, total de caixas e endereço. A lista de
 * itens é resumida — os primeiros bastam para reconhecer o palete, e o
 * produto da última caixa passada acende para dizer onde ela entrou.
 */
@Composable
private fun CartaoPallet(rotulo: String, p: ConteudoPallet, maxItens: Int = 4, destacado: String? = null) {
    Card(Modifier.fillMaxWidth().padding(vertical = 6.dp), border = BORDA_CARTAO) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Text(rotulo, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(p.codigo, fontSize = 21.sp, fontWeight = FontWeight.ExtraBold, fontFamily = FontFamily.Monospace)
            Text(
                "NF ${p.notaNumero ?: "—"} · ${p.cliente ?: "sem nota ainda"}",
                fontSize = 14.sp,
                lineHeight = 18.sp,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                "${p.totalCaixas} caixa(s) · ${p.endereco ?: "sem endereço"} · ${p.status.lowercase()}",
                fontSize = 13.sp,
                lineHeight = 17.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            // Lista simples, não LazyColumn: o cartão mora dentro de uma tela
            // que rola, e lista preguiçosa dentro de rolagem não se mede.
            if (p.itens.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                p.itens.take(maxItens).forEach { i ->
                    val aceso = destacado != null && i.produtoCodigo == destacado
                    Text(
                        "${i.quantidade}× ${i.produtoCodigo} ${i.produtoDescricao}",
                        fontSize = 13.sp,
                        fontWeight = if (aceso) FontWeight.Bold else FontWeight.Normal,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 1.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(if (aceso) VerdeClaro else Color.Transparent)
                            .padding(horizontal = 4.dp, vertical = 1.dp),
                    )
                }
                if (p.itens.size > maxItens) {
                    Text(
                        "+ ${p.itens.size - maxItens} item(ns)",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

/**
 * Caixa bipada que ficou sem resposta. Fica à vista até se resolver, dizendo
 * de qual par ela é: enquanto existir, nenhum bipe novo é enviado antes dela.
 */
@Composable
private fun FaixaCaixaSemResposta(c: CaixaRepaletizacaoSemResposta, habilitado: Boolean, aoReenviar: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        shape = FORMA_AVISO,
        border = BorderStroke(1.5.dp, Vermelho.copy(alpha = 0.4f)),
        colors = CardDefaults.cardColors(containerColor = Vermelho.copy(alpha = 0.08f)),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text("Caixa sem resposta do servidor", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Vermelho)
            Text(
                "Código ${c.codigoLido}, de ${c.origem} para ${c.destino}. Pode ter passado ou não: é " +
                    "reenviada sozinha antes do próximo bipe, sem passar duas vezes. NÃO bipe esta caixa de novo.",
                fontSize = 14.sp,
                lineHeight = 19.sp,
                color = Tinta,
                modifier = Modifier.padding(top = 2.dp),
            )
            BotaoSecundario(
                "Reenviar agora",
                Modifier.fillMaxWidth().padding(top = 8.dp),
                habilitado = habilitado,
                aoClicar = aoReenviar,
            )
        }
    }
}

/**
 * Sair (ou trocar a origem) com caixa passada e cartões por imprimir. O
 * cartão velho mente sobre a quantidade, então a saída normal é concluir;
 * sair sem imprimir existe, mas dito com todas as letras.
 */
@Composable
private fun DialogoSaidaRepaletizacao(
    saida: SaidaRepaletizacao,
    par: Repaletizacao,
    semResposta: CaixaRepaletizacaoSemResposta?,
    carregando: Boolean,
    aoConcluir: () -> Unit,
    aoSairSemImprimir: () -> Unit,
    aoContinuar: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = aoContinuar,
        title = { Text("Imprimir os cartões?", fontSize = 20.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(
                    "Passadas de ${par.origem.codigo} para ${par.destino.codigo}: ${par.movidas} caixa(s). " +
                        "Os cartões dos dois pallets ainda mostram as quantidades antigas.",
                    fontSize = 15.sp,
                    lineHeight = 20.sp,
                )
                semResposta?.let {
                    Text(
                        "A caixa ${it.codigoLido} ainda não foi confirmada pelo servidor. Concluir reenvia ela " +
                            "antes de imprimir.",
                        fontSize = 15.sp,
                        lineHeight = 20.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = Vermelho,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
                Spacer(Modifier.height(14.dp))
                BotaoGrande("Concluir e imprimir", habilitado = !carregando, aoClicar = aoConcluir)
                // Trocar a origem com caixa sem resposta não tem saída sem
                // concluir: o par novo esconderia a caixa pendente.
                if (semResposta == null || saida == SaidaRepaletizacao.VOLTAR) {
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(
                        onClick = aoSairSemImprimir,
                        shape = CircleShape,
                        border = BorderStroke(1.5.dp, Vermelho.copy(alpha = 0.5f)),
                        colors = ButtonDefaults.outlinedButtonColors(containerColor = Color.White, contentColor = Vermelho),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                    ) {
                        Text(
                            when {
                                semResposta != null -> "Sair e voltar depois"
                                saida == SaidaRepaletizacao.VOLTAR -> "Sair sem imprimir"
                                else -> "Trocar sem imprimir"
                            },
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold,
                            textAlign = TextAlign.Center,
                        )
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = aoContinuar) { Text("Continuar aqui", fontSize = 17.sp) }
        },
    )
}

@Composable
internal fun TextoPasso(texto: String, cor: Color = Tinta) {
    Text(
        texto,
        fontSize = 15.sp,
        lineHeight = 20.sp,
        fontWeight = FontWeight.SemiBold,
        color = cor,
        modifier = Modifier.padding(vertical = 8.dp),
    )
}

/** Botão de contorno das ações secundárias. Altura mínima, nunca fixa: o texto quebra. */
@Composable
private fun BotaoSecundario(
    texto: String,
    modifier: Modifier = Modifier,
    habilitado: Boolean = true,
    alturaMinima: Dp = 52.dp,
    aoClicar: () -> Unit,
) {
    OutlinedButton(
        onClick = aoClicar,
        enabled = habilitado,
        shape = CircleShape,
        border = BorderStroke(1.5.dp, if (habilitado) BordaDigitar else Linha),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = Color.White,
            contentColor = Tinta,
            disabledContainerColor = Color.White.copy(alpha = 0.6f),
            disabledContentColor = Tinta2.copy(alpha = 0.6f),
        ),
        modifier = modifier.heightIn(min = alturaMinima),
    ) { Text(texto, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center) }
}

/**
 * Reimprimir a etiqueta de um pallet.
 *
 * O cartão e a etiqueta se perdem no transporte e na câmara fria, e sequencial
 * não se reaproveita — reimprimir é a saída, não renumerar.
 */
@Composable
fun TelaReimpressao(estado: EstadoApp, vm: AppViewModel) {

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Cabecalho("Reimprimir", "bipe a etiqueta ou digite o número")
        FaixaEnvio(estado)

        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())) {
            estado.mensagem?.let { AvisoErroMov(it) }
            estado.sucesso?.let { AvisoOkMov(it) }

            // São dois papéis diferentes, e o operador precisa dos dois: a
            // etiqueta identifica o palete, o cartão diz o que tem dentro.
            Text("O que imprimir", fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(
                    "ETIQUETA" to "Etiqueta 80×50",
                    "CARTAO" to "Cartão 80×220",
                ).forEach { (tipo, rotulo) ->
                    val ativo = estado.tipoImpressao == tipo
                    Card(
                        Modifier
                            .weight(1f)
                            .clickable { vm.escolherTipoImpressao(tipo) },
                        border = if (ativo) BorderStroke(2.dp, VERDE) else BORDA_CARTAO,
                        colors = CardDefaults.cardColors(
                            containerColor = if (ativo) MaterialTheme.colorScheme.primaryContainer
                            else MaterialTheme.colorScheme.surfaceVariant,
                        ),
                    ) {
                        Column(Modifier.padding(10.dp)) {
                            Text(
                                rotulo,
                                fontSize = 14.sp,
                                fontWeight = if (ativo) FontWeight.Bold else FontWeight.Normal,
                            )
                            Text(
                                if (tipo == "ETIQUETA") "código de barras" else "itens do palete",
                                fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
            Text(
                "Bipar a etiqueta imprime na hora, no tipo escolhido acima.",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 10.dp),
            )
        }

        Spacer(Modifier.height(6.dp))
        EntradaManual(
            rotulo = "Código do pallet",
            textoBotao = "Imprimir",
            habilitado = !estado.carregando,
            valorInicial = "P-RED-",
        ) { vm.reimprimir(it) }
    }
}

// --- apoio -------------------------------------------------------------------

@Composable
internal fun AvisoErroMov(texto: String) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        shape = FORMA_AVISO,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
    ) {
        Text(texto, Modifier.padding(12.dp), color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 15.sp)
    }
}

@Composable
internal fun AvisoOkMov(texto: String) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        shape = FORMA_AVISO,
        colors = CardDefaults.cardColors(containerColor = VERDE),
    ) { Text(texto, Modifier.padding(12.dp), color = Color.White, fontSize = 15.sp) }
}

/** Atenção sem ser erro: a origem esvaziou, e o operador precisa agir no papel. */
@Composable
private fun AvisoAtencao(texto: String) {
    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        shape = FORMA_AVISO,
        border = BorderStroke(1.dp, AmbarBorda),
        colors = CardDefaults.cardColors(containerColor = AmbarClaro),
    ) {
        Text(
            texto,
            Modifier.padding(12.dp),
            color = Tinta,
            fontSize = 15.sp,
            lineHeight = 20.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}
