"""
Leitura dos dois relatórios do módulo "Produtividade de Separação".

  - PAINEL  ("Painel NN.xls", um por dia) → QUEM separou e QUANTO: separador,
            conferente, pesos separado/embarcado, caixas, doca, horários de
            separação e embarque.
  - RESUMO  ("resumo painel ... .xlsx", pode vir consolidado com vários dias)
            → QUANDO a instrução foi liberada (`Data > Inclusão Painel`),
            entregas, cidade e KM. NÃO tem peso nem horário de separação.

Os dois se completam pela INSTRUÇÃO e viram um registro só no banco (pedido
do usuário, 15/09/2026). Nos arquivos reais de 01–15/09 o cruzamento deu
100%: 129 instruções nos dois lados, nenhuma órfã.

Cabeçalho em DOIS níveis (grupo > detalhe), resolvido por NOME — nunca por
posição (o layout desses relatórios muda entre empresas; ver o caso do
Produto Paletizado). A resolução é EXATA: "Separado > Volume" e "Previsto >
Volume" só diferem pelo grupo, e busca por trecho pegaria o errado.

O tipo do arquivo é descoberto pelo cabeçalho, não pelo nome — o usuário
pode subir os dois misturados numa tacada só.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

from app.services.excel_reader import read_excel_flexible

TIPO_PAINEL = "painel"
TIPO_RESUMO = "resumo"

# nome interno -> "grupo > detalhe" (sem acento; comparação normalizada)
_COLS_PAINEL = {
    "empresa":     "Empresa > Nome",
    "data_op":     "Data Operacao > Atual",
    "sep_cod":     "Separador > Codigo",
    "separador":   "Separador > Nome",
    "instrucao":   "Escala > Instrucao",
    "cliente":     "Escala > Terceiro",
    "escalas":     "Escala > Escalas",
    "sku":         "Escala > SKU",
    "prioridade":  "Escala > Prioridade",
    "tipo_op":     "Escala > Tipo de Operacao",
    "tipo_carga":  "Escala > Tipo de Carga",
    "placa":       "Veiculo > Placa",
    "frete":       "Veiculo > Tipo de Frete",
    "temperatura": "Veiculo > Temperatura",
    "prev_kg":     "Previsto > Peso Liquido",
    "sep_kg":      "Separado > Peso Liquido",
    "sep_vol":     "Separado > Volume",
    "emb_kg":      "Embarcado > Peso Liquido",
    "emb_vol":     "Embarcado > Volume",
    "doca":        "Doca > Doca",
    "conf_cod":    "Conferente > Codigo",
    "conferente":  "Conferente > Nome",
    "sep_ini":     "Separacao > Inicio",
    "sep_fim":     "Separacao > Fim",
    "emb_ini":     "Embarque > Inicio",
    "emb_fim":     "Embarque > Fim",
    # O embarque "acaba" em dois momentos diferentes, e o usuário quer os
    # dois (30/09/2026): a ÚLTIMA CAIXA bipada e o FECHAMENTO DE STATUS, que
    # vem depois. Nos arquivos de 29→30/09 a distância entre eles variou de
    # 0 a 312 min por instrução, e 118 min no fechamento do dia.
    "ult_caixa":   "Embarcado > Ultima Caixa",
    "fech_status": "Data > Fechamento Status",
    # O WMS já diz quais escalas entram na conta de produtividade; o
    # "desconsiderar" manual da tela segue valendo como exceção.
    "calcula_prod": "Escala > Calcula Produtividade",
    # Situação no instante da exportação (09/10/2026). Não tem horário: vale
    # para o momento da carga, nunca para reler um instante anterior. Foi o
    # que mostrou a noite de 09/10 inteira "Faturado" e "Em Viagem" enquanto
    # a tela dizia 86% e 14 em andamento.
    "status_op":   "Status > Operacao",
    "status_veic": "Status > Veiculo",
}

_COLS_RESUMO = {
    "empresa":     "Empresa > Sigla",
    "data_op":     "Data Operacao > Final",
    "instrucao":   "Instrucao > Codigo",
    "sku":         "Instrucao > SKU",
    "prioridade":  "Instrucao > Prioridade",
    "escalas":     "Instrucao > Escala",
    "entregas":    "Instrucao > Entregas",
    "cidade":      "Cidade > Nome",
    "uf":          "Cidade > UF",
    "km":          "Cidade > KM",
    "liberacao":   "Data > Inclusao Painel",
    "emb_ini":     "Embarque > Inicio",
    "emb_fim":     "Embarque > Fim",
    "placa":       "Veiculo > Placa",
    "temperatura": "Veiculo > Temperatura",
    "tipo_op":     "Operacao > Tipo",
    "ult_caixa":   "Embarcado > Ultima Caixa",
    "fech_status": "Data > Fechamento Status",
    # Guardadas desde já: são o caminho do pedido até o faturamento e só
    # existem no arquivo do dia. Sem gravar agora, a tela de SLAs nasceria
    # sem histórico nenhum (30/09/2026).
    "transf_pedido": "Data > Transferencia Pedido",
    "contratacao":   "Data > Contratacao",
    "faturamento":   "Data > Faturamento",
    "prev_viagem":   "Data > Previsao Inicial Viagem",
}

# nome da coluna como aparece no arquivo (os mapas acima são sem acento
# porque a comparação é normalizada)
_ROTULO = {
    "Data Operacao > Atual": "Data Operação > Atual",
    "Data Operacao > Final": "Data Operação > Final",
    "Escala > Instrucao": "Escala > Instrução",
    "Separado > Peso Liquido": "Separado > Peso Líquido",
    "Embarcado > Peso Liquido": "Embarcado > Peso Líquido",
    "Instrucao > Codigo": "Instrução > Código",
    "Data > Inclusao Painel": "Data > Inclusão Painel",
    "Embarcado > Ultima Caixa": "Embarcado > Última Caixa",
    "Separacao > Inicio": "Separação > Início",
    "Separacao > Fim": "Separação > Fim",
    "Embarque > Inicio": "Embarque > Início",
    "Data > Transferencia Pedido": "Data > Transferência Pedido",
    "Data > Contratacao": "Data > Contratação",
    "Data > Previsao Inicial Viagem": "Data > Previsão Inicial Viagem",
    "Escala > Tipo de Operacao": "Escala > Tipo de Operação",
    "Status > Operacao": "Status > Operação",
    "Status > Veiculo": "Status > Veículo",
}

# colunas sem as quais o arquivo não serve pra nada
_OBRIGATORIAS = {
    TIPO_PAINEL: ("instrucao", "data_op", "separador", "sep_kg", "emb_kg"),
    TIPO_RESUMO: ("instrucao", "data_op", "liberacao"),
}

_NUMERICAS = {"escalas", "sku", "prioridade", "prev_kg", "sep_kg", "sep_vol",
              "emb_kg", "emb_vol", "entregas", "km"}
_DATAS = {"data_op", "sep_ini", "sep_fim", "emb_ini", "emb_fim", "liberacao",
          "ult_caixa", "fech_status", "transf_pedido", "contratacao",
          "faturamento", "prev_viagem"}
_SO_DIA = {"data_op"}


def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9>]", "", t.lower())


def normalizar_nome(valor) -> str:
    """Nome de pessoa comparável. Nos arquivos reais "ISMAEL NETO" vinha com
    e sem espaço no fim e virava duas pessoas no ranking (22 → 20
    separadores depois de normalizar)."""
    if valor is None:
        return ""
    t = re.sub(r"\s+", " ", str(valor)).strip().upper()
    return "" if t in ("NAN", "NONE", "NAT") else t


def _texto(valor) -> str:
    if valor is None:
        return ""
    t = str(valor).strip()
    return "" if t.lower() in ("nan", "none", "nat") else t


def detectar_tipo(colunas) -> str | None:
    n = {_norm(c) for c in colunas}
    if "separador>nome" in n:
        return TIPO_PAINEL
    if "data>inclusaopainel" in n:
        return TIPO_RESUMO
    return None


def ler_arquivo(caminho: str) -> dict:
    """Lê um relatório (painel OU resumo) e devolve
    {"tipo", "registros": [dict por instrução], "faltando": [colunas]}.

    Levanta ValueError com mensagem clara se não reconhecer o arquivo ou se
    faltar coluna obrigatória."""
    df = read_excel_flexible(caminho)
    tipo = detectar_tipo(df.columns)
    if tipo is None:
        raise ValueError("arquivo não reconhecido. São aceitos o Painel de produtividade "
                         "(coluna “Separador > Nome”) e o Resumo do painel (coluna "
                         "“Data > Inclusão Painel”).")

    mapa = _COLS_PAINEL if tipo == TIPO_PAINEL else _COLS_RESUMO
    por_nome = {_norm(c): c for c in df.columns}
    faltando = [k for k, alvo in mapa.items() if _norm(alvo) not in por_nome]
    obrig = [k for k in _OBRIGATORIAS[tipo] if k in faltando]
    if obrig:
        nomes = [f"“{_ROTULO.get(mapa[k], mapa[k])}”" for k in obrig]
        raise ValueError(("coluna obrigatória ausente: " if len(nomes) == 1
                          else "colunas obrigatórias ausentes: ") + ", ".join(nomes) + ".")

    dados = pd.DataFrame({k: df[por_nome[_norm(a)]] if _norm(a) in por_nome
                          else pd.Series([None] * len(df), index=df.index)
                          for k, a in mapa.items()})
    dados = dados[dados["instrucao"].map(_texto) != ""]

    for c in _NUMERICAS & set(dados.columns):
        dados[c] = pd.to_numeric(dados[c], errors="coerce")
    for c in _DATAS & set(dados.columns):
        dados[c] = pd.to_datetime(dados[c], errors="coerce")

    registros = []
    for _, r in dados.iterrows():
        reg = {}
        for c in dados.columns:
            v = r[c]
            if c in _DATAS:
                if pd.isna(v):
                    reg[c] = None
                else:
                    reg[c] = v.strftime("%Y-%m-%d") if c in _SO_DIA else v.strftime("%Y-%m-%d %H:%M:%S")
            elif c in _NUMERICAS:
                reg[c] = None if pd.isna(v) else float(v)
            elif c in ("separador", "conferente"):
                reg[c] = normalizar_nome(v)
            else:
                reg[c] = _texto(v)
        reg["instrucao"] = reg["instrucao"].split(".")[0]      # "25480.0" -> "25480"
        reg["empresa"] = reg.get("empresa", "").upper()
        reg["placa"] = re.sub(r"[^A-Z0-9]", "", reg.get("placa", "").upper())
        if not reg.get("data_op"):
            continue
        registros.append(reg)

    return {"tipo": tipo, "registros": registros, "faltando": faltando}
