# Telas da Retaguarda — Windows (navegador)

Aplicação servida pelo backend na rede local. Sem instalação, sem privilégio de
administrador. Complementa `MODELO-DE-DADOS.md` e `TELAS-COLETOR.md`.

Perfis: **SUPERVISOR** (operação) e **ADMIN** (operação + cadastros).

---

## 0. Visual (0.5.0)

Liquid glass na moldura, papel na planilha.

- **Vidro** (branco translúcido com desfoque do fundo) no cabeçalho, nas abas,
  nos diálogos, nos avisos e nos cartões de informação — o que fica por cima do
  conteúdo.
- **Papel** nas tabelas: quem confere NF lê linha por linha, e fundo
  translúcido atrás de número cansa a vista.
- **Verde só na ação** (botão principal, o que está pronto); âmbar marca o que
  pede atenção; a situação sai da pílula, não da cor da linha.
- **Fontes embutidas** em `web/fontes/` (Onest e JetBrains Mono, esta nos
  códigos `RED-`, `P-RED-` e nas NF): o servidor e o PC podem não ter internet.
- Movimento sutil (troca de aba, abertura de diálogo), desligado com
  `prefers-reduced-motion`.

## 1. Instruções

Lista com número, data, criador, quantidade de NFs, progresso em caixas e
status. Filtros por status, período e cliente.

**Retenção:** instruções concluídas e expedidas permanecem nas telas
operacionais por **2 anos**. Depois disso saem da lista padrão e continuam
acessíveis pela consulta histórica — nada é apagado.

### 1.1 Criar instrução e importar XMLs

Gera o próximo `RED-000001` e abre o importador. Aceita **vários XMLs de uma
vez** (arrastar ou selecionar a pasta).

**Prévia obrigatória antes de gravar.** Nada entra sem o operador ver:

```
┌────────────────────────────────────────────────────────────┐
│ 3 arquivos lidos                                           │
├────────────────────────────────────────────────────────────┤
│ ✅ NF 952463  ATACADAO S A · BRASILIA/DF                   │
│    12 itens · 245 caixas · qVol confere                    │
│    ⓘ 4 produtos novos serão cadastrados                    │
│                                                            │
│ ✅ NF 952918  SUPERMERCADO DEMO LTDA · CIDADE/UF       │
│    8 itens · 96 caixas · qVol confere                      │
│                                                            │
│ ❌ NF 951204  já pertence à instrução RED-000003           │
├────────────────────────────────────────────────────────────┤
│              [ Cancelar ]   [ Importar as 2 válidas ]      │
└────────────────────────────────────────────────────────────┘
```

Validações do importador:

| Situação | Resultado |
|---|---|
| Arquivo não é NF-e 4.00 | rejeita o arquivo |
| Chave já existe no sistema | rejeita, apontando a instrução dona |
| `SUM(qCom)` ≠ `qVol` | **avisa e deixa decidir** — não bloqueia |
| `uCom` ≠ `CX` em algum item | avisa, pede conferência da unidade |
| `cEAN` ausente ou "SEM GTIN" | importa, produto entra sem código de barras |
| `cEAN` com dígito verificador inválido | importa o produto, descarta o código |
| `cEAN` já vinculado a outro produto | importa o produto, reporta o conflito |

Uma NF pertence a **uma única instrução** (chave única global). Uma instrução
pode conter NFs de clientes diferentes — é o caso das duas NFs de referência.

A tela da instrução mostra, por NF, a **quantidade de itens** e a **quantidade
de caixas** previstas, além dos dados do cliente.

### 1.2 Adicionar NF a instrução existente

Instrução `ABERTA` ou `EM_SEPARACAO` aceita novas NFs a qualquer momento, pelo
mesmo importador da §1.1 e com a mesma prévia. Instrução `CONCLUIDA` não —
para incluir NF nela seria preciso reabrir a conclusão, e nesse ponto a
instrução já foi expedida ou está a caminho.

Acrescentar NF não afeta o que já foi separado: os itens novos entram
`ABERTO` e a instrução simplesmente cresce.

### 1.3 Excluir

**Excluir NF** e **excluir instrução** só funcionam sem nenhuma leitura
registrada. Havendo, o botão fica desabilitado com o motivo visível —
*"3 itens com separação iniciada"* — e não uma mensagem de erro após o clique.

Excluir uma NF de instrução com outras NFs já em separação é permitido: a trava
é por NF, não pela instrução inteira.

### 1.4 Concluir instrução

Exige todos os itens fechados. Item **completo** fecha pelo botão **Fechar** da
linha; só o **incompleto** oferece *"Fechar com divergência"*. Antes a única
ação era a de divergência, que carimbava `FECHADO_DIVERGENCIA` mesmo com tudo
coletado e obrigava ao *"Concluir mesmo assim"* como se faltasse caixa.

Enquanto não for concluída aqui, a instrução **continua no coletor** — com todos
os cadeados fechados, marcada *"aguardando conferência"*. **Reabrir** um item
devolve a instrução a `EM_SEPARACAO` e a traz de volta ao coletor, limpando quem
e quando concluiu.

Havendo divergência:

```
┌────────────────────────────────────────────────────────────┐
│ RED-000001 tem 2 itens com divergência                     │
├────────────────────────────────────────────────────────────┤
│ NF 952463 · 2685 CARNE BOVINA CONSERVA                     │
│   Previsto 20 · Separado 14 · Falta 6                      │
│   "Sem saldo em estoque, conferido com o Rodrigo"          │
│   Fechado por MARIA · 05/09 16:12                          │
│                                                            │
│ NF 952918 · 376670 SALSICHA MARCA DEMO 24X180G SS               │
│   Previsto 19 · Separado 17 · Falta 2                      │
│   "2 caixas avariadas na câmara 4"                         │
│   Fechado por MARIA · 05/09 16:40                          │
├────────────────────────────────────────────────────────────┤
│            [ Voltar ]        [ Concluir mesmo assim ]      │
└────────────────────────────────────────────────────────────┘
```

As justificativas já foram dadas item a item no fechamento. Aqui só se confirma
— sem redigitar.

---

## 2. Acompanhamento da separação

Tela viva, usada enquanto a operação acontece. Atualiza sozinha a cada poucos
segundos.

Instrução → **popup** com as NFs (previsto, realizado, status) → clique na NF
abre os itens:

```
┌──────────────────────────────────────────────────────────────┐
│ NF 952463 · ATACADAO S A                        142/245      │
├──────────────────────────────────────────────────────────────┤
│ 🔓 2685    CARNE BOVINA CONSERVA MARCA DEMO    14/20   ● JOÃO    │
│ 🔒 2690    CARNE BOVINA MARCA DEMO        15/15             │
│ 🔒⚠ 389426 CARNE BOV COZ DESFIADA           9/20             │
│ 🔓 384699  CARNE CONSERVA MARCA DEMO 24X320G    0/20             │
├──────────────────────────────────────────────────────────────┤
│ Item selecionado:  [ Fechar cadeado ]  [ Reabrir ]           │
└──────────────────────────────────────────────────────────────┘
```

O ponto verde mostra **quem está separando aquele item agora** — informação que
não existe em lugar nenhum hoje e resolve o "quem pegou esse item?".

**Fechar cadeado** exige justificativa, sempre, e por item. Avisa se houver
separador ativo no item: *"JOÃO está separando este item neste momento"* —
fechar é permitido, mas consciente. A próxima leitura dele é recusada com a
mensagem correspondente no coletor.

**Reabrir** volta o item para `EM_SEPARACAO`, preservando a contagem e o
histórico da justificativa anterior.

---

### 2.1 Desembarque: o pallet montado por quantidade (0.8.3)

A carreta chega e é descarregada de uma vez, no chão da doca. Bipar caixa a
caixa depois é retrabalho — e o que não era bipado ficava fora do sistema.
**Desembarcar**, na linha da NF (só admin), monta o pallet por quantidade:

1. Escolhe-se no cadastro **quem desembarcou**. É esse nome que aparece em
   **Montado por**, no Estoque — não o do admin que digitou.
2. **Iniciar desembarque** cria o pallet P-RED, já amarrado à NF, e manda a
   etiqueta para a impressora.
3. A tabela lista os itens da NF com o que ela pede, o que já foi lançado e um
   campo **Neste pallet**. O número é o total daquele pallet, não o acréscimo:
   digitar 8 onde havia 10 tira duas caixas. O máximo de cada linha é o que
   falta na NF — somando o que o coletor já bipou, nunca passa do XML.
4. **Salvar caixas** grava; **Encerrar pallet** fecha e imprime o cartão. O
   pallet entra na fila de endereçamento do coletor como qualquer outro, e o
   diálogo volta a pedir o responsável, para o próximo pallet da mesma NF.
5. **Cancelar desembarque** (com justificativa) tira as caixas lançadas — elas
   ficam marcadas como excluídas, não apagadas — e o pallet sai de circulação.

Item que fecha a quantidade da NF **fecha o cadeado sozinho**: o desembarque é
a conferência. Tirar caixa depois reabre o cadeado — fechado com menos do que a
NF pede seria divergência, e não é disso que se trata.

Cada caixa lançada vira uma leitura igual à do bipe, com o produto, o pallet e
o responsável; o que as distingue é a ligação com o desembarque. Por isso
estoque, cartão, expedição e relatório enxergam o pallet sem saber que ele não
foi bipado. Uma NF tem **um** desembarque aberto por vez.

## 3. Conteúdo do pallet: repaletizar e excluir

### 3.0 Juntar / repaletizar, bipando (0.7.0)

Bloco no alto da aba **Endereçamento**, com o mesmo fluxo do coletor (§6A de
TELAS-COLETOR): interruptor **Juntar o pallet inteiro** (marcado: origem e
destino, pede confirmação e junta tudo — fusão) ou caixa a caixa (desmarcado).
Com o leitor USB: Origem, Enter, Destino, Enter — o par abre com os dois pallets
lado a lado (NF, cliente, endereço, total e itens) e o campo **Caixa** fica com o
foco. Cada bipe entra numa fila que manda uma caixa por vez, na ordem, com chave
própria; a lista das últimas leituras diz *passou* / *recusada e por quê* / *na
fila* / *sem resposta*. Só pallets da mesma NF, ou pallet novo como destino.

- Recusa do servidor: a mensagem aparece junto ao campo, aquela caixa sai da
  fila e o resto segue.
- Sem resposta (ou 15 s sem retorno): a fila **para** e o **Reenviar** manda a
  mesma chave — nunca passa caixa a mais.
- **Desfazer última**: volta a última caixa do operador. Sem resposta, a tela
  relê os dois pallets antes de liberar o botão de novo — repetir às cegas
  devolveria duas caixas.
- **Concluir e imprimir cartões**: fecha a sessão e mostra cartão por cartão o
  que saiu e o que não saiu, com **Imprimir de novo**. Caixa bipada durante a
  conclusão não passa, e o resumo diz quais foram.
- **Trocar pallets** com caixas passadas ou na fila pede confirmação; enquanto o
  diálogo está aberto a fila espera. Resposta que chega depois de fechar o par
  é avisada, não descartada.

Perfil CONSULTA não vê o bloco. O bloco abaixo continua para transferir por
quantidade e excluir.

### Por quantidade

Localiza-se o pallet (busca ou bipe com leitor
USB) e escolhe-se quanto de cada item mexer. A mesma seleção de quantidades
serve às duas ações, porque a pergunta do operador é sempre a mesma — *quais
caixas deste pallet* — e só o destino muda:

```
┌──────────────────────────────────────────────────────────────┐
│ P-RED-000123 · NF 952463 · ATACADAO · 67 cx · C8-D28 nív.4   │
├──────────────────────────────────────────────────────────────┤
│ Item                              No pallet    Transferir    │
├──────────────────────────────────────────────────────────────┤
│ 2685   CARNE BOVINA CONSERVA          20         [ 8 ]       │
│ 2690   CARNE BOVINA MARCA DEMO       15         [ 15 ]      │
│ 2503   FEIJOADA MARCA DEMO 12X830G        32         [ 0 ]       │
├──────────────────────────────────────────────────────────────┤
│ 23 caixa(s) selecionada(s)                                   │
│                                                              │
│ ⚠ Os cartões afetados serão reimpressos.                     │
├──────────────────────────────────────────────────────────────┤
│      [ Transferir para pallet novo ]  [ Excluir separação ]  │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 Transferir — repaletização

Serve para dividir pallet alto demais, isolar mercadoria avariada ou separar
carga que precisa ir em veículos diferentes.

O sistema cria o pallet destino na hora, já `ENCERRADO` e **sem endereço** —
ele entra na fila de endereçamento do coletor. Imprime o cartão novo e
reimprime o da origem, que mudou de conteúdo.

Esvaziando a origem por completo, ela vai para `ESVAZIADO` e libera a posição
que ocupava.

**Transferir não é excluir.** A quantidade separada do item não muda; as caixas
só trocaram de pallet. Cadeado não reabre e o líquido do relatório continua o
mesmo.

Pallet expedido não pode ser repaletizado. Pallet de **retorno aberto** (§9.2),
em conferência ou aguardando tratativa, também não (`PALLET_EM_RETORNO`): a
conferência compara com a foto do que ele levou, e caixa trocada de pallet no
meio dela viraria falta num e sobra no outro.

### 3.2 Excluir separação

Para caixa bipada por engano: **reduz a quantidade separada**. Cancela as
leituras mais recentes daquele item naquele pallet e reabre o cadeado se estava
fechado.

Fica em **dois lugares**. O principal é a **linha do item na instrução** (§1):
botão **Excluir caixas**, que lista em quais pallets estão as caixas daquele
item e quantas em cada (`GET /itens/{id}/pallets`); pallet expedido aparece
travado. É olhando o item que se descobre a caixa a mais — antes a exclusão só
existia aqui na aba Endereçamento, atrás de digitar o código do pallet, e
ninguém a achava. O segundo lugar é este, a partir do pallet.

Pallet de retorno aberto (§9.2) recusa a exclusão pelo mesmo motivo da
transferência (`PALLET_EM_RETORNO`).

Diferente do coletor, **exige justificativa** — quem opera aqui é supervisor, e
o custo de digitar é baixo comparado ao de não saber depois por que sumiram
caixas. A exclusão aparece em coluna própria no relatório (§8).

O botão é vermelho e a confirmação repete a diferença em relação a transferir:
a escolha errada entre as duas é justamente o erro que a tela precisa evitar.

---

## 4. Produtos

**Aba Cadastro** — lista com código, descrição, códigos de barras vinculados e
se estão confirmados. Busca por código, descrição ou código de barras (o campo
aceita bipe direto de um leitor USB).

Editar permite adicionar e desativar códigos de barras. O produto em si vem do
XML e raramente precisa de edição manual.

**Aba Códigos não identificados** — a fila alimentada pelas leituras recusadas
no coletor:

```
┌────────────────────────────────────────────────────────────┐
│ Código           Leituras  Último          Por             │
├────────────────────────────────────────────────────────────┤
│ 17890000999123      7      05/09 14:22    JOÃO   [Vincular]│
│ 27890000888456      2      05/09 11:05    PEDRO  [Vincular]│
└────────────────────────────────────────────────────────────┘
```

A contagem de leituras é o que diz a urgência: 7 tentativas significa que tem
separador parado agora.

**Importar planilha de origem** (`CODIGO` + `DESCRICAO` + `COD_BARRAS_CAIXA`)
— entra como conferência cruzada. O relatório separa em três grupos: confere
com o XML, diverge do XML (cada uma é erro de cadastro na origem, vale
devolver para origem corrigir) e produto ainda não conhecido.

---

## 5. Usuários · 6. Impressoras · 7. Boxes

**Usuários** — login, nome, perfil, impressora padrão, permissão
`multi_item` e reset de senha. Usuário com leituras registradas é **inativado**,
nunca excluído — o histórico precisa continuar apontando para alguém.

**Impressoras** — nome, IP, porta, e botão **"Imprimir etiqueta de teste"**, que
também detecta o dpi por SGD e grava. Resolve na hora a dúvida de 203 × 300 dpi
e serve de diagnóstico quando o coletor não imprime.

**Boxes** — a malha inicial vem do `BOXES DEMO.xls` na primeira subida: 740
boxes e 4.440 posições (a ANTECAMARA/DOCA fica de fora). Box pode ser
inativado, nunca excluído.

### 7.1 Endereços — cadastro em lote (0.7.0)

Aba **Endereços**, para supervisor e admin. Em cima, o cadastro: filtros por
depósito, rua (só as do depósito escolhido), texto (box ou código de barras) e
*só ativos*, com os totais — boxes, ativos, posições e ocupadas. Tabela com
box, código de barras, depósito, rua, tipo, níveis, ocupados e situação.

Para incluir ou corrigir de uma vez, pela planilha:

1. **Baixar modelo** (só o cabeçalho) ou **Baixar cadastro atual** (para editar
   o que já existe).
2. Preencher no Excel: Depósito (código), Depósito (nome), Rua, Box, Código de
   barras (em branco = o próprio box), Tipo `ESTOCAGEM` ou `PICKING`, Níveis (1
   a 20), Ativo `SIM` ou `NÃO`. Também vale CSV.
3. **Subir planilha** (só admin): a prévia mostra as contagens — novos,
   alterados, iguais, com erro — e a tabela das linhas que mudam, erros
   primeiro, com o **número da linha do Excel** e o que muda ou o que está
   errado. Nada é gravado ainda.
4. **Gravar N box(es)**, habilitado só sem erro. Com qualquer erro nada é
   gravado, nem as linhas certas. Se o cadastro mudou entre a prévia e o
   Gravar, a prévia é refeita com a situação de agora.

A planilha **nunca apaga**: box que não está nela fica como está. Reduzir
níveis só se o nível que sai nunca teve pallet; box com pallet não desativa;
código de barras de outro box, box repetido e depósito novo sem nome dão erro.
A planilha lida na hora da prévia é a que o Gravar manda — salvar de novo no
Excel no meio não troca o que foi conferido.

---

## 8. Consulta de endereçamento

Busca por **NF, instrução, pallet ou box** — a mesma tela responde nos dois
sentidos:

```
┌──────────────────────────────────────────────────────────────┐
│ NF 952463 · ATACADAO S A · 245 caixas · 6 pallets            │
├──────────────────────────────────────────────────────────────┤
│ P-RED-000123   C8-D28  nível 4   45 cx   CAMARA 8 · Rua D    │
│ P-RED-000124   C8-D28  nível 5   50 cx   CAMARA 8 · Rua D    │
│ P-RED-000131   C4-A12  nível 2   38 cx   CAMARA 4 · Rua A    │
│ P-RED-000132   C4-A12  nível 3   42 cx   CAMARA 4 · Rua A    │
│ P-RED-000140   C5-B07  nível 1   40 cx   CAMARA 5 · Rua B    │
│ P-RED-000141   ⚠ sem endereçamento       30 cx               │
├──────────────────────────────────────────────────────────────┤
│ Ordenado por câmara e rua        [ Imprimir roteiro ]        │
└──────────────────────────────────────────────────────────────┘
```

Duas coisas que fazem diferença no uso real: o pallet **sem endereçamento**
aparece em destaque (senão só se descobre no carregamento), e a ordenação segue
câmara e rua — é a lista que alguém leva na mão para buscar os pallets, então
ela precisa refletir o caminho a percorrer, não a ordem de criação.

---

## 9. Baixar NF expedida

Lista as NFs **concluídas e ainda não expedidas**, com filtro por cliente,
instrução e período. Seleção múltipla. NF com retorno aberto (§9.2) fica fora
da lista até a tratativa.

A confirmação mostra o efeito antes de aplicar:

```
┌────────────────────────────────────────────────────────────┐
│ Confirmar expedição de 2 notas                             │
├────────────────────────────────────────────────────────────┤
│ NF 952463 · ATACADAO      6 pallets · 6 posições liberadas │
│ NF 952918 · SUPERMERCADO DEMO      3 pallets · 3 posições liberadas │
│                                                            │
│ Os pallets saem do estoque e os endereços ficam livres.    │
├────────────────────────────────────────────────────────────┤
│                  [ Cancelar ]    [ Confirmar ]             │
└────────────────────────────────────────────────────────────┘
```

---

### 9.1 Estornar expedição (erro operacional)

Para a NF baixada por engano, quando os pallets nunca saíram do lugar. Exige
justificativa. Restaura os endereços originais. A NF volta a `CONCLUIDA`, ou a
`RETORNO_PARCIAL` se ainda tem pallet na rua de uma saída anterior.

Recusado em dois casos:

- algum pallet daquela expedição **já voltou** (`EXPEDICAO_COM_RETORNO`) — a
  carga saiu de fato. Se o retorno foi registrado por engano, cancele o retorno
  antes (§9.2);
- a NF tem **retorno aberto** (`RETORNO_EM_ABERTO`).

Posição que já tenha sido ocupada por outro pallet nesse meio tempo **não** é
tomada de volta — o pallet retorna sem endereço e entra na fila de
endereçamento, com aviso explícito na confirmação:

```
⚠ 1 dos 6 pallets não pôde voltar ao endereço original
  P-RED-000124 · C8-D28 nível 5 agora ocupado por P-RED-000198
  Este pallet ficará pendente de endereçamento.
```

### 9.2 Retorno de NF expedida (0.6.0)

Para carga que saiu e o cliente **não recebeu**: recusou tudo (atraso na
entrega, por exemplo) ou alguns pallets voltaram para reentregar. Substitui a
reentrega imediata, que devolvia o pallet ao estoque no olho — caixa extraviada
no caminho só aparecia no carregamento seguinte. (`POST /reentregas` segue no
servidor por compatibilidade; a tela não usa mais.)

Três passos, com a NF presa entre o primeiro e o último:

```
  Retaguarda          Coletor · "Conferir"                Retaguarda
  ──────────          ────────────────────                ──────────
  Registrar ──► EM_CONFERENCIA ──último pallet──► AGUARDANDO_TRATATIVA ──► TRATADO
  pallets +     caixa a caixa     fechado           Reentrega
  motivo        falta: justifica                    Refaturamento
                     │                              Descarte
                     └── ou "Retorno completo" (supervisor, sem bipar)
```

Enquanto o retorno está em conferência ou aguardando tratativa, a NF **sai das
prontas para expedir** e o servidor recusa expedi-la (`RETORNO_EM_ABERTO`): a
mercadoria voltou, mas ninguém decidiu ainda se ela sai de novo. A situação da
NF em si não muda até a tratativa. Os pallets do retorno aberto também ficam
presos: não se juntam, não se repaletizam e não perdem caixa
(`PALLET_EM_RETORNO`, §3) — a conferência compara com a foto do que levaram.

Cliente que recebeu e devolveu parte, com NF dele, é **devolução** (§10), não
retorno.

#### Registrar

Na aba **Expedição**, seção *Retorno de mercadoria*: número da NF expedida
(`EXPEDIDA` ou `RETORNO_PARCIAL`) e **Abrir**. Aparecem os pallets que estão na
rua — sempre pallet inteiro. **Recusa total — marcar todos** é o caso mais
comum: um clique em vez de marcar vinte, e o cursor já vai para o motivo, que é
obrigatório.

```
┌────────────────────────────────────────────────────────────┐
│ NF 952463 · ATACADAO S A                          EXPEDIDA │
├────────────────────────────────────────────────────────────┤
│ Marque os pallets que voltaram — sempre pallet inteiro.    │
│                            [ Recusa total — marcar todos ] │
│ ☑ P-RED-000123   45 caixas   expedido em 03/09 16:42       │
│ ☑ P-RED-000124   50 caixas   expedido em 03/09 16:42       │
│ ☐ P-RED-000131   38 caixas   expedido em 03/09 16:42       │
│                                                            │
│ Motivo [ cliente recusou por atraso na entrega         ]   │
│                                                            │
│ Voltam: 2 de 3 pallet(s), 95 caixa(s)                      │
│                                    [ Registrar retorno ]   │
└────────────────────────────────────────────────────────────┘
```

A confirmação repete os pallets, as caixas e se é recusa total ou retorno
parcial. Ao registrar:

- os pallets vão para `ENCERRADO` **sem endereço** e entram na fila de
  endereçamento do coletor — a posição antiga já foi reaproveitada;
- o servidor tira a **foto do que cada pallet levou**, por produto. A
  conferência compara com ela, e não com o pallet como estiver depois.

**Um retorno aberto por NF.** Com outro em aberto a tela avisa antes de deixar
marcar pallet, e o servidor recusa. Pallets que já voltaram aparecem embaixo,
com **Reimprimir cartão** — o cartão costuma se perder no transporte.

Registrar é de SUPERVISOR ou ADMIN; os outros perfis veem a NF sem os
controles.

#### Lista de retornos e conferência

A conferência caixa a caixa é no coletor, por qualquer usuário
(`TELAS-COLETOR.md` §5B). Aqui fica a lista **Retornos**: os abertos por
padrão, e *mostrar tratados e cancelados* para o resto. Ela se atualiza sozinha
a cada 5 s enquanto houver retorno em conferência (e nenhum diálogo aberto) —
o supervisor espera o fim da bipagem para dar a tratativa.

Coluna **Conferência**: pallets fechados/total e caixas conferidas/esperadas.
Com falta, *"faltam N caixa(s)"* enquanto está em conferência — ainda é
progresso — e *"N caixa(s) faltaram"* depois, quando já veio justificada do
coletor.

| Situação | Ações (supervisor) |
|---|---|
| `EM_CONFERENCIA` | Ver conferência · Retorno completo · Cancelar retorno |
| `AGUARDANDO_TRATATIVA` | Ver conferência · Tratativa · Cancelar retorno |
| `TRATADO` | Ver conferência · Desfazer tratativa |
| `CANCELADO` | Ver conferência |

**Ver conferência** é leitura e fica para todos os perfis: pallet a pallet,
produto a produto, conferido/esperado, a justificativa da falta, a marca
`MANUAL` e *"não voltou"* no pallet fechado com zero.

**Retorno completo** — para pallet que volta lacrado e foi contado de outro
jeito (na doca, com o motorista). Fecha os pallets ainda abertos como
conferidos, sem bipar, e leva o retorno à tratativa. Observação opcional. Fica
registrado como conferência **manual**, com usuário e hora; os pallets já
fechados continuam como estão.

**Reabrir pallet** — dentro do *Ver conferência*, enquanto não houver
tratativa: depois dela o pallet pode já ter ido para a rua de novo ou para o
descarte. O pallet volta a aberto, sem a justificativa e sem a marca manual, e
o retorno volta a `EM_CONFERENCIA`. As leituras ficam.

#### Tratativa

Com o retorno em `AGUARDANDO_TRATATIVA`. O diálogo resume cliente, pallets,
caixas conferidas e motivo, com aviso âmbar se faltou caixa.

| Tratativa | Pede | Efeito |
|---|---|---|
| **Reentregar** | — | pallets ficam no estoque; a NF volta às prontas para expedir, com o que voltou |
| **Refaturar** | NFs novas (vírgula, ponto e vírgula ou espaço) | pallets retornados saem do estoque (`ESVAZIADO`) e liberam o endereço; a mercadoria é bipada de novo, em pallets novos, pela separação normal das NFs novas |
| **Descartar** | justificativa | pallets saem do estoque (`ESVAZIADO`) e liberam o endereço |

**Reentrega com falta sai com o que voltou.** A falta vai por **NF de
devolução do cliente**, e o diálogo lista o que faltou, produto a produto — é
o que essa NF precisa cobrir; o número dela pode ir na observação. Na
tratativa, as caixas que faltaram **saem do pallet** (ficam na leitura como
"faltou no retorno", `MODELO-DE-DADOS.md` §9.3): expedição, cartão, estoque e
fila de endereçamento passam a ver só o que voltou. Pallet que não voltou nada
sai de circulação (`ESVAZIADO`, endereço liberado). Não é exclusão: a caixa foi
separada e saiu, então não reabre o item nem desconta do separador no
relatório. Na lista, a tratativa aparece como *"saiu com N · falta de M cx por
NF de devolução do cliente"*.

Se **nada** voltou, reentregar é recusado (`NADA_A_REENTREGAR`): descarte, ou
cancele o retorno se foi lançado por engano. Se as caixas aparecerem antes da
tratativa, **reabra o pallet** no *Ver conferência* e confira de novo.

Qualquer tratativa é recusada se algum pallet **mudou desde o registro**
(`PALLET_ALTERADO`): saiu de `ENCERRADO`/`ENDERECADO` ou tem conteúdo diferente
da foto. A tratativa decide o destino do que voltou, e precisa ser o mesmo
pallet que voltou.

Situação da NF depois: com pallet dela no CD, `CONCLUIDA` se nada ficou na rua
ou `RETORNO_PARCIAL` se ficou; sem nenhum (refaturado ou descartado), continua
`EXPEDIDA` — foi faturada e saiu, e o destino da mercadoria está no retorno.

#### Desfazer e cancelar

Os dois pedem justificativa. Tudo no retorno — registro, retorno completo,
reabrir, tratativa, desfazer, cancelar — fica no log de auditoria.

Depois de desfazer ou cancelar, a situação da NF é **recalculada pelos
pallets**, pela mesma regra da tratativa: `EXPEDIDA` se nenhum está no CD,
`CONCLUIDA` se estão no CD e nada ficou na rua, `RETORNO_PARCIAL` se parte está
no CD e parte na rua. Não volta à situação de antes do registro: nesse meio
tempo outro pallet da NF pode ter saído ou voltado.

**Desfazer tratativa** — o retorno volta a `AGUARDANDO_TRATATIVA`. Pallets
refaturados ou descartados voltam ao estoque como `ENCERRADO`, sem endereço.
Recusado quando não há mais o que desfazer:

- reentrega cuja NF **já saiu de novo** (`NOTA_JA_REEXPEDIDA`) — a mercadoria
  já foi;
- reentrega com pallet que **mudou desde o registro** (`PALLET_ALTERADO`);
- refaturamento cujas NFs novas **já têm caixa separada**
  (`REFATURAMENTO_EM_SEPARACAO`) — a mercadoria já está em pallet novo, e
  voltar os pallets do retorno duplicaria o estoque;
- refaturamento ou descarte com pallet que deixou de estar esvaziado — foi
  usado para outra coisa, e voltá-lo criaria estoque que não existe
  (`PALLET_REUTILIZADO`);
- NF que já tem **outro retorno aberto** (`RETORNO_EM_ABERTO`) — um retorno
  aberto por NF.

O diálogo avisa o risco que o servidor não enxerga: descarte só se desfaz se a
mercadoria ainda não foi descartada de fato.

**Cancelar retorno** — retorno registrado por engano, quando a mercadoria não
voltou. Só antes da tratativa (tratado, desfaça a tratativa primeiro). Os
pallets voltam a constar como **expedidos**: saem da fila de endereçamento, o
endereço que tenham ganhado nesse meio tempo é liberado e a marca de retornado
sai da expedição. Pallet que mudou desde o registro — fora de `ENCERRADO` ou
`ENDERECADO`, ou com conteúdo diferente da foto — trava o cancelamento
(`PALLET_ALTERADO`). O que foi conferido fica no histórico, mas deixa de valer.

#### Permissões

| Ação | Quem |
|---|---|
| Registrar, retorno completo, reabrir pallet, tratativa, desfazer tratativa, cancelar | SUPERVISOR ou ADMIN |
| Lista de retornos e *Ver conferência* | todos os perfis; os outros veem sem os botões de ação |
| Conferir caixa a caixa, fechar pallet (com falta, justificando), desfazer a última leitura | qualquer usuário, no coletor |

O servidor confere o perfil em cada rota: esconder o botão não é a trava.

---

## 10. Devolução

Caso distinto do retorno (§9.2): o cliente **recebeu**, abriu a carga e devolve
parte dela, com **NF de devolução emitida por ele**.

### 10.1 Importar a NF de devolução

Sobe-se o XML obtido no Consulta DANFE / Meu DANFE e informa-se a NF de venda
de origem, de um de três jeitos (0.7.0):

- **NF do sistema** — busca pelo número (sem o limite das 200 mais novas; a NF
  exata, sendo uma só, já fica escolhida);
- **XML da NF de origem** — para a NF que não passou pelo sistema; se o sistema
  tiver a chave, a devolução fica ligada a ela também;
- **Digitar o número** — só o número, num campo livre.

Se o XML da devolução já cita a NF de venda (`refNFe`), o diálogo abre com ela:
escolhida, se está no sistema; digitada, com a chave à vista, se não está. Ao
escolher a origem, a tela confere com o servidor e mostra o que vai como origem
e os avisos: NF de outro CNPJ ("confira se é a NF certa") e NF de venda que já
tem devolução — a mesma venda pode ter mais de uma devolução, então é aviso, não
trava. Sem origem, não lança. Na lista e no detalhe, a origem aparece com o
jeito que foi informada: *do sistema*, *pelo XML* ou *digitada*.

### 10.2 De-para dos itens

O XML traz o `cProd` **do cliente**, não o da EMPRESA. Mas o `cEAN` é GTIN, código
global — o mesmo GTIN-14 aparece nos dois XMLs. O sistema sugere o vínculo
sozinho e o operador confirma:

```
┌──────────────────────────────────────────────────────────────────────┐
│ NF devolução 44521 · ATACADAO S A · origem NF 952463                 │
├──────────────────────────────────────────────────────────────────────┤
│ Item do cliente                    Qtd   Produto no sistema          │
├──────────────────────────────────────────────────────────────────────┤
│ 957712 CARNE BOV CONS MARCA DEMO 320G   8 →  ✅ 2685 CARNE BOVINA CONS…  │
│        cEAN 27890000217084               vinculado pelo GTIN         │
│                                                                      │
│ 957730 SALSICHA MARCA DEMO 180G         12 →  ✅ 376670 SALSICHA MARCA DEMO…   │
│        cEAN 17890001250023               vinculado pelo GTIN         │
│                                                                      │
│ 990455 FEIJOADA MARCA DEMO 830G         4 →  ⚠ [ selecionar produto  ▾ ] │
│        cEAN ausente                      sem GTIN — vincular à mão   │
├──────────────────────────────────────────────────────────────────────┤
│                       [ Cancelar ]   [ Lançar para conferência ]     │
└──────────────────────────────────────────────────────────────────────┘
```

Só se lança com todos os itens vinculados.

**Quantidade e unidade (0.7.3).** Cada item traz a quantidade do XML num campo
editável e a escolha **caixas** ou **unidades** — já marcada pela unidade do XML
(`uCom`: `UN`, `PC`, `LT`… viram unidades; o resto, caixas). A NF do cliente nem
sempre lança na unidade certa, e é aqui que se corrige. Ao lado, o que a
conferência vai esperar, em unidades: em caixas, multiplicado pela unidade por
caixa do produto escolhido (produto sem ela avisa: cadastre em Produtos ou
lance em unidades); em unidades, a própria quantidade. Quantidade inteira,
maior que zero.

**Lista.** Clicar numa devolução abre o detalhe logo abaixo da linha; clicar de
novo recolhe. Abrir outra fecha a que estava aberta.

### 10.3 Conferência física

A devolução vai para conferência no coletor, espelhando a separação: quantidade
esperada por item, cadeado ao completar, divergência com justificativa quando
vier menos que a NF declara.

**Duas formas de contar**, porque o cliente pode devolver uma lata avulsa:

| Modo | Efeito |
|---|---|
| **Bipe** da caixa | soma a caixa completa (`unidades_por_caixa`) |
| **Digitação** | soma N unidades — para a sobra que não fecha caixa |

O contador mostra as duas leituras do número:

```
┌──────────────────────────────┐
│ ← Devolução 44521        🔓 ⋮│
├──────────────────────────────┤
│ 2685 CARNE BOVINA CONSERVA   │
│ MARCA DEMO 24X320G RA            │
├──────────────────────────────┤
│         53 / 200             │
│      2 cx + 5 un             │
│                              │
│      [ Digitar unidades ]    │
└──────────────────────────────┘
```

Bipe segue sendo o caminho normal; a digitação é exceção e fica **marcada como
tal**, então o relatório separa o que foi conferido por leitura do que foi
informado à mão.

Produto sem `unidades_por_caixa` cadastrado não aceita bipe — a tela manda
cadastrar antes, em vez de contar errado.

Divergência aqui é informação de negócio, não erro: é o que revela cliente
devolvendo menos do que declarou na nota.

### 10.3.1 Pallet de devolução

Conferida a mercadoria, ela vira pallet **`P-DEV-000001`** e é endereçada pelo
coletor na mesma malha de 4.440 posições, ficando em estoque até a baixa.

O prefixo distinto (`P-DEV-` × `P-RED-`) faz o coletor reconhecer sozinho o que
foi lido, e a consulta de endereçamento passa a mostrar os dois tipos.

### 10.4 Baixa da NF de devolução

Conferida, a devolução aguarda destino:

| Destino | Efeito |
|---|---|
| **Descarte** | sai do sistema definitivamente, com justificativa |
| **Revenda** | sai do estoque de devolução; origem emite nova NF, importada pelo fluxo normal de XML. O número da nova NF fica registrado, ligando as duas pontas |

Nos dois casos os pallets de devolução são liberados dos endereços que ocupavam.

---

### 10.5 Apagar devolução (0.7.2)

No cabeçalho do detalhe, ao lado do número da NF, o botão **Excluir**
(supervisor e admin), para a
lançada por engano — XML de outra devolução, NF de origem errada. Pede
justificativa e fica no log de auditoria com o que foi apagado.

- **Lançada, sem conferência:** apaga direto. A mesma NF pode ser lançada de
  novo.
- **Com a conferência começada:** o diálogo avisa quanto já foi conferido e só
  apaga com **Apagar também a conferência** marcado. As leituras somem e os
  pallets P-DEV voltam livres, sem endereço — a mercadoria continua neles e é
  conferida de novo na devolução certa. Se o coletor bipar enquanto o diálogo
  está aberto, o servidor recusa e o aviso aparece na hora.
- **Baixada:** não se apaga; o botão nem aparece. A mercadoria já saiu do
  estoque.

Na conferência de estoque (§10.12) a linha do produto não tem **Produto
trocado** nem **Encerrar** — não há item da NF a trocar nem divergência a
justificar —, e no lugar deles vem **Baixar caixas**.

### 10.6 Produto trocado (0.7.4)

O cliente declarou um produto na NF e mandou outro. No detalhe da devolução,
cada item da NF tem o botão **Produto trocado** (supervisor e admin, antes da
baixa). O diálogo pede:

- **Produto que veio** — do cadastro;
- **Quantidade que veio**, em **caixas** ou **unidades** (com o equivalente em
  unidades, como no lançamento);
- **Veio só o produto trocado** — marcado quando nada do produto da NF veio;
- **Justificativa**.

O sistema cria um item novo na devolução, com o produto que veio, marcado
**troca do item n** (com quem registrou, quando e a justificativa). O
conferente o encontra no coletor, em **Conferir devoluções**, como
`NO LUGAR DO ITEM n: …`, e confere como qualquer item.

- **Troca do item todo:** o item da NF fecha com divergência e a justificativa
  "Produto trocado: veio …". Se o item já tinha sido fechado (ex.: "não veio"),
  fica como estava.
- **Troca de parte:** o item da NF segue aberto, para conferir a parte dele
  que veio. Com o produto da NF já conferido, só a troca de parte é aceita.
- Devolução já **conferida** volta a **em conferência**: há um item novo a
  conferir.
- **Desfazer troca** — no item da troca, enquanto ninguém o conferiu: ele sai
  e o item da NF volta a esperar o produto declarado.
- Tudo fica no log de auditoria.
- O diálogo já traz marcado **Imprimir a etiqueta 2ª via das caixas que
  vieram** (§10.7): a caixa do produto trocado não tem o código do item da NF,
  e com a etiqueta o conferente bipa em vez de digitar.

### 10.6A Motivo da devolução e planilha (0.7.7)

**0.8.0.** O motivo é escolhido de uma lista — o **cadastro de motivos**, no
botão **Motivos** do alto da tela (supervisor e admin): cadastrar, renomear,
desativar e reativar. Desativar tira da lista; o que já foi gravado fica. O
cadastro começa com os motivos já usados. **definir**, na linha da NF, escolhe
o motivo sem abrir a nota; no lançamento e no item, a lista oferece "o mesmo da
NF". A tela deixa de ficar guardada no navegador: cada versão nova aparece sem
precisar forçar a atualização (só a fonte fica guardada).

**0.7.9.** A lista mostra, sem abrir a NF, o **cliente** com bairro e cidade e
**todos os motivos** da nota (o da NF e os dos itens que diferem). O cliente é
o emitente na devolução parcial e o **destinatário** na total — NF emitida pela
EMPRESA (raiz de CNPJ 00000000). **Exportar Excel**, no alto da lista, baixa todas
as devoluções numa planilha só, um item por linha, com a situação da NF, a data
de importação, o bairro e a cidade.

O motivo aparece na mesma linha da NF, na lista, e item a item no detalhe. Ao
lançar, o campo **Motivo da devolução** preenche todos os itens; o item que
tiver outro motivo recebe o seu na coluna **Motivo** e deixa de seguir a NF.
Depois, **Alterar** (na NF) e **alterar** (no item) mudam o motivo a qualquer
momento, mesmo depois da baixa — supervisor e admin. Item sem motivo próprio
mostra o da NF, marcado *(da NF)*.

**Exportar Excel**, no cabeçalho do detalhe, baixa a planilha com todos os
itens — conferido, falta, situação, motivo e justificativa — para mandar por
e-mail.

### 10.7 Etiqueta 2ª via (0.7.5)

Botão **Etiqueta 2ª via** em cada item (supervisor e admin, antes da baixa).
Pergunta quantas (sugere as caixas que o item ainda espera) e a impressora (a
do usuário, se não escolher). A etiqueta é de 80 × 50 mm, no modelo aprovado
em campo (0.8.1): o código do produto e o nome no alto, o Code 128 no meio com
o número embaixo, e a faixa preta **2ª VIA ETIQUETA** no rodapé. O código de
barras é impresso no modo A, em que a impressora junta os dígitos aos pares, e
a largura é calculada com folga — na 0.7.5 ele saía maior que a etiqueta e o
coletor não lia.

- O código é o GTIN-14 que o produto já tem no cadastro.
- Produto sem código que o coletor leia ganha um **código interno** `029…` —
  a faixa que a GS1 reserva para uso interno, com o id do produto — gravado no
  produto (Produtos → códigos de barras). A segunda etiqueta repete o mesmo.
- Colada na caixa, o conferente bipa como qualquer caixa: soma as unidades por
  caixa do produto.
- Produto **sem unidades por caixa** não etiqueta: o bipe seria recusado logo
  depois. A tela manda cadastrar o fator em Produtos primeiro.
- **Reimpressão de etiqueta (0.7.6)** — na aba **Produtos**, o botão
  **Reimprimir etiqueta** em cada produto abre o mesmo diálogo e imprime a mesma
  etiqueta 80 × 50 mm, sem devolução nenhuma. Aparece só para o **usuário**
  `admin` (o login, não o perfil), e cada reimpressão fica no log de auditoria.

### 10.8 Caixas conferidas: pallet e exclusão (0.7.5)

No detalhe, **Caixas conferidas (n)** lista cada leitura: item, produto,
quantidade (caixa ou unidades soltas), pallet e quem conferiu. Leitura sem
pallet aparece em vermelho, com o aviso de que não entra no estoque — a lista
já abre nesse caso.

Supervisor e admin marcam leituras (**Marcar todas**, **Marcar as sem
pallet**, ou uma a uma) e:

- **Pôr no pallet** — digitam o P-DEV (a lista sugere os da NF) e as
  leituras marcadas vão para ele: as que ficaram sem pallet passam a aparecer
  no estoque, e as de outro pallet da mesma NF juntam tudo num só. O pallet
  que fica vazio volta livre, sem NF e sem endereço. Pallet de separação ou de
  outra NF é recusado.
- No coletor, **Repaletização** com o interruptor ligado junta dois P-DEV da
  mesma NF de uma vez (0.7.5); caixa a caixa, só aqui.
- **Excluir caixas** — com justificativa. A leitura fica marcada como
  excluída (não some: fica quem, quando e por quê), o item volta a esperar
  essas unidades e, se estava encerrado, reabre para encerrar de novo com o
  que ficou; NF conferida volta a em conferência. Pallet vazio volta livre.

### 10.9 Encerrar item e baixa (0.7.5)

Item em aberto tem **Encerrar** (e o rodapé, **Encerrar itens em aberto**):
completo fecha direto; faltando unidade, pede a justificativa — é o mesmo
cadeado do coletor. Encerrados todos, a NF fica **conferida** e o botão
**Baixar devolução**, no rodapé do detalhe, libera (§10.4). A baixa é aqui,
não na Expedição; antes disso o botão aparece desabilitado, com o que falta.

**Reabrir conferência (0.8.4).** Esqueceu de dar entrada numa caixa? O botão
aparece no rodapé assim que há item fechado — e é o único caminho de volta da
NF **conferida**. Ele reabre todos os itens e devolve a NF à lista do coletor,
em **Conferir**: bipa-se a caixa que faltou, no mesmo pallet, e encerra-se de
novo. O item que estava fechado com divergência perde a justificativa da
falta — ela descrevia uma contagem que está sendo refeita. Baixada não reabre:
estorne a baixa antes (§10.13).

### 10.10 Tela viva (0.7.5)

A lista se atualiza sozinha a cada 5 s, como a de instruções, mantendo aberta
a NF que estava aberta. Não mexe enquanto há diálogo aberto, leitura marcada
ou pallet digitado.

### 10.11 Filtro da lista (0.8.2)

A lista abre em **No estoque** — aguardando conferência, em conferência e
conferida —, que é o que ainda está aqui. **Já baixadas** mostra o histórico e
**Todas**, tudo. O filtro vai na rota (`GET /devolucoes?status=…`), não na
tela: a lista de uma operação de meses não precisa trafegar inteira a cada
5 s. Olhando as baixadas, o selo do menu não muda — ele conta o que está em
aberto.

### 10.12 Conferência de estoque: a sobra (0.8.2)

Mercadoria que está no CD sem NF nenhuma — nem de venda, nem de devolução.
**Conferência de estoque**, na barra (supervisor e admin), abre uma
`SOBRA-000001`: uma devolução sem NF, que aparece na mesma lista com a marca
**sobra de estoque** e entra na mesma planilha.

No coletor ela aparece em **Conferir** como as devoluções. O separador bipa o
pallet P-DEV e depois **qualquer caixa**: cada bipe conta uma caixa, e tocando
no produto ele digita quantas caixas de uma vez (§TELAS-COLETOR 6.4). Não há
quantidade esperada: o esperado é o que foi bipado.

A baixa tem dois caminhos, porque o produto pode sair em partes:

- **Baixar caixas**, na linha do produto: quantas caixas saem, descarte ou
  revenda, NF de revenda e observação. O que fica continua no pallet, e o
  pallet que esvazia sai do estoque liberando o endereço. Saindo a última
  caixa da conferência, ela fecha como baixada.
- **Baixar tudo**, no rodapé: sai tudo de uma vez. A sobra não passa pelo
  cadeado item a item — não há NF dizendo quanto era —, então a baixa fecha os
  itens junto.

A caixa baixada **não é apagada**: fica marcada como baixada (`cancelada = 3`),
ligada à baixa que a levou, com quem, quando e por quê.

### 10.13 Encerrar a conferência de estoque e estornar a baixa (0.8.3)

**Encerrar conferência**, no rodapé da sobra: a conferência de estoque não tem
NF dizendo quanto era, então nunca terminava sozinha — ficava aparecendo no
coletor para sempre. Encerrar fecha os itens, põe a nota em *conferida* e
encerra os P-DEV dela (que vão para a frente da fila de endereçamento). A
mercadoria **continua no estoque**, esperando a baixa. Apareceu mais sobra?
**Reabrir conferência** volta a aceitar bipe, no mesmo pallet — o mesmo botão
que reabre a devolução do cliente (§10.9).

**Estornar baixa**, na devolução já baixada (só o admin): baixou por engano —
descarte no lugar de revenda, NF trocada — e a mercadoria continua aqui. Os
pallets que ainda têm caixa voltam do *expedido* e **retomam o endereço**, se a
posição continuar livre; se outro pallet já a ocupou, voltam sem endereço, para
o coletor endereçar de novo — a mesma regra do estorno de expedição (§9.1). A
devolução volta a *conferida*, e a conferência de estoque volta a *em
conferência*, com os itens abertos.

O que o estorno **não** desfaz: caixa que já tinha saído na baixa por caixa
(§10.12) não volta — ela saiu do CD antes, por outra decisão —, e o aviso diz
quantas são. Baixa de revenda cuja NF nova já entrou e está sendo separada é
recusada: estornar contaria a mesma mercadoria duas vezes. A baixa estornada
não some: fica guardada inteira, com quem estornou e por quê. Devolução que já
teve baixa (de caixas ou estornada) deixa de poder ser apagada.

## 10A. Estoque

Responde "o que está aqui" e "o que já saiu", na granularidade de **pallet** —
que é a unidade que ocupa posição e sobe no caminhão.

Pallet de devolução (marca **DEV**) conta **volumes** na coluna — uma leitura
da conferência, um volume —, e abrir a linha mostra o que foi conferido nele,
por produto e em **unidades** (0.7.5; antes abria escrito "sem caixas").

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Estoque                            12 pallet(s) · 543 caixas · 2 sem end.│
├──────────────────────────────────────────────────────────────────────────┤
│ Situação [ Em estoque ▾ ]  Tipo [ Todos ▾ ]  NF [    ]  Cliente [      ] │
│ Box [        ]   [ Aplicar ]   Baixar CSV                                │
├──────────────────────────────────────────────────────────────────────────┤
│ PALLET         NF      CLIENTE       CAIXAS  ENDEREÇO      MONTADO POR    │
│ P-RED-000123   952463  ATACADAO S A     45   C8-D28 nív.4  JOÃO SILVA     │
│                                                            06/09, 13:35   │
│ P-RED-000124   952463  ATACADAO S A     50   sem endereço  JOÃO SILVA     │
└──────────────────────────────────────────────────────────────────────────┘
```

**Situação** é o filtro central:

| Valor | O que traz |
|---|---|
| **Em estoque** | `EM_USO`, `ENCERRADO` e `ENDERECADO` — está fisicamente no CD |
| **Já saiu** | `EXPEDIDO`, com **quem expediu, data e hora** |
| **Todos** | os dois, com a situação em coluna |
| **Fundidos e esvaziados** | pallets que deixaram de existir como volume |

Escolhendo *Já saiu* ou *Todos*, aparecem as colunas **Situação** e **Saída**
(usuário + data/hora). Pallet de devolução sai pela baixa, não pela expedição —
a coluna diz qual dos dois foi.

Clicar na linha abre o conteúdo do pallet sem sair da tela: saber que um pallet
tem 45 caixas não diz quais produtos são, e é isso que decide se vale ir até a
rua conferir.

**Duas armadilhas que a tela evita de propósito:**

- **Pallet fundido ou esvaziado não entra no estoque.** Ele continua na tabela,
  mas as caixas foram para outro pallet — somar os dois contaria cada caixa
  duas vezes. Aparecem só no filtro próprio, com zero caixas.
- **Estrado sem nada em cima não é estoque.** Conta pallet e não representa
  mercadoria nenhuma.

Pallet **em montagem** (`EM_USO`) entra no estoque: as caixas já estão em cima
dele, dentro do CD. Deixar de fora faria o número mentir para menos durante toda
a separação — e o pallet de devolução em conferência nunca passa por
`ENCERRADO`, iria direto de invisível para expedido.

A contagem de caixas vem das **leituras vivas**, não do previsto da NF: é o que
foi bipado que está no palete. Exclusão de separação já sai descontada.

**Pallet que voltou de NF expedida** (§9.2) entra em *Em estoque* desde o
registro — volta `ENCERRADO`, sem endereço — e leva embaixo do código a
situação do retorno: *"Retorno NF 952463 · em conferência"*, *"· aguardando
tratativa"* ou *"· para reentrega"*. Fechado com falta no coletor, as
**caixas** passam a ser as conferidas na volta, não as que saíram: o estoque é
o que está aqui. Refaturado ou descartado, o pallet vai para *Fundidos e
esvaziados* com *"· refaturado (952501)"* ou *"· descartado"*, e a coluna Saída
traz a data da tratativa. Retorno cancelado não conta. O CSV ganha a coluna
*Retorno*.

O **CSV** é gerado do que está na tela, com os mesmos filtros, para conferência
de inventário fora do sistema.

---

## 10B. Coletores

Provisionamento dos aparelhos. Começa pelo **endereço do servidor**, mostrado e
editável:

```
┌──────────────────────────────────────────────────────────────┐
│ Endereço do servidor                                         │
│ IP desta máquina agora: 192.168.1.95                         │
│ [ 192.168.1.95:8080            ]  [ Aplicar ]                │
└──────────────────────────────────────────────────────────────┘
```

O IP vem do **servidor**, que enxerga as próprias placas de rede — nunca de
`location.origin`. A retaguarda quase sempre é aberta na própria máquina do
servidor, onde a página se vê como `localhost`; um QR com `localhost` dentro não
leva coletor nenhum a lugar algum. Máquina com mais de uma placa (Wi-Fi e cabo,
VPN, adaptador virtual) pode ter o palpite errado, por isso o campo é editável e
os QRs acompanham. Endereço com `localhost` acende aviso.

Daí saem **dois QRs, na ordem de uso**:

| QR | Lido com | Faz |
|---|---|---|
| **1. Instalar o app** | navegador do coletor | baixa o APK para a pasta de downloads |
| **2. Configurar** | gatilho do coletor, na tela de configuração do app | preenche IP e porta e testa a conexão |

São dois porque o momento é diferente: no primeiro o coletor ainda não tem o
app, então quem lê é o navegador; no segundo o app já existe e lê pelo laser.

Na mesma aba: a versão publicada, o formulário para publicar um APK novo e o
endereço fixo do APK para quem preferir montar um perfil do StageNow.

**Se o IP da máquina mudar**, os coletores param de encontrar o servidor. A
correção é bipar de novo o QR 2 em cada aparelho — segundos cada, mas em todos.
É a razão de o IP fixo (ou reserva de DHCP) valer a conversa com o TI.

---

## 11. Relatório de separação

Filtros por período, usuário, instrução, NF e produto.

**Analítico** — uma linha por item separado, com a exclusão em coluna própria:

```
┌───────────────────────────────────────────────────────────────────────┐
│ Usuário  Data/hora     Instrução   NF      Item              Sep Exc =│
├───────────────────────────────────────────────────────────────────────┤
│ JOÃO     05/09 14:02   RED-000001  952463  2685 CARNE BOV      6   2  4│
│ JOÃO     05/09 14:31   RED-000001  952463  2690 CARNE GOLD    15   0 15│
│ PEDRO    05/09 15:10   RED-000001  952918  376670 SALSICHA    17   0 17│
├───────────────────────────────────────────────────────────────────────┤
│ TOTAL                                                         38   2 36│
└───────────────────────────────────────────────────────────────────────┘
```

A coluna **=** é o líquido, o número que vale. No exemplo do item `2685`: o
separador coletou 3 caixas, excluiu 2 e coletou mais 3 — separado 6, excluído 2,
**líquido 4**.

**Aba Exclusões** — só as exclusões, isoladas: usuário, data/hora, instrução,
NF, item, quantidade, pallet, origem (coletor ou retaguarda) e justificativa
quando houver. Fica separado de propósito: exclusão diluída no volume total não
aparece, e frequência de exclusão por separador é o padrão que costuma anteceder
falta no cliente.

**Aba Produtividade** — caixas líquidas por usuário e por hora, para
dimensionamento de turno.

Todas exportam para Excel/CSV.

---

## 12. Pontos a confirmar

Nenhum em aberto. O desenho funcional está fechado; resta o contrato da API
antes de começar o código.
