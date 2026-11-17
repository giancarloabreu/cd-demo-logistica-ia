"""
Rate limiting genérico em memória (por processo) para endpoints sensíveis
além do login. A VM roda e2-micro (964 MB RAM, 1 worker Gunicorn sync) —
chamadas repetidas a rotas pesadas (parsing de planilhas, disparo da
automação OpenTech) podem degradar o sistema inteiro para todos os usuários.
"""
from __future__ import annotations
import time
from functools import wraps
from flask import request, session, jsonify

_buckets: dict = {}


def rate_limited(nome: str, max_chamadas: int, janela_seg: int):
    """Limita chamadas por usuário logado (ou IP, se anônimo) numa janela de
    tempo deslizante. Usar depois de @login_required na pilha de decorators."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            chave = str(session.get("user") or request.remote_addr or "anon")
            bucket_key = (nome, chave)
            now = time.time()
            tentativas = [ts for ts in _buckets.get(bucket_key, []) if now - ts < janela_seg]
            if len(tentativas) >= max_chamadas:
                return jsonify({"error": "Muitas requisições em pouco tempo. Aguarde um momento e tente novamente."}), 429
            tentativas.append(now)
            _buckets[bucket_key] = tentativas
            return f(*args, **kwargs)
        return wrapped
    return decorator
