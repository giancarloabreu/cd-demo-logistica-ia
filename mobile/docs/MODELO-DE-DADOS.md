# Modelo de Dados — App Separação Redespacho

Versão de discussão. Decisões consolidadas a partir do `Requisitos.txt`, do
`BOXES DEMO.xls` e das definições do cliente.

---

## 1. Visão geral

```
                    Rede local do CD (sem internet)
                                 │
   TC8300 ──HTTP──┐              │
   TC8300 ──HTTP──┼──►  Backend Ktor (.jar)  ──►  SQLite (arquivo local)
   PC Windows ────┘        no servidor                    │
   (navegador)             porta 8080                     └─► backup diário
                                 │                            \\logistica\AppSeparaRedespacho\
                                 └──TCP 9100──►  Zebra ZT231
```

O backend é o **único** processo que toca o banco. Coletores e estações falam
HTTP/JSON. Nada exige privilégio de administrador: o backend sobe por Tarefa
Agendada "ao fazer logon" e a retaguarda roda no navegador.

---

## 2. Convenções

- Todas as tabelas têm `id INTEGER PRIMARY KEY`.
- Timestamps em UTC, coluna sufixada `_em`.
- Autoria sufixada `_por` (FK para `usuario`).
- Exclusão é lógica (`ativo`) nos cadastros; movimentos nunca são apagados.

### 2.1 Sequenciais

```
sequencia(nome TEXT PK, valor INTEGER)
```

| Nome | Formato | Observação |
|---|---|---|
| `INSTRUCAO` | `RED-000001` | não reaproveitável |
| `PALLET` | `P-RED-000001` | pallet de separação |
| `PALLET_DEVOLUCAO` | `P-DEV-000001` | pallet de devolução (§9A) |
| `SOBRA` | `SOBRA-000001` | conferência de estoque: a sobra sem NF (§9A) |

Nunca decrementa, mesmo com exclusão de instrução ou pallet. Os prefixos
distintos (`P-RED-…`, `P-DEV-…`, `C8-D28`) permitem que a tela de endereçamento
identifique sozinha o que foi lido, sem o operador escolher o tipo antes.

---

## 3. Acesso

### `usuario`

| Campo | Tipo | Nota |
|---|---|---|
| `login` | TEXT UNIQUE | |
| `nome` | TEXT | |
| `senha_hash` | TEXT | senha numérica, hash com salt |
| `perfil` | ENUM | `SEPARADOR` \| `SUPERVISOR` \| `ADMIN` |
| `impressora_id` | FK null | impressora padrão do usuário |
| `multi_item` | BOOL | permite iniciar item sem fechar o anterior |
| `ativo` | BOOL | |

**Perfis**

- `SEPARADOR` — mobile apenas.
- `SUPERVISOR` — retaguarda: fechar item com divergência, reabrir, concluir
  instrução, excluir separação, baixar NF expedida, vincular código de barras,
  registrar e tratar retorno de NF expedida.
- `ADMIN` — o acima + cadastros (usuário, produto, box, impressora).

### `impressora`

`nome`, `ip`, `porta` (9100), `dpi` (203 ou 300 — autodetectado por SGD
`getvar "head.resolution"`), `ultimo_contato_em`, `ativo`.

---

## 4. Armazenagem

Importado do `BOXES DEMO.xls`, **excluindo a ANTECAMARA/DOCA** (fictícia, de
outra operação). Restam **740 boxes × 6 níveis = 4.440 posições**.

```
deposito   codigo, nome, ativo                          -- 5 câmaras
rua        deposito_id, sigla                           -- UNIQUE(deposito_id, sigla)
box        codigo UNIQUE, codigo_barras UNIQUE,
           deposito_id, rua_id, tipo, qtd_niveis, ativo -- tipo: ESTOCAGEM | PICKING
posicao    box_id, nivel                                -- UNIQUE(box_id, nivel), nivel 1..6
```

`posicao` é materializada (4.440 linhas) para dar FK ao endereçamento e
consulta de ocupação em uma query.

**Cadastro em lote (0.7.0).** Na retaguarda, aba Endereços: baixa-se a
planilha (vazia ou com o cadastro atual), preenche-se no Excel e sobe-se de
volta. Sem migração: grava nas mesmas tabelas. É por box (`box.codigo`), e
**nunca apaga**: box novo ganha `posicao` de 1 a N; níveis a mais acrescentam
posições; níveis a menos só se a posição que sai nunca teve endereçamento (tem
histórico com FK) — aí a `posicao` é apagada; desativar (`ativo = 0`) só com o
box vazio. Depósito e rua que não existem são criados; nome de depósito
diferente renomeia. Tudo ou nada, numa transação.

---

## 5. Produtos

O código de barras da caixa **é o `cEAN`** do XML (GTIN-14 / DUN-14). Verificado
contra amostra física do produto `2685` (`27890000217084`) e por três
evidências independentes:

- estabilidade do `cEAN` por `cProd` entre NFs distintas (`376670`, `390199`,
  `390256`, com 14 dias de intervalo);
- os 17 `cEAN` das NFs de referência são GTIN-14 com dígito verificador válido;
- prefixos 1, 2 e 9 — variantes normais de DUN-14.

**Atenção:** o `cEANTrib` (13 dígitos) é o GTIN da *unidade*, não da caixa.
Nunca usar para casar leitura.

### 5.1 Simbologia e normalização da leitura

As caixas usam **Code 128 simples**, com os 14 dígitos do GTIN e nada mais —
verificado em duas caixas do mesmo produto com datas de produção distintas. Sem
AI GS1, sem lote, sem validade. Code 128 vem habilitado por padrão no DataWedge
do TC8300, sem necessidade de provisionar decoder extra.

A comparação com o `cEAN` é portanto direta. Ainda assim, toda leitura passa por
uma função de normalização — a mesma aplicada ao importar o XML — para que uma
eventual mudança de etiqueta no fornecedor não quebre a operação:

| Entrada do scanner | Normalizado |
|---|---|
| `27890000217084` (14 díg.) | `27890000217084` |
| `0127890000217084` (GS1-128, AI 01) | `27890000217084` |
| `(01)278900002170841012345` (AI 01 + lote) | `27890000217084` |
| `7890000217080` (13 díg. — GTIN da unidade) | recusado, com aviso |

Regras: remover parênteses e separadores `GS`/FNC1; se começar com AI `01`,
tomar os 14 dígitos seguintes e descartar o restante (lote `10`, validade `17`
etc.); validar o dígito verificador GTIN-14. Só então comparar.

Isso protege contra o caso em que a etiqueta é GS1-128 com lote — a string
bruta mudaria a cada lote, embora o GTIN seja fixo por produto.

### `produto`

`codigo` UNIQUE (o `cProd` da NF — TEXT, o comprimento varia de 4 a 6 dígitos),
`descricao` (`xProd`), `unidades_por_caixa`, `gramas_por_unidade`,
`un_cx_origem` (`XML_VALIDADO` \| `MANUAL`), `origem`
(`XML` \| `IMPORTACAO` \| `MANUAL`), `ativo`.

**Unidades por caixa.** A separação trabalha só em caixas, mas a devolução pode
vir de uma lata avulsa (§9A) — então é preciso saber quantas unidades a caixa
contém. O dado sai do `xProd`, no padrão `24X420G`, e é **validado de forma
independente** contra `qTrib / qCom`:

```
CARNE EM CONSERVA ALMONDEGA ... 24X420G     24 × 420 g   = 10,080 kg
qTrib 60,48 KG ÷ qCom 6 CX                               = 10,080 kg  ✅
```

Conferiu nos 17 produtos das NFs de referência, sem exceção. Batendo, grava
como `XML_VALIDADO`; divergindo ou sem o padrão na descrição, o produto entra
sem a informação e a tela de cadastro pede o valor à mão.

### `produto_codigo_barras`

`produto_id` FK, `codigo` **UNIQUE global**, `simbologia`, `origem`
(`XML` \| `IMPORTACAO` \| `MANUAL` \| `VINCULO_COLETOR`), `confirmado` BOOL,
`confirmado_em`, `ativo`.

Relação **1:N** — o mesmo produto pode ter embalagem antiga e nova válidas ao
mesmo tempo. O UNIQUE global garante que um código jamais aponte para dois
produtos; a importação rejeita e reporta duplicatas em vez de sobrescrever.

**Ciclo de confirmação.** A importação do XML grava o `cEAN` com
`origem = XML`, `confirmado = false`, validando o dígito verificador GTIN-14. A
primeira leitura bem-sucedida no coletor promove para `confirmado = true`.
Assim o sistema opera desde o primeiro dia sem cadastro prévio, e um `cEAN`
errado no cadastro do emissor não trava a separação — a leitura cai em
`codigo_nao_identificado` e o supervisor vincula.

A planilha da unidade de origem (`CODIGO` + `DESCRICAO` + `COD_BARRAS_CAIXA`)
entra como **conferência cruzada**, não como pré-requisito: o relatório de
importação aponta onde ela diverge do `cEAN` do XML.

### `codigo_nao_identificado`

`codigo`, `qtd_leituras`, `primeira_leitura_em`, `ultima_leitura_em`,
`ultimo_usuario_id`, `resolvido_em`, `resolvido_produto_id`, `resolvido_por`.

Fila de pendências: todo código lido que não existe em
`produto_codigo_barras` cai aqui para o supervisor vincular na retaguarda.

---

## 6. Instrução, nota e item

### `instrucao`

`numero` UNIQUE (`RED-000001`), `status`, `criado_em/_por`,
`concluido_em/_por`, `observacao`.

`status`: `ABERTA` → `EM_SEPARACAO` → `CONCLUIDA` | `CANCELADA`

### `nota_fiscal`

`instrucao_id` FK, `chave` UNIQUE (44), `numero`, `serie`, `emissao`,
`dest_cnpj`, `dest_nome`, `dest_logradouro`, `dest_numero`,
`dest_complemento`, `dest_bairro`, `dest_municipio`, `dest_uf`, `dest_cep`,
`qtd_volumes_xml`, `status`, `xml_original` BLOB, `importado_em/_por`.

`status`: `PENDENTE` → `EM_SEPARACAO` → `CONCLUIDA` → `EXPEDIDA`, mais
`RETORNO_PARCIAL` (ver seção 9). O retorno de NF expedida (§9.3) não cria
situação nova: o que prende a NF é o retorno aberto.

Mapeamento do XML (NF-e 4.00 `nfeProc`):

| Campo | Origem |
|---|---|
| chave | `infNFe/@Id` sem prefixo `NFe` |
| número / série / emissão | `ide/nNF`, `ide/serie`, `ide/dhEmi` |
| destinatário | `dest/xNome`, `dest/CNPJ` |
| endereço | `dest/enderDest/*` — inclusive `xCpl` |
| `qtd_volumes_xml` | `transp/vol/qVol` |

`dest_complemento` é **nullable**: o `xCpl` existe na NF 952918 ("ANEXO 01") e
está ausente na NF 952463. A etiqueta cartão pallet omite a linha quando vazio.

**Validação na importação:** `SUM(qCom)` deve bater com `qVol`. Confere nas duas
NFs de referência — 952918: 96 = 96; 952463: 245 = 245. Se não bater, o sistema
avisa antes de montar a instrução.

O `infCpl` traz o endereço do CD de redespacho em texto livre. **Não é usado** —
a etiqueta leva o endereço do destinatário.

### `item_nota`

`nota_fiscal_id` FK, `n_item`, `produto_id` FK, `qtd_prevista`,
`qtd_separada` (cache), `status`, `fechado_em/_por`, `justificativa`,
`reaberto_em/_por`. UNIQUE(`nota_fiscal_id`, `n_item`).

`status`: `ABERTO` → `EM_SEPARACAO` → `FECHADO` | `FECHADO_DIVERGENCIA`

`qtd_prevista` vem de `det/prod/qCom`, com `uCom = CX` — já em caixas, sem
fator de conversão.

**O cadeado**

```
Separador (mobile)  ── só fecha com qtd_separada == qtd_prevista ──►  FECHADO
                                                                        │
Supervisor (desktop) ── fecha incompleto, justificativa           ──►  FECHADO_DIVERGENCIA
                        OBRIGATÓRIA item a item                         │
                                                                        ▼
                                              libera o separador para o próximo item
```

O supervisor pode **reabrir** um item fechado (chegou a mercadoria que
faltava), voltando-o para `EM_SEPARACAO`. Exclusão de separação também reabre
automaticamente.

Derivações: a NF é `CONCLUIDA` quando todos os itens estão em `FECHADO` ou
`FECHADO_DIVERGENCIA`. A NF tem divergência se existe ao menos um item
`FECHADO_DIVERGENCIA`.

---

## 7. Pallet

### `pallet`

`codigo` UNIQUE, `tipo` (`SEPARACAO` \| `DEVOLUCAO`), `status`,
`nota_fiscal_id` FK **nullable**, `nota_devolucao_id` FK **nullable**,
`usuario_atual_id` FK **nullable**, `fundido_em_pallet_id` FK **nullable**,
`criado_em/_por`, `impresso_em`, `encerrado_em/_por`.

Pallet de devolução (`P-DEV-…`) usa a mesma tabela, o mesmo endereçamento e a
mesma malha de 4.440 posições — só muda o vínculo, que é com a nota de
devolução em vez da nota de venda.

**Um pallet, um separador.** Cada separador monta o seu: `usuario_atual_id` é
gravado ao selecionar e liberado ao encerrar. Outro usuário que tente
selecionar um pallet `EM_USO` recebe *"Pallet em uso por JOÃO"*. Trabalhar na
mesma NF, ou até no mesmo item, não implica compartilhar pallet.

```
CRIADO  ──1ª leitura──►  EM_USO  ──menu──►  ENCERRADO  ──►  ENDERECADO  ──►  EXPEDIDO
(impresso, vazio)        (amarra à NF)      (imprime cartão)
                                                 │
                                                 ├── juntado a outro ──► FUNDIDO
                                                 └── esvaziado ────────► ESVAZIADO
```

`nota_fiscal_id` é gravado na **primeira leitura** e nunca muda: é a trava que
impede misturar NFs diferentes no mesmo pallet.

**Encerrar** só vale para pallet em montagem. Já `ENCERRADO`, `ENDERECADO`,
`EXPEDIDO`, `FUNDIDO` ou `ESVAZIADO` é recusado. Pallet endereçado ainda em
separação continua `EM_USO` e encerra normalmente.

**Retorno** (§9.3): o pallet que volta de NF expedida faz `EXPEDIDO →
ENCERRADO`, sem endereço, e entra na fila de endereçamento. Refaturamento e
descarte o levam a `ESVAZIADO`; desfazer a tratativa, de volta a `ENCERRADO`;
cancelar o retorno, de volta a `EXPEDIDO`. Enquanto o retorno está aberto o
pallet não é fundido, não tem caixa transferida nem excluída
(`PALLET_EM_RETORNO`): a conferência compara com a foto do registro.

### `pallet_movimentacao`

`tipo` (`FUSAO` \| `TRANSFERENCIA`), `pallet_origem_id` FK,
`pallet_destino_id` FK, `item_nota_id` FK **nullable**, `qtd_caixas`,
`movido_em/_por`, `justificativa`.

Duas operações que movem caixas entre pallets, com o mesmo mecanismo por baixo:

| Tipo | Onde | O que move | Destino |
|---|---|---|---|
| **`FUSAO`** | coletor | **tudo** da origem (`item_nota_id` nulo) | pallet existente |
| **`TRANSFERENCIA`** | retaguarda | N caixas de itens escolhidos | **pallet novo**, criado na hora, sem endereço |

Em ambos, as leituras movidas passam a apontar para o pallet destino e
`leitura.pallet_origem_id` (§8) registra onde a caixa foi bipada. Consulta de
conteúdo continua sendo um único FK, sem percorrer cadeia de movimentações, e a
auditoria não perde nada.

**Movimentação não é exclusão.** A quantidade separada do item não muda — as
caixas só trocaram de pallet. Cadeado não reabre, relatório não altera o
líquido.

**Fusão** — mesma NF (ou mesma devolução), mesmo `tipo` de pallet, nenhum dos
dois expedido nem em retorno aberto, origem nunca fundida antes. Origem vai
para `FUNDIDO` e libera a posição que ocupava; destino endereçado mantém a sua.

**Transferência** — o destino nasce `ENCERRADO` **sem endereço**, entrando na
fila de endereçamento do coletor. Origem esvaziada por completo vai para
`ESVAZIADO` e libera a posição. Origem em retorno aberto é recusada.

A **exclusão de separação** (§8) segue a mesma trava: pallet de retorno aberto
não perde caixa (`PALLET_EM_RETORNO`).

Nos dois casos os cartões afetados são **reimpressos**: um cartão declarando 45
caixas num pallet que tem 67 é pior que cartão nenhum.

**Repaletização (0.7.0, migração 7)** — caixa a caixa, bipando, entre dois
pallets da mesma NF (ou para um pallet novo, que assume a NF da origem), no
coletor e na retaguarda. Cada caixa vira uma `pallet_movimentacao`
`TRANSFERENCIA` de 1 caixa com justificativa *"Repaletização"* (o desfazer, uma
de volta com *"Repaletização desfeita"*), e a leitura muda de pallet com
`pallet_origem_id` preenchido, como na transferência. A caixa que passa é a
leitura mais recente daquele produto na origem.

#### `repaletizacao_caixa`

Uma linha por caixa passada: `idempotency_key` UNIQUE (o reenvio não move
outra caixa), `leitura_id`, `produto_id`, `pallet_origem_id`,
`pallet_destino_id`, `origem_status_anterior`, `destino_status_anterior`,
`origem_esvaziou`, `pallet_movimentacao_id`, `codigo_lido`, `usuario_id`,
`movido_em`, `desfeita_em/_por`, `concluida_em`. Índice em (`usuario_id`,
`pallet_origem_id`, `pallet_destino_id`).

É o que permite desfazer a **última** caixa do usuário (só as da sessão aberta:
`concluir` grava `concluida_em`) e voltar os pallets ao que eram: a origem que
esvaziou com ela volta a `ENCERRADO` (ou `EM_USO`), sem o endereço, que foi
liberado; o destino que era novo e ficou vazio de novo volta a `CRIADO`, sem NF.
A migração 7 só **acrescenta** a tabela e o índice.

### `enderecamento`

`pallet_id` FK, `posicao_id` FK, `enderecado_em/_por`, `liberado_em/_por`,
`motivo_liberacao` (`EXPEDICAO` \| `CORRECAO` \| `FUSAO` \| `TRANSFERENCIA`).

No retorno (§9.3) o refaturamento libera como `TRANSFERENCIA` — as caixas
seguem em pallets novos —, o descarte como `EXPEDICAO` — vão embora — e o
cancelamento como `CORRECAO`.

Posição ocupada = existe linha com `liberado_em IS NULL`. Índice único parcial
sobre (`posicao_id`) filtrado por `liberado_em IS NULL` garante **1 pallet por
nível** no próprio banco, não só na aplicação.

---

## 8. Leitura — o coração da auditoria

### `leitura`

| Campo | Nota |
|---|---|
| `idempotency_key` | UNIQUE — UUID gerado no coletor |
| `pallet_id`, `item_nota_id`, `produto_id` | FK |
| `pallet_origem_id` | FK null — preenchido na fusão, guarda onde foi bipada |
| `codigo_lido` | o que o scanner devolveu |
| `usuario_id`, `coletor_id` | quem e em qual aparelho |
| `lido_em` / `registrado_em` | relógio do coletor / do servidor |
| `cancelada`, `cancelada_em/_por`, `cancelamento_motivo` | `0` = está no pallet; `1` = exclusão de separação; `2` = faltou no retorno (reentrega com falta, §9.3) |
| `retorno_falta_id` | FK null (migração 6) — o retorno cuja reentrega tirou a caixa do pallet; só com `cancelada = 2` |

**Uma linha por caixa bipada.** `qtd_separada` = `COUNT` das leituras não
excluídas (`cancelada <> 1`; materializado no item por performance,
recalculável a qualquer momento). A caixa que faltou num retorno foi separada
e saiu: conta como separada, mas não está em pallet nenhum — toda consulta de
conteúdo de pallet usa `cancelada = 0`. Isso dá rastreabilidade caixa a caixa: quem bipou, quando, em que
pallet.

O `idempotency_key` é o que torna seguro reenviar uma leitura quando o wifi
oscila entre APs — o servidor descarta a duplicata em vez de contar duas vezes.

### `desembarque`

**Migração 16 (0.8.3).** `nota_fiscal_id` FK, `pallet_id` FK, `usuario_id` FK
(quem desembarcou, escolhido no cadastro), `status` (`ABERTO` \| `ENCERRADO` \|
`CANCELADO`), `aberto_em/_por` (o admin que lançou), `encerrado_em/_por`,
`justificativa`; e `leitura.desembarque_id` FK, nula nas leituras de bipe.

Um desembarque é **um pallet**: a carreta é descarregada de uma vez e o pallet
é montado por quantidade, na retaguarda, em vez de caixa a caixa no coletor.
Cada caixa lançada vira uma `leitura` normal — mesmo produto, mesmo pallet,
`usuario_id` do responsável, `coletor_id` e `lido_em` nulos — e é isso que faz
o resto do sistema (contagem do item, estoque, cartão, expedição, relatório)
enxergá-la sem caso especial. Tirar caixa lançada marca `cancelada = 1`, como
a exclusão: nada é apagado. O teto por item é `qtd_prevista - qtd_separada`, o
que impede passar do XML mesmo com o coletor bipando ao mesmo tempo.

### `item_em_separacao`

PK(`item_nota_id`, `usuario_id`), + `iniciado_em`, `pallet_id`.

Controla a regra de concorrência: **vários usuários podem estar no mesmo
item**; mas se `usuario.multi_item = false`, cada usuário só pode ter uma linha
aqui — precisa fechar antes de começar outro.

---

## 9. Expedição, estorno e retorno

Uma NF pode ser expedida mais de uma vez — sai, o cliente não recebe, volta e é
reentregue. Por isso `expedicao` é **1:N** com a nota: uma linha por tentativa
de entrega.

### `expedicao`

`nota_fiscal_id` FK, `expedido_em/_por`, `observacao`,
`estornado_em/_por`, `estorno_motivo`, `estorno_tipo`
(`ERRO_OPERACIONAL` \| `RETORNO_CLIENTE`).

Efeito ao expedir: NF → `EXPEDIDA`; pallets da NF → `EXPEDIDO`; endereçamentos
ativos → liberados com motivo `EXPEDICAO`.

### `expedicao_pallet`

`expedicao_id` FK, `pallet_id` FK, `retornado_em/_por`.

Registra quais pallets saíram em cada tentativa e quais voltaram — é o que
permite retorno parcial.

`retornado_em/_por` é gravado no registro do retorno (§9.3) — ou na reentrega
imediata (§9.2) — e limpo ao cancelar o retorno. **Na rua** é a última saída do
pallet, com a expedição não estornada, sem `retornado_em` e o pallet
`EXPEDIDO`: só esses entram num retorno. Reexpedir cria linha nova, e é assim
que o desfazer de uma reentrega descobre que a NF já saiu de novo.

### 9.1 Estorno por erro operacional

Desfaz uma expedição lançada por engano. Os pallets não saíram fisicamente.

- pallets voltam a `ENDERECADO`; a NF volta a `CONCLUIDA`, ou a
  `RETORNO_PARCIAL` se ainda tem pallet na rua de uma saída anterior
- os endereçamentos são **reativados nas posições originais**
- posição que tenha sido ocupada nesse meio tempo não é tomada de volta: o
  pallet volta como `ENCERRADO` sem endereço e entra na lista de pendentes de
  endereçamento
- exige justificativa
- recusado se algum pallet daquela expedição já voltou
  (`EXPEDICAO_COM_RETORNO` — cancele o retorno antes) ou se a NF tem retorno
  aberto (`RETORNO_EM_ABERTO`)

### 9.2 Reentrega imediata

Fluxo anterior à 0.6.0, mantido no servidor (`POST /reentregas`) por
compatibilidade; a retaguarda usa o retorno com conferência (§9.3). Recusado
com retorno aberto na NF.

A mercadoria saiu, **não foi recebida** e voltou inteira. Sempre na
granularidade de **pallet completo** — o cliente não chegou a abrir a carga.

Fluxo diferente do estorno: as posições originais **não** são restauradas,
porque o pallet esteve fora e o endereço provavelmente já foi reutilizado.

- seleciona-se quais pallets voltaram (pode ser parcial em número de pallets)
- pallets retornados → `ENCERRADO`, **sem endereço**, entrando na fila de
  endereçamento do coletor
- o cartão pallet pode ter se perdido no transporte: a retaguarda oferece
  **reimprimir cartão**
- retorno de **todos** os pallets → NF volta a `CONCLUIDA`, pronta para nova
  expedição
- retorno **parcial** → NF fica em `RETORNO_PARCIAL`
- exige motivo

Status da NF:

```
PENDENTE → EM_SEPARACAO → CONCLUIDA → EXPEDIDA
                              ↑           │
                              └───────────┤ estorno / reentrega total /
                                          │ retorno (recalculada, §9.3)
                                     RETORNO_PARCIAL ──► nova expedição
```

Estorno e retorno não devolvem a NF a uma situação guardada: olham onde estão
os pallets dela. `CONCLUIDA` se estão no CD e nada ficou na rua;
`RETORNO_PARCIAL` se parte está no CD e parte na rua; `EXPEDIDA` se nenhum
está no CD.

### 9.3 Retorno de NF expedida (0.6.0, migração 5)

Substitui na tela a reentrega imediata. A mercadoria saiu e voltou — recusa
total ou pallets para reentregar — e pode ter voltado **faltando**: extravio na
volta, caixa avariada que ficou pelo caminho. Por isso passa por conferência
antes de ter destino:

1. **Registro** (retaguarda): quais pallets voltaram e por quê, com a foto do
   que cada um levou;
2. **Conferência** caixa a caixa no coletor, ou "retorno completo" pelo
   supervisor, registrado como manual;
3. **Tratativa**: reentregar, refaturar para outras NFs ou descartar.

A migração 5 só **acrescenta**: cria as quatro tabelas abaixo e seus índices
(`CREATE TABLE IF NOT EXISTS`) e registra a versão 5 em `schema_versao`. Não
altera nem apaga nada do que existe. `nota_fiscal` e `pallet` ficam como estão:
mudar o `CHECK` delas exigiria recriar a tabela com os dados de produção
dentro. Por isso a NF não ganha situação nova (`RETORNO_PARCIAL` já existia) e
o pallet refaturado ou descartado usa o `ESVAZIADO` que a transferência já
usava. O que prende a NF é o retorno aberto.

#### `retorno`

| Campo | Nota |
|---|---|
| `nota_fiscal_id` | FK, indexado (`ix_retorno_nota`) |
| `motivo` | obrigatório |
| `status` | `EM_CONFERENCIA` \| `AGUARDANDO_TRATATIVA` \| `TRATADO` \| `CANCELADO` (`CHECK` no banco) |
| `nota_status_anterior` | situação da NF no registro (`EXPEDIDA` ou `RETORNO_PARCIAL`). Só histórico: desfazer e cancelar **recalculam** a situação pelos pallets, não voltam a esta |
| `registrado_em/_por` | |
| `conferido_em/_por` | quem fechou o último pallet, ou marcou o retorno completo |
| `conferencia_manual` | BOOL — há pallet fechado pelo "retorno completo" |
| `tratativa` | `REENTREGA` \| `REFATURAMENTO` \| `DESCARTE` (`CHECK` no banco); nulo até a tratativa |
| `nfs_refaturamento` | NFs novas, separadas por `, ` |
| `tratativa_obs` | observação; é a justificativa no descarte |
| `tratado_em/_por` | |
| `cancelado_em/_por`, `cancelamento_motivo` | |

**Um retorno aberto por NF** (aplicação): dois ao mesmo tempo disputariam os
mesmos pallets, e a NF não saberia para qual estado voltar.

#### `retorno_pallet`

`retorno_id` FK, `pallet_id` FK, `expedicao_pallet_id` FK (a saída de que ele
voltou), `qtd_esperada`, `status` (`ABERTO` \| `CONFERIDO` \|
`CONFERIDO_FALTA`), `manual` BOOL, `justificativa`, `fechado_em/_por`,
`status_pallet_tratativa`. Índices em `retorno_id` e `pallet_id`.

`qtd_esperada` é o total de caixas vivas do pallet no registro. `manual` marca o
fechado pelo retorno completo: conta como conferido inteiro, produto a produto,
sem leitura. `status_pallet_tratativa` guarda a situação que o pallet tinha
quando a tratativa o esvaziou — refaturamento, descarte, ou a reentrega com
falta de um pallet que não voltou nada; é limpo ao desfazer a tratativa.

#### `retorno_pallet_produto`

PK(`retorno_pallet_id`, `produto_id`), + `esperada`.

**A foto**: o que cada pallet levou, por produto, contado das leituras não
canceladas no momento do registro. A conferência compara com isto, e não com o
pallet de agora.

#### `retorno_leitura`

`idempotency_key` UNIQUE, `retorno_pallet_id` FK, `produto_id` FK,
`codigo_lido`, `usuario_id`, `lido_em` / `registrado_em`, `cancelada`,
`cancelada_em/_por`. Índice em (`retorno_pallet_id`, `cancelada`).

Mesma ideia de `leitura` (§8): uma linha por caixa conferida, e conferido =
`COUNT` das não canceladas. Tabela separada porque a caixa não muda de pallet
nem de item — só confirma que voltou. A chave de idempotência é o que torna
seguro reenviar a leitura que ficou sem resposta.

**Nada apaga leitura**: desfazer a última marca `cancelada`, e o conferido fica
no histórico, inclusive num retorno cancelado.

#### Estados

```
                 registrar (pallets + motivo)
                             │
                             ▼
       ┌──────────────► EM_CONFERENCIA ──────── cancelar ────────► CANCELADO
       │ reabrir               │                                      ▲
       │ pallet                │ último pallet fechado                │
       │                       │ ou "retorno completo"                │
       │                       ▼                                      │
       └────────────── AGUARDANDO_TRATATIVA ─────── cancelar ─────────┘
                          │          ▲
                tratativa │          │ desfazer tratativa
                          ▼          │
                         TRATADO ────┘
```

Pallet do retorno: `ABERTO` ── fechar ──► `CONFERIDO` (completo) ou
`CONFERIDO_FALTA` (com justificativa, inclusive com zero caixa — o pallet que
nem voltou). Reabrir, só antes da tratativa, volta a `ABERTO` e leva o retorno
de novo a `EM_CONFERENCIA`.

#### Efeito em cada passo

| Passo | `expedicao_pallet` | `pallet` | `nota_fiscal` |
|---|---|---|---|
| Registrar | `retornado_em/_por` gravados | `EXPEDIDO` → `ENCERRADO`, sem endereço | não muda |
| Tratativa REENTREGA | — | fica no CD; com falta, as caixas que faltaram saem do pallet (`leitura.cancelada = 2`) e o que ficou sem nenhuma vai a `ESVAZIADO`, endereço liberado (`CORRECAO`) | ver abaixo |
| Tratativa REFATURAMENTO | — | `ESVAZIADO`, endereço liberado (`TRANSFERENCIA`) | ver abaixo |
| Tratativa DESCARTE | — | `ESVAZIADO`, endereço liberado (`EXPEDICAO`) | ver abaixo |
| Desfazer tratativa | — | refaturado/descartado/esvaziado pela falta → `ENCERRADO`, sem endereço; caixas tiradas por falta voltam ao pallet | recalculada pelos pallets |
| Cancelar | `retornado_em/_por` limpos | `EXPEDIDO`, endereço liberado (`CORRECAO`) | recalculada pelos pallets |

Situação da NF depois da tratativa: com pallet dela no CD (`EM_USO`,
`ENCERRADO`, `ENDERECADO`), `CONCLUIDA` se nada ficou na rua e
`RETORNO_PARCIAL` se ficou — pronta para sair de novo; sem nenhum, continua
`EXPEDIDA`: foi faturada e saiu, e o destino da mercadoria está no retorno.

**Travas de desfazer.** Reentrega não se desfaz se a última saída de algum
pallet já não é a de que ele voltou — a NF saiu de novo, a mercadoria já foi.
Refaturamento e descarte não se desfazem com pallet fora de `ESVAZIADO`: foi
reutilizado, e voltá-lo criaria estoque que não existe. Cancelar só antes da
tratativa, e com todos os pallets ainda `ENCERRADO` ou `ENDERECADO`.

**Estoque.** Enquanto no CD, o pallet de retorno conta no estoque. Com o
retorno ainda aberto, o pallet fechado com falta no coletor (não manual) conta
as caixas **conferidas**, não as que saíram; depois da tratativa valem as
leituras de novo — na reentrega a falta já saiu do pallet.

**Reentrega com falta (migração 6).** A NF sai com o que voltou, e a falta vai
por NF de devolução do cliente. Na tratativa, para cada produto de cada pallet
`CONFERIDO_FALTA` não manual, `esperada − conferida` leituras do pallet (as mais
recentes daquele produto) passam a `cancelada = 2`, com `retorno_falta_id`, e
`cancelamento_motivo` *"Faltou no retorno da NF …: justificativa"*. Toda
consulta de conteúdo usa `cancelada = 0`, então expedição, cartão, estoque,
roteiro e fila passam a ver só o que voltou, sem mudança nelas. Não é exclusão:
`qtd_separada` e os relatórios de separação e produtividade continuam contando
essas caixas (só `cancelada = 1` desconta), e o item não reabre. O pallet que
ficou sem nenhuma caixa vai a `ESVAZIADO` e a situação anterior dele fica em
`status_pallet_tratativa`. Desfazer a tratativa devolve as leituras
(`cancelada = 0`, `retorno_falta_id` nulo) e o pallet esvaziado a `ENCERRADO`,
sem endereço. Reentregar quando nada voltou é recusado. A migração 6 só
acrescenta a coluna `leitura.retorno_falta_id` e um índice parcial nela.

Cada passo grava em `log_auditoria` com `entidade = 'retorno'`:
`RETORNO_REGISTRADO`, `PALLET_REABERTO`, `RETORNO_COMPLETO_MANUAL`,
`TRATATIVA`, `TRATATIVA_DESFEITA`, `RETORNO_CANCELADO`.

---

## 9A. Devolução

Caso distinto do retorno (§9.3): o cliente **recebeu**, abriu a carga e devolve
parte dela. A granularidade é **item e quantidade**, não pallet, e a devolução
chega acompanhada de **NF própria emitida pelo cliente**.

### `nota_devolucao`

`chave` UNIQUE, `numero`, `serie`, `emissao`, `emitente_cnpj`, `emitente_nome`,
`nota_fiscal_origem_id` FK **nullable**, `status`, `xml_original` BLOB,
`importado_em/_por`, e a origem: `origem_tipo` (`SISTEMA` \| `XML` \|
`DIGITADA`), `origem_numero`, `origem_serie`, `origem_chave`,
`origem_emissao`, `origem_xml` BLOB. Índice em `nota_fiscal_origem_id`.

A NF de venda de origem vem de um de três jeitos: escolhida entre as do
sistema (`nota_fiscal_origem_id`), lida do XML dela (número, série, chave e
emissão; se o sistema tiver a chave, liga também o FK) ou só digitada
(`origem_numero`). Uma NF de venda pode ter **várias** devoluções — o cliente
devolve uma parte hoje e outra depois.

**Migração 8.** Até a 0.7 o FK era `NOT NULL UNIQUE` (1:1, sempre NF do
sistema). SQLite não altera constraint: a tabela é recriada numa transação
(`nota_devolucao_nova` → cópia → `DROP` → `RENAME`), com `foreign_keys`
desligado só durante a troca. As colunas antigas ficam na mesma ordem e com os
mesmos ids — `item_devolucao`, `leitura_devolucao`, `baixa_devolucao` e
`pallet` continuam apontando para as mesmas linhas —, e as devoluções que já
existiam ganham `origem_tipo = 'SISTEMA'` com número, série, chave e emissão da
NF a que já apontavam. Ou vai tudo ou nada muda.

**Apagar (0.7.2).** Devolução lançada por engano sai de verdade — é a exceção
ao "nada apaga", porque nada físico dependia dela ainda. Sem conferência:
`item_devolucao` e `nota_devolucao` são apagados. Com conferência começada, só
com confirmação explícita: as `leitura_devolucao` vão junto, os endereços
ativos dos pallets P-DEV dela são liberados (`CORRECAO`) e os pallets voltam a
`CRIADO`, sem `nota_devolucao_id` — a mercadoria continua neles para a
devolução certa. `BAIXADA` (com `baixa_devolucao`) nunca se apaga. Cada
exclusão grava `DEVOLUCAO_APAGADA` em `log_auditoria`, com número, chave,
emitente, origem, leituras, pallets e a justificativa.

`status`: `AGUARDANDO_CONFERENCIA` → `EM_CONFERENCIA` → `CONFERIDA` → `BAIXADA`

**Conferência de estoque — a sobra (migração 14, 0.8.2).** Coluna `sobra`
(INTEGER NOT NULL DEFAULT 0). Sobra é mercadoria que está no CD sem NF nenhuma:
ela entra como uma devolução **sem NF** — `chave` e `numero` saem da sequência
`SOBRA` (`SOBRA-000001`), `serie = '0'`, emitente e cliente `SOBRA DE ESTOQUE`,
`sobra = 1`. A escolha foi reaproveitar a devolução inteira em vez de criar
tabelas e um tipo de pallet novos: a sobra herda pallet P-DEV, conferência no
coletor, estoque, endereçamento, baixa e planilha, e a tabela `pallet` em
produção não precisou ser recriada. Os itens nascem na conferência, um por
produto, com `xprod_cliente = 'Sobra de estoque'`; sem NF dizendo quanto era,
`qtd_esperada_un` acompanha `qtd_conferida_un`.

### `item_devolucao`

`nota_devolucao_id` FK, `n_item`, `cprod_cliente`, `xprod_cliente`,
`cean_cliente`, `produto_id` FK (o de-para), `qtd_esperada_un`,
`qtd_conferida_un`, `status`, `divergencia_justificativa`.

**Produto trocado (migração 9, 0.7.4).** `troca_de_item_id` FK para o próprio
`item_devolucao`, `troca_justificativa`, `troca_em`, `troca_por` FK `usuario`.
Nulos no item que veio na NF; preenchidos no item criado na retaguarda para o
produto que o cliente mandou no lugar do declarado. Esse item tem `n_item`
depois do último da NF e `cean_cliente` nulo, e é conferido como os outros. A
migração só acrescenta colunas.

**Motivo (migração 10, 0.7.7).** `nota_devolucao.motivo` e `item_devolucao.motivo`,
texto. O do item só é gravado quando difere do da NF; nulo = segue a NF.

**Tudo em unidades.** Diferente da separação, que só conta caixas fechadas, a
devolução pode vir de uma lata avulsa. Por isso as quantidades da devolução são
armazenadas na **menor unidade**, convertidas na entrada por
`produto.unidades_por_caixa`. A tela apresenta o total de forma legível —
`2 cx + 5 un (53)`.

**O de-para.** O XML vem do cliente, com o `cProd` dele — que não é o da EMPRESA.
Mas o `cEAN` é GTIN, código global: o mesmo GTIN-14 da caixa aparece nos dois
XMLs. O sistema **sugere o vínculo automaticamente pelo `cEAN`** e o operador
apenas confirma, ajustando manualmente o que não casar (produto sem GTIN,
cadastro divergente do cliente).

### `leitura_devolucao`

**0.7.5.** `pallet_id` passa a ser exigido pela aplicação em toda leitura nova
(a coluna segue nula nas antigas, que a retaguarda põe num pallet). A
retaguarda muda `pallet_id` das leituras marcadas (pôr no pallet) e usa
`cancelada`/`cancelada_em`/`cancelada_por`/`cancelamento_motivo` para excluir
caixa — nada é apagado. Toda contagem (estoque, box, consulta, conferido do
item) considera só `cancelada = 0`.

**0.8.2.** `cancelada = 3` é a caixa de sobra que foi baixada (ver
`baixa_sobra`): sai das contagens como a excluída, mas a razão é outra — ela
saiu do CD, não foi erro de leitura.

Mesma estrutura de `leitura` (§8), apontando para `item_devolucao`, mais
`qtd_un` e `modo` (`BIPE` \| `DIGITADO`). Tabela separada porque o movimento é
de entrada, não de saída.

A conferência reusa a lógica da separação — normalização do código, recusa de
item errado, cadeado ao completar — com uma diferença de contagem:

| Modo | Efeito |
|---|---|
| **Bipe** da caixa | `+= unidades_por_caixa` — bipar a caixa presume caixa completa |
| **Digitado** | `+= N unidades` — para devolução de latas avulsas |

Bipe continua sendo o caminho normal e rápido; a digitação existe para a
sobra que não fecha caixa. Toda leitura digitada fica marcada como tal, então o
relatório distingue o que foi conferido por leitura do que foi informado à mão.

Produto sem `unidades_por_caixa` preenchido não aceita bipe na conferência — a
tela pede o cadastro antes, em vez de contar errado.

### `baixa_devolucao_estornada`

**Migração 16 (0.8.3).** Cópia integral da baixa desfeita —
`nota_devolucao_id`, `tipo`, `observacao`, `nf_revenda_referencia`,
`baixado_em/_por` — mais `estornado_em`, `estornado_por` e `motivo`.

`baixa_devolucao.nota_devolucao_id` é UNIQUE: uma baixa por NF. Para a NF poder
ser baixada de novo depois do estorno, a linha sai de lá — e, para nada se
perder, chega aqui inteira. É o histórico de uma saída que chegou a ser
registrada. Por isso, devolução com baixa estornada (ou com `baixa_sobra`) não
pode mais ser apagada: `DEVOLUCAO_COM_BAIXA`.

### `baixa_sobra`

**Migração 15 (0.8.2).** `nota_devolucao_id` FK, `item_devolucao_id` FK,
`qtd_un`, `tipo` (`DESCARTE` \| `REVENDA`), `observacao`, `nf_revenda`,
`baixado_em/_por`; e `leitura_devolucao.baixa_sobra_id` FK, nula enquanto a
caixa está no estoque.

A sobra sai em partes — algumas caixas de um produto hoje, o resto depois —,
então a baixa dela é por caixa, não pela nota inteira. As caixas saem das
leituras mais antigas para as mais novas; a leitura maior que o pedido se
divide em duas (o que fica e o que sai). A caixa que sai **não é apagada**:
ganha `cancelada = 3` (3 = baixada, ao lado de 0 = no pallet e 1 = excluída),
com quem, quando, por quê e o `baixa_sobra_id` da baixa que a levou. Pallet
que fica vazio vai a `EXPEDIDO` liberando o endereço, e saindo a última caixa
a conferência ganha o `baixa_devolucao` e vai a `BAIXADA`, como qualquer
devolução.

### `baixa_devolucao`

`nota_devolucao_id` FK, `tipo` (`DESCARTE` \| `REVENDA`), `baixado_em/_por`,
`observacao`, `nf_revenda_referencia`.

- **Descarte** — a mercadoria sai do sistema definitivamente.
- **Revenda** — sai do estoque de devolução; origem emite nova NF, que entra
  pelo importador normal de XML como qualquer outra instrução. O campo de
  referência guarda o número da nova NF, ligando as duas pontas.

Em ambos os casos os pallets de devolução são liberados dos endereços que
ocupavam.

### 9A.1 Estoque de devolução

Conferida a devolução, a mercadoria **vira pallet** (`P-DEV-000001`) e é
endereçada pelo coletor na mesma malha de posições da separação. Fica em
estoque, ocupando endereço, até a baixa por descarte ou revenda.

Consequência prática: a consulta de endereçamento e o mapa de ocupação passam a
mostrar os dois tipos de pallet, distinguidos pelo prefixo.

### `log_auditoria`

`entidade`, `entidade_id`, `acao`, `usuario_id`, `em`, `detalhe_json`.

---

## 10. Invariantes (travas de banco + aplicação)

1. Não excluir NF ou instrução que tenha qualquer leitura não cancelada.
2. Um pallet pertence a exatamente uma NF (gravado na 1ª leitura).
3. Leitura exige pallet selecionado, em status `CRIADO` ou `EM_USO`.
4. Leitura em pallet `ENCERRADO`, `FUNDIDO` ou `EXPEDIDO` é recusada.
4a. Pallet `EM_USO` pertence a um único separador; outro usuário não o
    seleciona enquanto não for encerrado.
4b. Fusão exige mesma NF, mesmo tipo, origem nunca fundida e nenhum dos dois
    expedido. O cartão do destino é sempre reimpresso.
5. Leitura em item `FECHADO`/`FECHADO_DIVERGENCIA` é recusada, com aviso
   explícito na tela do coletor ("item fechado pelo supervisor às HH:MM").
6. Separador só fecha cadeado com `qtd_separada == qtd_prevista`.
7. Fechamento com divergência exige justificativa **item a item**.
8. Sequenciais nunca são reaproveitados.
9. Uma posição comporta no máximo um pallet ativo.
10. Um código de barras aponta para no máximo um produto.
11. Instrução só conclui com todos os itens fechados. Havendo divergência, a
    tela lista as NFs afetadas com as justificativas já registradas e pede
    confirmação.
12. Só é possível expedir NF com status `CONCLUIDA` ou `RETORNO_PARCIAL`.
13. Estorno e retorno exigem justificativa e nunca reutilizam posição já
    ocupada por outro pallet.
14. Instruções concluídas e expedidas permanecem nas telas operacionais por
    **2 anos**; depois seguem visíveis apenas na consulta histórica.
15. NF com retorno `EM_CONFERENCIA` ou `AGUARDANDO_TRATATIVA` não é expedida
    nem reentregue (`RETORNO_EM_ABERTO`) e fica fora das prontas para expedir.
    Um retorno aberto por NF.
16. Leitura de retorno só em pallet `ABERTO` de retorno `EM_CONFERENCIA`, de
    produto que saiu naquele pallet e até a quantidade da foto.
17. Pallet de retorno com falta só fecha com justificativa.
18. Tratativa só com a conferência encerrada. Desfazer recusa NF reexpedida ou
    pallet reutilizado; cancelar só antes da tratativa.

---

## 11. Telas

### Retaguarda (navegador, sem instalação)

1. Instruções — listar, criar, importar XML, excluir NF, excluir instrução, concluir
2. Acompanhamento — instrução → NFs → itens; fechar/reabrir cadeado com justificativa
3. Excluir separação
4. Produtos — cadastro, códigos de barras, fila de códigos não identificados
5. Usuários — perfil, permissão `multi_item`, impressora padrão
6. Impressoras
7. Boxes — importação do XLS
8. **Consulta de endereçamento por NF**
9. Baixar NF expedida · retorno de mercadoria (registro, tratativa, desfazer)
10. Relatório de separação — quem, quando, quanto

### Mobile (TC8300)

1. Login (usuário + senha numérica)
2. Menu: instruções em aberto · criar pallet · endereçamento de pallets ·
   conferir (devolução e retorno) · sair
3. Criar pallet — quantidade e impressão (80 × 50 mm, Code 128)
4. Instrução → NFs → itens (com cadeado) → separação
5. Menu ⋮ da separação: encerrar pallet (imprime cartão 80 × 220 mm) ·
   trocar pallet · excluir separação
6. Endereçamento — lê pallet, lê box, grade de 6 níveis (azul = ocupado,
   branco = livre), botão "Endereçar"

---

## 12. Pendências

- Planilha da unidade de origem, para conferência cruzada dos `cEAN`. Único
  item em aberto, e não bloqueia — o sistema opera a partir do XML.

## 13. Confirmado com o cliente

- Código de barras fixo por produto, idêntico entre lotes diferentes.
- É o `cEAN` do XML (amostra do produto `2685`).
- Simbologia da caixa: **Code 128 simples**, 14 dígitos, sem AI GS1 nem lote.
- Endereço da etiqueta: destinatário (`enderDest`), não o redespacho do `infCpl`.
- Antecâmara/DOCA fora da importação; os 740 boxes das câmaras são válidos,
  inclusive os 2 de picking.
- Justificativa de divergência é **item a item**.
- Supervisor pode reabrir item fechado.
- Instrução só conclui com todos os itens fechados; só expede NF concluída.
