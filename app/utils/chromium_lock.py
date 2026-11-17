"""
Lock compartilhado entre as automações que sobem Chromium headless
(OpenTech e Ravex) — ver BUG-10 no BACKLOG.md.

Cada automação já tem seu próprio lock (data/opentech/atualizar.lock e
data/ravex/atualizar.lock), mas eles só impedem DUAS execuções da MESMA
automação. Nada impedia OpenTech e Ravex de rodarem Chromium ao mesmo tempo
— e na e2-micro (0,25 vCPU compartilhado) dois Chromiums simultâneos se
estrangulam mutuamente: ciclos que deveriam levar 1-2 min passam de 15, e a
grade Kendo da Ravex não renderiza dentro dos timeouts (falhas alternadas
"Botão 'Exportar para Excel' não apareceu").

Uso (nos scripts de cron, depois do auto-throttle e do lock próprio):

    lock = adquirir_lock_chromium()
    if lock is None:
        print("outra automação com Chromium em andamento — pulando")
        return 0        # o próximo tick do cron (5 min) tenta de novo
    try:
        ...automação...
    finally:
        liberar_lock_chromium(lock)

Em Windows/dev (sem fcntl) o lock é no-op, igual aos locks individuais.
"""
from __future__ import annotations
from app.utils.paths import DATA_DIR as _DATA_DIR
from pathlib import Path

LOCK_CHROMIUM = _DATA_DIR / "chromium.lock"


def adquirir_lock_chromium():
    """Tenta o lock não-bloqueante. Retorna o file handle (manter aberto até
    liberar) ou None se outra automação está com o Chromium rodando. Em
    plataformas sem fcntl retorna um handle sem trava (no-op)."""
    LOCK_CHROMIUM.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_CHROMIUM, "w")
    try:
        import fcntl
    except ImportError:
        return lock_file  # Windows/dev: sem trava
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except OSError:
        lock_file.close()
        return None


def liberar_lock_chromium(lock_file) -> None:
    if lock_file is None:
        return
    try:
        import fcntl
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_file.close()
    except Exception:
        pass
