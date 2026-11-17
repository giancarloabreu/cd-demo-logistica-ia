"""
Blueprint de Adensamento — Monitoramento CD Demo
Registra em server.py com:
    from web.adensamento import adensamento_bp, init_adensamento_db
    init_adensamento_db()
    app.register_blueprint(adensamento_bp)

Usa o mesmo banco (data/monitoramento.db) e o mesmo esquema de auth/CSRF
por sessão do app principal.
"""
from __future__ import annotations
import io, hmac, json, tempfile
from functools import wraps
from pathlib import Path

from flask import (Blueprint, render_template, request, jsonify,
                   send_file, session)

from app.database import get_db, registrar_log
from app.services.adensamento_service import parse_txt, montar_adensamento
from app.utils.rate_limit import rate_limited
from app.utils.access_control import (
    modulo_required, tem_acesso_modulo, usuario_efetivo as _usuario_efetivo,
    bloquear_escrita_exceto_operador, somente_leitura_exceto_operador,
)

adensamento_bp = Blueprint("adensamento", __name__)

# resultado do último processamento, por usuário (para exportar/reordenar)
_ultimo = {}
_alertas = {}   # códigos com alerta de peso, por usuário

_CONFIG_PADRAO = {
    "range_ambiente": "5", "range_resfriado": "5", "range_congelado": "30",
    "misto_qtd_max": "25", "misto_peso_max": "0", "misto_threshold_pct": "60",
}


# ── auth (mesma lógica do server principal, desacoplada) ──────────────
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
# Cadastro/config sandboxed por usuário (a pedido, 24/07/2026): cada usuário
# tem seu próprio cadastro, mas sempre herda ao vivo o pool base (usuario='')
# como fundação — códigos que o usuário nunca customizou continuam visíveis
# a partir do base, só os que ele mexeu ficam com a versão dele. O pool base
# é onde vivem os 213 códigos/6 configs que já existiam antes desta migração
# (preservados, não perdidos — todo mundo parte deles).
_USUARIO_BASE = ""


def init_adensamento_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS adensamento_regras (
            codigo      TEXT PRIMARY KEY,
            nome        TEXT DEFAULT '',
            temperatura TEXT DEFAULT '',
            qtd_max     INTEGER DEFAULT 0,
            peso_max    REAL DEFAULT 0,
            completo    INTEGER DEFAULT 0,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS adensamento_config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        );
        CREATE TABLE IF NOT EXISTS adensamento_boxes (
            usuario     TEXT NOT NULL DEFAULT '',
            camara      TEXT NOT NULL,
            box         TEXT NOT NULL,
            altura      TEXT NOT NULL,
            quantidade  INTEGER NOT NULL DEFAULT 0,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (usuario, camara, box, altura)
        );
        CREATE TABLE IF NOT EXISTS adensamento_historico (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario           TEXT NOT NULL DEFAULT '',
            processado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
            nome_arquivo      TEXT DEFAULT '',
            total_caixas      INTEGER DEFAULT 0,
            paletes_origem    INTEGER DEFAULT 0,
            paletes_sugeridos INTEGER DEFAULT 0,
            peso_total        REAL DEFAULT 0,
            resumo_json       TEXT DEFAULT '',
            pallets_json      TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_adensamento_historico_data
            ON adensamento_historico(processado_em);
        """)

        # migração: adiciona usuario e troca a PK de codigo/chave sozinhos
        # para (usuario, codigo)/(usuario, chave) — preserva os dados
        # existentes atribuindo tudo ao pool base (usuario='')
        cols_regras = [r[1] for r in db.execute("PRAGMA table_info(adensamento_regras)").fetchall()]
        if "usuario" not in cols_regras:
            db.executescript("""
                ALTER TABLE adensamento_regras RENAME TO adensamento_regras_old;
                CREATE TABLE adensamento_regras (
                    usuario     TEXT NOT NULL DEFAULT '',
                    codigo      TEXT NOT NULL,
                    nome        TEXT DEFAULT '',
                    temperatura TEXT DEFAULT '',
                    qtd_max     INTEGER DEFAULT 0,
                    peso_max    REAL DEFAULT 0,
                    completo    INTEGER DEFAULT 0,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (usuario, codigo)
                );
                INSERT INTO adensamento_regras
                    (usuario, codigo, nome, temperatura, qtd_max, peso_max, completo, updated_at)
                    SELECT '', codigo, nome, temperatura, qtd_max, peso_max, completo, updated_at
                    FROM adensamento_regras_old;
                DROP TABLE adensamento_regras_old;
            """)
            print("[DB] Migração: adensamento_regras agora é sandboxed por usuário "
                  "(dados existentes preservados no pool base)")

        cols_cfg = [r[1] for r in db.execute("PRAGMA table_info(adensamento_config)").fetchall()]
        if "usuario" not in cols_cfg:
            db.executescript("""
                ALTER TABLE adensamento_config RENAME TO adensamento_config_old;
                CREATE TABLE adensamento_config (
                    usuario TEXT NOT NULL DEFAULT '',
                    chave   TEXT NOT NULL,
                    valor   TEXT,
                    PRIMARY KEY (usuario, chave)
                );
                INSERT INTO adensamento_config (usuario, chave, valor)
                    SELECT '', chave, valor FROM adensamento_config_old;
                DROP TABLE adensamento_config_old;
            """)
            print("[DB] Migração: adensamento_config agora é sandboxed por usuário "
                  "(dados existentes preservados no pool base)")

        for ch, val in _CONFIG_PADRAO.items():
            db.execute("INSERT OR IGNORE INTO adensamento_config(usuario,chave,valor) "
                       "VALUES('',?,?)", (ch, val))


def _get_config(usuario: str = "") -> dict:
    """Config efetiva do usuário = pool base + overrides próprios (se houver)."""
    with get_db() as db:
        rows = db.execute("SELECT chave,valor FROM adensamento_config WHERE usuario=?",
                          (_USUARIO_BASE,)).fetchall()
        cfg = {r["chave"]: r["valor"] for r in rows}
        if usuario and usuario != _USUARIO_BASE:
            proprio = db.execute("SELECT chave,valor FROM adensamento_config WHERE usuario=?",
                                 (usuario,)).fetchall()
            cfg.update({r["chave"]: r["valor"] for r in proprio})
    full = dict(_CONFIG_PADRAO)
    full.update(cfg)
    num = {k: float(v) if v not in (None, "") else 0 for k, v in full.items()}
    return full, num


def _get_cadastro(usuario: str = "") -> dict:
    """Cadastro efetivo do usuário = pool base + overrides/adições próprias."""
    with get_db() as db:
        base = db.execute("SELECT * FROM adensamento_regras WHERE usuario=?",
                          (_USUARIO_BASE,)).fetchall()
        cadastro = {r["codigo"]: {
            "nome": r["nome"], "temperatura": r["temperatura"],
            "qtd_max": r["qtd_max"], "peso_max": r["peso_max"],
            "completo": bool(r["completo"]),
        } for r in base}
        if usuario and usuario != _USUARIO_BASE:
            proprio = db.execute("SELECT * FROM adensamento_regras WHERE usuario=?",
                                 (usuario,)).fetchall()
            cadastro.update({r["codigo"]: {
                "nome": r["nome"], "temperatura": r["temperatura"],
                "qtd_max": r["qtd_max"], "peso_max": r["peso_max"],
                "completo": bool(r["completo"]),
            } for r in proprio})
    return cadastro


def _upsert_regra(usuario, codigo, nome, temperatura, qtd_max, peso_max):
    # qtd_max/peso_max chegam como string do JS (input.value é sempre string,
    # mesmo em type="number") — comparar string com int (`"50" > 0`) explode
    # com TypeError. Converte pra número ANTES de comparar; era isso que
    # fazia o salvar "não salvar" sempre que os dois campos vinham preenchidos.
    try:
        qtd_max_n = int(qtd_max) if qtd_max not in (None, "") else 0
    except (TypeError, ValueError):
        qtd_max_n = 0
    try:
        peso_max_n = float(peso_max) if peso_max not in (None, "") else 0
    except (TypeError, ValueError):
        peso_max_n = 0
    completo = 1 if (qtd_max_n > 0 and peso_max_n > 0) else 0
    with get_db() as db:
        db.execute("""
            INSERT INTO adensamento_regras(usuario,codigo,nome,temperatura,qtd_max,peso_max,completo,updated_at)
            VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(usuario,codigo) DO UPDATE SET
              nome=excluded.nome, temperatura=excluded.temperatura,
              qtd_max=excluded.qtd_max, peso_max=excluded.peso_max,
              completo=excluded.completo, updated_at=CURRENT_TIMESTAMP
        """, (usuario, str(codigo), nome or "", temperatura or "",
              qtd_max_n, peso_max_n, completo))


def _excluir_regra(usuario, codigo):
    """Remove o código do sandbox PRÓPRIO do usuário (usuario != pool base).
    Se o código também existir no pool base, ele volta a aparecer vindo de
    lá — excluir só tira a customização pessoal, não o pool compartilhado."""
    with get_db() as db:
        db.execute("DELETE FROM adensamento_regras WHERE usuario=? AND codigo=?",
                   (usuario, str(codigo)))


def _salvar_regras_lote(usuario, linhas):
    """Salva o grid inteiro de produtos de uma vez (mesma UX do cadastro de
    boxes: um botão "Salvar" só, não um por linha). orig_codigo é usado
    quando o usuário editou o próprio código na tela (renomear a chave) —
    remove a chave antiga antes de gravar a nova, senão vira duplicata."""
    n = 0
    for linha in linhas:
        codigo = str(linha.get("codigo", "")).strip()
        if not codigo:
            continue  # linha em branco (ex.: "+ nova linha" não preenchida) — ignora
        orig_codigo = linha.get("orig_codigo")
        if orig_codigo and str(orig_codigo).strip() != codigo:
            _excluir_regra(usuario, orig_codigo)
        _upsert_regra(usuario, codigo, linha.get("nome"), linha.get("temperatura"),
                      linha.get("qtd_max"), linha.get("peso_max"))
        n += 1
    return n


def _excluir_regras_lote(usuario, codigos):
    n = 0
    for cod in codigos:
        _excluir_regra(usuario, cod)
        n += 1
    return n


# ── cadastro de boxes (posições com capacidade reduzida — "meia posição") ──
# Diferente do cadastro de produtos, aqui não existe pool base compartilhado
# de verdade (cada CD tem um layout físico de câmaras/boxes só dele) — o
# merge com _USUARIO_BASE existe só por uniformidade com _get_cadastro, mas
# na prática cada usuário cadastra o próprio layout do zero ou herda de outro.
def _chave_box(camara, box, altura):
    """Normaliza pra comparar sem sensibilidade a maiúsculas/espaços — o
    endereço de origem vem cru do TXT (parse_txt), e aqui o usuário digita
    manualmente, então precisa casar mesmo com pequenas diferenças de digitação."""
    return (str(camara or "").strip().upper(),
            str(box or "").strip().upper(),
            str(altura or "").strip().upper())


def _get_boxes(usuario: str = "") -> dict:
    """{(camara,box,altura) normalizados: quantidade} — usado pelo algoritmo
    de adensamento pra achar o limite de uma posição física."""
    with get_db() as db:
        base = db.execute("SELECT * FROM adensamento_boxes WHERE usuario=?",
                          (_USUARIO_BASE,)).fetchall()
        boxes = {_chave_box(r["camara"], r["box"], r["altura"]): r["quantidade"] for r in base}
        if usuario and usuario != _USUARIO_BASE:
            proprio = db.execute("SELECT * FROM adensamento_boxes WHERE usuario=?",
                                 (usuario,)).fetchall()
            boxes.update({_chave_box(r["camara"], r["box"], r["altura"]): r["quantidade"]
                         for r in proprio})
    return boxes


def _get_boxes_lista(usuario: str = "") -> list:
    """Versão em lista (pra UI/exportação) — preserva os valores como o
    usuário digitou (não normalizados), ordenada por câmara/box/altura."""
    with get_db() as db:
        base = db.execute("SELECT * FROM adensamento_boxes WHERE usuario=?",
                          (_USUARIO_BASE,)).fetchall()
        linhas = {_chave_box(r["camara"], r["box"], r["altura"]):
                  {"camara": r["camara"], "box": r["box"], "altura": r["altura"],
                   "quantidade": r["quantidade"]} for r in base}
        if usuario and usuario != _USUARIO_BASE:
            proprio = db.execute("SELECT * FROM adensamento_boxes WHERE usuario=?",
                                 (usuario,)).fetchall()
            linhas.update({_chave_box(r["camara"], r["box"], r["altura"]):
                           {"camara": r["camara"], "box": r["box"], "altura": r["altura"],
                            "quantidade": r["quantidade"]} for r in proprio})
    return sorted(linhas.values(), key=lambda x: (x["camara"], x["box"], x["altura"]))


def _upsert_box(usuario, camara, box, altura, quantidade):
    camara, box, altura = (str(camara or "").strip(), str(box or "").strip(),
                            str(altura or "").strip())
    try:
        qtd_n = int(quantidade) if quantidade not in (None, "") else 0
    except (TypeError, ValueError):
        qtd_n = 0
    if not (camara and box and altura):
        return False
    with get_db() as db:
        db.execute("""
            INSERT INTO adensamento_boxes(usuario,camara,box,altura,quantidade,updated_at)
            VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(usuario,camara,box,altura) DO UPDATE SET
              quantidade=excluded.quantidade, updated_at=CURRENT_TIMESTAMP
        """, (usuario, camara, box, altura, qtd_n))
    return True


def _excluir_box(usuario, camara, box, altura):
    with get_db() as db:
        db.execute("DELETE FROM adensamento_boxes WHERE usuario=? AND camara=? AND box=? AND altura=?",
                   (usuario, str(camara or "").strip(), str(box or "").strip(), str(altura or "").strip()))


def _salvar_boxes_lote(usuario, linhas):
    """Salva várias linhas de uma vez (botão único "Salvar" pro grid inteiro,
    não um por linha). Cada item pode trazer orig_camara/orig_box/orig_altura
    quando a linha já existia e o usuário editou a CHAVE (câmara/box/altura) —
    nesse caso remove a chave antiga antes de gravar a nova, senão vira duplicata."""
    n = 0
    for linha in linhas:
        camara = str(linha.get("camara", "")).strip()
        box = str(linha.get("box", "")).strip()
        altura = str(linha.get("altura", "")).strip()
        if not (camara and box and altura):
            continue  # linha em branco (ex.: linha "+ nova" não preenchida) — ignora
        orig_camara = linha.get("orig_camara")
        orig_box = linha.get("orig_box")
        orig_altura = linha.get("orig_altura")
        if orig_camara and (orig_camara != camara or orig_box != box or orig_altura != altura):
            _excluir_box(usuario, orig_camara, orig_box, orig_altura)
        if _upsert_box(usuario, camara, box, altura, linha.get("quantidade")):
            n += 1
    return n


def _excluir_boxes_lote(usuario, chaves):
    n = 0
    for ch in chaves:
        _excluir_box(usuario, ch.get("camara"), ch.get("box"), ch.get("altura"))
        n += 1
    return n


def _herdar_boxes(usuario_destino, usuario_origem):
    with get_db() as db:
        boxes = db.execute("SELECT * FROM adensamento_boxes WHERE usuario=?",
                           (usuario_origem,)).fetchall()
        for r in boxes:
            db.execute("""
                INSERT INTO adensamento_boxes(usuario,camara,box,altura,quantidade,updated_at)
                VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(usuario,camara,box,altura) DO UPDATE SET
                  quantidade=excluded.quantidade, updated_at=CURRENT_TIMESTAMP
            """, (usuario_destino, r["camara"], r["box"], r["altura"], r["quantidade"]))
    return len(boxes)


def _inserir_codigos_novos(usuario, codigos_txt):
    """Sobe códigos do TXT que ainda não existem no cadastro efetivo do
    usuário (base + próprio) — pendências vão pro sandbox dele, não pro base."""
    cadastro_atual = _get_cadastro(usuario)
    with get_db() as db:
        for c in codigos_txt:
            if c["codigo"] not in cadastro_atual:
                db.execute("INSERT OR IGNORE INTO adensamento_regras"
                           "(usuario,codigo,nome,temperatura,qtd_max,peso_max,completo) "
                           "VALUES(?,?,?,?,0,0,0)",
                           (usuario, c["codigo"], c["nome"], c["temperatura"]))


def _herdar_cadastro(usuario_destino: str, usuario_origem: str) -> int:
    """Copia (upsert, sobrescreve) o cadastro+config PRÓPRIO de usuario_origem
    para usuario_destino. usuario_origem='' herda de volta o pool base
    (equivale a "resetar" pro padrão)."""
    with get_db() as db:
        regras = db.execute("SELECT * FROM adensamento_regras WHERE usuario=?",
                            (usuario_origem,)).fetchall()
        for r in regras:
            db.execute("""
                INSERT INTO adensamento_regras(usuario,codigo,nome,temperatura,qtd_max,peso_max,completo,updated_at)
                VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(usuario,codigo) DO UPDATE SET
                  nome=excluded.nome, temperatura=excluded.temperatura,
                  qtd_max=excluded.qtd_max, peso_max=excluded.peso_max,
                  completo=excluded.completo, updated_at=CURRENT_TIMESTAMP
            """, (usuario_destino, r["codigo"], r["nome"], r["temperatura"],
                  r["qtd_max"], r["peso_max"], r["completo"]))
        config = db.execute("SELECT * FROM adensamento_config WHERE usuario=?",
                            (usuario_origem,)).fetchall()
        for c in config:
            db.execute("""
                INSERT INTO adensamento_config(usuario,chave,valor) VALUES(?,?,?)
                ON CONFLICT(usuario,chave) DO UPDATE SET valor=excluded.valor
            """, (usuario_destino, c["chave"], c["valor"]))
    _herdar_boxes(usuario_destino, usuario_origem)
    return len(regras)


def _usuarios_com_cadastro_proprio() -> list:
    """Usuários que já customizaram algo (fora o pool base) — candidatos pra herdar."""
    with get_db() as db:
        rows = db.execute("""
            SELECT DISTINCT usuario FROM adensamento_regras WHERE usuario != ''
            UNION
            SELECT DISTINCT usuario FROM adensamento_config WHERE usuario != ''
        """).fetchall()
    return sorted(r["usuario"] for r in rows)


def _usuarios_com_historico() -> list:
    """Usuários com pelo menos 1 processamento salvo — candidatos pra espelhar
    (mesma ideia do get_usuarios_com_dados() do painel principal, mas aqui é
    escopado ao módulo: /api/espelho/usuarios lista quem tem sessao_dados do
    Monitoramento, o que não tem nada a ver com quem processou algo aqui)."""
    with get_db() as db:
        rows = db.execute("SELECT DISTINCT usuario FROM adensamento_historico").fetchall()
    return sorted(r["usuario"] for r in rows)


# ── histórico de importações ───────────────────────────────────────────
def _salvar_historico(usuario, nome_arquivo, resultado):
    """Grava o resultado completo de um processamento (resumo + todos os
    paletes sugeridos) para poder reabrir depois — ver função de histórico."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    r = resultado["resumo"]
    peso_total = round(sum(p["peso_total"] for p in resultado["pallets"]), 3)
    # BRT explícito — o DEFAULT CURRENT_TIMESTAMP do SQLite é UTC, e o resto do
    # app (ex.: historico_temperatura, _now_brt_str) grava/exibe tudo em BRT.
    agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        db.execute("""
            INSERT INTO adensamento_historico
                (usuario, processado_em, nome_arquivo, total_caixas, paletes_origem,
                 paletes_sugeridos, peso_total, resumo_json, pallets_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (usuario, agora, nome_arquivo, r.get("total_caixas", 0), r.get("paletes_origem", 0),
              r.get("paletes_sugeridos", 0), peso_total,
              json.dumps(r, ensure_ascii=False),
              json.dumps(resultado["pallets"], ensure_ascii=False)))


def _listar_historico(usuario: str, limite=300):
    """Histórico é sandboxed: cada usuário só vê o próprio, a menos que
    esteja espelhando alguém (usuario aqui já vem resolvido como o
    usuário efetivo — ver _usuario_efetivo)."""
    with get_db() as db:
        rows = db.execute("""
            SELECT id, usuario, processado_em, nome_arquivo, total_caixas,
                   paletes_origem, paletes_sugeridos, peso_total
            FROM adensamento_historico WHERE usuario=? ORDER BY processado_em DESC LIMIT ?
        """, (usuario, limite)).fetchall()
    itens = []
    for r in rows:
        d = dict(r)
        d["peso_medio_palete"] = round(d["peso_total"] / d["paletes_sugeridos"], 1) \
            if d["paletes_sugeridos"] else 0
        itens.append(d)
    return itens


def _obter_historico(hist_id, usuario: str):
    """usuario = usuário efetivo (já resolvido via _usuario_efetivo). Só
    retorna o registro se pertencer a ele — impede acessar hist_id de outra
    pessoa por fora do espelhamento, só trocando o id na URL."""
    with get_db() as db:
        row = db.execute("SELECT * FROM adensamento_historico WHERE id=? AND usuario=?",
                         (hist_id, usuario)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["resumo"] = json.loads(d.pop("resumo_json") or "{}")
    d["pallets"] = json.loads(d.pop("pallets_json") or "[]")
    return d


# ── página ───────────────────────────────────────────────────────────
@adensamento_bp.route("/adensamento")
def pagina():
    from flask import redirect, url_for
    if not session.get("user"):
        return redirect(url_for("login_page"))
    if not tem_acesso_modulo("adensamento"):
        return redirect(url_for("index"))
    return render_template("adensamento.html", user=session.get("user"))


# ── cadastro / config (sempre do usuário REAL — não afetado por espelhar,
# ver decisão registrada no BACKLOG: mirroring só se aplica ao histórico) ──
@adensamento_bp.route("/api/adensamento/cadastro")
@login_required
@modulo_required("adensamento")
def cadastro_listar():
    usuario = str(session.get("user", "")).strip().lower()
    cadastro = _get_cadastro(usuario)
    cfg, _ = _get_config(usuario)
    alertas = _alertas.get(session.get("user"), set())
    regras = []
    for codigo, r in cadastro.items():
        d = dict(r)
        d["codigo"] = codigo
        d["alerta"] = codigo in alertas
        regras.append(d)
    # ordena: pendentes (sem cadastro) primeiro, depois alertas de peso, depois o resto
    regras.sort(key=lambda x: (bool(x["completo"]), not x["alerta"],
                               x["temperatura"] or "", x["nome"] or ""))
    pend = sum(1 for r in regras if not r["completo"])
    return jsonify({"regras": regras, "config": cfg, "pendencias": pend,
                    "alertas": sum(1 for r in regras if r["alerta"]),
                    "usuarios_para_herdar": [u for u in _usuarios_com_cadastro_proprio() if u != usuario]})


@adensamento_bp.route("/api/adensamento/cadastro", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@csrf_required
def cadastro_salvar():
    """Salva o grid inteiro de uma vez (um botão "Salvar" só, não um por
    linha) — recebe todas as linhas que estavam na tela (novas e editadas),
    mesma UX do cadastro de boxes."""
    usuario = str(session.get("user", "")).strip().lower()
    d = request.get_json(force=True) or {}
    linhas = d.get("linhas") or []
    n = _salvar_regras_lote(usuario, linhas)
    registrar_log(usuario, "adensamento_cadastro_salvar", detalhe=f"linhas={n}")
    return jsonify({"ok": True, "salvos": n})


@adensamento_bp.route("/api/adensamento/cadastro/excluir", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@csrf_required
def cadastro_excluir():
    """Exclui só os códigos marcados (checkbox) — recebe a lista de códigos
    selecionados, não um por vez."""
    usuario = str(session.get("user", "")).strip().lower()
    d = request.get_json(force=True) or {}
    codigos = d.get("codigos") or []
    n = _excluir_regras_lote(usuario, codigos)
    registrar_log(usuario, "adensamento_cadastro_excluir", detalhe=f"linhas={n}")
    return jsonify({"ok": True, "excluidos": n})


@adensamento_bp.route("/api/adensamento/config", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@csrf_required
def config_salvar():
    usuario = str(session.get("user", "")).strip().lower()
    d = request.get_json(force=True)
    with get_db() as db:
        for ch in _CONFIG_PADRAO:
            if ch in d:
                db.execute("INSERT INTO adensamento_config(usuario,chave,valor) VALUES(?,?,?) "
                           "ON CONFLICT(usuario,chave) DO UPDATE SET valor=excluded.valor",
                           (usuario, ch, str(d[ch])))
    return jsonify({"ok": True})


@adensamento_bp.route("/api/adensamento/cadastro/herdar", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@csrf_required
def cadastro_herdar():
    """Copia o cadastro/config de outro usuário (ou '' = pool base/padrão)
    para o sandbox do usuário atual, sobrescrevendo o que ele já tinha."""
    usuario = str(session.get("user", "")).strip().lower()
    d = request.get_json(force=True) or {}
    origem = str(d.get("usuario_origem", "")).strip().lower()
    if origem == usuario:
        return jsonify({"error": "Não é possível herdar de si mesmo."}), 400
    n = _herdar_cadastro(usuario, origem)
    registrar_log(usuario, "adensamento_herdar_cadastro",
                  alvo=origem or "(pool base)", detalhe=f"importados={n}")
    return jsonify({"ok": True, "importados": n})


# ── modelo de importação (xlsx) ──────────────────────────────────────
@adensamento_bp.route("/api/adensamento/cadastro/modelo")
@login_required
@modulo_required("adensamento")
def cadastro_modelo():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    usuario = str(session.get("user", "")).strip().lower()
    cadastro = _get_cadastro(usuario)
    regras = sorted(
        ({"codigo": cod, **info} for cod, info in cadastro.items()),
        key=lambda r: (r["temperatura"] or "", r["nome"] or "")
    )
    cfg, _ = _get_config(usuario)

    A = lambda **k: Font(name="Arial", **k)
    head = PatternFill("solid", fgColor="1F3864")
    yel = PatternFill("solid", fgColor="FFF2CC")
    gry = PatternFill("solid", fgColor="EDEDED")
    thin = Side(style="thin", color="BFBFBF")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    ctr = Alignment(horizontal="center", vertical="center")

    wb = Workbook()
    ws = wb.active; ws.title = "Cadastro"
    ws["A1"] = "Cadastro de Adensamento — Limites por Código"
    ws["A1"].font = A(bold=True, size=14, color="1F3864"); ws.merge_cells("A1:E1")
    ws["A2"] = ("Preencha/revise as colunas AMARELAS (Qtd e Peso máximos por palete de cada código). "
                "Códigos com 0 estão pendentes de cadastro.")
    ws["A2"].font = A(italic=True, size=9, color="808080"); ws.merge_cells("A2:E2")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top"); ws.row_dimensions[2].height = 28
    hdr = ["Código", "Nome do Produto", "Temperatura",
           "Qtd Máx Caixas / Palete", "Peso Máx / Palete (kg)"]
    for c, h in enumerate(hdr, 1):
        cell = ws.cell(4, c, h); cell.font = A(bold=True, color="FFFFFF", size=10)
        cell.fill = head; cell.alignment = ctr; cell.border = bd
    for i, r in enumerate(regras):
        rr = 5 + i
        for c, v in enumerate([r["codigo"], r["nome"], r["temperatura"],
                               r["qtd_max"], round(r["peso_max"], 1)], 1):
            cell = ws.cell(rr, c, v); cell.font = A(size=10); cell.border = bd
            cell.alignment = ctr if c != 2 else Alignment(vertical="center")
            cell.fill = yel if c in (4, 5) else gry
            if c == 5:
                cell.number_format = "#,##0.0"
    for c, w in enumerate([12, 52, 15, 24, 22], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A5"

    ws2 = wb.create_sheet("Config_Temperatura")
    ws2["A1"] = "Janela de data por temperatura (dias) e palete misto"
    ws2["A1"].font = A(bold=True, size=13, color="1F3864"); ws2.merge_cells("A1:B1")
    linhas = [("Temperatura", "Range de Dias"),
              ("AMBIENTE", cfg.get("range_ambiente", "5")),
              ("RESFRIADO", cfg.get("range_resfriado", "5")),
              ("CONGELADO", cfg.get("range_congelado", "30")),
              ("", ""),
              ("Qtd Máx Caixas / Palete Misto", cfg.get("misto_qtd_max", "25")),
              ("Peso Máx / Palete Misto (kg)", cfg.get("misto_peso_max", "0")),
              ("Limiar de sobra p/ misto (%)", cfg.get("misto_threshold_pct", "60"))]
    for i, (a, b) in enumerate(linhas):
        r = 3 + i
        ca = ws2.cell(r, a and r, a) if False else ws2.cell(r, 1, a)
        ca.font = A(bold=(i == 0), size=10, color="FFFFFF" if i == 0 else "000000")
        cb = ws2.cell(r, 2, b); cb.font = A(bold=(i == 0), size=10,
                                            color="FFFFFF" if i == 0 else "000000")
        if i == 0:
            ca.fill = head; cb.fill = head; ca.alignment = ctr; cb.alignment = ctr
        elif a:
            cb.fill = yel; cb.border = bd; cb.alignment = ctr; ca.border = bd
    ws2.column_dimensions["A"].width = 30; ws2.column_dimensions["B"].width = 16

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="cadastro_adensamento_modelo.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@adensamento_bp.route("/api/adensamento/cadastro/importar", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@rate_limited("adensamento_cadastro_importar", max_chamadas=5, janela_seg=60)
@csrf_required
def cadastro_importar():
    import pandas as pd
    usuario = str(session.get("user", "")).strip().lower()
    f = request.files.get("arquivo")
    if not f or not f.filename:
        return jsonify({"error": "Envie a planilha de cadastro."}), 400
    try:
        xls = pd.ExcelFile(io.BytesIO(f.read()))
        df = pd.read_excel(xls, sheet_name="Cadastro", header=3)
        col = {c.lower().strip(): c for c in df.columns}
        def pick(*names):
            for n in names:
                for k, v in col.items():
                    if n in k:
                        return v
            return None
        c_cod = pick("código", "codigo")
        c_nome = pick("nome")
        c_temp = pick("temperatura")
        c_qtd = pick("qtd")
        c_peso = pick("peso")
        n = 0
        for _, row in df.iterrows():
            cod = str(row.get(c_cod, "")).strip()
            if not cod or cod.lower() == "nan":
                continue
            cod = cod.split(".")[0]  # evita 385231.0
            _upsert_regra(usuario, cod, str(row.get(c_nome, "") or ""),
                          str(row.get(c_temp, "") or ""),
                          row.get(c_qtd, 0), row.get(c_peso, 0))
            n += 1
        # aba de config (opcional)
        if "Config_Temperatura" in xls.sheet_names:
            cfgdf = pd.read_excel(xls, sheet_name="Config_Temperatura", header=None)
            mapa = {}
            for _, r in cfgdf.iterrows():
                a = str(r[0]).lower(); b = r[1]
                if "ambiente" in a: mapa["range_ambiente"] = b
                elif "resfriado" in a: mapa["range_resfriado"] = b
                elif "congelado" in a: mapa["range_congelado"] = b
                elif "misto" in a and "caixa" in a: mapa["misto_qtd_max"] = b
                elif "misto" in a and "peso" in a: mapa["misto_peso_max"] = b
                elif "limiar" in a or "threshold" in a: mapa["misto_threshold_pct"] = b
            if mapa:
                with get_db() as db:
                    for ch, v in mapa.items():
                        try: v = str(int(float(v)))
                        except Exception: continue
                        db.execute("INSERT INTO adensamento_config(usuario,chave,valor) VALUES(?,?,?) "
                                   "ON CONFLICT(usuario,chave) DO UPDATE SET valor=excluded.valor", (usuario, ch, v))
        return jsonify({"ok": True, "importados": n})
    except Exception as e:
        return jsonify({"error": f"Falha ao ler a planilha: {e}"}), 400


# ── cadastro de boxes (posições com capacidade reduzida) ──────────────
@adensamento_bp.route("/api/adensamento/boxes")
@login_required
@modulo_required("adensamento")
def boxes_listar():
    usuario = str(session.get("user", "")).strip().lower()
    return jsonify({"linhas": _get_boxes_lista(usuario)})


@adensamento_bp.route("/api/adensamento/boxes", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@csrf_required
def boxes_salvar():
    """Salva o grid inteiro de uma vez (um botão "Salvar" só, não um por
    linha) — recebe todas as linhas que estavam na tela (novas e editadas)."""
    usuario = str(session.get("user", "")).strip().lower()
    d = request.get_json(force=True) or {}
    linhas = d.get("linhas") or []
    n = _salvar_boxes_lote(usuario, linhas)
    registrar_log(usuario, "adensamento_boxes_salvar", detalhe=f"linhas={n}")
    return jsonify({"ok": True, "salvos": n})


@adensamento_bp.route("/api/adensamento/boxes/excluir", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@csrf_required
def boxes_excluir():
    """Exclui só as linhas marcadas (checkbox) — recebe a lista de chaves
    selecionadas, não uma posição por vez."""
    usuario = str(session.get("user", "")).strip().lower()
    d = request.get_json(force=True) or {}
    chaves = d.get("chaves") or []
    n = _excluir_boxes_lote(usuario, chaves)
    registrar_log(usuario, "adensamento_boxes_excluir", detalhe=f"linhas={n}")
    return jsonify({"ok": True, "excluidos": n})


@adensamento_bp.route("/api/adensamento/boxes/modelo")
@login_required
@modulo_required("adensamento")
def boxes_modelo():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    usuario = str(session.get("user", "")).strip().lower()
    linhas = _get_boxes_lista(usuario)

    A = lambda **k: Font(name="Arial", **k)
    head = PatternFill("solid", fgColor="1F3864")
    yel = PatternFill("solid", fgColor="FFF2CC")
    thin = Side(style="thin", color="BFBFBF")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    ctr = Alignment(horizontal="center", vertical="center")

    wb = Workbook()
    ws = wb.active; ws.title = "Boxes"
    ws["A1"] = "Cadastro de Boxes — Posições com Capacidade Reduzida"
    ws["A1"].font = A(bold=True, size=14, color="1F3864"); ws.merge_cells("A1:D1")
    ws["A2"] = ("Preencha uma linha por posição física (câmara + box + altura) que NÃO "
                "comporta a quantidade cheia (ex.: meia posição). Não precisa listar "
                "posições normais — só as com exceção.")
    ws.merge_cells("A2:D2")
    for c, h in enumerate(["Câmara", "Box", "Altura", "Quantidade Permitida (caixas)"], 1):
        cell = ws.cell(4, c, h); cell.font = A(bold=True, size=10, color="FFFFFF")
        cell.fill = head; cell.alignment = ctr; cell.border = bd
    for i, r in enumerate(linhas):
        rr = 5 + i
        for c, v in enumerate([r["camara"], r["box"], r["altura"], r["quantidade"]], 1):
            cell = ws.cell(rr, c, v); cell.font = A(size=10); cell.border = bd
            cell.alignment = ctr
            if c == 4: cell.fill = yel
    for c, w in enumerate([16, 16, 12, 26], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A5"

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="cadastro_boxes_modelo.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@adensamento_bp.route("/api/adensamento/boxes/importar", methods=["POST"])
@login_required
@modulo_required("adensamento")
@bloquear_escrita_exceto_operador
@rate_limited("adensamento_boxes_importar", max_chamadas=5, janela_seg=60)
@csrf_required
def boxes_importar():
    import pandas as pd
    usuario = str(session.get("user", "")).strip().lower()
    f = request.files.get("arquivo")
    if not f or not f.filename:
        return jsonify({"error": "Envie a planilha de boxes."}), 400
    try:
        xls = pd.ExcelFile(io.BytesIO(f.read()))
        df = pd.read_excel(xls, sheet_name="Boxes", header=3)
        col = {c.lower().strip(): c for c in df.columns}
        def pick(*names):
            for n in names:
                for k, v in col.items():
                    if n in k:
                        return v
            return None
        c_cam = pick("câmara", "camara")
        c_box = pick("box")
        c_alt = pick("altura")
        c_qtd = pick("quantidade", "qtd")
        n = 0
        for _, row in df.iterrows():
            camara = str(row.get(c_cam, "")).strip()
            box = str(row.get(c_box, "")).strip()
            altura = str(row.get(c_alt, "")).strip()
            if altura.endswith(".0"):
                altura = altura[:-2]  # evita "1.0" quando a coluna vira numérica
            if not (camara and box and altura) or camara.lower() == "nan":
                continue
            if _upsert_box(usuario, camara, box, altura, row.get(c_qtd, 0)):
                n += 1
        return jsonify({"ok": True, "importados": n})
    except Exception as e:
        return jsonify({"error": f"Falha ao ler a planilha: {e}"}), 400


# ── processar TXT ────────────────────────────────────────────────────
@adensamento_bp.route("/api/adensamento/processar", methods=["POST"])
@login_required
@modulo_required("adensamento")
@rate_limited("adensamento_processar", max_chamadas=5, janela_seg=60)
@csrf_required
def processar():
    # Operador tem passe livre aqui mesmo sendo só-leitura no resto do
    # sistema — pedido do usuário 24/08/2026 ("o operador pode... subir
    # TXT no módulo adensamento"). Qualidade continua bloqueado.
    if somente_leitura_exceto_operador():
        return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
    usuario = str(session.get("user", "")).strip().lower()
    f = request.files.get("relatorio")
    if not f or not f.filename:
        return jsonify({"error": "Envie o relatório TXT (Produto Paletizado)."}), 400
    try:
        tmp = Path(tempfile.mkdtemp()) / "relatorio.txt"
        f.save(tmp)
        caixas = parse_txt(str(tmp))
        if not caixas:
            return jsonify({"error": "Nenhuma caixa válida encontrada no TXT."}), 400
        # sobe códigos novos para o cadastro (pendências) — vão pro sandbox
        # do próprio usuário, não pro pool base
        vistos = {}
        for c in caixas:
            vistos.setdefault(c["codigo"], c)
        _inserir_codigos_novos(usuario, [{"codigo": k, "nome": v["nome"],
                                          "temperatura": v["temperatura"]}
                                         for k, v in vistos.items()])
        cadastro = _get_cadastro(usuario)
        _, cfg_num = _get_config(usuario)
        boxes = _get_boxes(usuario)
        resultado = montar_adensamento(caixas, cadastro, cfg_num, boxes)
        _ultimo[usuario] = resultado["pallets"]
        _alertas[usuario] = set(resultado.get("alertas_codigos", []))
        try:
            _salvar_historico(usuario, f.filename, resultado)
        except Exception as e:
            print(f"[Adensamento] Erro ao salvar histórico: {e}")
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": f"Erro ao processar: {e}"}), 500


# ── espelhamento (lista própria — /api/espelho/usuarios é do Monitoramento,
# filtra por sessao_dados, o que não tem nada a ver com quem usou este módulo) ──
@adensamento_bp.route("/api/adensamento/espelho/usuarios")
@login_required
@modulo_required("adensamento")
def espelho_usuarios():
    eu = str(session.get("user", "")).strip().lower()
    return jsonify({"usuarios": [u for u in _usuarios_com_historico() if u != eu]})


# ── histórico (rotas) — sandboxed, respeita espelhamento ──────────────
@adensamento_bp.route("/api/adensamento/historico")
@login_required
@modulo_required("adensamento")
def historico_listar():
    return jsonify({"itens": _listar_historico(_usuario_efetivo())})


@adensamento_bp.route("/api/adensamento/historico/<int:hist_id>")
@login_required
@modulo_required("adensamento")
def historico_abrir(hist_id):
    item = _obter_historico(hist_id, _usuario_efetivo())
    if not item:
        return jsonify({"error": "Registro não encontrado."}), 404
    return jsonify(item)


# ── exportar Excel do resultado atual ────────────────────────────────
@adensamento_bp.route("/api/adensamento/exportar_excel", methods=["POST"])
@login_required
@modulo_required("adensamento")
@csrf_required
def exportar_excel():
    import pandas as pd
    pallets = _ultimo.get(session.get("user"))
    if not pallets:
        return jsonify({"error": "Processe um relatório antes de exportar."}), 400

    PERIGO = ("=", "+", "-", "@")
    def _safe(v):
        return "'" + v if isinstance(v, str) and v.startswith(PERIGO) else v

    linhas_resumo, linhas_det = [], []
    for pl in pallets:
        linhas_resumo.append({
            "Pallet Destino": pl["pallet_destino"], "Tipo": pl["tipo"],
            "Ação": "Empilhar" if pl["acao"] else "Fica como está",
            "Local": pl["local_nome"], "Temperatura": pl["temperatura"],
            "Câmara": pl["camara"], "Rua": pl.get("rua", ""), "Código": pl["codigo"], "Produto": pl["nome"],
            "Qtd Caixas": pl["qtd_caixas"], "Peso Total (kg)": pl["peso_total"],
            "Produção Ini": pl["producao_min"], "Produção Fim": pl["producao_max"],
            "Nº Paletes Origem": pl["n_origens"],
            "Empilhar (de baixo para cima: base ↑ … ↑ topo)": pl["origens_capa"],
        })
        for cx in pl["caixas"]:
            linhas_det.append({
                "Pallet Destino": pl["pallet_destino"], "Tipo": pl["tipo"],
                "CB": cx["cb"], "Código": cx["codigo"], "Produto": cx["nome"],
                "Endereço Origem": cx["endereco_origem"],
                "Câmara": cx["camara"], "Box": cx["box"], "Altura": cx["altura"],
                "Rua": cx["rua"], "Palete Origem": cx["palete_origem"],
                "Peso (kg)": cx["peso"], "Produção": cx["producao"],
            })
    def _sanitizar(df):
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].apply(_safe)
        return df
    df_r = _sanitizar(pd.DataFrame(linhas_resumo))
    df_d = _sanitizar(pd.DataFrame(linhas_det))
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df_r.to_excel(w, index=False, sheet_name="Paletes Sugeridos")
        df_d.to_excel(w, index=False, sheet_name="Detalhe Caixas")
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="adensamento_sugestao.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
