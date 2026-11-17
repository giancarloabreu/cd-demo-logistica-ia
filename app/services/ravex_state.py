"""
Estado global compartilhado do DataFrame de rastreio obtido automaticamente
da Ravex — segundo sistema de monitoramento, cobre um grupo de placas que
não aparece na OpenTech (ver MEL-14 no BACKLOG.md). Mesmo padrão exato de
app/services/opentech_state.py: a automação (Playwright) roda num processo
separado disparado por cron, persiste o resultado em ARQUIVO_ESTADO, e o
processo do Flask/Gunicorn só lê esse arquivo periodicamente.
"""
from __future__ import annotations
from app.utils.paths import DATA_DIR as _DATA_DIR
import os
import threading
from pathlib import Path
import pandas as pd

_lock = threading.Lock()
_df_ravex: pd.DataFrame = pd.DataFrame()
_ultimo_mtime_carregado: float = 0.0

ARQUIVO_ESTADO = _DATA_DIR / "ravex" / "estado_painel.pkl"


def get_df_ravex() -> pd.DataFrame:
    with _lock:
        return _df_ravex.copy()


def set_df_ravex(df: pd.DataFrame) -> None:
    """Atualiza o cache em memória do processo atual."""
    global _df_ravex
    with _lock:
        _df_ravex = df.copy() if df is not None else pd.DataFrame()


def persistir_em_arquivo(df: pd.DataFrame) -> None:
    """Grava o DataFrame em disco (escrita atômica) para outros processos
    lerem. Chamado ao fim de cada ciclo de atualização (automação via cron)."""
    ARQUIVO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO_ESTADO.with_suffix(".tmp")
    (df if df is not None else pd.DataFrame()).to_pickle(tmp)
    os.replace(tmp, ARQUIVO_ESTADO)


def sincronizar_com_arquivo() -> bool:
    """Recarrega o cache em memória deste processo se o arquivo em disco
    mudou desde a última leitura. Retorna True se havia dado novo."""
    global _ultimo_mtime_carregado
    try:
        mtime = ARQUIVO_ESTADO.stat().st_mtime
    except FileNotFoundError:
        return False
    if mtime <= _ultimo_mtime_carregado:
        return False
    try:
        df = pd.read_pickle(ARQUIVO_ESTADO)
    except Exception:
        return False
    set_df_ravex(df)
    _ultimo_mtime_carregado = mtime
    return True
