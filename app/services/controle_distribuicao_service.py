import os

import pandas as pd

from app.services.excel_reader import read_excel_flexible
from app.services import mapeamento_colunas
from app.utils.text_utils import normalize_key, normalize_plate


def _get(row, col):
    if col is None:
        return ""
    value = row.get(col, "")
    if value is None:
        return ""
    return str(value).strip()


def ler_controle_bruto(path: str) -> pd.DataFrame:
    """O Controle inteiro, com todas as colunas do arquivo (a aba "Outras
    operações" filtra por qualquer uma delas). Em `attrs`:
    "colunas_campo" = {campo: coluna do arquivo ou None} e
    "avisos_mapeamento" = campos lidos pela posição."""
    df = read_excel_flexible(path)
    df.columns = mapeamento_colunas.nomes_unicos(df.columns)
    avisos: list[str] = []
    df.attrs["colunas_campo"] = mapeamento_colunas.resolver_arquivo(
        "controle_distribuicao", df.columns, os.path.basename(str(path)), avisos)
    df.attrs["avisos_mapeamento"] = avisos
    return df


def carregar_controle_distribuicao(path: str) -> pd.DataFrame:
    """
    Fonte da Instrução de Entrada.

    A chave de cruzamento com o Estoque Faturado é **Pedido + Carga**, e o
    grupo certo é `Pedido Comercial` — NÃO `Pedido Principal`. Validado com os
    arquivos reais em 04/09/2026:

        Estoque Faturado  Empresa Compra > Pedido + Carga
          ↔ Controle      Pedido Comercial > Código + Carga    → 70 de 72
        Estoque Faturado  Empresa Venda > Pedido + Carga
          ↔ Controle      Pedido Principal > Código + Carga    → 21 de 22
        (cruzado, compra×principal e venda×comercial            → 0)

    Ou seja: `Pedido Principal` é o pedido do lado VENDEDOR. Cruzar por ele
    traria a instrução errada.

    Por que a placa não serve como chave: uma carreta pode entregar em vários
    CDs na mesma viagem, e o fallback por placa varre o controle inteiro sem
    filtrar CD — o card do CDA acabava recebendo também a instrução do CDB.
    Reproduzido em teste. Pedido+Carga é específico do recebimento.

    Resolução de coluna é EXATA, pelo mapeamento de colunas
    (mapeamento_colunas.py, 20/10/2026 — o que o usuário escolheu na tela
    vale primeiro). A busca por substring escolhia `Status Pedido >
    Aguardando` para o candidato "Pedido" — coluna cujo valor é
    "08-Embarque/Desembarque" — e por isso o cruzamento por pedido nunca
    funcionou; a da placa pegava "Carreta" em qualquer nome. Quando uma
    coluna essencial não é encontrada, isso vai em `df.attrs["avisos"]` para
    a tela mostrar: os dois bugs desse módulo falhavam em silêncio.
    """
    df = ler_controle_bruto(path)
    # no painel principal só importam pedido, carga e instrução (avisos
    # próprios abaixo); campos lidos pela posição também aparecem
    avisos: list[str] = [a for a in df.attrs.get("avisos_mapeamento", []) if "posição" in a]
    col = df.attrs["colunas_campo"]

    col_pedido = col["pedido"]
    col_carga = col["carga"]
    col_instrucao = col["instrucao_filial"]

    if col_pedido is None:
        avisos.append("Não achei a coluna 'Pedido Comercial > Código' no Controle de "
                      "Distribuição — a instrução vai cair no cruzamento por placa, "
                      "que pode trazer instrução de outro CD.")
    if col_carga is None:
        avisos.append("Não achei a coluna 'Pedido Comercial > Carga' no Controle de "
                      "Distribuição — o cruzamento fica só pelo pedido, sem a carga.")
    if col_instrucao is None:
        avisos.append("Não achei a coluna 'Instrução Filial' no Controle de Distribuição "
                      "— nenhum card vai mostrar instrução de entrada.")

    # A placa continua sendo lida: ela ainda é o que agrupa a carreta física no
    # card, e serve de último recurso quando o pedido não resolve.
    col_placa_carreta = col["placa_reboque"]
    col_placa = col["placa_cavalo"]

    registros = []
    for _, row in df.iterrows():
        pedido = normalize_key(_get(row, col_pedido))
        carga = normalize_key(_get(row, col_carga))
        instrucao = _get(row, col_instrucao)
        if instrucao.lower() == "nan":
            instrucao = ""

        placas = [p for p in (normalize_plate(_get(row, col_placa_carreta)),
                              normalize_plate(_get(row, col_placa))) if p and p != "NAN"]

        # Instrução SEM placa é legítima e comum — compra de produto de
        # terceiro, compra de devolução (usuário, 04/09/2026). Nos arquivos
        # reais são 35 das 72 linhas. Ela precisa entrar mesmo sem placa,
        # senão some do sistema: é justamente o cruzamento por Pedido+Carga
        # que a alcança.
        for placa in (placas or [""]):
            registros.append({
                "Pedido Compra": pedido,
                "Carga": carga,
                "Placa": placa,
                "Placa Cruzamento": placa,
                "Instrução Entrada": instrucao,
            })

    colunas = ["Pedido Compra", "Carga", "Placa", "Placa Cruzamento", "Instrução Entrada"]
    base = pd.DataFrame(registros, columns=colunas) if registros else pd.DataFrame(columns=colunas)
    base = base.drop_duplicates(subset=colunas, keep="first")
    base.attrs["avisos"] = avisos
    return base
