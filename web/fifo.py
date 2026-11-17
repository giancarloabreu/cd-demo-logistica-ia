"""
Blueprint do módulo "Devolução & Vencimento" (FIFO) — pedido do usuário
24/08/2026: hoje o comercial monta manualmente uma planilha todo dia com
produtos de Devolução + Próx. Vencimento (FIFO) a partir do relatório
Produto Paletizado, pra gerar pedido de venda direcionado.

Reaproveita o MESMO TXT já processado no Painel do Armazém (mesmo dia, sem
reenviar o arquivo) — web/armazem.py chama salvar_fifo_diario() logo depois
de calcular as métricas do Armazém (upload() e _recalcular_todos()).

Registra em server.py com:
    from web.fifo import fifo_bp, init_fifo_db
    init_fifo_db()
    app.register_blueprint(fifo_bp)
"""
from __future__ import annotations
import hmac
import io
from functools import wraps
from datetime import date

from flask import Blueprint, render_template, request, jsonify, session, send_file

from app.database import get_db, registrar_log
from app.services.fifo_service import filtrar_linhas_fifo, agrupar_relatorio
from app.services.export_fifo_excel_service import exportar_excel
from app.utils.access_control import (
    modulo_required, tem_acesso_modulo, usuario_efetivo as _usuario_efetivo,
    bloquear_escrita,
)

fifo_bp = Blueprint("fifo", __name__)

DIAS_FILTRO_PADRAO = 15


def _hoje_brt() -> str:
    """"Hoje" em horário de Brasília, não do servidor (mesmo achado do
    Armazém, 29/08/2026: `date.today()` numa VM em UTC vira o dia seguinte
    a partir das ~21h de Brasília — usar sempre isso em vez de
    `date.today()` quando o sentido for "que dia é hoje pro usuário")."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


# ── auth (duplicado do padrão armazem.py/adensamento.py) ──────────────
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


# ── banco ────────────────────────────────────────────────────────────
def init_fifo_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS armazem_fifo_linhas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario        TEXT NOT NULL DEFAULT '',
            empresa        TEXT NOT NULL DEFAULT '',
            data           DATE NOT NULL,
            tipo_estoque   TEXT NOT NULL,
            temperatura    TEXT NOT NULL DEFAULT '',
            local          TEXT NOT NULL DEFAULT '',
            reserva_codigo TEXT DEFAULT '',
            reserva_nome   TEXT DEFAULT '',
            produto_codigo TEXT NOT NULL DEFAULT '',
            produto_nome   TEXT NOT NULL DEFAULT '',
            producao       DATE,
            validade       DATE,
            peso_liquido   REAL NOT NULL DEFAULT 0,
            bloqueado      INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_armazem_fifo_busca
            ON armazem_fifo_linhas(usuario, data, tipo_estoque);
        -- Reservas que o usuário marcou pra nunca aparecer no relatório
        -- (ex.: produto não localizado — reserva existe mas não dá pra
        -- vender de verdade). Ausência de linha = reserva aparece
        -- normalmente; "desmarcar" só grava aqui (pedido do usuário
        -- 24/08/2026).
        CREATE TABLE IF NOT EXISTS armazem_fifo_reserva_excluida (
            usuario        TEXT NOT NULL DEFAULT '',
            reserva_codigo TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (usuario, reserva_codigo)
        );
        """)
        # empresa (redesenho Local×Empresa, 29/08/2026) — id autoincrement,
        # não precisa recriar a tabela como armazem_diario. Linha antiga
        # (sem empresa) vira empresa=usuario, mesma convenção do fallback
        # de 1-empresa em _empresas_do_usuario().
        cols_fifo = [r[1] for r in db.execute("PRAGMA table_info(armazem_fifo_linhas)").fetchall()]
        if "empresa" not in cols_fifo:
            db.execute("ALTER TABLE armazem_fifo_linhas ADD COLUMN empresa TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE armazem_fifo_linhas SET empresa=usuario WHERE empresa=''")
            print("[DB] Migração: coluna empresa adicionada em armazem_fifo_linhas")


def salvar_fifo_diario(usuario: str, empresa: str, data: str, linhas: list[dict], locais: dict[str, str]):
    """Chamado de web/armazem.py logo depois de calcular as métricas do
    Armazém (upload de TXT novo ou reclassificação de local) — persiste só
    o subconjunto relevante pra Devolução + Próx. Vencimento (ver
    filtrar_linhas_fifo), não o arquivo inteiro. Upsert por (usuario,
    empresa, data): um novo processamento no mesmo dia PRA MESMA EMPRESA
    substitui o anterior — empresas diferentes não se apagam mais
    (redesenho Local×Empresa, 29/08/2026; antes era só por (usuario,data),
    upload da 2ª empresa no mesmo dia apagava a 1ª)."""
    locais_devolucao = {nome for nome, cls in locais.items() if cls == "devolucao"}
    locais_desconsiderar = {nome for nome, cls in locais.items() if cls == "desconsiderar"}
    data_ref = date.fromisoformat(data)
    candidatas = filtrar_linhas_fifo(linhas, locais_devolucao, locais_desconsiderar, data_ref)

    with get_db() as db:
        db.execute("DELETE FROM armazem_fifo_linhas WHERE usuario=? AND empresa=? AND data=?", (usuario, empresa, data))
        db.executemany("""
            INSERT INTO armazem_fifo_linhas
                (usuario, empresa, data, tipo_estoque, temperatura, local, reserva_codigo, reserva_nome,
                 produto_codigo, produto_nome, producao, validade, peso_liquido, bloqueado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            (usuario, empresa, data, r["tipo_estoque"], r["temperatura"], r["local"],
             r["reserva_codigo"], r["reserva_nome"], r["codigo"], r["nome"],
             r["producao"].isoformat() if r["producao"] else None,
             r["validade"].isoformat() if r["validade"] else None,
             r["peso_liquido"], int(r["bloqueado"]))
            for r in candidatas
        ])


def _carregar_linhas_persistidas(usuario: str, data: str) -> list[dict]:
    """Sempre TODAS as empresas desse Local juntas — Devolução & Vencimento
    não tem seletor de empresa (decisão registrada no BACKLOG: mostra tudo
    junto, distingue só pela coluna Empresa)."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM armazem_fifo_linhas WHERE usuario=? AND data=?", (usuario, data)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["producao"] = date.fromisoformat(d["producao"]) if d["producao"] else None
        d["validade"] = date.fromisoformat(d["validade"]) if d["validade"] else None
        d["bloqueado"] = bool(d["bloqueado"])
        out.append(d)
    return out


def _dias_com_dado(usuario: str) -> list[str]:
    with get_db() as db:
        rows = db.execute(
            "SELECT DISTINCT data FROM armazem_fifo_linhas WHERE usuario=? ORDER BY data DESC", (usuario,)
        ).fetchall()
    return [r["data"] for r in rows]


def _reservas_conhecidas(usuario: str) -> list[dict]:
    """Reservas distintas já vistas nos relatórios desse usuário (qualquer
    dia salvo) + se estão marcadas pra ficar de fora — alimenta a lista de
    checkboxes em "⚙️ Reservas" (pedido do usuário 24/08/2026)."""
    with get_db() as db:
        vistas = db.execute("""
            SELECT DISTINCT reserva_codigo, reserva_nome FROM armazem_fifo_linhas
            WHERE usuario=? AND reserva_codigo != ''
            ORDER BY reserva_nome
        """, (usuario,)).fetchall()
        excluidas = {r["reserva_codigo"] for r in db.execute(
            "SELECT reserva_codigo FROM armazem_fifo_reserva_excluida WHERE usuario=?", (usuario,)
        ).fetchall()}
    return [
        {"codigo": r["reserva_codigo"], "nome": r["reserva_nome"], "excluida": r["reserva_codigo"] in excluidas}
        for r in vistas
    ]


def _reservas_excluidas(usuario: str) -> frozenset:
    with get_db() as db:
        rows = db.execute(
            "SELECT reserva_codigo FROM armazem_fifo_reserva_excluida WHERE usuario=?", (usuario,)
        ).fetchall()
    return frozenset(r["reserva_codigo"] for r in rows)


# ── página ───────────────────────────────────────────────────────────
@fifo_bp.route("/fifo")
def pagina():
    from flask import redirect, url_for
    if not session.get("user"):
        return redirect(url_for("login_page"))
    if not tem_acesso_modulo("fifo"):
        return redirect(url_for("index"))
    return render_template("fifo.html", user=session.get("user"))


@fifo_bp.route("/api/fifo/reservas")
@login_required
@modulo_required("fifo")
def reservas():
    return jsonify({"reservas": _reservas_conhecidas(_usuario_efetivo())})


@fifo_bp.route("/api/fifo/reservas", methods=["POST"])
@login_required
@modulo_required("fifo")
@bloquear_escrita
@csrf_required
def salvar_reservas():
    usuario = _usuario_efetivo()
    excluidas = [str(c).strip() for c in (request.json or {}).get("excluidas", []) if str(c).strip()]
    with get_db() as db:
        db.execute("DELETE FROM armazem_fifo_reserva_excluida WHERE usuario=?", (usuario,))
        db.executemany(
            "INSERT INTO armazem_fifo_reserva_excluida (usuario, reserva_codigo) VALUES (?,?)",
            [(usuario, c) for c in excluidas],
        )
    registrar_log(session.get("user", ""), "fifo_salvar_reservas_excluidas", detalhe=",".join(excluidas))
    return jsonify({"ok": True})


@fifo_bp.route("/api/fifo/dados")
@login_required
@modulo_required("fifo")
def dados():
    usuario = _usuario_efetivo()
    data_param = request.args.get("data", "").strip() or _hoje_brt()
    dias_filtro = request.args.get("dias", type=int) or DIAS_FILTRO_PADRAO
    mostrar_bloqueados = request.args.get("bloqueados", "").strip() == "1"
    try:
        data_ref = date.fromisoformat(data_param)
    except ValueError:
        return jsonify({"error": "Data inválida."}), 400

    linhas = _carregar_linhas_persistidas(usuario, data_param)
    if not linhas:
        return jsonify({"tem_dados": False, "dias_com_dado": _dias_com_dado(usuario)})

    relatorio = agrupar_relatorio(linhas, data_ref, dias_filtro, mostrar_bloqueados, _reservas_excluidas(usuario))
    relatorio["tem_dados"] = True
    relatorio["dias_com_dado"] = _dias_com_dado(usuario)
    return jsonify(relatorio)


@fifo_bp.route("/api/fifo/exportar")
@login_required
@modulo_required("fifo")
def exportar():
    usuario = _usuario_efetivo()
    data_param = request.args.get("data", "").strip() or _hoje_brt()
    dias_filtro = request.args.get("dias", type=int) or DIAS_FILTRO_PADRAO
    mostrar_bloqueados = request.args.get("bloqueados", "").strip() == "1"
    try:
        data_ref = date.fromisoformat(data_param)
    except ValueError:
        return jsonify({"error": "Data inválida."}), 400

    linhas = _carregar_linhas_persistidas(usuario, data_param)
    if not linhas:
        return jsonify({"error": "Sem dados pra exportar nesse dia."}), 400

    relatorio = agrupar_relatorio(linhas, data_ref, dias_filtro, mostrar_bloqueados, _reservas_excluidas(usuario))
    conteudo = exportar_excel(relatorio, data_ref)
    registrar_log(session.get("user", ""), "fifo_exportar_excel")
    return send_file(
        io.BytesIO(conteudo),
        as_attachment=True,
        download_name=f"Devolucao_Vencimento_{data_param}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
