"""Link de download dos arquivos gerados em saida/ (imagens, Excel).

03/11/2026: a posse do arquivo ia numa lista dentro do cookie de sessão. A
exportação de imagens leva minutos no worker único; as chamadas automáticas
da tela (/api/me, status OpenTech/Ravex) ficavam na fila com o cookie antigo
e, atendidas depois, regravavam a sessão sem a lista — o download e a cópia
recebiam "Acesso negado" ("Arquivo não disponível no site" no Chrome).

Agora o link leva uma assinatura do nome do arquivo e do usuário, e nada é
gravado na sessão: outra pessoa com o mesmo link continua sem baixar, e uma
resposta atrasada não tem como apagar a permissão."""
from __future__ import annotations

from flask import current_app, session
from itsdangerous import BadSignature, URLSafeTimedSerializer

_VALIDADE_S = 24 * 3600          # os arquivos de saida/ são limpos todo dia


def _assinador() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="saida-arquivo")


def url_saida(nome: str) -> str:
    """URL do arquivo gerado, válida para o usuário da sessão atual."""
    token = _assinador().dumps([nome, session.get("user", "")])
    return f"/saida/{nome}?k={token}"


def link_valido(nome: str, token: str | None) -> bool:
    """O link foi assinado para ESTE arquivo e para o usuário logado."""
    if not token:
        return False
    try:
        n, u = _assinador().loads(token, max_age=_VALIDADE_S)
    except (BadSignature, ValueError, TypeError):
        return False
    return n == nome and bool(u) and u == session.get("user", "")
