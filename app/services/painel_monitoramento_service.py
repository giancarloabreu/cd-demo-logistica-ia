import os

import pandas as pd

from app.services.excel_reader import read_excel_flexible
from app.services import mapeamento_colunas
from app.utils.text_utils import normalize_plate


def _clean_coord(value):
    if value is None:
        return ""

    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return ""

    # O relatório da OpenTech já costuma vir como -16.69847.
    # Se vier com vírgula decimal, converte para ponto.
    text = text.replace(",", ".")

    try:
        number = float(text)
    except Exception:
        return ""

    return str(number)


def _placa(value) -> str:
    """Célula vazia vem do Excel como NaN, e normalize_plate(NaN) dava "NAN":
    no relatório de 18/10/2026 eram 2.198 linhas com a carreta "NAN" (cavalo
    sem carreta), todas cruzando pela mesma placa falsa."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    p = normalize_plate(value)
    return "" if p in ("NAN", "NONE") else p


def _clean_ae(value) -> str:
    """AE como texto de dígitos: o Excel pode trazer 37697730.0."""
    s = str(value or "").strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _clean_temp(value):
    """
    A coluna 'Última temperatura recebida' vem multiplicada por 1000
    (ex: 16000 = 16.0 C, -19000 = -19.0 C). Retorna float ou None.
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if text == "" or text.lower() == "nan":
        return None
    try:
        return float(text) / 1000.0
    except Exception:
        return None


def _get_series(df, col, default=""):
    if col is None:
        return pd.Series([default] * len(df), index=df.index)
    return df[col].fillna("").astype(str)


def carregar_painel_monitoramento(path: str) -> pd.DataFrame:
    """
    Fonte de rastreamento.

    Regra de cruzamento:
    - Cria um índice por Placa carreta
    - Cria também um índice por Placa veículo
    Assim, se a placa do Estoque Faturado não bater na carreta, ainda pode bater no veículo.
    """
    df = read_excel_flexible(path)
    # Colunas pelo NOME exato (mapeamento_colunas.py, 20/10/2026) — por pedaço
    # do nome, "Km Restante" casaria "Km restante reta (1.18)" e "Carreta",
    # "Placa carreta 2". Sem a placa da carreta segue pela do cavalo, como o
    # antigo; sem as duas, ColunasFaltando (o ciclo automático mantém o painel
    # anterior em vez de zerar o rastreio).
    df.columns = mapeamento_colunas.nomes_unicos(df.columns)
    col = mapeamento_colunas.resolver_arquivo("opentech", df.columns, os.path.basename(str(path)))
    if not col["placa_carreta"] and not col["placa_veiculo"]:
        defi = mapeamento_colunas.TIPOS["opentech"]["campos"]
        raise mapeamento_colunas.ColunasFaltando(
            "opentech", [{"campo": k, "rotulo": defi[k][0]} for k in ("placa_carreta", "placa_veiculo")],
            list(df.columns))
    col_placa_carreta = col["placa_carreta"]
    col_placa_veiculo = col["placa_veiculo"]
    col_km = col["km"]
    col_posicao = col["posicao"]
    col_ref_cidade = col["ref_cidade"]
    col_lat = col["latitude"]
    col_lon = col["longitude"]
    col_temp = col["temperatura"]
    col_ignicao = col["ignicao"]
    col_sit_veiculo = col["sit_veiculo"]
    # Autorização de embarque: uma por carregamento, nunca se repete — é o ID
    # da viagem (ver app/services/carretas_viagem.py).
    col_ae = col["ae"]
    col_sit_viagem = col["sit_viagem"]

    registros = []

    for _, row in df.iterrows():
        placa_carreta = _placa(row.get(col_placa_carreta, "")) if col_placa_carreta else ""
        placa_veiculo = _placa(row.get(col_placa_veiculo, "")) if col_placa_veiculo else ""

        lat = _clean_coord(row.get(col_lat, "")) if col_lat else ""
        lon = _clean_coord(row.get(col_lon, "")) if col_lon else ""

        ultima_posicao = str(row.get(col_posicao, "") or "").strip() if col_posicao else ""
        ref_cidade = str(row.get(col_ref_cidade, "") or "").strip() if col_ref_cidade else ""
        km_opentech = str(row.get(col_km, "") or "").strip() if col_km else ""
        temperatura_atual = _clean_temp(row.get(col_temp, "")) if col_temp else None
        ignicao = str(row.get(col_ignicao, "") or "").strip() if col_ignicao else ""
        situacao_veiculo = str(row.get(col_sit_veiculo, "") or "").strip() if col_sit_veiculo else ""
        ae = _clean_ae(row.get(col_ae, "")) if col_ae else ""
        situacao_viagem = str(row.get(col_sit_viagem, "") or "").strip() if col_sit_viagem else ""
        if situacao_viagem.lower() == "nan":
            situacao_viagem = ""

        cidade_atual = ref_cidade or ultima_posicao

        base = {
            "Placa Carreta": placa_carreta,
            "Placa Veículo": placa_veiculo,
            "KM OpenTech Interno": km_opentech,
            "Última Posição": ultima_posicao,
            "Cidade Atual": cidade_atual,
            "Latitude": lat,
            "Longitude": lon,
            "Temperatura Atual": temperatura_atual,
            "Ignição": ignicao,
            "Situação Veículo": situacao_veiculo,
            "Fonte Rastreio": "OpenTech",
            "AE": ae,
            "Situação Viagem": situacao_viagem,
        }

        if placa_carreta:
            r1 = base.copy()
            r1["Placa Cruzamento"] = placa_carreta
            r1["Tipo Cruzamento"] = "Carreta"
            registros.append(r1)

        if placa_veiculo and placa_veiculo != placa_carreta:
            r2 = base.copy()
            r2["Placa Cruzamento"] = placa_veiculo
            r2["Tipo Cruzamento"] = "Veículo"
            registros.append(r2)

    if not registros:
        return pd.DataFrame(columns=[
            "Placa Carreta", "Placa Veículo", "Placa Cruzamento", "Tipo Cruzamento",
            "KM OpenTech Interno", "Última Posição", "Cidade Atual", "Latitude", "Longitude",
            "Temperatura Atual", "Ignição", "Situação Veículo", "Fonte Rastreio",
            "AE", "Situação Viagem",
        ])

    base = pd.DataFrame(registros)
    # A mesma placa pode vir em mais de uma linha: carreta numa, cavalo noutra,
    # ou uma viagem NOVA antiga pendurada ao lado da que está EM ANDAMENTO
    # (visto no relatório real). Fica a viagem em andamento; no empate, a AE
    # mais nova (a numeração é sequencial). Antes ficava a primeira linha.
    base["_em_curso"] = (base["Situação Viagem"].str.upper() == "EM ANDAMENTO").astype(int)
    base["_tem_ae"] = (base["AE"] != "").astype(int)
    base["_ae_n"] = pd.to_numeric(base["AE"], errors="coerce").fillna(0)
    base = base.sort_values(["_em_curso", "_tem_ae", "_ae_n"], ascending=False, kind="stable")
    base = base.drop_duplicates(subset=["Placa Cruzamento"], keep="first")
    return base.drop(columns=["_em_curso", "_tem_ae", "_ae_n"]).sort_index()
