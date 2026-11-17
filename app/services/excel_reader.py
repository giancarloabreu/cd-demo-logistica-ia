import os
import pandas as pd


# Relatorios que o usuario consegue exportar em TXT (tab-separado, latin-1) —
# hoje o Estoque Redespacho. Antes, subir o TXT estourava
# `Excel file format cannot be determined`, a excecao era engolida pelo
# chamador e o filtro de chave de NF desligava EM SILENCIO (achado 04/09/2026,
# com os arquivos reais: com Excel o filtro descarta 18 linhas, com TXT zero).
_TXT_EXT = (".txt", ".tsv", ".csv")


def _sep_de(path: str) -> str:
    return "," if os.path.splitext(path)[1].lower() == ".csv" else "	"


def _largura_maxima(path: str, sep: str) -> int:
    """Maior numero de campos entre as linhas do arquivo.

    Precisa varrer antes de ler: o relatorio comeca com linhas de titulo de 1
    campo so ("Redespacho", "Estoque em Posse: 1") e o pandas usaria essa
    primeira linha para fixar a largura, descartando todo o resto como linha
    malformada."""
    largura = 0
    with open(path, encoding="latin-1", errors="replace") as f:
        for linha in f:
            largura = max(largura, linha.count(sep) + 1)
    return largura


def _ler_txt(path: str, header, nrows):
    sep = _sep_de(path)
    return pd.read_csv(
        path, sep=sep, header=header, nrows=nrows, dtype=str,
        encoding="latin-1", engine="python", skip_blank_lines=False,
        names=range(_largura_maxima(path, sep)),
    )


def _ler_grade(path: str, header=None, nrows=None):
    """Le a grade crua de um Excel OU de um TXT/CSV, sem interpretar cabecalho."""
    if os.path.splitext(path)[1].lower() in _TXT_EXT:
        return _ler_txt(path, header, nrows)
    return pd.read_excel(
        path, header=header, nrows=nrows, dtype=str, engine=_get_excel_engine(path)
    )


def _get_excel_engine(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()

    if ext == ".xls":
        return "xlrd"

    if ext in [".xlsx", ".xlsm"]:
        return "openpyxl"

    return None


# Palavras-chave "fortes" — específicas de cabeçalhos de detalhe (não de grupo).
# Relatórios com cabeçalho em 2 linhas (grupo + detalhe) costumam ter a linha de
# detalhe com menos colunas preenchidas, mas com nomes muito específicos.
# Dar peso bem maior evita escolher a linha de grupo por ter mais colunas.
_STRONG_KEYWORDS = [
    "INSTRUÇÃO FILIAL", "INSTRUCAO FILIAL",
    "PLACA CARRETA", "PLACA VEÍCULO", "PLACA VEICULO",
    "LATITUDE", "LONGITUDE",
    "NOTA FISCAL",
]

_KEYWORDS = [
    "PLACA",
    "EMPRESA",
    "NOTA",
    "NF",
    "PEDIDO",
    "TRANSPORTADORA",
    "INSTRU",
    "LATITUDE",
    "LONGITUDE",
]


def _score_row(values: list[str]) -> int:
    count = len(values)
    hits = sum(1 for v in values for k in _KEYWORDS if k in v)
    strong_hits = sum(1 for v in values for k in _STRONG_KEYWORDS if k in v)
    return count + (hits * 5) + (strong_hits * 100)


def read_excel_flexible(path: str) -> pd.DataFrame:
    """
    Le Excel tentando identificar automaticamente o cabecalho.

    Suporta relatorios com cabecalho em DUAS linhas (linha de "grupo" +
    linha de "detalhe", ex: grupo "Veiculo Reboque" / detalhe "Placa").
    Quando isso e detectado, as colunas da linha de detalhe que pertencem
    a um grupo nomeado ganham um nome combinado "Grupo > Detalhe", para
    que colunas com o mesmo nome de detalhe (ex: duas colunas "Placa")
    possam ser diferenciadas pelo grupo (ex: "Veiculo Cavalo > Placa" e
    "Veiculo Reboque > Placa").

    Funciona com:
    - .xlsx / .xlsm usando openpyxl
    - .xls usando xlrd
    - .txt / .tsv / .csv tab-separados em latin-1
    """
    preview = _ler_grade(path, header=None, nrows=30)

    best_header_row = 0
    best_score = -1

    for idx, row in preview.iterrows():
        values = [str(x).strip().upper() for x in row.dropna().tolist()]
        score = _score_row(values)

        if score > best_score:
            best_score = score
            best_header_row = idx

    # -- Detecta cabecalho de 2 linhas (grupo + detalhe) ----------------
    # Se a linha ANTERIOR ao cabecalho escolhido tambem parece um
    # cabecalho (algumas celulas preenchidas, mas nao todas, e sem
    # parecer dado numerico/data), tratamos como linha de "grupo" e
    # combinamos com a linha de detalhe.
    header_row = best_header_row
    group_row_idx = None

    if best_header_row > 0:
        prev = preview.iloc[best_header_row - 1]
        prev_values = [str(x).strip() for x in prev.tolist()]
        non_empty_prev = [v for v in prev_values if v and v.lower() != "nan"]
        looks_like_group = (
            0 < len(non_empty_prev) < len(prev_values)
            and not any(v.replace(".", "").replace(",", "").isdigit() for v in non_empty_prev)
        )
        if looks_like_group:
            group_row_idx = best_header_row - 1

    if group_row_idx is not None:
        group_vals = [str(x).strip() for x in preview.iloc[group_row_idx].tolist()]
        detail_vals = [str(x).strip() for x in preview.iloc[header_row].tolist()]

        # Propaga o grupo para a direita ate encontrar outro grupo (merge de celulas)
        propagated_groups = []
        current_group = ""
        for v in group_vals:
            if v and v.lower() != "nan":
                current_group = v
            propagated_groups.append(current_group)

        combined_columns = []
        for grp, det in zip(propagated_groups, detail_vals):
            det_clean = det if det.lower() != "nan" else ""
            grp_clean = grp if grp.lower() != "nan" else ""
            if grp_clean and det_clean:
                combined_columns.append(f"{grp_clean} > {det_clean}")
            elif det_clean:
                combined_columns.append(det_clean)
            else:
                combined_columns.append(grp_clean)

        df = _ler_grade(path, header=header_row)
        df.columns = combined_columns[: len(df.columns)]
    else:
        df = _ler_grade(path, header=header_row)
        df.columns = [str(col).strip() for col in df.columns]

    df = df.dropna(how="all")

    return df
