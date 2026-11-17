package br.com.cddemo.redespacho.ui

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.cddemo.redespacho.rede.Instrucao
import br.com.cddemo.redespacho.ui.theme.BordaDigitar
import br.com.cddemo.redespacho.ui.theme.Linha
import br.com.cddemo.redespacho.ui.theme.Tinta
import br.com.cddemo.redespacho.ui.theme.Tinta2
import br.com.cddemo.redespacho.ui.theme.Trilho
import br.com.cddemo.redespacho.ui.theme.Verde
import br.com.cddemo.redespacho.ui.theme.VerdeClaro
import br.com.cddemo.redespacho.ui.theme.Vermelho

/**
 * Alvos grandes por exigência de operação: o aparelho é usado de pé, com luva,
 * dentro de câmara fria. O padrão Android de 48 dp é pequeno demais.
 * Ver docs/TELAS-COLETOR.md §0.
 */
private val ALTURA_BOTAO = 64.dp

/** Raio dos cartões da prévia. Os avisos usam um pouco menos: são mais baixos. */
internal val RAIO_CARTAO = 22.dp
internal val FORMA_AVISO = RoundedCornerShape(18.dp)

/**
 * Borda fina dos cartões das telas sólidas. Cartão branco sobre fundo quase
 * branco precisa de um fio para ser visto com a tela embaçada; sombra não
 * serve, some no brilho da câmara.
 */
internal val BORDA_CARTAO = BorderStroke(1.dp, Linha)

/**
 * Cabeçalho único das telas internas.
 *
 * Sem botão "Voltar": numa tela de 4 polegadas ele roubava a largura do título
 * e ainda competia com o botão físico de voltar do Android, que já é tratado
 * pelo BackHandler da Activity. Um caminho de volta, não dois.
 */
@Composable
fun Cabecalho(titulo: String, subtitulo: String? = null) {
    Column(Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        Text(
            titulo,
            fontSize = 19.sp,
            fontWeight = FontWeight.ExtraBold,
            letterSpacing = (-0.2).sp,
            color = Tinta,
            maxLines = 2,
        )
        subtitulo?.let {
            Text(it, fontSize = 13.sp, color = Tinta2)
        }
    }
}

/**
 * Botão principal: pílula verde de 64 dp.
 *
 * Afunda um pouco com mola ao ser apertado — de luva não se sente o toque, e
 * o botão que responde ao dedo evita o segundo toque "para garantir".
 */
@Composable
fun BotaoGrande(
    texto: String,
    modifier: Modifier = Modifier,
    habilitado: Boolean = true,
    aoClicar: () -> Unit,
) {
    val animar = lembrarAnimacoes()
    val toque = remember { MutableInteractionSource() }
    val apertado by toque.collectIsPressedAsState()
    val escala by animateFloatAsState(
        if (apertado && animar) 0.96f else 1f,
        if (animar) spring(dampingRatio = 0.5f, stiffness = Spring.StiffnessMedium) else snap(),
        label = "escalaBotao",
    )
    Button(
        onClick = aoClicar,
        enabled = habilitado,
        shape = CircleShape,
        interactionSource = toque,
        colors = ButtonDefaults.buttonColors(
            containerColor = Verde,
            contentColor = Color.White,
            disabledContainerColor = Trilho,
            disabledContentColor = Tinta2,
        ),
        // Altura mínima, não fixa: com a fonte do sistema aumentada o texto
        // quebra em duas linhas, e altura fixa cortava a segunda ao meio.
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = ALTURA_BOTAO)
            .graphicsLayer {
                scaleX = escala
                scaleY = escala
            },
    ) {
        Text(texto, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
    }
}

/**
 * Digitação como plano B, atrás de um botão baixo.
 *
 * O caminho normal é o gatilho: o operador não toca na tela para bipar e o
 * teclado não abre. Um campo sempre visível convidava o toque, abria o teclado
 * e custava ~64 dp em toda tela — na tela de 4" do TC8300 era ele que empurrava
 * o conteúdo para a rolagem. Aqui é um botão de 52 dp que abre um diálogo.
 */
@Composable
fun EntradaManual(
    rotulo: String,
    textoBotao: String,
    habilitado: Boolean = true,
    teclado: KeyboardType = KeyboardType.Text,
    valorInicial: String = "",
    modifier: Modifier = Modifier.fillMaxWidth(),
    aoEnviar: (String) -> Unit,
) {
    var aberto by remember { mutableStateOf(false) }
    OutlinedButton(
        onClick = { aberto = true },
        enabled = habilitado,
        shape = CircleShape,
        border = BorderStroke(1.5.dp, if (habilitado) BordaDigitar else Linha),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = Color.White,
            contentColor = Tinta,
            disabledContainerColor = Color.White.copy(alpha = 0.6f),
            disabledContentColor = Tinta2.copy(alpha = 0.6f),
        ),
        modifier = modifier.heightIn(min = 52.dp),
    ) { Text("⌨  Digitar", fontSize = 15.sp, fontWeight = FontWeight.SemiBold) }

    if (aberto) {
        var valor by remember { mutableStateOf(valorInicial) }
        val enviar: () -> Unit = {
            val v = valor.trim()
            if (v.isNotEmpty()) {
                aberto = false
                aoEnviar(v)
            }
        }
        AlertDialog(
            onDismissRequest = { aberto = false },
            title = { Text(rotulo, fontSize = 18.sp, fontWeight = FontWeight.Bold) },
            text = {
                OutlinedTextField(
                    value = valor,
                    onValueChange = {
                        valor = if (teclado == KeyboardType.Number) it.filter(Char::isDigit)
                        else it.uppercase()
                    },
                    singleLine = true,
                    shape = RoundedCornerShape(14.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = teclado, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { enviar() }),
                )
            },
            confirmButton = {
                TextButton(onClick = enviar, enabled = valor.isNotBlank()) {
                    Text(textoBotao, fontSize = 17.sp)
                }
            },
            dismissButton = {
                TextButton(onClick = { aberto = false }) { Text("Cancelar", fontSize = 17.sp) }
            },
        )
    }
}

/**
 * Avisos sólidos, mesmo sobre o vidro: erro e sucesso têm de ser lidos na
 * primeira olhada, e texto sobre fundo translúcido não garante isso.
 */
@Composable
private fun Avisos(mensagem: String?, sucesso: String?) {
    mensagem?.let {
        Card(
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            shape = FORMA_AVISO,
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
        ) {
            Text(
                it,
                modifier = Modifier.padding(16.dp),
                color = MaterialTheme.colorScheme.onErrorContainer,
                fontSize = 16.sp,
            )
        }
    }
    sucesso?.let {
        Card(
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            shape = FORMA_AVISO,
            colors = CardDefaults.cardColors(containerColor = Verde),
        ) {
            Text(it, modifier = Modifier.padding(16.dp), color = Color.White, fontSize = 16.sp)
        }
    }
}

@Composable
fun TelaConfiguracao(estado: EstadoApp, vm: AppViewModel) {
    // Rola: o aviso do "Testar conexão" aparece no meio da tela e empurrava o
    // "Salvar e continuar" para fora — botão que some justo depois do teste dar
    // certo é o pior momento possível.
    Column(
        Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Configuração", fontSize = 30.sp, fontWeight = FontWeight.ExtraBold, color = Tinta)
        Text(
            "Bipe o QR da aba Coletores, ou digite o endereço",
            fontSize = 15.sp,
            color = Tinta2,
        )
        Spacer(Modifier.height(24.dp))

        OutlinedTextField(
            value = estado.servidor,
            onValueChange = vm::aoMudarServidor,
            label = { Text("IP do servidor") },
            placeholder = { Text("192.168.0.10") },
            singleLine = true,
            shape = RoundedCornerShape(18.dp),
            textStyle = MaterialTheme.typography.headlineSmall,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = estado.porta,
            onValueChange = vm::aoMudarPorta,
            label = { Text("Porta") },
            singleLine = true,
            shape = RoundedCornerShape(18.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            textStyle = MaterialTheme.typography.headlineSmall,
            modifier = Modifier.fillMaxWidth(),
        )

        Avisos(estado.mensagem, estado.sucesso)
        Spacer(Modifier.height(16.dp))

        OutlinedButton(
            onClick = vm::testarConexao,
            enabled = !estado.carregando && estado.servidor.isNotBlank(),
            shape = CircleShape,
            border = BorderStroke(1.5.dp, BordaDigitar),
            colors = ButtonDefaults.outlinedButtonColors(containerColor = Color.White, contentColor = Tinta),
            modifier = Modifier.fillMaxWidth().heightIn(min = ALTURA_BOTAO),
        ) { Text("Testar conexão", fontSize = 18.sp) }

        Spacer(Modifier.height(12.dp))
        BotaoGrande(
            "Salvar e continuar",
            habilitado = !estado.carregando && estado.servidor.isNotBlank(),
            aoClicar = vm::salvarConfiguracao,
        )
    }
}

/**
 * Login sobre o fundo de câmara fria (pintado pela Activity). Os campos são o
 * vidro; o botão "Entrar" é sólido, porque é a ação.
 */
@Composable
fun TelaLogin(estado: EstadoApp, vm: AppViewModel) {
    var senha by remember { mutableStateOf("") }
    val formaCampo = RoundedCornerShape(18.dp)

    // Rola, mas centralizado quando cabe: com o aviso de erro de conexão o
    // conteúdo passa da altura da tela de 4", e sem rolagem o "Entrar" e o
    // atalho do servidor — justo o que resolve o erro — ficavam inalcançáveis.
    // A altura mínima igual à da tela mantém o centro quando não há aviso.
    BoxWithConstraints(Modifier.fillMaxSize()) {
        Column(
            Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .heightIn(min = maxHeight)
                .padding(24.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                "Separação",
                fontSize = 34.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-0.5).sp,
                color = Tinta,
            )
            Text(
                "Redespacho · CD Demo",
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                color = Verde,
            )
            Spacer(Modifier.height(32.dp))

            OutlinedTextField(
                value = estado.login,
                onValueChange = vm::aoMudarLogin,
                label = { Text("Usuário") },
                singleLine = true,
                shape = formaCampo,
                colors = coresCampoVidro(),
                textStyle = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = senha,
                onValueChange = { senha = it.filter(Char::isDigit) },
                label = { Text("Senha") },
                singleLine = true,
                shape = formaCampo,
                colors = coresCampoVidro(),
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                textStyle = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.fillMaxWidth(),
            )

            Avisos(estado.mensagem, estado.sucesso)
            Spacer(Modifier.height(24.dp))

            BotaoGrande(
                if (estado.carregando) "Entrando..." else "Entrar",
                habilitado = !estado.carregando && estado.login.isNotBlank() && senha.isNotBlank(),
            ) { vm.entrar(senha) }

            Spacer(Modifier.height(8.dp))
            TextButton(
                onClick = { vm.irPara(Tela.CONFIGURACAO) },
                modifier = Modifier.align(Alignment.CenterHorizontally),
            ) {
                // Tinta principal, não a secundária: este texto fica sobre a luz
                // âmbar do fundo, onde a Tinta2 dava só ~4,4:1.
                Text(
                    if (estado.servidor.isBlank()) "Configurar servidor"
                    else "Servidor: ${estado.servidor}:${estado.porta}",
                    fontSize = 14.sp,
                    color = Tinta,
                )
            }
        }
    }
}

/**
 * Cartão de ação do menu, em vidro. Dois por linha cabem na tela do TC8300 sem
 * apertar o alvo: o texto encolhe, a área de toque não.
 */
@Composable
private fun CartaoAcao(
    titulo: String,
    detalhe: String,
    modifier: Modifier = Modifier,
    habilitado: Boolean = true,
    icone: Icone? = null,
    aoClicar: () -> Unit,
) {
    Column(
        modifier
            // Ocupa a altura da linha: com a linha em IntrinsicSize.Min, o par
            // fica da altura do cartão mais alto (um título que quebre em duas
            // linhas com a fonte do sistema maior) e os dois lado a lado não
            // ficam desiguais.
            .fillMaxHeight()
            .heightIn(min = 84.dp)
            .vidro(RoundedCornerShape(RAIO_CARTAO))
            .clickable(enabled = habilitado, role = Role.Button, onClick = aoClicar)
            .padding(start = 12.dp, top = 12.dp, end = 12.dp, bottom = 11.dp)
            .alpha(if (habilitado) 1f else 0.5f),
        verticalArrangement = Arrangement.SpaceBetween,
    ) {
        icone?.let { IconeTraco(it, Verde, Modifier.size(20.dp)) }
        Column(Modifier.padding(top = 4.dp)) {
            Text(titulo, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Tinta, lineHeight = 19.sp)
            // Uma linha só: o orçamento de altura do menu conta com isso. Os
            // textos são curtos para caber nos ~115 dp; as reticências são só a
            // garantia para uma fonte do sistema maior que a prevista.
            Text(
                detalhe,
                fontSize = 11.5.sp,
                color = Color(0xFF44575E),
                lineHeight = 14.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/**
 * Cartão de ação baixo, de largura inteira, para a ação que sobra da grade de
 * dois: o mesmo vidro, com ícone e textos lado a lado. O detalhe quebra em
 * vez de ganhar reticências — aqui não há par ao lado para ficar desigual.
 */
@Composable
private fun CartaoAcaoLinha(titulo: String, detalhe: String, icone: Icone, aoClicar: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .vidro(RoundedCornerShape(RAIO_CARTAO))
            .clickable(role = Role.Button, onClick = aoClicar)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconeTraco(icone, Verde, Modifier.size(20.dp))
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(titulo, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Tinta, lineHeight = 19.sp)
            Text(detalhe, fontSize = 11.5.sp, color = Color(0xFF44575E), lineHeight = 14.sp)
        }
    }
}

/**
 * Menu em vidro. Orçamento na tela útil de ~460 dp do TC8300: margens 32, nome
 * 24, perfil 17, respiro 12, cartões 84 + 84 + 84 (o "Repaletização" cabe numa
 * linha na fonte padrão, ~108 dp dos 115 do cartão; o antigo "Transferir
 * caixas" quebrava em duas e a linha tinha 99), a linha baixa do "Consultar
 * pallet" 56, com 3 × 10 entre eles e 10 de folga para a sombra, respiro 10 e
 * "Sair" 52 — ~495 dp. Não cabe inteiro: a grade rola uns 35 dp, e a linha de
 * baixo fica meio à vista, o sinal de que há mais para rolar. Com aviso ou
 * "Sem servidor" rola mais. O "Sair" fica sempre fixo.
 */
@Composable
fun TelaMenu(estado: EstadoApp, vm: AppViewModel) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        // Nome e pallet ativo na mesma linha: o chip numa linha própria custava
        // ~30 dp e fazia o menu rolar justo no caso comum — meio do turno, com
        // pallet aberto no aparelho.
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                estado.usuario?.nome ?: "",
                fontSize = 22.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-0.2).sp,
                color = Tinta,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            estado.palletAtivo?.let {
                Text(
                    it,
                    fontSize = 13.sp,
                    lineHeight = 16.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = Verde,
                    maxLines = 1,
                    modifier = Modifier
                        .padding(start = 8.dp)
                        .clip(CircleShape)
                        .background(VerdeClaro.copy(alpha = 0.85f))
                        .padding(horizontal = 10.dp, vertical = 5.dp),
                )
            }
        }
        // Altura de linha própria nas linhas de 13 sp: herdadas do bodyLarge
        // (24 sp) elas gastavam ~7 dp cada de ar que o menu não tem.
        Text(
            "${estado.usuario?.perfil} · ${estado.servidor}",
            fontSize = 13.sp,
            lineHeight = 17.sp,
            color = Color(0xFF3F5259),
        )
        if (!estado.online || estado.leiturasPendentes > 0) {
            Text(
                if (estado.online) "Enviando ${estado.leiturasPendentes} leitura(s)…"
                else "Sem servidor" + if (estado.leiturasPendentes > 0)
                    " · ${estado.leiturasPendentes} aguardando" else "",
                fontSize = 13.sp,
                lineHeight = 17.sp,
                fontWeight = FontWeight.SemiBold,
                color = Vermelho,
            )
        }
        // Leituras de outro usuário guardadas aqui: vão quando o dono entrar.
        if (estado.leiturasRetidas > 0) {
            Text(
                "${estado.leiturasRetidas} leitura(s) de ${estado.donosRetidas.joinToString()} guardada(s) — " +
                    "vão quando o dono entrar neste coletor",
                fontSize = 13.sp,
                lineHeight = 17.sp,
                fontWeight = FontWeight.SemiBold,
                color = Color(0xFF7A5600),
            )
        }

        Avisos(estado.mensagem, estado.sucesso)
        Spacer(Modifier.height(12.dp))

        // Rola: o menu cresce com o sistema, e conteúdo que empurra botão para
        // fora da tela parece botão que sumiu. "Sair" fica fixo embaixo.
        // O padding embaixo fica dentro da rolagem: a sombra do vidro desce
        // ~12 dp abaixo do cartão, e a rolagem corta o que passa da borda.
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(bottom = 10.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                Modifier.fillMaxWidth().height(IntrinsicSize.Min),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                CartaoAcao(
                    "Separar",
                    "em aberto",
                    Modifier.weight(1f),
                    habilitado = !estado.carregando,
                    icone = Icone.SEPARAR,
                ) { vm.carregarInstrucoes() }
                CartaoAcao(
                    "Criar pallet",
                    "separação/devolução",
                    Modifier.weight(1f),
                    icone = Icone.CRIAR_PALLET,
                ) {
                    vm.irPara(Tela.CRIAR_PALLET)
                }
            }
            Row(
                Modifier.fillMaxWidth().height(IntrinsicSize.Min),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                CartaoAcao("Endereçar", "pallets na fila", Modifier.weight(1f), icone = Icone.ENDERECAR) {
                    vm.abrirEnderecamento()
                }
                // Um cartão para as duas conferências de carga que volta: um
                // sétimo cartão estourava o orçamento de altura do menu de 4".
                CartaoAcao("Conferir", "devolução e retorno", Modifier.weight(1f), icone = Icone.DEVOLUCOES) {
                    vm.abrirDevolucoes()
                }
            }

            Row(
                Modifier.fillMaxWidth().height(IntrinsicSize.Min),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // "Repaletização": juntar o pallet inteiro ou passar caixa a
                // caixa, sempre entre pallets da mesma NF. Foi "Transferir
                // caixas" e depois "Repaletizar"; o nome atual é pedido do chão.
                CartaoAcao(
                    "Repaletização",
                    "juntar ou por caixa",
                    Modifier.weight(1f),
                    icone = Icone.TRANSFERIR,
                ) {
                    vm.abrirTransferencia()
                }
                CartaoAcao("Reimprimir", "etiqueta ou cartão", Modifier.weight(1f), icone = Icone.REIMPRIMIR) {
                    vm.abrirReimpressao()
                }
            }

            // Linha baixa, de largura inteira: um sétimo cartão da altura dos
            // outros ficaria sozinho numa linha e custaria 94 dp; esta custa 66.
            CartaoAcaoLinha(
                "Consultar pallet",
                "NF, conteúdo e onde estão os outros",
                icone = Icone.CONSULTAR,
            ) { vm.abrirConsultaPallet() }
            // Mais uma linha baixa: a grade rola mais 66 dp, e o "Sair" segue fixo.
            CartaoAcaoLinha(
                "Conferência de box",
                "o que está em cada nível do prédio",
                icone = Icone.ENDERECAR,
            ) { vm.abrirConferenciaBox() }
        }

        Spacer(Modifier.height(10.dp))
        // "Sair" em vidro, com o texto em vermelho: presente, mas sem o peso de
        // um botão cheio — ninguém deve sair do app por engano.
        Box(
            Modifier
                .fillMaxWidth()
                .height(52.dp)
                .vidro(CircleShape)
                .clickable(role = Role.Button, onClick = vm::sair),
            contentAlignment = Alignment.Center,
        ) { Text("Sair", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = Vermelho) }
    }
}

@Composable
fun TelaInstrucoes(estado: EstadoApp, vm: AppViewModel) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Cabecalho("Instruções em aberto")

        Avisos(estado.mensagem, estado.sucesso)

        if (estado.instrucoes.isEmpty() && !estado.carregando) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    "Nenhuma instrução em aberto",
                    fontSize = 17.sp,
                    textAlign = TextAlign.Center,
                    color = Tinta2,
                )
            }
        } else {
            LazyColumn {
                items(estado.instrucoes) { i -> CartaoInstrucao(i) { vm.abrirInstrucao(i) } }
            }
        }
    }
}

@Composable
private fun CartaoInstrucao(i: Instrucao, aoClicar: () -> Unit) {
    val progresso =
        if (i.qtdCaixasPrevistas > 0) i.qtdCaixasSeparadas.toFloat() / i.qtdCaixasPrevistas else 0f

    // Cores da pílula iguais às da retaguarda: a situação sai da pílula, não
    // da cor do cartão inteiro.
    val (fundoPilula, tintaPilula) = when {
        i.prontaParaConclusao -> VerdeClaro to Verde
        i.status == "EM_SEPARACAO" -> Color(0xFFFBEFD4) to Color(0xFF7A5600)
        else -> Color(0xFFEEF2F3) to Tinta2
    }

    Card(
        Modifier.fillMaxWidth().padding(vertical = 6.dp).clickable(onClick = aoClicar),
        border = BORDA_CARTAO,
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    i.numero,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = Tinta,
                )
                // Cadeados todos fechados: a instrução fica aqui até a
                // retaguarda concluir, mas dizendo que a parte do separador
                // acabou — senão ele não distingue "acabei" de "ainda falta".
                Text(
                    if (i.prontaParaConclusao) "AGUARDANDO CONFERÊNCIA"
                    else i.status.replace('_', ' '),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = tintaPilula,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier
                        .padding(start = 8.dp)
                        .clip(CircleShape)
                        .background(fundoPilula)
                        .padding(horizontal = 9.dp, vertical = 5.dp),
                )
            }
            Spacer(Modifier.height(6.dp))
            Text(
                "${i.qtdNotas} nota(s) · ${i.qtdCaixasSeparadas}/${i.qtdCaixasPrevistas} caixas",
                fontSize = 16.sp,
                color = Tinta,
            )
            Spacer(Modifier.height(10.dp))
            LinearProgressIndicator(
                progress = { progresso },
                color = Verde,
                trackColor = Color(0xFFE3EAEC),
                strokeCap = StrokeCap.Round,
                gapSize = 0.dp,
                drawStopIndicator = {},
                modifier = Modifier.fillMaxWidth().height(8.dp),
            )
        }
    }
}

@Composable
fun BarraCarregando(visivel: Boolean) {
    if (visivel) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            CircularProgressIndicator(
                Modifier.padding(top = 8.dp).semantics { contentDescription = "Carregando" },
                color = Verde,
            )
        }
    }
}

/**
 * Botão de ícone das telas de bipe (voltar, mais opções): 52 dp, redondo, sem
 * fundo — o alvo é grande para a luva, o desenho é pequeno para não pesar.
 * 52 e não os 48 do Android: é o mínimo de toque do projeto para luva
 * (docs/TELAS-COLETOR.md §0).
 */
@Composable
internal fun BotaoIcone(icone: Icone, descricao: String, aoClicar: () -> Unit) {
    Box(
        Modifier
            .size(52.dp)
            .clip(CircleShape)
            .clickable(role = Role.Button, onClick = aoClicar)
            .semantics { contentDescription = descricao },
        contentAlignment = Alignment.Center,
    ) { IconeTraco(icone, Tinta, Modifier.size(22.dp)) }
}
