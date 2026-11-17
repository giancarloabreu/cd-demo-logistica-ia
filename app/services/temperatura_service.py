"""
Histórico de temperatura por carreta (MEL-4).

A cada ciclo da OpenTech (scripts/atualizar_opentech.py — ver BUG-5),
`registrar_temperaturas` grava a leitura atual de cada carreta. O painel
consulta a série de um placa específico para desenhar o gráfico e detectar
abertura de porta (variação brusca entre duas leituras próximas no tempo).

Timestamps são gravados em BRT (não UTC) como texto "YYYY-MM-DD HH:MM:SS" —
mesmo padrão usado em `_now_brt_str()` (opentech_automation.py) — pra bater
com o horário exibido no resto do painel.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from app.database import get_db
from app.utils.text_utils import normalize_plate

_BRT = ZoneInfo("America/Sao_Paulo")
_FMT = "%Y-%m-%d %H:%M:%S"


def _agora_brt_str() -> str:
    return datetime.now(_BRT).strftime(_FMT)


def registrar_temperaturas(df_monitoramento: pd.DataFrame) -> int:
    """Grava a temperatura atual de cada carreta presente no painel OpenTech
    recém-carregado. Só considera registros do tipo 'Carreta' (evita gravar a
    mesma leitura duas vezes, já que o painel também cruza por Placa Veículo).
    Retorna quantos registros foram gravados."""
    if df_monitoramento is None or df_monitoramento.empty:
        return 0
    df = df_monitoramento
    if "Tipo Cruzamento" in df.columns:
        df = df[df["Tipo Cruzamento"] == "Carreta"]
    if "Placa Carreta" not in df.columns or "Temperatura Atual" not in df.columns:
        return 0

    agora = _agora_brt_str()
    registros = []
    for _, row in df.iterrows():
        placa = normalize_plate(row.get("Placa Carreta", ""))
        temp = row.get("Temperatura Atual")
        if not placa or temp is None:
            continue
        try:
            temp_f = float(temp)
        except (TypeError, ValueError):
            continue
        if pd.isna(temp_f):
            continue
        registros.append((placa, temp_f, agora))

    if not registros:
        return 0
    with get_db() as db:
        db.executemany(
            "INSERT INTO historico_temperatura (placa, temperatura, timestamp) VALUES (?,?,?)",
            registros,
        )
    return len(registros)


def obter_serie_temperatura(placa: str, horas: int = 48) -> list[dict]:
    """Retorna a série de leituras de uma carreta nas últimas `horas`, mais
    antiga primeiro."""
    placa = normalize_plate(placa)
    desde = (datetime.now(_BRT) - timedelta(hours=horas)).strftime(_FMT)
    with get_db() as db:
        rows = db.execute(
            """SELECT temperatura, timestamp FROM historico_temperatura
               WHERE placa=? AND timestamp>=? ORDER BY timestamp ASC""",
            (placa, desde),
        ).fetchall()
    return [{"temperatura": r["temperatura"], "timestamp": r["timestamp"]} for r in rows]


def detectar_variacao_brusca(serie: list[dict], limite_graus: float = 6.0,
                              janela_min: float = 20.0) -> bool:
    """True se alguma dupla de leituras consecutivas variou >= limite_graus
    dentro de janela_min minutos — indício de porta aberta."""
    for i in range(1, len(serie)):
        try:
            t0 = datetime.strptime(serie[i - 1]["timestamp"], _FMT)
            t1 = datetime.strptime(serie[i]["timestamp"], _FMT)
        except ValueError:
            continue
        delta_min = (t1 - t0).total_seconds() / 60
        delta_temp = abs(serie[i]["temperatura"] - serie[i - 1]["temperatura"])
        if delta_min <= janela_min and delta_temp >= limite_graus:
            return True
    return False
