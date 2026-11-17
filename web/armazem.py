"""
Blueprint do módulo "Painel do Armazém" (FEAT-10, fase 1).
Registra em server.py com:
    from web.armazem import armazem_bp, init_armazem_db
    init_armazem_db()
    app.register_blueprint(armazem_bp)

Mesmo padrão de web/adensamento.py: usa o mesmo banco (data/monitoramento.db)
e duplica auth/CSRF/módulo por sessão (evita import circular com server.py).
"""
from __future__ import annotations
import datetime as _dt
import hmac
import io
import os
import tempfile
from functools import wraps
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify, session, send_file

from app.database import get_db, registrar_log
from app.services.mapeamento_colunas import ColunasFaltando
from app.services.armazem_service import (
    parse_produto_paletizado, calcular_metricas, metricas_zeradas, merge_metricas,
    aplicar_capacidade,
    detectar_locais_novos, paletes_por_local, TEMPERATURAS, parse_colagem_qlikview,
    detalhar_linhas, CATEGORIAS_DETALHE,
    gerar_modelo_qlikview, parse_planilha_qlikview, PlanilhaIlegivel,
)
from app.services.export_armazem_image_service import exportar_imagens, exportar_imagens_visoes
from app.utils.saida_link import url_saida
from web.fifo import salvar_fifo_diario
from app.utils.access_control import (
    modulo_required, tem_acesso_modulo, usuario_efetivo as _usuario_efetivo,
    bloquear_escrita, somente_leitura, cds_da_conta_canonica,
)
from app.utils.paths import get_output_path

armazem_bp = Blueprint("armazem", __name__)

CLASSIFICACOES_VALIDAS = {"varejo", "atacado", "devolucao", "desconsiderar"}


def _hoje_brt() -> str:
    """"Hoje" em horário de Brasília, não do servidor — achado ao vivo
    29/08/2026: a VM roda em UTC, então `date.today()` (usado por todo
    esse arquivo) virava o dia seguinte a partir das 21h de Brasília,
    fazendo o upload/snapshot do fim do dia gravar com a data de amanhã
    (calendário abria direto no dia errado, e "hoje" de verdade ficava
    sem nenhum TXT processado). Usar sempre isso em vez de
    `date.today()` quando o sentido for "que dia é hoje pro usuário"."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()

# Nome fixo do local de falta na compra (confirmado na planilha real,
# Painel-Geral!E54) — nasce pré-cadastrado como "desconsiderar" na primeira
# inicialização, pra não aparecer como pendência assim que alguém subir um
# TXT com esse local (só acontece "quando existe tratativas", raro).
_LOCAL_FALTA_COMPRA = "CDA CB FALTA NA COMPRA"

# Último processamento por (usuário efetivo, empresa) — mesmo padrão de
# _states em server.py e _ultimo em adensamento.py (em memória, não
# persiste em disco: histórico "leve" fica só no banco como agregado, ver
# armazem_diario). Chave virou tupla (usuario, empresa) no redesenho
# Local×Empresa (29/08/2026) — antes era só usuario.
_estado: dict[tuple[str, str], dict] = {}


# ── auth (duplicado do padrão server.py/adensamento.py) ───────────────
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
def init_armazem_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS armazem_locais (
            usuario         TEXT NOT NULL DEFAULT '',
            nome            TEXT NOT NULL,
            classificacao   TEXT NOT NULL DEFAULT 'pendente',
            criado_em       DATETIME DEFAULT CURRENT_TIMESTAMP,
            classificado_em DATETIME,
            PRIMARY KEY (usuario, nome)
        );
        CREATE TABLE IF NOT EXISTS armazem_capacidade (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario            TEXT NOT NULL DEFAULT '',
            temperatura        TEXT NOT NULL,
            capacidade_ton     REAL NOT NULL DEFAULT 0,
            vigente_desde      DATE NOT NULL,
            criado_em          DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_armazem_capacidade_busca
            ON armazem_capacidade(usuario, temperatura, vigente_desde);
        CREATE TABLE IF NOT EXISTS armazem_contrato (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario            TEXT NOT NULL DEFAULT '',
            valor_contrato_ton REAL NOT NULL DEFAULT 0,
            vigente_desde      DATE NOT NULL,
            criado_em          DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_armazem_contrato_busca
            ON armazem_contrato(usuario, vigente_desde);
        CREATE TABLE IF NOT EXISTS armazem_capacidade_contratada (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario                   TEXT NOT NULL DEFAULT '',
            empresa                   TEXT NOT NULL DEFAULT '',
            temperatura               TEXT NOT NULL,
            capacidade_contratada_ton REAL NOT NULL DEFAULT 0,
            vigente_desde             DATE NOT NULL,
            criado_em                 DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_armazem_capacidade_contratada_busca
            ON armazem_capacidade_contratada(usuario, empresa, temperatura, vigente_desde);
        CREATE TABLE IF NOT EXISTS armazem_config (
            usuario       TEXT PRIMARY KEY,
            rotulo        TEXT NOT NULL DEFAULT '',
            ambiente_modo TEXT NOT NULL DEFAULT 'consome_resfriado'
        );
        CREATE TABLE IF NOT EXISTS armazem_diario (
            usuario       TEXT NOT NULL DEFAULT '',
            empresa       TEXT NOT NULL DEFAULT '',
            data          DATE NOT NULL,
            processado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            nome_arquivo  TEXT DEFAULT '',
            total_ton     REAL DEFAULT 0,
            total_paletes INTEGER DEFAULT 0,
            metricas_json TEXT DEFAULT '',
            PRIMARY KEY (usuario, empresa, data)
        );
        -- Linhas do Produto Paletizado do dia, para o detalhamento por
        -- clique (quais produtos formam o número de FIFO, vencido, faixa
        -- ou endereçamento). Guardadas por poucos dias: é detalhe de
        -- conferência do dia a dia, e o disco da VM é curto.
        CREATE TABLE IF NOT EXISTS armazem_linhas_dia (
            usuario       TEXT NOT NULL DEFAULT '',
            empresa       TEXT NOT NULL DEFAULT '',
            data          DATE NOT NULL,
            temperatura   TEXT NOT NULL DEFAULT '',
            tipo_produto  TEXT DEFAULT '',
            codigo        TEXT DEFAULT '',
            nome          TEXT DEFAULT '',
            local         TEXT DEFAULT '',
            palete        TEXT DEFAULT '',
            peso_liquido  REAL DEFAULT 0,
            producao      DATE,
            validade      DATE,
            enderecado    INTEGER DEFAULT 0,
            reserva_sigla TEXT DEFAULT '',
            reserva_nome  TEXT DEFAULT '',
            bloqueio      INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_armazem_linhas_dia
            ON armazem_linhas_dia(usuario, empresa, data);
        CREATE TABLE IF NOT EXISTS armazem_qlikview_diario (
            usuario       TEXT NOT NULL DEFAULT '',
            empresa       TEXT NOT NULL DEFAULT '',
            data          DATE NOT NULL,
            temperatura   TEXT NOT NULL,
            atacado_ton   REAL DEFAULT 0,
            varejo_ton    REAL DEFAULT 0,
            devolucao_ton REAL DEFAULT 0,
            terceiros_ton REAL DEFAULT 0,
            paletes       INTEGER,
            capacidade_ton REAL,
            colado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (usuario, empresa, data, temperatura)
        );
        """)
        # Migração (23/09/2026): a tabela nasceu sem empresa e com a chave
        # (usuario, data, temperatura). Sem reconstruir, duas empresas do
        # mesmo Local colidiriam no mesmo dia. O conteúdo antigo vai para a
        # 1ª empresa do Local — no caso CDA/CDB é a CDA, porque a CDB só
        # passa a existir em 01/10.
        cols_q = [r["name"] for r in db.execute("PRAGMA table_info(armazem_qlikview_diario)").fetchall()]
        if "empresa" not in cols_q:
            antigas = db.execute("SELECT * FROM armazem_qlikview_diario").fetchall()
            db.execute("ALTER TABLE armazem_qlikview_diario RENAME TO armazem_qlikview_diario_old")
            db.executescript("""
            CREATE TABLE armazem_qlikview_diario (
                usuario       TEXT NOT NULL DEFAULT '',
                empresa       TEXT NOT NULL DEFAULT '',
                data          DATE NOT NULL,
                temperatura   TEXT NOT NULL,
                atacado_ton   REAL DEFAULT 0,
                varejo_ton    REAL DEFAULT 0,
                devolucao_ton REAL DEFAULT 0,
                terceiros_ton REAL DEFAULT 0,
                paletes       INTEGER,
                capacidade_ton REAL,
                colado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (usuario, empresa, data, temperatura)
            );
            """)
            for r in antigas:
                # prefere a empresa com a sigla da própria conta (CDA na conta
                # do CD Demo); sem correspondência, a primeira do Local
                emps = _empresas_do_usuario(r["usuario"])
                emp = next((e for e in emps if e.strip().upper() == r["usuario"].strip().upper()), emps[0])
                db.execute("""INSERT OR REPLACE INTO armazem_qlikview_diario
                    (usuario, empresa, data, temperatura, atacado_ton, varejo_ton, devolucao_ton,
                     terceiros_ton, paletes, capacidade_ton, colado_em)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["usuario"], emp, r["data"], r["temperatura"], r["atacado_ton"], r["varejo_ton"],
                     r["devolucao_ton"], r["terceiros_ton"], r["paletes"], r["capacidade_ton"], r["colado_em"]))
            db.execute("DROP TABLE armazem_qlikview_diario_old")
            print(f"[DB] Migração: QlikView do Armazém movido para a 1ª empresa de cada Local "
                  f"({len(antigas)} linha(s))")
        # Locais de estoque passaram a ser POR CONTA (apontado pelo usuário
        # 06/10/2026: a conta de CDD enxergava os locais do CDA). A PK muda,
        # então não dá ALTER TABLE: renomeia, cria, copia e descarta.
        #
        # A classificação existente é REPLICADA para todas as contas que já
        # têm dado de Armazém. Sem isso cada CD acordaria com todos os locais
        # em "pendente", e local pendente zera o painel — o estrago seria
        # maior que o problema que a migração resolve.
        cols_locais = [r[1] for r in db.execute("PRAGMA table_info(armazem_locais)").fetchall()]
        tem_old = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='armazem_locais_old'"
        ).fetchone() is not None
        # A tabela `_old` de pé significa migração INCOMPLETA: o DDL commita
        # sozinho no sqlite3 e os INSERTs voltam atrás, então uma falha no meio
        # deixava a tabela nova vazia e a migração era pulada para sempre,
        # perdendo a classificação de todos os locais (achado na revisão
        # pré-deploy, 08/10/2026). Retoma a partir dela.
        if "usuario" not in cols_locais or tem_old:
            fonte = "armazem_locais_old" if tem_old else "armazem_locais"
            antigos = db.execute(
                f"SELECT nome, classificacao, classificado_em FROM {fonte}").fetchall()
            contas = {r["usuario"] for r in db.execute(
                "SELECT DISTINCT usuario FROM armazem_diario "
                "UNION SELECT DISTINCT usuario FROM armazem_config "
                "UNION SELECT DISTINCT usuario FROM armazem_qlikview_diario "
                "UNION SELECT DISTINCT usuario FROM armazem_capacidade "
                "UNION SELECT DISTINCT usuario FROM cd_conta_canonica").fetchall()
                if (r["usuario"] or "").strip()}
            if not tem_old:
                db.execute("ALTER TABLE armazem_locais RENAME TO armazem_locais_old")
            db.execute("""
                CREATE TABLE IF NOT EXISTS armazem_locais (
                    usuario         TEXT NOT NULL DEFAULT '',
                    nome            TEXT NOT NULL,
                    classificacao   TEXT NOT NULL DEFAULT 'pendente',
                    criado_em       DATETIME DEFAULT CURRENT_TIMESTAMP,
                    classificado_em DATETIME,
                    PRIMARY KEY (usuario, nome)
                )""")
            # a conta "" guarda o histórico de antes da migração: serve de
            # molde para conta nova que apareça depois (ver _get_locais)
            alvos = sorted(contas) + [""]
            for conta in alvos:
                for r in antigos:
                    db.execute("INSERT OR IGNORE INTO armazem_locais"
                               "(usuario,nome,classificacao,classificado_em) VALUES(?,?,?,?)",
                               (conta, r["nome"], r["classificacao"], r["classificado_em"]))
            # confere ANTES de descartar: nomes distintos por conta têm de
            # bater com o que havia na tabela antiga
            copiados = db.execute(
                "SELECT COUNT(DISTINCT nome) c FROM armazem_locais").fetchone()["c"]
            if copiados >= len({r["nome"] for r in antigos}):
                db.execute("DROP TABLE armazem_locais_old")
                print(f"[DB] Migração: armazem_locais agora é por conta — "
                      f"{len(antigos)} local(is) replicado(s) para {len(contas)} conta(s)")
            else:
                print(f"[DB] AVISO: migração de armazem_locais incompleta "
                      f"({copiados} de {len({r['nome'] for r in antigos})} locais). "
                      f"armazem_locais_old foi MANTIDA e a migração será retomada "
                      f"no próximo boot.")

        # o local de falta na compra nasce desconsiderado em toda conta
        db.execute("""
            INSERT OR IGNORE INTO armazem_locais(usuario,nome,classificacao,classificado_em)
            VALUES('', ?, 'desconsiderar', CURRENT_TIMESTAMP)
        """, (_LOCAL_FALTA_COMPRA,))
        # modelo_pagamento — quinzenal (2x) ou mensal. Define como o gráfico
        # de Evolução Mensal escolhe qual dia destacar como pico de peso
        # armazenado: quinzenal = 1 pico em cada metade do mês (1-15, 16-fim),
        # mensal = 1 pico só no mês inteiro. Pedido do usuário 22/08/2026.
        cols_config = [r[1] for r in db.execute("PRAGMA table_info(armazem_config)").fetchall()]
        if "modelo_pagamento" not in cols_config:
            db.execute("ALTER TABLE armazem_config ADD COLUMN modelo_pagamento TEXT NOT NULL DEFAULT 'mensal'")
            print("[DB] Migração: coluna modelo_pagamento adicionada em armazem_config")

        # armazem_diario ganha empresa (redesenho Local×Empresa, 29/08/2026)
        # — PK vira (usuario, empresa, data), SQLite não deixa alterar PK
        # in-place, recria a tabela. Snapshot antigo (de antes de existir
        # empresa) vira empresa=usuario — mesma chave interna que
        # _empresas_do_usuario() usa como fallback de "1 empresa só", então
        # continua sendo encontrado por quem nunca teve multi-empresa.
        cols_diario = [r[1] for r in db.execute("PRAGMA table_info(armazem_diario)").fetchall()]
        if "empresa" not in cols_diario:
            db.executescript("""
                DROP TABLE IF EXISTS armazem_diario_old;
                ALTER TABLE armazem_diario RENAME TO armazem_diario_old;
                CREATE TABLE armazem_diario (
                    usuario       TEXT NOT NULL DEFAULT '',
                    empresa       TEXT NOT NULL DEFAULT '',
                    data          DATE NOT NULL,
                    processado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                    nome_arquivo  TEXT DEFAULT '',
                    total_ton     REAL DEFAULT 0,
                    total_paletes INTEGER DEFAULT 0,
                    metricas_json TEXT DEFAULT '',
                    PRIMARY KEY (usuario, empresa, data)
                );
                INSERT INTO armazem_diario
                    (usuario, empresa, data, processado_em, nome_arquivo, total_ton, total_paletes, metricas_json)
                    SELECT usuario, usuario, data, processado_em, nome_arquivo, total_ton, total_paletes, metricas_json
                    FROM armazem_diario_old;
                DROP TABLE IF EXISTS armazem_diario_old;
            """)
            print("[DB] Migração: armazem_diario recriada com empresa (PK usuario+empresa+data)")


def _get_locais(usuario: str = "") -> dict[str, str]:
    """Classificação dos locais DAQUELA conta.

    A conta "" é o molde: guarda o que valia antes de os locais virarem por
    conta, e é de onde uma conta nova herda a classificação em vez de nascer
    com tudo pendente (local pendente zera o painel).
    """
    usuario = (usuario or "").strip().lower()
    with get_db() as db:
        proprios = {r["nome"]: r["classificacao"] for r in db.execute(
            "SELECT nome, classificacao FROM armazem_locais WHERE usuario=?",
            (usuario,)).fetchall()}
        if not usuario:
            return proprios
        molde = {r["nome"]: r["classificacao"] for r in db.execute(
            "SELECT nome, classificacao FROM armazem_locais WHERE usuario=''").fetchall()}
    # O molde entra local por local, não tudo-ou-nada: antes bastava a conta
    # ter UMA linha própria para parar de herdar, e os outros locais viravam
    # desconhecidos de uma vez — o que zera o painel, porque local pendente
    # zera (achado na revisão pré-deploy, 08/10/2026). O que a conta já
    # classificou sempre vence o molde.
    return {**molde, **proprios}


def _ref_data_para_mes(ano_mes: str) -> str:
    """Data de referência pra resolver a capacidade vigente (via
    _get_capacidades) na Evolução Mensal do QlikView — usa o último dia do
    mês exibido, ou hoje se o mês ainda não terminou (evita "capacidade do
    fim do mês" antes de ele acabar). Achado do usuário 24/08/2026:
    capacidade cadastrada em "Capacidade e Contrato" não aparecia no
    gráfico do QlikView."""
    import calendar
    from datetime import date
    ano, mes = (int(x) for x in ano_mes.split("-"))
    ultimo_dia = date(ano, mes, calendar.monthrange(ano, mes)[1]).isoformat()
    hoje = _hoje_brt()
    return min(ultimo_dia, hoje)


def _get_capacidades(usuario: str, data_ref: str | None = None) -> dict[str, float]:
    """
    Capacidade (ton) vigente em `data_ref` (padrão: hoje), por temperatura,
    pra este usuário/CD. Histórico append-only (pedido do usuário 08/08/2026:
    "pode alterar durante o mês, vai servir pra mostrar os limites dia a
    dia") — editar nunca sobrescreve o passado, só adiciona uma nova linha
    vigente a partir de uma data; consultar um dia pega a última linha com
    vigente_desde <= esse dia.
    """
    data_ref = data_ref or _hoje_brt()
    cfg = {temp: 0.0 for temp in TEMPERATURAS}
    with get_db() as db:
        for temp in TEMPERATURAS:
            row = db.execute("""
                SELECT capacidade_ton FROM armazem_capacidade
                WHERE usuario=? AND temperatura=? AND vigente_desde<=?
                ORDER BY vigente_desde DESC, id DESC LIMIT 1
            """, (usuario, temp, data_ref)).fetchone()
            if row:
                cfg[temp] = row["capacidade_ton"]
    return cfg


def _definir_capacidade(usuario: str, temperatura: str, capacidade_ton: float, vigente_desde: str):
    """Registra uma NOVA vigência (não sobrescreve a anterior — é assim que
    o histórico de limites dia-a-dia se forma)."""
    with get_db() as db:
        db.execute("""
            INSERT INTO armazem_capacidade(usuario, temperatura, capacidade_ton, vigente_desde)
            VALUES (?,?,?,?)
        """, (usuario, temperatura, capacidade_ton, vigente_desde))


def _historico_capacidade(usuario: str) -> list[dict]:
    with get_db() as db:
        rows = db.execute("""
            SELECT temperatura, capacidade_ton, vigente_desde
            FROM armazem_capacidade WHERE usuario=? ORDER BY vigente_desde DESC, id DESC
        """, (usuario,)).fetchall()
    return [dict(r) for r in rows]


def _migrar_dados_fallback_para_empresa(usuario: str, empresas: list[str]) -> None:
    """Quando uma conta que já vinha subindo TXT no modo 1-empresa (salvo
    com empresa=usuario, o fallback de antes de ter unidades vinculadas)
    ganha vinculadas de verdade e passa a ter 2+ empresas, o dado antigo
    fica órfão — a tela passa a procurar empresa=CDA/CDB, nunca mais
    empresa=usuario, e o histórico já subido "some" (achado ao vivo
    29/08/2026: usuário configurou CDA+CDB, o calendário da semana que já
    tinha subido ficou vazio). Migra pra primeira empresa da lista (é a
    suposição razoável: quem só tinha 1 empresa até agora só pode ter
    subido dado dela). Checagem é 1 SELECT indexado e barato — roda a
    cada chamada, mas só faz alguma coisa de verdade na primeira vez
    depois da conta ganhar vinculadas (depois disso não sobra fallback
    pra achar, então é sempre um no-op rápido)."""
    if len(empresas) < 2 or usuario in empresas:
        return
    alvo = empresas[0]
    with get_db() as db:
        tem_fallback = db.execute(
            "SELECT 1 FROM armazem_diario WHERE usuario=? AND empresa=? LIMIT 1", (usuario, usuario)
        ).fetchone()
        if not tem_fallback:
            return
        for tabela in ("armazem_diario", "armazem_capacidade_contratada", "armazem_fifo_linhas"):
            # UPDATE OR IGNORE: se já existir uma linha em `alvo` pro mesmo
            # (usuario, data) — ex.: já subiu de novo direto em CDA depois
            # da migração — a linha antiga do fallback não sobrescreve a
            # nova, só fica pra trás; o DELETE seguinte limpa essa sobra.
            db.execute(f"UPDATE OR IGNORE {tabela} SET empresa=? WHERE usuario=? AND empresa=?",
                      (alvo, usuario, usuario))
            db.execute(f"DELETE FROM {tabela} WHERE usuario=? AND empresa=?", (usuario, usuario))
    print(f"[ARMAZEM] Migração: dados de '{usuario}' (modo 1-empresa) movidos pra empresa '{alvo}'")


def _empresas_do_usuario(usuario: str) -> list[str]:
    """Empresas (siglas de CD) desse Local, pro redesenho Local×Empresa —
    ver cds_da_conta_canonica. Sem mapeamento explícito (todo CD fora do
    caso CDA/CDB hoje) = 1 empresa só, usando o próprio `usuario` como
    chave interna (nunca mostrado como nome de empresa na UI — a tela só
    exibe cabeçalho de empresa quando há 2+)."""
    empresas = cds_da_conta_canonica(usuario)
    if not empresas:
        return [usuario]
    _migrar_dados_fallback_para_empresa(usuario, empresas)
    return empresas


def _empresa_pedida(empresas_validas: list[str]) -> str:
    """Empresa do parâmetro, ou o padrão da tela. Com 2+ empresas no Local o
    padrão é a CONSOLIDADA: sem isso a primeira carga da página (que não
    manda o parâmetro ainda) devolvia os números da 1ª empresa enquanto o
    seletor já mostrava "Todas" — números de CDA sob o rótulo de consolidada
    (usuário, 01/10/2026)."""
    pedida = request.args.get("empresa", "").strip()
    if pedida:
        return pedida
    return _TODAS_EMPRESAS if len(empresas_validas) > 1 else empresas_validas[0]


# Sentinela pra "ver todas as empresas juntas" — nunca colide com uma sigla
# de CD de verdade (siglas vêm de cd_conta_canonica, sempre maiúsculas
# curtas tipo CDA/CDB; "TODAS" não é validado contra empresas_validas por
# ser tratado à parte em cada rota que usa esse parâmetro). Pedido do
# usuário 29/08/2026: o seletor só deixava ver 1 empresa por vez, faltava
# uma visão combinada com capacidade/estoque somados.
_TODAS_EMPRESAS = "TODAS"


def _metricas_dia_empresa(usuario: str, empresa: str, data_str: str, hoje_str: str | None = None) -> dict | None:
    """Métricas já calculadas de UMA empresa nesse dia — usa o estado ao
    vivo se for hoje e essa sessão do processo já processou (mais rápido,
    reflete pendência não resolvida), senão cai pro snapshot salvo no
    banco. None = essa empresa não tem dado nesse dia (não entra na soma
    de _TODAS_EMPRESAS)."""
    if hoje_str and data_str == hoje_str:
        estado = _estado.get((usuario, empresa))
        if estado:
            return None if estado["tem_pendencia"] else estado["metricas"]
    snap = _snapshot_do_dia(usuario, empresa, data_str)
    return snap["metricas"] if snap else None


def _visao_consolidada(usuario: str, empresas: list[str], data_str: str,
                       hoje_str: str | None = None) -> tuple[list[dict], list[dict]]:
    """(métricas das empresas que entram na soma, empresas que ficaram de fora).

    Empresa sem painel do dia — ou com local de estoque esperando
    classificação — não tem número pra somar. Isso sempre foi assim, mas
    saía em silêncio: a tela mostrava a soma de uma empresa só, com cara de
    consolidada (achado do usuário em homologação, 01/10/2026 — "CDB entrou
    em cena, mas na ocupação das câmaras tá somente CDA"). Agora a rota diz
    quem ficou de fora e por quê, pra tela poder avisar."""
    dentro, fora = [], []
    for emp in empresas:
        m = _metricas_dia_empresa(usuario, emp, data_str, hoje_str)
        if m is not None:
            dentro.append(m)
            continue
        estado = _estado.get((usuario, emp))
        if estado and estado.get("tem_pendencia"):
            fora.append({"empresa": emp, "motivo": "pendencia",
                         "locais_novos": estado.get("locais_novos") or []})
        else:
            fora.append({"empresa": emp, "motivo": "sem_painel"})
    return dentro, fora


def _get_capacidade_contratada(usuario: str, empresa: str, data_ref: str | None = None) -> dict[str, float]:
    """Mesma lógica de _get_capacidades, mas por empresa — capacidade
    CONTRATADA (o que essa empresa paga por mês), não a física da câmara.
    Pedido do usuário 29/08/2026: por temperatura, igual a física já é."""
    data_ref = data_ref or _hoje_brt()
    cfg = {temp: 0.0 for temp in TEMPERATURAS}
    with get_db() as db:
        for temp in TEMPERATURAS:
            row = db.execute("""
                SELECT capacidade_contratada_ton FROM armazem_capacidade_contratada
                WHERE usuario=? AND empresa=? AND temperatura=? AND vigente_desde<=?
                ORDER BY vigente_desde DESC, id DESC LIMIT 1
            """, (usuario, empresa, temp, data_ref)).fetchone()
            if row:
                cfg[temp] = row["capacidade_contratada_ton"]
    return cfg


def _definir_capacidade_contratada(usuario: str, empresa: str, temperatura: str, capacidade_ton: float, vigente_desde: str):
    with get_db() as db:
        db.execute("""
            INSERT INTO armazem_capacidade_contratada(usuario, empresa, temperatura, capacidade_contratada_ton, vigente_desde)
            VALUES (?,?,?,?,?)
        """, (usuario, empresa, temperatura, capacidade_ton, vigente_desde))


def _historico_capacidade_contratada(usuario: str, empresa: str) -> list[dict]:
    with get_db() as db:
        rows = db.execute("""
            SELECT temperatura, capacidade_contratada_ton, vigente_desde
            FROM armazem_capacidade_contratada WHERE usuario=? AND empresa=? ORDER BY vigente_desde DESC, id DESC
        """, (usuario, empresa)).fetchall()
    return [dict(r) for r in rows]


def _capacidade_fisica_efetiva(usuario: str, empresas: list[str], data_ref: str | None = None) -> dict[str, float]:
    """Capacidade FÍSICA por temperatura, com fallback pra soma da
    capacidade CONTRATADA de todas as empresas do Local quando a física
    ainda não foi configurada (0/vazia) — achado do usuário 29/08/2026: ao
    configurar a capacidade contratada de uma empresa nova (CDB) recém
    adicionada, a física continuou vazia (é um campo à parte, nunca
    derivado automaticamente) e a Ocupação de Câmaras ficou sem barra
    nenhuma por falta de denominador. Enquanto a física real não é digitada,
    a soma do que já foi contratado é uma estimativa razoável (nunca maior
    que a física de verdade); assim que o Gerente salvar um valor físico
    explícito em "Capacidade e Contrato", ele passa a valer de novo, mesmo
    que menor que a soma contratada."""
    fisica = _get_capacidades(usuario, data_ref)
    if all(fisica.get(temp, 0) for temp in TEMPERATURAS):
        return fisica
    soma_contratada = {temp: 0.0 for temp in TEMPERATURAS}
    for emp in empresas:
        contratada = _get_capacidade_contratada(usuario, emp, data_ref)
        for temp in TEMPERATURAS:
            soma_contratada[temp] += contratada.get(temp, 0) or 0
    return {temp: fisica.get(temp, 0) or soma_contratada[temp] for temp in TEMPERATURAS}


def _capacidade_da_visao(usuario: str, empresas_visao: list[str], todas: list[str],
                         data_ref: str | None = None) -> tuple[dict[str, float], str]:
    """(capacidade que serve de denominador pra ocupação DESTA visão, origem).

    Num Local com 2+ empresas a capacidade vem do que cada empresa
    CONTRATOU, e a visão consolidada SOMA as duas — a capacidade física
    cadastrada no CD cobria a empresa que já existia (287/300 do CDA) e,
    com a CDB trazendo espaço próprio, a consolidada passava de 100% de
    ocupação (usuário, 01/10/2026: "se for consolidado ele deve considerar
    CDA + CDB para a capacidade"). Sem nenhuma contratada cadastrada, a
    consolidada ainda cai na física pra não ficar sem barra; a visão de UMA
    empresa fica com 0 e a tela pede o cadastro, em vez de pegar emprestado
    o denominador da outra. CD de uma empresa só segue na física, como
    sempre foi."""
    if len(todas) < 2:
        return _capacidade_fisica_efetiva(usuario, todas, data_ref), "fisica"
    soma = {temp: 0.0 for temp in TEMPERATURAS}
    for emp in empresas_visao:
        contratada = _get_capacidade_contratada(usuario, emp, data_ref)
        for temp in TEMPERATURAS:
            soma[temp] += contratada.get(temp, 0) or 0
    if any(soma.values()):
        return soma, "contratada"
    if len(empresas_visao) >= len(todas):
        return _capacidade_fisica_efetiva(usuario, todas, data_ref), "fisica"
    return soma, "contratada"


def _contratada_da_visao(usuario: str, empresas_visao: list[str], todas: list[str],
                         data_ref: str | None = None) -> dict[str, float] | None:
    """Capacidade CONTRATADA somada das empresas desta visão. None quando o
    Local tem uma empresa só — aí o financeiro continua como sempre foi,
    sem dimensão de empresa."""
    if len(todas) < 2:
        return None
    soma = {temp: 0.0 for temp in TEMPERATURAS}
    for emp in empresas_visao:
        contratada = _get_capacidade_contratada(usuario, emp, data_ref)
        for temp in TEMPERATURAS:
            soma[temp] += contratada.get(temp, 0) or 0
    return soma


def _get_valor_contrato(usuario: str, data_ref: str | None = None) -> float:
    """R$/ton do contrato — UM valor só pro CD inteiro, não por temperatura
    (correção do usuário 08/08/2026: a planilha original só calcula R$ na
    aba Total, contra a capacidade combinada). Mesmo histórico append-only
    da capacidade."""
    data_ref = data_ref or _hoje_brt()
    with get_db() as db:
        row = db.execute("""
            SELECT valor_contrato_ton FROM armazem_contrato
            WHERE usuario=? AND vigente_desde<=? ORDER BY vigente_desde DESC, id DESC LIMIT 1
        """, (usuario, data_ref)).fetchone()
    return row["valor_contrato_ton"] if row else 0.0


def _definir_valor_contrato(usuario: str, valor_contrato_ton: float, vigente_desde: str):
    with get_db() as db:
        db.execute("""
            INSERT INTO armazem_contrato(usuario, valor_contrato_ton, vigente_desde) VALUES (?,?,?)
        """, (usuario, valor_contrato_ton, vigente_desde))


def _historico_contrato(usuario: str) -> list[dict]:
    with get_db() as db:
        rows = db.execute("""
            SELECT valor_contrato_ton, vigente_desde FROM armazem_contrato
            WHERE usuario=? ORDER BY vigente_desde DESC, id DESC
        """, (usuario,)).fetchall()
    return [dict(r) for r in rows]


def _get_config(usuario: str) -> dict:
    with get_db() as db:
        row = db.execute(
            "SELECT rotulo, ambiente_modo, modelo_pagamento FROM armazem_config WHERE usuario=?", (usuario,)
        ).fetchone()
    if row:
        return {"rotulo": row["rotulo"], "ambiente_modo": row["ambiente_modo"],
                "modelo_pagamento": row["modelo_pagamento"]}
    return {"rotulo": "", "ambiente_modo": "consome_resfriado", "modelo_pagamento": "mensal"}


# ── QlikView (colado manualmente — Fase 2) ─────────────────────────────
_QLIK_CAMPOS = ("atacado_ton", "varejo_ton", "devolucao_ton", "terceiros_ton", "paletes", "capacidade_ton")


def _salvar_qlikview_linha(usuario: str, empresa: str, data: str, temperatura: str, valores: dict):
    """Uma linha por (usuario, empresa, data, temperatura). Colar de novo pro mesmo
    dia/temperatura ATUALIZA só os campos que vieram nesse paste — um paste
    parcial (ex.: só a linha "Atacado", sem Paletes/Capacidade porque o
    usuário selecionou menos linhas dessa vez) preserva os campos que já
    estavam salvos em vez de zerá-los. `COALESCE(?, campo)`: quando o valor
    não veio no paste (None), mantém o que já tinha; quando veio (mesmo que
    seja 0 de verdade), aplica o novo valor."""
    with get_db() as db:
        db.execute("""
            INSERT INTO armazem_qlikview_diario
                (usuario, empresa, data, temperatura, atacado_ton, varejo_ton, devolucao_ton, terceiros_ton, paletes, capacidade_ton, colado_em)
            VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(usuario, empresa, data, temperatura) DO UPDATE SET
              atacado_ton=COALESCE(excluded.atacado_ton, armazem_qlikview_diario.atacado_ton),
              varejo_ton=COALESCE(excluded.varejo_ton, armazem_qlikview_diario.varejo_ton),
              devolucao_ton=COALESCE(excluded.devolucao_ton, armazem_qlikview_diario.devolucao_ton),
              terceiros_ton=COALESCE(excluded.terceiros_ton, armazem_qlikview_diario.terceiros_ton),
              paletes=COALESCE(excluded.paletes, armazem_qlikview_diario.paletes),
              capacidade_ton=COALESCE(excluded.capacidade_ton, armazem_qlikview_diario.capacidade_ton),
              colado_em=CURRENT_TIMESTAMP
        """, (usuario, empresa, data, temperatura, valores.get("atacado_ton"), valores.get("varejo_ton"),
              valores.get("devolucao_ton"), valores.get("terceiros_ton"),
              valores.get("paletes"), valores.get("capacidade_ton")))


# Campos que SOMAM entre empresas na visão consolidada. Capacidade também
# soma: no QlikView ela é a capacidade contratada de cada empresa, não a
# câmara física do Local (usuário, 23/09/2026).
_QLIK_SOMAVEIS = ("atacado_ton", "varejo_ton", "devolucao_ton", "terceiros_ton",
                  "paletes", "capacidade_ton")


def _qlik_filtro_empresa(usuario: str, empresa: str | None) -> tuple[str, list]:
    """(trecho SQL, parâmetros). Empresa vazia/TODAS = todas as empresas do
    Local, somadas pelo chamador."""
    if empresa and empresa != _TODAS_EMPRESAS:
        return " AND empresa=?", [empresa]
    return "", []


def _qlik_somar(linhas) -> dict:
    """Soma as linhas de várias empresas de um mesmo (data, temperatura)."""
    out: dict = {}
    for r in linhas:
        alvo = out.setdefault(r["temperatura"], {"temperatura": r["temperatura"]})
        for campo in _QLIK_SOMAVEIS:
            v = r[campo]
            if v is not None:
                alvo[campo] = (alvo.get(campo) or 0) + v
    for v in out.values():
        for campo in _QLIK_SOMAVEIS:
            v.setdefault(campo, None)
    return out


def _qlikview_do_dia(usuario: str, data: str, empresa: str | None = None) -> dict | None:
    filtro, params = _qlik_filtro_empresa(usuario, empresa)
    with get_db() as db:
        rows = db.execute(f"""
            SELECT temperatura, atacado_ton, varejo_ton, devolucao_ton, terceiros_ton, paletes, capacidade_ton
            FROM armazem_qlikview_diario WHERE usuario=? AND data=?{filtro}
        """, [usuario, data, *params]).fetchall()
    if not rows:
        return None
    return _qlik_somar(rows)


def _qlikview_do_mes(usuario: str, ano_mes: str, empresa: str | None = None) -> dict:
    """ano_mes = 'YYYY-MM'. Retorna {data: {temperatura: {...}}} pro
    gráfico de evolução mensal. Sem empresa (ou TODAS), soma as empresas do
    Local — cada uma cola o seu QlikView."""
    filtro, params = _qlik_filtro_empresa(usuario, empresa)
    with get_db() as db:
        rows = db.execute(f"""
            SELECT data, temperatura, atacado_ton, varejo_ton, devolucao_ton, terceiros_ton, paletes, capacidade_ton
            FROM armazem_qlikview_diario WHERE usuario=? AND data LIKE ?{filtro} ORDER BY data
        """, [usuario, ano_mes + "-%", *params]).fetchall()
    por_data: dict = {}
    for r in rows:
        por_data.setdefault(r["data"], []).append(r)
    return {data: _qlik_somar(linhas) for data, linhas in por_data.items()}


def _registrar_locais_pendentes(nomes: list[str], usuario: str = ""):
    """Local novo entra como pendente NA CONTA que carregou o arquivo.

    Herda a classificação do molde ("") quando ele já conhece esse local —
    assim um CD que sobe arquivo com um local já classificado por outro não
    precisa classificar de novo.
    """
    usuario = (usuario or "").strip().lower()
    with get_db() as db:
        molde = {r["nome"]: r["classificacao"] for r in db.execute(
            "SELECT nome, classificacao FROM armazem_locais WHERE usuario=''").fetchall()}
        for nome in nomes:
            db.execute(
                "INSERT OR IGNORE INTO armazem_locais(usuario,nome,classificacao) VALUES(?,?,?)",
                (usuario, nome, molde.get(nome, "pendente")))


def _recalcular_todos():
    """Chamado ao classificar um local ou editar capacidade/contrato:
    recalcula na hora os painéis já carregados em memória (sem exigir novo
    upload do mesmo arquivo — a pendência costuma se resolver na sessão
    logo após o primeiro upload). Regrava o snapshot do dia sempre que os
    dados deixam de estar pendentes — como é upsert por (usuario, empresa,
    data), regravar não duplica nada (ver _salvar_snapshot_diario).
    `_estado` é chaveado por (usuario, empresa) desde o redesenho
    Local×Empresa (29/08/2026) — cada empresa recalcula com sua PRÓPRIA
    capacidade contratada, mas a capacidade FÍSICA e o valor do contrato
    (R$/ton) continuam por usuário só (compartilhados)."""
    # a classificação é por conta desde 08/10/2026, então ela é lida DENTRO
    # do laço: ler uma vez fora aplicaria a régua de uma conta em todas
    for (usuario, empresa), estado in _estado.items():
        locais = _get_locais(usuario)
        tem_pendencia_global = any(cls == "pendente" for cls in locais.values())
        desconsiderar = {nome for nome, cls in locais.items() if cls == "desconsiderar"}
        capacidades = _capacidade_fisica_efetiva(usuario, _empresas_do_usuario(usuario))
        valor_contrato = _get_valor_contrato(usuario)
        ambiente_modo = _get_config(usuario)["ambiente_modo"]
        capacidade_contratada = _get_capacidade_contratada(usuario, empresa)
        estado["tem_pendencia"] = tem_pendencia_global
        if tem_pendencia_global:
            estado["metricas"] = metricas_zeradas(capacidades, valor_contrato, ambiente_modo, capacidade_contratada)
            estado["paletes_por_local"] = {}
        else:
            linhas = estado.get("linhas") or []
            estado["metricas"] = calcular_metricas(linhas, capacidades, desconsiderar, valor_contrato,
                                                    ambiente_modo, capacidade_contratada)
            estado["paletes_por_local"] = paletes_por_local(linhas)
            # Só REGRAVA o snapshot quando as linhas do arquivo estão em
            # memória. Estado hidratado do banco (depois de restart do
            # gunicorn, que na e2-micro acontece) não tem `linhas`, e
            # recalcular com lista vazia zerava as métricas e gravava esse
            # zero por cima do snapshot real do dia — apagando as toneladas
            # já apuradas (achado na revisão pré-deploy, 08/10/2026).
            if linhas:
                _salvar_snapshot_diario(usuario, empresa, estado.get("data_snapshot"),
                                        estado.get("nome_arquivo", ""), estado["metricas"])
                salvar_fifo_diario(usuario, empresa, estado.get("data_snapshot"), linhas, locais)
                _salvar_linhas_dia(usuario, empresa, estado.get("data_snapshot"), linhas)


# Quantos dias de linhas detalhadas ficam guardados. O painel do dia é o
# uso real do detalhamento; dias anteriores saem pelo cleanup.
RETENCAO_LINHAS_DIAS = 7


def _salvar_linhas_dia(usuario: str, empresa: str, data: str, linhas: list[dict]) -> None:
    """Substitui as linhas daquele dia/empresa e limpa as antigas."""
    if not data:
        return
    limite = (_dt.date.fromisoformat(data) - _dt.timedelta(days=RETENCAO_LINHAS_DIAS)).isoformat()
    with get_db() as db:
        db.execute("DELETE FROM armazem_linhas_dia WHERE usuario=? AND empresa=? AND data=?",
                   (usuario, empresa, data))
        db.executemany("""
            INSERT INTO armazem_linhas_dia
                (usuario, empresa, data, temperatura, tipo_produto, codigo, nome, local, palete,
                 peso_liquido, producao, validade, enderecado, reserva_sigla, reserva_nome, bloqueio)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            (usuario, empresa, data, r["temperatura"], r["tipo_produto"], r["codigo"], r["nome"],
             r["local"], str(r.get("palete") or ""), r["peso_liquido"],
             r["producao"].isoformat() if r["producao"] else None,
             r["validade"].isoformat() if r["validade"] else None,
             int(bool(r["endereçado"])), r["reserva_sigla"], r["reserva_nome"],
             int(bool(r["bloqueio_motivos"])))
            for r in linhas
        ])
        db.execute("DELETE FROM armazem_linhas_dia WHERE usuario=? AND data<?", (usuario, limite))


def _carregar_linhas_dia(usuario: str, empresa: str, data: str) -> list[dict]:
    """Volta ao formato do parser (datas como date, bloqueio como lista),
    para reaproveitar as mesmas regras de detalhar_linhas."""
    with get_db() as db:
        rows = db.execute("""
            SELECT * FROM armazem_linhas_dia WHERE usuario=? AND empresa=? AND data=?
        """, (usuario, empresa, data)).fetchall()
    def _d(v):
        return _dt.date.fromisoformat(v) if v else None
    return [{
        "temperatura": r["temperatura"], "tipo_produto": r["tipo_produto"], "codigo": r["codigo"],
        "nome": r["nome"], "local": r["local"], "palete": r["palete"],
        "peso_liquido": r["peso_liquido"], "peso_bruto": 0.0,
        "producao": _d(r["producao"]), "validade": _d(r["validade"]),
        "endereçado": bool(r["enderecado"]), "reserva_codigo": "",
        "reserva_nome": r["reserva_nome"], "reserva_sigla": r["reserva_sigla"],
        "bloqueio_motivos": ["bloqueio"] if r["bloqueio"] else [],
    } for r in rows]


def _salvar_snapshot_diario(usuario: str, empresa: str, data: str, nome_arquivo: str, metricas: dict):
    """Um snapshot por (usuario, empresa, data) — subir um novo TXT no
    mesmo dia PRA MESMA EMPRESA substitui o snapshot dela automaticamente
    (pedido original do usuário 08/08/2026, mantido); empresas diferentes
    não se sobrescrevem mais (redesenho Local×Empresa, 29/08/2026 — antes
    a chave era só (usuario,data), upload da 2ª empresa apagava a 1ª)."""
    import json
    with get_db() as db:
        db.execute("""
            INSERT INTO armazem_diario(usuario, empresa, data, nome_arquivo, total_ton, total_paletes, metricas_json, processado_em)
            VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(usuario, empresa, data) DO UPDATE SET
              nome_arquivo=excluded.nome_arquivo, total_ton=excluded.total_ton,
              total_paletes=excluded.total_paletes, metricas_json=excluded.metricas_json,
              processado_em=CURRENT_TIMESTAMP
        """, (usuario, empresa, data, nome_arquivo, metricas["total_ton"], metricas["total_paletes"],
              json.dumps(metricas, ensure_ascii=False, default=str)))


def _dias_com_dado(usuario: str, empresa: str) -> list[str]:
    with get_db() as db:
        rows = db.execute("SELECT data FROM armazem_diario WHERE usuario=? AND empresa=? ORDER BY data DESC",
                          (usuario, empresa)).fetchall()
    return [r["data"] for r in rows]


def _snapshot_do_dia(usuario: str, empresa: str, data: str) -> dict | None:
    import json
    with get_db() as db:
        row = db.execute("SELECT * FROM armazem_diario WHERE usuario=? AND empresa=? AND data=?",
                         (usuario, empresa, data)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["metricas"] = json.loads(d.pop("metricas_json") or "{}")
    return d


# ── página ───────────────────────────────────────────────────────────
@armazem_bp.route("/armazem")
def pagina():
    from flask import redirect, url_for
    if not session.get("user"):
        return redirect(url_for("login_page"))
    if not tem_acesso_modulo("armazem"):
        return redirect(url_for("index"))
    return render_template("armazem.html", user=session.get("user"))


# ── locais de estoque (cadastro + pendências) ──────────────────────────
@armazem_bp.route("/api/armazem/locais")
@login_required
@modulo_required("armazem")
def locais_listar():
    locais = _get_locais(_usuario_efetivo())
    itens = [{"nome": nome, "classificacao": cls} for nome, cls in locais.items()]
    itens.sort(key=lambda x: (x["classificacao"] != "pendente", x["nome"]))
    pendencias = sum(1 for i in itens if i["classificacao"] == "pendente")
    return jsonify({"locais": itens, "pendencias": pendencias})


@armazem_bp.route("/api/armazem/locais/classificar", methods=["POST"])
@login_required
@modulo_required("armazem")
@bloquear_escrita
@csrf_required
def locais_classificar():
    d = request.json or {}
    nome = str(d.get("nome", "")).strip()
    classificacao = str(d.get("classificacao", "")).strip().lower()
    if not nome or classificacao not in CLASSIFICACOES_VALIDAS:
        return jsonify({"error": "Local ou classificação inválida."}), 400
    with get_db() as db:
        db.execute("""
            INSERT INTO armazem_locais(usuario, nome, classificacao, classificado_em)
            VALUES(?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(usuario, nome) DO UPDATE SET
              classificacao=excluded.classificacao, classificado_em=CURRENT_TIMESTAMP
        """, (_usuario_efetivo(), nome, classificacao))
    registrar_log(session.get("user", ""), "armazem_classificar_local",
                 detalhe=f"{nome} -> {classificacao}")
    _recalcular_todos()
    return jsonify({"ok": True})


# ── capacidade por câmara (histórico de vigências — cada CD/usuário tem a
# sua, e o valor pode mudar no meio do mês) ────────────────────────────
@armazem_bp.route("/api/armazem/capacidade", methods=["GET", "POST"])
@login_required
@modulo_required("armazem")
@csrf_required
def capacidade():
    usuario = _usuario_efetivo()
    if request.method == "GET":
        data_ref = request.args.get("data")  # opcional: consultar vigência de um dia específico
        # ?empresa=CDA|CDB|TODAS: a capacidade da VISÃO, a mesma do topo da
        # página (contratada pela empresa; consolidada = soma das empresas).
        # A Evolução Mensal usava a física da câmara em qualquer visão e não
        # mudava ao trocar o filtro (usuário, 22/10/2026).
        visao = {}
        if request.args.get("empresa"):
            empresas_validas = _empresas_do_usuario(usuario)
            emp = _empresa_pedida(empresas_validas)
            emps_visao = (empresas_validas if emp == _TODAS_EMPRESAS or emp not in empresas_validas
                          else [emp])
            caps_visao, origem = _capacidade_da_visao(usuario, emps_visao, empresas_validas, data_ref)
            visao = {"capacidades_visao": caps_visao,
                     "capacidade_visao": {"empresas": emps_visao, "origem": origem}}
        return jsonify({
            **visao,
            "capacidades": _get_capacidades(usuario, data_ref),
            # capacidades_estimadas: mesmo fallback usado no cálculo de ocupação
            # (soma da capacidade CONTRATADA quando a física ainda não foi
            # digitada) — o front usa isso só como placeholder/dica no campo
            # vazio, nunca como se já fosse um valor salvo de verdade.
            "capacidades_estimadas": _capacidade_fisica_efetiva(usuario, _empresas_do_usuario(usuario), data_ref),
            "historico": _historico_capacidade(usuario),
        })
    if somente_leitura():
        return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
    d = request.json or {}
    vigente_desde = str(d.get("vigente_desde") or _hoje_brt())
    for temp in TEMPERATURAS:
        try:
            cap = float(str(d.get(temp, 0)).replace(",", "."))
        except (TypeError, ValueError):
            continue
        _definir_capacidade(usuario, temp, cap, vigente_desde)
    registrar_log(session.get("user", ""), "armazem_editar_capacidade", detalhe=f"vigente desde {vigente_desde}")
    _recalcular_todos()
    return jsonify({"ok": True, "capacidades": _get_capacidades(usuario)})


# ── contrato (R$/ton — UM valor só pro CD, não por temperatura; correção
# do usuário 08/08/2026: "fiz a conta em relação ao contrato total, não foi
# real por tonelada por temperatura") ───────────────────────────────────
def _previsto_por_empresa(usuario: str, empresas: list[str], valor_contrato_ton: float, data_ref: str | None = None) -> dict:
    """R$ Previsto de cada empresa = soma da capacidade CONTRATADA dela
    (todas as temperaturas) × valor_contrato_ton (compartilhado entre as
    empresas do mesmo Local — confirmado com o usuário 29/08/2026, é a taxa
    que é uma só, não a capacidade). Combinado = soma de todos os
    previstos individuais (equivalente a somar as capacidades primeiro,
    já que a taxa é igual pra todas)."""
    por_empresa = {}
    for emp in empresas:
        cap = _get_capacidade_contratada(usuario, emp, data_ref)
        cap_total = sum(cap.values())
        por_empresa[emp] = {
            "capacidade_contratada": cap,
            "capacidade_contratada_total": cap_total,
            "previsto_mensal": round(cap_total * valor_contrato_ton, 2),
        }
    previsto_combinado = round(sum(v["previsto_mensal"] for v in por_empresa.values()), 2)
    return {"por_empresa": por_empresa, "previsto_combinado": previsto_combinado}


@armazem_bp.route("/api/armazem/contrato", methods=["GET", "POST"])
@login_required
@modulo_required("armazem")
@csrf_required
def contrato():
    usuario = _usuario_efetivo()
    empresas = _empresas_do_usuario(usuario)
    if request.method == "GET":
        data_ref = request.args.get("data")
        valor = _get_valor_contrato(usuario, data_ref)
        previsto = _previsto_por_empresa(usuario, empresas, valor, data_ref)
        return jsonify({
            "valor_contrato_ton": valor,
            "historico": _historico_contrato(usuario),
            "empresas": empresas,
            **previsto,
        })
    if somente_leitura():
        return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
    d = request.json or {}
    vigente_desde = str(d.get("vigente_desde") or _hoje_brt())
    try:
        valor = float(str(d.get("valor_contrato_ton", 0)).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"error": "Valor inválido."}), 400
    _definir_valor_contrato(usuario, valor, vigente_desde)

    capacidades_contratadas = d.get("capacidades_contratadas") or {}
    for emp in empresas:
        valores_emp = capacidades_contratadas.get(emp) or {}
        for temp in TEMPERATURAS:
            if temp not in valores_emp:
                continue
            try:
                cap = float(str(valores_emp[temp]).replace(",", "."))
            except (TypeError, ValueError):
                continue
            _definir_capacidade_contratada(usuario, emp, temp, cap, vigente_desde)

    registrar_log(session.get("user", ""), "armazem_editar_contrato", detalhe=f"R$ {valor}/ton desde {vigente_desde}")
    _recalcular_todos()
    previsto = _previsto_por_empresa(usuario, empresas, _get_valor_contrato(usuario))
    return jsonify({"ok": True, "valor_contrato_ton": _get_valor_contrato(usuario), "empresas": empresas, **previsto})


# ── navegar entre contratos (gerente troca de canônica pelo nome do
# contrato, não pelo username cru) — pedido do usuário 29/08/2026: a
# etiqueta que já mostra o rótulo do contrato no topo vira clicável só
# pra admin/master, abre a lista de todos os contratos existentes e troca
# via o MESMO mecanismo de Espelhamento (somente leitura, já testado —
# decisão explícita do usuário: não editar por essa via, só navegar/ver).
@armazem_bp.route("/api/armazem/contratos")
@login_required
def contratos():
    if session.get("perfil") not in ("admin", "master"):
        return jsonify({"error": "Só Gerente pode navegar entre contratos."}), 403
    eu = str(session.get("user", "")).strip().lower()
    with get_db() as db:
        # direto em armazem_diario, não o get_usuarios_com_dados() genérico
        # (esse também conta quem só tem dado em sessao_dados/Monitoramento —
        # achado ao vivo 29/08/2026: "qualidade-cda" aparecia aqui mesmo sem
        # acesso nenhum ao módulo Armazém, só por ter usado o Monitoramento).
        todos = db.execute("""
            SELECT usuario, MAX(processado_em) AS atualizado
            FROM armazem_diario GROUP BY usuario ORDER BY atualizado DESC
        """).fetchall()
        rotulos = {r["usuario"]: r["rotulo"] for r in db.execute(
            "SELECT usuario, rotulo FROM armazem_config").fetchall()}
        # CD cadastrado que ainda não subiu arquivo nenhum também precisa
        # aparecer: desde 01/10/2026 o Gerente alimenta o CD espelhando, e
        # o primeiro arquivo de um CD novo (o caso do CD CDD) só entra se
        # ele conseguir selecionar o contrato ANTES de existir dado. Vem de
        # cd_conta_canonica, que é o cadastro de CD de verdade.
        cadastrados = {r["usuario"] for r in db.execute(
            "SELECT DISTINCT usuario FROM cd_conta_canonica").fetchall()}
    com_dado = {u["usuario"] for u in todos}
    lista = [{"usuario": u["usuario"], "rotulo": rotulos.get(u["usuario"], ""),
              "atualizado": u["atualizado"]}
             for u in todos if u["usuario"] != eu]
    lista += [{"usuario": u, "rotulo": rotulos.get(u, ""), "atualizado": None}
              for u in sorted(cadastrados - com_dado) if u != eu]
    return jsonify({"contratos": lista})


# ── config geral: rótulo/identificação + modo de capacidade do Ambiente
# (pedido do usuário 08/08/2026: "há CDs que existe contrato com capacidade
# para o ambiente" — no CDA hoje o Ambiente consome a câmara do Resfriado,
# mas isso precisa ser configurável por CD/usuário, não fixo) ───────────
@armazem_bp.route("/api/armazem/config", methods=["GET", "POST"])
@login_required
@modulo_required("armazem")
@csrf_required
def config():
    usuario = _usuario_efetivo()
    if request.method == "GET":
        return jsonify(_get_config(usuario))
    if somente_leitura():
        return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
    d = request.json or {}
    rotulo = str(d.get("rotulo", "")).strip()[:120]
    ambiente_modo = str(d.get("ambiente_modo", "")).strip()
    if ambiente_modo not in ("consome_resfriado", "capacidade_propria"):
        ambiente_modo = _get_config(usuario)["ambiente_modo"]
    modelo_pagamento = str(d.get("modelo_pagamento", "")).strip()
    if modelo_pagamento not in ("quinzenal", "mensal"):
        modelo_pagamento = _get_config(usuario)["modelo_pagamento"]
    with get_db() as db:
        db.execute("""
            INSERT INTO armazem_config(usuario, rotulo, ambiente_modo, modelo_pagamento) VALUES(?,?,?,?)
            ON CONFLICT(usuario) DO UPDATE SET rotulo=excluded.rotulo, ambiente_modo=excluded.ambiente_modo,
              modelo_pagamento=excluded.modelo_pagamento
        """, (usuario, rotulo, ambiente_modo, modelo_pagamento))
    registrar_log(session.get("user", ""), "armazem_editar_config",
                 detalhe=f"rotulo={rotulo} ambiente_modo={ambiente_modo} modelo_pagamento={modelo_pagamento}")
    _recalcular_todos()
    return jsonify({"ok": True, **_get_config(usuario)})


# ── upload / processamento ─────────────────────────────────────────────
@armazem_bp.route("/api/armazem/upload", methods=["POST"])
@login_required
@modulo_required("armazem")
@bloquear_escrita
@csrf_required
def upload():
    usuario = _usuario_efetivo()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = str(request.form.get("empresa", "")).strip() or empresas_validas[0]
    if empresa not in empresas_validas:
        return jsonify({"error": f"Empresa {empresa!r} não reconhecida pra este CD."}), 400

    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    # TXT ou Excel (12/11/2026): o tipo é decidido pelo conteúdo do arquivo
    # (_tipo_arquivo), não pela extensão
    ext = os.path.splitext(arquivo.filename)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls"):
        ext = ".txt"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        arquivo.save(tmp.name)
        tmp_path = tmp.name
    try:
        linhas = parse_produto_paletizado(tmp_path)
    except ColunasFaltando as exc:
        # o Armazém não tem a tela de mapeamento: diz o que falta e onde ajustar
        nomes = ", ".join(f"“{c['rotulo']}”" for c in exc.campos)
        return jsonify({"error": f"O arquivo não tem a coluna {nomes}. Confirme que é o relatório "
                                 "Produto Paletizado exportado com todas as colunas. Se o nome da coluna "
                                 "mudou, o mapeamento das colunas do TXT é feito na tela do Expedito "
                                 "(🧩 Mapeamento de colunas)."}), 400
    except PlanilhaIlegivel as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Erro ao ler o arquivo: {exc}"}), 400
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    if not linhas:
        return jsonify({"error": "Arquivo vazio ou em formato inesperado (esperado: Produto Paletizado, em TXT ou Excel)."}), 400

    locais_conhecidos = set(_get_locais(usuario).keys())
    novos = detectar_locais_novos(linhas, locais_conhecidos)
    if novos:
        _registrar_locais_pendentes(novos, usuario)

    locais = _get_locais(usuario)
    tem_pendencia = any(cls == "pendente" for cls in locais.values())
    desconsiderar = {nome for nome, cls in locais.items() if cls == "desconsiderar"}
    capacidades = _capacidade_fisica_efetiva(usuario, empresas_validas)
    valor_contrato = _get_valor_contrato(usuario)
    ambiente_modo = _get_config(usuario)["ambiente_modo"]
    capacidade_contratada = _get_capacidade_contratada(usuario, empresa)

    data_snapshot = _hoje_brt()

    if tem_pendencia:
        metricas = metricas_zeradas(capacidades, valor_contrato, ambiente_modo, capacidade_contratada)
    else:
        metricas = calcular_metricas(linhas, capacidades, desconsiderar, valor_contrato,
                                     ambiente_modo, capacidade_contratada)
        _salvar_snapshot_diario(usuario, empresa, data_snapshot, arquivo.filename, metricas)
        salvar_fifo_diario(usuario, empresa, data_snapshot, linhas, locais)
        _salvar_linhas_dia(usuario, empresa, data_snapshot, linhas)

    _estado[(usuario, empresa)] = {
        "linhas": linhas,             # cache pra recalcular sozinho ao classificar (ver _recalcular_todos)
        "nome_arquivo": arquivo.filename,
        "data_snapshot": data_snapshot,  # dia do snapshot — reupload no mesmo dia pra mesma empresa substitui (ver _salvar_snapshot_diario)
        "metricas": metricas,
        "paletes_por_local": paletes_por_local(linhas) if not tem_pendencia else {},
        "tem_pendencia": tem_pendencia,
        "locais_novos": novos,
    }
    registrar_log(session.get("user", ""), "armazem_upload",
                 detalhe=f"[{empresa}] {arquivo.filename} ({len(linhas)} linhas, {len(novos)} locais novos)")
    return jsonify({"ok": True, "empresa": empresa, "linhas": len(linhas), "locais_novos": novos,
                    "tem_pendencia": tem_pendencia})


# ── dados pro painel ────────────────────────────────────────────────────
@armazem_bp.route("/api/armazem/dados")
@login_required
@modulo_required("armazem")
def dados():
    usuario = _usuario_efetivo()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = _empresa_pedida(empresas_validas)

    if empresa == _TODAS_EMPRESAS and len(empresas_validas) > 1:
        hoje = _hoje_brt()
        lista, fora = _visao_consolidada(usuario, empresas_validas, hoje, hoje)
        if not lista:
            return jsonify({"tem_dados": False, "empresas": empresas_validas,
                            "empresa": _TODAS_EMPRESAS, "empresas_fora": fora})
        amb = _get_config(usuario)["ambiente_modo"] == "capacidade_propria"
        caps, origem = _capacidade_da_visao(usuario, empresas_validas, empresas_validas)
        contratada = _contratada_da_visao(usuario, empresas_validas, empresas_validas)
        return jsonify({
            "tem_dados": True, "empresa": _TODAS_EMPRESAS, "empresas": empresas_validas,
            "metricas": aplicar_capacidade(merge_metricas(lista), caps, amb, contratada),
            "capacidade_visao": {"empresas": empresas_validas, "origem": origem},
            "tem_pendencia": False, "locais_novos": [],
            "empresas_fora": fora,
            "empresas_na_visao": [e for e in empresas_validas
                                  if e not in {f["empresa"] for f in fora}],
            "config": _get_config(usuario),
        })

    if empresa not in empresas_validas:
        empresa = empresas_validas[0]
    estado = _estado.get((usuario, empresa))
    if not estado:
        # _estado só existe em memória (populado ao processar um TXT nesta
        # sessão do processo) — ao recarregar a página, logar de novo ou
        # depois de um restart do servidor, ficava vazio mesmo já existindo
        # snapshot de hoje salvo no banco, obrigando a ver o dia atual pelo
        # Calendário (pedido do usuário 24/08/2026: mostrar hoje direto).
        # Carrega o snapshot de hoje do banco, se tiver, como se fosse o
        # estado ao vivo (nunca tem pendência: só é salvo sem pendência —
        # ver _salvar_snapshot_diario).
        snap = _snapshot_do_dia(usuario, empresa, _hoje_brt())
        if not snap:
            return jsonify({"tem_dados": False, "empresas": empresas_validas, "empresa": empresa})
        estado = {
            "linhas": [], "nome_arquivo": snap["nome_arquivo"], "data_snapshot": snap["data"],
            "metricas": snap["metricas"], "paletes_por_local": {},
            "tem_pendencia": False, "locais_novos": [],
        }
        _estado[(usuario, empresa)] = estado
    amb = _get_config(usuario)["ambiente_modo"] == "capacidade_propria"
    caps, origem = _capacidade_da_visao(usuario, [empresa], empresas_validas)
    contratada = _contratada_da_visao(usuario, [empresa], empresas_validas)
    return jsonify({
        "tem_dados": True,
        "empresa": empresa,
        "empresas": empresas_validas,
        "metricas": aplicar_capacidade(estado["metricas"], caps, amb, contratada),
        "capacidade_visao": {"empresas": [empresa], "origem": origem},
        "tem_pendencia": estado["tem_pendencia"],
        "locais_novos": estado["locais_novos"],
        "config": _get_config(usuario),
    })


@armazem_bp.route("/api/armazem/exportar")
@login_required
@modulo_required("armazem")
def exportar():
    """Gera o dashboard como PNG (mesmo padrão de /api/exportar_imagem do
    painel principal — Playwright renderiza um HTML estático e devolve a
    URL do arquivo em saida/, que o front baixa em seguida). Aceita
    ?data=YYYY-MM-DD opcional pra exportar o snapshot de um dia do
    calendário em vez do painel ao vivo — antes só dava pra exportar o
    painel atual, o botão ficava escondido vendo um dia retroativo
    (pedido do usuário 22/08/2026: manter a exportação também no
    snapshot retroativo)."""
    usuario = _usuario_efetivo()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = _empresa_pedida(empresas_validas)
    data_param = request.args.get("data", "").strip()

    fora_da_visao: list[dict] = []

    amb_propria = _get_config(usuario)["ambiente_modo"] == "capacidade_propria"

    def _com_capacidade(m, empresas_visao):
        """A imagem vai por e-mail: a capacidade tem que ser a da visão
        exportada, igual à da tela (01/10/2026)."""
        if not m:
            return None
        caps, _ = _capacidade_da_visao(usuario, empresas_visao, empresas_validas, data_param or None)
        contratada = _contratada_da_visao(usuario, empresas_visao, empresas_validas, data_param or None)
        return aplicar_capacidade(m, caps, amb_propria, contratada)

    def _metricas_da_visao(emp: str):
        """Métricas de uma visão (consolidada ou de uma empresa), ou None."""
        if emp == _TODAS_EMPRESAS:
            hoje = _hoje_brt()
            lista, fora = _visao_consolidada(usuario, empresas_validas, data_param or hoje, hoje)
            fora_da_visao[:] = fora
            return _com_capacidade(merge_metricas(lista), empresas_validas) if lista else None
        if data_param:
            snap = _snapshot_do_dia(usuario, emp, data_param)
            return _com_capacidade(snap["metricas"], [emp]) if snap and snap["metricas"] else None
        estado = _estado.get((usuario, emp))
        if not estado or estado["tem_pendencia"] or not estado["metricas"].get("total_ton"):
            return None
        return _com_capacidade(estado["metricas"], [emp])

    # Com 2+ empresas o usuário manda um e-mail por visão (consolidada, CDA,
    # CDB). São 5 imagens por visão (Painel + 4 do QlikView): gerar as três
    # de uma vez prenderia o processo web por minutos na VM, então cada
    # chamada gera UMA visão e a tela pede as outras sob demanda
    # (23/09/2026).
    multi = len(empresas_validas) > 1
    if empresa != _TODAS_EMPRESAS and empresa not in empresas_validas:
        empresa = empresas_validas[0]
    if empresa == _TODAS_EMPRESAS and not multi:
        empresa = empresas_validas[0]
    visoes_pedidas = [empresa]

    metricas_por_visao = {v: _metricas_da_visao(v) for v in visoes_pedidas}
    if not any(metricas_por_visao.values()):
        return jsonify({"error": "Sem dados pra exportar (processe um arquivo e resolva pendências primeiro)."}), 400
    metricas = metricas_por_visao.get(empresa if empresa in metricas_por_visao else visoes_pedidas[0])

    from datetime import datetime
    from zoneinfo import ZoneInfo
    # Evolução Mensal do QlikView entra na imagem também (pedido do usuário
    # 22/08/2026: "o qlikview não está exportando como imagem") — usa o mês
    # que o seletor da tela estava mostrando (?mes=YYYY-MM), caindo pro mês
    # atual se não vier.
    mes_param = request.args.get("mes", "").strip()
    if not mes_param:
        mes_param = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m")
    config = _get_config(usuario)
    modelo_pagamento = config.get("modelo_pagamento", "mensal")
    ref_evol = _ref_data_para_mes(mes_param)

    def _capacidades_evol(emp: str) -> dict:
        # a mesma capacidade da visão usada no topo do painel (contratada da
        # empresa; consolidada = soma) — era a física da câmara em todas
        emps = empresas_validas if emp == _TODAS_EMPRESAS else [emp]
        return _capacidade_da_visao(usuario, emps, empresas_validas, ref_evol)[0]
    ambiente_propria = config.get("ambiente_modo") == "capacidade_propria"

    ts = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d_%H%M%S")
    rotulo_base = config.get("rotulo", "")

    def _rotulo(emp: str) -> str:
        # Com 2+ empresas, a etiqueta livre sozinha não diz qual empresa (ou
        # combinação) está na imagem (achado ao vivo 29/08/2026) — sufixo com
        # a(s) sigla(s) reais, nunca fixo no código.
        if not multi:
            return rotulo_base
        sigla = " / ".join(empresas_validas) if emp == _TODAS_EMPRESAS else emp
        return f"{rotulo_base} — {sigla}" if rotulo_base else sigla

    visoes = []
    for emp in visoes_pedidas:
        m_visao = metricas_por_visao.get(emp)
        if not m_visao:
            continue          # empresa sem arquivo processado no dia
        visoes.append({
            "chave": "consolidada" if emp == _TODAS_EMPRESAS else emp.lower(),
            "titulo": (f"Consolidada ({' + '.join(empresas_validas)})"
                       if emp == _TODAS_EMPRESAS else f"Somente {emp}"),
            "empresa": emp,
            "metricas": m_visao,
            "rotulo": _rotulo(emp),
            "qlikview_dias": _qlikview_do_mes(usuario, mes_param, emp),
            "qlikview_ano_mes": mes_param,
            "modelo_pagamento": modelo_pagamento,
            "capacidades": _capacidades_evol(emp),
            "ambiente_propria": ambiente_propria,
        })
    try:
        grupos = exportar_imagens_visoes(visoes, prefixo=f"Painel_Armazem_{ts}")
    except Exception:
        return jsonify({"error": "Erro ao gerar a imagem. Tente novamente."}), 500

    # o link leva a posse do arquivo (ver app/utils/saida_link.py)
    registrar_log(session.get("user", ""), "armazem_exportar_imagem",
                  detalhe=f"{len(grupos)} visão(ões)")
    saida = [{"chave": g["chave"], "titulo": g["titulo"],
              "imagens": [{"label": i["label"], "url": url_saida(i["path"].name)} for i in g["imagens"]]}
             for g in grupos]
    # `visoes` diz à tela quais colunas existem, para ela oferecer as outras
    # sem precisar conhecer as empresas do Local.
    disponiveis = ([{"empresa": _TODAS_EMPRESAS,
                     "titulo": f"Consolidada ({' + '.join(empresas_validas)})"}]
                   + [{"empresa": e, "titulo": f"Somente {e}"} for e in empresas_validas]) if multi else []
    return jsonify({"grupos": saida, "visoes": disponiveis, "visao_atual": empresa,
                    # a imagem vai por e-mail: se a consolidada saiu sem uma
                    # das empresas, a tela precisa dizer isso antes do envio
                    "empresas_fora": fora_da_visao if empresa == _TODAS_EMPRESAS else [],
                    # compatibilidade: a visão gerada também sai como antes
                    "imagens": saida[0]["imagens"] if saida else []})


# ── calendário (padrão do Adensamento: ponto azul nos dias com dado) ──
@armazem_bp.route("/api/armazem/calendario")
@login_required
@modulo_required("armazem")
def calendario():
    usuario = _usuario_efetivo()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = _empresa_pedida(empresas_validas)
    if empresa == _TODAS_EMPRESAS and len(empresas_validas) > 1:
        # união dos dias de todas as empresas — um ponto no calendário se
        # QUALQUER uma tiver dado naquele dia, mesmo que as outras não
        dias = sorted({d for emp in empresas_validas for d in _dias_com_dado(usuario, emp)}, reverse=True)
        return jsonify({"dias": dias})
    if empresa not in empresas_validas:
        empresa = empresas_validas[0]
    return jsonify({"dias": _dias_com_dado(usuario, empresa)})


@armazem_bp.route("/api/armazem/dia/<data>")
@login_required
@modulo_required("armazem")
def dia(data):
    """Snapshot de um dia específico (calendário) — somente leitura, não
    mexe no estado 'ao vivo' (_estado). Se o dia não tiver TXT próprio mas
    tiver dado colado do QlikView, ainda assim devolve o que tiver."""
    usuario = _usuario_efetivo()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = _empresa_pedida(empresas_validas)
    qlik = _qlikview_do_dia(usuario, data, empresa)

    if empresa == _TODAS_EMPRESAS and len(empresas_validas) > 1:
        lista, fora = _visao_consolidada(usuario, empresas_validas, data)
        if not lista and not qlik:
            return jsonify({"tem_dados": False, "empresas_fora": fora})
        amb = _get_config(usuario)["ambiente_modo"] == "capacidade_propria"
        caps, origem = _capacidade_da_visao(usuario, empresas_validas, empresas_validas, data)
        contratada = _contratada_da_visao(usuario, empresas_validas, empresas_validas, data)
        return jsonify({"tem_dados": True, "qlikview": qlik, "empresas_fora": fora,
                        "empresas_na_visao": [e for e in empresas_validas
                                              if e not in {f["empresa"] for f in fora}],
                        "capacidade_visao": {"empresas": empresas_validas, "origem": origem},
                        "metricas": (aplicar_capacidade(merge_metricas(lista), caps, amb, contratada)
                                     if lista else None)})

    if empresa not in empresas_validas:
        empresa = empresas_validas[0]
    snap = _snapshot_do_dia(usuario, empresa, data)
    if not snap and not qlik:
        return jsonify({"tem_dados": False})
    caps_dia, origem_dia = _capacidade_da_visao(usuario, [empresa], empresas_validas, data)
    resp = {"tem_dados": True, "qlikview": qlik,
            "capacidade_visao": {"empresas": [empresa], "origem": origem_dia}}
    if snap:
        amb = _get_config(usuario)["ambiente_modo"] == "capacidade_propria"
        caps = caps_dia
        contratada = _contratada_da_visao(usuario, [empresa], empresas_validas, data)
        resp.update({"metricas": aplicar_capacidade(snap["metricas"], caps, amb, contratada),
                     "nome_arquivo": snap["nome_arquivo"],
                     "processado_em": snap["processado_em"]})
    else:
        resp["metricas"] = None
    return jsonify(resp)


# ── QlikView (colado manualmente — Fase 2) ─────────────────────────────
@armazem_bp.route("/api/armazem/detalhe")
@login_required
@modulo_required("armazem")
def detalhe():
    """Produtos por trás de um número do painel (clique em FIFO, Vencido,
    faixa de vencimento ou endereçamento). Usa as mesmas regras do cálculo
    (armazem_service.detalhar_linhas), sobre as linhas guardadas do dia."""
    usuario = _usuario_efetivo()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = request.args.get("empresa", "").strip() or _TODAS_EMPRESAS
    categoria = request.args.get("categoria", "").strip().lower()
    temperatura = request.args.get("temperatura", "").strip().upper() or None
    faixa = request.args.get("faixa", "").strip() or None
    data = request.args.get("data", "").strip() or _hoje_brt()
    if categoria not in CATEGORIAS_DETALHE:
        return jsonify({"error": "Categoria inválida."}), 400
    if temperatura and temperatura not in TEMPERATURAS:
        return jsonify({"error": "Temperatura inválida."}), 400

    empresas = empresas_validas if empresa == _TODAS_EMPRESAS else \
        ([empresa] if empresa in empresas_validas else empresas_validas[:1])
    locais_desconsiderar = {nome for nome, cls in _get_locais(usuario).items()
                            if cls == "desconsiderar"}

    itens, sem_dado = [], []
    for emp in empresas:
        linhas = _carregar_linhas_dia(usuario, emp, data)
        if not linhas:
            sem_dado.append(emp)
            continue
        for item in detalhar_linhas(linhas, categoria, locais_desconsiderar, temperatura, faixa):
            item["empresa"] = emp
            itens.append(item)
    itens.sort(key=lambda x: (x["dias_vencer"] is None, x["dias_vencer"], -x["peso_kg"]))

    LIMITE = 1500          # tabela de conferência; acima disso vira relatório
    total_kg = sum(i["peso_kg"] for i in itens)
    return jsonify({
        "categoria": categoria, "temperatura": temperatura, "faixa": faixa, "data": data,
        "empresas": empresas, "multi_empresa": len(empresas) > 1,
        "total_kg": round(total_kg, 3), "total_itens": len(itens),
        "truncado": len(itens) > LIMITE,
        "itens": itens[:LIMITE],
        "sem_dado": sem_dado,
    })


@armazem_bp.route("/api/armazem/qlikview/colar", methods=["POST"])
@login_required
@modulo_required("armazem")
@bloquear_escrita
@csrf_required
def qlikview_colar():
    usuario = _usuario_efetivo()
    d = request.json or {}
    texto = str(d.get("texto", ""))
    ano_mes = str(d.get("ano_mes", ""))
    temperatura = str(d.get("temperatura", "")).strip().upper()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = str(d.get("empresa", "")).strip() or empresas_validas[0]
    if empresa not in empresas_validas:
        return jsonify({"error": "Escolha a empresa deste QlikView: cada empresa cola o seu."}), 400
    if not texto.strip():
        return jsonify({"error": "Cole os dados copiados do QlikView na caixa de texto."}), 400
    if temperatura not in TEMPERATURAS:
        return jsonify({"error": "Selecione a temperatura (a planilha original tem uma aba por temperatura, "
                                 "o texto colado sozinho não diz qual é)."}), 400

    r = parse_colagem_qlikview(texto, ano_mes, temperatura)
    if not r["ok"]:
        return jsonify({"error": " ".join(r["erros"]) or "Não consegui reconhecer os dados colados.",
                        "linhas_reconhecidas": r["linhas_reconhecidas"],
                        "linhas_ignoradas": r["linhas_ignoradas"]}), 400

    for linha in r["linhas"]:
        _salvar_qlikview_linha(usuario, empresa, linha["data"], linha["temperatura"], linha)

    registrar_log(session.get("user", ""), "armazem_qlikview_colar",
                 detalhe=f"{temperatura} {ano_mes}: {len(r['linhas'])} dia(s), {len(r['erros'])} erro(s)")
    return jsonify({"ok": True, "linhas_salvas": len(r["linhas"]),
                    "linhas_reconhecidas": r["linhas_reconhecidas"],
                    "linhas_ignoradas": r["linhas_ignoradas"], "erros": r["erros"]})


@armazem_bp.route("/api/armazem/qlikview/modelo", methods=["GET"])
@login_required
@modulo_required("armazem")
def qlikview_modelo():
    """Baixa o .xlsx modelo pra preencher offline e subir depois — mesma
    estrutura da planilha real do usuário (Temperatura, Tipo Local, dias do
    mês). Se o mês já tem dado salvo, vem pré-preenchido (baixar de novo
    serve pra ATUALIZAR um mês existente sem retypar tudo)."""
    ano_mes = str(request.args.get("ano_mes", ""))
    try:
        ano, mes = (int(x) for x in ano_mes.split("-"))
        if not (1 <= mes <= 12):
            raise ValueError
    except ValueError:
        return jsonify({"error": "Informe ano_mes no formato YYYY-MM."}), 400

    usuario = _usuario_efetivo()
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = str(request.args.get("empresa", "")).strip() or empresas_validas[0]
    if empresa not in empresas_validas:
        empresa = empresas_validas[0]
    dados_existentes = _qlikview_do_mes(usuario, ano_mes, empresa)
    conteudo = gerar_modelo_qlikview(ano_mes, dados_existentes)
    return send_file(
        io.BytesIO(conteudo),
        as_attachment=True,
        download_name=f"modelo_qlikview_{empresa}_{ano_mes}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@armazem_bp.route("/api/armazem/qlikview/upload", methods=["POST"])
@login_required
@modulo_required("armazem")
@bloquear_escrita
@csrf_required
def qlikview_upload():
    usuario = _usuario_efetivo()
    ano_mes = str(request.form.get("ano_mes", ""))
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = str(request.form.get("empresa", "")).strip() or empresas_validas[0]
    if empresa not in empresas_validas:
        return jsonify({"error": "Escolha a empresa desta planilha: cada empresa tem o seu QlikView."}), 400
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return jsonify({"error": "Selecione o arquivo .xlsx preenchido."}), 400
    if not arquivo.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "O arquivo precisa ser .xlsx (o modelo baixado pelo site)."}), 400

    conteudo = arquivo.read()
    if len(conteudo) > 2 * 1024 * 1024:
        return jsonify({"error": "Arquivo maior que 2MB — algo não parece certo pra essa planilha."}), 400

    r = parse_planilha_qlikview(conteudo, ano_mes)
    if not r["ok"]:
        return jsonify({"error": " ".join(r["erros"]) or "Não consegui ler a planilha.",
                        "linhas_reconhecidas": r["linhas_reconhecidas"],
                        "linhas_ignoradas": r["linhas_ignoradas"]}), 400

    for linha in r["linhas"]:
        _salvar_qlikview_linha(usuario, empresa, linha["data"], linha["temperatura"], linha)

    registrar_log(session.get("user", ""), "armazem_qlikview_upload",
                 detalhe=f"{empresa} {ano_mes}: {len(r['linhas'])} linha(s), {len(r['erros'])} erro(s)")
    return jsonify({"ok": True, "linhas_salvas": len(r["linhas"]),
                    "linhas_reconhecidas": r["linhas_reconhecidas"],
                    "linhas_ignoradas": r["linhas_ignoradas"], "erros": r["erros"]})


@armazem_bp.route("/api/armazem/qlikview/mes/<ano_mes>")
@login_required
@modulo_required("armazem")
def qlikview_mes(ano_mes):
    """ano_mes = 'YYYY-MM'. Devolve a evolução do mês, com Paletes vindo do
    TXT do dia quando existir (mais preciso — conta pallets reais via a
    coluna Palete do Produto Paletizado) e caindo pro valor colado do
    QlikView quando não houver upload de TXT naquele dia (pedido do
    usuário 08/08/2026: "se houver quantidade de pallet ele considera a do
    relatorio, se nao tiver ele busca do qlikview")."""
    usuario = _usuario_efetivo()
    # Cada empresa cola o seu QlikView (23/09/2026); a visão consolidada
    # soma. A contagem de paletes continua vindo do TXT quando existir —
    # das empresas que entram na visão, somadas.
    empresas_validas = _empresas_do_usuario(usuario)
    empresa = str(request.args.get("empresa", "")).strip() or _TODAS_EMPRESAS
    if empresa not in empresas_validas:
        empresa = _TODAS_EMPRESAS
    empresas_visao = empresas_validas if empresa == _TODAS_EMPRESAS else [empresa]
    dados_mes = _qlikview_do_mes(usuario, ano_mes, empresa)
    for data_dia, por_temp in dados_mes.items():
        paletes_temp: dict = {}
        for emp in empresas_visao:
            snap = _snapshot_do_dia(usuario, emp, data_dia)
            if not snap:
                continue
            for temp, m_temp in (snap["metricas"].get("temperaturas") or {}).items():
                pal = (m_temp or {}).get("paletes")
                if pal is not None:
                    paletes_temp[temp] = (paletes_temp.get(temp) or 0) + pal
        if not paletes_temp:
            continue
        for temp, valores in por_temp.items():
            pal_txt = paletes_temp.get(temp)
            if pal_txt:
                valores["paletes"] = pal_txt
                valores["paletes_origem"] = "txt"
            else:
                valores["paletes_origem"] = "qlikview"
    return jsonify({"dias": dados_mes})
