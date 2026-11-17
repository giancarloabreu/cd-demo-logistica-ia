"""
Resumo periódico das câmaras do Sensores (09/10/2026).

Pedido do usuário: a cada 8 horas, um texto com a situação de TODAS as
câmaras — as que estão fora da faixa, as que saíram e recuperaram (por
quanto tempo), as que ficaram sem sinal e as que passaram o período sem
alerta. O alerta avisa o incidente; o resumo confirma que o silêncio é
normalidade, e não falha de envio (motivo real: a CG8 passou dias sem
alertar com o push marcado ✅).

Calcula pelas LEITURAS, não pelos eventos do motor: o evento depende do
cooldown e da regra de sustentação, e o resumo precisa dizer o que a câmara
de fato registrou — inclusive a saída curta que não chegou a virar alerta.

Este módulo não conhece Flask nem Telegram: recebe câmaras e leituras e
devolve o texto (HTML simples, o mesmo parse_mode dos alertas).
"""
from __future__ import annotations

import datetime as _dt
import html

# Horários de fechamento do resumo: um a cada 8 horas. Cada um cobre as 8
# horas anteriores — o das 06h fecha a madrugada, o das 14h a manhã, o das
# 22h a tarde.
HORARIOS = (6, 14, 22)
JANELA_H = 8

# Uma leitura "vale" até a próxima, com este teto. A cadência real do sensor
# fica perto de 8 min (BACKLOG, uptime); acima disto o buraco é falta de
# sinal, não tempo fora da faixa.
VALIDADE_LEITURA_MIN = 30

_FMT = "%Y-%m-%d %H:%M:%S"


def _ts(s) -> _dt.datetime | None:
    try:
        return _dt.datetime.strptime(str(s)[:19], _FMT)
    except (TypeError, ValueError):
        return None


def _num(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def _dur(minutos: float) -> str:
    m = int(round(minutos))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}" if m else f"{h}h"


def _hora(d: _dt.datetime, ref: _dt.datetime) -> str:
    return d.strftime("%H:%M") if d.date() == ref.date() else d.strftime("%d/%m %H:%M")


def slot_atual(agora: _dt.datetime) -> _dt.datetime:
    """O fechamento mais recente que já passou (06h, 14h ou 22h)."""
    hoje = agora.replace(minute=0, second=0, microsecond=0)
    for h in sorted(HORARIOS, reverse=True):
        candidato = hoje.replace(hour=h)
        if candidato <= agora:
            return candidato
    return (hoje - _dt.timedelta(days=1)).replace(hour=max(HORARIOS))


def episodios(leituras: list[tuple[_dt.datetime, float]], minimo, maximo,
              fim: _dt.datetime) -> list[dict]:
    """Trechos contínuos fora da faixa: [{ini, fim, minutos, pico, recuperou}].

    `leituras` em ordem. Cada leitura vale até a próxima (teto de
    VALIDADE_LEITURA_MIN); o trecho termina na primeira leitura de volta à
    faixa — é a hora da recuperação.
    """
    if minimo is None or maximo is None:
        return []
    saida, atual = [], None
    for i, (t, v) in enumerate(leituras):
        prox = leituras[i + 1][0] if i + 1 < len(leituras) else fim
        vale_ate = min(prox, t + _dt.timedelta(minutes=VALIDADE_LEITURA_MIN), fim)
        fora = v < minimo or v > maximo
        if fora:
            desvio = v - maximo if v > maximo else minimo - v
            if atual is None:
                atual = {"ini": t, "fim": vale_ate, "minutos": 0.0, "pico": v,
                         "desvio": desvio, "recuperou": None}
            if desvio > atual["desvio"]:
                atual["pico"], atual["desvio"] = v, desvio
            atual["minutos"] += max((vale_ate - t).total_seconds() / 60, 0.0)
            atual["fim"] = vale_ate
        elif atual is not None:
            atual["recuperou"] = t
            saida.append(atual)
            atual = None
    if atual is not None:
        saida.append(atual)
    return saida


def situacao_camara(cam: dict, leituras: list[tuple[_dt.datetime, float]],
                    ini: _dt.datetime, fim: _dt.datetime,
                    sinal_limite_min: int = 50) -> dict:
    """Situação de UMA câmara no período [ini, fim).

    `leituras`: (ts, valor) de temperatura em ordem, podendo começar antes de
    `ini` — é o que permite dizer "fora há 5h" quando a saída começou no
    período anterior.
    """
    minimo, maximo = cam.get("temp_min"), cam.get("temp_max")
    ate_fim = [(t, v) for t, v in leituras if t < fim]
    no_periodo = [(t, v) for t, v in ate_fim if t >= ini]
    ultima = ate_fim[-1] if ate_fim else None
    eps = episodios(ate_fim, minimo, maximo, fim)
    # o que encostou no período: começou antes e terminou depois de `ini`
    eps_periodo = [e for e in eps if e["fim"] > ini]
    sem_sinal = ultima is None or (fim - ultima[0]).total_seconds() / 60 > sinal_limite_min
    fora_agora = bool(eps and eps[-1]["recuperou"] is None and not sem_sinal)
    minutos_fora = sum(
        max((min(e["fim"], fim) - max(e["ini"], ini)).total_seconds() / 60, 0.0)
        for e in eps_periodo)
    valores = [v for _, v in no_periodo]
    return {
        "nome": cam.get("nome_exibicao") or cam.get("rotulo") or cam.get("nome") or cam.get("id"),
        "faixa": (minimo, maximo),
        "ultima": ultima,
        "sem_sinal": sem_sinal,
        "fora_agora": fora_agora,
        "atual": eps[-1] if fora_agora else None,
        "recuperados": [e for e in eps_periodo if e["recuperou"] is not None],
        "minutos_fora": minutos_fora,
        "min": min(valores) if valores else None,
        "max": max(valores) if valores else None,
        "leituras": len(no_periodo),
        "alertas_ativos": bool(cam.get("alertas_ativos", 1)),
    }


def texto_do_local(local: str, situacoes: list[dict], ini: _dt.datetime,
                   fim: _dt.datetime) -> str:
    """Mensagem de UM local — uma por local, para o gerente que recebe todos
    não esbarrar no limite de 4.096 caracteres do Telegram."""
    e = html.escape
    linhas = [f"📋 <b>Resumo Sensores · {e(local or 'Sem local')}</b>",
              f"Período: {_hora(ini, fim)} às {fim.strftime('%H:%M')} de {fim.strftime('%d/%m')}"]

    def faixa(s):
        a, b = s["faixa"]
        return f"faixa {_num(a)} a {_num(b)} °C" if a is not None and b is not None else "sem faixa"

    fora = [s for s in situacoes if s["fora_agora"]]
    recuperou = [s for s in situacoes if not s["fora_agora"] and s["recuperados"]
                 and not s["sem_sinal"]]
    sinal = [s for s in situacoes if s["sem_sinal"]]
    ok = [s for s in situacoes if s not in fora and s not in recuperou and s not in sinal]

    if fora:
        linhas += ["", "🔴 <b>Fora da faixa agora</b>"]
        for s in fora:
            a = s["atual"]
            linhas.append(f"• {e(s['nome'])}: {_num(s['ultima'][1])} °C ({faixa(s)}), "
                          f"fora há {_dur((fim - a['ini']).total_seconds() / 60)}, "
                          f"pico {_num(a['pico'])} °C")
    if recuperou:
        linhas += ["", "🟡 <b>Saíram da faixa e recuperaram</b>"]
        for s in recuperou:
            eps = s["recuperados"]
            pico = max(eps, key=lambda x: x["desvio"])["pico"]
            vezes = f" em {len(eps)} saídas" if len(eps) > 1 else ""
            ultima_rec = max(x["recuperou"] for x in eps)
            linhas.append(f"• {e(s['nome'])}: {_dur(s['minutos_fora'])} fora{vezes}, "
                          f"pico {_num(pico)} °C · recuperou às {_hora(ultima_rec, fim)}")
    if sinal:
        linhas += ["", "📡 <b>Sem sinal</b>"]
        for s in sinal:
            quando = (f"última leitura às {_hora(s['ultima'][0], fim)}" if s["ultima"]
                      else "nenhuma leitura registrada")
            linhas.append(f"• {e(s['nome'])}: {quando}")
    if ok:
        titulo = ("✅ <b>Todas as câmaras dentro da faixa no período</b>"
                  if not (fora or recuperou or sinal) else "✅ <b>Sem alertas no período</b>")
        linhas += ["", titulo]
        for s in ok:
            amplitude = (f"{_num(s['min'])} a {_num(s['max'])} °C"
                         if s["min"] is not None else "sem leitura no período")
            linhas.append(f"• {e(s['nome'])}: {amplitude}")
    if not situacoes:
        linhas += ["", "Nenhuma câmara ativa neste local."]
    return "\n".join(linhas)
