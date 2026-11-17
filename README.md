# CD Demo: monitoramento de centro de distribuição com IA

Sistema web que construí e mantenho em produção para a operação de um centro de distribuição
de alimentos refrigerados e congelados. Ele é usado todos os dias por analistas e coordenação.

Este repositório é a **versão pública de demonstração**. O código e os cálculos são os de produção.
Empresas, produtos, clientes, placas e pessoas foram trocados por dados fictícios.

**Demo online:** _(link da Vercel)_. Abre sem login e já vem com os dados de exemplo do dia.

---

## O problema

Num CD de frio, boa parte da gestão diária era feita em planilhas montadas à mão a partir de
relatórios do WMS e do ERP. Os principais eram:

- descobrir **quais pallets descer para o picking** antes do turno da noite, em ordem FEFO;
- encontrar **pallets fracos** que poderiam ser empilhados para liberar posições;
- montar o relatório de **devolução e produtos perto do vencimento** para direcionar vendas;
- alimentar o **e-mail diário de gestão do armazém** (ocupação, tempo de vida, endereçamento).

O sistema lê esses relatórios no formato original, aplica as regras da operação e devolve o
resultado pronto em tela, Excel ou imagem.

## Módulos nesta demo

| Módulo | O que faz |
|---|---|
| 🧭 **Expedito** (com IA) | Cruza o follow up de pedidos do ERP com o estoque do WMS e monta o **plano de abastecimento do picking**: quais pallets descer, em que ordem (FEFO), o que já está no chão e o que vai faltar. A conversa com IA (Gemini) analisa a operação da noite. |
| 🧊 **Adensamento FEFO** | Sugere empilhar pallets inteiros do mesmo código, local, temperatura e janela de produção, respeitando o limite de caixas e de peso por código e a capacidade de cada box. Sobras viram pallets mistos. |
| 📦 **Devolução & Vencimento** | Separa o que está no local de devolução e o que vence dentro de uma janela configurável, agrupado por reserva e produto. |
| 🏭 **Painel do Armazém** | Estoque por temperatura, ocupação das câmaras, tempo de vida, estoque por vencimento, % endereçado e reserva/bloqueio. As fórmulas foram reconstruídas da planilha que alimentava o e-mail de gestão. |

Os relatórios são lidos **pelo cabeçalho**, não por posição fixa. O layout do mesmo relatório
muda entre empresas e entre computadores (encoding, formato de número e de data), e o sistema
detecta isso sozinho (`app/services/paletizado_colunas.py`). Quando uma coluna obrigatória não
é encontrada, uma tela de mapeamento deixa o analista apontar a coluna certa.

## IA com mascaramento de dados

A conversa do Expedito usa a API do Gemini sobre números que o sistema **já calculou**. A IA não
decide a operação: ela explica e responde perguntas sobre o painel da noite.

Antes de qualquer chamada, `app/services/expedito_ia.py`:

1. monta o contexto por uma **lista do que é permitido** (e não do que é proibido), campo por
   campo, para que um campo novo nunca vaze por esquecimento;
2. troca nomes de pessoas, clientes e veículos por **codinomes**;
3. varre o JSON pronto procurando nome real (`conferir_vazamento`). Se encontrar, a chamada não é feita;
4. traduz a resposta de volta para os nomes reais só na tela.

Se a API cair ou a cota acabar, o Expedito responde com a análise calculada em vez de ficar
sem resposta. Na demo pública existe um limite diário de perguntas (`GEMINI_LIMITE_DIA`).

## Arquitetura

```
api/index.py            entrada da Vercel (código somente leitura, dados em /tmp)
web/server.py           aplicação Flask, sessão, controle de acesso
web/<modulo>.py         rotas de cada módulo (blueprints)
web/demo.py             modo demonstração: visitante sem login e carga dos dados de exemplo
web/templates/          telas (HTML + JS puro, sem framework)
app/database.py         SQLite (WAL), usuários, preferências, chaves criptografadas (Fernet)
app/services/           regras de negócio, uma por arquivo, sem Flask
demo/gerar_dados.py     gera os relatórios fictícios com as datas de hoje
mobile/                 app Android do coletor + servidor (Kotlin), ver mobile/README.md
```

- **Stack:** Python, Flask, SQLite, pandas, openpyxl; front-end em HTML/CSS/JS puro.
- **Produção:** VM no Google Cloud (Gunicorn + Nginx), deploy automático a partir do GitHub.
- **Segurança:** senhas com PBKDF2, CSRF em toda escrita, limite de tentativas de login, perfis
  por papel (gerente, coordenador, analista, qualidade, operador) e por módulo, log de auditoria.

### O que ficou fora da versão pública

Em produção o sistema também tem rastreamento de carretas e entregas integrado aos portais de
rastreamento de frota, monitoramento de temperatura das câmaras por sensores, alertas
automáticos por Telegram, SLAs e termos contratuais. Esses módulos dependem de credenciais e
dados reais da operação, então aparecem aqui só como arquivos de contorno
(ver o cabeçalho de `app/services/xsense_service.py`, por exemplo).

## Como a demo funciona

Na primeira requisição de cada instância, `web/demo.py` gera os arquivos de exemplo
(`demo/gerar_dados.py`) e os envia pelas **mesmas rotas de upload** que o analista usa. Não
existe atalho direto no banco para os resultados: o que aparece na tela é o cálculo de produção.

Os arquivos podem ser baixados na página `/demo` e enviados de novo em qualquer módulo.

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
DEMO_MODE=1 python web/server.py                     # Windows (PowerShell): $env:DEMO_MODE=1; python web/server.py
```

Depois, abra http://localhost:5000.

Para gerar só os arquivos de exemplo: `python demo/gerar_dados.py` (saem em `planilhas_modelo/`).

## Publicar na Vercel

1. Importe o repositório na Vercel (Framework: **Other**). O `vercel.json` já aponta tudo para `api/index.py`.
2. Configure as variáveis de ambiente:
   - `SECRET_KEY`: qualquer texto longo e aleatório. Mantém a sessão válida entre instâncias.
   - `GEMINI_API_KEY`: chave do Google AI Studio (opcional, liga a conversa com IA).
   - `GEMINI_LIMITE_DIA`: perguntas por dia na demo (padrão 40).
3. Faça o deploy.

## Autor

**Giancarlo Pinheiro**. Tenho 12 anos de logística e sou formado em Gestão Logística, com
especialização em TI aplicada à logística. Hoje curso Engenharia de Software.

Construí o sistema com desenvolvimento assistido por IA. As regras de negócio e as decisões
de cada módulo vieram da operação e estão registradas nos comentários do próprio código.
