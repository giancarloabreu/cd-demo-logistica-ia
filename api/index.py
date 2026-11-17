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


class _CaminhoOriginal:
    """Rede de segurança para a reescrita da Vercel ("/(.*)" -> "/api/index").
    Normalmente a função recebe o caminho que o visitante pediu. Se chegar o
    caminho da própria função, o original é recuperado do cabeçalho que a
    Vercel manda com o trecho capturado pela regra."""

    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        caminho = environ.get("PATH_INFO", "")
        if caminho in ("/api/index", "/api/index.py"):
            from urllib.parse import parse_qs
            capturado = parse_qs(environ.get("HTTP_X_NOW_ROUTE_MATCHES", "")).get("1", [""])[0]
            if capturado and not capturado.startswith("api/index"):
                environ["PATH_INFO"] = "/" + capturado.lstrip("/")
        return self.wsgi(environ, start_response)


app.wsgi_app = _CaminhoOriginal(app.wsgi_app)
