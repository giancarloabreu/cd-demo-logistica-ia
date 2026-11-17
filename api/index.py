"""
Entrada da Vercel (função Python).

Na Vercel o código fica em disco somente leitura: tudo que o sistema grava
(banco SQLite, uploads, exportações) vai para /tmp. Cada instância nova
começa vazia e a demo recarrega os dados de exemplo do dia sozinha.
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

os.environ.setdefault("DEMO_MODE", "1")
os.environ.setdefault("CD_BASE_DIR", "/tmp/cd-demo")
os.environ.setdefault("CD_DATA_DIR", "/tmp/cd-demo/data")
Path(os.environ["CD_DATA_DIR"]).mkdir(parents=True, exist_ok=True)

from web.server import app  # noqa: E402  (precisa do sys.path acima)
