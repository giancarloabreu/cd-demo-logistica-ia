"""
Última posição de cada veículo, tirada da grade de posição da Ravex
(Grid de Monitoramento) — pedido do usuário 15/11/2026: ao clicar na
entrega, mostrar ao lado um mapa com a posição do veículo.

A grade chega pela extensão do Chrome (conta principal, marcada pelo
gerente) ou pela VM, e é aplicada no cron (ravex_automation.aplicar_grade).
Aqui fica só um JSON pequeno por placa (latitude, longitude, horário da
última comunicação), separado do painel do Abastecimento: o DataFrame
daquele painel não ganha coluna nova.
"""
from __future__ import annotations

from app.utils.paths import DATA_DIR as _DATA_DIR
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.utils.text_utils import normalize_plate

ARQ = _DATA_DIR / "ravex" / "posicoes.json"
_TZ = ZoneInfo("America/Sao_Paulo")
_RE_HORA = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
_cache: dict = {"mtime": 0.0, "dados": {}}


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _hora(v) -> str:
    m = _RE_HORA.search(str(v or ""))
    return f"{m.group(1)} {m.group(2)}" if m else ""


def _temperatura(v) -> float | None:
    """UltimaTemperatura da grade: "*" ou vazio = sem leitura (sensor
    ausente ou desligado); valor fora de -60..60 °C também (leitura inválida)."""
    if v in (None, "", "*"):
        return None
    t = _num(v)
    return round(t, 1) if t is not None and -60 <= t <= 60 else None


def extrair(dados: list[dict], agora: str = "") -> dict[str, dict]:
    """Posição de cada placa da grade. Coordenada vazia, zerada ou fora do
    Brasil fica de fora. `em_real` = o horário veio do rastreador
    (UltimaComunicacao); sem ele vale o horário da grade. `temp` = última
    temperatura do baú (16/11/2026: temperatura ao lado da placa e gráfico
    da viagem)."""
    agora = agora or datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
    pos: dict[str, dict] = {}
    for item in dados or []:
        if not isinstance(item, dict):
            continue
        placa = normalize_plate(item.get("Placa") or item.get("Veiculo") or "")
        lat, lon = _num(item.get("Latitude")), _num(item.get("Longitude"))
        if not placa or lat is None or lon is None or not (-34.5 <= lat <= 5.5 and -74.5 <= lon <= -28.5):
            continue
        em = _hora(item.get("UltimaComunicacao"))
        pos[placa] = {
            "lat": round(lat, 6), "lon": round(lon, 6),
            "em": em or agora, "em_real": bool(em),
            "endereco": str(item.get("GeoPosicao") or "").strip()[:200],
            "ignicao": str(item.get("TituloIgnicao") or "").strip()[:40],
            "temp": _temperatura(item.get("UltimaTemperatura")),
        }
    return pos


def gravar(dados: list[dict]) -> int:
    """Grava a última posição de cada placa da grade (JSON do mapa) e o
    histórico das placas com viagem em aberto (PNP e pontas da viagem,
    15/11/2026 — app/services/entregas_pnp.py)."""
    agora = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
    pos = extrair(dados, agora)
    ARQ.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQ.with_suffix(".tmp")
    # em_real segue no arquivo (16/11/2026): sem o horário do rastreador, a
    # posição não acende "veículo no raio" (idade desconhecida)
    tmp.write_text(json.dumps({"grade_em": agora, "posicoes": pos}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ARQ)
    try:
        from app.database import get_db
        from app.services.entregas_pnp import registrar_historico
        with get_db() as db:
            registrar_historico(db, pos, agora)
    except Exception as e:
        print(f"[Ravex] Erro ao gravar o histórico de posições: {type(e).__name__}: {e}")
    return len(pos)


def _ler() -> dict:
    try:
        mtime = ARQ.stat().st_mtime
    except FileNotFoundError:
        return {}
    if mtime != _cache["mtime"]:
        try:
            _cache["dados"] = json.loads(ARQ.read_text(encoding="utf-8"))
            _cache["mtime"] = mtime
        except (OSError, ValueError):
            return _cache["dados"] or {}
    return _cache["dados"] or {}


def posicao(placa: str) -> dict | None:
    """Última posição da placa, com o horário da grade em que veio."""
    d = _ler()
    p = (d.get("posicoes") or {}).get(normalize_plate(placa or ""))
    return {**p, "grade_em": d.get("grade_em", "")} if p else None


def grade_em() -> str:
    """Horário da última grade aplicada (vazio = nenhuma ainda)."""
    return _ler().get("grade_em", "")
