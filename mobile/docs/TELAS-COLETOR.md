# Telas do Coletor — Zebra TC8300

Fluxo detalhado do app mobile. Complementa `MODELO-DE-DADOS.md`.

---

## 0. Premissas de hardware e ambiente

O aparelho é operado **de pé, com luva, dentro de câmara fria**, com o gatilho
físico na mão. Isso condiciona todo o desenho:

- **Sem foco de teclado.** A leitura chega por *intent* do DataWedge, nunca por
  campo de texto com foco. Um `EditText` focado numa tela de separação é fonte
  garantida de leitura perdida. Digitar existe só como plano B, atrás de um
  botão **⌨ Digitar** que abre um diálogo — não há campo exposto convidando o
  toque, e o teclado nunca abre sozinho (`stateHidden`).
- **Um portão de duplo-disparo só.** O debounce de 400 ms *grava estado* a cada
  consulta; chamado no receiver e de novo no ViewModel, a segunda chamada via o
  mesmo código a ~0 ms e recusava — **toda** leitura do gatilho morria em
  silêncio no primeiro teste em campo. Ele vive só no ViewModel, por onde passam
  gatilho e digitação.
- **Perfil do DataWedge criado pelo app** com `CONFIG_MODE=CREATE_IF_NOT_EXIST`
  (`OVERWRITE` não cria perfil inexistente) e `<queries>` declarando
  `com.symbol.datawedge` no manifesto — sem ele, com targetSdk ≥ 30, o Android
  descarta o broadcast de provisionamento sem erro nenhum.
- **Alvos grandes.** Ações principais com 64 dp; secundárias com no mínimo
  52 dp (o padrão Android de 48 dp é pequeno demais para luva).
- **Tela de 4".** O TC8300 tem 800 × 480 — cerca de 320 × 533 dp, ~460 dp úteis
  descontadas as barras. O caso normal de cada tela cabe **sem rolar**; a
  rolagem existe como rede de segurança (aviso longo, fila grande), e a ação
  principal fica sempre fixa no rodapé.
- **Alto contraste.** Câmara é escura e a tela costuma estar embaçada.
- **Tela sempre ligada** durante a separação (`FLAG_KEEP_SCREEN_ON`).
- **Feedback sonoro e tátil obrigatório** — o separador não olha a tela a cada
  bipe. Som e vibração distintos por tipo de evento:

| Evento | Som | Vibração |
|---|---|---|
| Leitura aceita | bipe curto agudo | 50 ms |
| Item errado | dois bipes graves | 300 ms |
| Código não cadastrado | bipe triplo médio | 200 ms |
| Item completo | acorde ascendente | 2 × 100 ms |

---

### 0.1 Visual (0.5.0)

"Vidro onde se olha, sólido onde se bipa." A prévia aprovada fica registrada
como referência de cor, forma e movimento.

- **Tema próprio, sempre claro**, com o verde `#14532D` da retaguarda fixo.
  Antes o app usava o tema padrão do Android Studio com cor dinâmica: roxo no
  Android 8 a 11 e a cor do papel de parede no 12+ — cada coletor de uma cor.
  Modo escuro do sistema não inverte o app: o contraste que funciona na câmara
  é o claro.
- **Vidro só no menu e no login.** Fundo de câmara fria em gradientes radiais
  (macios em qualquer versão); cartões brancos translúcidos — alfa 0,42 no
  Android 12+ (API 31, onde o fundo também é desfocado) e 0,90 abaixo.
- **Telas de bipe sólidas e de alto contraste.** O movimento é o que é fluido:
  contador que salta com mola a cada bipe próprio (não a cada correção do
  servidor), barra ondulada que para alguns segundos depois do último bipe,
  cadeado que muda de forma quando o item pode ser fechado, e transição com
  mola entre telas. Tudo para quando o sistema desliga as animações.
- **Fonte do sistema** (Roboto). Os coletores não têm internet, então nada de
  fonte baixada.

## 1. Configuração e login

### 1.1 Configuração do servidor

IP e porta do servidor. **O caminho normal é bipar o QR** da aba Coletores da
retaguarda: ele traz `http://ip:porta`, o app separa host de porta e já testa a
conexão. Digitar à mão continua disponível, mas é lento e erra — e vira problema
de verdade no dia em que o endereço muda, porque são todos os aparelhos de uma
vez.

```
┌──────────────────────────────┐
│ Configuração                 │
│ Bipe o QR da aba Coletores,  │
│ ou digite o endereço         │
│                              │
│ IP do servidor               │
│ [ 192.168.0.10            ]  │
│ Porta                        │
│ [ 8080                    ]  │
│                              │
│ [    Testar conexão       ]  │
│ [   Salvar e continuar    ]  │
└──────────────────────────────┘
```

Código que não pareça um endereço é recusado com som de erro — bipar a etiqueta
errada aqui não deixa o aparelho apontando para lugar nenhum.

### 1.2 Login

Usuário (texto) + senha (numérica, teclado numérico). Rodapé mostra versão do
app e indicador de conexão com o servidor.

Falha de conexão exibe o IP configurado na própria mensagem, em vez de um
genérico "erro de rede".

---

## 2. Menu principal

```
┌──────────────────────────────┐
│ JOÃO SILVA          ● online │
├──────────────────────────────┤
│  ▸ Instruções em aberto      │
│  ▸ Criar pallet              │
│  ▸ Endereçamento de pallets  │
│  ▸ Conferir                  │  ← devolução e retorno
│  ▸ Repaletização             │  ← juntar ou por caixa (§6A)
│  ▸ Consultar pallet          │  ← NF e onde estão os outros (§6C)
│  ▸ Conferência de box        │  ← o que está em cada nível (§6D)
│  ▸ Reimprimir                │
│                              │
│  Pallet ativo: P-RED-000123  │  ← só aparece se houver
├──────────────────────────────┤
│  Sair                        │
└──────────────────────────────┘
```

O pallet ativo fica visível no menu de propósito: é o erro mais comum o
separador esquecer qual pallet está usando.

**Instruções em aberto** lista as de status `ABERTA` e `EM_SEPARACAO`.
**Conferir** (cartão *devolução e retorno*) junta numa lista só, em duas
seções, as duas conferências de carga que volta: **Retornos** — NF nossa,
expedida, que voltou; só os `EM_CONFERENCIA` (§5B) — em cima, porque é carga na
doca esperando destino; **Devoluções** — NF do cliente,
`AGUARDANDO_CONFERENCIA` e `EM_CONFERENCIA` (§5A) — embaixo. Um cartão para as
duas: um sétimo cartão estourava a altura do menu de 4".

Fica sempre visível, como o endereçamento, e mostra a lista vazia quando não
há nada a conferir. Esconder a entrada exigiria consultar o servidor toda vez
que o menu aparece, e um botão que some é mais confuso do que uma lista vazia.
Servidor anterior à 0.6.0 não tem retorno (responde 404): a seção some e a tela
volta a ser só a de devoluções.

---

## 3. Criar pallet

Quantidade por botões rápidos (**1 · 5 · 10**) mais campo livre. Impressora
vem preenchida com a padrão do usuário, trocável.

**Tipo de pallet** — `Separação` (`P-RED-…`) ou `Devolução` (`P-DEV-…`), cada um
com seu sequencial. A etiqueta muda só o texto do rodapé: "Pallet Separação" ou
"Pallet Devolução".

Ao imprimir: reserva N sequenciais, grava-os como `CRIADO` e envia o ZPL.
Confirmação mostra a faixa gerada — *"P-RED-000123 a P-RED-000132 impressos"*.

**Sobre falha de impressão.** A porta 9100 aceita o job sem confirmar que o
papel saiu. Se a impressora estiver sem etiqueta, o sequencial já foi
consumido. Como os sequenciais são não reaproveitáveis por design, a saída é
uma opção **"Reimprimir pallet"** (lê o código ou digita o número) em vez de
tentar recuperar a numeração. Buraco na sequência é aceitável; etiqueta perdida
sem como reimprimir, não.

### Etiqueta de separação — 80 × 50 mm

```
┌────────────────────────────┐
│  █║▌║▌║ ║▌║▌ ║▌║ ║▌║▌║ █   │   Code 128
│                            │
│      P-RED-000123          │   texto grande
│                            │
│     Pallet Separação       │
└────────────────────────────┘
```

---

## 4. Navegação até o item

**Instruções em aberto** → lista com número, data, cliente e progresso em
caixas (`142/245`).

**NFs da instrução** → número da NF, cliente, previsto/separado, status.

**Itens da NF** → código, descrição, previsto/separado e o **cadeado**:

| Ícone | Estado |
|---|---|
| 🔓 | `ABERTO` / `EM_SEPARACAO` |
| 🔒 | `FECHADO` |
| 🔒⚠ | `FECHADO_DIVERGENCIA` |

Item fechado abre apenas em consulta, sem permitir leitura.

---

**Instrução pronta.** Com todos os cadeados fechados, a instrução continua na
lista com a marca **AGUARDANDO CONFERÊNCIA** — o separador vê que a parte dele
acabou, sem que ela suma. Só sai do coletor quando a retaguarda conclui. Se o
supervisor reabrir um item, ela volta a `EM_SEPARACAO` e reaparece.

**Item fechado abre**, só para consulta e exclusão: é depois de fechar o cadeado
que se descobre a caixa bipada por engano, e o menu de excluir mora nesta tela.
A leitura continua barrada pelo servidor.

## 5. Tela de separação

A tela crítica do app. Tudo que importa cabe sem rolagem.

```
┌──────────────────────────────┐
│ ← NF 952463 · Item 3     🔓 ⋮│
├──────────────────────────────┤
│ 2685                         │
│ CARNE BOVINA CONSERVA        │
│ MARCA DEMO 24X320G RA            │
├──────────────────────────────┤
│                              │
│         12 / 20              │   ← números enormes
│           caixas             │
│                              │
├──────────────────────────────┤
│ Pallet: P-RED-000123         │
└──────────────────────────────┘
```

Sem pallet selecionado, a faixa inferior vira banner vermelho **"Selecione um
pallet"** e qualquer leitura de produto é recusada.

### 5.1 Tratamento de cada leitura

Ordem de avaliação, do mais específico ao mais genérico:

| # | Situação | Resposta |
|---|---|---|
| 1 | Formato `P-RED-…` | tenta selecionar como pallet (§5.2) |
| 2 | Nenhum pallet ativo | 🔴 "Nenhum pallet de separação selecionado" |
| 3 | Item fechado pelo supervisor | 🔴 "Item fechado pelo supervisor às HH:MM" |
| 4 | Código não cadastrado | 🟡 "Código não cadastrado" + envia à fila de pendências |
| 5 | Casa com **outro** produto | 🔴 "Item errado: SALSICHA MARCA DEMO 24X180G SS" |
| 6 | Quantidade já completa | 🔴 "Quantidade já completa (20/20)" |
| 7 | Casa com o produto do item | ✅ +1 caixa |

Casos 2 a 6 abrem **diálogo bloqueante** com som, exigindo confirmação — é o
comportamento pedido nos requisitos, e o que impede o separador de continuar
bipando no automático depois de um erro.

**Debounce de 400 ms** para leituras idênticas consecutivas. O gatilho do
TC8300 dispara duas vezes com facilidade, e sem isso o duplo-disparo vira caixa
fantasma na contagem.

**Envio.** O contador sobe na hora e a leitura vai ao servidor em segundo plano
com a chave de idempotência. Não confirmando, a tela desfaz o incremento e
avisa. É o que sustenta a operação durante o salto entre APs sem inventar
contagem.

### 5.2 Selecionar pallet

Leitura do código impresso. Validações, em ordem:

1. Pallet existe
2. Status é `CRIADO` ou `EM_USO` — `ENCERRADO` é recusado
3. Se `EM_USO`, pertence a esta NF — senão: **"Este pallet pertence à NF 952918"**

A terceira é a trava anti-mistura de notas. Na primeira leitura de produto o
pallet se amarra à NF e não muda mais.

**Cada separador monta o seu próprio pallet.** O separador X usa o pallet 1, o
Y usa o pallet 2 — mesmo trabalhando na mesma NF, e mesmo no mesmo item. Um
pallet `EM_USO` fica vinculado ao separador que o iniciou; outro usuário que
tentar selecioná-lo recebe **"Pallet em uso por JOÃO"**.

Meio pallet de cada não é problema: os dois se juntam depois (§5.5).

### 5.3 Fechar o cadeado

Só fecha com `separado == previsto`. Caso contrário: *"Item não coletado por
completo (12/20)"* e o cadeado permanece aberto. Fechamento incompleto é
exclusivo do supervisor, na retaguarda.

### 5.4 Menu ⋮

**Encerrar pallet** — encerra **o pallet ativo do próprio separador**, nunca o
de outro. Imprime o cartão, muda para `ENCERRADO` e volta a pedir seleção. Sem
pallet aberto no aparelho, avisa e aponta **Transferir caixas** (§6A), onde se
encerra qualquer pallet lido — o vínculo com o pallet mora só na memória do app
e some ao reiniciá-lo.

**Trocar pallet de separação** — encerra o atual (mesmo efeito acima) e abre a
seleção.

**Juntar pallets** — funde dois pallets parciais em um só (§5.5).

**Excluir caixas** — informa a quantidade; o pallet é o ativo ou, sem pallet
aberto, é pedido no próprio diálogo. Cancela as leituras mais recentes daquele
item naquele pallet e reabre o cadeado se estava fechado. Pallet expedido é
recusado, e pallet de retorno aberto também (`PALLET_EM_RETORNO`, §5B).

O servidor só encerra pallet em montagem: já endereçado (`ENDERECADO`),
expedido, fundido ou esvaziado é recusado. Pallet endereçado ainda em separação
continua `EM_USO` (§6) e encerra normalmente.

**Reimprimir etiqueta** — do pallet ativo. Para qualquer outro, e para o
cartão, use **Reimprimir** no menu (§6B).

### 5.5 Juntar pallets

Situação comum: dois separadores trabalharam a mesma NF e cada um terminou com
meio pallet. Fisicamente as caixas de um vão para cima do outro; no sistema, um
pallet absorve o outro.

```
┌──────────────────────────────┐
│ ← Juntar pallets             │
├──────────────────────────────┤
│ Origem  (será esvaziado)     │
│   P-RED-000124   22 cx       │
│                              │
│ Destino (recebe as caixas)   │
│   P-RED-000123   45 cx       │
├──────────────────────────────┤
│ Resultado: 67 caixas         │
│ NF 952463 · ATACADAO S A     │
│                              │
│ ⚠ O cartão do destino será   │
│   reimpresso. Descarte o     │
│   cartão do pallet de origem.│
├──────────────────────────────┤
│        [ Juntar ]            │
└──────────────────────────────┘
```

Validações antes de permitir:

| Situação | Resultado |
|---|---|
| Pallets de **NFs diferentes** | recusa — é a mesma trava anti-mistura |
| Tipos diferentes (`P-RED` com `P-DEV`) | recusa |
| Origem ou destino já **expedido** | recusa |
| Origem ou destino em **retorno aberto** (§5B) | recusa — `PALLET_EM_RETORNO` |
| Origem já **fundida** antes | recusa |
| Origem **endereçada** | permite; libera a posição que ocupava |
| Destino **endereçado** | permite; mantém a posição |

Efeito: as caixas passam a pertencer ao destino, a origem fica `FUNDIDO` e sai
de circulação. O cartão do destino é **reimpresso automaticamente** — sem isso
ficaria um cartão dizendo 45 caixas num pallet que tem 67, que é pior do que
não ter cartão.

Juntar três ou mais é repetir a operação.

A rastreabilidade não se perde: cada leitura continua registrando em qual
pallet foi originalmente bipada e por quem.

### Cartão pallet — mídia 80 × 220 mm, conteúdo na horizontal

A bobina é a mesma de 80 mm; o conteúdo é rotacionado 90° (`^FWR`), de modo que
a **área de leitura fica 220 mm de largura × 80 mm de altura**. É o que libera
espaço para a lista de produtos em 3 colunas.

Layout da 0.5.1, aprovado pela prévia renderizada:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 2685 CARNE BOVINA CONSERVA  20 │ 2712 PATE FRANGO     11 │ ...           │
│ 2690 CARNE BOVINA GOLD      15 │ 2720 APRESUNTADO     18 │               │
│ ... itens na ordem da NF, de cima para baixo, 10 por coluna              │
├───────────────────┬──────────────────────────┬───────────────────────────┤
│ 45 caixas         │ SUPERMERCADO BOA         │██████████ NF ██████████████│
│ 12/09/2026 14:32  │ ESPERANCA LTDA           │███                     ████│
│ JOAO SILVA        │ AV. DAS INDUSTRIAS, 1234 │███      953120         ████│
│ Instrução RED-045 │ GALPAO 3 - DOCA 12       │███                     ████│
│ P-RED-000123      │ JARDIM PRIMAVERA         │████████████████████████████│
│ ║▌║▌║║▌║▌║▌║      │ S.J. RIO PRETO/SP - CEP  │█ CONTÉM BONIFICAÇÃO  (opc.)│
└───────────────────┴──────────────────────────┴───────────────────────────┘
```

- **Esquerda**: o que foi separado, quando e por quem (numa segunda via, quem
  encerrou o pallet e quando — não quem pediu a reimpressão), a instrução, e
  embaixo o número do pallet e o Code 128.
- **Meio**: cliente em até 2 linhas e endereço em até 5, quebrando por palavra
  (logradouro, complemento, bairro, cidade/CEP; juntando partes se não couber).
  Nada é impresso por cima de nada: texto que não cabe quebra ou termina em
  reticências.
- **Direita**: a NF em branco sobre preto, do maior tamanho que caiba (6 dígitos
  ≈ 2 cm de altura). Bonificação vira faixa branca dentro da caixa preta.

Capacidade: 3 colunas × 10 linhas = **30 produtos**, bem acima do observado
nas NFs de referência (8 e 12 itens). Passando de 30, emite segunda via com
"Folha 2 de 2" — nunca corta a lista.

As coordenadas são de leitura (`Desenho` em `Zpl.kt`): com `^FWR` o `^FO`
marca o canto do campo na mídia, que na leitura é o canto de baixo — a 0.5.0
não descontava a altura, e a lista de itens saía de baixo para cima.

---

## 5A. Conferência de devolução

Chega pelo menu em **Conferir**, seção Devoluções. A NF do cliente já foi
importada e vinculada na retaguarda; aqui só se conta o que veio na carreta.

`Devoluções` → `Itens da NF` → `Conferência do item`, espelhando
`Instruções → Notas → Itens → Separação`.

A diferença que muda a tela: **a contagem é em unidades, não em caixas**. O
cliente devolve o que sobrou na gôndola, e isso inclui lata avulsa.

```
┌──────────────────────────────┐
│ ← Devolução 44521         🔓 │
├──────────────────────────────┤
│ 2685 CARNE BOVINA CONSERVA   │
│ MARCA DEMO 24X320G RA            │
│ cliente: 957712 CARNE BOV…   │
│                              │
│           48 / 72            │
│           unidades           │
│  cada bipe soma 24 un (1 cx) │
│                              │
│ [ código da caixa/pallet   ] │
│ [ Bipar caixa ][ Un. soltas ]│
│ Pallet P-DEV-000001          │
└──────────────────────────────┘
```

| Modo | Efeito |
|---|---|
| **Bipar caixa** | soma `unidades_por_caixa` — presume caixa cheia |
| **Unidades soltas** | soma N unidades digitadas |

O contador **não** antecipa a soma como na separação: aqui o número exibido é
sempre o que o servidor devolveu. Contagem provisória errada viraria divergência
contra a NF de um terceiro, e o volume de bipes é baixo o bastante para não
precisar da folga.

Bipar a etiqueta `P-DEV-…` seleciona o pallet, igual à separação. Pallet de
separação é recusado: `PALLET_TIPO_INVALIDO`.

**Pallet primeiro (0.7.5, coletor 0.7.3).** Sem pallet selecionado, o campo
pede **Etiqueta do pallet**, **Unidades soltas** fica desabilitado e bipar
uma caixa dá o alerta **Pallet primeiro** — que some sozinho assim que o
pallet é bipado. O pallet também pode ser bipado na **lista de itens** da NF,
antes de abrir o item, e o campo de digitar aceita o código dele a qualquer
momento (**Código da caixa ou pallet**), para quando a etiqueta do segundo
pallet não lê. Pallet que o servidor recusar (encerrado, de outra NF) é
solto na hora, com o alerta falando dele — e não da caixa. O servidor recusa do mesmo jeito
(`PALLET_OBRIGATORIO`), para o coletor antigo também: caixa conferida sem
pallet não aparecia no estoque — o P-DEV endereçado depois ficava com zero
caixas. Abrir outra NF tira o pallet da anterior. As leituras antigas sem
pallet se acertam na retaguarda (`TELAS-RETAGUARDA.md` §10.8).

**Produto trocado (0.7.4).** Veio outro produto no lugar do da NF: bipar a
caixa dele no item da NF dá `Item errado`. O supervisor registra a troca na
retaguarda (`TELAS-RETAGUARDA.md` §10.6) e a lista de itens ganha um item
novo, com o produto que veio e a linha `cliente: … NO LUGAR DO ITEM n: …`. É
conferido como qualquer item. Não precisa de APK novo.

**Cadeado.** Completo, fecha direto. Faltando unidade, pede justificativa e
fecha como `FECHADO_DIVERGENCIA` — cliente que devolve menos do que declarou é
informação de negócio, não erro a esconder. Fechado o último item, a NF vai a
`CONFERIDA` e a baixa (DESCARTE ou REVENDA) é feita na retaguarda.

### 5A.1 Conferência de estoque: a sobra (0.8.2, coletor 0.7.4)

Sobra é mercadoria que está aqui sem NF nenhuma. O supervisor abre a
conferência na retaguarda (`TELAS-RETAGUARDA.md` §10.12) e ela aparece em
**Conferir**, junto das devoluções, com o número `SOBRA-000001` e o texto
*Conferência de estoque*.

```
┌──────────────────────────────┐
│ SOBRA-000001                 │
│ Conferência de estoque · 3   │
│ produto(s)                   │
│ Pallet P-DEV-000007          │
│ Bipe a caixa: cada bipe      │
│ conta 1 caixa. Para várias   │
│ do mesmo produto, toque nele │
│ e digite.                    │
├──────────────────────────────┤
│ 2685 CARNE BOVINA…      4    │
│                       caixas │
│                        96 un │
│ 2503 PATINHO…           1    │
│                       caixas │
└──────────────────────────────┘
```

Não há item esperado nem tela de conferência: bipa-se **na própria lista**.
O pallet P-DEV vem antes, como na devolução — sem ele, **Pallet primeiro**.
Cada bipe conta **uma caixa**; tocar no produto abre **Somar caixas**, para
quando são muitas do mesmo. Produto fora do cadastro é recusado
(`CODIGO_NAO_CADASTRADO`): a sobra entra pelo que o sistema conhece.

A baixa — descarte ou revenda, por caixa ou de uma vez — é na retaguarda.

---

## 5B. Conferência de retorno (0.6.0)

Chega pelo menu em **Conferir**, seção **Retornos**. Retorno é NF **nossa**, já
expedida, cujos pallets voltaram — o cliente recusou a entrega ou alguns
pallets voltaram para reentregar. A retaguarda registrou quais pallets voltaram
e tirou a foto do que cada um levou, por produto (`TELAS-RETAGUARDA.md` §9.2);
aqui se confere caixa a caixa contra essa foto. Qualquer usuário confere; a
tratativa (reentrega, refaturamento, descarte) fica na retaguarda.

`Conferir` → `Pallets do retorno` → `Conferência do pallet`, espelhando a
devolução: lista, cadeado, falta com justificativa.

**Lista.** Só os retornos `EM_CONFERENCIA` — aguardando tratativa já não tem o
que bipar. O cartão traz NF, cliente, motivo e pallets fechados/total, em verde
quando todos fecharam.

### 5B.1 Pallets do retorno

```
┌──────────────────────────────┐
│ ← Retorno NF 952463          │
│   ATACADAO S A               │
│ Motivo: cliente recusou por  │
│ atraso na entrega            │
│ 1 de 2 pallet(s) em aberto · │
│ bipe a etiqueta ou toque no  │
│ pallet                       │
├──────────────────────────────┤
│ 🔒 P-RED-000123       45/45  │
│    conferido          caixas │
│                              │
│ 🔓 P-RED-000124       31/50  │
│    em conferência     caixas │
└──────────────────────────────┘
```

Um cartão por pallet: vermelho discreto em aberto, verde conferido
(*"conferido · manual"* quando veio do retorno completo da retaguarda), âmbar
com falta, com a justificativa embaixo. **Bipar a etiqueta** abre o pallet
direto; **tocar** também — é por aqui que se fecha o pallet que nem voltou, sem
bipe nenhum. Etiqueta de pallet que não é deste retorno dá *"Pallet fora deste
retorno"*; caixa bipada aqui pede a etiqueta do pallet antes.

### 5B.2 Conferência do pallet

```
┌──────────────────────────────┐
│ ← Retorno NF 952463      🔓 ⋮│
├──────────────────────────────┤
│ P-RED-000124                 │
│ ATACADAO S A                 │
│                              │
│           31 / 50            │
│            caixas            │
│   ▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░    │
│                              │
│ 2685 CARNE BOVINA CO…  20/20 │
│ 2690 CARNE BOV GOLD    11/15 │
│                      falta 4 │
│ 2503 FEIJOADA MARCA DEMO…   0/15 │
│                     falta 15 │
│ Faltando caixa? Toque no     │
│ cadeado e justifique         │
├──────────────────────────────┤
│          ⌨  Digitar          │
└──────────────────────────────┘
```

Só os produtos deste pallet, com conferido/esperado — é contra eles que o
servidor confere, e a falta aparece produto a produto. A linha do último
produto bipado acende: diz de relance em qual produto a caixa contou.

- **Caixa** vai ao servidor e o número da tela é **sempre o dele**, como na
  devolução: sem soma otimista, porque a falta aqui vira extravio na tratativa.
- **Etiqueta de outro pallet do mesmo retorno** troca de pallet sem voltar à
  lista — desce um palete, bipa o próximo.
- **Recusas** em diálogo, com som: *"Produto fora do pallet"* (não saiu neste
  pallet — confira se a caixa é de outro), *"Quantidade completa"*, *"Código
  não cadastrado"*. *"Pallet já conferido"* e *"Retorno fora de conferência"* —
  fechado por outro coletor, ou retorno completo na retaguarda — também
  recarregam a tela com o que vale.
- **Uma operação por vez, na ordem do bipe.** Sem soma otimista, duas leituras
  no ar podiam voltar fora de ordem e o contador recuava; e fechar ou desfazer
  com leitura no ar mediria um pallet que ainda ia mudar.

**Cadeado.** Completo, fecha direto como conferido. Faltando caixa, abre
**Fechar com falta** — *"Conferido 31 de 50 caixas. Faltam 19 — informe o
motivo."* — com motivos prontos a um toque (**Extravio no transporte**,
**Caixa avariada**; com zero caixas, antes deles, **Pallet não voltou**) e campo
livre: digitar de luva, na câmara, é o último recurso. Sem justificativa não
fecha — extravio na volta é informação que precisa chegar até a tratativa.

O diálogo fica preso ao pallet em que o cadeado foi tocado: bipar a etiqueta de
outro pallet com ele aberto fecha o diálogo, e o fechamento vai sempre para o
pallet do toque.

Fechado, volta à lista de pallets — a não ser que o operador já tenha bipado a
etiqueta de outro pallet enquanto esperava: aí continua nele. Fechado o último,
*"Conferência encerrada — a tratativa é na retaguarda"* e o retorno sai da
lista do coletor. Retorno cancelado ou concluído na retaguarda com a tela
aberta: a tela diz a situação e para de aceitar leitura. O cadeado de
pallet fechado continua tocável, só para explicar que **reabrir é na
retaguarda**: pallet fechado abre só para consulta, sem aceitar leitura.

Sem resposta ao fechar, o pallet pode ter fechado sem a resposta chegar. Tocar
no cadeado de novo é seguro: o servidor responde "já conferido".

### 5B.3 Leitura sem resposta

Sem rede, ou com erro do próprio servidor (`ERRO_INTERNO`/5xx, que não diz se
gravou), o coletor **guarda a leitura inteira** — pallet, código e chave de
idempotência — e avisa *"A caixa X do pallet Y ainda não foi confirmada. Ela é
reenviada sozinha na próxima leitura, sem contar duas vezes: NÃO bipe ela de
novo."*

A próxima operação de retorno (bipe, cadeado ou desfazer) **reenvia primeiro a
leitura guardada, com a mesma chave**, para o pallet onde ela foi bipada: se a
primeira chegou, o servidor devolve o resultado dela sem contar de novo; se não
chegou, conta agora. Nos dois casos, uma caixa. Enquanto o reenvio não tiver
resposta, **bipe novo não é enviado** — o aviso diz *"Esta leitura NÃO contou:
bipe a caixa de novo quando a rede voltar"*. É isso que impede a contagem
dobrada: uma chave por caixa, e chave nova só depois que a anterior se resolve.
Rebipar a caixa guardada é que contaria duas vezes, por isso o aviso pede o
contrário.

Recusa de negócio é resposta: descarta a leitura guardada (a caixa não contou).
Sessão expirada guarda a leitura, e o reenvio sai no primeiro bipe depois do
login.

Falha ao carregar a lista de retornos (rede, tempo esgotado) mostra *"Não foi
possível carregar os retornos"* e mantém a seção; só servidor anterior à 0.6.0
(404) a esconde.

### 5B.4 Menu ⋮

**Desfazer última leitura** — a caixa bipada duas vezes por engano. Cancela a
última leitura viva deste pallet, que fica no histórico. Só com o pallet aberto
e alguma caixa conferida.

**Reimprimir cartão** — pallet que voltou costuma ter perdido o cartão no
caminho. Vai para a impressora do usuário; sem impressora cadastrada, ou
pallet sem conteúdo, avisa em vez de fingir que imprimiu.

---

## 6. Endereçamento de pallets

1. **Lê o pallet** — **a qualquer momento**, inclusive em separação. O
   separador leva o palete para a câmara antes de lembrar de encerrar; recusar
   aqui fazia ele largar o pallet sem endereço. Pallet sem nenhuma caixa é
   recusado (`PALLET_VAZIO`). Já endereçado, mostra onde está e oferece
   re-endereçar.

   Pallet endereçado em separação **continua `EM_USO`**: o separador segue
   bipando nele. Endereço diz onde está; a situação diz se acabou. Na fila ele
   aparece marcado *"em separação"*, depois dos encerrados — é assim que se
   sabe que não terminou. Na expedição, o que ficou `EM_USO` numa nota já
   concluída é encerrado automaticamente e sai junto; sem isso a carga ficaria
   para trás sem ninguém perceber.
2. **Confirma o conteúdo** — NF, cliente e total de caixas, para o operador
   perceber que pegou o pallet certo antes de andar até o box.
3. **Lê a etiqueta do box.**
4. **Grade de 6 níveis:**

```
┌──────────────────────────────┐
│  Box C8-D28                  │
│  CAMARA 8 - CONGELADO · Rua D│
├──────────────────────────────┤
│  Nível 6   │ ░░░░░░░░░░░░░░  │  branco = livre
│  Nível 5   │ ██████████████  │  azul = ocupado
│  Nível 4   │ ░░░░░░░░░░░░░░  │
│  Nível 3   │ ██████████████  │
│  Nível 2   │ ░░░░░░░░░░░░░░  │
│  Nível 1   │ ██████████████  │
├──────────────────────────────┤
│        [ ENDEREÇAR ]         │  habilita ao tocar um livre
└──────────────────────────────┘
```

Nível ocupado não é selecionável e exibe o pallet que o ocupa. O botão só
habilita após a seleção de um nível livre.

No coletor os seis níveis vão em **duas colunas** (1‑2 / 3‑4 / 5‑6): seis linhas
mais o pallet e o botão passavam de 560 dp, e a tela de 4" tem ~460. O botão
**Endereçar** fica fixo no rodapé; o gatilho continua lendo outro box se
precisar.

---

## 6A. Repaletização (0.7.0)

Card **Repaletização** no menu (antes "Unir pallet" / "Transferir caixas").
Só entre pallets da **mesma NF** — ou para um pallet novo, ainda sem uso, que
passa a ser da NF da origem. NF diferente é recusada pelo servidor
(`PALLETS_DE_NOTAS_DIFERENTES`), e origem igual ao destino também. Um
interruptor escolhe o jeito:

- **Juntar o pallet inteiro — ligado** (âmbar: o segundo bipe move carga): bipa
  a origem, bipa o destino e **todas** as caixas passam; a origem vai a
  `FUNDIDO` e o cartão do destino sai na impressora. É a fusão de sempre.
  Vale também para pallet de devolução (0.7.5), da mesma NF de devolução:
  passam as unidades conferidas e não sai cartão (ele é da NF de venda).
- **Desligado — caixa a caixa** (só separação; pallet de devolução é recusado
  com `PALLET_DE_DEVOLUCAO` — ali a caixa muda de pallet na retaguarda,
  `TELAS-RETAGUARDA.md` §10.8): bipa a origem, bipa o destino (o servidor
  confere o par sem mover nada) e depois as caixas, uma a uma. Cada bipe passa
  uma caixa daquele produto; no alto ficam a última caixa passada e os totais
  *origem N cx · destino M cx · passadas*. Etiqueta de pallet bipada nesse passo
  não troca nada: a tela pede para **Concluir** antes.

**Desfazer última** volta para a origem a última caixa que este operador passou.
**Concluir** imprime os cartões do destino e da origem (se ela ainda tiver
caixa) e diz o que saiu e o que não saiu na impressora; depois volta ao começo,
com o interruptor como estava. Sair ou trocar a origem com caixas passadas sem
concluir pergunta antes — cartão velho mente sobre a quantidade.

Origem que esvazia sai de circulação (`ESVAZIADO`, endereço liberado): a tela
manda descartar o cartão dela e concluir. **Sem resposta** numa caixa: a leitura
fica guardada com a mesma chave e é reenviada antes de qualquer outro bipe —
nunca conta duas vezes; enquanto isso uma faixa vermelha diz qual caixa e qual
par, com **Reenviar agora**. Pallet de retorno aberto não se mexe
(`PALLET_EM_RETORNO`). Com a origem na tela e em `EM_USO`, há o botão
**Encerrar este pallet** — é daqui que se encerra um pallet que não está aberto
no aparelho.

## 6B. Reimprimir

Card no menu. Escolhe-se o papel — **Etiqueta 80×50** (código de barras) ou
**Cartão 80×220** (itens do palete) — e bipa-se a etiqueta, ou usa-se
⌨ Digitar. Confere que o pallet existe antes de mandar para a porta 9100, que
aceita qualquer coisa sem conferir. O cartão exige nota vinculada: pallet que
ainda não recebeu caixa não tem o que listar nele.

## 6C. Consultar pallet (0.7.0)

Card no menu. Só leitura: bipa-se a etiqueta (ou ⌨ Digitar) e a tela mostra de
qual NF é o pallet, o cliente, a situação da NF, quantas caixas ele tem, onde
está (box, nível e depósito — ou *expedido* / *sem endereço*) e o que tem, item
a item (os seis primeiros; *Ver todos* abre o resto). Embaixo, **Outros pallets
da NF (k)**, recolhido, com o resumo de onde estão; tocando, abre a lista, e
cada pallet dela abre o próprio conteúdo. Pallet de devolução mostra a NF de
devolução e conta em unidades.

Bipar outra etiqueta troca a consulta e começa tudo recolhido, desde o topo. A
tela abre vazia: uma consulta antiga poderia estar velha (o pallet endereçado,
juntado ou expedido depois) e mandar o operador ao lugar errado. Pallet que não
existe mostra o aviso e apaga a consulta anterior. Servidor anterior à 0.7.0
não tem a rota: a tela diz que é preciso atualizar o servidor.

## 6D. Conferência de box (0.7.2)

Card no menu, na linha baixa abaixo de *Consultar pallet*. Só leitura: bipa-se
a etiqueta do box (ou ⌨ Digitar) e a tela mostra o prédio inteiro, **de cima
para baixo**, um cartão por nível:

```
┌──────────────────────────────┐
│ Box C8-D28                   │
│ CAMARA 8 - CONGELADO · Rua D │
│ 3 de 6 nível(is) ocupado(s)  │
├──────────────────────────────┤
│ Nível 6  P-RED-000124        │
│          48 cx · NF 952918   │
│          ATACADAO S A        │
│ Nível 5                      │  ← vazio: em branco
│ Nível 4  P-RED-000130        │
│          …                   │
└──────────────────────────────┘
```

Nível ocupado vem em azul claro, com o pallet, a quantidade (caixas; pallet de
devolução em unidades), a NF e o cliente. Nível dividido por bonificação mostra
os dois pallets. **Nível vazio aparece em branco**, só com o número — é o que se
procura com os olhos no prédio. Bipando a etiqueta de um **pallet**, a tela abre
o box onde ele está, com o pallet marcado em verde; pallet sem endereço (ou já
expedido) dá aviso. Bipar outro box troca a conferência, desde o topo. A tela
abre vazia, pelo mesmo motivo da consulta de pallet.

---

## 7. Sessão e conectividade

Token guardado localmente, revalidado a cada chamada. Perda de conexão mostra
faixa persistente no topo, sem derrubar a sessão. A tela de separação
revalida o estado do item a cada poucos segundos, para refletir um fechamento
feito pelo supervisor enquanto o separador está na tela.

---

## 8. Permissões no coletor

Definido com o cliente:

| Ação | Quem pode |
|---|---|
| Reimprimir pallet | qualquer separador |
| Re-endereçar pallet já endereçado | qualquer separador |
| Excluir separação | qualquer separador, **sem aprovação** |
| Conferir retorno: bipar, fechar pallet com falta, desfazer última | qualquer usuário |
| Registrar retorno, reabrir pallet do retorno, retorno completo, tratativa, desfazer tratativa, cancelar retorno | só supervisor, na retaguarda |

A exclusão de separação é a única ação do coletor que reduz quantidade já
contada, e por decisão do cliente não passa por supervisor. A contrapartida é o
rastro: cada exclusão cancela leituras nominais — ficam registrados o usuário,
o horário, o pallet e quantas caixas saíram. O relatório de separação expõe
isso, então o controle é por auditoria, não por bloqueio.
