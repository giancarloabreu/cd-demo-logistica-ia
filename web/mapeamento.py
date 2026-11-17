"""
Mapeamento de colunas dos relatórios — rotas da tela (20/10/2026).

A resolução fica em app/services/mapeamento_colunas.py. Aqui: listar os
campos de cada relatório com a coluna achada no último arquivo, e gravar a
escolha do usuário. O mapeamento vale para todos os próximos arquivos
daquele tipo; se a coluna escolhida não existir num arquivo, a leitura volta
ao automático para aquele campo.
"""
from __future__ import annotations

import hmac
from functools import wraps

from flask import Blueprint, jsonify, request, session

from app.database import registrar_log
from app.services import mapeamento_colunas as mc
# os relatórios do Expedito se registram ao importar
from app.services import follow_up as _follow_up  # noqa: F401
from app.services import paletizado_colunas as _paletizado
# o Romaneio Detalhado (regra de data do CD) se registra ao importar
from app.services import romaneio_regra_data as _romaneio  # noqa: F401
from app.utils.access_control import somente_leitura, tem_acesso_modulo

mapeamento_bp = Blueprint("mapeamento", __name__)

_paletizado._registrar_tipo()

# módulo(s) que dão acesso a cada relatório
_MODULOS_DO_TIPO = {
    "estoque_faturado": ("monitoramento",),
    "controle_distribuicao": ("monitoramento",),
    "fila_redespacho": ("monitoramento",),
    "estoque_redespacho": ("monitoramento",),
    "opentech": ("monitoramento",),
    "romaneio_detalhado": ("monitoramento",),
    "follow_up": ("expedito",),
    "produto_paletizado": ("expedito", "armazem"),
}


# ── auth (mesmo padrão de web/carretas_bot.py) ──────────────────────
def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("user"):
            return jsonify({"error": "Não autenticado"}), 401
        return f(*a, **k)
    return d


def csrf_required(f):
    @wraps(f)
    def d(*a, **k):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            tok = request.headers.get("X-CSRF-Token", "")
            ses = session.get("csrf_token", "")
            if not ses or not hmac.compare_digest(tok, ses):
                return jsonify({"error": "Requisição inválida (CSRF)."}), 403
        return f(*a, **k)
    return d


def _tem_acesso(tipo: str) -> bool:
    return any(tem_acesso_modulo(m) for m in _MODULOS_DO_TIPO.get(tipo, ()))


# O mapeamento é global (vale para todos os CDs). O da OpenTech alimenta o
# painel único que o ciclo automático lê para todos: só o gerente altera,
# como as credenciais da OpenTech (revisão 21/10/2026)
_SO_GERENTE = ("opentech",)


def _pode_editar(tipo: str = "") -> bool:
    perfis = ("admin", "master") if tipo in _SO_GERENTE else ("admin", "master", "coordenador", "analista")
    return session.get("perfil") in perfis and not somente_leitura()


@mapeamento_bp.route("/api/mapeamento")
@login_required
def listar():
    """?modulo=monitoramento|expedito -> os relatórios daquele módulo."""
    modulo = (request.args.get("modulo") or "").strip()
    tipos = [t for t, mods in _MODULOS_DO_TIPO.items()
             if (not modulo or modulo in mods) and _tem_acesso(t) and t in mc.TIPOS]
    return jsonify({"tipos": [{**mc.descrever(t), "pode_editar": _pode_editar(t)} for t in tipos],
                    "pode_editar": _pode_editar()})


@mapeamento_bp.route("/api/mapeamento/<tipo>")
@login_required
def detalhe(tipo):
    if tipo not in mc.TIPOS or not _tem_acesso(tipo):
        return jsonify({"error": "Relatório desconhecido."}), 404
    return jsonify({**mc.descrever(tipo), "pode_editar": _pode_editar(tipo)})


@mapeamento_bp.route("/api/mapeamento/<tipo>", methods=["POST"])
@login_required
@csrf_required
def salvar(tipo):
    if tipo not in mc.TIPOS or not _tem_acesso(tipo):
        return jsonify({"error": "Relatório desconhecido."}), 404
    if not _pode_editar(tipo):
        return jsonify({"error": "Seu perfil não altera o mapeamento deste relatório."
                                 + (" O da OpenTech vale para todos os CDs e é alterado pelo gerente."
                                    if tipo in _SO_GERENTE else "")}), 403
    campos = (request.get_json(silent=True) or {}).get("campos") or {}
    if not isinstance(campos, dict):
        return jsonify({"error": "Dados inválidos."}), 400
    validos = mc.TIPOS[tipo]["campos"]
    alterados = []
    for campo, coluna in campos.items():
        if campo not in validos:
            continue
        coluna = str(coluna or "").strip()[:300]
        mc.salvar(tipo, campo, coluna, session.get("user", ""))
        alterados.append(f"{campo}={coluna or '(automático)'}")
    if alterados:
        registrar_log(session.get("user", ""), "mapeamento_colunas",
                      detalhe=f"{tipo}: " + "; ".join(alterados)[:900])
    return jsonify({"ok": True, **mc.descrever(tipo)})
