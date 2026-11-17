"""
Estado global compartilhado do DataFrame de monitoramento obtido
automaticamente da OpenTech. Usado por todos os usuários como fonte
padrão, salvo quando um usuário faz importação manual (que sobrescreve
apenas a sessão dele temporariamente).

A automação (Playwright) roda num processo separado, disparado por cron
(scripts/atualizar_opentech.py — ver BUG-5 no BACKLOG.md), e persiste o
resultado em ARQUIVO_ESTADO. O processo do Flask/Gunicorn nunca executa o
Playwright — só lê esse arquivo periodicamente (sincronizar_com_arquivo).
"""
from __future__ import annotations
from app.utils.paths import DATA_DIR as _DATA_DIR
import os
import threading
from pathlib import Path
import pandas as pd

_lock = threading.Lock()
_df_monitoramento: pd.DataFrame = pd.DataFrame()
_ultimo_mtime_carregado: float = 0.0

ARQUIVO_ESTADO = _DATA_DIR / "opentech" / "estado_painel.pkl"


def get_df_monitoramento() -> pd.DataFrame:
    with _lock:
        return _df_monitoramento.copy()


def set_df_monitoramento(df: pd.DataFrame) -> None:
    """Atualiza o cache em memória do processo atual."""
    global _df_monitoramento
    with _lock:
        _df_monitoramento = df.copy() if df is not None else pd.DataFrame()


def persistir_em_arquivo(df: pd.DataFrame) -> None:
    """Grava o DataFrame em disco (escrita atômica) para outros processos
    lerem. Chamado ao fim de cada ciclo de atualização (automação via cron
    ou botão "atualizar agora")."""
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
    set_df_monitoramento(df)
    _ultimo_mtime_carregado = mtime
    return True
