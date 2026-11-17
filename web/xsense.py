"""
Blueprint do módulo Sensores — monitoramento de câmaras frias
(ver xsense-monitoramento-spec.md na raiz do projeto).

Registra em server.py com:
    from web.xsense import xsense_bp, init_xsense_db
    init_xsense_db()
    app.register_blueprint(xsense_bp)

Mesmo padrão de web/adensamento.py e web/armazem.py: usa o mesmo banco
(data/monitoramento.db) e duplica auth/CSRF/módulo por sessão (evita
import circular com server.py).

Status (14/08/2026): em produção — schema, motor de detecção, CRUD,
dashboard, coleta automática (scripts/coletar_xsense.py, via cron) e
alertas por Telegram (bot com comandos self-service + webhook) rodando.
Não existe mais endpoint de lançamento manual de leitura de propósito —
removido a pedido do usuário (14/08/2026): permitir alguém inserir uma
leitura à mão abriria brecha pra mascarar um problema real de verdade
(digitar um valor "normal" por cima de uma excursão real). Toda leitura
tem que vir do sensor de verdade, via processar_leitura() chamado só
pelo coletor automático.
Pendente: WhatsApp (bloqueado até o usuário resolver a conta WhatsApp
Business API — sem CNPJ disponível até agora).
"""
from __future__ import annotations
import hmac
import threading
from functools import wraps

from flask import Blueprint, render_template, request, jsonify, session

from app.database import (
    get_db, registrar_log, get_xsense_dashboard_params, set_xsense_dashboard_params,
)
from app.services.xsense_service import (
    ConfigCamara, EstadoCamara, Leitura, avaliar, aplicar_cooldown, agora_brt_str, fmt_duracao_min,
)
from app.utils.access_control import modulo_required, tem_acesso_modulo, cd_do_usuario

xsense_bp = Blueprint("xsense", __name__)

DIMENSOES = ("temperatura", "umidade")
SEVERIDADES = ("HIGH", "MEDIUM", "LOW", "INFO")
# Limite original da spec (14/08/2026) era 8 — fazia sentido com 1 CD só e
# cadastro 100% manual pelo admin. Com o fluxo de solicitação por CD
# (26/08/2026) e vários CDs, esse número ficaria curto rápido — aumentado
# bastante, mantido como teto de segurança (evita crescimento sem controle),
# não como limite operacional real.
MAX_DESTINATARIOS = 300


# ── auth (duplicado do padrão server.py/adensamento.py/armazem.py) ────
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


def admin_required(f):
    """Configurar câmaras/destinatários mexe em quem recebe alerta (inclusive
    número de WhatsApp) — mesmo padrão de admin_required em server.py pras
    telas de configuração de OpenTech/Ravex/CD."""
    @wraps(f)
    def d(*a, **k):
        if not session.get("user"):
            return jsonify({"error": "Não autenticado"}), 401
        if session.get("perfil") not in ("admin", "master"):
            return jsonify({"error": "Acesso restrito ao Admin"}), 403
        return f(*a, **k)
    return d


# ── banco ────────────────────────────────────────────────────────────
def init_xsense_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS xsense_camaras (
            id                TEXT PRIMARY KEY,
            nome              TEXT NOT NULL,
            provider          TEXT NOT NULL DEFAULT 'xsense',
            provider_ref      TEXT,
            ativo             INTEGER NOT NULL DEFAULT 1,
            temp_min          REAL NOT NULL,
            temp_max          REAL NOT NULL,
            temp_limite_duro  REAL,
            umid_min          REAL,
            umid_max          REAL,
            n_sustentado      INTEGER NOT NULL DEFAULT 2,
            k_oscilacao       INTEGER NOT NULL DEFAULT 4,
            w_janela_min      INTEGER NOT NULL DEFAULT 120,
            cooldown_min      INTEGER NOT NULL DEFAULT 60,
            m_recuperacao     INTEGER NOT NULL DEFAULT 2,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xsense_leituras (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            camara_id     TEXT NOT NULL,
            dimensao      TEXT NOT NULL DEFAULT 'temperatura',
            provider      TEXT NOT NULL DEFAULT 'xsense',
            ts            TEXT NOT NULL,
            valor         REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_xsense_leituras_camara_ts
            ON xsense_leituras(camara_id, dimensao, ts DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_xsense_leituras_camara_dim_ts
            ON xsense_leituras(camara_id, dimensao, ts);
        CREATE TABLE IF NOT EXISTS xsense_estado (
            camara_id         TEXT NOT NULL,
            dimensao          TEXT NOT NULL,
            estado            TEXT NOT NULL DEFAULT 'NORMAL',
            condicao_ativa    TEXT,
            severidade        TEXT,
            desde_ts          TEXT,
            ultima_leitura_ts TEXT,
            ultimo_alerta_ts  TEXT,
            contador_fora     INTEGER NOT NULL DEFAULT 0,
            contador_dentro   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (camara_id, dimensao)
        );
        CREATE TABLE IF NOT EXISTS xsense_eventos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            camara_id    TEXT NOT NULL,
            dimensao     TEXT NOT NULL,
            tipo         TEXT NOT NULL,
            severidade   TEXT NOT NULL,
            ts_inicio    TEXT NOT NULL,
            valor        REAL,
            detalhe      TEXT DEFAULT '',
            notificado   INTEGER NOT NULL DEFAULT 0,
            canais       TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_xsense_eventos_camara_ts
            ON xsense_eventos(camara_id, ts_inicio DESC);
        CREATE TABLE IF NOT EXISTS xsense_destinatarios (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nome             TEXT NOT NULL,
            telegram_chat_id TEXT DEFAULT '',
            whatsapp_num     TEXT DEFAULT '',
            severidade_min   TEXT NOT NULL DEFAULT 'MEDIUM',
            ativo            INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS xsense_destinatario_camara (
            destinatario_id INTEGER NOT NULL,
            camara_id       TEXT NOT NULL,
            PRIMARY KEY (destinatario_id, camara_id)
        );
        -- Resumo de 8 em 8 horas (09/10/2026): uma linha por fechamento. A
        -- linha é gravada ANTES do envio — o cron roda a cada 5 min e dois
        -- ciclos sobrepostos não podem mandar o mesmo resumo duas vezes.
        CREATE TABLE IF NOT EXISTS xsense_resumo_envio (
            slot        TEXT PRIMARY KEY,
            enviado_em  TEXT,
            entregues   INTEGER NOT NULL DEFAULT 0,
            elegiveis   INTEGER NOT NULL DEFAULT 0
        );
        """)
        # status/local_solicitado — fluxo de aprovação por CD (26/08/2026):
        # /start no bot não ativa mais na hora, pergunta o Local e cadastra
        # como 'pendente'; só passa a receber alerta depois que o
        # coordenador/analista daquele Local (ou um gerente) aprova.
        # Registros que já existiam antes dessa migração são gente
        # cadastrada manualmente pelo admin — considerada já aprovada
        # (senão parava de receber alerta do nada numa atualização).
        cols_dest = [r[1] for r in db.execute("PRAGMA table_info(xsense_destinatarios)").fetchall()]
        if "status" not in cols_dest:
            db.execute("ALTER TABLE xsense_destinatarios ADD COLUMN status TEXT NOT NULL DEFAULT 'aprovado'")
            print("[DB] Migração: coluna status adicionada em xsense_destinatarios")
        if "local_solicitado" not in cols_dest:
            db.execute("ALTER TABLE xsense_destinatarios ADD COLUMN local_solicitado TEXT NOT NULL DEFAULT ''")
            print("[DB] Migração: coluna local_solicitado adicionada em xsense_destinatarios")
        # eh_gerente — substitui a regra implícita "sem filtro de câmara =
        # vê tudo" por uma flag explícita (achado ao vivo 26/08/2026: quem
        # tinha câmara-filtro vazio ganhava acesso AUTOMÁTICO a qualquer CD
        # novo cadastrado depois, sem ninguém decidir isso de propósito —
        # bug de autorização, não só de UX). Migração backfilla eh_gerente=1
        # pra quem JÁ estava nesse estado "sem filtro" (preserva o
        # comportamento de quem usava assim até aqui, ex. usuario) — daqui
        # pra frente só quem for marcado explicitamente vê tudo.
        if "eh_gerente" not in cols_dest:
            db.execute("ALTER TABLE xsense_destinatarios ADD COLUMN eh_gerente INTEGER NOT NULL DEFAULT 0")
            db.execute("""
                UPDATE xsense_destinatarios SET eh_gerente=1
                WHERE local_solicitado='' AND id NOT IN (SELECT destinatario_id FROM xsense_destinatario_camara)
            """)
            print("[DB] Migração: coluna eh_gerente adicionada em xsense_destinatarios "
                 "(backfill pra quem já via tudo sem filtro)")
        # rotulo — apelido de exibição por câmara (pode divergir do nome
        # cadastrado no Sensores, ex. "Antecâmara 2/1" -> "Antecâmara"; vazio
        # = usa o nome original). Migração pra quem já tinha a tabela criada.
        cols = [r[1] for r in db.execute("PRAGMA table_info(xsense_camaras)").fetchall()]
        if "rotulo" not in cols:
            db.execute("ALTER TABLE xsense_camaras ADD COLUMN rotulo TEXT NOT NULL DEFAULT ''")
            print("[DB] Migração: coluna rotulo adicionada em xsense_camaras")
        # alertas_ativos — diferente de "ativo": a câmara continua sendo
        # monitorada (leitura, gráfico, temperatura no card), só não entra
        # em EM_OBSERVACAO/ALERTANDO nem gera push. Pedido do usuário
        # (12/08/2026) pra câmaras como Antecâmara, que variam mais e não
        # merecem alerta.
        if "alertas_ativos" not in cols:
            db.execute("ALTER TABLE xsense_camaras ADD COLUMN alertas_ativos INTEGER NOT NULL DEFAULT 1")
            print("[DB] Migração: coluna alertas_ativos adicionada em xsense_camaras")
        # tipo — categoria de temperatura pro filtro do painel (congelada/
        # resfriada/antecamara/ambiente). Pedido do usuário (14/08/2026).
        # Pra quem sincronizou do Sensores depois dessa migração, vem
        # preenchido automaticamente (mapeado do "type"/descrição do
        # portal); pra câmara já cadastrada antes, tenta adivinhar pelo
        # nome/faixa uma vez só (o admin pode corrigir editando a câmara).
        if "tipo" not in cols:
            db.execute("ALTER TABLE xsense_camaras ADD COLUMN tipo TEXT NOT NULL DEFAULT ''")
            print("[DB] Migração: coluna tipo adicionada em xsense_camaras")
            for row in db.execute("SELECT id, nome, temp_min, temp_max FROM xsense_camaras").fetchall():
                nome_baixo = (row["nome"] or "").lower()
                if "ante" in nome_baixo:
                    tipo = "antecamara"
                elif "ambiente" in nome_baixo:
                    tipo = "ambiente"
                elif row["temp_max"] is not None and row["temp_max"] <= -10:
                    tipo = "congelada"
                elif row["temp_min"] is not None and row["temp_min"] >= -2:
                    tipo = "resfriada"
                else:
                    tipo = ""
                if tipo:
                    db.execute("UPDATE xsense_camaras SET tipo=? WHERE id=?", (tipo, row["id"]))
        # local — nome da instalação/CD no Sensores (ex.: "CDA - Cidade
        # - UF"), pra filtro + etiqueta no card (3 primeiras letras). Hoje é
        # igual pra toda câmara (só uma instalação), mas guardado por
        # câmara pra já ficar pronto se um dia monitorar mais de um CD/
        # empresa (pedido do usuário 14/08/2026). Vem preenchido sozinho na
        # sincronização; câmara manual fica em branco até o admin editar.
        if "local" not in cols:
            db.execute("ALTER TABLE xsense_camaras ADD COLUMN local TEXT NOT NULL DEFAULT ''")
            print("[DB] Migração: coluna local adicionada em xsense_camaras")
        # node_id — qual instalação (nó da árvore do portal de sensores) essa
        # câmara pertence. Faltava desde sempre porque só existia 1
        # instalação (CDA); descoberto 26/08/2026 que isso também travava a
        # COLETA DE LEITURA (não só a listagem) numa instalação só — o
        # coletor mandava sempre o mesmo node_id global, então câmara de
        # outro CD nunca ia trazer leitura de verdade, mesmo importada.
        # Câmara já existente sem node_id (todas eram de CDA até aqui) cai
        # no fallback do node_id salvo em Configurações — não quebra nada.
        if "node_id" not in cols:
            db.execute("ALTER TABLE xsense_camaras ADD COLUMN node_id TEXT NOT NULL DEFAULT ''")
            print("[DB] Migração: coluna node_id adicionada em xsense_camaras")


# ── câmaras (CRUD) ──────────────────────────────────────────────────
def _camara_to_dict(row) -> dict:
    return dict(row)


def _config_camara(row, dimensao: str = "temperatura") -> ConfigCamara:
    """Monta a config do motor de detecção pra UMA dimensão. Cuidado: antes
    disso o mesmo cfg (com faixa de temperatura) era usado pra avaliar
    umidade também — descoberto ao vivo (13/08/2026) quando 67% de
    umidade virou "EXCURSAO" por estar fora da faixa de 0-4°C da câmara.
    Limite crítico (temp_limite_duro) só existe pra temperatura mesmo."""
    if dimensao == "umidade":
        return ConfigCamara(
            id=row["id"], nome=row["nome"], minimo=row["umid_min"], maximo=row["umid_max"],
            limite_duro=None, n_sustentado=row["n_sustentado"],
            k_oscilacao=row["k_oscilacao"], w_janela_min=row["w_janela_min"],
            cooldown_min=row["cooldown_min"], m_recuperacao=row["m_recuperacao"],
        )
    return ConfigCamara(
        id=row["id"], nome=row["nome"], minimo=row["temp_min"], maximo=row["temp_max"],
        limite_duro=row["temp_limite_duro"], n_sustentado=row["n_sustentado"],
        k_oscilacao=row["k_oscilacao"], w_janela_min=row["w_janela_min"],
        cooldown_min=row["cooldown_min"], m_recuperacao=row["m_recuperacao"],
    )


@xsense_bp.route("/xsense")
def pagina():
    from flask import redirect, url_for
    if not session.get("user"):
        return redirect(url_for("login_page"))
    if not tem_acesso_modulo("xsense"):
        return redirect(url_for("index"))
    return render_template("xsense.html", user=session.get("user"))


@xsense_bp.route("/xsense/dashboard-mensal")
def pagina_dashboard_mensal():
    from flask import redirect, url_for
    if not session.get("user"):
        return redirect(url_for("login_page"))
    if not tem_acesso_modulo("xsense"):
        return redirect(url_for("index"))
    return render_template("xsense_dashboard_mensal.html", user=session.get("user"))


@xsense_bp.route("/api/xsense/camaras", methods=["GET", "POST"])
@login_required
@modulo_required("xsense")
@csrf_required
def camaras():
    if request.method == "GET":
        # Recorte por CD: o usuário de um CD não enxerga câmara de outro
        # (apontado pelo usuário 06/10/2026 — o site mostrava todas, mesmo
        # com o bot do Telegram já limitando por CD; risco de LGPD).
        with get_db() as db:
            rows = db.execute("SELECT * FROM xsense_camaras ORDER BY nome").fetchall()
            permitidos = _locais_permitidos_sessao(db)
        visiveis = [r for r in rows if _camara_visivel(r, permitidos)]
        # Câmara sem CD cadastrado fica PENDENTE: invisível para perfil
        # restrito e listada para o admin, que é quem cadastra o CD dela.
        pendentes = [r["nome"] for r in rows
                     if not str((r["local"] if "local" in r.keys() else "") or "").strip()]
        return jsonify({"camaras": [_camara_to_dict(r) for r in visiveis],
                        "restrito": permitidos is not None,
                        "ocultas": len(rows) - len(visiveis),
                        "pendentes": pendentes if permitidos is None else []})

    if session.get("perfil") not in ("admin", "master"):
        return jsonify({"error": "Acesso restrito ao Admin"}), 403
    d = request.json or {}
    cid = str(d.get("id", "")).strip()
    nome = str(d.get("nome", "")).strip()
    if not cid or not nome:
        return jsonify({"error": "Informe id e nome da câmara."}), 400
    try:
        temp_min = float(d["temp_min"]); temp_max = float(d["temp_max"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "temp_min e temp_max são obrigatórios (numéricos)."}), 400
    if temp_min >= temp_max:
        return jsonify({"error": "temp_min precisa ser menor que temp_max."}), 400

    def _f(campo):
        v = d.get(campo)
        return float(v) if v not in (None, "") else None

    def _i(campo, default):
        v = d.get(campo)
        try:
            return int(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    alertas_ativos = 1 if d.get("alertas_ativos", True) else 0
    tipo = str(d.get("tipo", "")).strip().lower()
    if tipo not in ("", "congelada", "resfriada", "antecamara", "ambiente"):
        tipo = ""
    local = str(d.get("local", "")).strip()
    # node_id — instalação do portal essa câmara pertence (corrigido
    # 26/08/2026, ver sincronizar_camaras); só vem preenchido quando cria/
    # atualiza via importação da sincronização. Editar manualmente sem
    # mandar node_id preserva o que já estava salvo (COALESCE), não zera —
    # diferente de "local", que sempre foi sobrescrito pelo form de editar.
    node_id_novo = d.get("node_id")
    with get_db() as db:
        db.execute("""
            INSERT INTO xsense_camaras
                (id, nome, rotulo, provider, provider_ref, ativo, alertas_ativos, tipo, local, node_id, temp_min, temp_max, temp_limite_duro,
                 umid_min, umid_max, n_sustentado, k_oscilacao, w_janela_min, cooldown_min, m_recuperacao, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
              nome=excluded.nome, rotulo=excluded.rotulo, provider=excluded.provider, provider_ref=excluded.provider_ref,
              ativo=excluded.ativo, alertas_ativos=excluded.alertas_ativos, tipo=excluded.tipo, local=excluded.local,
              node_id=COALESCE(NULLIF(excluded.node_id,''), xsense_camaras.node_id),
              temp_min=excluded.temp_min, temp_max=excluded.temp_max,
              temp_limite_duro=excluded.temp_limite_duro, umid_min=excluded.umid_min, umid_max=excluded.umid_max,
              n_sustentado=excluded.n_sustentado, k_oscilacao=excluded.k_oscilacao,
              w_janela_min=excluded.w_janela_min, cooldown_min=excluded.cooldown_min,
              m_recuperacao=excluded.m_recuperacao, updated_at=CURRENT_TIMESTAMP
        """, (cid, nome, str(d.get("rotulo", "")).strip(), str(d.get("provider", "xsense")), d.get("provider_ref"),
              1 if d.get("ativo", True) else 0, alertas_ativos, tipo, local, node_id_novo or "", temp_min, temp_max,
              _f("temp_limite_duro"), _f("umid_min"), _f("umid_max"), _i("n_sustentado", 2), _i("k_oscilacao", 4),
              _i("w_janela_min", 120), _i("cooldown_min", 60), _i("m_recuperacao", 2)))
        if not alertas_ativos:
            # zera qualquer estado de alerta pendente na hora — senão o card
            # ficava mostrando ALERTANDO até a próxima leitura só pra "descobrir"
            # que os alertas foram desligados nesse meio tempo
            db.execute("""
                UPDATE xsense_estado SET estado='NORMAL', condicao_ativa=NULL, severidade=NULL
                WHERE camara_id=?
            """, (cid,))
    registrar_log(session.get("user", ""), "xsense_salvar_camara", alvo=cid)
    return jsonify({"ok": True})


@xsense_bp.route("/api/xsense/camaras/<camara_id>", methods=["DELETE"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def camara_excluir(camara_id):
    with get_db() as db:
        db.execute("DELETE FROM xsense_camaras WHERE id=?", (camara_id,))
        db.execute("DELETE FROM xsense_destinatario_camara WHERE camara_id=?", (camara_id,))
    registrar_log(session.get("user", ""), "xsense_excluir_camara", alvo=camara_id)
    return jsonify({"ok": True})


@xsense_bp.route("/api/xsense/camaras/<camara_id>/historico", methods=["GET"])
@login_required
@modulo_required("xsense")
def camara_historico(camara_id):
    """Histórico local (rápido, sem tocar no portal) — o que já foi
    coletado/importado fica salvo em xsense_leituras. Ver também
    /importar_historico pra puxar mais leituras do Sensores sob demanda."""
    try:
        dias = max(1, min(int(request.args.get("dias", 7)), 365))
    except (TypeError, ValueError):
        dias = 7
    from datetime import datetime, timedelta
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as db:
        cam = db.execute("SELECT * FROM xsense_camaras WHERE id=?", (camara_id,)).fetchone()
        if not cam:
            return jsonify({"error": "Câmara não encontrada."}), 404
        # Esconder na lista não basta: o id da câmara ia na URL e qualquer
        # usuário do módulo lia o histórico de qualquer CD. Responde o mesmo
        # 404 de câmara inexistente, para a resposta não confirmar que ela
        # existe em outro CD.
        if not _camara_visivel(cam, _locais_permitidos_sessao(db)):
            return jsonify({"error": "Câmara não encontrada."}), 404
        resultado = {}
        for dim in DIMENSOES:
            rows = db.execute("""
                SELECT ts, valor FROM xsense_leituras
                WHERE camara_id=? AND dimensao=? AND ts>=? ORDER BY ts ASC
            """, (camara_id, dim, desde)).fetchall()
            resultado[dim] = [{"ts": r["ts"], "valor": r["valor"]} for r in rows]
    return jsonify({"camara": _camara_to_dict(cam), "leituras": resultado, "dias": dias})


@xsense_bp.route("/api/xsense/camaras/<camara_id>/importar_historico", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def camara_importar_historico(camara_id):
    """Puxa o histórico direto do portal de sensores (não é uma coleta contínua
    — é um backfill sob demanda) e grava em xsense_leituras. Não roda o
    motor de detecção sobre esses pontos: importar histórico é só pra ver
    o gráfico, não pra gerar alerta retroativo de coisa que já passou."""
    from app.database import get_xsense_credenciais, set_xsense_status
    from app.services.xsense_scraper import buscar_historico

    d = request.json or {}
    try:
        dias = max(1, min(int(d.get("dias", 90)), 365))
    except (TypeError, ValueError):
        dias = 90

    with get_db() as db:
        cam = db.execute("SELECT * FROM xsense_camaras WHERE id=?", (camara_id,)).fetchone()
    if not cam:
        return jsonify({"error": "Câmara não encontrada."}), 404
    if not cam["provider_ref"]:
        return jsonify({"error": "Essa câmara não veio do Sensores (foi cadastrada manualmente) — "
                                  "não tem como buscar histórico dela no portal."}), 400

    creds = get_xsense_credenciais()
    if not creds.get("usuario") or not creds.get("senha"):
        return jsonify({"error": "Credenciais do portal de sensores não configuradas "
                                  "(⚙️ Configurações, no painel principal)."}), 400

    # node_id da PRÓPRIA câmara (instalação/CD que ela pertence) — cai no
    # salvo em Configurações só como fallback pra câmara antiga, importada
    # antes de existir essa coluna (26/08/2026)
    node_id_cam = cam["node_id"] or creds.get("node_id", "")
    ok, msg, dados = buscar_historico(
        creds["usuario"], creds["senha"], node_id_cam, cam["provider_ref"], dias)
    set_xsense_status("ok" if ok else "falha", agora_brt_str())
    if not ok:
        return jsonify({"error": msg}), 502

    inseridos = 0
    with get_db() as db:
        for dim, pontos in dados.items():
            if not pontos:
                continue
            linhas = [(camara_id, dim, p["ts"], p["valor"]) for p in pontos]
            cur = db.executemany(
                "INSERT OR IGNORE INTO xsense_leituras(camara_id, dimensao, ts, valor) VALUES (?,?,?,?)",
                linhas)
            inseridos += cur.rowcount

    registrar_log(session.get("user", ""), "xsense_importar_historico",
                 alvo=camara_id, detalhe=f"{inseridos} leituras novas ({dias} dias)")
    return jsonify({"ok": True, "msg": f"{inseridos} leitura(s) nova(s) importada(s).", "inseridos": inseridos})


# "Nome do grupo" genérico que o próprio portal usa quando ninguém
# customizou isso lá — combinar com "nome" nesse caso só adicionaria ruído
# ("CR2 - Item monitorado"), então nesse caso o rótulo sugerido cai só no
# nome (ver _rotulo_sugerido).
_GRUPO_XSENSE_GENERICO = "item monitorado"


def _rotulo_sugerido(nome: str, nome_grupo: str) -> str:
    """Combina "Identificação do Item" + "Nome do grupo" — mesma dupla
    coluna que a tabela do próprio portal de sensores mostra (pedido do usuário
    29/08/2026: "ficaria melhor visualmente, como CDA já está" — algumas
    câmaras do CDA já tinham esse padrão só porque alguém digitou à mão na
    importação; isso generaliza pra sugerir automaticamente)."""
    grupo = (nome_grupo or "").strip()
    if not grupo or grupo.lower() == _GRUPO_XSENSE_GENERICO:
        return nome
    return f"{nome} - {grupo}"


@xsense_bp.route("/api/xsense/sincronizar", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def sincronizar():
    """Loga no portal de sensores com a credencial cadastrada (Configurações,
    master) e lista as câmaras de TODAS as instalações (CDs) encontradas
    na árvore da empresa — marcando quais já estão importadas aqui (por
    provider_ref). Quem decide importar é o admin, isso aqui só lê e
    mostra. Corrigido 26/08/2026: antes passava o node_id salvo em
    config, o que travava a sincronização numa instalação só (relevante
    só enquanto existia 1 CD) — agora sempre descobre e varre todas.

    Aproveita a consulta pra também preencher o rótulo (Nome + Nome do
    grupo do portal) de câmaras JÁ importadas que ainda não têm rótulo
    nenhum salvo (29/08/2026) — nunca sobrescreve um rótulo que alguém já
    personalizou manualmente, só preenche o que estava vazio."""
    from app.database import get_xsense_credenciais, set_xsense_status
    from app.services.xsense_scraper import sincronizar_camaras

    creds = get_xsense_credenciais()
    if not creds.get("usuario") or not creds.get("senha"):
        return jsonify({"error": "Credenciais do portal de sensores não configuradas "
                                  "(⚙️ Configurações, no painel principal)."}), 400

    ok, msg, itens, _node_ids, _nomes = sincronizar_camaras(creds["usuario"], creds["senha"])
    set_xsense_status("ok" if ok else "falha", agora_brt_str())
    if not ok:
        return jsonify({"error": msg}), 502

    with get_db() as db:
        rotulos_vazios = {r["provider_ref"] for r in db.execute(
            "SELECT provider_ref FROM xsense_camaras "
            "WHERE provider_ref IS NOT NULL AND provider_ref != '' AND (rotulo IS NULL OR rotulo = '')"
        ).fetchall()}
        existentes = rotulos_vazios | {r["provider_ref"] for r in db.execute(
            "SELECT provider_ref FROM xsense_camaras WHERE provider_ref IS NOT NULL AND provider_ref != '' "
            "AND (rotulo IS NOT NULL AND rotulo != '')"
        ).fetchall()}
        rotulos_atualizados = 0
        for it in itens:
            it["ja_importada"] = it["provider_ref"] in existentes
            if it["provider_ref"] in rotulos_vazios:
                sugerido = _rotulo_sugerido(it["nome"], it.get("nome_grupo", ""))
                db.execute("UPDATE xsense_camaras SET rotulo=? WHERE provider_ref=?",
                          (sugerido, it["provider_ref"]))
                rotulos_atualizados += 1

    registrar_log(session.get("user", ""), "xsense_sincronizar",
                 detalhe=f"{len(itens)} itens, {rotulos_atualizados} rótulo(s) preenchido(s)")
    return jsonify({"ok": True, "itens": itens, "msg": msg, "rotulos_atualizados": rotulos_atualizados})


def coletar_leituras_todas_camaras() -> dict:
    """Roda UM ciclo do coletor periódico (mesma lógica de
    scripts/coletar_xsense.py, chamado por ele a cada 5 min via cron).
    Busca só o que falta desde a última leitura já processada de cada
    câmara (não um backfill de N dias — isso é importar_historico_todas_
    camaras, mais abaixo) e passa cada ponto pelo motor de detecção de
    verdade — se uma câmara estiver em excursão, o alerta dispara. Sem
    rota HTTP própria: é só a função que o cron chama."""
    from app.database import get_xsense_credenciais, set_xsense_status
    from app.services.xsense_scraper import coletar_leituras_recentes

    creds = get_xsense_credenciais()
    if not creds.get("usuario") or not creds.get("senha"):
        return {"ok": False, "msg": "Credenciais do portal de sensores não configuradas "
                                     "(⚙️ Configurações, no painel principal)."}

    with get_db() as db:
        camaras = {r["provider_ref"]: dict(r) for r in db.execute(
            "SELECT * FROM xsense_camaras WHERE provider='xsense' AND ativo=1 AND provider_ref IS NOT NULL"
        ).fetchall()}
        ultima_por_dim: dict[tuple[str, str], str] = {
            (r["camara_id"], r["dimensao"]): r["ultima_leitura_ts"]
            for r in db.execute("SELECT camara_id, dimensao, ultima_leitura_ts FROM xsense_estado").fetchall()
        }

    if not camaras:
        return {"ok": False, "msg": "Nenhuma câmara importada do Sensores."}

    desde_ts = {}
    node_ids_por_ref = {}
    for ref, cam in camaras.items():
        ts_temp = ultima_por_dim.get((cam["id"], "temperatura"))
        ts_umid = ultima_por_dim.get((cam["id"], "umidade"))
        candidatos = [t for t in (ts_temp, ts_umid) if t]
        desde_ts[ref] = min(candidatos) if candidatos else None
        node_ids_por_ref[ref] = cam.get("node_id") or creds.get("node_id", "")

    ok, msg, leituras_por_camara = coletar_leituras_recentes(
        creds["usuario"], creds["senha"], node_ids_por_ref, desde_ts)
    set_xsense_status("ok" if ok else "falha", agora_brt_str())
    if not ok:
        return {"ok": False, "msg": msg}

    processadas, pontos_total, pushes_total = 0, 0, 0
    for ref, dims in leituras_por_camara.items():
        cam = camaras.get(ref)
        if not cam:
            continue
        teve_ponto = False
        for dim, pontos in dims.items():
            ja_processado_ate = ultima_por_dim.get((cam["id"], dim))
            for p in sorted(pontos, key=lambda p: p["ts"]):
                if ja_processado_ate and p["ts"] <= ja_processado_ate:
                    continue
                pushes = processar_leitura(cam["id"], dim, p["valor"], p["ts"])
                pushes_total += len(pushes)
                pontos_total += 1
                teve_ponto = True
                ja_processado_ate = p["ts"]
        if teve_ponto:
            processadas += 1

    pushes_total += verificar_sinal_todas_camaras()["alertas_disparados"]

    return {"ok": True, "msg": msg, "camaras_atualizadas": processadas,
            "leituras_processadas": pontos_total, "alertas_disparados": pushes_total}


def verificar_sinal_todas_camaras() -> dict:
    """Detecta câmara SEM SINAL (sem leitura nova há mais de
    SINAL_LIMITE_MIN) e dispara alerta Telegram — mesmo canal dos alertas
    de temperatura, uma transição só (fica quieto assim que já avisou,
    não fica lembrando a cada ciclo como o alerta de excursão faz;
    avisa de novo quando volta a reportar). Pedido do usuário
    29/08/2026: "alertar câmaras sem sinal" no Telegram.

    Chamada no fim de todo ciclo de coleta ao vivo (cron e "Atualizar
    agora") — NÃO entra no backfill de histórico, que é propositalmente
    silencioso (ver importar_historico_todas_camaras)."""
    from datetime import datetime
    agora = datetime.now()
    agora_str = agora.strftime("%Y-%m-%d %H:%M:%S")
    alertas_disparados = 0
    with get_db() as db:
        camaras = db.execute("SELECT * FROM xsense_camaras WHERE ativo=1 AND alertas_ativos=1").fetchall()
        for cam in camaras:
            est = db.execute(
                "SELECT ultima_leitura_ts FROM xsense_estado WHERE camara_id=? AND dimensao='temperatura'",
                (cam["id"],)).fetchone()
            ultima_ts = est["ultima_leitura_ts"] if est else None
            minutos = None
            if ultima_ts:
                try:
                    minutos = (agora - datetime.strptime(ultima_ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
                except ValueError:
                    minutos = None
            sem_sinal_agora = minutos is None or minutos > SINAL_LIMITE_MIN

            ultimo_evento = db.execute("""
                SELECT tipo FROM xsense_eventos WHERE camara_id=? AND tipo IN ('SEM_SINAL','SINAL_RECUPERADO')
                ORDER BY ts_inicio DESC, id DESC LIMIT 1
            """, (cam["id"],)).fetchone()
            estava_sem_sinal = bool(ultimo_evento) and ultimo_evento["tipo"] == "SEM_SINAL"

            evento = None
            if sem_sinal_agora and not estava_sem_sinal:
                detalhe = (f"sem leitura nova há {fmt_duracao_min(int(minutos))}"
                          if minutos is not None else "nunca recebeu leitura")
                evento = ("SEM_SINAL", "MEDIUM", detalhe)
            elif not sem_sinal_agora and estava_sem_sinal:
                evento = ("SINAL_RECUPERADO", "INFO", f"voltou a reportar (última leitura: {ultima_ts})")
            if not evento:
                continue

            tipo, severidade, detalhe = evento
            cur = db.execute("""
                INSERT INTO xsense_eventos(camara_id, dimensao, tipo, severidade, ts_inicio, valor, detalhe, notificado)
                VALUES (?,?,?,?,?,?,?,1)
            """, (cam["id"], "temperatura", tipo, severidade, agora_str, None, detalhe))
            alertas_disparados += 1
            pushes = [{"camara_id": cam["id"], "dimensao": "temperatura", "tipo": tipo,
                       "severidade": severidade, "valor": None, "detalhe": detalhe,
                       "lembrete": False, "ts": agora_str, "evento_id": cur.lastrowid}]
            _notificar_pushes(cam, pushes)
            _gravar_canais(pushes, db)
    return {"alertas_disparados": alertas_disparados}


@xsense_bp.route("/api/xsense/coletar_agora", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def coletar_agora():
    """Botão manual "🔄 Atualizar agora" — roda o mesmo ciclo de coleta que
    o cron faz a cada 5 min, mas na hora: atualiza o "Última leitura"/
    temperatura atual do card (xsense_estado) e roda o motor de detecção
    de verdade (dispara alerta na hora se alguma câmara estiver em
    excursão) — diferente de "Importar histórico", que só popula o
    gráfico e nunca mexe no card (ver importar_historico_todas_camaras)."""
    r = coletar_leituras_todas_camaras()
    if not r.get("ok"):
        return jsonify({"error": r.get("msg", "Falha.")}), 502
    registrar_log(session.get("user", ""), "xsense_coletar_agora",
                 detalhe=f"{r['camaras_atualizadas']} câmara(s), {r['leituras_processadas']} leitura(s), "
                         f"{r['alertas_disparados']} alerta(s)")
    return jsonify(r)


def importar_historico_todas_camaras(dias: int = 90) -> dict:
    """Backfill de histórico (não é a coleta contínua) pra TODAS as câmaras
    de uma vez — botão "📥 Importar histórico" no topbar (pedido do usuário
    29/08/2026: "queria importar o histórico de todas as câmaras pra
    mostrar os gráficos preenchidos" — o botão anterior, "coletar agora",
    resolvia outro problema: só pegava o que faltava desde a última
    leitura, não populava gráfico vazio).

    Reaproveita coletar_leituras_recentes() (mesma sessão HTTP cacheada pra
    todas as câmaras) em vez de logar via Playwright uma vez por câmara
    como buscar_historico() faz — importante numa VM e2-micro com várias
    câmaras: uma sessão só, não N logins. Grava direto em xsense_leituras
    SEM rodar o motor de detecção — mesma decisão já tomada pra importação
    por câmara (camara_importar_historico): histórico importado é só pra
    ver o gráfico, não pra gerar alerta retroativo de excursão que já
    passou."""
    from datetime import datetime, timedelta
    from app.database import get_xsense_credenciais, set_xsense_status
    from app.services.xsense_scraper import coletar_leituras_recentes

    creds = get_xsense_credenciais()
    if not creds.get("usuario") or not creds.get("senha"):
        return {"ok": False, "msg": "Credenciais do portal de sensores não configuradas "
                                     "(⚙️ Configurações, no painel principal)."}

    with get_db() as db:
        camaras = {r["provider_ref"]: dict(r) for r in db.execute(
            "SELECT * FROM xsense_camaras WHERE provider='xsense' AND ativo=1 AND provider_ref IS NOT NULL"
        ).fetchall()}
    if not camaras:
        return {"ok": False, "msg": "Nenhuma câmara importada do Sensores."}

    node_ids_por_ref = {ref: (cam.get("node_id") or creds.get("node_id", "")) for ref, cam in camaras.items()}
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    desde_ts = {ref: desde for ref in node_ids_por_ref}

    ok, msg, leituras_por_camara = coletar_leituras_recentes(
        creds["usuario"], creds["senha"], node_ids_por_ref, desde_ts,
        lookback_max_min=dias * 24 * 60)
    set_xsense_status("ok" if ok else "falha", agora_brt_str())
    if not ok:
        return {"ok": False, "msg": msg}

    camaras_atualizadas, pontos_total = 0, 0
    with get_db() as db:
        for ref, dims in leituras_por_camara.items():
            cam = camaras.get(ref)
            if not cam:
                continue
            teve_ponto = False
            for dim, pontos in dims.items():
                if not pontos:
                    continue
                linhas = [(cam["id"], dim, p["ts"], p["valor"]) for p in pontos]
                cur = db.executemany(
                    "INSERT OR IGNORE INTO xsense_leituras(camara_id, dimensao, ts, valor) VALUES (?,?,?,?)",
                    linhas)
                pontos_total += cur.rowcount
                if cur.rowcount:
                    teve_ponto = True
            if teve_ponto:
                camaras_atualizadas += 1

    return {"ok": True, "msg": msg, "camaras_atualizadas": camaras_atualizadas,
            "leituras_processadas": pontos_total, "dias": dias}


@xsense_bp.route("/api/xsense/importar_historico_todas", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def importar_historico_todas():
    """Botão manual "📥 Importar histórico" — backfill de N dias (padrão
    90) pra todas as câmaras de uma vez, pra popular os gráficos."""
    d = request.json or {}
    try:
        dias = max(1, min(int(d.get("dias", 90)), 365))
    except (TypeError, ValueError):
        dias = 90
    r = importar_historico_todas_camaras(dias)
    if not r.get("ok"):
        return jsonify({"error": r.get("msg", "Falha.")}), 502
    registrar_log(session.get("user", ""), "xsense_importar_historico_todas",
                 detalhe=f"{r['camaras_atualizadas']} câmara(s), {r['leituras_processadas']} leitura(s), {dias} dias")
    return jsonify(r)


# ── destinatários (CRUD) + aprovação por CD ────────────────────────────
def _pode_gerenciar_destinatarios() -> bool:
    """Gerente (admin/master) sempre pode; Coordenador/Analista podem
    gerenciar só a coluna do PRÓPRIO CD (pedido do usuário 26/08/2026,
    junto com o fluxo de solicitação de alerta via bot) — Qualidade/
    Operador não mexem aqui (mesmo nível de quem edita config de câmara)."""
    return session.get("perfil") in ("admin", "master", "coordenador", "analista")


def _locais_conhecidos(db) -> list:
    rows = db.execute("SELECT DISTINCT local FROM xsense_camaras WHERE ativo=1 AND local != '' ORDER BY local").fetchall()
    return [r["local"] for r in rows]


def _todas_camaras_do_local(db, local: str) -> set:
    return {r["id"] for r in db.execute("SELECT id FROM xsense_camaras WHERE local=? AND ativo=1", (local,)).fetchall()}


def _camaras_atuais_do_destinatario(db, dest_id: int) -> set:
    return {r["camara_id"] for r in db.execute(
        "SELECT camara_id FROM xsense_destinatario_camara WHERE destinatario_id=?", (dest_id,)).fetchall()}


def _locais_de_camaras(camara_ids, cache_camaras: dict) -> list:
    """Locais que um conjunto de câmaras representa (câmara vazia -> lista
    vazia; "vazio = todos os locais" é uma decisão do CHAMADOR, não dessa
    função — ver comentário em _definir_local_destinatario sobre por que
    não dá pra inferir isso só pelo tamanho do conjunto)."""
    return sorted({cache_camaras.get(cid) for cid in camara_ids if cache_camaras.get(cid)})


def _locais_permitidos_do_destinatario(db, dest) -> list | None:
    """CDs que esse destinatário do bot pode consultar via /status, /grafico,
    /foratemp etc. — None = sem restrição (vê todos). "Vê tudo" agora é
    SEMPRE a flag explícita eh_gerente (corrigido 26/08/2026) — antes era
    inferido de "câmara-filtro vazio", o que causava um bug de autorização
    real: cadastrar um CD novo dava acesso automático a ele pra quem
    estivesse nesse estado "vazio", sem ninguém ter decidido isso de
    propósito. Pedido do usuário 26/08/2026: o bot não deve nem perguntar
    "qual CD" pra quem só tem acesso a 1 — vai direto; só pergunta quando
    a pessoa tem 2+."""
    if not dest:
        return None
    if dest["eh_gerente"]:
        return None
    camaras = _camaras_atuais_do_destinatario(db, dest["id"])
    cache = {r["id"]: r["local"] for r in db.execute("SELECT id, local FROM xsense_camaras").fetchall()}
    return _locais_de_camaras(camaras, cache)


def _locais_permitidos_sessao(db) -> list | None:
    """CDs que o usuário LOGADO NO SITE (não confundir com destinatário do
    bot) pode ver no dashboard mensal — None = sem restrição (admin/master).
    `xsense_camaras.local` guarda a string cheia da instalação (ex.: "CDA -
    Cidade - UF"), não a sigla limpa; casa por prefixo contra
    `unidades_permitidas` da sessão (mesma imprecisão já registrada como
    gap conhecido no BACKLOG, seção Multi-empresa — ok pro escopo simples
    de hoje, revisar se um dia a sigla exata virar necessária)."""
    if session.get("perfil") in ("admin", "master"):
        return None
    raw = session.get("unidades_permitidas", "") or ""
    siglas = [u.strip().upper() for u in raw.split(",") if u.strip()]
    if not siglas:
        # Lista vazia = TODOS os CDs, mesma convenção de modulos_permitidos e
        # unidades_permitidas no resto do sistema (decisão do usuário,
        # 06/10/2026: "vazio todos os cds"). Antes isto devolvia [], que
        # escondia TUDO de quem ainda não tinha unidade cadastrada.
        return None
    todos = _locais_conhecidos(db)
    return [loc for loc in todos if any(loc.strip().upper().startswith(s) for s in siglas)]


def _local_e_do_meu_cd(local: str) -> bool:
    """O local (nome completo da instalação) pertence ao CD do usuário?

    `cd_do_usuario()` devolve a SIGLA ("CDA") e `local_solicitado` guarda o
    nome de exibição do portal ("CDA - Cidade - UF"). As três rotas de
    aprovação comparavam os dois por igualdade exata, então coordenador e
    analista NUNCA viam solicitação pendente nem conseguiam aprovar (achado na
    revisão pré-deploy, 08/10/2026). Casa por prefixo, mesma régua de
    _locais_permitidos_sessao.
    """
    sigla = (cd_do_usuario() or "").strip().upper()
    alvo = str(local or "").strip().upper()
    return bool(sigla) and bool(alvo) and alvo.startswith(sigla)


def _camara_visivel(cam, locais_permitidos) -> bool:
    """A câmara aparece para a sessão atual?

    `locais_permitidos` None = sem restrição (Gerente/Desenvolvedor).

    Câmara SEM local cadastrado fica invisível para perfil restrito
    (decisão do usuário, 06/10/2026: "camara sem cd fica invisivel", e ela
    fica pendente até alguém cadastrar o CD). Para admin/master continua
    visível — é quem precisa vê-la para classificar.
    """
    if locais_permitidos is None:
        return True
    local = str((cam["local"] if "local" in cam.keys() else "") or "").strip()
    return bool(local) and local in locais_permitidos


def _classificar_risco(eficiencia: float | None, params: dict) -> str | None:
    """Alto/Médio/Sem risco a partir da % de tempo dentro da faixa e dos
    limiares configuráveis pelo usuário (ver get/set_xsense_dashboard_params)
    — pedido explícito 29/08/2026: não cravar um limiar fixo no código, é
    decisão de negócio (Qualidade) que muda, e cada operação pode ter um
    padrão diferente do que considera aceitável."""
    if eficiencia is None:
        return None
    if eficiencia < params["eficiencia_alto"]:
        return "alto"
    if eficiencia < params["eficiencia_medio"]:
        return "medio"
    return "sem_risco"


def _calcular_fora_leituras(db, camara_id: str, dimensao: str, minimo, maximo,
                             ts_inicio: str, ts_fim: str) -> dict:
    """Eventos e minutos fora de faixa de UMA câmara+dimensão, direto das
    leituras brutas (xsense_leituras) em vez do log de eventos ao vivo.

    Achado ao vivo 31/08/2026: o cálculo antigo (baseado em xsense_eventos,
    escrito pelo coletor a cada ~5min) mostrava 120h fora pra CR3 em agosto,
    mas a exportação real do histórico do portal de sensores pro mesmo período — sem
    nenhum buraco de coleta — dá 81h de fato fora, batendo com a conferência
    manual do usuário. A diferença: quando o NOSSO coletor fica parado
    (a VM caiu, sessão expirou etc.), o log de eventos pareia o início da
    excursão com a PRÓXIMA recuperação que ele consegue ver depois de voltar
    — contando o buraco inteiro da NOSSA coleta como se fosse tempo real
    fora da faixa. As leituras brutas do portal de sensores (repostas todo dia de
    madrugada por scripts/importar_historico_xsense.py, ver cron) são a
    fonte confiável — não dependem da NOSSA disponibilidade, só da da
    Sensores. Os alertas em tempo real (Telegram) continuam via xsense_eventos
    — isso aqui é só pro relatório histórico do dashboard mensal."""
    from datetime import datetime
    if minimo is None or maximo is None:
        return {"eventos": 0, "minutos_fora": 0, "em_andamento": 0}

    rows = db.execute("""
        SELECT ts, valor FROM xsense_leituras
        WHERE camara_id=? AND dimensao=? AND ts >= ? AND ts < ?
        ORDER BY ts ASC
    """, (camara_id, dimensao, ts_inicio, ts_fim)).fetchall()
    if not rows:
        return {"eventos": 0, "minutos_fora": 0, "em_andamento": 0}

    eventos = 0
    minutos_fora = 0.0
    em_run = False
    for i in range(len(rows) - 1):
        v = rows[i]["valor"]
        fora = v is not None and not (minimo <= v <= maximo)
        if fora and not em_run:
            eventos += 1
        if fora:
            t0 = datetime.strptime(rows[i]["ts"], "%Y-%m-%d %H:%M:%S")
            t1 = datetime.strptime(rows[i + 1]["ts"], "%Y-%m-%d %H:%M:%S")
            minutos_fora += max(0.0, (t1 - t0).total_seconds() / 60)
        em_run = fora

    em_andamento = 0
    ultimo = rows[-1]
    v_ultimo = ultimo["valor"]
    fora_ultimo = v_ultimo is not None and not (minimo <= v_ultimo <= maximo)
    if fora_ultimo:
        if not em_run:
            eventos += 1
        agora = datetime.now()
        fim_janela = min(agora, datetime.strptime(ts_fim, "%Y-%m-%d %H:%M:%S"))
        t0 = datetime.strptime(ultimo["ts"], "%Y-%m-%d %H:%M:%S")
        if fim_janela > t0:
            minutos_fora += (fim_janela - t0).total_seconds() / 60
        em_andamento = 1

    return {"eventos": eventos, "minutos_fora": int(round(minutos_fora)), "em_andamento": em_andamento}


def _intervalo_medio_camara(db, camara_id: str, dimensao: str = "temperatura", amostra: int = 200) -> float:
    """Mediana (minutos) do intervalo entre leituras consecutivas mais
    recentes da câmara — usada como 'esperado' pro cálculo de uptime, em vez
    de um valor fixo pra todas: cada sensor Sensores reporta na cadência dele
    (5, 8, 16min...), não dá pra generalizar (achado ao vivo 31/08/2026).
    Mediana, não média, pra não deixar um buraco pontual de queda inflar o
    intervalo 'normal' da câmara."""
    from datetime import datetime
    rows = db.execute("""
        SELECT ts FROM xsense_leituras WHERE camara_id=? AND dimensao=?
        ORDER BY ts DESC LIMIT ?
    """, (camara_id, dimensao, amostra)).fetchall()
    if len(rows) < 2:
        return 5.0  # sem histórico suficiente — cai no valor antigo como ponto de partida
    tss = sorted(datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S") for r in rows)
    deltas = sorted((tss[i + 1] - tss[i]).total_seconds() / 60 for i in range(len(tss) - 1))
    mediana = deltas[len(deltas) // 2]
    return max(1.0, mediana)


def _calcular_indicadores_mensais(db, ano: int, mes: int, locais_permitidos: list | None, params: dict) -> dict:
    """Indicadores agregados de um mês pro dashboard Sensores — só leitura,
    sem disparo de alerta (pedido explícito do usuário 28/08/2026: começar
    simples)."""
    from datetime import datetime

    inicio = f"{ano:04d}-{mes:02d}-01 00:00:00"
    if mes == 12:
        fim = f"{ano+1:04d}-01-01 00:00:00"
    else:
        fim = f"{ano:04d}-{mes+1:02d}-01 00:00:00"

    cam_rows = db.execute(
        "SELECT id, nome, local, ativo, tipo, temp_min, temp_max, umid_min, umid_max FROM xsense_camaras"
    ).fetchall()
    cam_local = {r["id"]: (r["local"] or "Sem local") for r in cam_rows}
    cam_nome = {r["id"]: r["nome"] for r in cam_rows}
    cam_tipo = {r["id"]: r["tipo"] for r in cam_rows}
    if locais_permitidos is not None:
        ids_permitidos = {cid for cid, loc in cam_local.items() if loc in locais_permitidos}
    else:
        ids_permitidos = set(cam_local.keys())

    # filtro por tipo de câmara (congelada/resfriada/antecâmara/ambiente) —
    # pedido do usuário 31/08/2026: antecâmara já não gera alerta
    # (alertas_ativos=0), então também não deveria poluir o dashboard
    # mensal por padrão. Configurável em Parâmetros, não fixo no código.
    tipos_permitidos = set(params.get("tipos_dashboard") or ("congelada", "resfriada"))
    ids_permitidos = {cid for cid in ids_permitidos if cam_tipo.get(cid, "") in tipos_permitidos}

    # exclusão por câmara específica — ex.: câmara já instalada/monitorada
    # mas ainda sem uso real (pedido do usuário 31/08/2026), independente
    # do tipo dela.
    camaras_excluidas = set(params.get("camaras_excluidas") or ())
    ids_permitidos -= camaras_excluidas

    # eventos/minutos fora — direto das leituras brutas (temperatura +
    # umidade), ver _calcular_fora_leituras acima pro porquê.
    por_camara: dict[str, dict] = {}
    for row in cam_rows:
        cid = row["id"]
        if cid not in ids_permitidos:
            continue
        item = por_camara.setdefault(cid, {"eventos": 0, "minutos_fora": 0, "em_andamento": 0})
        for dim, minimo, maximo in (
            ("temperatura", row["temp_min"], row["temp_max"]),
            ("umidade", row["umid_min"], row["umid_max"]),
        ):
            r = _calcular_fora_leituras(db, cid, dim, minimo, maximo, inicio, fim)
            item["eventos"] += r["eventos"]
            item["minutos_fora"] += r["minutos_fora"]
            item["em_andamento"] += r["em_andamento"]
        if not item["eventos"] and not item["minutos_fora"]:
            del por_camara[cid]

    # uptime — leituras reais vs esperadas, só pras câmaras ativas dentro do
    # escopo. Achado ao vivo 31/08/2026 (usuário): o "esperado" NÃO pode ser
    # fixo em 5min pra toda câmara — 5min é só o quanto o NOSSO coletor
    # tenta puxar de novo (scripts/coletar_xsense.py), mas cada sensor
    # Sensores reporta na cadência dele (a exportação real da CR3 mostrou
    # ~8min de intervalo médio, não 5) — com um "esperado" fixo mais
    # apertado que a cadência real do sensor, toda câmara saudável aparece
    # com uptime artificialmente baixo. Correção: o esperado de cada câmara
    # vem da MEDIANA do intervalo entre leituras dela mesma (robusta a
    # buracos pontuais de queda, que não devem inflar o "normal").
    agora = datetime.now()
    fim_janela = min(datetime.strptime(fim, "%Y-%m-%d %H:%M:%S"), agora)
    inicio_janela = datetime.strptime(inicio, "%Y-%m-%d %H:%M:%S")
    minutos_totais = max(1, int((fim_janela - inicio_janela).total_seconds() // 60))

    uptime_por_camara = {}
    for cid in ids_permitidos:
        if not next((r["ativo"] for r in cam_rows if r["id"] == cid), 0):
            continue
        n = db.execute("""
            SELECT COUNT(DISTINCT ts) n FROM xsense_leituras
            WHERE camara_id=? AND dimensao='temperatura' AND ts >= ? AND ts < ?
        """, (cid, inicio, fim)).fetchone()["n"]
        intervalo = _intervalo_medio_camara(db, cid)
        esperadas = max(1, int(minutos_totais // intervalo))
        uptime_por_camara[cid] = min(100.0, round(100.0 * n / esperadas, 1))

    # eficiência — % do período que a câmara passou DENTRO da faixa seg ura
    # (diferente de uptime, que é sobre o sensor mandar leitura ou não).
    # Calculada pra TODA câmara ativa no escopo, mesmo as sem problema
    # nenhum no mês (ficam em 100%) — senão a média geral ficaria
    # artificialmente pessimista, considerando só quem teve evento.
    eficiencia_por_camara = {}
    for cid in ids_permitidos:
        if not next((r["ativo"] for r in cam_rows if r["id"] == cid), 0):
            continue
        minutos_fora_cid = por_camara.get(cid, {}).get("minutos_fora", 0)
        eficiencia_por_camara[cid] = round(100.0 * max(0.0, 1 - minutos_fora_cid / minutos_totais), 1)

    # agrega por local (CD)
    por_local: dict[str, dict] = {}
    for cid, item in por_camara.items():
        loc = cam_local.get(cid, "Sem local")
        agr = por_local.setdefault(loc, {"eventos": 0, "minutos_fora": 0})
        agr["eventos"] += item["eventos"]
        agr["minutos_fora"] += item["minutos_fora"]
    efic_por_local: dict[str, list] = {}
    for cid, efic in eficiencia_por_camara.items():
        efic_por_local.setdefault(cam_local.get(cid, "Sem local"), []).append(efic)

    total_eventos = sum(i["eventos"] for i in por_camara.values())
    total_minutos_fora = sum(i["minutos_fora"] for i in por_camara.values())
    uptime_medio = round(sum(uptime_por_camara.values()) / len(uptime_por_camara), 1) if uptime_por_camara else None
    eficiencia_media = round(sum(eficiencia_por_camara.values()) / len(eficiencia_por_camara), 1) \
        if eficiencia_por_camara else None

    campea_id = max(por_camara, key=lambda cid: por_camara[cid]["eventos"]) if por_camara else None

    return {
        "total_eventos": total_eventos,
        "total_minutos_fora": total_minutos_fora,
        "total_fmt": fmt_duracao_min(total_minutos_fora),
        "uptime_medio": uptime_medio,
        "eficiencia_media": eficiencia_media,
        "camara_campea": ({"id": campea_id, "nome": cam_nome.get(campea_id, campea_id),
                          "eventos": por_camara[campea_id]["eventos"]} if campea_id else None),
        "por_camara": [
            {"id": cid, "nome": cam_nome.get(cid, cid), "local": cam_local.get(cid, ""),
             "eventos": item["eventos"], "minutos_fora": item["minutos_fora"],
             "fora_fmt": fmt_duracao_min(item["minutos_fora"]), "em_andamento": item["em_andamento"],
             "uptime": uptime_por_camara.get(cid),
             "eficiencia": eficiencia_por_camara.get(cid),
             "risco": _classificar_risco(eficiencia_por_camara.get(cid), params)}
            for cid, item in sorted(por_camara.items(), key=lambda kv: -kv[1]["eventos"])
        ],
        "por_local": [
            {"local": loc, "eventos": agr["eventos"], "minutos_fora": agr["minutos_fora"],
             "fora_fmt": fmt_duracao_min(agr["minutos_fora"]),
             "eficiencia": (round(sum(efic_por_local[loc]) / len(efic_por_local[loc]), 1)
                           if efic_por_local.get(loc) else None)}
            for loc, agr in sorted(por_local.items(), key=lambda kv: -kv[1]["eventos"])
        ],
    }


def _definir_local_destinatario(usuario_log: str, dest_id: int, local: str, incluir: bool):
    """Ativa/desativa alertas de UM local pra um destinatário, em cima do
    filtro por câmara que já existe (xsense_destinatario_camara). Só se
    aplica a quem NÃO é gerente (eh_gerente=0) — gerente vê tudo sempre,
    incluindo CD futuro, e não passa pela grade de CD por CD (ver
    destinatario_definir_gerente)."""
    with get_db() as db:
        atuais = _camaras_atuais_do_destinatario(db, dest_id)
        camaras_local = _todas_camaras_do_local(db, local)
        atuais = (atuais | camaras_local) if incluir else (atuais - camaras_local)
        db.execute("DELETE FROM xsense_destinatario_camara WHERE destinatario_id=?", (dest_id,))
        for cid in atuais:
            db.execute("INSERT OR IGNORE INTO xsense_destinatario_camara(destinatario_id, camara_id) VALUES (?,?)",
                      (dest_id, cid))
    registrar_log(usuario_log, "xsense_destinatario_local", alvo=str(dest_id),
                 detalhe=f"{local}={'on' if incluir else 'off'}")


@xsense_bp.route("/api/xsense/destinatarios", methods=["GET", "POST"])
@login_required
@modulo_required("xsense")
@csrf_required
def destinatarios():
    if request.method == "GET":
        if not _pode_gerenciar_destinatarios():
            return jsonify({"error": "Sem acesso."}), 403
        with get_db() as db:
            rows = db.execute("SELECT * FROM xsense_destinatarios ORDER BY nome").fetchall()
            camaras_por_dest = {}
            for r in db.execute("SELECT * FROM xsense_destinatario_camara").fetchall():
                camaras_por_dest.setdefault(r["destinatario_id"], []).append(r["camara_id"])
            cache_camaras = {r["id"]: r["local"] for r in db.execute("SELECT id, local FROM xsense_camaras").fetchall()}
            # Coordenador e Analista gerenciam destinatários do PRÓPRIO CD:
            # a lista de locais tem de vir recortada, senão a tela oferece
            # os CDs dos outros no seletor.
            permitidos = _locais_permitidos_sessao(db)
            locais = (_locais_conhecidos(db) if permitidos is None
                      else [l for l in _locais_conhecidos(db) if l in permitidos])
            # O recorte estava só na lista `locais` do seletor: as LINHAS de
            # destinatário continuavam vindo de todos os CDs, com nome e
            # contato de Telegram de gente de outra unidade (achado na
            # revisão pré-deploy, 08/10/2026). Destinatário fica visível
            # quando alguma câmara dele — ou o CD que ele pediu — está no
            # recorte da sessão; gerente de alerta aparece para quem vê tudo.
            if permitidos is not None:
                local_da_camara = {c["id"]: c["local"] for c in db.execute(
                    "SELECT id, local FROM xsense_camaras").fetchall()}
                perm = set(permitidos)

                def _dest_visivel(r):
                    if r["status"] == "pendente":
                        return str(r["local_solicitado"] or "") in perm
                    if r["eh_gerente"]:
                        return False
                    locs = {local_da_camara.get(cid) or ""
                            for cid in camaras_por_dest.get(r["id"], [])}
                    return bool(locs & perm)

                rows = [r for r in rows if _dest_visivel(r)]
        itens = []
        for r in rows:
            d = dict(r)
            cids = camaras_por_dest.get(r["id"], [])
            d["camaras"] = cids
            if r["status"] == "pendente":
                d["locais"] = []  # ainda não recebe nada — só o pedido em local_solicitado
            elif r["eh_gerente"]:
                d["locais"] = locais  # flag explícita — vê todos os locais, sempre
            else:
                d["locais"] = _locais_de_camaras(cids, cache_camaras)
            itens.append(d)
        return jsonify({"destinatarios": itens, "locais_conhecidos": locais})

    # Cadastro manual completo (nome/telegram/câmaras à mão) continua
    # admin/master-only — o que abriu pra coordenador/analista foi só a
    # grade de aprovação/local (rotas /aprovar, /rejeitar, /local abaixo).
    if session.get("perfil") not in ("admin", "master"):
        return jsonify({"error": "Acesso restrito ao Admin"}), 403

    d = request.json or {}
    nome = str(d.get("nome", "")).strip()
    if not nome:
        return jsonify({"error": "Informe o nome do destinatário."}), 400
    if not d.get("id"):
        with get_db() as db:
            n = db.execute("SELECT COUNT(*) c FROM xsense_destinatarios").fetchone()["c"]
        if n >= MAX_DESTINATARIOS:
            return jsonify({"error": f"Máximo de {MAX_DESTINATARIOS} destinatários (limite da spec)."}), 400
    severidade = str(d.get("severidade_min", "MEDIUM")).strip().upper()
    if severidade not in SEVERIDADES:
        severidade = "MEDIUM"

    with get_db() as db:
        if d.get("id"):
            db.execute("""
                UPDATE xsense_destinatarios SET nome=?, telegram_chat_id=?, whatsapp_num=?,
                    severidade_min=?, ativo=? WHERE id=?
            """, (nome, d.get("telegram_chat_id", ""), d.get("whatsapp_num", ""),
                  severidade, 1 if d.get("ativo", True) else 0, int(d["id"])))
            dest_id = int(d["id"])
        else:
            cur = db.execute("""
                INSERT INTO xsense_destinatarios(nome, telegram_chat_id, whatsapp_num, severidade_min, ativo)
                VALUES (?,?,?,?,?)
            """, (nome, d.get("telegram_chat_id", ""), d.get("whatsapp_num", ""), severidade,
                  1 if d.get("ativo", True) else 0))
            dest_id = cur.lastrowid
        db.execute("DELETE FROM xsense_destinatario_camara WHERE destinatario_id=?", (dest_id,))
        for cid in (d.get("camaras") or []):
            db.execute("INSERT OR IGNORE INTO xsense_destinatario_camara(destinatario_id, camara_id) VALUES (?,?)",
                      (dest_id, cid))
    registrar_log(session.get("user", ""), "xsense_salvar_destinatario", alvo=nome)
    return jsonify({"ok": True, "id": dest_id})


@xsense_bp.route("/api/xsense/destinatarios/<int:dest_id>", methods=["DELETE"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def destinatario_excluir(dest_id):
    with get_db() as db:
        db.execute("DELETE FROM xsense_destinatarios WHERE id=?", (dest_id,))
        db.execute("DELETE FROM xsense_destinatario_camara WHERE destinatario_id=?", (dest_id,))
    registrar_log(session.get("user", ""), "xsense_excluir_destinatario", alvo=str(dest_id))
    return jsonify({"ok": True})


@xsense_bp.route("/api/xsense/destinatarios/pendentes")
@login_required
@modulo_required("xsense")
def destinatarios_pendentes():
    """Solicitações de alerta em aberto (vindas do /start do bot) — gerente
    vê todas, coordenador/analista só as do próprio CD."""
    if not _pode_gerenciar_destinatarios():
        return jsonify({"error": "Sem acesso."}), 403
    eh_gerente = session.get("perfil") in ("admin", "master")
    with get_db() as db:
        if eh_gerente:
            rows = db.execute("SELECT * FROM xsense_destinatarios WHERE status='pendente' ORDER BY id").fetchall()
        else:
            # o filtro é por prefixo da sigla, não por igualdade (ver
            # _local_e_do_meu_cd): o local guardado é o nome completo
            rows = [r for r in db.execute(
                "SELECT * FROM xsense_destinatarios WHERE status='pendente' ORDER BY id"
            ).fetchall() if _local_e_do_meu_cd(r["local_solicitado"])]
    return jsonify({"pendentes": [dict(r) for r in rows]})


@xsense_bp.route("/api/xsense/destinatarios/<int:dest_id>/aprovar", methods=["POST"])
@login_required
@modulo_required("xsense")
@csrf_required
def destinatario_aprovar(dest_id):
    if not _pode_gerenciar_destinatarios():
        return jsonify({"error": "Sem acesso."}), 403
    with get_db() as db:
        row = db.execute("SELECT * FROM xsense_destinatarios WHERE id=? AND status='pendente'", (dest_id,)).fetchone()
    if not row:
        return jsonify({"error": "Solicitação não encontrada (já foi decidida?)."}), 404
    eh_gerente = session.get("perfil") in ("admin", "master")
    if not eh_gerente and not _local_e_do_meu_cd(row["local_solicitado"]):
        return jsonify({"error": "Você só pode aprovar solicitações do seu próprio CD."}), 403
    local = row["local_solicitado"]
    with get_db() as db:
        db.execute("UPDATE xsense_destinatarios SET status='aprovado', ativo=1 WHERE id=?", (dest_id,))
    _definir_local_destinatario(session.get("user", ""), dest_id, local, True)
    registrar_log(session.get("user", ""), "xsense_aprovar_destinatario", alvo=row["nome"], detalhe=local)
    return jsonify({"ok": True})


@xsense_bp.route("/api/xsense/destinatarios/<int:dest_id>/rejeitar", methods=["POST"])
@login_required
@modulo_required("xsense")
@csrf_required
def destinatario_rejeitar(dest_id):
    if not _pode_gerenciar_destinatarios():
        return jsonify({"error": "Sem acesso."}), 403
    with get_db() as db:
        row = db.execute("SELECT * FROM xsense_destinatarios WHERE id=? AND status='pendente'", (dest_id,)).fetchone()
    if not row:
        return jsonify({"error": "Solicitação não encontrada (já foi decidida?)."}), 404
    eh_gerente = session.get("perfil") in ("admin", "master")
    if not eh_gerente and not _local_e_do_meu_cd(row["local_solicitado"]):
        return jsonify({"error": "Você só pode rejeitar solicitações do seu próprio CD."}), 403
    with get_db() as db:
        db.execute("DELETE FROM xsense_destinatarios WHERE id=?", (dest_id,))
        db.execute("DELETE FROM xsense_destinatario_camara WHERE destinatario_id=?", (dest_id,))
    registrar_log(session.get("user", ""), "xsense_rejeitar_destinatario", alvo=row["nome"],
                 detalhe=row["local_solicitado"])
    return jsonify({"ok": True})


@xsense_bp.route("/api/xsense/destinatarios/<int:dest_id>/local", methods=["POST"])
@login_required
@modulo_required("xsense")
@csrf_required
def destinatario_definir_local(dest_id):
    """Grade de check-box por CD (pedido do usuário 26/08/2026): liga/
    desliga UM local pra esse destinatário já aprovado — cada coordenador/
    analista só mexe na coluna do próprio CD; gerente mexe em qualquer
    uma. Cobre tanto "pessoa trocou de CD" (desliga um, liga outro) quanto
    "passou a cuidar de mais de um CD" (liga um a mais, sem desligar o
    que já tinha)."""
    if not _pode_gerenciar_destinatarios():
        return jsonify({"error": "Sem acesso."}), 403
    d = request.json or {}
    local = str(d.get("local", "")).strip()
    incluir = bool(d.get("incluir", True))
    if not local:
        return jsonify({"error": "Informe o local."}), 400
    eh_gerente = session.get("perfil") in ("admin", "master")
    if not eh_gerente and not _local_e_do_meu_cd(local):
        return jsonify({"error": "Você só pode gerenciar o próprio CD."}), 403
    with get_db() as db:
        existe = db.execute("SELECT 1 FROM xsense_destinatarios WHERE id=?", (dest_id,)).fetchone()
    if not existe:
        return jsonify({"error": "Destinatário não encontrado."}), 404
    _definir_local_destinatario(session.get("user", ""), dest_id, local, incluir)
    return jsonify({"ok": True})


@xsense_bp.route("/api/xsense/destinatarios/<int:dest_id>/gerente", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def destinatario_definir_gerente(dest_id):
    """Marca/desmarca um destinatário como "vê tudo" (flag explícita,
    26/08/2026) — só admin/master pode, é exatamente a ideia que o usuário
    trouxe: "podemos permitir que gerentes cadastrem o id do chat como
    gerente". Substitui a regra antiga (implícita, câmara-filtro vazio =
    vê tudo) que causava CD novo entrar sozinho pra quem estivesse nesse
    estado, sem ninguém decidir isso de propósito."""
    d = request.json or {}
    marcar = bool(d.get("eh_gerente", True))
    with get_db() as db:
        row = db.execute("SELECT * FROM xsense_destinatarios WHERE id=?", (dest_id,)).fetchone()
        if not row:
            return jsonify({"error": "Destinatário não encontrado."}), 404
        db.execute("UPDATE xsense_destinatarios SET eh_gerente=? WHERE id=?", (1 if marcar else 0, dest_id))
    registrar_log(session.get("user", ""), "xsense_destinatario_gerente", alvo=row["nome"],
                 detalhe="marcado" if marcar else "desmarcado")
    return jsonify({"ok": True})


@xsense_bp.route("/api/xsense/destinatarios/broadcast", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def destinatarios_broadcast():
    """Manda uma mensagem avulsa (aviso de atualização, manutenção
    programada etc.) pra todos os destinatários ativos com Telegram
    configurado — fora do fluxo de alerta de câmara, sem passar pelo
    motor de detecção nem pelo cooldown (pedido do usuário 14/08/2026)."""
    from app.database import get_xsense_telegram_token
    from app.services.telegram_alertas import enviar_mensagem

    texto = str((request.json or {}).get("mensagem", "")).strip()
    if not texto:
        return jsonify({"error": "Mensagem vazia."}), 400
    if len(texto) > 3500:
        return jsonify({"error": "Mensagem muito longa (máx. 3500 caracteres)."}), 400

    token = get_xsense_telegram_token()
    if not token:
        return jsonify({"error": "Bot do Telegram não configurado (⚙️ Configurações → Bot Telegram)."}), 400

    with get_db() as db:
        dests = db.execute(
            "SELECT * FROM xsense_destinatarios WHERE ativo=1 AND telegram_chat_id != ''").fetchall()
    if not dests:
        return jsonify({"error": "Nenhum destinatário ativo com Telegram cadastrado."}), 400

    enviados, falhas = 0, []
    for dest in dests:
        ok, msg = enviar_mensagem(token, dest["telegram_chat_id"], texto)
        if ok:
            enviados += 1
        else:
            falhas.append(dest["nome"])

    registrar_log(session.get("user", ""), "xsense_broadcast_telegram",
                 detalhe=f"{enviados} enviado(s), {len(falhas)} falha(s)")
    msg = f"{enviados} destinatário(s) notificado(s)."
    if falhas:
        msg += f" Falhou pra: {', '.join(falhas)}."
    return jsonify({"ok": True, "enviados": enviados, "falhas": falhas, "msg": msg})


@xsense_bp.route("/api/xsense/telegram/contatos_pendentes", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def telegram_contatos_pendentes():
    """Consulta o bot (getUpdates) por gente que mandou /start recentemente
    e ainda não está cadastrada como destinatário — pra copiar o chat_id
    na hora de criar/editar. Cada consulta consome as mensagens (offset
    avança), então se perder a lista, é só pedir pra pessoa mandar de novo."""
    from app.database import (get_xsense_telegram_config, get_xsense_telegram_token,
                              set_xsense_telegram_offset)
    from app.services.telegram_alertas import buscar_atualizacoes

    token = get_xsense_telegram_token()
    if not token:
        return jsonify({"error": "Bot do Telegram não configurado (⚙️ Configurações, no painel principal)."}), 400

    cfg = get_xsense_telegram_config()
    ok, msg, contatos, novo_offset = buscar_atualizacoes(token, cfg["offset_updates"])
    if not ok:
        return jsonify({"error": msg}), 502
    set_xsense_telegram_offset(novo_offset)

    with get_db() as db:
        ja_cadastrados = {r["telegram_chat_id"] for r in db.execute(
            "SELECT telegram_chat_id FROM xsense_destinatarios WHERE telegram_chat_id != ''").fetchall()}
    novos = [c for c in contatos if c["chat_id"] not in ja_cadastrados]
    return jsonify({"ok": True, "contatos": novos})


# ── comandos do bot (self-service — /start, /status, /pararalertas, /retomaralertas) ──
_ESTADO_EMOJI = {"NORMAL": "🟢", "EM_OBSERVACAO": "🟡", "ALERTANDO": "🔴", "RECUPERADO": "🟢"}


def _texto_boas_vindas() -> str:
    return (
        "👋 <b>Monitoramento CD Demo — Câmaras Sensores</b>\n\n"
        "Você vai receber por aqui os alertas de temperatura das câmaras frias "
        "(excursão fora da faixa, oscilação e quando normaliza de novo).\n\n"
        "<b>Comandos:</b>\n"
        "/painel — abre o painel visual (Mini App)\n"
        "/foratemp — câmaras fora da faixa agora, por CD (resumo ou gráfico)\n"
        "/status — mostra botões pra escolher a câmara (ou \"Todas\")\n"
        "/status nome — direto, sem botão (ex.: /status CR3)\n"
        "/grafico — mostra botões pra escolher a câmara pro gráfico\n"
        "/grafico nome — direto, sem botão (ex.: /grafico CR3)\n"
        "/pararalertas — parar de receber alertas\n"
        "/retomaralertas — voltar a receber\n"
        "/glossario — o que significa cada status e as regras de cada câmara\n"
        "/ajuda — mostra essa mensagem de novo"
    )


def _texto_glossario() -> str:
    partes = [
        "📖 <b>O que significa cada status</b>\n",
        "🟢 <b>NORMAL</b> — temperatura dentro da faixa ideal. Tudo certo.",
        "🟡 <b>EM OBSERVAÇÃO</b> — saiu da faixa numa leitura só. O sistema "
        "\"fica de olho\" mas ainda não alerta — pode ter sido só um pico "
        "passageiro (porta aberta um instante).",
        "🔴 <b>ALERTANDO (Excursão)</b> — ficou fora da faixa por leituras "
        "seguidas. Confirmado, dispara alerta aqui no Telegram.",
        "🔴 <b>ALERTANDO (Oscilação)</b> — mesmo sem ficar fora por muito "
        "tempo de cada vez, está entrando e saindo da faixa repetidas "
        "vezes numa janela curta — sinal de instabilidade (porta que não "
        "fecha direito, compressor com problema, etc.).",
        "🟢 <b>RECUPERADO</b> — voltou a ficar dentro da faixa por leituras "
        "seguidas, depois de ter alertado. Manda um aviso de \"normalizou\", "
        "sempre, mesmo que a câmara tenha severidade mínima alta.\n",
        "⚙️ <b>Regras configuradas por câmara:</b>",
    ]
    with get_db() as db:
        camaras = db.execute("SELECT * FROM xsense_camaras WHERE ativo=1 ORDER BY nome").fetchall()
    if not camaras:
        partes.append("Nenhuma câmara ativa cadastrada ainda.")
    for cam in camaras:
        nome = cam["rotulo"] or cam["nome"]
        linhas_cam = [f"\n<b>{nome}</b> — faixa {cam['temp_min']}°C a {cam['temp_max']}°C"]
        if cam["temp_limite_duro"] is not None:
            linhas_cam.append(f"Limite crítico: {cam['temp_limite_duro']}°C (alerta na 1ª leitura, sem esperar)")
        linhas_cam.append(f"Excursão: {cam['n_sustentado']} leitura(s) fora seguidas")
        linhas_cam.append(f"Oscilação: {cam['k_oscilacao']}+ cruzamentos de faixa em {cam['w_janela_min']} min")
        linhas_cam.append(f"Recuperação: {cam['m_recuperacao']} leitura(s) dentro seguidas")
        linhas_cam.append(f"Cooldown: espera {cam['cooldown_min']} min antes de repetir o mesmo alerta")
        if not cam["alertas_ativos"]:
            linhas_cam.append("🔕 Alertas desligados nessa câmara (só monitora, não avisa)")
        partes.append("\n".join(linhas_cam))
    return "\n".join(partes)


def _resolver_camaras(db, filtro: str | None, locais_permitidos: list | None = None):
    """Acha câmara(s) ativa(s) por nome/rótulo/local (case-insensitive,
    parcial) — ex.: "/status CDA" traz todas as câmaras daquele local, não
    só uma câmara chamada "CDA". filtro vazio/None = todas (dentro do que
    for permitido). locais_permitidos (None = sem restrição, pedido do
    usuário 26/08/2026: o /status de quem só tem acesso a alguns CDs não
    pode nem enxergar câmara de outro CD, mesmo digitando o nome certo)."""
    todas = db.execute("SELECT * FROM xsense_camaras WHERE ativo=1 ORDER BY nome").fetchall()
    if locais_permitidos is not None:
        todas = [c for c in todas if c["local"] in locais_permitidos]
    if not filtro:
        return todas, None
    filtro_norm = filtro.strip().lower()
    bateram = [c for c in todas if filtro_norm in (c["nome"] or "").lower()
              or filtro_norm in (c["rotulo"] or "").lower()
              or filtro_norm in (c["local"] or "").lower()]
    if not bateram:
        opcoes = ", ".join((c["rotulo"] or c["nome"]) for c in todas) or "nenhuma disponível pra você"
        return [], f"Não achei nenhuma câmara com \"{filtro}\" no nome/local (ou você não tem acesso). Disponíveis: {opcoes}"
    return bateram, None


def _texto_status_24h(camaras=None) -> str:
    with get_db() as db:
        if camaras is None:
            camaras = db.execute("SELECT * FROM xsense_camaras WHERE ativo=1 ORDER BY nome").fetchall()
        if not camaras:
            return "Nenhuma câmara ativa cadastrada ainda."
        from datetime import datetime, timedelta
        desde = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        linhas = ["📊 <b>Últimas 24h</b>"]
        for cam in camaras:
            agg = db.execute("""
                SELECT MIN(valor) mn, MAX(valor) mx FROM xsense_leituras
                WHERE camara_id=? AND dimensao='temperatura' AND ts>=?
            """, (cam["id"], desde)).fetchone()
            ultima = db.execute("""
                SELECT valor, ts FROM xsense_leituras WHERE camara_id=? AND dimensao='temperatura'
                ORDER BY ts DESC LIMIT 1
            """, (cam["id"],)).fetchone()
            est = db.execute("SELECT estado FROM xsense_estado WHERE camara_id=? AND dimensao='temperatura'",
                             (cam["id"],)).fetchone()
            nome = cam["rotulo"] or cam["nome"]
            if cam["local"]:
                nome = f"{nome} <i>({cam['local']})</i>"
            if not ultima:
                linhas.append(f"\n⚪ <b>{nome}</b>\nSem leituras.")
                continue
            emoji = _ESTADO_EMOJI.get((est["estado"] if est else "NORMAL"), "⚪")
            linha = [f"\n{emoji} <b>{nome}</b>", f"Agora: {ultima['valor']:.1f}°C (faixa {cam['temp_min']}-{cam['temp_max']}°C)"]
            if agg and agg["mn"] is not None:
                linha.append(f"Mín: {agg['mn']:.1f}°C | Máx: {agg['mx']:.1f}°C")
            if est and est["estado"] not in ("NORMAL", "RECUPERADO"):
                linha.append(f"⚠️ {est['estado'].replace('_',' ')}")
            minutos_desde = (datetime.now() - datetime.strptime(ultima["ts"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            if minutos_desde > SINAL_LIMITE_MIN:
                linha.append(f"📡❌ Sem sinal há {fmt_duracao_min(int(minutos_desde))}")
            linhas.append("\n".join(linha))
        return "\n".join(linhas)


def _teclado_camaras(db, prefixo: str, locais_permitidos: list | None = None) -> list:
    """Monta o teclado inline pros comandos /status e /grafico sem
    argumento — callback_data no formato "st:<id>"/"gr:<id>" (ou
    "st:all"/"gr:all", "st:local:<nome>", "st:porcamara").

    Com 2+ locais cadastrados, PERGUNTA O LOCAL PRIMEIRO (um botão por CD +
    "Todos os locais" + "Ver por câmara" pra quem quer ir direto numa
    câmara específica sem passar pelo local) — evita uma lista só de
    câmaras misturando CDs diferentes quando expandir pra mais de uma
    empresa/instalação (pedido do usuário 14/08/2026). Com 1 local só,
    pula direto pra lista de câmaras — ver _teclado_camaras_flat.

    locais_permitidos (None = sem restrição) escopa os CDs oferecidos ao
    que a PESSOA que está perguntando tem acesso — pedido do usuário
    26/08/2026: "quem tem acesso a 1 CD só nem precisa que o bot pergunte,
    quem tem 2+ o bot pergunta [dentre os que ela tem]"."""
    camaras = db.execute("SELECT local FROM xsense_camaras WHERE ativo=1").fetchall()
    locais = sorted({cam["local"] for cam in camaras if cam["local"]})
    if locais_permitidos is not None:
        locais = [l for l in locais if l in locais_permitidos]
    if len(locais) > 1:
        linhas = [[(f"📍 {local[:24]}", f"{prefixo}:local:{local}")] for local in locais]
        linhas.append([("📋 Todos os locais", f"{prefixo}:all")])
        linhas.append([("🔍 Ver por câmara", f"{prefixo}:porcamara")])
        return linhas
    if len(locais) == 1:
        return _teclado_camaras_flat(db, prefixo, locais[0])
    return _teclado_camaras_flat(db, prefixo, locais_permitidos=locais_permitidos)


def _teclado_camaras_flat(db, prefixo: str, local: str | None = None, locais_permitidos: list | None = None) -> list:
    """Lista plana de câmaras ativas (um botão por câmara + 'Todas') —
    usado direto quando só existe 1 local (dentro do permitido), ou como
    segundo passo depois que a pessoa escolhe "Ver por câmara" (todas) ou
    um local específico no teclado de _teclado_camaras."""
    query = "SELECT id, nome, rotulo, local FROM xsense_camaras WHERE ativo=1"
    params = []
    if local:
        query += " AND local=?"
        params.append(local)
    camaras = db.execute(query + " ORDER BY nome", params).fetchall()
    if locais_permitidos is not None:
        camaras = [c for c in camaras if c["local"] in locais_permitidos]
    linhas = [[(cam["rotulo"] or cam["nome"], f"{prefixo}:{cam['id']}")] for cam in camaras]
    linhas.append([("📋 Todas" + (" deste local" if local else ""), f"{prefixo}:all" if not local else f"{prefixo}:local:{local}")])
    return linhas


def _resolver_camaras_por_alvo(db, alvo: str, locais_permitidos: list | None):
    """Resolve o "alvo" de um callback (all / local:<nome> / <camara_id>)
    pra lista de câmaras, respeitando locais_permitidos (None = sem
    restrição). Usado por /status, /grafico e /foratemp — inclusive no
    2º passo do /foratemp, que reconstrói o alvo original a partir do
    callback_data "ftv:<modo>:<alvo>". Devolve None (não [] ) quando o
    alvo pedido está fora do que a pessoa tem acesso — deixa o chamador
    escolher a mensagem de erro certa."""
    if alvo == "all":
        camaras = db.execute("SELECT * FROM xsense_camaras WHERE ativo=1 ORDER BY nome").fetchall()
        if locais_permitidos is not None:
            camaras = [c for c in camaras if c["local"] in locais_permitidos]
        return camaras
    if alvo.startswith("local:"):
        local_nome = alvo[len("local:"):]
        if locais_permitidos is not None and local_nome not in locais_permitidos:
            return None
        return db.execute("SELECT * FROM xsense_camaras WHERE ativo=1 AND local=? ORDER BY nome",
                          (local_nome,)).fetchall()
    cam = db.execute("SELECT * FROM xsense_camaras WHERE id=? AND ativo=1", (alvo,)).fetchone()
    if cam and locais_permitidos is not None and cam["local"] not in locais_permitidos:
        return None
    return [cam] if cam else []


def _filtrar_fora_da_faixa(camaras: list) -> list:
    """Filtra só as câmaras cujo estado ATUAL (dimensão temperatura, mesma
    que already usada pro emoji de status em _texto_status_24h) não está
    NORMAL/RECUPERADO — pro /foratemp (pedido do usuário 26/08/2026: "ver
    câmaras que estão fora da temperatura")."""
    if not camaras:
        return []
    with get_db() as db:
        ids = [c["id"] for c in camaras]
        placeholders = ",".join("?" * len(ids))
        estados = {r["camara_id"]: r["estado"] for r in db.execute(
            f"SELECT camara_id, estado FROM xsense_estado WHERE dimensao='temperatura' AND camara_id IN ({placeholders})",
            ids).fetchall()}
    return [c for c in camaras if estados.get(c["id"], "NORMAL") not in ("NORMAL", "RECUPERADO")]


SINAL_LIMITE_MIN = 50  # mesmo limiar do banner-coleta e do card, ver web/templates/xsense.html


def _filtrar_sem_sinal(camaras: list) -> list:
    """Filtra só as câmaras sem leitura nova há mais de SINAL_LIMITE_MIN —
    mesmo critério usado no card do painel principal (achado ao vivo
    29/08/2026, filtro "Só sem sinal" pedido pro painel e pro Mini App)."""
    if not camaras:
        return []
    from datetime import datetime
    agora = datetime.now()
    with get_db() as db:
        ids = [c["id"] for c in camaras]
        placeholders = ",".join("?" * len(ids))
        ultimas = {r["camara_id"]: r["ultima_leitura_ts"] for r in db.execute(
            f"SELECT camara_id, ultima_leitura_ts FROM xsense_estado WHERE dimensao='temperatura' AND camara_id IN ({placeholders})",
            ids).fetchall()}
    def sem_sinal(cam):
        ts = ultimas.get(cam["id"])
        if not ts:
            return True
        try:
            minutos = (agora - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        except ValueError:
            return True
        return minutos > SINAL_LIMITE_MIN
    return [c for c in camaras if sem_sinal(c)]


def _teclado_locais_solicitacao(db) -> list:
    """Teclado do /start (26/08/2026) — pergunta de QUAL CD a pessoa quer
    receber alerta antes de cadastrar (fluxo de aprovação por CD). Um botão
    por local só, sem "Todos" — a solicitação é sempre de 1 CD por vez;
    quem cobre mais de um CD ganha o segundo depois, pela aprovação."""
    linhas = _locais_conhecidos(db)
    return [[(f"📍 {local[:30]}", f"reg:{local}")] for local in linhas]


def _enviar_status_ou_grafico(token: str, chat_id: str, acao: str, camaras: list) -> None:
    from app.services.telegram_alertas import enviar_mensagem, enviar_foto
    from app.services.xsense_chart import gerar_grafico_camara
    if acao == "st":
        enviar_mensagem(token, chat_id, _texto_status_24h(camaras))
        return
    plural = "s" if len(camaras) > 1 else ""
    enviar_mensagem(token, chat_id, f"⏳ Gerando o{plural} gráfico{plural}, aguarde um instante...")
    for cam in camaras:
        png = gerar_grafico_camara(cam["id"], horas=24)
        legenda = cam["rotulo"] or cam["nome"]
        if png:
            enviar_foto(token, chat_id, png, legenda)
        else:
            enviar_mensagem(token, chat_id, f"📈 {legenda} — sem leituras nas últimas 24h.")


def _processar_um_evento(token: str, upd: dict) -> None:
    """Processa UM evento (mensagem ou tap de botão) e manda a resposta —
    núcleo compartilhado entre o polling (processar_comandos_telegram, usado
    pelo cron/botão manual) e o webhook (resposta instantânea). Nunca
    levanta — problemas de envio já voltam (ok, msg) das funções do
    telegram_alertas e só ficam printados, pra um evento com erro não
    travar o processamento dos outros."""
    from app.services.telegram_alertas import enviar_mensagem, enviar_botoes, responder_callback

    chat_id = upd["chat_id"]
    nome = upd["nome"] or upd["username"] or "Sem nome"

    with get_db() as db:
        dest = db.execute("SELECT * FROM xsense_destinatarios WHERE telegram_chat_id=?", (chat_id,)).fetchone()

    # ── tap num botão (seleção de câmara / de CD no cadastro) ──────
    if upd.get("tipo") == "callback":
        responder_callback(token, upd.get("callback_query_id", ""))
        acao, _, alvo = (upd["texto"] or "").partition(":")

        if acao == "reg":
            # escolheu o CD no fluxo de solicitação de alerta (/start)
            if dest:
                if dest["status"] == "pendente":
                    enviar_mensagem(token, chat_id,
                        f"⏳ Você já tem uma solicitação pendente para <b>{dest['local_solicitado']}</b>. "
                        "Aguarde um responsável aprovar.")
                else:
                    enviar_mensagem(token, chat_id, "Você já está cadastrado — manda /ajuda pra ver os comandos.")
                return
            with get_db() as db:
                db.execute("""
                    INSERT INTO xsense_destinatarios(nome, telegram_chat_id, severidade_min, ativo, status, local_solicitado)
                    VALUES (?,?,?,0,'pendente',?)
                """, (nome, chat_id, "MEDIUM", alvo))
            registrar_log("telegram_bot", "xsense_solicitar_alerta", alvo=nome, detalhe=alvo)
            enviar_mensagem(token, chat_id,
                f"✅ Solicitação enviada para receber alertas de <b>{alvo}</b>.\n\n"
                "Assim que um coordenador/analista desse CD (ou um gerente) aprovar, "
                "você começa a receber os alertas por aqui automaticamente.")
            return

        if not dest:
            enviar_mensagem(token, chat_id, "Você ainda não está cadastrado — manda /start primeiro.")
            return

        if acao == "ftv":
            # 2º passo do /foratemp: "ftv:<modo>:<alvo original>" — modo é
            # "st" (resumo) ou "gr" (gráfico), alvo é o mesmo formato do "ft"
            # (all / local:<nome> / <camara_id>)
            modo, _, alvo_orig = alvo.partition(":")
            with get_db() as db:
                locais_permitidos = _locais_permitidos_do_destinatario(db, dest)
                camaras = _resolver_camaras_por_alvo(db, alvo_orig, locais_permitidos)
            if camaras is None:
                enviar_mensagem(token, chat_id, "Você não tem acesso a esse CD.")
                return
            camaras = _filtrar_fora_da_faixa(camaras)
            if not camaras:
                enviar_mensagem(token, chat_id, "🟢 Nenhuma câmara fora da faixa agora nesse escopo.")
                return
            _enviar_status_ou_grafico(token, chat_id, modo, camaras)
            return

        if acao not in ("st", "gr", "ft"):
            return
        with get_db() as db:
            locais_permitidos = _locais_permitidos_do_destinatario(db, dest)
        if alvo == "porcamara":
            # escape hatch do teclado "escolha o local primeiro" — lista
            # todas as câmaras de todos os locais QUE A PESSOA TEM ACESSO,
            # uma por uma
            with get_db() as db:
                teclado = _teclado_camaras_flat(db, acao, locais_permitidos=locais_permitidos)
            titulo = {"st": "📊 Escolha a câmara:", "gr": "📈 Escolha a câmara pro gráfico:",
                     "ft": "Escolha a câmara:"}[acao]
            enviar_botoes(token, chat_id, titulo, teclado)
            return
        with get_db() as db:
            camaras = _resolver_camaras_por_alvo(db, alvo, locais_permitidos)
        if camaras is None:
            enviar_mensagem(token, chat_id, "Você não tem acesso a esse CD.")
            return
        if acao == "ft":
            camaras = _filtrar_fora_da_faixa(camaras)
            if not camaras:
                enviar_mensagem(token, chat_id, "🟢 Nenhuma câmara fora da faixa agora nesse escopo.")
                return
            enviar_botoes(token, chat_id, "Como você quer ver?",
                         [[("📊 Resumo", f"ftv:st:{alvo}")], [("📈 Gráfico", f"ftv:gr:{alvo}")]])
            return
        if camaras:
            _enviar_status_ou_grafico(token, chat_id, acao, camaras)
        return

    # ── mensagem de texto normal ────────────────────────────────────
    texto_original = (upd["texto"] or "").strip()
    partes = texto_original.split(maxsplit=1)
    comando = (partes[0] if partes else "").lower()
    argumento = partes[1].strip() if len(partes) > 1 else ""

    if comando.startswith("/start"):
        if dest and dest["status"] == "pendente":
            resposta = (f"⏳ Sua solicitação de acesso a alertas de <b>{dest['local_solicitado']}</b> "
                       "ainda está aguardando aprovação de um responsável.")
        elif dest:
            resposta = _texto_boas_vindas()
        else:
            # pergunta de qual CD antes de cadastrar — vira 'pendente' até
            # um coordenador/analista daquele CD (ou gerente) aprovar
            with get_db() as db:
                teclado = _teclado_locais_solicitacao(db)
            if not teclado:
                resposta = "Nenhum CD com câmara cadastrada ainda — fala com o administrador."
            else:
                ok, erro_envio = enviar_botoes(token, chat_id, "📍 De qual CD você quer receber os alertas?", teclado)
                # resultado era descartado antes (achado ao vivo 29/08/2026:
                # usuário mandou /start, nada chegou pra ela, e não sobrou
                # nenhum rastro no log pra investigar) — agora, se o Telegram
                # recusar o envio por qualquer motivo, pelo menos fica
                # registrado, em vez de falhar em silêncio total.
                if not ok:
                    print(f"[XSENSE_TELEGRAM] Falha ao enviar teclado do /start pra {chat_id}: {erro_envio}")
                resposta = None
    elif comando.startswith("/ajuda") or comando.startswith("/help"):
        resposta = _texto_boas_vindas()
    elif comando.startswith("/painel"):
        # Mini App (WebView) — painel visual das câmaras (26/08/2026)
        if not dest:
            resposta = "Você ainda não está cadastrado — manda /start primeiro."
        elif dest["status"] == "pendente":
            resposta = (f"⏳ Sua solicitação de acesso a alertas de <b>{dest['local_solicitado']}</b> "
                       "ainda está aguardando aprovação de um responsável.")
        else:
            from app.database import get_xsense_telegram_config
            site_url = get_xsense_telegram_config().get("site_url", "")
            if not site_url:
                resposta = "O painel ainda não foi configurado — fala com o administrador (Configurações → Bot Telegram)."
            else:
                from app.services.telegram_alertas import enviar_botao_webapp
                enviar_botao_webapp(token, chat_id, "🖥 Toque no botão pra abrir o painel visual:",
                                    "📊 Abrir painel", f"{site_url}/xsense/miniapp")
                resposta = None
    elif comando.startswith("/glossario"):
        resposta = _texto_glossario()
    elif comando.startswith("/status") or comando.startswith("/grafico"):
        acao = "st" if comando.startswith("/status") else "gr"
        if not dest:
            resposta = "Você ainda não está cadastrado — manda /start primeiro."
        elif not argumento:
            # sem argumento — em vez de despejar tudo de uma vez, manda
            # botão pra escolher a câmara (pedido do usuário 14/08/2026);
            # com 2+ locais PERMITIDOS pra essa pessoa, _teclado_camaras
            # pergunta o local primeiro (pedido do usuário 14/08/2026) —
            # escopado ao que ela tem acesso (pedido do usuário 26/08/2026:
            # quem só tem 1 CD nem precisa que o bot pergunte)
            with get_db() as db:
                locais_permitidos = _locais_permitidos_do_destinatario(db, dest)
                teclado = _teclado_camaras(db, acao, locais_permitidos)
                todos_locais = {c["local"] for c in
                                db.execute("SELECT local FROM xsense_camaras WHERE ativo=1").fetchall()
                                if c["local"]}
                if locais_permitidos is not None:
                    todos_locais &= set(locais_permitidos)
                multi_local = len(todos_locais) > 1
            if multi_local:
                titulo = "📍 Escolha o local:" if acao == "st" else "📍 Escolha o local pro gráfico:"
            else:
                titulo = "📊 Escolha a câmara:" if acao == "st" else "📈 Escolha a câmara pro gráfico:"
            enviar_botoes(token, chat_id, titulo, teclado)
            resposta = None
        else:
            with get_db() as db:
                locais_permitidos = _locais_permitidos_do_destinatario(db, dest)
                camaras, erro = _resolver_camaras(db, argumento, locais_permitidos)
            if erro:
                resposta = erro
            else:
                _enviar_status_ou_grafico(token, chat_id, acao, camaras)
                resposta = None
    elif comando.startswith("/foratemp"):
        # ver só câmaras fora da faixa agora, por CD (pedido do usuário
        # 26/08/2026 — útil pro gerente bater o olho no que tá ruim sem
        # abrir câmara por câmara)
        if not dest:
            resposta = "Você ainda não está cadastrado — manda /start primeiro."
        else:
            with get_db() as db:
                locais_permitidos = _locais_permitidos_do_destinatario(db, dest)
                teclado = _teclado_camaras(db, "ft", locais_permitidos)
                todos_locais = {c["local"] for c in
                                db.execute("SELECT local FROM xsense_camaras WHERE ativo=1").fetchall()
                                if c["local"]}
                if locais_permitidos is not None:
                    todos_locais &= set(locais_permitidos)
            titulo = "📍 Fora da faixa em qual CD?" if len(todos_locais) > 1 else "Fora da faixa em qual câmara?"
            enviar_botoes(token, chat_id, titulo, teclado)
            resposta = None
    elif comando.startswith("/pararalertas"):
        if dest:
            with get_db() as db:
                db.execute("UPDATE xsense_destinatarios SET ativo=0 WHERE id=?", (dest["id"],))
            resposta = "🔕 Alertas desativados. Manda /retomaralertas quando quiser voltar a receber."
        else:
            resposta = "Você ainda não está cadastrado — manda /start primeiro."
    elif comando.startswith("/retomaralertas"):
        if dest and dest["status"] == "aprovado":
            with get_db() as db:
                db.execute("UPDATE xsense_destinatarios SET ativo=1 WHERE id=?", (dest["id"],))
            resposta = "🔔 Alertas reativados."
        elif dest and dest["status"] == "pendente":
            resposta = (f"⏳ Sua solicitação de acesso a alertas de <b>{dest['local_solicitado']}</b> "
                       "ainda está aguardando aprovação de um responsável.")
        else:
            resposta = "Você ainda não está cadastrado — manda /start primeiro."
    else:
        resposta = "Não entendi 🤔 — manda /ajuda pra ver os comandos disponíveis."

    if resposta is not None:
        enviar_mensagem(token, chat_id, resposta)


def processar_comandos_telegram() -> dict:
    """
    Roda um ciclo de polling (getUpdates) e processa os eventos recebidos
    via _processar_um_evento(). Só funciona se o webhook NÃO estiver ativo
    (ver /api/xsense/telegram/webhook) — com webhook ligado, getUpdates é
    recusado pelo Telegram, então essa função (e o cron que a chama,
    scripts/processar_telegram_bot.py) fica sem efeito, o que é esperado:
    o webhook já responde na hora, não precisa mais desse polling.
    """
    from app.database import get_xsense_telegram_config, get_xsense_telegram_token, set_xsense_telegram_offset
    from app.services.telegram_alertas import buscar_atualizacoes

    token = get_xsense_telegram_token()
    if not token:
        return {"ok": False, "msg": "Bot não configurado."}

    cfg = get_xsense_telegram_config()
    ok, msg, atualizacoes, novo_offset = buscar_atualizacoes(token, cfg["offset_updates"])
    if not ok:
        return {"ok": False, "msg": msg}
    # salva a posição só DEPOIS de responder (não antes) — descoberto
    # 14/08/2026: um "database is locked" transitório nessa gravação
    # derrubava o script inteiro ANTES do for-loop rodar, perdendo a
    # resposta de um clique de botão sem nem tentar responder. Assim, na
    # pior hipótese (erro no meio do loop) o próximo ciclo reprocessa os
    # mesmos eventos (inofensivo — responder de novo não quebra nada) em
    # vez de sumir com a resposta.
    for upd in atualizacoes:
        _processar_um_evento(token, upd)

    set_xsense_telegram_offset(novo_offset)
    return {"ok": True, "msg": f"{len(atualizacoes)} evento(s) processado(s).", "processados": len(atualizacoes)}


@xsense_bp.route("/api/xsense/telegram/processar_comandos", methods=["POST"])
@login_required
@modulo_required("xsense")
@admin_required
@csrf_required
def telegram_processar_comandos_route():
    """Dispara um ciclo manual de processamento de comandos — útil enquanto
    o cron (scripts/processar_telegram_bot.py) não está configurado na VM."""
    r = processar_comandos_telegram()
    if not r.get("ok"):
        return jsonify({"error": r.get("msg", "Falha.")}), 502
    return jsonify(r)


@xsense_bp.route("/api/xsense/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """
    Endpoint público — chamado pelo Telegram (não pelo navegador), por isso
    sem @login_required/@csrf_required. A segurança vem do header
    X-Telegram-Bot-Api-Secret-Token, que só o Telegram sabe (combinado na
    hora de registrar o webhook, ver /api/master/xsense/telegram/webhook
    em server.py) — qualquer POST sem o header certo é rejeitado antes de
    tocar em qualquer lógica.

    Responde 200 rápido SEMPRE — o processamento de verdade roda numa
    thread separada, não na resposta em si. Isso importa principalmente
    pro /grafico: manda várias fotos, uma de cada vez, e pode passar dos
    poucos segundos que o Telegram espera por um webhook; se demorar
    demais, o Telegram acha que falhou e reenvia a mesma atualização,
    duplicando as fotos. Devolver 200 na hora evita isso, e devolver 200
    mesmo em erro interno evita reenvio em loop pelo mesmo motivo.
    """
    from app.database import get_xsense_telegram_token, get_xsense_telegram_webhook_secret
    from app.services.telegram_alertas import parse_update

    secret_recebido = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    secret_esperado = get_xsense_telegram_webhook_secret()
    if not secret_recebido or not hmac.compare_digest(secret_recebido, secret_esperado):
        return jsonify({"ok": False}), 403

    token = get_xsense_telegram_token()
    if not token:
        return jsonify({"ok": True})  # nada a fazer, mas responde 200 mesmo assim

    upd = request.get_json(force=True, silent=True) or {}
    evento = parse_update(upd)
    if evento:
        def _rodar():
            try:
                _processar_um_evento(token, evento)
            except Exception as e:
                print(f"[XSENSE_TELEGRAM_WEBHOOK] Erro ao processar: {e}")
        threading.Thread(target=_rodar, daemon=True).start()
    return jsonify({"ok": True})


# ── processamento de leitura (núcleo — usado pelo scraper E pelo lançamento manual) ──
def processar_leitura(camara_id: str, dimensao: str, valor: float, ts: str | None = None) -> list[dict]:
    """
    Grava a leitura, roda o motor de detecção (app.services.xsense_service),
    persiste o novo estado e os eventos, e devolve os eventos que viraram
    push de verdade (já passados pelo cooldown) — pra quem chamar decidir
    se dispara WhatsApp/Telegram ou só loga (hoje: só loga, envio ainda não
    está plugado — ver cabeçalho do arquivo).
    """
    ts = ts or agora_brt_str()
    with get_db() as db:
        cam_row = db.execute("SELECT * FROM xsense_camaras WHERE id=?", (camara_id,)).fetchone()
        if not cam_row:
            raise ValueError(f"Câmara {camara_id!r} não cadastrada.")
        if not cam_row["ativo"]:
            raise ValueError(f"Câmara {cam_row['nome']!r} está inativa — reative antes de lançar leituras.")
        db.execute("INSERT OR IGNORE INTO xsense_leituras(camara_id, dimensao, ts, valor) VALUES (?,?,?,?)",
                  (camara_id, dimensao, ts, valor))

        if not cam_row["alertas_ativos"]:
            # câmara continua sendo lida/monitorada (gráfico, temperatura no
            # card) — só não roda o motor de detecção nem gera evento/push.
            # Ainda assim mantém "última leitura" em dia, senão o card mostra
            # hora velha mesmo recebendo leitura nova.
            db.execute("""
                INSERT INTO xsense_estado(camara_id, dimensao, estado, ultima_leitura_ts)
                VALUES (?,?,'NORMAL',?)
                ON CONFLICT(camara_id, dimensao) DO UPDATE SET
                  estado='NORMAL', condicao_ativa=NULL, severidade=NULL, ultima_leitura_ts=excluded.ultima_leitura_ts
            """, (camara_id, dimensao, ts))
            return []

        est_row = db.execute("SELECT * FROM xsense_estado WHERE camara_id=? AND dimensao=?",
                             (camara_id, dimensao)).fetchone()
        estado = EstadoCamara(**{k: est_row[k] for k in
                                 ("estado", "condicao_ativa", "severidade", "desde_ts",
                                  "ultima_leitura_ts", "ultimo_alerta_ts", "contador_fora", "contador_dentro")}) \
                 if est_row else EstadoCamara()

        cfg = _config_camara(cam_row, dimensao)
        if cfg.minimo is None or cfg.maximo is None:
            # sem faixa configurada pra essa dimensão (ex.: câmara sem
            # limite de umidade cadastrado) — grava a leitura, mas não dá
            # pra avaliar excursão/oscilação sem faixa nenhuma pra comparar
            db.execute("""
                INSERT INTO xsense_estado(camara_id, dimensao, estado, ultima_leitura_ts)
                VALUES (?,?,'NORMAL',?)
                ON CONFLICT(camara_id, dimensao) DO UPDATE SET ultima_leitura_ts=excluded.ultima_leitura_ts
            """, (camara_id, dimensao, ts))
            return []

        # janela de leituras recentes (folga de 3x a janela de oscilação, mínimo 6h)
        from datetime import datetime, timedelta
        desde = (datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") -
                timedelta(minutes=max(cfg.w_janela_min * 3, 360))).strftime("%Y-%m-%d %H:%M:%S")
        rows_janela = db.execute("""
            SELECT ts, valor FROM xsense_leituras WHERE camara_id=? AND dimensao=? AND ts>=? ORDER BY ts ASC
        """, (camara_id, dimensao, desde)).fetchall()
        leituras = [Leitura(r["ts"], r["valor"]) for r in rows_janela]

        novo_estado, eventos = avaliar(cfg, leituras, estado)

        pushes = []
        for ev in eventos:
            vira_push = aplicar_cooldown(ev, novo_estado, cfg, ts)
            if vira_push:
                # RECUPERACAO zera o relógio do cooldown em vez de reiniciá-lo:
                # ela sempre notifica (ignora cooldown), mas se ficasse marcando
                # ultimo_alerta_ts com o próprio horário, uma recaída rápida
                # (câmara volta a alertar minutos depois de "recuperada") ficava
                # silenciada até completar cooldown_min contado da RECUPERAÇÃO —
                # a recaída é um episódio novo, não deveria herdar o cooldown do
                # episódio anterior. Descoberto ao vivo (14/08/2026): CG4
                # recuperou às 15:20 e alertou de novo às ~15:30, e nada chegou
                # no Telegram porque o cooldown de 60min contava a partir da
                # RECUPERAÇÃO, não do alerta original.
                novo_estado.ultimo_alerta_ts = None if ev.tipo == "RECUPERACAO" else ts
            cur = db.execute("""
                INSERT INTO xsense_eventos(camara_id, dimensao, tipo, severidade, ts_inicio, valor, detalhe, notificado)
                VALUES (?,?,?,?,?,?,?,?)
            """, (camara_id, dimensao, ev.tipo, ev.severidade, ts, ev.valor, ev.detalhe, 1 if vira_push else 0))
            if vira_push:
                pushes.append({"camara_id": camara_id, "dimensao": dimensao, "tipo": ev.tipo,
                              "severidade": ev.severidade, "valor": ev.valor, "detalhe": ev.detalhe,
                              "lembrete": ev.lembrete, "ts": ts, "evento_id": cur.lastrowid})

        db.execute("""
            INSERT INTO xsense_estado(camara_id, dimensao, estado, condicao_ativa, severidade, desde_ts,
                ultima_leitura_ts, ultimo_alerta_ts, contador_fora, contador_dentro)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(camara_id, dimensao) DO UPDATE SET
              estado=excluded.estado, condicao_ativa=excluded.condicao_ativa, severidade=excluded.severidade,
              desde_ts=excluded.desde_ts, ultima_leitura_ts=excluded.ultima_leitura_ts,
              ultimo_alerta_ts=excluded.ultimo_alerta_ts, contador_fora=excluded.contador_fora,
              contador_dentro=excluded.contador_dentro
        """, (camara_id, dimensao, novo_estado.estado, novo_estado.condicao_ativa, novo_estado.severidade,
              novo_estado.desde_ts, novo_estado.ultima_leitura_ts, novo_estado.ultimo_alerta_ts,
              novo_estado.contador_fora, novo_estado.contador_dentro))

    if pushes:
        _notificar_pushes(cam_row, pushes)
        _gravar_canais(pushes)
    return pushes


def _gravar_canais(pushes: list[dict], db=None) -> None:
    """Guarda no evento quantos destinatários receberam (telegram:entregues/
    elegíveis). Com `db`, usa a transação de quem chamou — o ciclo de sinal
    notifica de dentro dela, e uma segunda conexão esperaria a trava."""
    pares = [(p["canais"], p["evento_id"]) for p in pushes
             if p.get("canais") and p.get("evento_id")]
    if not pares:
        return
    try:
        if db is not None:
            db.executemany("UPDATE xsense_eventos SET canais=? WHERE id=?", pares)
        else:
            with get_db() as conn:
                conn.executemany("UPDATE xsense_eventos SET canais=? WHERE id=?", pares)
    except Exception as e:
        print(f"[XSENSE_TELEGRAM] Não gravou o resultado do envio: {e}")


def _notificar_pushes(cam_row, pushes: list[dict]) -> None:
    """Manda os pushes pro Telegram (canal ativo enquanto o WhatsApp não
    destrava — ver app/services/telegram_alertas.py). Nunca deixa uma
    falha de envio derrubar o processamento da leitura — mesmo espírito do
    registrar_log(): alertar é importante, mas não pode ser mais importante
    que a leitura em si ter sido salva."""
    try:
        from app.database import get_xsense_telegram_token
        from app.services.telegram_alertas import enviar_mensagem, enviar_foto, formatar_mensagem_evento
        from app.services.xsense_service import SEVERIDADE_ORDEM
        from app.services.xsense_chart import gerar_grafico_camara

        token = get_xsense_telegram_token()
        if not token:
            return

        import html
        nome_exibicao = cam_row["rotulo"] or cam_row["nome"]
        if cam_row["local"]:
            nome_exibicao = f"{nome_exibicao} ({cam_row['local']})"
        # o texto vai com parse_mode=HTML: um "&" ou "<" no rótulo fazia o
        # Telegram recusar a mensagem inteira
        nome_exibicao = html.escape(nome_exibicao, quote=False)
        with get_db() as db:
            dests = db.execute("SELECT * FROM xsense_destinatarios WHERE ativo=1 AND telegram_chat_id != ''").fetchall()
            filtros = {}
            for r in db.execute("SELECT * FROM xsense_destinatario_camara").fetchall():
                filtros.setdefault(r["destinatario_id"], set()).add(r["camara_id"])
            local_por_cam = {r["id"]: r["local"] for r in
                             db.execute("SELECT id, local FROM xsense_camaras").fetchall()}
        local_da_camara = _norm_local(cam_row["local"])

        for push in pushes:
            texto = formatar_mensagem_evento(nome_exibicao, push)
            # gráfico das últimas 12h — contexto suficiente pro evento sem
            # gerar o mesmo PNG de novo pra cada destinatário. Lembrete
            # periódico ("ainda fora da faixa") vai só em texto, sem
            # gráfico — pedido do usuário 29/08/2026: já recebeu o
            # gráfico no alerta original, o lembrete só precisa confirmar
            # local/câmara/temperatura atual/tempo fora, rápido de ler.
            # Falha no gráfico não pode calar o alerta: cai para texto.
            png = None
            if push["dimensao"] == "temperatura" and not push.get("lembrete"):
                try:
                    png = gerar_grafico_camara(cam_row["id"], horas=12)
                except Exception as e:
                    print(f"[XSENSE_TELEGRAM] Gráfico indisponível, alerta segue em texto: {e}")
            elegiveis = entregues = 0
            for dest in dests:
                # Pendente de aprovação não recebe: quem pediu alerta pelo bot
                # espera o admin liberar (fluxo de 06/10/2026).
                if str(dest["status"] or "") == "pendente":
                    continue
                if not _destinatario_recebe(dest, filtros.get(dest["id"]) or set(),
                                            cam_row["id"], local_da_camara, local_por_cam):
                    continue
                # RECUPERACAO sempre passa — fecha o ciclo de quem recebeu o
                # alerta original, mesmo que a severidade dela (INFO) fique
                # abaixo do mínimo configurado pela pessoa
                if push["tipo"] != "RECUPERACAO" and SEVERIDADE_ORDEM.get(push["severidade"], 0) < \
                        SEVERIDADE_ORDEM.get(dest["severidade_min"], 0):
                    continue
                elegiveis += 1
                if png:
                    ok, msg = enviar_foto(token, dest["telegram_chat_id"], png, texto)
                else:
                    ok, msg = enviar_mensagem(token, dest["telegram_chat_id"], texto)
                if ok:
                    entregues += 1
                else:
                    print(f"[XSENSE_TELEGRAM] Falha ao notificar {dest['nome']!r}: {msg}")
            # "Push ✅" na tela era decidido ANTES do envio (cooldown) e não
            # dizia se alguém recebeu — a CG8 mostrava ✅ sem chegar a
            # ninguém (09/10/2026). O resultado real vai junto do evento.
            push["canais"] = f"telegram:{entregues}/{elegiveis}"
    except Exception as e:
        print(f"[XSENSE_TELEGRAM] Erro ao notificar: {e}")


def _resumos_por_local(ini, fim) -> dict:
    """{local normalizado: (local como cadastrado, texto)} do período."""
    from datetime import timedelta
    from app.services import xsense_resumo as xr
    desde = (ini - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    ate = fim.strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        camaras = [dict(r) for r in db.execute(
            # Só câmara com "gerar alertas" marcado entra no resumo (usuário,
            # 14/10/2026: antecâmara com alerta desligado aparecia). Lido a
            # cada envio, então a marcação da tela vale no fechamento seguinte.
            "SELECT * FROM xsense_camaras WHERE ativo=1 AND alertas_ativos=1 "
            "ORDER BY local, COALESCE(NULLIF(rotulo,''), nome)"
        ).fetchall()]
        leituras: dict[str, list] = {}
        for r in db.execute(
                "SELECT camara_id, ts, valor FROM xsense_leituras WHERE dimensao='temperatura' "
                "AND ts >= ? AND ts < ? ORDER BY camara_id, ts", (desde, ate)).fetchall():
            t = xr._ts(r["ts"])
            if t is not None and r["valor"] is not None:
                leituras.setdefault(r["camara_id"], []).append((t, float(r["valor"])))
    grupos: dict[str, tuple] = {}
    for cam in camaras:
        chave = _norm_local(cam.get("local"))
        cam["nome_exibicao"] = cam.get("rotulo") or cam.get("nome")
        sit = xr.situacao_camara(cam, leituras.get(cam["id"], []), ini, fim, SINAL_LIMITE_MIN)
        rotulo, lista = grupos.get(chave, (cam.get("local") or "", []))
        # o mesmo local grafado com e sem acento: o título fica com o acento
        local = cam.get("local") or ""
        if sum(ord(ch) > 127 for ch in local) > sum(ord(ch) > 127 for ch in rotulo):
            rotulo = local
        lista.append(sit)
        grupos[chave] = (rotulo, lista)
    return {k: (rot, xr.texto_do_local(rot, lista, ini, fim))
            for k, (rot, lista) in sorted(grupos.items())}


def enviar_resumo(ini, fim) -> dict:
    """Manda o resumo do período a cada destinatário aprovado: gerente
    recebe todos os locais; os demais, os CDs marcados para ele (mesma régua
    do alerta, _locais_do_destinatario). Uma mensagem por local."""
    from app.database import get_xsense_telegram_token
    from app.services.telegram_alertas import enviar_mensagem
    token = get_xsense_telegram_token()
    if not token:
        return {"ok": False, "msg": "Bot do Telegram não configurado.", "entregues": 0, "elegiveis": 0}
    resumos = _resumos_por_local(ini, fim)
    with get_db() as db:
        dests = db.execute("SELECT * FROM xsense_destinatarios WHERE ativo=1 "
                           "AND telegram_chat_id != '' AND status != 'pendente'").fetchall()
        filtros: dict[int, set] = {}
        for r in db.execute("SELECT * FROM xsense_destinatario_camara").fetchall():
            filtros.setdefault(r["destinatario_id"], set()).add(r["camara_id"])
        local_por_cam = {r["id"]: r["local"] for r in
                         db.execute("SELECT id, local FROM xsense_camaras").fetchall()}
    entregues = elegiveis = 0
    for dest in dests:
        locais = _locais_do_destinatario(dest, filtros.get(dest["id"]) or set(), local_por_cam)
        for chave, (_, texto) in resumos.items():
            if locais is not None and chave not in locais:
                continue
            elegiveis += 1
            try:
                ok, msg = enviar_mensagem(token, dest["telegram_chat_id"], texto)
            except Exception as e:
                ok, msg = False, str(e)
            if ok:
                entregues += 1
            else:
                print(f"[XSENSE_RESUMO] Falha ao enviar para {dest['nome']!r}: {msg}")
    return {"ok": True, "msg": f"{entregues} de {elegiveis} mensagem(ns) entregue(s).",
            "entregues": entregues, "elegiveis": elegiveis, "locais": len(resumos)}


def enviar_resumo_se_devido(agora=None) -> dict | None:
    """Chamado pelo cron da coleta (a cada 5 min). Envia o resumo do último
    fechamento (06h, 14h, 22h) uma vez só. Fechamento perdido há mais de 1
    hora — VM fora do ar, por exemplo — não é enviado atrasado: o próximo
    cobre a situação atual."""
    from datetime import datetime, timedelta
    from app.services import xsense_resumo as xr
    agora = agora or datetime.strptime(agora_brt_str(), "%Y-%m-%d %H:%M:%S")
    slot = xr.slot_atual(agora)
    if agora - slot > timedelta(hours=1):
        return None
    chave = slot.strftime("%Y-%m-%d %H:%M")
    with get_db() as db:
        cur = db.execute("INSERT OR IGNORE INTO xsense_resumo_envio(slot, enviado_em) VALUES (?,?)",
                         (chave, agora.strftime("%Y-%m-%d %H:%M:%S")))
        if not cur.rowcount:
            return None  # outro ciclo já mandou este fechamento
    r = enviar_resumo(slot - timedelta(hours=xr.JANELA_H), slot)
    with get_db() as db:
        db.execute("UPDATE xsense_resumo_envio SET entregues=?, elegiveis=? WHERE slot=?",
                   (r.get("entregues", 0), r.get("elegiveis", 0), chave))
    return {**r, "slot": chave}


@xsense_bp.route("/api/xsense/resumo/previa")
@login_required
@modulo_required("xsense")
@admin_required
def resumo_previa():
    """Texto do resumo das últimas 8 horas, sem enviar — para conferir o que
    os destinatários recebem."""
    from datetime import datetime, timedelta
    from app.services import xsense_resumo as xr
    fim = datetime.strptime(agora_brt_str(), "%Y-%m-%d %H:%M:%S")
    ini = fim - timedelta(hours=xr.JANELA_H)
    with get_db() as db:
        envios = [dict(r) for r in db.execute(
            "SELECT * FROM xsense_resumo_envio ORDER BY slot DESC LIMIT 6").fetchall()]
    return jsonify({"resumos": [{"local": rot, "texto": txt}
                                for rot, txt in _resumos_por_local(ini, fim).values()],
                    "horarios": list(xr.HORARIOS), "envios": envios})


def _norm_local(texto) -> str:
    """Local comparável: sem acento, caixa alta, espaços simples."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.upper().split())


def _locais_do_destinatario(dest, camaras_filtro: set, local_por_cam: dict) -> set | None:
    """CDs (normalizados) que o destinatário acompanha — None = todos.

    O filtro é gerido CD a CD na tela (os chips de Local), mas era gravado
    como a lista de câmaras que o CD tinha NAQUELE momento, e o envio
    comparava pelo id da câmara. Câmara cadastrada depois — a CG8 do CDA —
    ficava de fora para todo mundo que já tinha filtro, com o push marcado
    ✅ (achado em 09/10/2026). Agora o filtro vale pelo CD: as câmaras
    gravadas dizem QUAIS CDs a pessoa acompanha, e qualquer câmara desses
    CDs, nova ou antiga, entra.

      • gerente de alerta: todos, sempre — inclusive com filtro velho
        gravado de antes de virar gerente;
      • com CD marcado: os CDs marcados;
      • sem CD marcado: nenhum. É o que a tela mostra (nenhum chip ligado),
        e a tela de destinatários avisa que a pessoa está sem CD.
    """
    if dest["eh_gerente"]:
        return None
    locais = {_norm_local(local_por_cam.get(cid)) for cid in camaras_filtro}
    locais.discard("")
    return locais


def _destinatario_recebe(dest, camaras_filtro: set, camara_id: str, local_da_camara: str,
                         local_por_cam: dict) -> bool:
    """Se o alerta desta câmara vai para este destinatário (régua em
    _locais_do_destinatario)."""
    if camara_id in camaras_filtro:
        return True
    locais = _locais_do_destinatario(dest, camaras_filtro, local_por_cam)
    if locais is None:
        return True
    return bool(local_da_camara) and local_da_camara in locais


# ── Mini App (Telegram WebView) — v1 só-leitura, 26/08/2026 ───────────
# Sem login/senha do site: autentica pelo initData que o próprio Telegram
# assina (ver app/services/telegram_miniapp_auth.py). Escopo v1 (decidido
# com o usuário): dashboard de câmaras + fora da faixa + gráfico — igual
# ao que já existe pro bot em texto/botão, só com uma tela visual melhor.
# Aprovar pendente fica de fora por enquanto (precisaria vincular
# chat_id ↔ login do site, que não existe ainda).
def _autenticar_miniapp():
    """Lê o initData do header X-Telegram-Init-Data, valida a assinatura e
    resolve o destinatário (xsense_destinatarios) dono daquele chat_id.
    Devolve (dest_row, locais_permitidos, erro_resposta) — erro_resposta
    já vem pronto (jsonify, status) quando algo falha, None quando ok."""
    from app.database import get_xsense_telegram_token
    from app.services.telegram_miniapp_auth import chat_id_do_init_data

    token = get_xsense_telegram_token()
    if not token:
        return None, None, (jsonify({"error": "Bot do Telegram não configurado."}), 400)
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    chat_id = chat_id_do_init_data(init_data, token)
    if not chat_id:
        return None, None, (jsonify({"error": "Sessão do Telegram inválida — reabra o painel pelo bot."}), 401)
    with get_db() as db:
        dest = db.execute("SELECT * FROM xsense_destinatarios WHERE telegram_chat_id=?", (chat_id,)).fetchone()
        if not dest:
            return None, None, (jsonify({"error": "Você ainda não está cadastrado — manda /start pro bot primeiro."}), 403)
        if dest["status"] == "pendente":
            return None, None, (jsonify({
                "error": f"Sua solicitação de acesso a {dest['local_solicitado']} ainda está aguardando aprovação."
            }), 403)
        locais_permitidos = _locais_permitidos_do_destinatario(db, dest)
    return dest, locais_permitidos, None


@xsense_bp.route("/xsense/miniapp")
def miniapp_pagina():
    """Página do Mini App — sem login_required de propósito (é a WebView
    do Telegram abrindo, não tem sessão de site nenhuma; a autenticação
    real acontece nas chamadas de API, via initData)."""
    return render_template("xsense_miniapp.html")


@xsense_bp.route("/api/xsense/miniapp/camaras")
def miniapp_camaras():
    dest, locais_permitidos, erro = _autenticar_miniapp()
    if erro:
        return erro
    apenas_fora = request.args.get("fora_da_faixa") == "1"
    apenas_sem_sinal = request.args.get("sem_sinal") == "1"
    local_filtro = request.args.get("local", "")

    with get_db() as db:
        rows = db.execute("SELECT * FROM xsense_camaras WHERE ativo=1 ORDER BY nome").fetchall()
        camaras = list(rows)
        if locais_permitidos is not None:
            camaras = [c for c in camaras if c["local"] in locais_permitidos]
        if local_filtro:
            camaras = [c for c in camaras if c["local"] == local_filtro]
        if apenas_fora:
            camaras = _filtrar_fora_da_faixa(camaras)
        if apenas_sem_sinal:
            camaras = _filtrar_sem_sinal(camaras)

        itens = []
        for cam in camaras:
            est = db.execute("SELECT * FROM xsense_estado WHERE camara_id=? AND dimensao='temperatura'",
                             (cam["id"],)).fetchone()
            ultima = db.execute("""
                SELECT ts, valor FROM xsense_leituras WHERE camara_id=? AND dimensao='temperatura'
                ORDER BY ts DESC LIMIT 1
            """, (cam["id"],)).fetchone()
            itens.append({
                "id": cam["id"], "nome": cam["rotulo"] or cam["nome"], "local": cam["local"],
                "tipo": cam["tipo"], "temp_min": cam["temp_min"], "temp_max": cam["temp_max"],
                "estado": (est["estado"] if est else "NORMAL"),
                "temp_atual": ultima["valor"] if ultima else None,
                "ultima_leitura_ts": ultima["ts"] if ultima else None,
            })
        locais_disponiveis = _locais_conhecidos(db)
        if locais_permitidos is not None:
            locais_disponiveis = [l for l in locais_disponiveis if l in locais_permitidos]
    return jsonify({"camaras": itens, "locais": locais_disponiveis,
                    "nome": dest["nome"], "ve_tudo": locais_permitidos is None})


@xsense_bp.route("/api/xsense/miniapp/grafico/<camara_id>")
def miniapp_grafico(camara_id):
    dest, locais_permitidos, erro = _autenticar_miniapp()
    if erro:
        return erro
    with get_db() as db:
        cam = db.execute("SELECT * FROM xsense_camaras WHERE id=? AND ativo=1", (camara_id,)).fetchone()
        if not cam:
            return jsonify({"error": "Câmara não encontrada."}), 404
        if locais_permitidos is not None and cam["local"] not in locais_permitidos:
            return jsonify({"error": "Você não tem acesso a essa câmara."}), 403
        from datetime import datetime, timedelta
        desde_24h = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        pontos = db.execute("""
            SELECT ts, valor FROM xsense_leituras WHERE camara_id=? AND dimensao='temperatura' AND ts>=?
            ORDER BY ts ASC
        """, (camara_id, desde_24h)).fetchall()
    return jsonify({
        "nome": cam["rotulo"] or cam["nome"], "temp_min": cam["temp_min"], "temp_max": cam["temp_max"],
        "pontos": [{"ts": r["ts"], "valor": r["valor"]} for r in pontos],
    })


# ── dashboard ────────────────────────────────────────────────────────
@xsense_bp.route("/api/xsense/dashboard")
@login_required
@modulo_required("xsense")
def dashboard():
    from datetime import datetime, timedelta
    desde_8h = (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        camaras_rows = db.execute("SELECT * FROM xsense_camaras ORDER BY nome").fetchall()
        # ESTA é a rota que monta os cards da página do Sensores, e ela lia
        # todas as câmaras de todos os CDs — o vazamento que o usuário
        # relatou em 06/10/2026 ("o usuário de outra unidade está
        # visualizando todas as camaras de todos os locais"). O filtro que
        # já existia estava em dashboard_mensal, outra rota.
        permitidos = _locais_permitidos_sessao(db)
        camaras_rows = [c for c in camaras_rows if _camara_visivel(c, permitidos)]
        estados = {(r["camara_id"], r["dimensao"]): dict(r)
                  for r in db.execute("SELECT * FROM xsense_estado").fetchall()}
        itens = []
        for cam in camaras_rows:
            cam_d = dict(cam)
            ultimas = db.execute("""
                SELECT ts, valor FROM xsense_leituras WHERE camara_id=? AND dimensao='temperatura' AND ts>=?
                ORDER BY ts ASC
            """, (cam["id"], desde_8h)).fetchall()
            if not ultimas:
                # sem leitura nas últimas 8h (câmara sem coleta recente) —
                # ainda mostra o último ponto conhecido, senão o card fica
                # sem gráfico nenhum mesmo tendo dado (só que velho)
                ultima = db.execute("""
                    SELECT ts, valor FROM xsense_leituras WHERE camara_id=? AND dimensao='temperatura'
                    ORDER BY ts DESC LIMIT 1
                """, (cam["id"],)).fetchone()
                ultimas = [ultima] if ultima else []
            cam_d["ultimas_leituras"] = [dict(r) for r in ultimas]
            cam_d["estado_temperatura"] = estados.get((cam["id"], "temperatura"))
            cam_d["estado_umidade"] = estados.get((cam["id"], "umidade"))
            itens.append(cam_d)
    return jsonify({"camaras": itens})


@xsense_bp.route("/api/xsense/eventos")
@login_required
@modulo_required("xsense")
def eventos():
    camara_id = request.args.get("camara_id", "")
    severidade = request.args.get("severidade", "")
    limite = min(int(request.args.get("limite", 100) or 100), 500)
    query = "SELECT * FROM xsense_eventos WHERE 1=1"
    params: list = []
    if camara_id:
        query += " AND camara_id=?"; params.append(camara_id)
    if severidade:
        query += " AND severidade=?"; params.append(severidade.upper())
    query += " ORDER BY ts_inicio DESC LIMIT ?"; params.append(limite)
    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        # Esta rota não toca em xsense_camaras, e por isso passou pela
        # primeira varredura: ela devolvia o histórico de excursão de
        # temperatura de TODOS os CDs para qualquer usuário do módulo
        # (achado na revisão pré-deploy, 08/10/2026). O recorte é por
        # câmara, resolvido pelo cadastro.
        permitidos = _locais_permitidos_sessao(db)
        if permitidos is not None:
            visiveis = {c["id"] for c in db.execute(
                "SELECT id, local FROM xsense_camaras").fetchall()
                if _camara_visivel(c, permitidos)}
            rows = [r for r in rows if r["camara_id"] in visiveis]
    return jsonify({"eventos": [dict(r) for r in rows]})


@xsense_bp.route("/api/xsense/dashboard_mensal")
@login_required
@modulo_required("xsense")
def dashboard_mensal():
    from datetime import datetime
    hoje = datetime.now()
    try:
        ano = int(request.args.get("ano") or hoje.year)
        mes = int(request.args.get("mes") or hoje.month)
        if not (1 <= mes <= 12):
            raise ValueError
    except ValueError:
        return jsonify({"error": "ano/mes inválidos."}), 400

    mes_anterior = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    params = get_xsense_dashboard_params()
    local_filtro = request.args.get("local", "").strip()

    with get_db() as db:
        locais_permitidos = _locais_permitidos_sessao(db)
        locais_disponiveis = _locais_conhecidos(db)
        if locais_permitidos is not None:
            locais_disponiveis = [l for l in locais_disponiveis if l in locais_permitidos]

        locais_escopo = locais_permitidos
        if local_filtro:
            if local_filtro not in locais_disponiveis:
                return jsonify({"error": "CD inválido ou fora do seu acesso."}), 400
            locais_escopo = [local_filtro]

        atual = _calcular_indicadores_mensais(db, ano, mes, locais_escopo, params)
        anterior = _calcular_indicadores_mensais(db, mes_anterior[0], mes_anterior[1], locais_escopo, params)

    return jsonify({
        "ano": ano, "mes": mes,
        "locais_disponiveis": locais_disponiveis,
        "parametros": params,
        "atual": atual,
        "anterior": {"total_eventos": anterior["total_eventos"],
                    "total_minutos_fora": anterior["total_minutos_fora"],
                    "uptime_medio": anterior["uptime_medio"],
                    "eficiencia_media": anterior["eficiencia_media"]},
    })


@xsense_bp.route("/api/xsense/dashboard_mensal/parametros", methods=["GET", "POST"])
@login_required
@modulo_required("xsense")
@csrf_required
def dashboard_mensal_parametros():
    if request.method == "GET":
        return jsonify(get_xsense_dashboard_params())

    if session.get("perfil") not in ("admin", "master"):
        return jsonify({"error": "Só Gerente pode alterar os parâmetros de risco."}), 403
    d = request.json or {}
    try:
        alto = float(d.get("eficiencia_alto"))
        medio = float(d.get("eficiencia_medio"))
    except (TypeError, ValueError):
        return jsonify({"error": "Valores inválidos."}), 400
    if not (0 <= alto <= 100 and 0 <= medio <= 100):
        return jsonify({"error": "Os valores devem estar entre 0 e 100."}), 400
    if alto > medio:
        return jsonify({"error": "O limiar de risco Alto não pode ser maior que o de Médio."}), 400
    tipos = d.get("tipos_dashboard")
    if not isinstance(tipos, list) or not all(isinstance(t, str) for t in tipos):
        return jsonify({"error": "tipos_dashboard inválido."}), 400
    tipos_validos = [t for t in tipos if t in ("congelada", "resfriada", "antecamara", "ambiente")]
    if not tipos_validos:
        return jsonify({"error": "Selecione ao menos um tipo de câmara."}), 400
    excluidas = d.get("camaras_excluidas", [])
    if not isinstance(excluidas, list) or not all(isinstance(c, str) for c in excluidas):
        return jsonify({"error": "camaras_excluidas inválido."}), 400
    set_xsense_dashboard_params(alto, medio, tipos_validos, excluidas)
    registrar_log(session.get("user", ""), "xsense_dashboard_parametros",
                 detalhe=f"alto<{alto}% medio<{medio}% tipos={','.join(tipos_validos)} "
                         f"excluidas={len(excluidas)}")
    return jsonify({"ok": True})
