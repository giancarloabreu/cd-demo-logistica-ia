import os

import pandas as pd

from app.services.excel_reader import read_excel_flexible
from app.services import mapeamento_colunas
from app.utils.text_utils import normalize_key, normalize_plate, format_date_br


def _safe_series(df, idx_or_col, default=""):
    if idx_or_col is None:
        return pd.Series([default] * len(df), index=df.index)

    try:
        if isinstance(idx_or_col, int):
            if idx_or_col < len(df.columns):
                return df.iloc[:, idx_or_col].fillna("").astype(str)
            return pd.Series([default] * len(df), index=df.index)

        return df[idx_or_col].fillna("").astype(str)
    except Exception:
        return pd.Series([default] * len(df), index=df.index)


def _safe_raw_series(df, idx_or_col, default=""):
    if idx_or_col is None:
        return pd.Series([default] * len(df), index=df.index)

    try:
        if isinstance(idx_or_col, int):
            if idx_or_col < len(df.columns):
                return df.iloc[:, idx_or_col]
            return pd.Series([default] * len(df), index=df.index)

        return df[idx_or_col]
    except Exception:
        return pd.Series([default] * len(df), index=df.index)


def _numero_br(v: str) -> str:
    """'1.234,56' -> '1234.56'; '29,8' -> '29.8'; '29.8' fica."""
    t = str(v or "").strip()
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    return t


def carregar_estoque_faturado(path: str, avisos: list | None = None) -> pd.DataFrame:
    """
    Fonte principal dos dados da carga.

    As colunas são achadas pelo NOME do cabeçalho (mapeamento_colunas.py,
    20/10/2026). Antes eram lidas pela posição (A = empresa, N = NF, P =
    placa, R = local de estoque…) quando o arquivo tinha 29 colunas ou mais,
    e por pedaço do nome nos demais — uma coluna a mais no relatório
    deslocava tudo sem aviso. A posição antiga ficou como último recurso e,
    quando usada, gera aviso. Nota fiscal e empresa compradora são
    obrigatórias: sem elas, ColunasFaltando (a tela abre o mapeamento).

    A carga da empresa compradora, junto com o pedido de compra, é a chave de
    cruzamento com o Controle de Distribuição.
    """
    df = read_excel_flexible(path)
    df.columns = mapeamento_colunas.nomes_unicos(df.columns)
    col = mapeamento_colunas.resolver_arquivo("estoque_faturado", df.columns, os.path.basename(str(path)), avisos)
    col_empresa_exp = col["empresa"]
    col_data_fat = col["data_fat"]
    col_nf = col["nf"]
    col_transportadora = col["transportadora"]
    col_placa = col["placa"]
    col_empresa_compra = col["empresa_compra"]
    col_local_estoque = col["local_estoque"]
    col_pedido_compra = col["pedido_compra"]
    col_carga_compra = col["carga"]
    col_cod_produto = col["cod_produto"]
    col_temperatura = col["temperatura"]
    col_linha = col["linha"]
    col_produto = col["produto"]
    col_unidade = col["unidade"]
    col_quantidade = col["quantidade"]
    col_volume = col["volume"]
    col_preco = col.get("preco")

    base = pd.DataFrame()
    base["Empresa"] = _safe_series(df, col_empresa_exp)
    base["Data Faturamento"] = _safe_raw_series(df, col_data_fat).map(format_date_br)
    base["NF"] = _safe_series(df, col_nf).map(normalize_key)
    base["Transportadora"] = _safe_series(df, col_transportadora)
    base["Placa"] = _safe_series(df, col_placa).map(normalize_plate)
    base["Empresa Compra"] = _safe_series(df, col_empresa_compra)
    base["Local Estoque"] = _safe_series(df, col_local_estoque)
    base["Pedido Compra"] = _safe_series(df, col_pedido_compra).map(normalize_key)
    base["Carga"] = _safe_series(df, col_carga_compra).map(normalize_key)
    base["Cód Produto"] = _safe_series(df, col_cod_produto).map(normalize_key)
    base["Temperatura"] = _safe_series(df, col_temperatura)
    base["Linha"] = _safe_series(df, col_linha)
    base["Produto"] = _safe_series(df, col_produto)
    base["Unidade"] = _safe_series(df, col_unidade)
    base["Quantidade"] = pd.to_numeric(_safe_series(df, col_quantidade).str.replace(",", ".", regex=False), errors="coerce").fillna(0)
    base["Volume"] = pd.to_numeric(_safe_series(df, col_volume).str.replace(",", ".", regex=False), errors="coerce").fillna(0)
    # R$/kg da NF; vazio quando o relatório não tem a coluna (NaN, não 0:
    # preço zero sairia no e-mail como valor)
    base["Preço"] = (pd.to_numeric(_safe_series(df, col_preco).map(_numero_br), errors="coerce")
                     if col_preco else pd.Series([float("nan")] * len(df), index=df.index))

    # Mantém somente linhas com NF faturada (embarcadas)
    # Exclui vazio, "nan", "0" e qualquer valor que não seja numérico/alfanumérico real
    nf_valida = base["NF"].astype(str).str.strip()
    base = base[
        (nf_valida.str.len() > 0) &
        (~nf_valida.str.lower().isin(["nan", "none", "0", ""])) &
        (nf_valida != "0.0")
    ].copy()

    return base
