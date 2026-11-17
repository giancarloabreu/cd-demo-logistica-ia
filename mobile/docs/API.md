# Contrato da API

Backend Ktor na rede local. Consumido pelo coletor (Kotlin/Retrofit) e pela
retaguarda (navegador). Complementa `MODELO-DE-DADOS.md`, `TELAS-COLETOR.md`,
`TELAS-RETAGUARDA.md` e `OPERACAO.md`.

---

## 1. Convenções

**Base:** `http://<servidor>:8080/api/v1` · JSON UTF-8 em tudo.

**Autenticação:** `Authorization: Bearer <token>` em toda rota exceto
`/saude` e `/auth/login`.

**Datas:** ISO-8601 com offset (`2026-09-05T14:32:10-03:00`). O servidor grava
em UTC; o cliente envia o que seu relógio marca e o servidor carimba o seu.

**Erros** — sempre o mesmo envelope, com código estável para o cliente decidir
o que fazer, e `mensagem` já em português pronta para exibir:

```json
{
  "erro": {
    "codigo": "PALLET_DE_OUTRA_NOTA",
    "mensagem": "Este pallet pertence à NF 952918",
    "detalhe": { "palletCodigo": "P-RED-000123", "notaNumero": "952918" }
  }
}
```

| HTTP | Uso |
|---|---|
| 200 / 201 | sucesso |
| 400 | payload malformado |
| 401 | token ausente, inválido ou expirado |
| 403 | perfil sem permissão para a ação |
| 404 | recurso inexistente |
| **409** | **regra de negócio violada** — o caso mais comum aqui |
| 422 | payload bem formado, valor inválido |

**Idempotência.** Rotas que criam movimento aceitam `Idempotency-Key` no
cabeçalho. Repetição da mesma chave devolve o resultado original sem duplicar
— é o que sustenta o reenvio de leitura quando o wifi oscila.

**Paginação:** `?pagina=1&tamanho=50`, resposta com
`{ "itens": [...], "total": 245, "pagina": 1 }`.

---

## 2. Saúde e sessão

### `GET /saude` — aberta

```json
{
  "versao": "1.4.2",
  "horaServidor": "2026-09-05T14:32:10-03:00",
  "bancoOk": true,
  "versaoMinimaApp": "1.4.0"
}
```

Usada pelo "Testar conexão" do coletor e pelo controle de atualização
obrigatória do APK.

### `POST /auth/login`

```json
{ "login": "joao", "senha": "4821", "dispositivoId": "TC8300-A7F3" }
```

```json
{
  "token": "eyJ…",
  "usuario": {
    "id": 12, "login": "joao", "nome": "JOÃO SILVA", "perfil": "SEPARADOR",
    "multiItem": false,
    "impressoraPadrao": { "id": 3, "nome": "ZEBRA CAMARA 4" }
  }
}
```

`POST /auth/logout` encerra a sessão.

---

## 3. Coletor

### 3.1 Navegação

| Rota | Devolve |
|---|---|
| `GET /instrucoes?status=ABERTAS` | instruções `ABERTA` e `EM_SEPARACAO`, com progresso |
| `GET /instrucoes/{id}/notas` | NFs com previsto, separado e status |
| `GET /notas/{id}/itens` | itens com previsto, separado, cadeado e quem está separando |
| `GET /itens/{id}` | detalhe do item para a tela de separação |

### 3.2 Pallets

**`POST /pallets`** — cria e imprime.

```json
{ "tipo": "SEPARACAO", "quantidade": 5, "impressoraId": 3 }
```

```json
{
  "pallets": ["P-RED-000123", "…", "P-RED-000127"],
  "impressao": "ENVIADA"
}
```

`impressao` nunca afirma que o papel saiu — a porta 9100 não confirma. Daí
existir `POST /pallets/{codigo}/reimprimir`.

**`POST /pallets/{codigo}/selecionar`** — vincula o pallet ao separador.

Erros possíveis: `PALLET_NAO_ENCONTRADO`, `PALLET_ENCERRADO`,
`PALLET_DE_OUTRA_NOTA`, `PALLET_EM_USO_POR_OUTRO`.

**`POST /pallets/{codigo}/encerrar`** — encerra o pallet ativo do próprio
usuário e imprime o cartão. `PALLET_NAO_E_SEU` se tentar encerrar o de outro;
`PALLET_ENCERRADO` se já estiver encerrado. Só encerra pallet em montagem:
também recusa (409) pallet já `ENDERECADO`, `EXPEDIDO`, `FUNDIDO` ou
`ESVAZIADO`. Pallet endereçado ainda em separação continua `EM_USO` e encerra
normalmente.

**`POST /pallets/fusao`**

```json
{ "origem": "P-RED-000124", "destino": "P-RED-000123" }
```

Erros: `PALLETS_DE_NOTAS_DIFERENTES`, `PALLET_JA_FUNDIDO`,
`PALLET_JA_EXPEDIDO`, `TIPOS_INCOMPATIVEIS` e `PALLET_EM_RETORNO` (origem ou
destino é pallet de retorno aberto, §4.4B). Só entre pallets da mesma NF — ou
para um pallet novo, que assume a NF da origem. O pallet novo fica `ENCERRADO`, na fila de
endereçamento (0.7.8; antes ficava `CRIADO` e sumia do estoque e da expedição —
a migração 11 devolve ao estoque os que ficaram assim).

**Pallet de devolução (0.7.5)** — a fusão também junta dois P-DEV, da mesma
NF de devolução: passam as leituras da devolução, o destino sem NF assume a da
origem e vai a `EM_USO`, e não sai cartão (ele lista itens da NF de venda).
Antes movia zero caixa e recusava com `PALLET_VAZIO`. A repaletização caixa a
caixa continua só de separação (`PALLET_DE_DEVOLUCAO`): na devolução, a caixa
muda de pallet na retaguarda (TELAS-RETAGUARDA.md §10.8).

**Repaletização (0.7.0)** — a mesma tela da fusão, com o interruptor "juntar o
pallet inteiro" desligado: bipa a origem, o destino e as caixas, uma a uma.
Qualquer usuário; também na retaguarda.

| Rota | Corpo | Resposta |
|---|---|---|
| `POST /pallets/repaletizacao/abrir` | `{ "origem", "destino" }` | `Repaletizacao` — só confere o par |
| `POST /pallets/repaletizacao/caixa` | `{ "idempotencyKey", "origem", "destino", "codigoLido", "lidoEm"? }` | `Repaletizacao`, com a caixa passada |
| `POST /pallets/repaletizacao/desfazer` | `{ "origem", "destino" }` | `Repaletizacao` — volta a última caixa que **este** usuário passou |
| `POST /pallets/repaletizacao/concluir` | `{ "origem", "destino" }` | `{ "repaletizacao", "impressao": { "<pallet>": "ENVIADA" \| … } }` |

`Repaletizacao` = `{ notaNumero, cliente, origem, destino, produtoCodigo,
produtoDescricao, origemEsvaziada, movidas }`, com `origem` e `destino` no formato
de `GET /pallets/{codigo}/conteudo-detalhado` (`codigo`, `status`, `notaNumero`,
`cliente`, `endereco`, `totalCaixas`, `itens[]`). `movidas` são as caixas que o
usuário passou nesta sessão; `concluir` fecha a sessão (a partir dali `movidas`
e o desfazer zeram para o par), imprime o cartão do destino e o da origem, se
ela ainda tiver caixa, e responde em `movidas` quantas passaram.

A caixa que passa é a leitura mais recente daquele produto na origem. A mesma
`idempotencyKey` reenviada não move outra caixa: devolve a situação. Origem que
esvazia vai a `ESVAZIADO` e libera o endereço; destino novo (`CRIADO`, sem NF)
assume a NF da origem e fica `ENCERRADO`, na fila de endereçamento. Movimentar
não é excluir: a quantidade separada não muda.

Erros: `MESMO_PALLET` e `IDEMPOTENCY_KEY_OBRIGATORIA` (422);
`PALLETS_DE_NOTAS_DIFERENTES`, `PALLET_DE_DEVOLUCAO`, `PALLET_VAZIO`,
`PALLET_EM_USO_POR_OUTRO` (destino sem NF na mão de um separador),
`PALLET_JA_EXPEDIDO`, `PALLET_JA_FUNDIDO`, `PALLET_ENCERRADO`,
`PALLET_EM_RETORNO`, `CODIGO_NAO_CADASTRADO`, `PRODUTO_FORA_DO_PALLET`,
`NADA_A_DESFAZER`, `CAIXA_JA_MOVIDA` (409).

**`GET /pallets/{codigo}/consulta`** (0.7.0) — a tela *Consultar pallet*: de
qual NF é o pallet e onde estão os outros dela, cada um com o conteúdo.

```json
{
  "codigo": "P-RED-000124", "tipo": "SEPARACAO", "status": "ENDERECADO",
  "notaNumero": "952918", "cliente": "…", "notaStatus": "CONCLUIDA", "unidade": "cx",
  "pallets": [
    { "codigo": "P-RED-000124", "status": "ENDERECADO", "endereco": "C8-D28 nível 3",
      "deposito": "CAMARA 8 - CONGELADO", "quantidade": 48, "itens": [ … ], "consultado": true },
    { "codigo": "P-RED-000123", "status": "EXPEDIDO", "endereco": null, "deposito": null,
      "quantidade": 48, "itens": [ … ], "consultado": false }
  ]
}
```

O bipado vem primeiro; fundidos e esvaziados não entram. Cada pallet traz
também `box` e `nivel` separados (0.7.2), nulos sem endereço — é por eles que a
conferência de box abre o prédio a partir da etiqueta do pallet. Pallet de devolução
mostra a NF de devolução e conta em unidades (`"unidade": "un"`). Erro:
`PALLET_NAO_ENCONTRADO`.

### 3.3 Leitura — a rota crítica

**`POST /leituras`** · `Idempotency-Key` obrigatório

```json
{
  "idempotencyKey": "a3f1…",
  "palletCodigo": "P-RED-000123",
  "itemId": 4471,
  "codigoLido": "27890000217084",
  "lidoEm": "2026-09-05T14:32:10-03:00"
}
```

Sucesso:

```json
{
  "resultado": "ACEITA",
  "qtdSeparada": 13,
  "qtdPrevista": 20,
  "itemCompleto": false
}
```

Recusa — **HTTP 409**, com o código que o app usa para escolher o som e o texto
do diálogo:

| `codigo` | Situação |
|---|---|
| `PALLET_NAO_SELECIONADO` | nenhum pallet ativo |
| `PALLET_DE_OUTRA_NOTA` | pallet já amarrado a outra NF |
| `ITEM_FECHADO` | supervisor fechou; `detalhe.fechadoEm` alimenta a mensagem |
| `CODIGO_NAO_CADASTRADO` | vai para a fila de pendências |
| `PRODUTO_DIVERGENTE` | `detalhe.produtoLido` traz a descrição do que foi bipado |
| `QUANTIDADE_COMPLETA` | já atingiu o previsto |

O servidor normaliza o código antes de comparar (GS1 §5.1 do modelo). O
**debounce de 400 ms é do app**, não da API — evita a ida ao servidor.

**`POST /itens/{id}/fechar`** — separador; exige `separado == previsto`, senão
`ITEM_INCOMPLETO` com as duas quantidades no detalhe.

**`POST /separacao/excluir`**

```json
{ "palletCodigo": "P-RED-000123", "itemId": 4471, "quantidade": 2 }
```

Erros: `PALLET_JA_EXPEDIDO`, `QUANTIDADE_INSUFICIENTE` e `PALLET_EM_RETORNO`
(pallet de retorno aberto: a conferência compara com o que ele levou).

### 3.4 Devolução

`GET /devolucoes?status=PENDENTES` · `GET /devolucoes/{id}/itens`

**`POST /leituras-devolucao`** · `Idempotency-Key`

```json
{
  "idempotencyKey": "b7c2…",
  "palletCodigo": "P-DEV-000004",
  "itemDevolucaoId": 88,
  "modo": "BIPE",
  "codigoLido": "27890000217084",
  "lidoEm": "2026-09-05T15:10:00-03:00"
}
```

`modo: "BIPE"` soma `unidadesPorCaixa`; `modo: "DIGITADO"` exige `qtdUn` e
dispensa `codigoLido`. Bipe em produto sem unidades por caixa cadastradas
retorna `UNIDADES_POR_CAIXA_NAO_CADASTRADA`.

**Conferência de estoque (sobra; 0.8.2).** A devolução com `sobra = true` não
tem item esperado: o separador bipa **qualquer** caixa pela
`POST /devolucoes/{id}/sobra/leitura` (§4.4), uma caixa por bipe, e o pallet
P-DEV vem antes como na conferência. Várias caixas do mesmo produto entram
pelo `itemId` do item que o bipe já criou.

### 3.4A Retorno

Conferência caixa a caixa dos pallets de NF expedida que voltou. Rotas, corpos
e erros em §4.4B; o coletor usa `GET /retornos?status=EM_CONFERENCIA`,
`GET /retornos/{id}`, `POST /retornos/leituras`,
`POST /retornos/pallets/{id}/fechar`, `POST /retornos/pallets/{id}/desfazer-ultima`
e, para o cartão perdido no caminho, `POST /pallets/{codigo}/cartao`.

Servidor anterior à 0.6.0 responde 404 em `/retornos`: o app esconde a seção de
retornos e segue conferindo devolução.

### 3.5 Endereçamento

| Rota | Uso |
|---|---|
| `GET /pallets/{codigo}/enderecamento` | valida o pallet e devolve conteúdo e endereço atual |
| `GET /boxes/{codigo}/posicoes` | os níveis, do mais alto ao mais baixo, cada um livre ou com o pallet que o ocupa: `ocupadaPor`, `notaNumero`, `qtdCaixas` e, desde a 0.7.2, `cliente` e `tipoPallet` (pallet de devolução: NF de devolução e quantidade em unidades). Nível dividido por bonificação vem uma vez por pallet. Usado no endereçamento e na conferência de box |
| `POST /enderecamentos` | `{ "palletCodigo", "posicaoId" }` |

Erros: `PALLET_NAO_ENCERRADO`, `POSICAO_OCUPADA`, `BOX_NAO_ENCONTRADO`.

### 3.6 Atualização do app

`GET /app/versao` → `{ "versao": "1.4.2", "obrigatoria": true, "url": "/app/apk" }`
`GET /app/apk` → binário.

---

## 4. Retaguarda

### 4.1 Instruções

| Rota | Uso |
|---|---|
| `POST /instrucoes` | cria e devolve o `RED-000001` |
| `POST /instrucoes/{id}/xmls/previa` | multipart, N arquivos → **prévia, não grava** |
| `POST /instrucoes/{id}/xmls` | confirma a importação das NFs escolhidas |
| `DELETE /notas/{id}` · `DELETE /instrucoes/{id}` | 409 `SEPARACAO_INICIADA` se houver leitura |
| `POST /instrucoes/{id}/concluir` | `{ "confirmarDivergencias": true }` |
| `GET /notas/{id}/desembarque` | o desembarque **aberto** desta NF, ou 204 (admin; 0.8.3). É por ele que a tela retoma o pallet em montagem |
| `POST /notas/{id}/desembarque` | `{ "responsavelId", "impressoraId" }` — abre o desembarque (admin): cria um pallet P-RED já amarrado à NF, `EM_USO`, com o responsável escolhido no cadastro, e manda a etiqueta para a impressora. 409 `NOTA_JA_EXPEDIDA`, `DESEMBARQUE_EM_ABERTO` (com `palletCodigo`), `USUARIO_INATIVO`. Resposta `{ desembarque, impressao }` |
| `POST /desembarques/{id}/itens` | `{ "itens": [{ "itemId", "caixas" }] }` — quantas caixas de cada item vão **neste** pallet. O número é o total, não o acréscimo: menos do que havia cancela as leituras deste desembarque (as mais novas primeiro), nunca as do coletor. Teto por item: o que falta na NF mais o que já está no pallet — 409 `QUANTIDADE_MAIOR_QUE_A_NF` (com `limite` e `falta`). Item completo fecha o cadeado sozinho, e tirar caixa reabre. 409 `DESEMBARQUE_ENCERRADO`, `ITEM_FECHADO` (fechado com divergência); 422 `NADA_A_LANCAR`. Resposta: o desembarque |
| `POST /desembarques/{id}/encerrar` | fecha o pallet (`ENCERRADO`, `encerrado_por` = o responsável, que é o nome em **Montado por**) e imprime o cartão. 409 `DESEMBARQUE_VAZIO`, `DESEMBARQUE_ENCERRADO`. Resposta `{ desembarque, impressao }` |
| `POST /desembarques/{id}/cancelar` | `{ "justificativa" }` — desembarque aberto por engano: as caixas lançadas ficam marcadas como excluídas, os cadeados reabrem e o pallet vai a `ESVAZIADO`. 422 `JUSTIFICATIVA_OBRIGATORIA` |

A prévia devolve, por arquivo, o que a tela precisa mostrar antes de gravar:

```json
{
  "arquivos": [
    {
      "arquivo": "35260…3335.xml", "situacao": "OK",
      "nota": { "numero": "952463", "cliente": "ATACADAO S A",
                "qtdItens": 12, "qtdCaixas": 245, "qVolConfere": true },
      "produtosNovos": 4, "avisos": []
    },
    {
      "arquivo": "35260…1204.xml", "situacao": "REJEITADO",
      "erro": { "codigo": "NOTA_JA_IMPORTADA",
                "mensagem": "Já pertence à instrução RED-000003" }
    }
  ]
}
```

Concluir com divergência sem `confirmarDivergencias` retorna 409
`INSTRUCAO_COM_DIVERGENCIA`, trazendo a lista para a tela de confirmação.
Item ainda aberto retorna `ITENS_EM_ABERTO`.

### 4.2 Cadeado e movimentação

| Rota | Uso |
|---|---|
| `POST /itens/{id}/fechar-divergencia` | `{ "justificativa": "…" }` — obrigatória |
| `POST /itens/{id}/reabrir` | volta a `EM_SEPARACAO` |
| `POST /pallets/transferencia` | transferência por quantidade para um pallet novo (a repaletização bipando está em §3.2) |

```json
{
  "palletOrigem": "P-RED-000123",
  "itens": [ { "itemId": 4471, "quantidade": 8 },
             { "itemId": 4472, "quantidade": 15 } ],
  "impressoraId": 3
}
```

```json
{
  "palletDestino": "P-RED-000155",
  "qtdCaixas": 23,
  "origemEsvaziada": false,
  "cartoesImpressos": ["P-RED-000155", "P-RED-000123"]
}
```

Origem expedida, fundida ou esvaziada é recusada, e origem de retorno aberto
também (`PALLET_EM_RETORNO`), como na fusão e na exclusão.

### 4.3 Cadastros

CRUD convencional, com `DELETE` sempre inativando em vez de apagar:

`/produtos` · `/produtos/{id}/codigos-barras` · `/usuarios` · `/impressoras` ·
`/boxes`

Específicos:

| Rota | Uso |
|---|---|
| `GET /codigos-nao-identificados` | a fila de pendências |
| `POST /codigos-nao-identificados/{id}/vincular` | `{ "produtoId": 91 }` |
| `POST /produtos/importar` | planilha de origem → relatório de conferência |
| `POST /boxes/importar` | `BOXES DEMO.xls` convertido em CSV (só acrescenta) |
| `GET /boxes` | cadastro de endereços: `[{ codigo, codigoBarras, depositoCodigo, depositoNome, rua, tipo, niveis, ativo, ocupadas }]` — supervisor e admin |
| `GET /boxes/planilha` | `.xlsx` do modelo (só o cabeçalho); `?atuais=1` traz o cadastro de hoje para editar |
| `POST /boxes/planilha/previa` | multipart `arquivo` (`.xlsx` ou `.csv`) → o que mudaria, sem gravar — admin |
| `POST /boxes/planilha` | o mesmo arquivo, gravado — admin |

**Planilha de endereços (0.7.0).** Colunas: Depósito (código), Depósito
(nome), Rua, Box, Código de barras (em branco = o próprio box), Tipo
(`ESTOCAGEM` \| `PICKING`), Níveis (1 a 20), Ativo (`SIM` \| `NÃO`, em branco =
`SIM`). O cabeçalho do CSV antigo (`deposito_codigo`, `box_barras`…) também
vale. A resposta é
`{ novos, alterados, iguais, erros, linhas: [{ linha, box, acao, detalhe }] }`,
com `linha` = número da linha no Excel (1 é o cabeçalho) e `acao` `NOVO`,
`ALTERADO` ou `ERRO`; linha igual ao cadastro só entra em `iguais`. Gravar é
**tudo ou nada**: com qualquer erro, 409 `PLANILHA_COM_ERROS` e nada muda. A
planilha acrescenta e corrige, nunca apaga: box fora dela fica como está.
Recusa reduzir níveis se o nível que sai já teve pallet, desativar box ocupado,
código de barras de outro box, box ou código repetido e depósito novo sem
nome. 422 `PLANILHA_INVALIDA`, `PLANILHA_VAZIA`, `COLUNA_AUSENTE`.
| `POST /impressoras/{id}/testar` | detecta dpi por SGD e imprime etiqueta teste |

### 4.4 Expedição, reentrega e devolução

| Rota | Uso |
|---|---|
| `GET /enderecamentos?nota=952463` | roteiro ordenado por câmara e rua |
| `POST /expedicoes` | `{ "notaIds": [12, 13] }` — 409 `RETORNO_EM_ABERTO` se a NF tem retorno aberto (§4.4B) |
| `POST /expedicoes/{id}/estornar` | `{ "motivo": "…" }` — restaura endereços; a NF volta a `CONCLUIDA`, ou a `RETORNO_PARCIAL` se ainda tem pallet na rua. 409 `EXPEDICAO_COM_RETORNO` se algum pallet desta expedição já voltou (cancele o retorno antes) e `RETORNO_EM_ABERTO` se a NF tem retorno aberto |
| `GET /notas?status=EXPEDIDA&numero=8824` | busca NF por situação e número, entre instruções |
| `GET /notas/{id}/expedidos` | pallets que saíram, com o que já voltou marcado |
| `POST /reentregas` | `{ "notaId", "palletCodigos": [], "motivo" }` — reentrega imediata, sem conferência. Mantida por compatibilidade; a tela usa `/retornos` (§4.4B). Também recusa com `RETORNO_EM_ABERTO` |
| `GET /devolucoes?status=CONFERIDA` | lista, filtrável por situação (lista separada por vírgula). A retaguarda abre filtrando `AGUARDANDO_CONFERENCIA,EM_CONFERENCIA,CONFERIDA` — só o que ainda está no estoque (0.8.2) |
| `GET /devolucoes/{id}/itens` | itens com esperado/conferido em unidades |
| `POST /devolucoes/previa` | multipart do XML do cliente (+ origem, opcional) → de-para sugerido e avisos da origem |
| `POST /devolucoes` | multipart: XML + origem + `vinculos` (JSON) |
| `POST /devolucoes/conferencia` | uma leitura da conferência (BIPE ou DIGITADO). `palletCodigo` é obrigatório desde a 0.7.5: sem ele, 409 `PALLET_OBRIGATORIO` — a caixa conferida sem pallet não aparecia no estoque |
| `POST /devolucoes/itens/{id}/fechar` | cadeado do item, com justificativa se faltar. A retaguarda usa a mesma rota no **Encerrar** (0.7.5) |
| `POST /devolucoes/{id}/apagar` | `{ "justificativa", "comConferencia" }` — devolução lançada por engano (supervisor e admin; 0.7.2). Devolução que já teve baixa de caixas ou baixa estornada não se apaga: 409 `DEVOLUCAO_COM_BAIXA` (0.8.3). Sem conferência, apaga. Com conferência começada, 409 `DEVOLUCAO_COM_CONFERENCIA` (com `leituras` e `pallets`) a menos que `comConferencia = true`: aí as leituras vão junto e os pallets P-DEV voltam `CRIADO`, sem NF e sem endereço. Baixada: 409 `DEVOLUCAO_BAIXADA`. Resposta `{ numero, leiturasApagadas, palletsLiberados }` |
| `POST /devolucoes/itens/{id}/troca` | `{ "produtoId", "quantidade", "unidade": "CX" \| "UN", "itemTodo", "justificativa" }` — produto trocado (supervisor e admin; 0.7.4): cria um item novo com o produto que veio, ligado ao item `{id}` da NF, e o conferente o confere no coletor como qualquer item. `itemTodo = true` (nada do produto da NF veio) fecha o item da NF em `FECHADO_DIVERGENCIA` com a justificativa da troca — se ele ainda estava aberto. Devolução `CONFERIDA` volta a `EM_CONFERENCIA`. Recusa: 409 `DEVOLUCAO_BAIXADA`, `ITEM_E_TROCA` (o item já é troca de outro), `ITEM_JA_CONFERIDO` (`itemTodo` com o produto da NF já conferido — registre só a parte trocada), `UNIDADES_POR_CAIXA_NAO_CADASTRADA`; 422 `MESMO_PRODUTO`, `JUSTIFICATIVA_OBRIGATORIA`, `QUANTIDADE_INVALIDA`, `UNIDADE_INVALIDA`. Resposta: os itens da devolução |
| `POST /devolucoes/itens/{id}/desfazer-troca` | troca registrada por engano: o item da troca sai e o item da NF que ela fechou volta a esperar. Só sem leitura no item da troca: 409 `TROCA_COM_CONFERENCIA`; item que não é troca, 409 `ITEM_NAO_E_TROCA`. Resposta: os itens da devolução |
| `POST /devolucoes/itens/{id}/etiqueta` | `{ "copias": 3, "impressoraId": null }` — etiqueta 2ª via do produto do item (supervisor e admin; 0.7.5). Sai o GTIN-14 que o produto já tem ou, sem nenhum que o coletor leia, um código interno `029…` (GTIN-14 de uso interno, com o id do produto) gravado no produto com `origem MANUAL` e `simbologia INTERNO`. Sem `impressoraId`, a do usuário ou a primeira ativa. 422 `COPIAS_INVALIDAS` (fora de 1–200); 409 `DEVOLUCAO_BAIXADA`, `ITEM_SEM_PRODUTO`, `GTIN_DUPLICADO` e `UNIDADES_POR_CAIXA_NAO_CADASTRADA` (sem o fator, o bipe da etiqueta seria recusado). Resposta `{ etiqueta: { codigoBarras, produtoCodigo, produtoDescricao, codigoInterno, caixasFaltando }, copias, impressao: ENVIADA \| FALHOU \| SEM_IMPRESSORA }` |
| `POST /produtos/{id}/etiqueta` | `{ "copias", "impressoraId" }` — reimpressão da mesma etiqueta 2ª via (80 × 50 mm) de qualquer produto, com o mesmo código (0.7.6). Só o **usuário** `admin` — o login, não o perfil; os outros recebem 403. Fica no log de auditoria (`ETIQUETA_REIMPRESSA`). Resposta igual à da etiqueta do item, com `caixasFaltando` nulo |
| `GET /motivos-devolucao?todos=1` | cadastro de motivos de devolução `{ id, descricao, ativo }` (0.8.0); sem `todos`, só os ativos — a lista de escolha |
| `POST /motivos-devolucao` | `{ "descricao" }` — supervisor e admin. 409 `MOTIVO_DUPLICADO` (sem diferença de maiúscula); o desativado com o mesmo nome volta a valer. 422 `MOTIVO_OBRIGATORIO`, `MOTIVO_LONGO` (até 80) |
| `PUT /motivos-devolucao/{id}` | `{ "descricao", "ativo" }` — renomeia ou desativa; o que já foi gravado nas devoluções fica como está |
| `POST /devolucoes/{id}/motivo` | `{ "motivo" }` — motivo da devolução na NF (supervisor e admin; 0.7.7). Vale para todo item sem motivo próprio. `POST /devolucoes` também aceita o campo `motivo`, e cada vínculo, `motivo` do item |
| `POST /devolucoes/itens/{id}/motivo` | `{ "motivo" }` — motivo de um item só; vazio ou igual ao da NF volta a seguir a NF. O item traz `motivo` (o próprio, ou nulo) e a devolução, `motivo` (o da NF) |
| `GET /devolucoes/{id}/planilha` | a devolução em .xlsx, um item por linha: NF, cliente, origem, item, produto, código e descrição do cliente, esperado, conferido, falta, situação, motivo (o do item ou o da NF) e justificativa (0.7.7) |
| `GET /devolucoes/{id}/leituras` | as leituras que valem (sem as excluídas), na ordem de chegada: `{ id, itemId, nItem, produtoCodigo, produtoDescricao, modo, qtdUn, palletCodigo, usuario, lidoEm }`. `palletCodigo` nulo é leitura conferida sem pallet (0.7.5) |
| `POST /devolucoes/{id}/leituras/pallet` | `{ "leituraIds": [..], "palletCodigo": "P-DEV-000001" }` — supervisor e admin (0.7.5): põe as leituras num P-DEV, que se amarra à NF como na conferência. Pallet desta NF que fica sem leitura volta `CRIADO`, sem NF e sem endereço. 409 `PALLET_TIPO_INVALIDO`, `PALLET_DE_OUTRA_NOTA`, `PALLET_NAO_ENCONTRADO`, `PALLET_ENCERRADO`, `CAIXAS_MUDARAM` (leitura de outra NF ou já excluída), `DEVOLUCAO_BAIXADA`; 422 `CAIXAS_OBRIGATORIAS`. Resposta `{ caixas, palletsLiberados, itens }` |
| `POST /devolucoes/{id}/leituras/excluir` | `{ "leituraIds": [..], "justificativa" }` — supervisor e admin (0.7.5): as leituras ficam excluídas (`cancelada = 1`, com quem, quando e por quê), os itens recalculam — item fechado reabre — e a NF `CONFERIDA` volta a `EM_CONFERENCIA`. Pallet que fica vazio volta livre. 422 `JUSTIFICATIVA_OBRIGATORIA`, `CAIXAS_OBRIGATORIAS`; 409 `CAIXAS_MUDARAM`, `DEVOLUCAO_BAIXADA`. Resposta `{ caixas, palletsLiberados, itens }` |
| `POST /devolucoes/{id}/baixar` | `{ "tipo": "DESCARTE" \| "REVENDA", … }` — exige a NF `CONFERIDA`; a conferência de estoque (sobra), que não tem NF dizendo quanto era, baixa direto de `EM_CONFERENCIA` e os itens abertos fecham junto (0.8.2) |
| `POST /devolucoes/sobra` | abre uma **conferência de estoque** (supervisor e admin; 0.8.2): uma devolução sem NF, numerada `SOBRA-000001`, com `sobra = true`, emitente e cliente `SOBRA DE ESTOQUE`. Resposta: o resumo dela |
| `POST /devolucoes/{id}/sobra/leitura` | `{ "idempotencyKey", "codigoLido", "itemId", "caixas", "palletCodigo" }` — uma caixa de sobra. Com `codigoLido` (o bipe) o produto vem do GTIN e `caixas` é 1; com `itemId` (o digitar) somam-se `caixas` caixas do produto daquele item. Um item por produto: o bipe seguinte do mesmo produto soma no mesmo item, e o esperado acompanha o conferido — sem NF, o esperado é o que veio. 409 `NAO_E_SOBRA` (a NF é devolução de verdade), `DEVOLUCAO_BAIXADA`, `PALLET_OBRIGATORIO`, `CODIGO_NAO_CADASTRADO`, `UNIDADES_POR_CAIXA_NAO_CADASTRADA` e os erros de pallet da conferência; 422 `QUANTIDADE_INVALIDA` (fora de 1–999), `IDEMPOTENCY_KEY_OBRIGATORIA`. Resposta: os itens da conferência |
| `POST /devolucoes/{id}/conferir` | encerra a conferência de estoque sem baixar (supervisor e admin; 0.8.3): os itens fecham, a nota vai a `CONFERIDA`, os P-DEV dela saem de `EM_USO` para `ENCERRADO` — a sobra continua no estoque, mas sai do coletor. 409 `NAO_E_SOBRA` (devolução do cliente fecha item a item, pelo cadeado), `CONFERENCIA_VAZIA`, `DEVOLUCAO_BAIXADA` |
| `POST /devolucoes/{id}/reabrir` | volta a conferência para `EM_CONFERENCIA` e **todos** os itens para aberto, com a contagem recalculada — a NF reaparece na lista do coletor e aceita a caixa que ficou de fora (0.8.4). Vale para a devolução do cliente e para a conferência de estoque. O item que estava fechado com divergência perde a justificativa: a falta vai ser recontada, como na exclusão de caixa. 409 `DEVOLUCAO_BAIXADA` (estorne a baixa antes), `CONFERENCIA_VAZIA` (NF sem itens) |
| `POST /devolucoes/{id}/estornar-baixa` | `{ "justificativa" }` — desfaz a baixa (**só o admin**; 0.8.3). Os pallets que ainda têm caixa saem de `EXPEDIDO`: retomam o endereço que esta baixa liberou, se a posição continuar livre, senão voltam `ENCERRADO`, para a fila do coletor. A devolução volta a `CONFERIDA`; a conferência de estoque volta a `EM_CONFERENCIA`, com os itens reabertos. Caixa que saiu antes pela baixa por caixa (`cancelada = 3`) não volta, e a resposta diz quantas são. A baixa sai de `baixa_devolucao` e fica inteira em `baixa_devolucao_estornada`. 409 `DEVOLUCAO_NAO_BAIXADA`, `BAIXA_NAO_ENCONTRADA`, `REVENDA_EM_SEPARACAO` (a NF de revenda já entrou e está sendo separada); 422 `MOTIVO_OBRIGATORIO`. Resposta `{ numero, status, caixas, palletsEnderecados, palletsSemEndereco, caixasBaixadasPorCaixa }` |
| `POST /devolucoes/itens/{id}/baixa-sobra` | `{ "caixas", "tipo": "DESCARTE" \| "REVENDA", "observacao", "nfRevenda" }` — baixa de parte da sobra (supervisor e admin; 0.8.2): o produto pode sair em partes. As caixas saem das leituras mais antigas para as mais novas (a leitura maior que o pedido se divide), ficam marcadas `cancelada = 3` ligadas à baixa — nada é apagado — e o pallet que fica vazio vai a `EXPEDIDO` liberando o endereço. Saindo a última caixa da conferência, ela fecha como baixada. 409 `NAO_E_SOBRA`, `DEVOLUCAO_BAIXADA`, `QUANTIDADE_MAIOR_QUE_O_ESTOQUE` (com `caixasEmEstoque`), `UNIDADES_POR_CAIXA_NAO_CADASTRADA`; 422 `QUANTIDADE_INVALIDA`, `TIPO_INVALIDO`. Resposta `{ caixas, qtdUn, restanteUn, itens }` |

`GET /devolucoes` traz `sobra` em cada linha: `true` é conferência de estoque,
não NF do cliente — a retaguarda e o coletor mudam o que mostram por ele.

`GET /devolucoes/{id}/itens` traz também `nItem` e, no item de produto trocado
(0.7.4), `trocaDeItem` (o nº do item da NF no lugar do qual veio),
`trocaJustificativa`, `trocadoPor` e `trocadoEm`. O `xprodCliente` dele diz
`NO LUGAR DO ITEM n: …` — é o que o coletor mostra, sem precisar de APK novo.

`POST /devolucoes` é multipart porque carrega o XML junto do de-para: campo
`arquivo` com a NF de devolução, a origem e `vinculos` com
`[{ "nItem": 1, "produtoId": 91, "quantidade": 3, "unidade": "CX" }, …]`
serializado. `quantidade` e `unidade` (`CX` \| `UN`) são opcionais (0.7.3): sem
eles vale a quantidade do XML, em caixas. Em `CX` a esperada em unidades é
quantidade × unidade por caixa do produto (sem ela, 409
`UNIDADES_POR_CAIXA_NAO_CADASTRADA`); em `UN`, a própria quantidade, sem exigir o
fator. 422 `QUANTIDADE_INVALIDA` (menor que 1) e `UNIDADE_INVALIDA`. A prévia
traz por item `unidadeXml` (o `uCom` do XML) e `unidadeSugerida` (`CX` \| `UN`). Falta vínculo em qualquer
item, recusa com `ITENS_SEM_VINCULO`.

**NF de origem (0.7.0)** — um de três, nos dois POST: campo `notaOrigemId`
(NF de venda do sistema), arquivo `arquivoOrigem` (XML da NF de venda — a que
não passou pelo sistema; se o sistema tiver a chave, fica ligada a ela também)
ou campo `origemNumero` (só o número, digitado). Sem nenhum, `POST /devolucoes`
recusa com `NOTA_ORIGEM_OBRIGATORIA` (422); XML de origem ilegível,
`XML_ORIGEM_INVALIDO` (422). A mesma NF de venda pode ter mais de uma
devolução.

A prévia devolve, além do de-para, `origemTipo` (`SISTEMA` \| `XML` \|
`DIGITADA`), `notaOrigemId`, `notaOrigemNumero`, `origemChave`,
`origemCliente` e `referencia` — `{ chave, numero, notaId, cliente }` da NF de
venda que o próprio XML da devolução aponta (`NFref/refNFe`), com `notaId`
quando o sistema a tem. `avisos` inclui a origem de outro CNPJ ("confira se é a
NF certa") e a origem que já tem devolução. `DevolucaoResumo` ganha
`origemTipo` e `origemChave`, e `notaOrigemNumero` vale para os três modos.

A conferência distingue os dois jeitos de contar:

```json
{ "idempotencyKey": "…", "itemDevolucaoId": 7, "modo": "BIPE",
  "codigoLido": "27890000217084", "palletCodigo": "P-DEV-000001" }

{ "idempotencyKey": "…", "itemDevolucaoId": 7, "modo": "DIGITADO",
  "qtdUn": 7, "palletCodigo": "P-DEV-000001" }
```

Resposta: `{ "itemId", "qtdConferidaUn", "qtdEsperadaUn", "completo" }`.

O retorno de `/reentregas` diz onde a NF parou:

```json
{ "notaNumero": "952918", "notaStatus": "RETORNO_PARCIAL",
  "palletsRetornados": ["P-RED-000123"], "qtdCaixas": 49, "totalPendentes": 1 }
```

A prévia de devolução já devolve o vínculo sugerido pelo GTIN, que a tela só
confirma:

```json
{
  "itens": [
    { "nItem": 1, "cProdCliente": "957712", "ceanCliente": "27890000217084",
      "qtdUn": 192,
      "sugestao": { "produtoId": 91, "codigo": "2685",
                    "descricao": "CARNE BOVINA CONSERVA MARCA DEMO 24X320G RA",
                    "origem": "GTIN" } },
    { "nItem": 3, "cProdCliente": "990455", "ceanCliente": null,
      "qtdUn": 48, "sugestao": null }
  ]
}
```

Estorno em posição já ocupada não falha — devolve o que não pôde ser
restaurado, para a tela avisar:

```json
{ "restaurados": 5,
  "semEndereco": [ { "pallet": "P-RED-000124",
                     "posicaoOriginal": "C8-D28 nível 5",
                     "ocupadaPor": "P-RED-000198" } ] }
```

### 4.4A Estoque

| Rota | Uso |
|---|---|
| `GET /estoque?situacao=EM_ESTOQUE` | pallets que estão no CD |
| `GET /estoque?situacao=EXPEDIDO` | os que saíram, com quem, quando e por quê |
| `GET /estoque/{codigo}/itens` | conteúdo de um pallet |

`situacao` aceita `EM_ESTOQUE` (padrão), `EXPEDIDO`, `TODOS` e
`FORA_DE_CIRCULACAO`. Filtros opcionais: `nota`, `cliente`, `box`, `tipo`
(`SEPARACAO` ou `DEVOLUCAO`).

```json
{ "linhas": [
    { "palletCodigo": "P-RED-000123", "tipo": "SEPARACAO", "status": "EXPEDIDO",
      "situacao": "EXPEDIDO", "notaNumero": "952463", "cliente": "ATACADAO S A",
      "bonificacao": false, "qtdCaixas": 45, "endereco": null,
      "montadoEm": "2026-09-06T13:35:00Z", "montadoPor": "JOÃO SILVA",
      "saidaEm": "2026-09-06T16:42:00Z", "saidaPor": "MARIA",
      "saidaTipo": "EXPEDICAO" } ],
  "totais": { "pallets": 12, "caixas": 543, "semEndereco": 2 } }
```

`saidaTipo` é `EXPEDICAO` para pallet de venda, `BAIXA_DEVOLUCAO` para o de
devolução e `REFATURAMENTO` ou `DESCARTE` para o que voltou de NF expedida e
saiu pela tratativa do retorno — nesse caso `saidaEm` é a data da tratativa e
`saidaPor` vem nulo. Pallet que voltou (retorno ou reentrega) perde a marca de
saída: a expedição anterior deixou de valer.

`retorno` traz, pronto para exibir, a situação do último retorno não cancelado
do pallet — `"Retorno NF 952463 · em conferência"`, `"· aguardando
tratativa"` ou `"· para reentrega"` enquanto está no CD; `"· refaturado
(952501)"` ou `"· descartado"` depois de esvaziado; nulo sem retorno. Pallet no
CD fechado com falta na conferência do retorno tem `qtdCaixas` igual às caixas
**conferidas** na volta, não às que saíram.

`EM_ESTOQUE` cobre `EM_USO`, `ENCERRADO` e `ENDERECADO`.
Pallet `FUNDIDO`/`ESVAZIADO` e pallet sem caixa nenhuma ficam fora de
`EM_ESTOQUE` e `EXPEDIDO` — somar o fundido junto do destino contaria a mesma
caixa duas vezes.

### 4.4B Retorno de NF expedida

NF expedida que voltou (`TELAS-RETAGUARDA.md` §9.2, `MODELO-DE-DADOS.md` §9.3).
Registro, tratativa e desfazer são da retaguarda; a conferência caixa a caixa,
do coletor (`TELAS-COLETOR.md` §5B).

| Rota | Perfil | Uso |
|---|---|---|
| `GET /retornos?status=…` | qualquer | lista de `RetornoResumo` |
| `GET /retornos/{id}` | qualquer | `{ retorno, pallets }` |
| `POST /retornos` | SUPERVISOR/ADMIN | registra — **201** |
| `POST /retornos/leituras` | qualquer | uma caixa bipada |
| `POST /retornos/pallets/{id}/fechar` | qualquer | fecha a conferência do pallet |
| `POST /retornos/pallets/{id}/desfazer-ultima` | qualquer | cancela a última caixa do pallet |
| `POST /retornos/pallets/{id}/reabrir` | SUPERVISOR/ADMIN | volta o pallet a `ABERTO` |
| `POST /retornos/{id}/completo` | SUPERVISOR/ADMIN | "retorno completo", sem bipar |
| `POST /retornos/{id}/tratativa` | SUPERVISOR/ADMIN | `REENTREGA`, `REFATURAMENTO` ou `DESCARTE` |
| `POST /retornos/{id}/desfazer-tratativa` | SUPERVISOR/ADMIN | volta a aguardando tratativa |
| `POST /retornos/{id}/cancelar` | SUPERVISOR/ADMIN | retorno registrado por engano |

Em `/retornos/pallets/{id}/…` o `{id}` é o do **pallet dentro do retorno** (o
`id` de `RetornoPallet`, `retorno_pallet.id`), não o do pallet. Retorno, pallet
ou NF inexistente: 404 `NAO_ENCONTRADO`.

Toda rota que muda algo devolve o objeto atualizado — `RetornoResumo` nas do
retorno, `RetornoPallet` nas do pallet —, para a tela redesenhar sem nova
consulta.

**`GET /retornos`** — `status` aceita lista separada por vírgula entre
`EM_CONFERENCIA`, `AGUARDANDO_TRATATIVA`, `TRATADO` e `CANCELADO`; sem ele, os
abertos (os dois primeiros). Valor desconhecido é ignorado. Mais recentes
primeiro, até 200.

`RetornoResumo`:

```json
{ "id": 7, "notaId": 12, "notaNumero": "952463", "cliente": "ATACADAO S A",
  "motivo": "cliente recusou por atraso na entrega",
  "status": "AGUARDANDO_TRATATIVA",
  "registradoEm": "2026-09-12T14:05:00Z", "registradoPor": "MARIA",
  "qtdPallets": 2, "palletsFechados": 2,
  "caixasEsperadas": 95, "caixasConferidas": 91, "caixasFaltando": 4,
  "conferenciaManual": false,
  "conferidoEm": "2026-09-12T15:10:00Z", "conferidoPor": "JOÃO SILVA",
  "tratativa": null, "nfsRefaturamento": null, "tratativaObs": null,
  "tratadoEm": null, "tratadoPor": null }
```

`caixasFaltando` soma só os pallets já fechados com falta — em conferência, a
diferença entre esperadas e conferidas é progresso, não falta. `conferidoEm/Por`
é quem fechou o último pallet (ou marcou o retorno completo).

**`GET /retornos/{id}`** → `{ "retorno": RetornoResumo, "pallets": [RetornoPallet] }`,
pallets em ordem de código:

```json
{ "id": 31, "palletCodigo": "P-RED-000124", "status": "CONFERIDO_FALTA",
  "qtdEsperada": 50, "qtdConferida": 46, "manual": false,
  "justificativa": "Extravio no transporte",
  "produtos": [ { "produtoCodigo": "2685",
                  "produtoDescricao": "CARNE BOVINA CONSERVA MARCA DEMO 24X320G RA",
                  "esperada": 20, "conferida": 16 } ] }
```

`status` do pallet: `ABERTO`, `CONFERIDO` ou `CONFERIDO_FALTA`. `esperada` vem
da foto tirada no registro. Pallet fechado pelo retorno completo (`manual`)
conta como conferido inteiro, produto a produto.

**`POST /retornos`**

```json
{ "notaId": 12, "palletCodigos": ["P-RED-000123", "P-RED-000124"],
  "motivo": "cliente recusou por atraso na entrega" }
```

Os códigos são normalizados (espaços, maiúsculas) e repetidos, ignorados. Só
entram pallets da NF que estão na rua: a última saída de cada um, sem estorno e
sem retorno, com o pallet `EXPEDIDO`. Resposta **201** com o `RetornoResumo`
em `EM_CONFERENCIA`.

| Erro | HTTP | Quando |
|---|---|---|
| `MOTIVO_OBRIGATORIO` · `NENHUM_PALLET` | 422 | motivo vazio · lista vazia |
| `NOTA_NAO_EXPEDIDA` | 409 | NF não está `EXPEDIDA` nem `RETORNO_PARCIAL` |
| `RETORNO_EM_ABERTO` | 409 | a NF já tem retorno em conferência ou aguardando tratativa |
| `PALLET_NAO_RETORNAVEL` | 409 | fora da expedição desta NF ou já retornado; `detalhe.pallets` diz quais |

**`POST /retornos/leituras`** · `idempotencyKey` obrigatória, no corpo

```json
{ "idempotencyKey": "c41d…", "retornoId": 7, "palletCodigo": "P-RED-000124",
  "codigoLido": "27890000217084", "lidoEm": "2026-09-12T11:40:02-03:00" }
```

```json
{ "retornoPalletId": 31, "palletCodigo": "P-RED-000124",
  "qtdConferida": 46, "qtdEsperada": 50,
  "produtoCodigo": "2685", "produtoDescricao": "CARNE BOVINA CONSERVA MARCA DEMO 24X320G RA",
  "produtoConferida": 16, "produtoEsperada": 20, "palletCompleto": false }
```

A chave é conferida **antes de tudo**: repetida, devolve a situação do pallet e
do produto daquela leitura sem gravar de novo — mesmo que o pallet já tenha
fechado. É o que deixa o coletor reenviar depois de ficar sem resposta. O código
passa pela mesma normalização GS1 da separação.

| Erro | HTTP | Quando |
|---|---|---|
| `IDEMPOTENCY_KEY_OBRIGATORIA` | 422 | chave vazia |
| `RETORNO_NAO_EM_CONFERENCIA` | 409 | o retorno já saiu de `EM_CONFERENCIA` |
| `PALLET_FORA_DO_RETORNO` | 409 | o pallet não é deste retorno |
| `PALLET_JA_CONFERIDO` | 409 | pallet fechado; reabrir é na retaguarda |
| `CODIGO_NAO_CADASTRADO` | 409 | GTIN inválido ou sem produto; `detalhe.codigoLido` |
| `PRODUTO_FORA_DO_PALLET` | 409 | o produto não saiu neste pallet; `detalhe.produto` |
| `QUANTIDADE_COMPLETA` | 409 | todas as caixas do produto já conferidas; `detalhe.conferida`, `detalhe.esperada` |

Diferente da separação, código não cadastrado aqui não entra na fila de códigos
não identificados.

**`POST /retornos/pallets/{id}/fechar`** — `{ "justificativa": "…" }`, corpo
opcional. Completo fecha `CONFERIDO`; faltando caixa, exige justificativa e
fecha `CONFERIDO_FALTA` — inclusive com zero, o pallet que nem voltou. Fechado
o último, o retorno vai a `AGUARDANDO_TRATATIVA`. Erros:
`RETORNO_NAO_EM_CONFERENCIA`, `PALLET_JA_CONFERIDO` e `CONFERENCIA_INCOMPLETA`
(falta sem justificativa; `detalhe.conferida`, `detalhe.esperada`).

**`POST /retornos/pallets/{id}/desfazer-ultima`** — cancela a última leitura
viva do pallet; ela fica no histórico. Erros: `RETORNO_NAO_EM_CONFERENCIA`,
`PALLET_JA_CONFERIDO`, `NADA_A_DESFAZER`.

**`POST /retornos/pallets/{id}/reabrir`** — pallet volta a `ABERTO`, sem
justificativa nem marca manual; o retorno volta a `EM_CONFERENCIA`, limpando
quem e quando conferiu. Pallet já aberto volta como está. Erros:
`RETORNO_JA_TRATADO` (desfaça a tratativa antes) e `RETORNO_CANCELADO`.

**`POST /retornos/{id}/completo`** — `{ "observacao": "…" }`, opcional; sem
ela, grava *"Retorno completo marcado na retaguarda"*. Fecha os pallets
`ABERTO` como `CONFERIDO` com `manual = true` — a observação vira a
justificativa deles — e leva o retorno a `AGUARDANDO_TRATATIVA` com
`conferenciaManual = true`. Pallets já fechados não mudam. Erro:
`RETORNO_NAO_EM_CONFERENCIA`.

**`POST /retornos/{id}/tratativa`**

```json
{ "tipo": "REFATURAMENTO", "nfs": ["952501", "952502"], "observacao": null }
```

`nfs` só vale no refaturamento; cada elemento ainda é quebrado em vírgula, ponto
e vírgula, espaço, tabulação ou quebra de linha — o que vier colado do
faturamento — e grava em `nfsRefaturamento` separado por `, `. `observacao` é
opcional, e obrigatória no descarte como justificativa. `REFATURAMENTO` e
`DESCARTE` levam os pallets a `ESVAZIADO` e liberam o endereço; `REENTREGA` os
deixa no CD com o que voltou: nos pallets `CONFERIDO_FALTA` (não manuais), as
caixas que faltaram saem do pallet (`leitura.cancelada = 2`, ligadas ao
retorno), e o pallet que ficou sem nenhuma vai a `ESVAZIADO`. A falta é coberta
por NF de devolução do cliente. Efeito em `MODELO-DE-DADOS.md` §9.3.

| Erro | HTTP | Quando |
|---|---|---|
| `TIPO_INVALIDO` | 422 | fora de `REENTREGA`, `REFATURAMENTO`, `DESCARTE` |
| `NFS_OBRIGATORIAS` · `JUSTIFICATIVA_OBRIGATORIA` | 422 | refaturamento sem NF · descarte sem justificativa |
| `RETORNO_NAO_CONFERIDO` | 409 | ainda em conferência |
| `RETORNO_JA_TRATADO` · `RETORNO_CANCELADO` | 409 | |
| `NADA_A_REENTREGAR` | 409 | `REENTREGA` sem nenhuma caixa conferida: nada voltou. Descarte ou cancele o retorno |
| `PALLET_ALTERADO` | 409 | algum pallet mudou desde o registro: status fora de `ENCERRADO`/`ENDERECADO` ou conteúdo diferente da foto |

**`POST /retornos/{id}/desfazer-tratativa`** — `{ "justificativa": "…" }`,
obrigatória. Volta a `AGUARDANDO_TRATATIVA`, limpando a tratativa; pallets
refaturados ou descartados voltam a `ENCERRADO`, sem endereço. Desfazendo uma
reentrega com falta, as caixas que ela tirou voltam ao pallet e o pallet que
ficou vazio volta a `ENCERRADO`. Resposta: `RetornoResumo`.

| Erro | HTTP | Quando |
|---|---|---|
| `JUSTIFICATIVA_OBRIGATORIA` | 422 | justificativa vazia |
| `RETORNO_NAO_TRATADO` | 409 | o retorno não está `TRATADO` |
| `RETORNO_EM_ABERTO` | 409 | a NF já tem outro retorno em conferência ou aguardando tratativa |
| `NOTA_JA_REEXPEDIDA` | 409 | reentrega com pallet que saiu de novo |
| `PALLET_ALTERADO` | 409 | reentrega com pallet que mudou desde o registro |
| `REFATURAMENTO_EM_SEPARACAO` | 409 | refaturamento em que alguma das NFs novas já tem caixa separada |
| `PALLET_REUTILIZADO` | 409 | refaturamento ou descarte com pallet que deixou de estar `ESVAZIADO` |

**`POST /retornos/{id}/cancelar`** — `{ "justificativa": "…" }`, obrigatória.
Só antes da tratativa. Pallets voltam a `EXPEDIDO`, com o endereço eventual
liberado (`CORRECAO`) e `retornado_em` limpo. O conferido fica no histórico.
Resposta: `RetornoResumo` em `CANCELADO`.

| Erro | HTTP | Quando |
|---|---|---|
| `JUSTIFICATIVA_OBRIGATORIA` | 422 | justificativa vazia |
| `RETORNO_JA_TRATADO` | 409 | desfaça a tratativa antes |
| `RETORNO_CANCELADO` | 409 | já cancelado |
| `PALLET_ALTERADO` | 409 | algum pallet mudou desde o registro: status fora de `ENCERRADO`/`ENDERECADO` ou conteúdo diferente da foto |

**Situação da NF.** Tratativa, desfazer e cancelar recalculam a situação da NF
pelos pallets, sem voltar a um estado guardado: `EXPEDIDA` se nenhum está no
CD; `CONCLUIDA` se estão no CD e nada ficou na rua; `RETORNO_PARCIAL` se parte
está no CD e parte na rua.

**Travas fora de `/retornos`.** Com retorno aberto na NF, `POST /expedicoes` e
`POST /reentregas` recusam com `RETORNO_EM_ABERTO`, e a NF sai de
`GET /expedicoes/pendentes`. `POST /expedicoes/{id}/estornar` recusa com
`EXPEDICAO_COM_RETORNO` se algum pallet daquela expedição já voltou, e com
`RETORNO_EM_ABERTO` se a NF tem retorno aberto. Pallet de retorno aberto
recusa fusão, transferência e exclusão de separação com `PALLET_EM_RETORNO`.

Perfil sem permissão numa rota de SUPERVISOR/ADMIN: 403 `PERFIL_SEM_PERMISSAO`.

A tela de registro monta a NF com rotas que já existiam: acha a NF pelo número
em `GET /notas?status=EXPEDIDA,RETORNO_PARCIAL` e lista os pallets em
`GET /notas/{id}/expedidos` — cada um com `expedicaoPalletId`, `palletCodigo`,
`qtdCaixas`, `expedidoEm` e `retornadoEm` (nulo = está na rua). O cartão
reimpresso é `POST /pallets/{codigo}/cartao`.

### 4.5 Relatórios

`GET /relatorios/separacao` · `GET /relatorios/exclusoes` ·
`GET /relatorios/produtividade`

Filtros por `de`, `ate`, `usuarioId`, `instrucaoId`, `notaId`, `produtoId`.
`?formato=xlsx` devolve o arquivo. As três colunas do analítico saem prontas:

```json
{ "linhas": [ { "usuario": "JOÃO", "instrucao": "RED-000001", "nota": "952463",
                "produto": "2685", "separado": 6, "excluido": 2, "liquido": 4 } ],
  "totais": { "separado": 38, "excluido": 2, "liquido": 36 } }
```

---

## 5. Códigos de erro

Estáveis — o app decide comportamento por eles, nunca pelo texto da mensagem.

| Código | HTTP |
|---|---|
| `CREDENCIAIS_INVALIDAS` · `SESSAO_EXPIRADA` | 401 |
| `PERFIL_SEM_PERMISSAO` | 403 |
| `PALLET_NAO_SELECIONADO` · `PALLET_DE_OUTRA_NOTA` · `PALLET_ENCERRADO` · `PALLET_EM_USO_POR_OUTRO` · `PALLET_NAO_E_SEU` · `PALLET_JA_FUNDIDO` · `PALLET_JA_EXPEDIDO` · `PALLET_NAO_ENCERRADO` | 409 |
| `ITEM_FECHADO` · `ITEM_INCOMPLETO` · `QUANTIDADE_COMPLETA` · `QUANTIDADE_INSUFICIENTE` | 409 |
| `CODIGO_NAO_CADASTRADO` · `PRODUTO_DIVERGENTE` · `UNIDADES_POR_CAIXA_NAO_CADASTRADA` | 409 |
| `NAO_E_SOBRA` · `QUANTIDADE_MAIOR_QUE_O_ESTOQUE` | 409 |
| `DESEMBARQUE_EM_ABERTO` · `DESEMBARQUE_ENCERRADO` · `DESEMBARQUE_VAZIO` · `QUANTIDADE_MAIOR_QUE_A_NF` · `USUARIO_INATIVO` | 409 |
| `DEVOLUCAO_NAO_BAIXADA` · `BAIXA_NAO_ENCONTRADA` · `REVENDA_EM_SEPARACAO` · `DEVOLUCAO_COM_BAIXA` · `CONFERENCIA_VAZIA` | 409 |
| `POSICAO_OCUPADA` · `BOX_NAO_ENCONTRADO` | 409 |
| `SEPARACAO_INICIADA` · `NOTA_JA_IMPORTADA` · `INSTRUCAO_COM_DIVERGENCIA` · `ITENS_EM_ABERTO` · `INSTRUCAO_CONCLUIDA` | 409 |
| `PALLETS_DE_NOTAS_DIFERENTES` · `TIPOS_INCOMPATIVEIS` | 409 |
| `NOTA_NAO_EXPEDIDA` · `RETORNO_EM_ABERTO` · `PALLET_NAO_RETORNAVEL` · `PALLET_FORA_DO_RETORNO` · `PALLET_JA_CONFERIDO` · `PRODUTO_FORA_DO_PALLET` · `CONFERENCIA_INCOMPLETA` · `NADA_A_DESFAZER` | 409 |
| `RETORNO_NAO_EM_CONFERENCIA` · `RETORNO_NAO_CONFERIDO` · `RETORNO_JA_TRATADO` · `RETORNO_NAO_TRATADO` · `RETORNO_CANCELADO` · `NOTA_JA_REEXPEDIDA` · `PALLET_REUTILIZADO` | 409 |
| `MOTIVO_OBRIGATORIO` · `NENHUM_PALLET` · `IDEMPOTENCY_KEY_OBRIGATORIA` · `TIPO_INVALIDO` · `NFS_OBRIGATORIAS` · `JUSTIFICATIVA_OBRIGATORIA` | 422 |
| `NAO_ENCONTRADO` | 404 |
| `XML_INVALIDO` · `GTIN_INVALIDO` · `GTIN_DUPLICADO` | 422 |
| `IMPRESSORA_INACESSIVEL` | 502 |

---

## 6. Notas de implementação

**Autoridade do servidor.** Contagem, estado de cadeado e vínculo de pallet são
decididos no servidor. O app antecipa o incremento na tela por fluidez, mas
desfaz se a resposta recusar.

**Permissões verificadas no servidor**, não apenas escondendo botão. Fechar item
com divergência, expedir, estornar, baixar devolução e, no retorno, registrar,
marcar completo, reabrir pallet, dar ou desfazer tratativa e cancelar exigem
`SUPERVISOR` (ou `ADMIN`); cadastros exigem `ADMIN`. A conferência de retorno é
de qualquer usuário, como a separação.

**Transações.** Fusão, transferência, expedição, estorno, baixa e cada passo do
retorno (registro, tratativa, desfazer, cancelar) mexem em várias tabelas e
rodam em transação única — meia expedição aplicada seria pior que expedição
nenhuma.

**Impressão é assíncrona ao resultado.** A resposta confirma que o job foi
aceito pela impressora, nunca que o papel saiu. Toda rota que imprime tem
contraparte de reimpressão.
