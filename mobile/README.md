# Separação de Redespacho: app de coletor Android + servidor

App que construí para os coletores de código de barras (Android) usados no CD, com um servidor
que roda num computador da rede interna e uma tela de retaguarda no navegador.

## O problema

As caixas recebidas de outro CD para redespacho chegavam sem separação e com etiqueta de
código de barras **fixa** (o mesmo código em todas as caixas do produto). A separação era manual
e gerava falta, sobra e inversão de caixas entre clientes.

O pedido original está em [`Requisitos.txt`](Requisitos.txt).

## A solução

- **Retaguarda (navegador):** importa os XMLs das notas fiscais, monta a instrução de separação
  (cliente, itens, quantidade de caixas), cadastra produtos, usuários e impressoras e acompanha a
  separação. Uma instrução com caixa separada não pode ser excluída, e a conclusão com
  divergência exige justificativa.
- **Coletor (Android, Kotlin + Jetpack Compose):** o separador lê as caixas pelo scanner (Zebra
  DataWedge), o app confere cada leitura contra a nota e imprime a etiqueta do pallet e o cartão
  pallet na impressora térmica (ZPL). Também cobre endereçamento, consulta de pallet,
  movimentação, devolução e retorno.
- **Servidor (Kotlin + Ktor):** API REST, SQLite com migrações versionadas, backup automático,
  autenticação por PIN numérico com PBKDF2 (o operador digita com luva, no teclado numérico),
  atualização do APK pelo próprio servidor e pacote de instalação para Windows (`server/pacote`).

## Estrutura

```
app/       app Android (Kotlin, Compose) — scanner, filas de leitura, telas de separação
server/    servidor + retaguarda web (Kotlin, SQLite, HTML/JS)
docs/      API, modelo de dados, telas do coletor e da retaguarda, operação
```

Os testes unitários e os XMLs de notas usados nos testes ficaram fora da versão pública
porque usam notas reais. Nomes de clientes, produtos e números de nota nos documentos foram
trocados por fictícios.
