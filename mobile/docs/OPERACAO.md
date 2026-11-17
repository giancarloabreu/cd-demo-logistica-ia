# Operação e Infraestrutura

Temas que não são de tela nem de modelo, mas sem os quais o sistema não entra
em produção. Complementa `MODELO-DE-DADOS.md`.

---

## 1. Backend

Um `.jar` único, iniciado por **Tarefa Agendada "ao fazer logon"**. Tarefa
criada na própria conta não exige elevação, então nada no processo pede
administrador.

**A entrega é um ZIP** (`./gradlew :server:zipDistribuicao`, ~79 MB), com tudo
dentro de uma pasta só:

```
SeparacaoRedespacho/
├── iniciar.bat / parar.bat / configuracao.padrao.txt / LEIAME.txt
├── lib/        o servidor + classpath.txt (lista exata dos jars)
├── jre/        Java 21 embutido (jlink)
└── coletor/    separacao-redespacho.apk + versao.txt
```

**Sem janela, na bandeja.** O `iniciar.bat` confere o pacote, a rede e a
presença de outro servidor, sobe o Java com `javaw` (sem console) e espera o
`/saude` responder; a janela dele fecha sozinha. O servidor fica como ícone na
bandeja do Windows — duplo clique abre a retaguarda; o menu tem "Abrir pasta de
logs" e "Parar servidor" (com confirmação). A janela preta de antes, aberta o
dia todo, era fechada por engano e derrubava os coletores.

**Parar** (bandeja ou `parar.bat`) é pelo caminho normal: `parar.bat` chama
`POST /api/v1/interno/parar`, aceito só da própria máquina, e o servidor sai
rodando os ganchos de desligamento — última cópia de backup e sinal de presença
apagado. Só se ele não atender em 2 minutos o processo é derrubado à força.

**Atualizar sem perder dados.** O ZIP não traz `dados/` nem `configuracao.txt`
(traz `configuracao.padrao.txt`, copiada na primeira subida), então extrair a
versão nova por cima preserva banco e configuração. O Java carrega os jars da
lista `lib/classpath.txt`, e não `lib\*`: jar de versão anterior que sobrou na
pasta fica de fora. O schema só evolui por migração que acrescenta
(`Migracoes.kt`).

Um servidor por pasta: a subida trava `dados/servidor.trava`; um segundo
processo na mesma pasta desiste antes de tocar no banco.

O **Java vai embutido**. O PC do CD não tem Java e o operador não tem
privilégio para instalar — sem isso, o requisito de "sem instalação e sem
administrador" cairia logo no primeiro passo. O runtime é gerado por `jlink` a
partir de um JDK 21 completo da máquina de build (o JBR do Android Studio não
serve: não traz `jmods`); aponte outro com `-PjdkComJmods=<caminho>`.
`iniciar.ps1` prefere `jre\bin\javaw.exe` e só cai para o Java do sistema se
ele não existir.

**Onde roda:** qualquer máquina da rede do CD que os coletores alcancem — o
próprio SERVIDOR, um PC dedicado, ou uma estação durante o piloto. Requisitos
iguais nos três casos:

- uma **pasta local** para o banco (ex.: `C:\AppRedespacho\dados\`)
- porta 8080 liberada no firewall
- **IP fixo ou reserva de DHCP**

O IP fixo é o requisito que costuma passar batido e o que mais dói depois:
mudando o endereço, cada coletor precisa ser reconfigurado à mão.

Fica configurado em arquivo ao lado do executável: porta, caminho do banco,
caminho da pasta de backup e fuso de exibição.

**Verificação de saúde** — endpoint aberto que responde versão, hora do
servidor e situação do banco. É o que o coletor consulta no "Testar conexão" e
o primeiro lugar a olhar quando alguém diz que "o sistema caiu".

---

## 2. Banco e backup

SQLite em **disco local** da máquina do backend. Um único processo o acessa, o
que elimina o problema de lock em rede — a razão de o arquivo não ficar na
unidade compartilhada.

**Backup automático** para a pasta de rede a cada 15 min (`BACKUP_MIN`) e na
parada, por `VACUUM INTO` numa conexão própria (cópia consistente com o banco
em uso, sem parar o serviço). Sem gravação nova desde a última cópia, não grava
outra. A cópia chega como `.parcial` e só vira `.db` com um rename na mesma
pasta: ninguém lê cópia pela metade.

**Identidade no nome.** `redespacho-2026-09-12_0742-L3f2a9c1d-R7b01e4aa-G1234.db`:
`L` é a linhagem do banco (sorteada quando ele nasce, levada em toda cópia), `R`
o ramo (sorteado ao nascer e **no primeiro trabalho depois de cada
restauração**, com pai e ponto de origem registrados em `banco_ramo`) e `G` a
geração (sobe a cada transação que grava). Restaurar sem trabalhar não abre
ramo: a máquina que só subiu e parou continua idêntica à de origem, sem falso
alarme de bifurcação.
Ver `Identidade.kt`. O ramo é o que distingue "outra máquina continuou este
banco depois de mim" de "outra máquina continuou de um ponto anterior ao meu"
— comparar só gerações escondia a segunda.

**Restauração automática na subida** (`Restauracao.kt`), antes de abrir o banco:

| Banco local | O que acontece |
|---|---|
| não existe, ou sem nenhuma instrução/pallet/devolução | restaura a cópia de maior geração **com operação** (cópia de banco vazio só em último caso) |
| existe, e há cópia do mesmo banco que **descende do estado local** (árvore de ramos conferida dentro da cópia) | restaura — outra máquina continuou este banco |
| existe e está à frente (ou igual), com as cópias sendo estados anteriores dele | nada |
| existe, e há cópia do mesmo banco que seguiu **por outro caminho** (bifurcação) | nada; avisa "BIFURCAÇÃO" na bandeja, no `iniciar.bat` e na retaguarda |
| existe, e só há cópias de **outra linhagem** mais recentes | nada; avisa (dois servidores rodaram) |
| existe, sem identidade (0.5.0), e a cópia antiga mais nova tem atividade mais recente | nada; avisa |
| pasta configurada mas sem resposta na subida | nada; avisa que a restauração não foi conferida |
| cópias na pasta, nenhuma utilizável, sem banco local | sobe com banco novo e avisa "ATENÇÃO" |

**Na tarefa de logon** o `iniciar.ps1` espera a rede até 3 min antes de
desistir, e mesmo desistindo passa a pasta ao servidor: backup e presença
tentam de novo a cada ciclo. Enquanto a cópia não chega, `/saude` traz o aviso
em `backup` e a retaguarda mostra a faixa.

**Parada em ordem** (`Desligamento` em `Aplicacao.kt`): para de aceitar pedidos
→ espera a cópia agendada em andamento → cópia final → fecha o banco → apaga o
sinal de presença. O ícone da bandeja sai no próprio clique; no gancho de
desligamento o AWT travava e a cópia final nunca acontecia.

A cópia escolhida é copiada, passa pelo `quick_check` e só então entra no
lugar; corrompida, tenta a seguinte. O banco substituído vai inteiro (com
`-wal` e `-shm`) para `dados/substituidos/<data-hora>/` e nunca é apagado.
Cópias de antes da 0.5.1 (sem `L`/`G` no nome) só são usadas quando não há
banco local, escolhendo a mais recente que tenha operação.

Tudo o que o sistema usa na rede fica **dentro de uma única subpasta**, criada
sob uma pasta já existente — nada na raiz do compartilhamento, nenhuma
permissão nova a pedir:

```
\\SERVIDOR\Logistica\AppSeparaRedespacho\
        ├── xmls/     entrada de NFs (opcional)
        ├── backup/   redespacho-2026-09-12_0742-L3f2a9c1d-G1234.db
        ├── servidor-ativo.txt   sinal de presença
        └── apk/      instalador do coletor
```

Retenção: tudo das últimas 48 h, mais a última cópia de cada dia por 30 dias.
Restauração manual continua sendo parar, trocar `dados/redespacho.db` pela
cópia e iniciar — a automática cobre a troca de máquina, não a escolha de um
ponto anterior.

O banco em si guarda tudo: os 2 anos de retenção (§ retaguarda) são só de
exibição nas telas operacionais, nada é apagado.

---

## 3. Distribuição e atualização do APK

Os coletores não têm internet, então não há loja de aplicativos. O próprio
backend serve o APK:

1. O ADMIN sobe o APK novo pela retaguarda
2. No login, o coletor compara sua versão com a publicada
3. Havendo versão nova, oferece atualizar — baixando do servidor pela mesma
   rede que já usa

Atualização **obrigatória** quando a versão do coletor for anterior à mínima
suportada pelo backend, para não deixar um aparelho esquecido operando com
regra antiga.

**APK do pacote:** o APK vem dentro do ZIP em `coletor/` e o servidor o
publica sozinho ao subir, lendo a versão de `versao.txt` — na primeira
instalação e em toda atualização cujo APK seja **mais novo** que o publicado
(comparação número a número, a mesma do coletor). Sem isso alguém teria de
abrir a aba Coletores e publicá-lo à mão antes de qualquer aparelho conseguir
baixar — passo fácil de esquecer, e que só aparece com o separador já de
coletor na mão. Nunca rebaixa: APK mais novo publicado por um admin continua no
ar.

Até a 0.7.0 o servidor só adotava o APK do pacote na primeira instalação: nas
atualizações seguintes o APK novo ficava parado em `coletor/`, o servidor
seguia entregando o da instalação (no aviso do login e no QR "Instalar") e os
coletores não saíam dela. Corrigido na 0.7.1; num servidor anterior, publique à
mão pela aba Coletores o `coletor/separacao-redespacho.apk` com a versão de
`coletor/versao.txt`.

**Instalação inicial em cada aparelho**, já que o coletor ainda não tem o app
para se atualizar:

- Bluetooth do PC para o coletor, ou
- navegador do coletor em `http://IP:8080/api/v1/app/apk`

`GET /app/apk` é **aberto de propósito** — a rede é local e fechada, e exigir
login aqui impediria justamente a instalação inicial, que acontece antes de
existir app onde digitar usuário e senha.

A versão do app tem **fonte única** em `gradle.properties` (`versaoApp`): o
módulo Android a usa como `versionName` e o ZIP a grava em `coletor/versao.txt`.
Duas cópias divergiriam, e o coletor decide atualizar comparando strings.

---

## 4. Horário

Timestamps gravados em **UTC**, exibidos em **America/Sao_Paulo**.

O relógio do coletor pode estar errado, e por isso `leitura` guarda dois
carimbos: `lido_em` (relógio do aparelho) e `registrado_em` (relógio do
servidor). **Relatórios e ordenação usam sempre o do servidor** — o do coletor
serve para diagnosticar aparelho com hora torta, não para contar produção.

Divergência acima de alguns minutos entre os dois aparece como aviso na tela de
saúde, porque costuma ser sintoma de coletor sem sincronismo de rede.

---

## 5. Impressão

ZPL por socket TCP na porta 9100. Ao conectar, o backend consulta
`getvar "head.resolution"` e gera o layout na escala do dpi retornado — 203 ou
300 — sem cadastro manual.

A porta 9100 aceita o job sem confirmar que o papel saiu. Consequências já
tratadas: sequencial de pallet é não reaproveitável por design, e existe
**reimprimir** tanto para a etiqueta de separação quanto para o cartão pallet.

---

## 6. Provisionamento de um coletor novo

1. Instalar o APK — navegador do coletor no QR "Instalar" da aba Coletores, ou
   Bluetooth
2. Abrir o app e, na configuração, **bipar o QR "Configurar"** da aba Coletores
   (preenche IP e porta e testa a conexão)
3. Login

O perfil do DataWedge é provisionado pelo próprio app por intent a cada
abertura — perfil `SeparacaoRedespacho`, Code 128 e EAN-13, saída por intent
(action `br.com.cddemo.redespacho.SCAN`, broadcast), teclado desligado.

**Gatilho mudo?** Em Configurações → DataWedge, confira se o perfil
`SeparacaoRedespacho` existe, está associado ao app e com *Intent output*
ligado. Não existindo, o provisionamento não chegou ao DataWedge — reabra o app
uma vez. O primeiro teste em campo falhou por três motivos somados, todos
corrigidos na 0.4.1: debounce consumido duas vezes por leitura, falta de
`<queries>` no manifesto e `CONFIG_MODE=OVERWRITE`, que não cria perfil novo.

---

## 7. Segurança

Rede local sem internet, então o modelo é simples e proporcional:

- senha numérica com hash e salt, nunca em texto claro
- token de sessão por dispositivo, revalidado a cada chamada
- perfis `SEPARADOR` / `SUPERVISOR` / `ADMIN` verificados **no servidor**, não
  só escondendo botão na tela
- `log_auditoria` em toda ação que altera contagem, fecha cadeado, expede,
  estorna ou baixa devolução

Usuário nunca é excluído, apenas inativado — o histórico precisa continuar
apontando para alguém.
