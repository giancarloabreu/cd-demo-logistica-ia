from __future__ import annotations

from datetime import datetime, date, time, timedelta

from app.utils.number_utils import parse_int_br
from app.services.feriados import eh_feriado, nome_do_feriado


DRIVING_SPEED_KMH = 60
WORK_START   = time(7, 0)
LUNCH_START  = time(12, 0)
LUNCH_END    = time(13, 30)
WORK_END     = time(18, 0)

_DEFAULT_SCHEDULE = {
    0: "15:50",
    1: "15:50",
    2: "15:50",
    3: "15:50",
    4: "15:50",
    5: "11:00",
    6: None,
}

_cd_schedule: dict = {}
# Cidade carnavalesca para no Carnaval; as demais param em Corpus Christi.
# Vem do cadastro do CD quando existir o campo; o padrão (False) vale para os
# CDs de hoje, todos em Goiás.
_cd_carnavalesca: bool = False


def _load_schedule(cd_config: dict | None = None):
    global _cd_schedule, _cd_carnavalesca
    if cd_config and "horarios_corte" in cd_config:
        _cd_schedule = cd_config["horarios_corte"]
    else:
        _cd_schedule = {}
    _cd_carnavalesca = bool((cd_config or {}).get("carnavalesca"))


def _get_cutoff_str(weekday: int) -> str | None:
    if _cd_schedule:
        # Tenta chave string primeiro (como vem do JSON), depois inteiro
        val = _cd_schedule.get(str(weekday), _cd_schedule.get(weekday, _DEFAULT_SCHEDULE.get(weekday)))
    else:
        val = _DEFAULT_SCHEDULE.get(weekday)
    return val


def is_saturday(d: date) -> bool:
    return d.weekday() == 5

def is_sunday(d: date) -> bool:
    return d.weekday() == 6

def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def cd_is_open(d: date) -> bool:
    # Feriado nacional fecha o CD igual domingo. Entra aqui de propósito: é o
    # portão único que `next_discharge_day` e `add_driving_minutes` já usam,
    # então a previsão de descarga pula o feriado sozinha.
    if eh_feriado(d, _cd_carnavalesca):
        return False
    return _get_cutoff_str(d.weekday()) is not None


def motivo_fechado(d: date) -> str:
    """Por que o CD não abre nesse dia — para a tela poder explicar em vez de
    só empurrar a data."""
    feriado = nome_do_feriado(d, _cd_carnavalesca)
    if feriado:
        return feriado
    if is_sunday(d):
        return "domingo"
    if _get_cutoff_str(d.weekday()) is None:
        return "CD fechado"
    return ""


def cd_cutoff(d: date) -> time | None:
    s = _get_cutoff_str(d.weekday())
    if not s:
        return None
    h, m = s.split(":")
    return time(int(h), int(m))


def next_discharge_day(d: date) -> date:
    d = d + timedelta(days=1)
    while not cd_is_open(d):
        d += timedelta(days=1)
    return d


def normalize_start(dt: datetime) -> datetime:
    t = dt.time()
    d = dt.date()

    if t < WORK_START:
        return datetime.combine(d, WORK_START)
    if LUNCH_START <= t < LUNCH_END:
        return datetime.combine(d, LUNCH_END)
    if t >= WORK_END:
        next_d = d + timedelta(days=1)
        return datetime.combine(next_d, WORK_START)
    return dt


def add_driving_minutes(start: datetime, minutes: int) -> datetime:
    remaining = minutes
    current = normalize_start(start)

    while remaining > 0:
        t = current.time()
        d = current.date()

        if t >= WORK_END or t < WORK_START:
            current = datetime.combine(d + timedelta(days=1), WORK_START)
            continue

        if t < LUNCH_START:
            mins_to_lunch = int((datetime.combine(d, LUNCH_START) - current).total_seconds() / 60)
            if remaining <= mins_to_lunch:
                current += timedelta(minutes=remaining)
                remaining = 0
            else:
                remaining -= mins_to_lunch
                current = datetime.combine(d, LUNCH_END)
        elif LUNCH_START <= t < LUNCH_END:
            current = datetime.combine(d, LUNCH_END)
        else:
            mins_to_end = int((datetime.combine(d, WORK_END) - current).total_seconds() / 60)
            if remaining <= mins_to_end:
                current += timedelta(minutes=remaining)
                remaining = 0
            else:
                remaining -= mins_to_end
                current = datetime.combine(d + timedelta(days=1), WORK_START)

    return current


def _rotulo_feriado(chegada: date, descarga: date | None = None) -> str:
    """"07/09 Independência" — o feriado que ATRAVESSA o caminho da carreta.

    Olha o período INTEIRO entre a chegada e a descarga, não só o dia da
    chegada. Os dois casos que importam (pergunta do usuário, 04/09/2026):

      * chega NO dia do feriado                     -> chegada é feriado
      * chega na VÉSPERA fora do horário de corte   -> a descarga pula o
        feriado e cai depois dele, então o feriado está no meio do caminho
        mesmo sem a chegada cair nele

    Antes só o primeiro caso aparecia, e o segundo — que é o mais comum, já
    que a carreta que chega tarde é justamente a que espera — ficava mudo.

    Vai como campo próprio (e não só embutido no texto da estimativa) porque
    o card mostra numa linha separada, logo abaixo da estimativa."""
    fim = descarga or chegada
    if fim < chegada:
        chegada, fim = fim, chegada
    achados, d = [], chegada
    while d <= fim:
        nome = nome_do_feriado(d, _cd_carnavalesca)
        if nome:
            achados.append(f"{d.strftime('%d/%m')} {nome}")
        d += timedelta(days=1)
    return " · ".join(achados)


def _sufixo_descarga(chegada: date, descarga: date) -> str:
    """" / Descarga ..." dizendo o MOTIVO quando a chegada cai em feriado.

    Sem isso a data pula sem explicação e parece erro do sistema — quem olha o
    card não tem como saber que 04/06 era Corpus Christi."""
    motivo = nome_do_feriado(chegada, _cd_carnavalesca)
    if motivo:
        return f" / {motivo} — Descarga {descarga.strftime('%d/%m')}"
    return f" / Descarga Próx. dia útil {descarga.strftime('%d/%m')}"


def estimar_chegada(km_str: str, now: datetime | None = None) -> dict | None:
    km = parse_int_br(km_str)
    if km is None:
        return None
    if now is None:
        import zoneinfo as _zi
        now = datetime.now(_zi.ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)

    if km == 0:
        today = now.date()
        cutoff_hoje = cd_cutoff(today)
        dentro_corte = cd_is_open(today) and cutoff_hoje and now.time() <= cutoff_hoje
        return {
            "tempo": "0 min",
            "estimativa": "Veículo no CD",
            "status": "no_cd_no_horario" if dentro_corte else "no_cd",
        }

    minutes    = int(km / DRIVING_SPEED_KMH * 60)
    arrival    = add_driving_minutes(now, minutes)
    hours      = minutes // 60
    mins       = minutes % 60
    tempo      = f"{hours}h {mins:02d}min" if hours > 0 else f"{mins} min"

    today        = datetime.now().date()
    arrival_date = arrival.date()
    cutoff_hoje  = cd_cutoff(today)

    if arrival_date == today:
        if cd_is_open(today) and cutoff_hoje and arrival.time() <= cutoff_hoje:
            return {
                "tempo": tempo,
                "estimativa": f"Chega hoje {arrival.strftime('%H:%M')}",
                "status": "chega_no_horario",
                "feriado": "",
            }
        descarga = next_discharge_day(arrival_date)
        return {
            "tempo": tempo,
            "estimativa": (f"Chega hoje {arrival.strftime('%H:%M')}"
                           + _sufixo_descarga(arrival_date, descarga)),
            "status": "chega_apos_corte",
            "feriado": _rotulo_feriado(arrival_date, descarga),
        }

    cutoff_arr = cd_cutoff(arrival_date)
    dias_diferenca = (arrival_date - today).days

    if cd_is_open(arrival_date) and cutoff_arr and arrival.time() <= cutoff_arr:
        if dias_diferenca <= 1:
            # Chega amanhã, dentro do horário de corte — descarrega no
            # próprio dia da chegada (não é "2+ dias" de atraso real).
            return {
                "tempo": tempo,
                "estimativa": f"Chega {arrival.strftime('%d/%m %H:%M')} / Descarga {arrival.strftime('%d/%m')}",
                "status": "chega_apos_corte",
            }
        # Chega em 2+ dias — mesmo dentro do horário de corte daquele dia,
        # ainda é uma chegada distante ("2+ dias").
        return {
            "tempo": tempo,
            "estimativa": f"Chega {arrival.strftime('%d/%m %H:%M')}",
            "status": "chegada_futura",
        }

    descarga = next_discharge_day(arrival_date)
    return {
        "tempo": tempo,
        "estimativa": (f"Chega {arrival.strftime('%d/%m %H:%M')}"
                       + _sufixo_descarga(arrival_date, descarga)),
        "status": "chega_apos_corte" if dias_diferenca <= 1 else "chegada_futura",
        "feriado": _rotulo_feriado(arrival_date, descarga),
    }


def aplicar_estimativas(df):
    import pandas as pd
    import zoneinfo as _zi
    now = datetime.now(_zi.ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)

    resultados = []
    for _, row in df.iterrows():
        entregas = str(row.get("Entregas Frente", "")).strip()
        if entregas == "Sim":
            resultados.append({
                "Tempo Estimado":    "",
                "Estimativa Chegada":"Entregas na frente",
                "Status Estimativa": "entregas_frente",
                "Feriado":           "",
            })
            continue

        km_str = str(row.get("KM Restante", "")).strip()
        est = estimar_chegada(km_str, now)
        if est:
            resultados.append({
                "Tempo Estimado":    est["tempo"],
                "Estimativa Chegada":est["estimativa"],
                "Status Estimativa": est["status"],
                "Feriado":           est.get("feriado", ""),
            })
        else:
            resultados.append({
                "Tempo Estimado":    "",
                "Estimativa Chegada":"",
                "Status Estimativa": "sem_previsao",
                "Feriado":           "",
            })

    if resultados:
        est_df = pd.DataFrame(resultados, index=df.index)
        df = df.copy()
        for col in ["Tempo Estimado", "Estimativa Chegada", "Status Estimativa", "Feriado"]:
            df[col] = est_df[col]
    return df
