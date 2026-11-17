"""
Regra de data do CD pelo Romaneio Detalhado — rotas (16/11/2026).
A regra de negócio fica em app/services/romaneio_regra_data.py.

Tudo é da CONTA do CD (usuario_efetivo): parâmetros e análises valem para
coordenador, analista e gerente do mesmo CD. Carregar o romaneio, escolher a
NF e gerar o e-mail: os mesmos perfis que mudam a Regra de Data no card.
Ver a análise: qualquer perfil com o módulo.

O painel (cards e notas da placa) vive no estado do servidor em
web/server.py; ele entrega o acesso por registrar_fonte_painel(func), mesmo
padrão do bot de carretas (import circular com server.py).
"""
from __future__ import annotations

import hmac
import os
import tempfile
from functools import wraps
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, session

from app.database import registrar_log
from app.services import romaneio_regra_data as rr
from app.services.mapeamento_colunas import ColunasFaltando
from app.utils.access_control import modulo_required, somente_leitura, usuario_efetivo

romaneio_regra_bp = Blueprint("romaneio_regra", __name__)

_EXTENSOES = (".xls", ".xlsx", ".xlsm")
_fonte_painel = None


def registrar_fonte_painel(func) -> None:
    """func(conta) -> {"col": df_consolidado, "itens": df_itens,
    "estoque": caminho do último Estoque em Trânsito enviado ou None}."""
    global _fonte_painel
    _fonte_painel = func


# ── auth (mesmo padrão de web/outras_operacoes.py) ──────────────────
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


def _pode_editar() -> bool:
    return (session.get("perfil") in ("admin", "master", "coordenador", "analista")
            and not somente_leitura())


def _card_id() -> str:
    v = request.values.get("card_id")
    if v is None and request.is_json:
        v = (request.get_json(silent=True) or {}).get("card_id")
    return str(v or "").strip()[:200]


@romaneio_regra_bp.route("/api/romaneio_regra/config")
@login_required
@modulo_required("monitoramento")
def ver_config():
    cfg = rr.config(usuario_efetivo())
    cfg["pode_editar"] = _pode_editar()
    return jsonify(cfg)


@romaneio_regra_bp.route("/api/romaneio_regra/config", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def salvar_config():
    if not _pode_editar():
        return jsonify({"error": "Seu perfil não altera os parâmetros da regra de data."}), 403
    conta = usuario_efetivo()
    try:
        cfg = rr.salvar_config(conta, request.get_json(silent=True) or {}, session.get("user", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    s = cfg["segmentos"]
    registrar_log(session.get("user", ""), "romaneio_regra_config", alvo=conta,
                  detalhe=f"{cfg['pct']}% · varejo {','.join(s['Varejo']['empresas']) or '-'} "
                          f"({len(s['Varejo']['para'])}+{len(s['Varejo']['cc'])} e-mails) · atacado "
                          f"{','.join(s['Atacado']['empresas']) or '-'} "
                          f"({len(s['Atacado']['para'])}+{len(s['Atacado']['cc'])} e-mails)")
    cfg["pode_editar"] = True
    return jsonify({"ok": True, "config": cfg})


@romaneio_regra_bp.route("/api/romaneio_regra/resumos")
@login_required
@modulo_required("monitoramento")
def resumos():
    try:
        return jsonify({"resumos": rr.resumos(usuario_efetivo()), "pode_editar": _pode_editar()})
    except Exception as e:
        print(f"[Romaneio] Resumos não lidos: {e}")
        return jsonify({"resumos": {}, "pode_editar": _pode_editar()})


@romaneio_regra_bp.route("/api/romaneio_regra/analisar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def analisar():
    if not _pode_editar():
        return jsonify({"error": "Seu perfil não carrega o romaneio."}), 403
    card_id = _card_id()
    if not card_id:
        return jsonify({"error": "Card não informado."}), 400
    f = request.files.get("romaneio")
    if not f or not f.filename:
        return jsonify({"error": "Envie o Romaneio Detalhado."}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in _EXTENSOES:
        return jsonify({"error": "Formato não aceito. Envie o Romaneio Detalhado em .xls ou .xlsx."}), 400
    if _fonte_painel is None:
        return jsonify({"error": "Painel indisponível. Atualize a página."}), 503
    conta = usuario_efetivo()
    painel = _fonte_painel(conta) or {}
    try:
        card, linhas = rr.card_e_linhas(painel.get("col"), painel.get("itens"), card_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    if linhas and all(l.get("preco") is None for l in linhas) and painel.get("estoque"):
        # painel restaurado de antes da coluna de preço: relê o último arquivo
        try:
            from app.services.estoque_faturado_service import carregar_estoque_faturado
            rr.completar_precos(linhas, carregar_estoque_faturado(str(painel["estoque"])))
        except Exception as e:
            print(f"[Romaneio] Preço não relido do Estoque de {conta}: {e}")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        f.save(tmp.name)
        caminho = tmp.name
    try:
        avisos: list = []
        caixas = rr.ler_romaneio(caminho, f.filename, avisos)
        res = rr.analisar(conta, card, linhas, caixas, f.filename, session.get("user", ""), avisos)
        t = res["totais"]
        registrar_log(session.get("user", ""), "romaneio_regra_analise", alvo=f"{conta}:{card_id}",
                      detalhe=f"{f.filename} · {t['romaneio_cx']} cx · {t['fora_cx']} fora · "
                              f"{len(res['pendentes'])} bloco(s) a confirmar")
        return jsonify({"ok": True, "resultado": res})
    except ColunasFaltando as e:
        return jsonify(e.para_tela()), 422
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[Romaneio] Romaneio não lido ({card_id}): {e}")
        return jsonify({"error": "Não foi possível ler o arquivo. Confirme que é o Romaneio Detalhado."}), 400
    finally:
        try:
            os.unlink(caminho)
        except OSError:
            pass


@romaneio_regra_bp.route("/api/romaneio_regra/resultado")
@login_required
@modulo_required("monitoramento")
def resultado():
    card_id = _card_id()
    res = rr.resultado(usuario_efetivo(), card_id) if card_id else None
    if not res:
        return jsonify({"error": "Este card ainda não tem romaneio analisado."}), 404
    return jsonify({"resultado": res, "pode_editar": _pode_editar()})


@romaneio_regra_bp.route("/api/romaneio_regra/escolha", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def escolha():
    if not _pode_editar():
        return jsonify({"error": "Seu perfil não altera a análise do romaneio."}), 403
    d = request.get_json(silent=True) or {}
    card_id = str(d.get("card_id") or "").strip()[:200]
    bloco = str(d.get("bloco") or "")[:300]
    nf = str(d.get("nf") or "")[:20]
    try:
        res = rr.escolher(usuario_efetivo(), card_id, bloco, nf, session.get("user", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    registrar_log(session.get("user", ""), "romaneio_regra_escolha", alvo=f"{usuario_efetivo()}:{card_id}",
                  detalhe=f"{bloco} -> {nf or '(sem escolha)'}")
    return jsonify({"ok": True, "resultado": res})


@romaneio_regra_bp.route("/api/romaneio_regra/email")
@login_required
@modulo_required("monitoramento")
def email():
    if not _pode_editar():
        return jsonify({"error": "Seu perfil não gera o e-mail."}), 403
    card_id = _card_id()
    segmento = str(request.args.get("segmento") or "")
    res = rr.resultado(usuario_efetivo(), card_id) if card_id else None
    if not res:
        return jsonify({"error": "Este card ainda não tem romaneio analisado."}), 404
    try:
        nome, conteudo = rr.montar_email(res, segmento)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    registrar_log(session.get("user", ""), "romaneio_regra_email", alvo=f"{usuario_efetivo()}:{card_id}",
                  detalhe=f"{segmento} · NF {', '.join(res['segmentos'][segmento]['nfs'])}")
    return Response(conteudo, mimetype="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"',
                             "Cache-Control": "no-store"})


# ── resumo do dia (todas as carretas analisadas na data) ────────────
def _dia_pedido(conta: str):
    """Dia de ?dia=AAAA-MM-DD; sem ele, hoje quando há análise hoje, senão o
    último dia com análise. Devolve (data, dias com análise)."""
    dias = rr.dias_com_analise(conta)
    v = str(request.args.get("dia") or "").strip()[:10]
    if v:
        d = rr._iso(v)
        if not d:
            raise ValueError("Data inválida.")
        return d, dias
    hoje = rr._agora().date()
    if dias and not any(x["dia"] == hoje.isoformat() for x in dias):
        return rr._iso(dias[0]["dia"]), dias
    return hoje, dias


@romaneio_regra_bp.route("/api/romaneio_regra/resumo_dia")
@login_required
@modulo_required("monitoramento")
def resumo_dia():
    conta = usuario_efetivo()
    try:
        dia, dias = _dia_pedido(conta)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    rd = rr.resumo_dia(conta, dia)
    rd["dias"] = dias
    rd["pode_editar"] = _pode_editar()
    return jsonify(rd)


@romaneio_regra_bp.route("/api/romaneio_regra/resumo_dia/email")
@login_required
@modulo_required("monitoramento")
def resumo_dia_email():
    if not _pode_editar():
        return jsonify({"error": "Seu perfil não gera o e-mail."}), 403
    conta = usuario_efetivo()
    v = rr._iso(str(request.args.get("dia") or "").strip()[:10])
    if not v:
        return jsonify({"error": "Informe o dia do resumo."}), 400
    segmento = str(request.args.get("segmento") or "")
    rd = rr.resumo_dia(conta, v)
    try:
        nome, conteudo = rr.montar_email_resumo(rd, segmento)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    t = rd["segmentos"][segmento]["totais"]
    registrar_log(session.get("user", ""), "romaneio_regra_email_resumo", alvo=f"{conta}:{v.isoformat()}",
                  detalhe=f"{segmento} · {t['carretas']} carreta(s) · {t['fora_cx']} cx fora")
    return Response(conteudo, mimetype="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"',
                             "Cache-Control": "no-store"})
