"""
Expedito — copiloto da expedição (06/10/2026).

Duas guias, o mesmo analista em dois momentos do dia:

  - ABASTECIMENTO DO PICKING, antes do turno: recebe o follow up de pedidos e
    os TXTs de estoque (um por empresa) e devolve quais pallets descer, quais
    permanecem no chão e o que falta. O TXT entra no momento de gerar, porque
    "a foto do armazém pela manhã é diferente do armazém no final do dia".
  - ACOMPANHAMENTO DA NOITE: o painel do WMS carregado várias vezes, virando
    retrato do andamento, projeção de término e sugestão de alocação.

O cálculo mora nos serviços (app/services/picking_abastecimento.py,
follow_up.py, picking_enderecos.py e expedito.py). Aqui ficam as rotas, o
recorte por CD e o histórico — é a camada que conhece sessão e banco.

A guia lateral do gerente é restrita a Gerente e Desenvolvedor (perfis
admin/master), decisão do usuário 06/10/2026: ela mostra a conversa e o
desempenho nominal das pessoas.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import hmac
import json
import math
import os
import tempfile
import threading
from functools import wraps

from flask import (Blueprint, current_app, jsonify, render_template, request,
                   send_file, session)

from app.database import (get_db, get_expedito_ia_chave,
                          get_expedito_ia_config, registrar_log)
from app.services import follow_up as fup
from app.services import picking_abastecimento as pick
from app.services import picking_enderecos as ends
from app.services import expedito as noite
from app.services import expedito_ia as ia
from app.services import expedito_analise as analise_svc
from app.services import expedito_veiculos as veic
from app.services.armazem_service import parse_produto_paletizado
from app.services.mapeamento_colunas import ColunasFaltando

# campos do TXT sem os quais o plano sai vazio (20/10/2026)
_OBRIGATORIOS_TXT = ("temperatura", "local_nome", "produto_codigo", "palete", "peso_liquido", "producao")
from app.services.produtividade_parser import ler_arquivo
from werkzeug.exceptions import HTTPException

from app.utils.access_control import (PERFIS_IRRESTRITOS, cd_do_usuario,
                                      modulo_required, somente_leitura,
                                      tem_acesso_modulo, usuario_efetivo)

expedito_bp = Blueprint("expedito", __name__)

_TZ_BRT = _dt.timezone(_dt.timedelta(hours=-3))
MODULO = "expedito"

# Parâmetros do CD, em JSON para crescer sem migração — mesmo padrão de
# prod_parametros. Os rótulos aparecem na guia de parâmetros.
PARAMETROS_PADRAO = {
    **pick.PARAMETROS_PADRAO,
    **fup.PARAMETROS_PADRAO,
    # A partir desta hora a tela passa para a operação seguinte, rotulada
    # pelo dia do EMBARQUE: "após as 18h ele já muda para o dia seguinte,
    # porque o embarque acontece na madrugada" (usuário, 09/10/2026). Era 17h
    # e rotulava pelo dia de início da noite (06/10/2026).
    "hora_abertura_operacao": 18,
    # Janela da noite (usuário, 14/10/2026): separação e embarque com horário
    # próprio, e o corte final. Nascem VAZIOS — o horário é do CD, cadastrado
    # aqui, e não um valor do código; cada noite pode ajustar na lista de
    # presença.
    "janela_sep_inicio": "",
    "janela_sep_fim": "",
    "janela_emb_inicio": "",
    "janela_emb_fim": "",
    "janela_corte": "",
    # Iniciativa do Expedito (usuário, 14/10/2026): sugere sem ser perguntado,
    # entre o início da separação e o fim da operação. Não sugere com o painel
    # parado há mais que o limite, nem com o painel do dia todo finalizado.
    "iniciativa_ativa": True,
    "iniciativa_painel_max_min": 60,
    "iniciativa_intervalo_min": 30,
}
_CHAVES_JANELA = {"janela_sep_inicio": "sep_inicio", "janela_sep_fim": "sep_fim",
                  "janela_emb_inicio": "emb_inicio", "janela_emb_fim": "emb_fim",
                  "janela_corte": "corte"}
PARAM_META = {
    "aproveitamento_min_pct": ("Aproveitamento mínimo para descer o pallet inteiro", "%", 100),
    "misto_caixas_max": ("Caixas por pallet misto", "caixas", 999),
    "misto_codigos_max": ("Códigos distintos por pallet misto", "códigos", 999),
    "dias_producao_extra": ("Dias somados à produção (embarque na madrugada)", "dias", 30),
    "misto_demanda_max_kg": ("Demanda até este peso vai para o pallet misto (0 desliga)", "kg", 5000),
    "hora_abertura_operacao": ("Hora em que a tela passa para a operação do dia seguinte", "h", 23),
    "janela_sep_inicio": ("Início previsto da separação", "hh:mm", "23:59"),
    "janela_sep_fim": ("Fim previsto da separação", "hh:mm", "23:59"),
    "janela_emb_inicio": ("Início previsto do embarque", "hh:mm", "23:59"),
    "janela_emb_fim": ("Fim previsto do embarque", "hh:mm", "23:59"),
    "janela_corte": ("Corte final da operação (separação + embarque)", "hh:mm", "23:59"),
    "iniciativa_painel_max_min": ("Sugestão por iniciativa: painel carregado há no máximo", "min", 600),
    "iniciativa_intervalo_min": ("Sugestão por iniciativa: intervalo mínimo entre sugestões", "min", 600),
}


def _agora() -> _dt.datetime:
    return _dt.datetime.now(_TZ_BRT).replace(tzinfo=None)


def _hoje() -> _dt.date:
    return _agora().date()


# ── auth (cópia local, como nos outros blueprints, para não importar de
#    web/server.py e criar import circular) ────────────────────────────
def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("user"):
            return jsonify({"error": "Sessão expirada. Faça login novamente."}), 401
        return f(*a, **k)
    return d


def csrf_required(f):
    @wraps(f)
    def d(*a, **k):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            tok = request.headers.get("X-CSRF-Token", "")
            ses = session.get("csrf_token", "")
            if not ses or not hmac.compare_digest(tok, ses):
                return jsonify({"error": "Não foi possível validar a requisição. "
                                         "Recarregue a página e tente novamente."}), 403
        return f(*a, **k)
    return d


def gerente_required(f):
    """Guia lateral do gerente e cadastro — Gerente e Desenvolvedor."""
    @wraps(f)
    def d(*a, **k):
        if session.get("perfil") not in ("admin", "master"):
            return jsonify({"error": "Acesso restrito a gerentes e administradores."}), 403
        return f(*a, **k)
    return d


def bloquear_escrita(f):
    @wraps(f)
    def d(*a, **k):
        if somente_leitura():
            return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
        return f(*a, **k)
    return d


def init_expedito_db():
    with get_db() as db:
        db.executescript("""
        -- Posições de picking: o cadastro guarda ESPAÇO, não produto. O
        -- endereço de cada código não é fixo (usuário, 06/10/2026).
        CREATE TABLE IF NOT EXISTS expedito_posicao (
            usuario      TEXT NOT NULL,
            local        TEXT NOT NULL,
            temperatura  TEXT NOT NULL,
            camara       TEXT NOT NULL DEFAULT '',
            rua          TEXT NOT NULL DEFAULT '',
            box          TEXT NOT NULL DEFAULT '',
            altura       INTEGER NOT NULL DEFAULT 1,
            profundidade INTEGER NOT NULL DEFAULT 1,
            metade       TEXT NOT NULL DEFAULT '',
            meia         INTEGER NOT NULL DEFAULT 0,
            ativa        INTEGER NOT NULL DEFAULT 1,
            observacao   TEXT DEFAULT '',
            atualizado_em TEXT,
            PRIMARY KEY (usuario, box, altura, profundidade, metade)
        );
        CREATE INDEX IF NOT EXISTS idx_expedito_posicao_local
            ON expedito_posicao(usuario, local, temperatura);
        CREATE TABLE IF NOT EXISTS expedito_parametros (
            cd            TEXT PRIMARY KEY,
            dados         TEXT NOT NULL,
            atualizado_em TEXT,
            usuario       TEXT
        );
        -- Histórico: cada geração de plano e cada carga do painel da noite
        -- ficam guardadas para o gerente reler a operação pelo calendário.
        CREATE TABLE IF NOT EXISTS expedito_plano (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario    TEXT NOT NULL,
            tipo       TEXT NOT NULL,          -- abastecimento | acompanhamento
            data_op    TEXT NOT NULL,
            momento    TEXT NOT NULL,
            dados      TEXT NOT NULL,
            criado_por TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_expedito_plano_dia
            ON expedito_plano(usuario, tipo, data_op, momento DESC);

        -- Quem está na casa AGORA. O WMS diz quem separou, não quem está
        -- presente, e sem essa lista não há sugestão: recomendar carga para
        -- quem faltou é pior que não recomendar nada. Muda durante a noite
        -- (alguém sai às 23h, alguém entra cobrindo), e cada carga do painel
        -- guarda a composição que valia naquela hora.
        CREATE TABLE IF NOT EXISTS expedito_presenca (
            usuario   TEXT NOT NULL,
            data_op   TEXT NOT NULL,
            turno     TEXT NOT NULL,
            separador TEXT NOT NULL,
            presente  INTEGER NOT NULL DEFAULT 1,
            atualizado_em TEXT,
            PRIMARY KEY (usuario, data_op, turno, separador)
        );

        -- Sessão da IA: o mapa de codinomes daquela noite. Precisa ser
        -- GRAVADO, não recalculado: a lista de presentes muda durante o
        -- turno e, se o mapa fosse refeito a cada pergunta, o "Separador B"
        -- das 20h seria outra pessoa às 23h — a conversa perderia o fio e a
        -- tradução de volta passaria a apontar o nome errado. Daqui em
        -- diante o mapa só cresce (ver expedito_ia.ampliar_mapa).
        CREATE TABLE IF NOT EXISTS expedito_ia_sessao (
            usuario       TEXT NOT NULL,
            data_op       TEXT NOT NULL,
            dados         TEXT,
            atualizado_em TEXT,
            PRIMARY KEY (usuario, data_op)
        );

        -- Conversa, em tabela própria e não em expedito_plano: lá cada
        -- registro guarda o retrato inteiro em JSON, e pendurar mensagem
        -- nisso faria a linha crescer sem teto.
        --
        -- Duas colunas de texto, de propósito: `texto` é o que a tela
        -- mostra (nome real), `texto_ia` é o que volta para o Gemini no
        -- histórico (codinome). Guardar só o revelado faria a pergunta
        -- seguinte reenviar os nomes reais que a máscara tinha tirado.
        CREATE TABLE IF NOT EXISTS expedito_ia_mensagem (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario    TEXT NOT NULL,
            data_op    TEXT NOT NULL,
            papel      TEXT NOT NULL,
            autor      TEXT NOT NULL,
            texto      TEXT,
            texto_ia   TEXT,
            status     TEXT,
            meta       TEXT,
            momento    TEXT,
            criado_por TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_expedito_ia_msg
            ON expedito_ia_mensagem(usuario, data_op, papel, id);

        -- Nome do operador como a operação o conhece (09/10/2026). O WMS
        -- traz o nome do cadastro dele; aqui o analista renomeia (e, dando
        -- o mesmo nome a dois cadastros, junta a mesma pessoa) ou oculta
        -- quem não faz mais parte do efetivo. Por conta do Local: vale para
        -- presença, retrato, desempenho e conversa da IA.
        CREATE TABLE IF NOT EXISTS expedito_operador (
            usuario       TEXT NOT NULL,
            nome_wms      TEXT NOT NULL,
            nome_exibicao TEXT NOT NULL DEFAULT '',
            oculto        INTEGER NOT NULL DEFAULT 0,
            atualizado_em TEXT,
            alterado_por  TEXT,
            PRIMARY KEY (usuario, nome_wms)
        );

        -- Box de pallet MISTO no picking (14/10/2026). O pallet que está lá
        -- carrega vários códigos: não "permanece no chão" atendendo um deles
        -- e não desce. Por box, valendo para todos os níveis.
        CREATE TABLE IF NOT EXISTS expedito_posicao_misto (
            usuario   TEXT NOT NULL,
            box       TEXT NOT NULL,
            criado_em TEXT,
            criado_por TEXT,
            PRIMARY KEY (usuario, box)
        );

        -- Janela da noite ajustada na lista de presença (14/10/2026). Sem
        -- linha aqui, vale o padrão dos parâmetros.
        CREATE TABLE IF NOT EXISTS expedito_janela (
            usuario   TEXT NOT NULL,
            data_op   TEXT NOT NULL,
            turno     TEXT NOT NULL,
            dados     TEXT NOT NULL,
            atualizado_em TEXT,
            PRIMARY KEY (usuario, data_op, turno)
        );

        -- Migrações de dado que rodam uma vez só.
        CREATE TABLE IF NOT EXISTS expedito_migracao (
            nome   TEXT PRIMARY KEY,
            quando TEXT
        );
        """)
        # a PK de expedito_posicao mudou: migra fora do executescript, para
        # um erro aqui não derrubar o boot do sistema inteiro
        try:
            _migrar_pk_posicao(db)
        except Exception as exc:
            print(f"[DB] AVISO: migração da PK de expedito_posicao falhou: {exc}")
    try:
        _migrar_rotulo_embarque()
    except Exception as exc:
        print(f"[DB] AVISO: migração do rótulo da noite falhou: {exc}")
    # Conferente e separador na mesma lista de presença, separados pela função
    # (usuário, 14/10/2026). Quem já estava marcado era separador.
    with get_db() as db:
        cols = {r[1] for r in db.execute("PRAGMA table_info(expedito_presenca)")}
        if "funcao" not in cols:
            db.execute("ALTER TABLE expedito_presenca ADD COLUMN funcao TEXT NOT NULL "
                       "DEFAULT 'separador'")


# Tabelas cuja data é o rótulo da NOITE. Até 09/10/2026 o rótulo era o dia em
# que a noite começa; passou a ser o dia do embarque (o do carimbo do WMS),
# um dia depois. O plano de abastecimento tem data própria e fica de fora.
_ROTULO_NOITE = (("expedito_presenca", ""), ("expedito_ia_sessao", ""),
                 ("expedito_ia_mensagem", ""),
                 ("expedito_plano", " AND tipo='acompanhamento'"))


def _migrar_rotulo_embarque() -> None:
    """Soma um dia ao rótulo do que já foi gravado — uma vez só, numa
    transação. Em duas etapas (marca e tira o '#') para a troca não esbarrar
    na chave da linha do dia seguinte."""
    with get_db() as db:
        if db.execute("SELECT 1 FROM expedito_migracao WHERE nome='rotulo_embarque'").fetchone():
            return
        n = 0
        for tabela, filtro in _ROTULO_NOITE:
            n += db.execute(
                f"UPDATE {tabela} SET data_op = '#' || date(data_op, '+1 day') "
                f"WHERE data_op GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'{filtro}"
            ).rowcount
            db.execute(f"UPDATE {tabela} SET data_op = substr(data_op, 2) "
                       f"WHERE data_op LIKE '#%'")
        db.execute("INSERT INTO expedito_migracao(nome, quando) VALUES ('rotulo_embarque', ?)",
                   (_agora().strftime("%Y-%m-%d %H:%M:%S"),))
    if n:
        print(f"[DB] Expedito: {n} registro(s) da noite passaram ao rótulo do dia do embarque")


def _migrar_pk_posicao(db) -> None:
    """A PK de expedito_posicao virou (usuario, box, altura, profundidade,
    metade).

    Antes ela carregava local, camara e rua. A planilha exportada do WMS não
    traz esses campos (vêm vazios), então a chave de gravação deixou de casar
    com a identidade usada para confrontar o cadastro: "alterar" inseria uma
    segunda linha para o mesmo box e "remover" não apagava nada (achado na
    revisão pré-deploy, 08/10/2026).

    Box repetido com local/câmara diferentes colapsa numa linha só — é a
    consequência pretendida, já que o box é único no WMS.
    """
    pk = [r[1] for r in db.execute("PRAGMA table_info(expedito_posicao)").fetchall() if r[5]]
    tem_old = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='expedito_posicao_old'"
    ).fetchone() is not None
    # `_old` de pé = migração incompleta (o DDL commita sozinho e os INSERTs
    # voltam atrás), então retoma a partir dela em vez de pular
    if pk == ["usuario", "box", "altura", "profundidade", "metade"] and not tem_old:
        return
    fonte = "expedito_posicao_old" if tem_old else "expedito_posicao"
    antes = db.execute(f"SELECT COUNT(*) c FROM {fonte}").fetchone()["c"]
    if not tem_old:
        db.execute("ALTER TABLE expedito_posicao RENAME TO expedito_posicao_old")
    db.execute("""
        CREATE TABLE IF NOT EXISTS expedito_posicao (
            usuario      TEXT NOT NULL,
            local        TEXT NOT NULL,
            temperatura  TEXT NOT NULL,
            camara       TEXT NOT NULL DEFAULT '',
            rua          TEXT NOT NULL DEFAULT '',
            box          TEXT NOT NULL DEFAULT '',
            altura       INTEGER NOT NULL DEFAULT 1,
            profundidade INTEGER NOT NULL DEFAULT 1,
            metade       TEXT NOT NULL DEFAULT '',
            meia         INTEGER NOT NULL DEFAULT 0,
            ativa        INTEGER NOT NULL DEFAULT 1,
            observacao   TEXT DEFAULT '',
            atualizado_em TEXT,
            PRIMARY KEY (usuario, box, altura, profundidade, metade)
        )""")
    db.execute("""
        INSERT OR REPLACE INTO expedito_posicao
            (usuario, local, temperatura, camara, rua, box, altura, profundidade,
             metade, meia, ativa, observacao, atualizado_em)
        SELECT usuario, local, temperatura, camara, rua, box, altura, profundidade,
               metade, meia, ativa, observacao, atualizado_em
          FROM expedito_posicao_old""")
    depois = db.execute("SELECT COUNT(*) c FROM expedito_posicao").fetchone()["c"]
    # box repetido em local/câmara diferentes colapsa de propósito, então a
    # conta esperada é a de BOXES distintos, não a de linhas
    esperado = db.execute(
        "SELECT COUNT(*) c FROM (SELECT DISTINCT usuario, box, altura, profundidade, "
        "metade FROM expedito_posicao_old)").fetchone()["c"]
    if depois >= esperado:
        db.execute("DROP TABLE expedito_posicao_old")
        if antes:
            print(f"[DB] Migração: expedito_posicao agora tem PK por box "
                  f"({antes} -> {depois} linha(s))")
    else:
        print(f"[DB] AVISO: migração de expedito_posicao incompleta "
              f"({depois} de {esperado}). expedito_posicao_old foi MANTIDA e a "
              f"migração será retomada no próximo boot.")


def _parametros(cd: str) -> dict:
    with get_db() as db:
        row = db.execute("SELECT dados FROM expedito_parametros WHERE cd=?", (cd,)).fetchone()
    base = dict(PARAMETROS_PADRAO)
    if row:
        try:
            base.update({k: v for k, v in json.loads(row["dados"]).items()
                         if k in PARAMETROS_PADRAO
                         and not (isinstance(v, float) and not math.isfinite(v))})
        except (ValueError, TypeError, AttributeError):
            pass
    return base


def _tipos_venda_vistos(usuario: str) -> list[dict]:
    """Tipos de venda que apareceram no último follow up carregado.

    A tela de parâmetros não tem o arquivo em mãos, então as caixas de seleção
    saem do catálogo que o último plano guardou. Antes da primeira geração a
    lista vem vazia e a tela cai no campo de texto livre.
    """
    with get_db() as db:
        row = db.execute(
            "SELECT dados FROM expedito_plano WHERE usuario=? AND tipo=? "
            "ORDER BY id DESC LIMIT 1", (usuario, "abastecimento")).fetchone()
    if not row:
        return []
    try:
        return (json.loads(row["dados"]) or {}).get("tipos_venda") or []
    except (ValueError, TypeError):
        return []


def _posicoes(usuario: str) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM expedito_posicao WHERE usuario=? ORDER BY local, temperatura, rua, box, altura",
            (usuario,)).fetchall()
    return [{**dict(r), "ativa": bool(r["ativa"]), "meia": bool(r["meia"])} for r in rows]


def _chaves_picking(usuario: str) -> set:
    """Endereços que SÃO posição de picking — é assim que o plano sabe o que
    já está no chão."""
    # A chave é o BOX + nível. Câmara e rua ficam fora: o TXT escreve
    # câmara como "RESF"/"CONG" e rua como letra, a planilha do WMS traz
    # Deposito e Rua numéricos, e nada disso casa — o código do box é o
    # único campo comum às duas fontes (conferido em 08/10/2026).
    return {(p["box"].upper(), str(p["altura"]), str(p["profundidade"]))
            for p in _posicoes(usuario) if p["ativa"]}


def _boxes_misto(usuario: str) -> list:
    with get_db() as db:
        return [r["box"] for r in db.execute(
            "SELECT box FROM expedito_posicao_misto WHERE usuario=? ORDER BY box",
            (usuario,)).fetchall()]


def _salvar_plano(usuario: str, tipo: str, data_op: str, dados: dict) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO expedito_plano(usuario, tipo, data_op, momento, dados, criado_por) "
            "VALUES (?,?,?,?,?,?)",
            (usuario, tipo, data_op, _agora().strftime("%Y-%m-%d %H:%M:%S"),
             json.dumps(dados, ensure_ascii=False, default=str), session.get("user", "")))
        return cur.lastrowid


# ── página ───────────────────────────────────────────────────────────
@expedito_bp.route("/expedito")
def pagina():
    from flask import redirect, url_for
    if not session.get("user"):
        return redirect(url_for("login_page"))
    if not tem_acesso_modulo(MODULO):
        return redirect("/")
    return render_template("expedito.html")


# ── abastecimento do picking ─────────────────────────────────────────
@expedito_bp.route("/api/expedito/picking", methods=["POST"])
@login_required
@modulo_required(MODULO)
@bloquear_escrita
@csrf_required
def gerar_picking():
    """Follow up + TXTs de estoque -> plano de descida.

    Os arquivos entram na hora e NÃO ficam guardados: o que fica é o plano.
    O TXT é a foto do momento, e guardar um arquivo de 40 MB por geração
    encheria o disco da VM sem servir para nada.
    """
    usuario = usuario_efetivo()
    p = _parametros(usuario)

    arq_follow = request.files.get("follow_up")
    arqs_txt = request.files.getlist("estoque")
    if not arq_follow or not arq_follow.filename:
        return jsonify({"error": "Envie o follow up de pedidos."}), 400
    if not arqs_txt:
        return jsonify({"error": "Envie ao menos um TXT de estoque."}), 400

    data_alvo = (request.form.get("data") or "").strip()
    try:
        alvo = _dt.date.fromisoformat(data_alvo) if data_alvo else _hoje() + _dt.timedelta(days=1)
    except ValueError:
        return jsonify({"error": "Data de embarque inválida."}), 400

    temporarios = []
    try:
        # 1. estoque: um TXT por empresa, e são eles que definem o escopo
        linhas_txt, locais, por_arquivo = [], set(), []
        for arq in arqs_txt:
            try:
                caminho = _salvar_temp(arq, ".txt")
            except ValueError as exc:
                return jsonify({"error": f"{_nome(arq)}: {exc}."}), 400
            temporarios.append(caminho)
            try:
                # o plano depende destes campos: sem produção nenhuma caixa é
                # elegível, sem peso o pallet não conta — melhor parar e dizer
                linhas = parse_produto_paletizado(caminho, obrigatorios=_OBRIGATORIOS_TXT)
            except ColunasFaltando as exc:
                d = exc.para_tela()
                d["error"] = f"{_nome(arq)}: {d['error']}"
                return jsonify(d), 422
            except Exception as exc:
                return jsonify({"error": f"{_nome(arq)}: não foi possível ler o TXT ({exc})."}), 400
            if not linhas:
                return jsonify({"error": f"{_nome(arq)}: arquivo vazio ou fora do formato "
                                         f"do Produto Paletizado."}), 400
            locais_arq = sorted({str(l.get("local", "")).strip().upper() for l in linhas if l.get("local")})
            por_arquivo.append({"arquivo": _nome(arq), "linhas": len(linhas), "locais": locais_arq})
            locais.update(locais_arq)
            linhas_txt.extend(linhas)

        # 2. follow up: recorte pelos locais que têm estoque carregado
        try:
            caminho_fup = _salvar_temp(arq_follow, ".xlsx")
        except ValueError as exc:
            return jsonify({"error": f"{_nome(arq_follow)}: {exc}."}), 400
        temporarios.append(caminho_fup)
        try:
            dem = fup.demanda(caminho_fup, alvo, locais=sorted(locais), p=p)
        except ColunasFaltando as exc:
            d = exc.para_tela()
            d["error"] = f"{arq_follow.filename}: {d['error']}"
            return jsonify(d), 422
        except ValueError as e:
            return jsonify({"error": f"{arq_follow.filename}: {e}"}), 400

        # 3. o plano
        pallets, soltas = pick.montar_pallets(linhas_txt)
        plano = pick.eleger(pick.agrupar_demanda(dem["linhas"]), pallets, soltas, alvo, p,
                            posicoes_picking=_chaves_picking(usuario),
                            boxes_misto=set(_boxes_misto(usuario)))
        plano.update({
            "arquivo_estoque": pick.conferir_idade_do_arquivo(linhas_txt, alvo, _hoje()),
            "funil": dem["funil"],
            "demanda": dem["resumo"],
            "estoque": {"arquivos": por_arquivo, "pallets": len(pallets),
                        "caixas_soltas": len(soltas), "linhas": len(linhas_txt)},
            # catálogo para as caixas de seleção da tela de parâmetros
            "tipos_venda": dem["tipos_venda"],
            "gerado_em": _agora().strftime("%Y-%m-%d %H:%M"),
            "posicoes_cadastradas": len(_chaves_picking(usuario)),
        })
        plano["id"] = _salvar_plano(usuario, "abastecimento", alvo.isoformat(), plano)
        registrar_log(session.get("user", ""), "expedito_picking",
                      alvo=alvo.isoformat(),
                      detalhe=f"{len(arqs_txt)} TXT · {dem['resumo']['linhas']} linhas de demanda · "
                              f"{plano['resumo']['movimentacoes']} movimentações")
        return jsonify(plano)
    finally:
        for c in temporarios:
            try:
                os.unlink(c)
            except OSError:
                pass


# Teto do arquivo DEScompactado: o TXT real tem 15-25 MB; acima disto é
# arquivo errado ou compactação maliciosa, e a VM tem 1 GB de memória.
_LIMITE_DESCOMPACTADO = 300 * 1024 * 1024


def _salvar_temp(arquivo, sufixo: str) -> str:
    """Grava o upload num temporário. Chegando compactado (gzip — a tela
    compacta o TXT no navegador antes de enviar, 22/10/2026: follow up + TXT
    do CDA + TXT do CDB passavam dos 48 MB do envio), descompacta aqui, em
    blocos, para quem lê receber o arquivo de sempre. Levanta ValueError."""
    with tempfile.NamedTemporaryFile(suffix=sufixo, delete=False) as tmp:
        arquivo.save(tmp.name)
        caminho = tmp.name
    with open(caminho, "rb") as f:
        compactado = f.read(2) == b"\x1f\x8b"
    if not compactado:
        return caminho
    destino = caminho[:-len(sufixo)] + "_d" + sufixo if sufixo else caminho + "_d"
    try:
        total = 0
        with gzip.open(caminho, "rb") as g, open(destino, "wb") as out:
            while True:
                bloco = g.read(1 << 20)
                if not bloco:
                    break
                total += len(bloco)
                if total > _LIMITE_DESCOMPACTADO:
                    raise ValueError("arquivo grande demais depois de descompactado")
                out.write(bloco)
    except (OSError, EOFError, ValueError) as exc:
        for c in (destino, caminho):
            try:
                os.unlink(c)
            except OSError:
                pass
        raise ValueError(f"não foi possível descompactar o arquivo enviado ({exc})") from exc
    os.unlink(caminho)
    return destino


def _nome(arquivo) -> str:
    """Nome do arquivo como o usuário escolheu (sem o .gz da compactação)."""
    n = arquivo.filename or ""
    return n[:-3] if n.lower().endswith(".gz") else n


@expedito_bp.route("/api/expedito/planos")
@login_required
@modulo_required(MODULO)
def listar_planos():
    """Histórico para o calendário — o gerente relê a operação de qualquer dia."""
    usuario = usuario_efetivo()
    tipo = (request.args.get("tipo") or "abastecimento").strip()
    data = (request.args.get("data") or "").strip()
    with get_db() as db:
        if data:
            rows = db.execute(
                "SELECT id, momento, criado_por FROM expedito_plano "
                "WHERE usuario=? AND tipo=? AND data_op=? ORDER BY momento DESC",
                (usuario, tipo, data)).fetchall()
        else:
            rows = db.execute(
                "SELECT data_op, COUNT(*) n, MAX(momento) ultimo FROM expedito_plano "
                "WHERE usuario=? AND tipo=? GROUP BY data_op ORDER BY data_op DESC LIMIT 90",
                (usuario, tipo)).fetchall()
    return jsonify({"tipo": tipo, "itens": [dict(r) for r in rows]})


@expedito_bp.route("/api/expedito/plano/<int:plano_id>")
@login_required
@modulo_required(MODULO)
def ver_plano(plano_id: int):
    usuario = usuario_efetivo()
    with get_db() as db:
        row = db.execute("SELECT * FROM expedito_plano WHERE id=? AND usuario=?",
                         (plano_id, usuario)).fetchone()
    if not row:
        return jsonify({"error": "Plano não encontrado."}), 404
    return jsonify({**dict(row), "dados": json.loads(row["dados"])})


# ── parâmetros ───────────────────────────────────────────────────────
@expedito_bp.route("/api/expedito/parametros", methods=["GET", "POST"])
@login_required
@modulo_required(MODULO)
@csrf_required
def parametros():
    usuario = usuario_efetivo()
    if request.method == "GET":
        return jsonify({"parametros": _parametros(usuario),
                        "meta": {k: {"rotulo": v[0], "unidade": v[1], "maximo": v[2]}
                                 for k, v in PARAM_META.items()},
                        "tipos_venda": _tipos_venda_vistos(usuario),
                        "pode_editar": session.get("perfil") in ("admin", "master")})
    if session.get("perfil") not in ("admin", "master"):
        return jsonify({"error": "Acesso restrito a gerentes e administradores."}), 403
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400

    atual = _parametros(usuario)
    for chave, valor in d.items():
        if chave not in PARAMETROS_PADRAO:
            continue
        if chave in ("bloqueios_aceitos",):
            atual[chave] = [str(x).strip().upper() for x in (valor or []) if str(x).strip()]
            continue
        if chave in ("tipos_venda_fora", "aguardando_aceitos"):
            # guarda o texto como o ERP exporta — a tela mostra o nome
            # completo, e o casamento é por pedaço do nome sem ligar à caixa
            atual[chave] = [str(x).strip() for x in (valor or []) if str(x).strip()]
            continue
        if chave in ("usar_data_embarque", "usar_data_entrega",
                     "considerar_pedido_mae", "iniciativa_ativa"):
            atual[chave] = bool(valor)
            continue
        if chave in _CHAVES_JANELA:
            if not str(valor or "").strip():
                atual[chave] = ""          # vazio = não cadastrado
                continue
            hora = analise_svc.hora_valida(valor)
            if not hora:
                return jsonify({"error": f"“{PARAM_META[chave][0]}” precisa estar no formato HH:MM."}), 400
            atual[chave] = hora
            continue
        try:
            num = float(str(valor).replace(",", "."))
        except (TypeError, ValueError):
            rotulo = PARAM_META.get(chave, (chave,))[0]
            return jsonify({"error": f"“{rotulo}” precisa de um número."}), 400
        teto = PARAM_META.get(chave, (None, None, 10**6))[2]
        if not math.isfinite(num) or num < 0 or num > teto:
            rotulo = PARAM_META.get(chave, (chave,))[0]
            return jsonify({"error": f"“{rotulo}” deve ficar entre 0 e {teto}."}), 400
        atual[chave] = num
    if not atual["usar_data_embarque"] and not atual["usar_data_entrega"]:
        return jsonify({"error": "Escolha ao menos uma data para filtrar: embarque ou entrega."}), 400

    with get_db() as db:
        db.execute("INSERT INTO expedito_parametros(cd, dados, atualizado_em, usuario) "
                   "VALUES (?,?,?,?) ON CONFLICT(cd) DO UPDATE SET dados=excluded.dados, "
                   "atualizado_em=excluded.atualizado_em, usuario=excluded.usuario",
                   (usuario, json.dumps(atual), _agora().strftime("%Y-%m-%d %H:%M:%S"),
                    session.get("user", "")))
    registrar_log(session.get("user", ""), "expedito_parametros", alvo=usuario,
                  detalhe=json.dumps(d, ensure_ascii=False)[:300])
    return jsonify({"ok": True, "parametros": atual})


# ── cadastro de posições de picking ──────────────────────────────────
@expedito_bp.route("/api/expedito/posicoes")
@login_required
@modulo_required(MODULO)
def listar_posicoes():
    usuario = usuario_efetivo()
    posicoes = _posicoes(usuario)
    return jsonify({"posicoes": posicoes,
                    "capacidade": ends.resumo_capacidade(posicoes),
                    "pode_editar": session.get("perfil") in ("admin", "master")})


@expedito_bp.route("/api/expedito/posicoes/modelo")
@login_required
@modulo_required(MODULO)
def modelo_posicoes():
    import io
    return send_file(io.BytesIO(ends.gerar_planilha_modelo()),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name="Posicoes_de_picking.xlsx")


@expedito_bp.route("/api/expedito/posicoes/planilha", methods=["POST"])
@login_required
@modulo_required(MODULO)
@gerente_required
@csrf_required
def subir_posicoes():
    """Cadastro e alteração em lote pela mesma planilha.

    Com `conferir=1` devolve o confronto sem gravar nada — a tela mostra
    "isto vai remover 14 posições" ANTES de acontecer.
    """
    usuario = usuario_efetivo()
    arq = request.files.get("planilha")
    if not arq or not arq.filename:
        return jsonify({"error": "Envie a planilha de posições."}), 400
    caminho = _salvar_temp(arq, ".xlsx")
    try:
        try:
            lido = ends.parse_planilha(caminho)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        confronto = ends.aplicar(_posicoes(usuario), lido)
        if request.form.get("conferir") in ("1", "true", "sim"):
            return jsonify({"conferencia": True, **_resumo_confronto(confronto)})

        agora = _agora().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as db:
            # Posição cheia que vira meia (e o contrário) troca a coluna
            # `metade`, que faz parte da chave: sem limpar as variantes do
            # box antes, a antiga ficava para trás e o box passava a contar
            # 3 faces (achado na revisão pré-deploy, 08/10/2026).
            limpos = set()
            for pos in confronto["incluir"] + confronto["alterar"]:
                alvo = (pos["box"], pos["altura"], pos["profundidade"])
                if alvo not in limpos:
                    db.execute("DELETE FROM expedito_posicao WHERE usuario=? AND box=? "
                               "AND altura=? AND profundidade=?",
                               (usuario, pos["box"], pos["altura"], pos["profundidade"]))
                    limpos.add(alvo)
                db.execute("""
                    INSERT INTO expedito_posicao(usuario, local, temperatura, camara, rua, box,
                        altura, profundidade, metade, meia, ativa, observacao, atualizado_em)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(usuario, box, altura, profundidade, metade)
                    DO UPDATE SET local=excluded.local, temperatura=excluded.temperatura,
                        camara=excluded.camara, rua=excluded.rua, meia=excluded.meia,
                        ativa=excluded.ativa, observacao=excluded.observacao,
                        atualizado_em=excluded.atualizado_em
                """, (usuario, pos["local"], pos["temperatura"], pos["camara"], pos["rua"],
                      pos["box"], pos["altura"], pos["profundidade"], pos["metade"],
                      int(pos["meia"]), int(pos["ativa"]), pos["observacao"], agora))
            for pos in confronto["remover"]:
                # remove o box inteiro, as duas metades inclusas
                db.execute("DELETE FROM expedito_posicao WHERE usuario=? AND box=? "
                           "AND altura=? AND profundidade=?",
                           (usuario, pos["box"], pos["altura"], pos["profundidade"]))
        registrar_log(session.get("user", ""), "expedito_posicoes_planilha", alvo=usuario,
                      detalhe=f"{len(confronto['incluir'])} incluídas, "
                              f"{len(confronto['alterar'])} alteradas, "
                              f"{len(confronto['remover'])} removidas")
        return jsonify({"ok": True, **_resumo_confronto(confronto)})
    finally:
        try:
            os.unlink(caminho)
        except OSError:
            pass


def _resumo_confronto(c: dict) -> dict:
    return {
        "incluir": len(c["incluir"]), "alterar": len(c["alterar"]),
        "remover": len(c["remover"]), "sem_mudanca": len(c["sem_mudanca"]),
        "avisos": c["avisos"], "erros": c["erros"],
        "remover_detalhe": [ends.rotulo(p) for p in c["remover"][:20]],
    }


@expedito_bp.route("/api/expedito/posicoes/misto", methods=["GET", "POST", "DELETE"])
@login_required
@modulo_required(MODULO)
@csrf_required
def posicoes_misto():
    """Boxes de pallet misto (usuário, 14/10/2026). POST {boxes: "C5-F28, C5-F29"}
    acrescenta; DELETE {box} tira. O plano desconsidera o pallet que está
    nesses boxes."""
    usuario = usuario_efetivo()
    if request.method == "GET":
        return jsonify({"boxes": _boxes_misto(usuario),
                        "pode_editar": session.get("perfil") in ("admin", "master")})
    if session.get("perfil") not in ("admin", "master"):
        return jsonify({"error": "Acesso restrito a gerentes e administradores."}), 403
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400
    agora = _agora().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        if request.method == "DELETE":
            box = pick.chave_box(d.get("box"))
            db.execute("DELETE FROM expedito_posicao_misto WHERE usuario=? AND box=?",
                       (usuario, box))
            detalhe = "removido: " + box
        else:
            brutos = str(d.get("boxes") or "").replace(";", ",").replace("\n", ",").split(",")
            boxes = sorted({pick.chave_box(b) for b in brutos if pick.chave_box(b)})
            if not boxes:
                return jsonify({"error": "Informe ao menos um box, por exemplo C5-F28."}), 400
            invalidos = [b for b in boxes if len(b) > 30 or not all(ch.isalnum() or ch in "-_." for ch in b)]
            if invalidos:
                return jsonify({"error": "Box fora do formato: " + ", ".join(invalidos[:5])}), 400
            db.executemany("INSERT OR IGNORE INTO expedito_posicao_misto(usuario, box, criado_em, "
                           "criado_por) VALUES (?,?,?,?)",
                           [(usuario, b, agora, session.get("user", "")) for b in boxes])
            detalhe = "incluídos: " + ", ".join(boxes[:20])
    registrar_log(session.get("user", ""), "expedito_posicao_misto", alvo=usuario, detalhe=detalhe)
    return jsonify({"ok": True, "boxes": _boxes_misto(usuario)})


@expedito_bp.route("/api/expedito/posicoes", methods=["POST", "DELETE"])
@login_required
@modulo_required(MODULO)
@gerente_required
@csrf_required
def editar_posicao():
    """Edição de uma posição pela tela, sem planilha."""
    usuario = usuario_efetivo()
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400
    campos = {
        "local": str(d.get("local", "")).strip().upper(),
        "temperatura": str(d.get("temperatura", "")).strip().upper(),
        "camara": str(d.get("camara", "")).strip().upper(),
        "rua": str(d.get("rua", "")).strip().upper(),
        "box": str(d.get("box", "")).strip().upper(),
        "altura": int(d.get("altura") or 1),
        "profundidade": int(d.get("profundidade") or 1),
        "metade": str(d.get("metade", "")).strip().upper(),
    }
    # O box identifica a posição. Local e rua deixaram de ser obrigatórios
    # porque a exportação do WMS não os traz (vêm vazios) — exigi-los aqui
    # recusava a remoção e a edição de posição cadastrada por planilha
    # (achado em 08/10/2026). Câmara e rua são preenchidas a partir do box
    # quando vierem vazias, para a tela continuar mostrando o endereço.
    if not campos["box"]:
        return jsonify({"error": "Informe o box da posição."}), 400
    if not campos["camara"]:
        campos["camara"] = ends._camara_do_box(campos["box"])
    if not campos["rua"]:
        campos["rua"] = ends._rua_do_box(campos["box"])
    # Temperatura é opcional pelo mesmo motivo: a exportação do WMS não traz.
    # Quando vier preenchida, tem de ser uma das três; vazia é aceita, e o
    # DELETE não precisa dela, porque quem identifica a posição é o box.
    if campos["temperatura"] and campos["temperatura"] not in ends.TEMPERATURAS:
        return jsonify({"error": "Temperatura deve ser RESFRIADO, CONGELADO ou AMBIENTE."}), 400
    if campos["metade"] not in ("", "A", "B"):
        return jsonify({"error": "Metade deve ser A ou B."}), 400

    chave = (usuario, campos["box"], campos["altura"], campos["profundidade"])
    with get_db() as db:
        if request.method == "DELETE":
            # o box sai inteiro, as duas metades inclusas
            db.execute("DELETE FROM expedito_posicao WHERE usuario=? AND box=? "
                       "AND altura=? AND profundidade=?", chave)
            acao = "removida"
        else:
            db.execute("DELETE FROM expedito_posicao WHERE usuario=? AND box=? "
                       "AND altura=? AND profundidade=?", chave)
            db.execute("""
                INSERT INTO expedito_posicao(usuario, local, temperatura, camara, rua, box,
                    altura, profundidade, metade, meia, ativa, observacao, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(usuario, box, altura, profundidade, metade)
                DO UPDATE SET local=excluded.local, temperatura=excluded.temperatura,
                    camara=excluded.camara, rua=excluded.rua, meia=excluded.meia,
                    ativa=excluded.ativa, observacao=excluded.observacao,
                    atualizado_em=excluded.atualizado_em
            """, (usuario, campos["local"], campos["temperatura"], campos["camara"],
                  campos["rua"], campos["box"], campos["altura"], campos["profundidade"],
                  campos["metade"], int(bool(d.get("meia"))), int(d.get("ativa", True)),
                  str(d.get("observacao", ""))[:200],
                  _agora().strftime("%Y-%m-%d %H:%M:%S")))
            acao = "salva"
    registrar_log(session.get("user", ""), "expedito_posicao", alvo=usuario,
                  detalhe=f"{acao}: {ends.rotulo(campos)}")
    posicoes = _posicoes(usuario)
    return jsonify({"ok": True, "acao": acao, "posicoes": posicoes,
                    "capacidade": ends.resumo_capacidade(posicoes)})


# ── acompanhamento da noite ──────────────────────────────────────────
def _empresas_da_noite(cd: str = "") -> list:
    """Empresas cujo painel o usuário enxerga na guia da noite.

    A Produtividade guarda a instrução por EMPRESA (CDA, CDB), enquanto o
    resto do Expedito guarda por CONTA CANÔNICA — passar a conta como se
    fosse o CD devolvia lista vazia e o retrato nascia zerado (achado ao
    rodar o painel real, 07/10/2026). Com `cd` informado, usa a régua da
    Produtividade; sem ele, o LOCAL da conta em uso.

    A noite é do Local, não da empresa: CDA e CDB dividem o Local e a
    operação roda para as duas ao mesmo tempo (usuário, 08/10/2026). Antes
    a lista saía da permissão da sessão por empresa, e um analista cadastrado
    com "CDA" não veria as instruções da CDB — a mesma operação cortada ao
    meio. Sem Local (admin fora do espelho), segue o que a sessão enxerga.
    """
    from web.produtividade import _empresas_do_cd, _pode_ver_empresa, empresas_do_local
    if cd:
        return _empresas_do_cd(cd)
    local = empresas_do_local()
    if local:
        return local
    with get_db() as db:
        todas = [r["empresa"] for r in db.execute(
            "SELECT DISTINCT empresa FROM prod_instrucao "
            "WHERE empresa IS NOT NULL AND empresa != '' ORDER BY empresa").fetchall()]
    return [e for e in todas if _pode_ver_empresa(e)]


def _dia_operacao(agora=None, p=None) -> str:
    """A operação em curso, rotulada pelo dia do EMBARQUE.

    O embarque acontece na madrugada, e o WMS carimba a noite com o dia em
    que ela termina. O usuário pediu o mesmo rótulo na tela (09/10/2026):
    "após as 18h ele já muda para o dia seguinte... eu posso chegar de manhã
    e querer ver o que foi feito para o carregamento daquela madrugada". Às
    10h de 09/10 a operação atual é a de 09/10 (a madrugada que acabou de
    sair); às 18h passa para a de 10/10.
    """
    agora = agora or _agora()
    try:
        hora = int(float((p or {}).get("hora_abertura_operacao",
                                       PARAMETROS_PADRAO["hora_abertura_operacao"])))
    except (TypeError, ValueError, OverflowError):
        hora = PARAMETROS_PADRAO["hora_abertura_operacao"]
    dia = agora.date()
    return (dia + _dt.timedelta(days=1) if agora.hour >= hora else dia).isoformat()


class DataInvalida(ValueError):
    """?data= que não é uma data — o calendário manda ano parcial enquanto a
    pessoa digita, e o fromisoformat cru virava 500 em HTML."""


def _data_op_pedida(p=None) -> str:
    bruto = (request.values.get("data") or "").strip()
    if not bruto:
        return _dia_operacao(p=p)
    try:
        dia = _dt.date.fromisoformat(bruto[:10])
    except ValueError:
        raise DataInvalida(bruto)
    if not 2000 <= dia.year <= 2100:
        raise DataInvalida(bruto)
    return dia.isoformat()


@expedito_bp.errorhandler(DataInvalida)
def _erro_data(e):
    return jsonify({"error": "Data inválida. Escolha o dia no calendário."}), 400


@expedito_bp.errorhandler(Exception)
def _erro_geral(e):
    """Toda falha da API do Expedito chega à tela como motivo, em JSON. Uma
    página de erro em HTML aparecia como "Não foi possível ler a operação",
    sem dizer o quê (09/10/2026)."""
    if not request.path.startswith("/api/"):
        if isinstance(e, HTTPException):
            return e
        raise e
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description or e.name}), e.code
    current_app.logger.exception("Falha no Expedito: %s", request.path)
    return jsonify({"error": "Erro interno ao montar a operação (%s). Avise o suporte "
                             "com o horário." % type(e).__name__}), 500


# ── nome do operador ─────────────────────────────────────────────────
def _norm_nome(n) -> str:
    return " ".join(str(n or "").upper().split())


def _operadores_cadastro(usuario: str) -> dict:
    """{NOME NO WMS: {"nome": como a operação o chama, "oculto": bool}}."""
    with get_db() as db:
        rows = db.execute("SELECT nome_wms, nome_exibicao, oculto FROM expedito_operador "
                          "WHERE usuario=?", (usuario,)).fetchall()
    return {r["nome_wms"]: {"nome": r["nome_exibicao"] or r["nome_wms"],
                            "oculto": bool(r["oculto"])} for r in rows}


def _apelidos(usuario: str) -> dict:
    return {k: v["nome"] for k, v in _operadores_cadastro(usuario).items() if v["nome"] != k}


def _aplicar_apelidos(registros: list, apelidos: dict) -> list:
    """Troca o nome do WMS pelo da operação, em cópia — o banco da
    Produtividade fica como veio."""
    if not apelidos:
        return registros
    saida = []
    for r in registros:
        n, c = _norm_nome(r.get("separador")), _norm_nome(r.get("conferente"))
        if n in apelidos or c in apelidos:
            r = {**r}
            if n in apelidos:
                r["separador"] = apelidos[n]
            if c in apelidos:
                r["conferente"] = apelidos[c]
        saida.append(r)
    return saida


def _presentes(usuario: str, data_op: str, turno: str, funcao: str = "separador") -> list:
    """Quem está em turno, por função: separador ou conferente. A coluna
    `separador` da tabela guarda o nome nos dois casos."""
    with get_db() as db:
        rows = db.execute(
            "SELECT separador FROM expedito_presenca WHERE usuario=? AND data_op=? "
            "AND turno=? AND presente=1 AND funcao=? ORDER BY separador",
            (usuario, data_op, turno, funcao)).fetchall()
    return [r["separador"] for r in rows]


def _janela(usuario: str, data_op: str, turno: str, p: dict | None = None) -> dict:
    """Janela da noite: a ajustada para esta noite, ou a padrão."""
    p = p or _parametros(usuario)
    padrao = {curta: analise_svc.hora_valida(p.get(longa))
              for longa, curta in _CHAVES_JANELA.items()}
    with get_db() as db:
        row = db.execute("SELECT dados FROM expedito_janela WHERE usuario=? AND data_op=? "
                         "AND turno=?", (usuario, data_op, turno)).fetchone()
    noite = {}
    if row:
        try:
            noite = {k: v for k, v in json.loads(row["dados"]).items()
                     if k in padrao and analise_svc.hora_valida(v)}
        except (ValueError, TypeError, AttributeError):
            noite = {}
    return {**padrao, **{k: v for k, v in noite.items() if v}}


def _marcar_quem_operou(usuario: str, data_op: str, turno: str, foto: dict) -> dict:
    """Presença automática ao carregar o painel (usuário, 14/10/2026): quem
    separou ou conferiu nesta noite estava em turno. Acrescenta, nunca tira —
    a marcação manual continua mandando para quem não aparece no painel."""
    # Na mesma noite a pessoa separa OU confere (usuário, 14/10/2026). Quem
    # aparece nas duas funções no painel entra na que mais fez.
    n_sep, n_conf = {}, {}
    for x in foto.get("detalhe") or []:
        s_ = (x.get("separador") or "").strip().upper()
        c_ = (x.get("conferente") or "").strip().upper()
        if s_ and x.get("sep_ini") and x.get("estado") != "fila":
            n_sep[s_] = n_sep.get(s_, 0) + 1
        if c_ and (x.get("emb_ini") or x.get("emb_t")):
            n_conf[c_] = n_conf.get(c_, 0) + 1
    seps = {n for n, q in n_sep.items() if q >= n_conf.get(n, 0)}
    confs = {n for n in n_conf if n not in seps}
    agora = _agora().strftime("%Y-%m-%d %H:%M:%S")
    novos = {"separadores": 0, "conferentes": 0}
    with get_db() as db:
        for funcao, nomes, chave in (("separador", seps, "separadores"),
                                     ("conferente", confs - seps, "conferentes")):
            for n in sorted(nomes):
                cur = db.execute(
                    "INSERT OR IGNORE INTO expedito_presenca(usuario, data_op, turno, separador, "
                    "presente, atualizado_em, funcao) VALUES (?,?,?,?,1,?,?)",
                    (usuario, data_op, turno, n, agora, funcao))
                novos[chave] += cur.rowcount or 0
    return novos


def _separadores_wms(empresas: list, coluna: str = "separador") -> list:
    if not empresas or coluna not in ("separador", "conferente"):
        return []
    marcas = ",".join("?" * len(empresas))
    with get_db() as db:
        rows = db.execute(
            f"SELECT DISTINCT {coluna} AS nome FROM prod_instrucao WHERE empresa IN ({marcas}) "
            f"AND {coluna} IS NOT NULL AND {coluna} != '' ORDER BY {coluna}",
            empresas).fetchall()
    return sorted({_norm_nome(r["nome"]) for r in rows} - {""})


def _operadores(usuario: str, empresas: list, funcao: str | None = None) -> list:
    """Efetivo do Local como a tela mostra: [{nome, nomes_wms, oculto}]. Dois
    cadastros do WMS com o mesmo nome dado na tela viram uma pessoa só.
    `funcao` None = separadores e conferentes juntos (gestão de nomes)."""
    cad = _operadores_cadastro(usuario)
    grupos: dict[str, dict] = {}
    if funcao is None:
        nomes_wms = sorted(set(_separadores_wms(empresas)) | set(_separadores_wms(empresas, "conferente")))
    else:
        nomes_wms = _separadores_wms(empresas, funcao)
    for wms in nomes_wms:
        info = cad.get(wms) or {"nome": wms, "oculto": False}
        g = grupos.setdefault(info["nome"], {"nome": info["nome"], "nomes_wms": [],
                                             "oculto": True})
        g["nomes_wms"].append(wms)
        # a pessoa some da lista quando TODOS os cadastros dela estão ocultos
        g["oculto"] = g["oculto"] and info["oculto"]
    return sorted(grupos.values(), key=lambda g: g["nome"])


def _separadores_conhecidos(empresas: list, usuario: str | None = None,
                            funcao: str = "separador") -> list:
    """Quem já operou nos arquivos deste CD — a lista para marcar presença,
    em vez de digitar nome. Com o nome dado pela operação e sem os ocultos."""
    if usuario is None:
        return _separadores_wms(empresas, funcao)
    return [g["nome"] for g in _operadores(usuario, empresas, funcao) if not g["oculto"]]


@expedito_bp.route("/api/expedito/presenca", methods=["GET", "POST"])
@login_required
@modulo_required(MODULO)
@csrf_required
def presenca():
    usuario = usuario_efetivo()
    p = _parametros(usuario)
    turno = (request.values.get("turno") or "noturno").strip().lower()
    data_op = _data_op_pedida(p)

    if request.method == "GET":
        empresas = _empresas_da_noite((request.values.get("cd") or "").strip().upper())
        return jsonify({
            "data_op": data_op, "turno": turno, "empresas": empresas,
            "operacao_atual": _dia_operacao(p=p),
            "presentes": _presentes(usuario, data_op, turno),
            "conferentes_presentes": _presentes(usuario, data_op, turno, "conferente"),
            "conhecidos": _separadores_conhecidos(empresas, usuario),
            "conferentes_conhecidos": _separadores_conhecidos(empresas, usuario, "conferente"),
            "ocultos": [g["nome"] for g in _operadores(usuario, empresas) if g["oculto"]],
            "janela": _janela(usuario, data_op, turno, p),
            "pode_editar": not somente_leitura(),
        })

    if somente_leitura():
        return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not isinstance(d.get("presentes"), list):
        return jsonify({"error": "Informe a lista de separadores presentes."}), 400
    nomes = [str(n).strip().upper() for n in d["presentes"] if str(n).strip()]
    confs = [str(n).strip().upper() for n in (d.get("conferentes") or []) if str(n).strip()]
    janela = d.get("janela")
    if janela is not None:
        if not isinstance(janela, dict):
            return jsonify({"error": "Janela da noite em formato inválido."}), 400
        # campo vazio = vale o cadastrado em Parâmetros
        brutos = {k: str(v or "").strip() for k, v in janela.items()
                  if k in analise_svc.CHAVES_JANELA}
        janela = {k: analise_svc.hora_valida(v) for k, v in brutos.items() if v}
        if any(v is None for v in janela.values()):
            return jsonify({"error": "Os horários da janela precisam estar no formato HH:MM."}), 400
    duas = sorted(set(nomes) & set(confs))
    if duas:
        quem = ", ".join(duas[:5]) + (" e outros" if len(duas) > 5 else "")
        return jsonify({"error": "Na mesma noite a pessoa separa ou confere: " + quem
                                 + (" está marcado" if len(duas) == 1 else " estão marcados")
                                 + " nas duas listas."}), 400
    agora = _agora().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        db.execute("DELETE FROM expedito_presenca WHERE usuario=? AND data_op=? AND turno=?",
                   (usuario, data_op, turno))
        db.executemany(
            "INSERT OR IGNORE INTO expedito_presenca(usuario, data_op, turno, separador, presente, "
            "atualizado_em, funcao) VALUES (?,?,?,?,1,?,?)",
            [(usuario, data_op, turno, n, agora, "separador") for n in nomes]
            + [(usuario, data_op, turno, n, agora, "conferente") for n in confs])
        if janela is not None:
            db.execute("INSERT INTO expedito_janela(usuario, data_op, turno, dados, atualizado_em) "
                       "VALUES (?,?,?,?,?) ON CONFLICT(usuario, data_op, turno) DO UPDATE SET "
                       "dados=excluded.dados, atualizado_em=excluded.atualizado_em",
                       (usuario, data_op, turno, json.dumps(janela), agora))
    registrar_log(session.get("user", ""), "expedito_presenca", alvo=data_op + "/" + turno,
                  detalhe=f"{len(nomes)} separador(es), {len(confs)} conferente(s)")
    return jsonify({"ok": True, "presentes": nomes, "conferentes": confs, "data_op": data_op,
                    "janela": _janela(usuario, data_op, turno)})


@expedito_bp.route("/api/expedito/noite", methods=["GET", "POST"])
@login_required
@modulo_required(MODULO)
@csrf_required
def acompanhamento():
    """Retrato da operação agora.

    No POST o painel do WMS entra junto e é gravado na MESMA tabela da
    Produtividade — um upload, dois usos. No GET usa o que já está no banco,
    para a tela abrir mostrando a última situação sem exigir arquivo.
    """
    from web.produtividade import _gravar, empresas_para_upload, preencher_empresa
    usuario = usuario_efetivo()
    p_exp = _parametros(usuario)
    turno = (request.values.get("turno") or "noturno").strip().lower()
    data_op = _data_op_pedida(p_exp)

    arquivos_lidos, erros = [], []
    if request.method == "POST":
        if somente_leitura():
            return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
        for arq in [a for a in request.files.getlist("painel") if a and a.filename]:
            sufixo = os.path.splitext(arq.filename)[1].lower() or ".xls"
            caminho = _salvar_temp(arq, sufixo)
            try:
                info = ler_arquivo(caminho)
            except ValueError as e:
                erros.append(arq.filename + ": " + str(e))
                continue
            except Exception:
                erros.append(arq.filename + ": não foi possível ler o arquivo.")
                continue
            finally:
                try:
                    os.unlink(caminho)
                except OSError:
                    pass
            falta_empresa = preencher_empresa(info, request.form.get("empresa", ""))
            if falta_empresa:
                # Sem a coluna Empresa e com mais de uma empresa possível, a
                # tela precisa perguntar — devolve a lista para ela montar a
                # escolha, em vez de gravar zero e dizer "arquivo lido".
                return jsonify({"error": falta_empresa, "precisa_empresa": True,
                                "empresas_upload": empresas_para_upload()}), 400
            with get_db() as db:
                n = _gravar(db, info["tipo"], info["registros"])
            if not n and info["registros"]:
                erros.append(arq.filename + ": nenhuma instrução gravada.")
                continue
            arquivos_lidos.append({"arquivo": arq.filename, "tipo": info["tipo"],
                                   "instrucoes": n})
        if erros and not arquivos_lidos:
            return jsonify({"error": " · ".join(erros)}), 400

    # as empresas são resolvidas DEPOIS do upload: na primeira carga do dia o
    # banco ainda não tinha nada, e resolver antes devolvia lista vazia — o
    # retrato nascia zerado justamente no arquivo que acabara de entrar
    # (achado no teste ponta a ponta, 07/10/2026)
    empresas = _empresas_da_noite((request.values.get("cd") or "").strip().upper())
    foto, sugestoes, desempenho, registros = _retrato_da_noite(
        usuario, data_op, turno, empresas)
    automatica = None
    if request.method == "POST" and arquivos_lidos and registros:
        # quem aparece separando ou conferindo no painel entra na presença
        automatica = _marcar_quem_operou(usuario, data_op, turno, foto)
        if automatica["separadores"] or automatica["conferentes"]:
            foto, sugestoes, desempenho, registros = _retrato_da_noite(
                usuario, data_op, turno, empresas)
    presentes = _presentes(usuario, data_op, turno)

    saida = {"data_op": data_op, "turno": turno, "empresas": empresas,
             "operacao_atual": _dia_operacao(p=p_exp),
             "pode_editar": _pode_tirar_instrucao(),
             "empresas_upload": empresas_para_upload(),
             "retrato": foto, "sugestoes": sugestoes,
             "veiculos": _situacao_veiculos(usuario, foto),
             "desempenho": desempenho, "arquivos": arquivos_lidos, "erros": erros,
             "tiradas": foto.get("tiradas") or [],
             "analise": foto.get("analise"),
             "presenca_automatica": automatica,
             "sem_presentes": not presentes, "sem_painel": not registros,
             "momento": _agora().strftime("%Y-%m-%d %H:%M")}
    # o Expedito pode sugerir por conta própria, só na operação de hoje
    saida["iniciativa"] = None
    if registros and data_op == _dia_operacao(p=p_exp):
        try:
            saida["iniciativa"] = _pode_tomar_iniciativa(usuario, data_op, foto, p_exp) or "ativa"
            if saida["iniciativa"] == "ativa":
                _tomar_iniciativa(usuario, data_op, foto, sugestoes, desempenho, p_exp)
        except Exception as exc:
            current_app.logger.exception("Iniciativa do Expedito: %s", exc)
    if request.method == "POST" and registros:
        saida["id"] = _salvar_plano(usuario, "acompanhamento", data_op, saida)
        registrar_log(session.get("user", ""), "expedito_noite", alvo=data_op,
                      detalhe=str(len(registros)) + " instruções · "
                              + str(len(presentes)) + " presentes")
    return jsonify(saida)

# ── iniciativa do Expedito ───────────────────────────────────────────
_PEDIDO_INICIATIVA = (
    "INICIATIVA DO EXPEDITO: o analista não perguntou nada. Leia o contexto e "
    "aponte no máximo duas ações que mereçam atenção AGORA na operação — risco "
    "de a separação ou o embarque não fecharem na janela ou no corte, fila "
    "entre separação e embarque, separador parado, alocação da próxima "
    "instrução. Comece pela ação. Não repita sugestão já feita no histórico "
    "se os números não mudaram. Se nada exigir ação agora, responda "
    "exatamente SEM_SUGESTAO.")


def _ultima_iniciativa(usuario: str, data_op: str):
    with get_db() as db:
        return db.execute(
            "SELECT momento, meta FROM expedito_ia_mensagem WHERE usuario=? AND data_op=? "
            "AND papel='analista' AND meta LIKE '%\"iniciativa\": true%' ORDER BY id DESC LIMIT 1",
            (usuario, data_op)).fetchone()


def _pode_tomar_iniciativa(usuario: str, data_op: str, foto: dict, p: dict) -> str | None:
    """None quando pode; senão, o motivo (vai para a tela, como aviso)."""
    if not p.get("iniciativa_ativa", True):
        return "iniciativa desligada nos parâmetros"
    an = foto.get("analise") or {}
    if not an:
        return "sem análise da noite"
    if an.get("concluida"):
        return "operação finalizada"
    if (foto.get("painel_ha_min") or 0) > float(p.get("iniciativa_painel_max_min") or 60):
        return "painel sem carga recente"
    ini = analise_svc.quando((an.get("janela") or {}).get("sep_inicio") or "",
                             _dt.date.fromisoformat(data_op))
    if not ini:
        return "início da separação não cadastrado em Parâmetros"
    if _agora() < ini:
        return "antes do início da separação"
    ult = _ultima_iniciativa(usuario, data_op)
    if ult:
        try:
            meta = json.loads(ult["meta"] or "{}")
        except ValueError:
            meta = {}
        if meta.get("painel") == foto.get("momento"):
            return "nenhum painel novo desde a última sugestão"
        try:
            quando_ult = _dt.datetime.strptime(str(ult["momento"])[:19], "%Y-%m-%d %H:%M:%S")
            if (_agora() - quando_ult).total_seconds() / 60 < float(p.get("iniciativa_intervalo_min") or 30):
                return "sugestão recente"
        except ValueError:
            pass
    return None


def _tomar_iniciativa(usuario: str, data_op: str, foto: dict, sugestoes: list,
                      desempenho: dict, p: dict) -> bool:
    """Dispara a análise por iniciativa, na mesma thread da conversa. Sem
    nada a sugerir, ou com a IA fora, a mensagem fica oculta — iniciativa não
    gera ruído."""
    if _pode_tomar_iniciativa(usuario, data_op, foto, p):
        return False
    cfg = get_expedito_ia_config()
    chave = get_expedito_ia_chave()
    if not chave or not cfg["ativo"]:
        return False
    if any(m["status"] == "pendente" for m in _ia_mensagens(usuario, data_op, "analista", 10)):
        return False
    mapa_pessoas = _ia_mapa(usuario, data_op, _ia_nomes_da_noite(foto, desempenho))
    contexto, mapa = ia.mascarar(foto, sugestoes, desempenho, mapa_pessoas)
    contexto["linha_do_tempo"] = _ia_linha_do_tempo(usuario, data_op)
    proibidos = ia.termos_proibidos(foto, desempenho)
    historico = [{"papel": ("usuario" if m["autor"] == "usuario" else "expedito"),
                  "texto": m["texto_ia"]}
                 for m in _ia_mensagens(usuario, data_op, "analista", 8)
                 if m["status"] == "pronta" and m["texto_ia"]]
    meta0 = {"iniciativa": True, "painel": foto.get("momento")}
    msg_id = _ia_gravar(usuario, data_op, "analista", "expedito", "", "", "pendente", meta0,
                        "expedito")
    modelo = cfg["modelo"]

    def _rodar():
        try:
            okey, texto, meta = ia.conversar(chave, _PEDIDO_INICIATIVA, contexto, historico,
                                             "analista", modelo, proibidos)
            if okey and "SEM_SUGESTAO" not in texto.upper().replace(" ", "_"):
                _ia_concluir(msg_id, ia.revelar(texto, mapa), texto, "pronta", {**meta, **meta0})
            else:
                _ia_concluir(msg_id, "", "", "oculta", {**(meta or {}), **meta0})
        except Exception as e:
            print("[EXPEDITO_IA] Iniciativa falhou: %s" % e)
            _ia_concluir(msg_id, "", "", "oculta", meta0)

    threading.Thread(target=_rodar, daemon=True).start()
    return True


# ── tirar instrução da operação ──────────────────────────────────────
# Perfis que respondem pela operação de um CD. Tirar uma instrução muda o
# retrato e a Produtividade — fica com quem gere o CD, e vai para o log.
_PERFIS_GESTAO = ("admin", "master", "coordenador", "analista")


def _pode_tirar_instrucao() -> bool:
    return session.get("perfil") in _PERFIS_GESTAO and not somente_leitura()


@expedito_bp.route("/api/expedito/instrucao/desconsiderar", methods=["POST"])
@login_required
@modulo_required(MODULO)
@bloquear_escrita
@csrf_required
def tirar_instrucao():
    """Tira (ou devolve) instruções da operação — o "lixo" de teste que não
    saía da lista de veículos (usuário, 09/10/2026).

    Grava na mesma marcação de "desconsiderar" da Produtividade: fica à parte
    da instrução, então carregar o painel de novo NÃO a traz de volta, e a
    instrução que não é da operação também não conta no ritmo de ninguém.
    """
    if not _pode_tirar_instrucao():
        return jsonify({"error": "Seu perfil não pode alterar as instruções da operação."}), 403
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not isinstance(d.get("itens"), list) or not d["itens"]:
        return jsonify({"error": "Informe as instruções."}), 400
    tirar = bool(d.get("desconsiderar", True))
    do_local = set(_empresas_da_noite((request.values.get("cd") or "").strip().upper()))
    itens = []
    for x in d["itens"][:50]:
        if not isinstance(x, dict):
            continue
        emp = str(x.get("empresa") or "").strip().upper()
        inst = str(x.get("instrucao") or "").strip()
        if not emp or not inst:
            continue
        if emp not in do_local:
            return jsonify({"error": f"A instrução {inst} não é deste Local."}), 403
        itens.append((emp, inst))
    if not itens:
        return jsonify({"error": "Informe as instruções."}), 400
    motivo = str(d.get("motivo") or "Tirada da operação no Expedito")[:200]
    quem = session.get("user", "")
    agora = _agora().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        for emp, inst in itens:
            if tirar:
                db.execute(
                    "INSERT INTO prod_desconsiderar(empresa, instrucao, motivo, usuario, quando) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(empresa, instrucao) DO UPDATE SET "
                    "motivo=excluded.motivo, usuario=excluded.usuario, quando=excluded.quando",
                    (emp, inst, motivo, quem, agora))
            else:
                db.execute("DELETE FROM prod_desconsiderar WHERE empresa=? AND instrucao=?",
                           (emp, inst))
    registrar_log(quem, "expedito_tirar_instrucao" if tirar else "expedito_devolver_instrucao",
                  alvo=", ".join(f"{e}/{i}" for e, i in itens), detalhe=motivo if tirar else "")
    return jsonify({"ok": True, "alteradas": len(itens), "desconsiderar": tirar})


@expedito_bp.route("/api/expedito/operador", methods=["GET", "POST"])
@login_required
@modulo_required(MODULO)
@csrf_required
def operador():
    """Renomear ou ocultar um operador do efetivo (usuário, 09/10/2026).

    POST {nome, novo_nome?, oculto?}: `nome` é como a tela mostra hoje — pode
    juntar mais de um cadastro do WMS, e a mudança vale para todos eles.
    Renomear para o nome de outra pessoa da lista junta as duas. `novo_nome`
    vazio devolve o nome do WMS.
    """
    usuario = usuario_efetivo()
    empresas = _empresas_da_noite((request.values.get("cd") or "").strip().upper())
    if request.method == "GET":
        return jsonify({"operadores": _operadores(usuario, empresas),
                        "pode_editar": not somente_leitura()})
    if somente_leitura():
        return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400
    atual = _norm_nome(d.get("nome"))
    grupo = next((g for g in _operadores(usuario, empresas) if g["nome"] == atual), None)
    if grupo is None:
        return jsonify({"error": f"Operador {atual or '(vazio)'} não encontrado neste Local."}), 404
    cad = _operadores_cadastro(usuario)
    novo = None
    if "novo_nome" in d:
        novo = _norm_nome(d.get("novo_nome"))
        if len(novo) > 60:
            return jsonify({"error": "O nome passou de 60 caracteres."}), 400
        if novo and not all(ch.isalnum() or ch in " .'-" for ch in novo):
            return jsonify({"error": "Use letras, números, espaço, ponto, hífen ou apóstrofo."}), 400
    oculto = d.get("oculto")
    agora = _agora().strftime("%Y-%m-%d %H:%M:%S")
    quem = session.get("user", "")
    with get_db() as db:
        for wms in grupo["nomes_wms"]:
            antes = cad.get(wms) or {"nome": wms, "oculto": False}
            nome_final = antes["nome"] if novo is None else (novo or wms)
            oculto_final = antes["oculto"] if oculto is None else bool(oculto)
            db.execute(
                "INSERT INTO expedito_operador(usuario, nome_wms, nome_exibicao, oculto, "
                "atualizado_em, alterado_por) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(usuario, nome_wms) DO UPDATE SET "
                "nome_exibicao=excluded.nome_exibicao, oculto=excluded.oculto, "
                "atualizado_em=excluded.atualizado_em, alterado_por=excluded.alterado_por",
                (usuario, wms, "" if nome_final == wms else nome_final,
                 1 if oculto_final else 0, agora, quem))
        if novo is not None and (novo or grupo["nomes_wms"][0]) != atual:
            destino = novo or grupo["nomes_wms"][0]
            # a presença já marcada acompanha o nome novo
            db.execute("UPDATE OR IGNORE expedito_presenca SET separador=? "
                       "WHERE usuario=? AND separador=?", (destino, usuario, atual))
            db.execute("DELETE FROM expedito_presenca WHERE usuario=? AND separador=?",
                       (usuario, atual))
        if oculto:
            # oculto sai da presença da operação atual em diante
            db.execute("DELETE FROM expedito_presenca WHERE usuario=? AND separador=? "
                       "AND data_op >= ?", (usuario, atual, _dia_operacao(p=_parametros(usuario))))
    registrar_log(quem, "expedito_operador", alvo=atual,
                  detalhe=("renomeado para " + (novo or "o nome do WMS")) if novo is not None
                  else ("ocultado" if oculto else "reexibido"))
    return jsonify({"ok": True, "operadores": _operadores(usuario, empresas)})


# ── camada de IA ─────────────────────────────────────────────────────
# O cálculo medido continua sendo o chão: a IA raciocina EM CIMA do mesmo
# retrato que a tela mostra, nunca em lugar dele. Por isso _retrato_da_noite
# é compartilhado — se a conversa montasse o retrato por conta própria, ela
# responderia sobre número diferente do que o analista está vendo.

def _retrato_da_noite(usuario: str, data_op: str, turno: str, empresas: list):
    """(foto, sugestoes, desempenho, registros) — caminho único do retrato."""
    from web.produtividade import _desconsideradas, _parametros_do_cd, _registros
    p_prod = _parametros_do_cd(empresas[0] if empresas else usuario)
    # `data_op` é o dia do EMBARQUE, o mesmo do carimbo do WMS: a noite
    # começa na véspera. Busca a véspera também, porque quem separa uma noite
    # da outra é a janela do turno, dentro de retrato(); instrução sem
    # horário nenhum só entra com o carimbo desta noite.
    dia = _dt.date.fromisoformat(data_op)
    vespera = (dia - _dt.timedelta(days=1)).isoformat()
    apelidos = _apelidos(usuario)
    registros = _aplicar_apelidos(_registros(empresas, vespera, data_op), apelidos)
    presentes = _presentes(usuario, data_op, turno)
    # Histórico de turnos ANTERIORES, como o card promete: carimbos até a
    # véspera — a noite de hoje fica de fora.
    desde = (dia - _dt.timedelta(days=31)).isoformat()
    historicos = _aplicar_apelidos(_registros(empresas, desde, vespera), apelidos)
    desempenho = noite.desempenho_historico(historicos, p_prod)
    janela = _janela(usuario, data_op, turno)
    fim_sep_previsto = (analise_svc.quando(janela["sep_fim"], dia)
                        if janela.get("sep_fim") else None)
    # Momento do retrato = o do ÚLTIMO PAINEL carregado, não o da tela. Medir
    # no relógio da tela fazia quem parou no painel das 19h30 parecer ocioso
    # até a hora em que alguém clicou em "Atualizar retrato".
    agora = _agora()
    momento = agora
    carimbos = [str(r.get("atualizado_em") or "") for r in registros if r.get("atualizado_em")]
    if carimbos:
        try:
            ultimo = _dt.datetime.strptime(max(carimbos)[:19], "%Y-%m-%d %H:%M:%S")
            momento = min(agora, ultimo)
        except ValueError:
            pass
    foto = noite.retrato(registros, presentes, momento, p_prod, turno,
                         _desconsideradas(empresas), dia_operacao=vespera,
                         ritmo_historico=desempenho, carimbo=data_op,
                         fim_previsto=fim_sep_previsto)
    foto["painel_ha_min"] = round((agora - momento).total_seconds() / 60)
    sugestoes = noite.sugerir_alocacao(foto, desempenho,
                                       float(p_prod.get("sku_complexa", 30)))
    # análise da operação: término, janela de separação e de embarque contra
    # o corte, ondas de liberação (quando o resumo do painel foi carregado),
    # ociosidade e gargalos — a mesma que a IA recebe
    try:
        foto["analise"] = analise_svc.analisar(
            foto, data_op, janela,
            _presentes(usuario, data_op, turno, "conferente"), p_prod,
            historico=analise_svc.historico_embarque(historicos))
    except Exception as exc:
        current_app.logger.exception("Análise da noite falhou: %s", exc)
        foto["analise"] = None
    return foto, sugestoes, desempenho, registros


def _situacao_veiculos(usuario: str, foto: dict) -> dict:
    """Onde estão as carretas da noite.

    O rastreio (OpenTech + Ravex, unificado) é a fonte: o painel do WMS marca
    "Chegada Prevista" para todas, sempre, porque não tem integração com a
    Ravex (usuário, 08/10/2026). Falha de rastreio não derruba o retrato — o
    indicador some e o resto da tela continua.
    """
    try:
        from app.database import get_cds_localizacao
        from app.services.monitoramento_unificado import get_df_monitoramento_unificado
        cd = cd_do_usuario() or ""
        coords = {c["sigla"].strip().upper(): c for c in get_cds_localizacao()}
        alvo = coords.get(cd.strip().upper())
        if alvo is None and len(coords) == 1:
            alvo = next(iter(coords.values()))
        return veic.situacao(foto.get("detalhe") or [],
                             get_df_monitoramento_unificado(),
                             (alvo or {}).get("latitude"),
                             (alvo or {}).get("longitude"), _agora())
    except Exception as e:
        print("[EXPEDITO_VEICULOS] %s" % e)
        return {"veiculos": [], "resumo": {"total": 0, "indisponivel": True}}


def _ia_mapa(usuario: str, data_op: str, nomes) -> dict:
    """Mapa {NOME: codinome} da noite, lido do banco e ampliado se preciso."""
    with get_db() as db:
        row = db.execute(
            "SELECT dados FROM expedito_ia_sessao WHERE usuario=? AND data_op=?",
            (usuario, data_op)).fetchone()
    atual = {}
    if row:
        try:
            atual = json.loads(row["dados"]).get("pessoas") or {}
        except (ValueError, TypeError):
            atual = {}
    mapa, cresceu = ia.ampliar_mapa(atual, nomes)
    if cresceu:
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO expedito_ia_sessao(usuario, data_op, dados, "
                "atualizado_em) VALUES (?,?,?,?)",
                (usuario, data_op, json.dumps({"pessoas": mapa}, ensure_ascii=False),
                 _agora().strftime("%Y-%m-%d %H:%M:%S")))
    return mapa


def _ia_nomes_da_noite(foto: dict, desempenho: dict) -> list:
    """Todo nome que aparece na noite, na ordem dos presentes primeiro —
    quem está em casa fica com as primeiras letras, que é o que a conversa
    mais usa."""
    nomes = list((foto.get("equipe") or {}).get("presentes") or [])
    for x in (foto.get("detalhe") or []):
        for campo in ("separador", "conferente"):
            n = (x.get(campo) or "").strip().upper()
            if n and n not in nomes:
                nomes.append(n)
    for n in sorted(desempenho or {}):
        if n and n not in nomes:
            nomes.append(n)
    return nomes


def _ia_linha_do_tempo(usuario: str, data_op: str, limite: int = 24) -> list:
    """Como a noite evoluiu, carga a carga: é a memória do dia.

    Sai de expedito_plano, que já guarda um registro por leitura do painel.
    Números apenas — nada de nome, cliente ou placa.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT momento, dados FROM expedito_plano WHERE usuario=? AND tipo=? "
            "AND data_op=? ORDER BY id LIMIT ?",
            (usuario, "acompanhamento", data_op, int(limite))).fetchall()
    linha = []
    for r in rows:
        try:
            d = json.loads(r["dados"]) or {}
        except (ValueError, TypeError):
            continue
        f = d.get("retrato") or {}
        linha.append({
            "momento": str(r["momento"] or "")[11:16],
            "toneladas": f.get("toneladas"),
            "ritmo": (f.get("ritmo") or {}).get("t_h_por_pessoa"),
            "risco": (f.get("projecao") or {}).get("risco"),
            "presentes": len(((f.get("equipe") or {}).get("presentes") or [])),
        })
    return linha


def _ia_noites_anteriores(usuario: str, data_op: str, dias: int = 14) -> list:
    """Memória longa do gerente: o fechamento das noites anteriores.

    O usuário separou as duas memórias (06/10/2026): o analista conversa
    sobre a noite dele, a gerência olha o padrão ao longo dos dias. Guarda a
    ÚLTIMA leitura de cada dia, que é a que mais perto está do fechamento.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT data_op, MAX(id) ultimo FROM expedito_plano WHERE usuario=? "
            "AND tipo=? AND data_op < ? GROUP BY data_op "
            "ORDER BY data_op DESC LIMIT ?",
            (usuario, "acompanhamento", data_op, int(dias))).fetchall()
        ids = [r["ultimo"] for r in rows]
        if not ids:
            return []
        marcas = ",".join("?" * len(ids))
        dados = db.execute(
            "SELECT data_op, dados FROM expedito_plano WHERE id IN (" + marcas + ")",
            ids).fetchall()
    saida = []
    for r in dados:
        try:
            f = (json.loads(r["dados"]) or {}).get("retrato") or {}
        except (ValueError, TypeError):
            continue
        saida.append({
            "dia": r["data_op"],
            "toneladas": (f.get("toneladas") or {}).get("separadas"),
            "ritmo": (f.get("ritmo") or {}).get("t_h_por_pessoa"),
            "risco": (f.get("projecao") or {}).get("risco"),
            "presentes": len(((f.get("equipe") or {}).get("presentes") or [])),
            "instrucoes": (f.get("instrucoes") or {}).get("total"),
        })
    return sorted(saida, key=lambda x: x["dia"], reverse=True)


# Resposta pendente há mais que isto é de uma thread que não volta mais (o
# serviço reiniciou no meio): a tela esperava para sempre.
_PENDENTE_MAX_MIN = 6


def _ia_mensagens(usuario: str, data_op: str, papel: str, limite: int = 40) -> list:
    limite_ts = (_agora() - _dt.timedelta(minutes=_PENDENTE_MAX_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        # a iniciativa interrompida some; a resposta a uma pergunta avisa
        db.execute(
            "UPDATE expedito_ia_mensagem SET status='oculta' WHERE usuario=? AND data_op=? AND "
            "papel=? AND status='pendente' AND momento < ? AND meta LIKE '%\"iniciativa\": true%'",
            (usuario, data_op, papel, limite_ts))
        db.execute(
            "UPDATE expedito_ia_mensagem SET status='erro', texto=? WHERE usuario=? AND "
            "data_op=? AND papel=? AND status='pendente' AND momento < ?",
            ("A resposta foi interrompida no servidor. Pergunte de novo.",
             usuario, data_op, papel, limite_ts))
        rows = db.execute(
            "SELECT id, autor, texto, texto_ia, status, meta, momento FROM "
            "expedito_ia_mensagem WHERE usuario=? AND data_op=? AND papel=? "
            "ORDER BY id DESC LIMIT ?",
            (usuario, data_op, papel, int(limite))).fetchall()
    saida = []
    for r in reversed(rows):
        meta = {}
        try:
            meta = json.loads(r["meta"] or "{}")
        except (ValueError, TypeError):
            pass
        saida.append({"id": r["id"], "autor": r["autor"], "texto": r["texto"] or "",
                      "texto_ia": r["texto_ia"] or "", "status": r["status"] or "pronta",
                      "momento": str(r["momento"] or "")[11:16], "meta": meta})
    return saida


def _ia_gravar(usuario, data_op, papel, autor, texto, texto_ia, status, meta=None,
               quem="") -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO expedito_ia_mensagem(usuario, data_op, papel, autor, texto, "
            "texto_ia, status, meta, momento, criado_por) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (usuario, data_op, papel, autor, texto, texto_ia, status,
             json.dumps(meta or {}, ensure_ascii=False),
             _agora().strftime("%Y-%m-%d %H:%M:%S"), quem))
        return cur.lastrowid


def _ia_concluir(msg_id: int, texto: str, texto_ia: str, status: str, meta: dict) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE expedito_ia_mensagem SET texto=?, texto_ia=?, status=?, meta=? "
            "WHERE id=?",
            (texto, texto_ia, status, json.dumps(meta or {}, ensure_ascii=False), msg_id))


@expedito_bp.route("/api/expedito/ia/conversa", methods=["GET", "POST"])
@login_required
@modulo_required(MODULO)
@csrf_required
def ia_conversa():
    """GET lista a conversa da noite; POST faz a pergunta.

    O POST NÃO espera o Gemini responder: grava a resposta como pendente,
    dispara a chamada em thread e devolve na hora. O Gunicorn de produção
    roda 1 worker sync — segurar a requisição por 45 s travaria o site
    inteiro para todos os usuários, que é exatamente o estrago registrado no
    BACKLOG quando o Playwright rodava dentro do worker. A tela pergunta de
    novo até a resposta ficar pronta.
    """
    usuario = usuario_efetivo()
    p_exp = _parametros(usuario)
    papel = (request.values.get("papel") or "analista").strip().lower()
    if papel not in ("analista", "gerente"):
        papel = "analista"
    # A conversa do gerente trata desempenho nominal de pessoa — é a visão
    # que o usuário restringiu em 06/10/2026. Esconder o botão na tela não é
    # controle de acesso: o gate tem de estar aqui, nos dois métodos.
    if papel == "gerente" and session.get("perfil") not in PERFIS_IRRESTRITOS:
        return jsonify({"error": "Acesso restrito a gerentes e administradores."}), 403

    turno = (request.values.get("turno") or "noturno").strip().lower()
    data_op = _data_op_pedida(p_exp)
    cfg = get_expedito_ia_config()

    if request.method == "GET":
        msgs = [m for m in _ia_mensagens(usuario, data_op, papel) if m["status"] != "oculta"]
        return jsonify({
            "data_op": data_op, "turno": turno, "papel": papel,
            "mensagens": [{k: v for k, v in m.items() if k != "texto_ia"} for m in msgs],
            "pendente": any(m["status"] == "pendente" for m in msgs),
            "configurada": bool(cfg["has_chave"]) and bool(cfg["ativo"]),
            "pode_perguntar": not somente_leitura(),
        })

    if somente_leitura():
        return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400
    pergunta = str(d.get("pergunta") or "").strip()
    if not pergunta:
        return jsonify({"error": "Escreva a pergunta."}), 400
    if len(pergunta) > 2000:
        return jsonify({"error": "A pergunta passou de 2000 caracteres."}), 400
    chave = get_expedito_ia_chave()
    if not chave or not cfg["ativo"]:
        return jsonify({"error": "A conversa com a IA não está configurada. "
                                 "Cadastre a chave do Gemini em Configurações."}), 400

    empresas = _empresas_da_noite((request.values.get("cd") or "").strip().upper())
    foto, sugestoes, desempenho, registros = _retrato_da_noite(
        usuario, data_op, turno, empresas)
    if not registros:
        return jsonify({"error": "Não há painel do WMS carregado para esta noite. "
                                 "Envie o painel antes de conversar."}), 400

    mapa_pessoas = _ia_mapa(usuario, data_op,
                            _ia_nomes_da_noite(foto, desempenho))
    contexto, mapa = ia.mascarar(foto, sugestoes, desempenho, mapa_pessoas)
    contexto["linha_do_tempo"] = _ia_linha_do_tempo(usuario, data_op)
    if papel == "gerente":
        contexto["noites_anteriores"] = _ia_noites_anteriores(usuario, data_op)
    proibidos = ia.termos_proibidos(foto, desempenho)

    # a pergunta digitada é campo livre: mascara antes de qualquer coisa
    pergunta_ia = ia.mascarar_texto(pergunta, mapa)
    historico = [{"papel": ("usuario" if m["autor"] == "usuario" else "expedito"),
                  "texto": m["texto_ia"]}
                 for m in _ia_mensagens(usuario, data_op, papel, 12)
                 if m["status"] == "pronta" and m["texto_ia"]]

    quem = session.get("user", "")
    _ia_gravar(usuario, data_op, papel, "usuario", pergunta, pergunta_ia,
               "pronta", {}, quem)
    msg_id = _ia_gravar(usuario, data_op, papel, "expedito", "", "", "pendente",
                        {}, quem)
    modelo = cfg["modelo"]

    analise = foto.get("analise")

    def _calculada(motivo: str, meta: dict):
        """O Expedito não devolve "sem resposta" (usuário, 14/10/2026): sem a
        IA a tempo, responde com a análise calculada da noite."""
        if not analise:
            return False
        texto = (analise_svc.resposta_calculada(analise, foto)
                 + "\n> Resposta montada com os números da noite: " + motivo
                 + ". Pergunte de novo para a análise completa.")
        _ia_concluir(msg_id, texto, "", "pronta", {**meta, "calculada": True})
        return True

    def _rodar():
        try:
            okey, texto, meta = ia.conversar(
                chave, pergunta_ia, contexto, historico, papel, modelo, proibidos)
            if okey:
                _ia_concluir(msg_id, ia.revelar(texto, mapa), texto, "pronta", meta)
            elif not (meta.get("transitorio") and _calculada(texto, meta)):
                _ia_concluir(msg_id, texto, "", "erro", meta)
        except Exception as e:
            # Nunca repassa o texto cru da exceção para a tela: pode carregar
            # a URL da chamada ou o corpo da requisição.
            print("[EXPEDITO_IA] Falha ao conversar: %s" % e)
            if not _calculada("falha inesperada no serviço de IA", {}):
                _ia_concluir(msg_id, "Falha inesperada ao falar com a IA. "
                                     "Tente novamente.", "", "erro", {})

    threading.Thread(target=_rodar, daemon=True).start()
    registrar_log(quem, "expedito_ia_pergunta", alvo=data_op + "/" + papel,
                  detalhe="%d caracteres" % len(pergunta))
    return jsonify({"ok": True, "id": msg_id, "data_op": data_op, "papel": papel})


@expedito_bp.route("/api/expedito/ia/limpar", methods=["POST"])
@login_required
@modulo_required(MODULO)
@bloquear_escrita
@csrf_required
def ia_limpar():
    """Recomeça a conversa do dia. O mapa de codinomes FICA: apagar o mapa
    faria a mesma pessoa trocar de codinome no meio da noite."""
    usuario = usuario_efetivo()
    papel = (request.values.get("papel") or "analista").strip().lower()
    if papel == "gerente" and session.get("perfil") not in PERFIS_IRRESTRITOS:
        return jsonify({"error": "Acesso restrito a gerentes e administradores."}), 403
    data_op = _data_op_pedida(_parametros(usuario))
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM expedito_ia_mensagem WHERE usuario=? AND data_op=? AND papel=?",
            (usuario, data_op, papel))
        n = cur.rowcount
    registrar_log(session.get("user", ""), "expedito_ia_limpar",
                  alvo=data_op + "/" + papel, detalhe="%d mensagem(ns)" % n)
    return jsonify({"ok": True, "removidas": n})
