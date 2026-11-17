"""
Servidor Flask — Monitoramento CD Demo  v31
SQLite para persistência, autenticação com PBKDF2.
python web/server.py  →  http://localhost:5000
"""
import io, os, sys, tempfile, secrets, hmac, time
from datetime import datetime, timedelta
import zoneinfo as _zi
_BRT = _zi.ZoneInfo('America/Sao_Paulo')
from pathlib import Path
from functools import wraps

from flask import (Flask, render_template, request, jsonify,
                   send_file, session, redirect, url_for, Response)
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.utils.paths import DATA_DIR as _DATA_DIR

import threading
import shutil
import subprocess

from app.database import (
    get_db,
    get_cards_por_linha, set_cards_por_linha,
    get_changelog_visto, set_changelog_visto,
    get_opentech_config, get_opentech_credenciais, set_opentech_config,
    get_ravex_config, get_ravex_credenciais, set_ravex_config,
    get_xsense_config, get_xsense_credenciais, set_xsense_config,
    get_xsense_telegram_config, set_xsense_telegram_config, get_xsense_telegram_token,
    get_expedito_ia_config, set_expedito_ia_config, get_expedito_ia_chave,
    limpar_expedito_ia_chave,
    get_cd_usuario, set_cd_usuario,
    registrar_espelho, remover_espelho, heartbeat_espelho, get_observadores, get_usuarios_com_dados,
    listar_usuarios, criar_usuario, editar_usuario, excluir_usuario,
    salvar_sessao, carregar_sessao,
    init_db, verificar_login, trocar_senha,
    get_cd, set_cd,
    get_validacao, set_validacao, get_todas_validacoes,
    get_redespacho_selecionados, toggle_redespacho,
    get_observacao, set_observacao, get_todas_observacoes,
    get_cd_localizacao, get_cds_localizacao, set_cd_localizacao, del_cd_localizacao, is_master,
    registrar_log, listar_log_auditoria, listar_acoes_log,
    get_captcha_config, get_captcha_secret, set_captcha_config,
    get_empresas_consolidadas, set_empresa_consolidada, del_empresa_consolidada,
    get_preferencia, set_preferencia)
from app.services.estoque_faturado_service    import carregar_estoque_faturado
from app.services.controle_distribuicao_service import carregar_controle_distribuicao
from app.services.painel_monitoramento_service  import carregar_painel_monitoramento
from app.services.redespacho_service            import (carregar_redespacho,
                                                        carregar_chaves_estoque_redespacho,
                                                        COLUNAS_REDESPACHO)
from app.services.consolidacao_service          import consolidar
from app.services.export_image_service          import exportar_imagem, exportar_imagem_outras, html_painel, html_outras
from app.services.screenshot_service            import htmls_to_pngs_cada
from app.utils.saida_link                       import url_saida, link_valido
from app.services.export_redespacho_image_service import exportar_imagem_redespacho
from app.services.export_excel_service          import exportar_excel
from app.services.arrival_estimator             import aplicar_estimativas, _load_schedule
from app.services.opentech_automation           import testar_conexao_opentech
from app.services.ravex_automation              import testar_conexao_ravex
from app.services.xsense_scraper                import testar_conexao_xsense
from app.services.telegram_alertas              import testar_bot as testar_bot_telegram, definir_comandos as _definir_comandos_telegram
from app.services import (opentech_state, ravex_state, monitoramento_unificado, carretas_viagem,
                          descarga_service, card_validacao)
from app.services.temperatura_service           import obter_serie_temperatura, detectar_variacao_brusca
from app.utils.paths                            import ensure_app_folders
from app.utils.number_utils                     import parse_int_br
from app.utils.rate_limit                       import rate_limited
from app.utils.access_control                    import (
    MODULOS, modulo_required, modulos_da_sessao, tem_acesso_modulo, pode_ver_localizacao_exata,
    usuario_efetivo as _usuario_efetivo_compartilhado, bloquear_escrita, somente_leitura,
    unidades_extras_permitidas_sessao, conta_canonica_do_cd, cds_da_conta_canonica,
    pode_gravar_espelhando, rota_inicial,
)

app = Flask(__name__, template_folder="templates", static_folder="static")
# Atrás do nginx, remote_addr era sempre 127.0.0.1: o limite de tentativas de
# login valia para todos juntos e o log de auditoria não guardava o IP real
# (05/11/2026). O nginx manda X-Forwarded-For e o gunicorn escuta só em
# 127.0.0.1, então confiar em UM salto (o nginx) não deixa ninguém forjar o IP.
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
# O Flask ordena as chaves do JSON em ordem ALFABÉTICA por padrão, e isso
# embaralhava os gráficos da tela: a faixa "6 - 10" caía depois de "26 - 30"
# (a imagem exportada saía certa porque é montada em Python). Mantendo a
# ordem de inserção, a tela respeita a ordem definida no serviço.
app.json.sort_keys = False

# Central de novidades (MEL-10/MEL-12) — histórico de atualizações mostrado
# pro usuário. Cada entrada nova vai NO TOPO da lista (mais recente primeiro).
# "versao" é o identificador comparado com users.changelog_visto — só precisa
# ser único e crescente (a data serve bem); ao adicionar uma entrada nova, o
# modal volta a aparecer pra todo mundo automaticamente, sem mexer no banco.
CHANGELOG_ENTRADAS = [
    {
        "versao": "2026-07-25",
        "publicado_em": "25/07/2026 12:30",
        "titulo": "Novidades do sistema",
        "grupos": [
            {"titulo": "✅ Mais confiabilidade", "itens": [
                "As validações de \"Regra Data CD\" e \"Entregas Frente\" não somem mais depois que a OpenTech atualiza ou o sistema reinicia.",
                "Espelhamento mais estável: os cards não somem mais ao espelhar outro usuário, e o aviso \"sendo espelhado\" desaparece corretamente quando a pessoa sai.",
                "A atualização da OpenTech não trava mais o sistema — agora roda em segundo plano.",
            ]},
            {"titulo": "🌡 Novidades no painel", "itens": [
                "Histórico de temperatura por carreta com gráfico — clique no ícone de temperatura no card.",
                "Redespacho: cargas já recebidas no CD somem automaticamente da fila (cruzamento com o relatório de Estoque Redespacho).",
                "Menu lateral (☰) pra navegar entre os módulos.",
            ]},
            {"titulo": "📦 Adensamento", "itens": [
                "Cadastro de códigos agora é individual por usuário, com opção de herdar de outro usuário.",
                "Histórico de importações com calendário e função de espelhar (ver o que outro usuário fez).",
            ]},
            {"titulo": "🔒 Segurança e controle", "itens": [
                "Reforços de proteção contra ações indevidas e limite de tentativas de login.",
                "Controle de acesso por módulo (dá pra restringir quem vê Monitoramento e/ou Adensamento).",
                "Log de auditoria (Admin/Master, em ⚙ Configurações → Administração) registrando login, edições e ações administrativas.",
            ]},
            {"titulo": "🔧 Pequenos ajustes", "itens": [
                "Corrigido: a caixinha de informação do gráfico de temperatura não força mais a barra de rolagem ao passar o mouse no ponto mais recente.",
                "A gestão de usuários agora é só em ⚙ Configurações → Administração (o antigo modal duplicado foi removido).",
            ]},
        ],
    },
]
CHANGELOG_VERSAO = CHANGELOG_ENTRADAS[0]["versao"]

# ── SEGURANÇA ─────────────────────────────────────────────
# Detecta produção: se SECRET_KEY foi definida via env var, ou se
# não está rodando em localhost. Assim não precisa alterar nada no deploy.
_secret_env = os.environ.get("SECRET_KEY", "")
_is_prod = bool(_secret_env) or not any(
    os.environ.get("COMPUTERNAME", "").lower().startswith(p)
    for p in ("localhost",)
)
# Em produção usa a chave do ambiente; em dev usa uma fixa (ok para localhost)
if not _secret_env and os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "sim", "yes"):
    # Demo pública sem SECRET_KEY configurada: chave estável, igual em todas
    # as instâncias da Vercel — com uma chave aleatória por processo, a
    # sessão aberta numa instância não vale na outra e o visitante volta para
    # o login. Na demo a sessão só dá acesso ao usuário visitante.
    import hashlib as _hl
    _secret_env = _hl.sha256(("cd-demo-publica:" + os.environ.get("VERCEL_PROJECT_ID", "")).encode()).hexdigest()
app.secret_key = _secret_env or secrets.token_hex(32)  # sem SECRET_KEY: chave aleatória por processo
app.permanent_session_lifetime = timedelta(hours=8)

# Cookies seguros — SECURE só em produção (HTTPS); HTTPONLY e SAMESITE sempre.
# _is_prod já existia para isso mas não estava sendo usado aqui — com SECURE
# sempre True, o cookie de sessão era descartado pelo navegador em dev local
# (HTTP puro), quebrando login/sessão fora da VM.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = _is_prod

# Inicializa banco na partida
init_db()
ensure_app_folders()

# --- Adensamento ---
from web.adensamento import adensamento_bp, init_adensamento_db
init_adensamento_db()
app.register_blueprint(adensamento_bp)

# --- Painel do Armazém (FEAT-10) ---
from web.fifo import fifo_bp, init_fifo_db
init_fifo_db()
app.register_blueprint(fifo_bp)

from web.armazem import armazem_bp, init_armazem_db
init_armazem_db()
app.register_blueprint(armazem_bp)

# --- Xsense (monitoramento de câmaras) ---
from web.xsense import xsense_bp, init_xsense_db
init_xsense_db()
app.register_blueprint(xsense_bp)

# --- Mapa de Estoque (layout de câmaras + ocupação, Fase 1: 31/08/2026) ---
from web.mapa_estoque import mapa_estoque_bp, init_mapa_estoque_db
init_mapa_estoque_db()
app.register_blueprint(mapa_estoque_bp)

# --- Giro de Estoque (venda ÷ estoque médio, 05/09/2026) ---
from web.giro import giro_bp, init_giro_db
init_giro_db()
app.register_blueprint(giro_bp)

# --- Produtividade de Separação (painel + resumo do WMS, 16/09/2026) ---
from web.produtividade import produtividade_bp, init_produtividade_db
init_produtividade_db()
app.register_blueprint(produtividade_bp)

# --- Expedito: copiloto da expedição (abastecimento do picking + noite) ---
from web.expedito import expedito_bp, init_expedito_db
init_expedito_db()
app.register_blueprint(expedito_bp)

# --- Bot de carretas no Telegram (fase 4 do painel de carretas, 19/10/2026) ---
from web.carretas_bot import carretas_bot_bp, registrar_fonte_cards
app.register_blueprint(carretas_bot_bp)

# --- SLAs: Acompanhamento e Termo de Avença (27/10/2026) ---
from web.slas import slas_bp, init_slas_db
init_slas_db()
app.register_blueprint(slas_bp)

# --- Mapeamento de colunas dos relatórios (20/10/2026) ---
from web.mapeamento import mapeamento_bp
from app.services.mapeamento_colunas import ColunasFaltando
app.register_blueprint(mapeamento_bp)

# --- Outras operações: aba do painel de carretas pelo Controle + OpenTech (20/10/2026) ---
from web.outras_operacoes import outras_operacoes_bp
from app.services import outras_operacoes as _outras_operacoes
app.register_blueprint(outras_operacoes_bp)

# --- Monitoramento de Entregas (dados da Ravex, 13/11/2026) ---
from web.entregas import entregas_bp, init_entregas_db
init_entregas_db()
app.register_blueprint(entregas_bp)
# Coletor Ravex (extensão do Chrome do operador, 15/11/2026): rotas sem
# sessão, autenticadas pela chave de coleta — ver web/entregas_coleta.py
from web.entregas_coleta import coletor_bp
app.register_blueprint(coletor_bp)

# --- Ferramenta de validação (temporário, 11/11/2026): Picking + Adensamento ---
from web.validacao import validacao_bp, modulo_ativo as _validacao_ativa
app.register_blueprint(validacao_bp)

# --- Regra de data do CD pelo Romaneio Detalhado (16/11/2026) ---
from web.romaneio_regra import romaneio_regra_bp, registrar_fonte_painel as _registrar_painel_romaneio
app.register_blueprint(romaneio_regra_bp)

# Modo demonstração (DEMO_MODE=1): sem login, só os módulos da demo e dados
# fictícios. Desligado, não muda nada — ver web/demo.py.
from web.demo import instalar as _instalar_demo, ATIVO as _DEMO, MODULOS_DEMO as _MODULOS_DEMO
_instalar_demo(app)


def _painel_para_romaneio(conta: str) -> dict:
    """Cards, notas da placa e o último Estoque em Trânsito da conta — o
    mesmo estado que /api/dados e /api/itens_nf usam."""
    st = _estado_usuario(conta)
    pasta = _DATA_DIR / "user_uploads" / (conta or "").strip().lower()
    return {"col": st["df_consolidado"], "itens": st["df_itens"],
            "estoque": _arquivo_mais_recente(pasta, "estoque.*") if pasta.exists() else None}


_registrar_painel_romaneio(_painel_para_romaneio)

# ── SCHEDULER OPENTECH (background) ──────────────────────
# BUG-5: a automação (Playwright/Chromium) NÃO roda mais aqui — rodava numa
# thread deste worker Gunicorn e travava o sistema inteiro por 30-90s a cada
# ciclo (só há 1 worker sync). Agora ela roda em processo separado, disparado
# por cron (scripts/atualizar_opentech.py). Este loop só faz um trabalho leve:
# verificar se o painel em disco mudou e reprocessar os usuários carregados.
_opentech_thread = None
_opentech_stop = threading.Event()
_OPENTECH_SYNC_INTERVALO_SEG = 20

def _opentech_loop():
    while not _opentech_stop.is_set():
        try:
            # Reprocessa se QUALQUER uma das duas fontes de rastreio mudou em
            # disco (ver MEL-14 no BACKLOG — base unificada OpenTech+Ravex).
            novo_ot = opentech_state.sincronizar_com_arquivo()
            novo_rx = ravex_state.sincronizar_com_arquivo()
            if novo_ot:
                print("[OpenTech] Novo painel detectado em disco — reprocessando usuários...")
            if novo_rx:
                print("[Ravex] Novo painel detectado em disco — reprocessando usuários...")
            if novo_ot or novo_rx:
                _reprocessar_todos_opentech()
        except Exception as e:
            print(f"[OpenTech/Ravex] Erro ao sincronizar painel: {e}")
        _opentech_stop.wait(_OPENTECH_SYNC_INTERVALO_SEG)

def iniciar_opentech_scheduler():
    global _opentech_thread
    if _opentech_thread is None or not _opentech_thread.is_alive():
        _opentech_stop.clear()
        _opentech_thread = threading.Thread(target=_opentech_loop, daemon=True)
        _opentech_thread.start()
        print("[OpenTech] Scheduler de sincronização (leitura do painel em disco) iniciado.")

if os.environ.get("DEMO_MODE", "").strip().lower() not in ("1", "true", "sim", "yes"):
    iniciar_opentech_scheduler()

# ── ESTADO EM MEMÓRIA ─────────────────────────────────────
_states = {}

def _novo_estado():
    return {
        "df_consolidado": pd.DataFrame(),
        "df_itens":       pd.DataFrame(),
        "df_controle":    pd.DataFrame(),
        "df_redespacho":  pd.DataFrame(),
        "atualizado":     None,
        "carregado":      False,
        "usou_opentech":  False,  # True se não houve upload manual de monitoramento
        "redespacho_path": None,  # caminho persistente do último redespacho importado
        "estoque_redespacho_path": None,  # caminho persistente do último Estoque Redespacho importado (FEAT-7)
        "ultimo_acesso":  0.0,    # time.time() do último /api/dados deste painel (MEL-15)
        "rastreio_sujo":  False,  # True = chegou painel OpenTech/Ravex novo e este estado ainda não foi reprocessado (MEL-15)
    }


# A sessão é guardada como DataFrame serializado, então depois de um deploy o
# painel volta com o df ANTIGO e todo campo novo aparece vazio — sem erro
# nenhum, que é o pior jeito de falhar. Aconteceu duas vezes seguidas: com o
# "Pedido Filial" (NFs sem o pedido ao lado) e com o "Status Estimativa" (card
# de redespacho sem a cor da previsão de chegada).
#
# A referência é a lista do PRÓPRIO parser, não uma cópia mantida à mão aqui —
# a segunda falha foi justamente por essa cópia ter ficado desatualizada.
# Assim, qualquer coluna nova que o parser passe a entregar dispara o
# reprocessamento sozinha.


def _restaurar_caminhos_redespacho(st: dict, usuario: str) -> None:
    """Repõe os caminhos dos arquivos de redespacho no estado restaurado.

    A sessão guarda os DataFrames, mas NÃO os caminhos — eles só existiam em
    memória, colocados no upload. Depois de todo restart (portanto de todo
    deploy) `redespacho_path` voltava vazio, e aí:

      * o ciclo da OpenTech pulava o redespacho, porque ele começa com
        `if red_path and Path(red_path).exists()` — lat/lon congelavam, e com
        eles o KM e a PREVISÃO DE CHEGADA, além de cidade e logo do rastreio;
      * `_obter_chaves_estoque_redespacho` devolvia conjunto vazio, então o
        filtro de "já recebido no CD" parava de descartar carreta da fila.

    Tudo isso em silêncio, sem erro nenhum. Achado em 04/09/2026, quando o
    usuário notou que a previsão de chegada não mudava entre atualizações.
    """
    base = _DATA_DIR
    for chave, pasta in (("redespacho_path", "redespacho_uploads"),
                         ("estoque_redespacho_path", "estoque_redespacho_uploads")):
        if st.get(chave):
            continue
        d = base / pasta
        caminho = _arquivo_mais_recente(d, f"{usuario}.*") if d.exists() else None
        if caminho:
            st[chave] = str(caminho)


def _rebuild_redespacho_se_antigo(st: dict, usuario: str) -> None:
    """Reprocessa a Fila Redespacho a partir do arquivo quando o DataFrame
    restaurado é de uma versão anterior — evita depender de o usuário
    reimportar o arquivo pra ver um campo novo."""
    df = st.get("df_redespacho")
    if df is None or getattr(df, "empty", True):
        return
    faltando = [c for c in COLUNAS_REDESPACHO if c not in df.columns]
    if not faltando:
        return
    red_dir = _DATA_DIR / "redespacho_uploads"
    caminho = _arquivo_mais_recente(red_dir, f"{usuario}.*") if red_dir.exists() else None
    if not caminho:
        return
    try:
        st["df_redespacho"] = carregar_redespacho(
            str(caminho), usuario=usuario,
            chaves_recebidas=_obter_chaves_estoque_redespacho(st))
        st["redespacho_path"] = str(caminho)
        print(f"[Redespacho] DataFrame de {usuario} era de versão anterior "
              f"(faltavam: {', '.join(faltando)}) — reprocessado do arquivo.")
    except Exception as e:
        print(f"[Redespacho] Não consegui reprocessar a fila de {usuario}: {e}")


def _obter_chaves_estoque_redespacho(st: dict) -> set:
    """Recarrega as chaves do Estoque Redespacho persistidas no estado do
    usuário (se houver upload desse relatório) — usadas para descartar da
    Fila Redespacho os itens já recebidos fisicamente (ver FEAT-7)."""
    path = st.get("estoque_redespacho_path")
    if not path or not Path(path).exists():
        return set()
    try:
        return carregar_chaves_estoque_redespacho(path)
    except Exception as e:
        print(f"[Redespacho] Erro ao carregar chaves do Estoque Redespacho: {e}")
        return set()

def _usuario_atual() -> str:
    return str(session.get("user","")).strip().lower()

def _usuario_efetivo():
    return _usuario_efetivo_compartilhado()

def _estado_usuario(usuario=None):
    u = usuario or str(session.get("user","")).strip().lower() or "_anon"
    if u not in _states:
        _states[u] = _novo_estado()
        # Restaura sessão salva
        try:
            sessao = carregar_sessao(u)
            if sessao:
                _states[u]["df_consolidado"] = sessao.get("df_consolidado", pd.DataFrame())
                _states[u]["df_itens"]       = sessao.get("df_itens", pd.DataFrame())
                _states[u]["df_redespacho"]  = sessao.get("df_redespacho", pd.DataFrame())
                _states[u]["df_controle"]    = sessao.get("df_controle", pd.DataFrame())
                _restaurar_caminhos_redespacho(_states[u], u)
                _rebuild_redespacho_se_antigo(_states[u], u)
                _states[u]["usou_opentech"]  = sessao.get("usou_opentech", False)
                _states[u]["atualizado"]     = sessao.get("atualizado")
            # Carrega horários de corte do CD do usuário
            try:
                _load_schedule(get_cd_usuario(u))
            except Exception:
                pass
        except Exception:
            pass
        _states[u]["carregado"] = True
    return _states[u]

_ultima_atualizacao = None

SAIDA_DIR = Path(__file__).parent.parent / "saida"

# ── AUTH ──────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return (jsonify({"error": "Não autenticado"}), 401) \
                   if request.is_json else redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def csrf_required(f):
    """Verifica token CSRF em POSTs que modificam dados."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            token_header = request.headers.get("X-CSRF-Token", "")
            token_session = session.get("csrf_token", "")
            if not token_session or not hmac.compare_digest(token_header, token_session):
                return jsonify({"error": "Requisição inválida (CSRF)."}), 403
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return (jsonify({"error": "Não autenticado"}), 401) \
                   if request.is_json else redirect(url_for("login_page"))
        if session.get("perfil") not in ("admin", "master"):
            return jsonify({"error": "Acesso restrito ao Admin"}), 403
        return f(*args, **kwargs)
    return decorated

def can_edit():
    """True se o perfil pode editar campos. "operador" mudou de sentido no
    redesenho de papéis (24/08/2026): antes era o nível padrão com escrita
    plena, agora é o nível restrito (só-leitura) — por isso saiu daqui;
    coordenador/analista entraram no lugar dele."""
    return session.get("perfil") in ("admin", "master", "coordenador", "analista")

def get_unidades_sessao():
    """Retorna lista de unidades permitidas para o usuário logado (vazia = todas).
    Gerente/Dev (admin/master) nunca são restritos por unidade, mesmo que o
    cadastro tenha unidades_permitidas preenchido (ex.: valor legado de antes
    do redesenho de papéis) — mesma regra de bypass que modulo_required já
    aplica pra módulos."""
    if session.get("perfil") in ("admin", "master"):
        return []
    ups = session.get("unidades_permitidas", "")
    return [u.strip() for u in ups.split(",") if u.strip()] if ups else []

@app.route("/login")
def login_page():
    return redirect("/") if session.get("user") else render_template("login.html")

# Rate limit de login — máx 10 tentativas por IP em janela de 5 minutos
_login_attempts: dict = {}  # {ip: [timestamps]}
_LOGIN_MAX = 10
_LOGIN_WINDOW = 300  # segundos

def _check_login_rate(ip: str) -> bool:
    """Retorna True se ainda dentro do limite, False se bloqueado."""
    import time as _t
    now = _t.time()
    attempts = [ts for ts in _login_attempts.get(ip, []) if now - ts < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) < _LOGIN_MAX

def _record_login_attempt(ip: str):
    import time as _t
    _login_attempts.setdefault(ip, []).append(_t.time())

def _clear_login_attempts(ip: str):
    _login_attempts.pop(ip, None)

def _contar_tentativas(ip: str) -> int:
    """Quantas tentativas (certas ou erradas) esse IP já fez dentro da
    janela — usado só pra decidir a partir de quando exigir captcha
    (pedido do usuário 29/08/2026), não confundir com o limite rígido de
    _check_login_rate."""
    import time as _t
    now = _t.time()
    return len([ts for ts in _login_attempts.get(ip, []) if now - ts < _LOGIN_WINDOW])

def _verificar_captcha(token: str) -> bool:
    """Confirma o token do Cloudflare Turnstile com a API deles — nunca
    confia em nada que venha só do navegador. Sem secret key configurada,
    trata como "captcha não exigido" (ver _login_precisa_captcha)."""
    secret = get_captcha_secret()
    if not secret:
        return True
    if not token:
        return False
    import requests as _requests
    try:
        resp = _requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": token},
            timeout=10,
        )
        return bool(resp.json().get("success"))
    except Exception:
        return False

def _login_precisa_captcha(ip: str) -> bool:
    cfg = get_captcha_config()
    if not cfg["has_secret"] or not cfg["site_key"]:
        return False
    return _contar_tentativas(ip) >= cfg["ativo_apos"]

def _arquivo_mais_recente(diretorio: Path, padrao: str):
    """
    Entre os arquivos que casam com `padrao` (glob) em `diretorio`, retorna o
    de maior mtime (modificado mais recentemente), ou None se não achar nenhum.

    Existe porque `Path.glob()` retorna os arquivos na ordem que o sistema de
    arquivos entrega — NÃO por data. Quando sobra mais de um arquivo pro
    mesmo tipo (ex.: "estoque.xls" de uma semana atrás + "estoque.xlsx" de
    hoje, upload antigo nunca removido), pegar "o primeiro que o glob() der"
    é roleta-russa: um restart pode carregar dados de dias/semanas atrás sem
    aviso nenhum. Ver BACKLOG — achado em 27/07/2026.
    """
    candidatos = list(diretorio.glob(padrao))
    if padrao.endswith(".*"):
        # "joao.*" também casa "joao.silva.xlsx", que é de outro usuário
        candidatos = [c for c in candidatos if c.stem == padrao[:-2]]
    if not candidatos:
        return None
    return max(candidatos, key=lambda p: p.stat().st_mtime)


# ── Fonte do Painel de Monitoramento (23/09/2026) ────────────────────
# Quando a OpenTech falhou, o usuário subiu o painel à mão. O arquivo ficava
# salvo em data/user_uploads/<usuario>/monitoramento.* e continuava sendo
# usado mesmo depois que a automação voltou — e sobrevivia a restart. Agora:
#   • interruptor por usuário (automático x painel offline);
#   • o painel offline vale 24 h e é apagado depois disso;
#   • subir um painel à mão desliga o automático (ação explícita);
#   • ligar o automático apaga o painel offline na hora.
PAINEL_OFFLINE_HORAS = 24


def _uploads_dir(usuario: str) -> Path:
    return _DATA_DIR / "user_uploads" / (usuario or "").strip().lower()


def _auto_monitoramento(usuario: str) -> bool:
    chave = f"monitoramento_auto:{(usuario or '').strip().lower()}"
    return str(get_preferencia(chave, "1")).strip().lower() not in ("0", "false", "nao", "não")


def _set_auto_monitoramento(usuario: str, ativo: bool) -> None:
    set_preferencia(f"monitoramento_auto:{(usuario or '').strip().lower()}", "1" if ativo else "0")


def _apagar_painel_offline(usuario: str) -> bool:
    apagou = False
    d = _uploads_dir(usuario)
    if d.exists():
        for antigo in d.glob("monitoramento.*"):
            antigo.unlink(missing_ok=True)
            apagou = True
    return apagou


def _painel_offline(usuario: str):
    """(caminho, enviado_em, horas_restantes) do painel manual ainda válido.
    Passou de PAINEL_OFFLINE_HORAS: apaga e devolve (None, None, 0)."""
    d = _uploads_dir(usuario)
    p = _arquivo_mais_recente(d, "monitoramento.*") if d.exists() else None
    if not p:
        return None, None, 0.0
    enviado = datetime.fromtimestamp(p.stat().st_mtime, _BRT)
    horas = (datetime.now(_BRT) - enviado).total_seconds() / 3600
    if horas >= PAINEL_OFFLINE_HORAS:
        _apagar_painel_offline(usuario)
        return None, None, 0.0
    return p, enviado, PAINEL_OFFLINE_HORAS - horas


def _fonte_monitoramento(usuario: str, p_upload=None):
    """(df_monitoramento, automatico, aviso) conforme o interruptor."""
    if p_upload:                       # upload manual: desliga o automático
        df_mon = carregar_painel_monitoramento(str(p_upload))   # arquivo recusado não muda o interruptor
        _set_auto_monitoramento(usuario, False)
        return df_mon, False, ""
    if _auto_monitoramento(usuario):
        # o painel importado fica guardado (válido por 24 h) para o usuário
        # poder voltar ao modo offline sem enviar o arquivo de novo
        return monitoramento_unificado.get_df_monitoramento_unificado(), True, ""
    p, _enviado, _resta = _painel_offline(usuario)
    if p:
        return carregar_painel_monitoramento(str(p)), False, ""
    # Sem painel importado o interruptor NÃO é religado: a escolha é do
    # usuário (23/09/2026). O painel automático só serve de base para a
    # consolidação; o ciclo automático continua parado, porque o modo é
    # manual (usou_opentech=False).
    return (monitoramento_unificado.get_df_monitoramento_unificado(), False,
            "Atualização automática desligada, porém nenhum Painel de Monitoramento "
            "foi importado — importe o painel para usar os dados dele.")


def _restaurar_estado_usuario(usuario: str):
    """
    Reprocessa os últimos arquivos importados pelo usuário após restart do servidor.
    Chamada automaticamente no login se o estado estiver vazio.
    """
    uploads_dir = _DATA_DIR / "user_uploads" / usuario
    if not uploads_dir.exists():
        return False

    # Encontra arquivos salvos (o mais recente de cada tipo, ver _arquivo_mais_recente)
    p_est  = _arquivo_mais_recente(uploads_dir, "estoque.*")
    p_ctrl = _arquivo_mais_recente(uploads_dir, "controle.*")

    if not p_est:
        return False

    try:
        df_itens = carregar_estoque_faturado(str(p_est))
        controle = carregar_controle_distribuicao(str(p_ctrl)) if p_ctrl else pd.DataFrame()
        # respeita o interruptor e a validade de 24 h do painel offline
        monit, usou_auto, _aviso = _fonte_monitoramento(usuario)
        _load_schedule(get_cd_usuario(usuario))
        df = consolidar(df_itens, controle, monit, usuario=usuario)

        # Marcações do card pela viagem (AE/RVX) e pelas NFs — fase 3, 19/10/2026.
        # Substitui o laço por (unidade, NF) + observação pela placa, que vazava
        # a observação de uma viagem para a seguinte da mesma carreta.
        df = carretas_viagem.com_viagem(df, "coligada")
        df = card_validacao.aplicar(df, usuario)
        df = aplicar_estimativas(df)
        df = carretas_viagem.aplicar_no_df(df, usuario, "coligada")

        st = _estado_usuario(usuario)
        st["df_consolidado"] = df
        st["df_itens"]       = df_itens
        st["df_controle"]    = controle
        st["usou_opentech"]  = usou_auto
        st["atualizado"]     = datetime.now(_BRT).isoformat(timespec="seconds")

        red_dir = _DATA_DIR / "redespacho_uploads"
        red_path = _arquivo_mais_recente(red_dir, f"{usuario}.*") if red_dir.exists() else None

        estred_dir = _DATA_DIR / "estoque_redespacho_uploads"
        estred_path = _arquivo_mais_recente(estred_dir, f"{usuario}.*") if estred_dir.exists() else None
        if estred_path:
            st["estoque_redespacho_path"] = str(estred_path)

        if red_path:
            try:
                chaves_recebidas = _obter_chaves_estoque_redespacho(st)
                st["df_redespacho"] = carregar_redespacho(str(red_path), usuario=usuario, chaves_recebidas=chaves_recebidas)
                st["redespacho_path"] = str(red_path)
            except Exception as e:
                print(f"[Restauração] Erro ao restaurar redespacho de {usuario}: {e}")

        # Persiste de novo no banco — assim o próximo restart usa esse
        # resultado fresco (via carregar_sessao, rápido) em vez de precisar
        # reprocessar os arquivos brutos outra vez.
        try:
            salvar_sessao(df, df_itens, st["df_redespacho"], usuario,
                          df_controle=controle, usou_opentech=usou_auto)
        except Exception as e:
            print(f"[Restauração] Aviso: não conseguiu persistir sessão de {usuario}: {e}")

        print(f"[Restauração] {usuario}: {len(df)} linhas restauradas")
        return True
    except Exception as e:
        print(f"[Restauração] Erro ao restaurar {usuario}: {e}")
        return False


@app.route("/api/login", methods=["POST"])
def api_login():
    ip = request.remote_addr or "unknown"
    if not _check_login_rate(ip):
        return jsonify({"ok": False, "error": "Muitas tentativas. Aguarde alguns minutos."}), 429
    d = request.json or {}
    u = d.get("user", "").strip().lower()

    # Captcha (Cloudflare Turnstile) a partir da N-ésima tentativa desse IP
    # (pedido do usuário 29/08/2026) — só entra em vigor se a chave secreta
    # estiver configurada em Configurações → Segurança; sem chave, esse
    # bloco é pulado e o rate limit de IP continua sendo a única defesa,
    # igual já era antes.
    if _login_precisa_captcha(ip) and not _verificar_captcha(d.get("captcha_token", "")):
        _record_login_attempt(ip)
        registrar_log(u, "login_falhou_captcha", ip=ip)
        cfg = get_captcha_config()
        return jsonify({"ok": False, "error": "Confirme que você não é um robô pra continuar.",
                        "show_captcha": True, "captcha_site_key": cfg["site_key"]}), 401

    dados = verificar_login(u, d.get("password", ""))
    if dados:
        _clear_login_attempts(ip)
        session.permanent = True
        session["user"]               = dados["username"]
        session["nome"]               = dados["nome_exibicao"]
        session["perfil"]             = dados["perfil"]
        session["unidades_permitidas"]= dados["unidades_permitidas"]
        session["unidades_vinculadas"]= dados["unidades_vinculadas"]
        session["modulos_permitidos"] = dados["modulos_permitidos"]
        session["operador_call_center"] = dados.get("operador_call_center", 0)
        session["csrf_token"]         = secrets.token_hex(32)
        # Restaura estado do usuário em background (não bloqueia o login)
        threading.Thread(
            target=_restaurar_estado_usuario,
            args=(dados["username"],),
            daemon=True
        ).start()
        registrar_log(dados["username"], "login", ip=ip)
        return jsonify({"ok": True, "user": dados["username"],
                        "perfil": dados["perfil"],
                        "inicio": rota_inicial(),
                        "csrf_token": session["csrf_token"]})
    _record_login_attempt(ip)
    registrar_log(u, "login_falhou", ip=ip)
    resp = {"ok": False, "error": "Usuário ou senha incorretos"}
    if _login_precisa_captcha(ip):
        resp["show_captcha"] = True
        resp["captcha_site_key"] = get_captcha_config()["site_key"]
    return jsonify(resp), 401

@app.route("/api/logout", methods=["POST"])
@csrf_required
def api_logout():
    eu    = str(session.get("user","")).strip().lower()
    alvo  = session.get("espelhando","")
    if eu and alvo:
        try: remover_espelho(eu, alvo)
        except: pass
    registrar_log(eu, "logout", ip=request.remote_addr or "")
    session.clear()
    return jsonify({"ok": True})

def _modulos_do_menu() -> list[str]:
    """Lista que o menu lateral usa para filtrar os módulos (vazia = todos).
    A Ferramenta de validação (temporária) não está em MODULOS: aparece só
    para Gerente/Dev e enquanto estiver ativa; para os demais a lista vai
    sempre explícita, senão o "vazio = todos" mostraria o item a eles."""
    if _DEMO:
        return list(_MODULOS_DEMO)
    if session.get("perfil") in ("admin", "master"):
        return [] if _validacao_ativa() else list(MODULOS)
    return modulos_da_sessao() or list(MODULOS)


@app.route("/api/me")
def api_me():
    u = session.get("user")
    if not u:
        return jsonify({"user": None}), 401
    # Se o servidor reiniciou mas a sessão ainda é válida, restaura os dados.
    # Usa _usuario_efetivo() (não "u" direto) pra cobrir também quem está
    # espelhando outro usuário — senão, se o painel do usuário espelhado
    # ainda não tiver sido carregado nessa instância do processo (ex.: ele
    # não abre o próprio painel há um tempo), o observador ficava travado
    # na foto estática salva no banco (ou até vazio) até o usuário
    # espelhado abrir o painel dele por conta própria e disparar a própria
    # restauração — só aí os dados apareciam pro observador (por isso o
    # sintoma "preciso atualizar a página" nunca resolvia de verdade: quem
    # resolvia era o outro usuário, em outro momento, sem relação com o F5).
    alvo = _usuario_efetivo()
    st_alvo = _estado_usuario(alvo)
    if st_alvo["df_consolidado"].empty:
        threading.Thread(target=_restaurar_estado_usuario, args=(alvo,), daemon=True).start()
    # Unidades vinculadas ao usuário (restrição) — Gerente/Dev (admin/master)
    # nunca são restritos, mesmo com o campo preenchido no cadastro (valor
    # legado); mesma regra de get_unidades_sessao(), usada na filtragem real.
    if session.get("perfil") in ("admin", "master"):
        permitidas = []
    else:
        ups = session.get("unidades_permitidas", "")
        permitidas = [x.strip().upper() for x in ups.split(",") if x.strip()] if ups else []
    # Padrão visual: quem tem unidades vinculadas vê as suas já pré-selecionadas
    # (as duas, quando houver — ex.: CDA+CDB no mesmo Local); quem não tem
    # restrição (Gerente/Dev) começa em "Todas", sem pré-selecionar um CD.
    unidades_default = permitidas[:2] if permitidas else []
    # Lê perfil direto do banco para garantir atualização
    with get_db() as db:
        row_u = db.execute("SELECT perfil, nome_exibicao FROM users WHERE username=?", (u,)).fetchone()
    perfil_atual = row_u["perfil"] if row_u else session.get("perfil", "operador")
    nome_atual   = (row_u["nome_exibicao"] if row_u else None) or session.get("nome", u) or u
    # Nome de exibição do CD (troca o "CD Demo" fixo nas telas) — vem da
    # conta canônica (alvo), não do login cru, pra Coordenador/Analista/
    # Qualidade/Operador verem o nome certo do CD deles, não um em branco.
    # Suporta 2 empresas no mesmo local (ex.: CDE/CDF) — pedido do usuário
    # 24/08/2026; mostra as duas juntas, sem seletor.
    _cd_cfg = get_cd_usuario(alvo)
    _cd_nome1 = str(_cd_cfg.get("nome") or "CDA").strip()
    _cd_nome2 = str(_cd_cfg.get("nome2") or "").strip()
    cd_nome_exibicao = f"{_cd_nome1} / {_cd_nome2}" if _cd_nome2 else _cd_nome1
    return jsonify({
        "cd_nome_exibicao": cd_nome_exibicao,
        "user": u,
        "nome": nome_atual,
        "perfil": perfil_atual,
        # "atualizado" precisa vir do estado do usuário efetivo, não do global
        # _ultima_atualizacao: o global é por processo e só é preenchido quando
        # ALGUÉM importa naquele worker — depois de restart (ou pro observador
        # de um espelho) ele fica None mesmo com dados restaurados do banco.
        # Como o front decide "carrega direto" vs "mostra banner de restauração
        # + buildCards([])" testando `me.tem_dados && me.atualizado`, o global
        # None fazia os cards sumirem em todo reload (F5, link do menu lateral)
        # até o polling de 3s/8s recuperar — o sintoma clássico do espelho.
        "atualizado": st_alvo["atualizado"] or _ultima_atualizacao,
        "tem_dados": not st_alvo["df_consolidado"].empty,
        "unidades_default": unidades_default,
        "unidades_permitidas": permitidas,
        # a tela precisa saber se este espelho grava, pra mostrar ou esconder
        # os controles de escrita (01/10/2026)
        "espelho_grava": bool(session.get("espelhando")) and pode_gravar_espelhando(),
        # CDs que essa sessão pode VER mas não é dona (cobertura temporária
        # liberada pelo admin) — alimenta o botão "Espelhar" pra quem não é
        # admin/master (ver /api/espelho/usuarios e /iniciar, redesenho de
        # controle de acesso 29/08/2026).
        "unidades_extras_espelho": unidades_extras_permitidas_sessao(),
        # Mesma regra de bypass do modulo_required: admin/master sempre veem
        # todos os módulos. Usa o perfil da sessão (não o fresco do banco)
        # para bater com o que o decorator realmente vai aplicar nas rotas.
        "modulos_permitidos": _modulos_do_menu(),
        "is_master": perfil_atual == "master",
        # mesma régua do servidor (pode_ver_localizacao_exata): Operador de
        # armazém não vê posição exata; operador de call center vê (15/11/2026)
        "pode_ver_localizacao": pode_ver_localizacao_exata(),
        "opentech": get_opentech_config(),
        "ravex": get_ravex_config(),
        "cards_por_linha": get_cards_por_linha(u),
        "observadores": get_observadores(u),
        "espelhando": session.get("espelhando", ""),
        "csrf_token": session.get("csrf_token", ""),
        "changelog_versao": CHANGELOG_VERSAO,
        "changelog_visto": get_changelog_visto(u),
    })

@app.route("/api/changelog/visto", methods=["POST"])
@login_required
@csrf_required
def api_changelog_visto():
    set_changelog_visto(session["user"], CHANGELOG_VERSAO)
    return jsonify({"ok": True})

@app.route("/api/changelog/historico")
@login_required
def api_changelog_historico():
    return jsonify({"entradas": CHANGELOG_ENTRADAS})

@app.route("/api/preferencia_ui", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_preferencia_ui():
    d = request.json or {}
    usuario = str(session.get("user","")).strip().lower()
    if "cards_por_linha" in d:
        set_cards_por_linha(usuario, int(d.get("cards_por_linha", 4)))
    return jsonify({"ok": True})

@app.route("/api/trocar_senha", methods=["POST"])
@login_required
@csrf_required
def api_trocar_senha():
    d = request.json or {}
    ok, msg = trocar_senha(
        session["user"],
        d.get("senha_atual", ""),
        d.get("nova_senha", "")
    )
    if ok:
        registrar_log(session["user"], "trocar_senha", alvo=session["user"])
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)

# ── SEO: tirar o site da indexação do Google ───────────────
# Sistema interno do CD, não deveria aparecer em busca nenhuma (pedido do
# usuário 24/08/2026). O header X-Robots-Tag: noindex é o que remove do
# índice de verdade — mas só funciona se o Googlebot conseguir CRAWLEAR a
# página e ver o header. Por isso o robots.txt aqui ainda permite tudo
# (Allow) por enquanto: um Disallow agora bloquearia o próprio recrawl que
# tira as páginas já indexadas do ar, e elas ficariam indexadas (só que
# "sem informação disponível") por tempo indeterminado. Depois de confirmar
# via "site:dominio" no Google que sumiu do índice (normalmente dias a
# poucas semanas), aí sim troca pra Disallow — nesse ponto vira só reforço,
# já que o noindex já fez o trabalho.
@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nAllow: /\n", mimetype="text/plain")


@app.after_request
def _no_index(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# ── PÁGINAS ───────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    # Operador de call center entra direto em Monitoramento de Entregas, mesmo
    # digitando "/" ou vindo de um favorito (15/11/2026).
    if session.get("operador_call_center"):
        return redirect(url_for("entregas.pagina"))
    # Quem não tem o módulo Monitoramento é levado à página inicial permitida
    # dele (antes o modulo_required mandava de volta para cá e fazia laço).
    if not tem_acesso_modulo("monitoramento"):
        destino = rota_inicial()
        return redirect(destino) if destino != "/" else redirect(url_for("login_page"))
    return render_template("index.html", user=session.get("user"))

@app.route("/configuracoes")
@login_required
def configuracoes_page():
    return render_template("configuracoes.html")

# ── PROCESSAR SÓ REDESPACHO (sem exigir Estoque Faturado) ──
@app.route("/api/processar_redespacho", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@bloquear_escrita
@rate_limited("processar_redespacho", max_chamadas=5, janela_seg=60)
@csrf_required
def api_processar_redespacho():
    try:
        f = request.files.get("redespacho")
        if not f or not f.filename:
            return jsonify({"error": "Arquivo de Redespacho é obrigatório."}), 400
        tmpdir = Path(tempfile.mkdtemp())
        p_red = tmpdir / f"redespacho{Path(f.filename).suffix}"
        f.save(p_red)
        usuario = str(session.get("user","")).strip().lower()
        red_persist_dir = _DATA_DIR / "redespacho_uploads"
        red_persist_dir.mkdir(parents=True, exist_ok=True)
        red_persist_path = red_persist_dir / f"{usuario}{Path(p_red).suffix}"

        st = _estado_usuario()

        # Estoque Redespacho (opcional, FEAT-7) — se enviado agora, persiste;
        # senão reaproveita o último importado (se houver)
        f_estred = request.files.get("estoque_redespacho")
        if f_estred and f_estred.filename:
            p_estred = tmpdir / f"estoque_redespacho{Path(f_estred.filename).suffix}"
            f_estred.save(p_estred)
            carregar_chaves_estoque_redespacho(str(p_estred))    # coluna faltando para aqui
            estred_persist_dir = _DATA_DIR / "estoque_redespacho_uploads"
            estred_persist_dir.mkdir(parents=True, exist_ok=True)
            estred_persist_path = estred_persist_dir / f"{usuario}{Path(p_estred).suffix}"
            shutil.copy(str(p_estred), str(estred_persist_path))
            st["estoque_redespacho_path"] = str(estred_persist_path)

        chaves_recebidas = _obter_chaves_estoque_redespacho(st)
        avisos_fila: list = []
        df_red = carregar_redespacho(str(p_red), usuario=usuario, chaves_recebidas=chaves_recebidas,
                                     avisos=avisos_fila)
        # a cópia que os ciclos releem só é trocada depois da leitura dar certo
        for antigo in red_persist_dir.glob(f"{usuario}.*"):
            if antigo.stem == usuario:          # "joao.*" também casa "joao.silva.xlsx"
                antigo.unlink(missing_ok=True)
        shutil.copy(str(p_red), str(red_persist_path))
        st["df_redespacho"]   = df_red
        st["redespacho_path"] = str(red_persist_path)
        shutil.rmtree(tmpdir, ignore_errors=True)
        # a sessão gravada é o que o aviso de chegada (cron) lê: sem isto a
        # carreta só de redespacho não era avisada até o próximo login
        try:
            salvar_sessao(st["df_consolidado"], st["df_itens"], df_red, usuario,
                          df_controle=st.get("df_controle"), usou_opentech=st.get("usou_opentech", False))
        except Exception as e:
            print(f"[Redespacho] Sessão não gravada: {e}")
        return jsonify({"ok": True, "total": len(df_red), "avisos": avisos_fila})
    except ColunasFaltando as exc:
        return jsonify(exc.para_tela()), 422
    except Exception as exc:
        print(f"[Redespacho] Erro: {exc}")
        return jsonify({"error": "Erro interno ao processar o redespacho."}), 500

# ── PROCESSAR ─────────────────────────────────────────────
@app.route("/api/processar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@bloquear_escrita
@rate_limited("processar", max_chamadas=5, janela_seg=60)
@csrf_required
def api_processar():
    try:
        tmpdir = Path(tempfile.mkdtemp())

        def save_tmp(key):
            f = request.files.get(key)
            if not f or not f.filename: return None
            p = tmpdir / f"{key}{Path(f.filename).suffix}"
            f.save(p); return p

        p_est    = save_tmp("estoque")
        p_ctrl   = save_tmp("controle")
        p_mon    = save_tmp("monitoramento")
        p_red    = save_tmp("redespacho")
        p_estred = save_tmp("estoque_redespacho")

        # Cópia persistente do redespacho (para reprocessar quando OpenTech
        # atualizar) — gravada só depois que tudo foi lido sem erro, mais abaixo
        if p_red:
            usuario_tmp = str(session.get("user","")).strip().lower()
            red_persist_dir = _DATA_DIR / "redespacho_uploads"
            red_persist_dir.mkdir(parents=True, exist_ok=True)
            red_persist_path = red_persist_dir / f"{usuario_tmp}{Path(p_red).suffix}"

        # Estoque Redespacho (opcional, FEAT-7) — cópia persistente + lembrado no
        # estado do usuário pra ser reaplicado nos próximos reprocessamentos
        if p_estred:
            carregar_chaves_estoque_redespacho(str(p_estred))    # coluna faltando para aqui
            usuario_tmp = str(session.get("user","")).strip().lower()
            estred_persist_dir = _DATA_DIR / "estoque_redespacho_uploads"
            estred_persist_dir.mkdir(parents=True, exist_ok=True)
            estred_persist_path = estred_persist_dir / f"{usuario_tmp}{Path(p_estred).suffix}"
            shutil.copy(str(p_estred), str(estred_persist_path))
            _estado_usuario()["estoque_redespacho_path"] = str(estred_persist_path)

        if not p_est:
            return jsonify({"error": "Arquivo Estoque Faturado é obrigatório."}), 400

        usuario  = str(session.get("user","")).strip().lower()
        avisos_colunas: list = []
        df_itens = carregar_estoque_faturado(str(p_est), avisos_colunas)
        controle = carregar_controle_distribuicao(str(p_ctrl)) if p_ctrl else pd.DataFrame()
        # Upload manual desliga o automático; sem upload, vale o interruptor
        # (automático = OpenTech + Ravex; offline = último painel enviado)
        monit, usou_auto, aviso_fonte = _fonte_monitoramento(usuario, p_mon)
        _load_schedule(get_cd_usuario(usuario))
        df       = consolidar(df_itens, controle, monit, usuario=usuario)

        # Marcações do card pela viagem (AE/RVX) e pelas NFs — fase 3, 19/10/2026.
        # Substitui o laço por (unidade, NF) + observação pela placa, que vazava
        # a observação de uma viagem para a seguinte da mesma carreta.
        df = carretas_viagem.com_viagem(df, "coligada")
        df = card_validacao.aplicar(df, usuario)
        df = aplicar_estimativas(df)
        df = carretas_viagem.aplicar_no_df(df, usuario, "coligada")

        chaves_recebidas = _obter_chaves_estoque_redespacho(_estado_usuario())
        if p_red:
            df_red = carregar_redespacho(str(p_red), usuario=session.get('user',''), chaves_recebidas=chaves_recebidas,
                                         avisos=avisos_colunas)
        else:
            # sem a fila neste upload, segue a última importada (o ciclo do
            # rastreio já a relia do arquivo; a sessão gravada ficava vazia)
            st_ant = _estado_usuario()
            red_ant = st_ant.get("redespacho_path")
            df_red = st_ant.get("df_redespacho", pd.DataFrame())
            if red_ant and Path(red_ant).exists():
                try:
                    df_red = carregar_redespacho(red_ant, usuario=session.get('user',''),
                                                 chaves_recebidas=chaves_recebidas)
                except Exception as e:
                    print(f"[Processar] Redespacho anterior não relido: {e}")

        # Persiste cópias dos arquivos por usuário para restaurar após restart.
        # Antes de copiar, remove qualquer versão antiga do mesmo tipo (mesmo
        # com extensão diferente) — senão um upload em .xlsx depois de um em
        # .xls (ou vice-versa) deixa os dois arquivos na pasta, e uma futura
        # restauração pode escolher o errado (achado em 27/07/2026).
        if p_red:
            for antigo in red_persist_dir.glob(f"{usuario_tmp}.*"):
                if antigo.stem == usuario_tmp:  # "joao.*" também casa "joao.silva.xlsx"
                    antigo.unlink(missing_ok=True)
            shutil.copy(str(p_red), str(red_persist_path))
        uploads_dir = _DATA_DIR / "user_uploads" / usuario
        uploads_dir.mkdir(parents=True, exist_ok=True)
        if p_est:
            for antigo in uploads_dir.glob("estoque.*"):
                antigo.unlink(missing_ok=True)
            shutil.copy(str(p_est), str(uploads_dir / f"estoque{Path(str(p_est)).suffix}"))
        if p_ctrl:
            for antigo in uploads_dir.glob("controle.*"):
                antigo.unlink(missing_ok=True)
            shutil.copy(str(p_ctrl), str(uploads_dir / f"controle{Path(str(p_ctrl)).suffix}"))
        if p_mon:
            for antigo in uploads_dir.glob("monitoramento.*"):
                antigo.unlink(missing_ok=True)
            shutil.copy(str(p_mon), str(uploads_dir / f"monitoramento{Path(str(p_mon)).suffix}"))

        st = _estado_usuario()
        st["df_consolidado"] = df
        st["df_itens"]       = df_itens
        st["df_controle"]    = controle
        st["df_redespacho"]  = df_red
        st["redespacho_path"] = str(red_persist_path) if p_red else st.get("redespacho_path")
        st["usou_opentech"]  = usou_auto
        st["atualizado"]     = datetime.now(_BRT).isoformat(timespec="seconds")

        # Persiste no banco para sobreviver a reinicializações
        global _ultima_atualizacao
        # Limpa seleções de redespacho antigas ao importar novo relatório
        if p_red:
            try:
                with get_db() as db:
                    db.execute("DELETE FROM redespacho_sel WHERE usuario=?", (session.get("user",""),))
            except Exception:
                pass
        salvar_sessao(df, df_itens, df_red, usuario, df_controle=controle, usou_opentech=usou_auto)
        _ultima_atualizacao = datetime.now(_BRT).isoformat(timespec="seconds")

        unidades = sorted({u.strip() for x in df["Empresa Compra"].dropna()
                           for u in str(x).split("·") if u.strip()})
        if controle is not None and hasattr(controle, "attrs"):
            # o Controle já juntava avisos de coluna não achada, mas ninguém os mostrava
            avisos_colunas += list(controle.attrs.get("avisos", []))
        return jsonify({
            "ok": True, "total_nfs": len(df),
            "total_itens": len(df_itens), "unidades": unidades,
            "atualizado": _ultima_atualizacao,
            "avisos": avisos_colunas,
        })
    except ColunasFaltando as exc:
        return jsonify(exc.para_tela()), 422
    except Exception as exc:
        print(f"[Processar] Erro: {exc}")
        return jsonify({"error": "Erro interno ao processar. Tente novamente."}), 500

# ── REPROCESSAMENTO COM PAINEL OPENTECH ATUALIZADO ────────

def _log_diagnostico_recalculo(usuario: str, antes_por_placa: dict, df_novo) -> None:
    """
    Diagnóstico temporário para BUG-2 (estimativa de chegada não recalcula
    quando a OpenTech atualiza KM). Compara Latitude/Longitude/KM Restante/
    Estimativa Chegada antes x depois do reprocessamento e loga os casos em
    que o KM mudou mas a estimativa exibida não acompanhou.
    """
    if not antes_por_placa or df_novo is None or df_novo.empty:
        return
    total = lat_mudou = km_mudou = est_nao_acompanhou = 0
    for _, r in df_novo.iterrows():
        placa = str(r.get("Placa", "")).strip().upper()
        prev = antes_por_placa.get(placa)
        if not prev:
            continue
        total += 1
        lat_novo, lon_novo = r.get("Latitude", ""), r.get("Longitude", "")
        km_novo = r.get("KM Restante", "")
        est_novo = r.get("Estimativa Chegada", "")
        if (str(lat_novo) != str(prev["lat"])) or (str(lon_novo) != str(prev["lon"])):
            lat_mudou += 1
        km_diferente = str(km_novo) != str(prev["km"])
        if km_diferente:
            km_mudou += 1
            if str(est_novo) == str(prev["est"]):
                est_nao_acompanhou += 1
                print(f"[DIAG BUG-2] {usuario} placa={placa} KM {prev['km']!r} -> {km_novo!r} "
                      f"mas Estimativa Chegada não mudou: {est_novo!r} "
                      f"(lat {prev['lat']!r} -> {lat_novo!r}, lon {prev['lon']!r} -> {lon_novo!r})")
    if total:
        print(f"[DIAG BUG-2] {usuario}: {total} placas comparadas | "
              f"{lat_mudou} com lat/lon alterada | {km_mudou} com KM Restante alterado | "
              f"{est_nao_acompanhou} com KM alterado mas estimativa não acompanhou")


def _reprocessar_estado_opentech(usuario: str) -> bool:
    """
    Reprocessa o df_consolidado de um usuário usando o df_itens já
    armazenado + o painel global mais recente (OpenTech + Ravex unificados).
    Só reprocessa se o usuário não tiver feito upload manual do
    Painel de Monitoramento (usou_opentech=True).
    Retorna True se reprocessou.
    """
    st = _states.get(usuario)
    if not st or st["df_itens"].empty or not st.get("usou_opentech"):
        return False
    itens_lidos = st["df_itens"]   # ver o "is" no fim: upload no meio vence
    try:
        monit = monitoramento_unificado.get_df_monitoramento_unificado()

        df_antes = st.get("df_consolidado")
        antes_por_placa = {}
        if df_antes is not None and not df_antes.empty:
            for _, r in df_antes.iterrows():
                antes_por_placa[str(r.get("Placa", "")).strip().upper()] = {
                    "lat":    r.get("Latitude", ""),
                    "lon":    r.get("Longitude", ""),
                    "km":     r.get("KM Restante", ""),
                    "est":    r.get("Estimativa Chegada", ""),
                    "status": r.get("Status Estimativa", ""),
                }

        df = consolidar(st["df_itens"], st.get("df_controle", pd.DataFrame()), monit, usuario=usuario)
        # Marcações do card pela viagem (AE/RVX) e pelas NFs — fase 3, 19/10/2026.
        # Substitui o laço por (unidade, NF) + observação pela placa, que vazava
        # a observação de uma viagem para a seguinte da mesma carreta.
        df = carretas_viagem.com_viagem(df, "coligada")
        df = card_validacao.aplicar(df, usuario)
        df = aplicar_estimativas(df)
        df = carretas_viagem.aplicar_no_df(df, usuario, "coligada")
        if st["df_itens"] is not itens_lidos:
            # um upload trocou os arquivos enquanto este reprocessamento (ex.:
            # disparado pelo bot, numa thread) rodava — o resultado é velho
            return False
        st["df_consolidado"] = df
        st["atualizado"] = datetime.now(_BRT).isoformat(timespec="seconds")
        st["rastreio_sujo"] = False

        _log_diagnostico_recalculo(usuario, antes_por_placa, df)

        # Reprocessa redespacho com lat/lon atualizados do painel OpenTech (se houver)
        red_path = st.get("redespacho_path")
        if red_path and Path(red_path).exists():
            try:
                chaves_recebidas = _obter_chaves_estoque_redespacho(st)
                st["df_redespacho"] = carregar_redespacho(red_path, usuario=usuario, chaves_recebidas=chaves_recebidas)
            except Exception as e:
                print(f"[OpenTech] Erro ao reprocessar redespacho de {usuario}: {e}")

        return True
    except Exception as e:
        print(f"[OpenTech] Erro ao reprocessar usuário {usuario}: {e}")
        return False


# Janela que define "usuário ativo": houve /api/dados dele (ou de um observador
# espelhando ele) há menos de X segundos. Fora disso o reprocessamento fica
# adiado até a pessoa voltar a abrir o painel (feito sob demanda em /api/dados).
_RASTREIO_ATIVO_JANELA_SEG = 15 * 60

# Bot de carretas: cards de um local, da memória do worker. Painel carregado
# do banco (ninguém abriu desde o restart) ou rastreio mais novo que o card:
# reprocessa antes de responder, como o /api/dados faz.
_BOT_POSICAO_MAX_SEG = 25 * 60


def _cards_do_local_bot(local: str):
    from app.services.carretas_bot import conta_do_local
    conta = conta_do_local(local)
    if not conta:
        return None
    st = _estado_usuario(conta)
    if st["df_consolidado"].empty and st["df_redespacho"].empty:
        return None
    def _quando():
        """atualizado do estado: ISO com fuso (reprocessamento) ou sem fuso,
        no relógio do servidor (sessão gravada — UTC na VM)."""
        try:
            q = datetime.fromisoformat(str(st.get("atualizado") or ""))
        except ValueError:
            return None
        return (q.astimezone() if q.tzinfo is None else q).astimezone(_BRT)

    q = _quando()
    velho = q is None or (datetime.now(_BRT) - q).total_seconds() > _BOT_POSICAO_MAX_SEG
    if st.get("usou_opentech") and (st.get("rastreio_sujo") or velho):
        _reprocessar_estado_opentech(conta)
    q = _quando()
    atualizado = q.strftime("%d/%m %H:%M") if q else ""
    return {"col": st["df_consolidado"], "red": st["df_redespacho"], "itens": st["df_itens"],
            "atualizado": atualizado}


registrar_fonte_cards(_cards_do_local_bot)


def _reprocessar_todos_opentech():
    """
    Chegou painel de rastreio novo (OpenTech e/ou Ravex). Antes esta função
    reprocessava TODOS os usuários carregados, em sequência, dentro do único
    worker Gunicorn — na e2-micro (0.25 vCPU) isso monopolizava a CPU por
    vários segundos e o site inteiro ficava lento pra todo mundo (MEL-15).

    Agora: marca todos como "sujos" (barato) e reprocessa de imediato só quem
    está de fato usando o painel agora, com uma pausa entre um usuário e outro
    pra ceder a vez às requisições web. Quem estiver inativo é reprocessado
    sob demanda no próximo /api/dados dele (ver api_dados).
    """
    agora = time.time()
    ativos = []
    for usuario, st in list(_states.items()):
        if st["df_itens"].empty or not st.get("usou_opentech"):
            continue  # upload manual de monitoramento — ciclo automático não se aplica
        st["rastreio_sujo"] = True
        if agora - st.get("ultimo_acesso", 0.0) <= _RASTREIO_ATIVO_JANELA_SEG:
            ativos.append(usuario)
    for i, usuario in enumerate(ativos):
        if i > 0:
            time.sleep(1.0)  # respiro entre usuários — deixa o worker atender requisições
        _reprocessar_estado_opentech(usuario)


# ── DADOS ─────────────────────────────────────────────────
# COLIGADA e REDESPACHO geram cards SEPARADOS mesmo quando é a mesma carreta:
# são notas e instruções de entrada diferentes. Verificado nos arquivos reais
# (04/09/2026): DEM2B34 e DEM3C45 estão nos dois, mas com ZERO NFs em comum e
# instruções distintas (redespacho 25048/25064/25110 x coligada 24469..25067).
# A etiqueta lateral só diz de que tipo é AQUELE card — não há card misto, e
# por isso nada aqui precisa cruzar as duas fontes.


@app.route("/api/dados")
@login_required
@modulo_required("monitoramento")
def api_dados():
    alvo = _usuario_efetivo()
    st = _estado_usuario(alvo)
    # MEL-15: registra atividade (também vale quando um observador espelha
    # este painel — mantém o alvo na lista de reprocessamento imediato) e,
    # se um painel de rastreio novo chegou enquanto este usuário estava
    # inativo, reprocessa agora, só ele, antes de responder.
    st["ultimo_acesso"] = time.time()
    if st.get("rastreio_sujo"):
        _reprocessar_estado_opentech(alvo)
    df = st["df_consolidado"]
    if df.empty:
        return jsonify({"rows": [], "unidades": [], "empresas": []})

    unidade_param = request.args.get("unidade", "")
    unidades_sel  = [u.strip() for u in unidade_param.split(",") if u.strip()]
    # Intersecta com unidades permitidas pelo perfil
    permitidas = get_unidades_sessao()
    if permitidas:
        unidades_sel = [u for u in unidades_sel if u in permitidas] if unidades_sel else permitidas

    # "Empresa Compra" pode trazer VÁRIAS unidades ("CDA · CDB") desde que o
    # card virou a carreta física. Por isso o filtro compara as partes, não a
    # string inteira — com `isin` um card CDA·CDB sumia ao filtrar por CDA.
    def _unidades_de(v) -> list[str]:
        return [u.strip() for u in str(v or "").split("·") if u.strip()]

    filtered = df.copy()
    if unidades_sel:
        alvo = set(unidades_sel)
        filtered = filtered[filtered["Empresa Compra"].map(
            lambda v: bool(alvo & set(_unidades_de(v))))]

    filtered = filtered.sort_values(
        ["Empresa Compra","Empresa","Placa","NF"], kind="stable"
    ).reset_index(drop=True)

    def clean(v):
        if isinstance(v, float) and pd.isna(v): return ""
        return str(v) if not isinstance(v, (int, float)) else v

    rows    = [{c: clean(r[c]) for c in filtered.columns} for _, r in filtered.iterrows()]
    if not pode_ver_localizacao_exata():
        for row in rows:
            row["Latitude"] = ""
            row["Longitude"] = ""
    # lista as unidades INDIVIDUAIS, senão "CDA · CDB" apareceria no filtro
    # como se fosse uma terceira unidade
    unidades = sorted({u for x in df["Empresa Compra"].dropna() for u in _unidades_de(x)})
    empresas = sorted(set(str(x).strip() for x in filtered["Empresa"].dropna()   if str(x).strip()))

    return jsonify({"rows": rows, "unidades": unidades, "empresas": empresas})

# ── REDESPACHO ────────────────────────────────────────────
@app.route("/api/redespacho")
@login_required
@modulo_required("monitoramento")
def api_redespacho():
    st = _estado_usuario(_usuario_efetivo())
    df = st["df_redespacho"]
    if df.empty:
        return jsonify({"grupos": []})

    # Linha COM chave mas SEM instrução de entrada e SEM placa: a fábrica
    # expediu e falta faturar a instrução "virtual" (regra do usuário,
    # 04/09/2026). Nos arquivos reais são 39 linhas, e antes elas eram
    # DESCARTADAS aqui pelo `if not placa: continue` — sumiam da tela. Agora
    # viram card agrupado pela INSTRUÇÃO ORIGEM — a que a planta usa para
    # carregar, então todas as NFs com a mesma origem são o redespacho de UM
    # veículo. Nos arquivos reais as 39 viram 2 cards (25090331 e 25124282).
    _AGUARDA = "AGUARDANDO:"
    df = df.copy()
    df["_Card"] = [
        str(r["Placa"]).strip() or (_AGUARDA + str(r.get("Instrução Origem", "")).strip())
        for _, r in df.iterrows()
    ]

    grupos = []
    for chave_card, grp in df.groupby("_Card", sort=False, dropna=False):
        chave_card = str(chave_card).strip()
        if not chave_card: continue
        aguardando = chave_card.startswith(_AGUARDA)
        placa = "" if aguardando else chave_card

        def first(col):
            if col not in grp.columns: return ""
            for v in grp[col]:
                s = str(v).strip()
                if s and s.lower() not in ("nan","none"): return s
            return ""

        fcol = next((c for c in ["Formulário","Formulario","NF"] if c in grp.columns), None)
        nfs  = [str(v).strip() for v in grp[fcol].dropna()
                if str(v).strip() and str(v).lower() not in ("nan","none")] if fcol else []

        # NF -> pedido FILIAL, no mesmo formato do card de coligada
        pcol = "Pedido Filial" if "Pedido Filial" in grp.columns else None
        nf_ped = []
        if fcol:
            for _, r in grp.iterrows():
                n_ = str(r[fcol]).strip()
                if not n_ or n_.lower() in ("nan", "none"):
                    continue
                p_ = str(r[pcol]).strip() if pcol else ""
                nf_ped.append(f"{n_}:{'' if p_.lower() in ('nan','none') else p_}")

        grupos.append({
            "Placa":               placa,
            "Aguardando":          "1" if aguardando else "",
            "NF Pedidos":          "|".join(dict.fromkeys(nf_ped)),
            "Feriado":             first("Feriado"),
            "Descarga Concluída":  first("Descarga Concluída"),
            "Descarga Iniciada":   first("Descarga Iniciada"),
            "Cidade Atual":        first("Cidade Atual"),
            "Fonte Rastreio":      first("Fonte Rastreio"),
            "Unidade":             first("Unidade"),
            "Status Estimativa":   first("Status Estimativa"),
            "Observação":          first("Observação"),
            "Card ID":             chave_card,
            "Instrução Entrada":   first("Instrução Entrada"),
            "KM Restante":         first("KM Restante"),
            "Tempo Estimado":      first("Tempo Estimado"),
            "Estimativa Chegada":  first("Estimativa Chegada"),
            "Latitude":            first("Latitude") if pode_ver_localizacao_exata() else "",
            "Longitude":           first("Longitude") if pode_ver_localizacao_exata() else "",
            "Empresa Expedidora":  first("Empresa Expedidora"),
            "Qtd NFs":             str(len(nfs)),
            "NFs":                 " / ".join(nfs),
            "Viagem":              first("Viagem"),
            "Presença":            first("Presença"),
            "No CD Desde":         first("No CD Desde"),
            "Aguardando Min":      first("Aguardando Min"),
            "Chegada CD":          first("Chegada CD"),
            "Saída CD":            first("Saída CD"),
            "Idas CD":             first("Idas CD"),
            "Fora CD":             first("Fora CD"),
            "Sem Sinal CD":        first("Sem Sinal CD"),
        })

    return jsonify({"grupos": grupos})

@app.route("/api/redespacho/selecao")
@login_required
@modulo_required("monitoramento")
def api_red_selecao():
    return jsonify({"selecionados": sorted(get_redespacho_selecionados(_usuario_efetivo()))})

@app.route("/api/redespacho/toggle", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@bloquear_escrita
@csrf_required
def api_red_toggle():
    d = request.json or {}
    placa = str(d.get("placa","")).strip()
    if not placa:
        return jsonify({"error": "placa obrigatória"}), 400
    novo = toggle_redespacho(placa, str(session.get("user","")).strip().lower())
    return jsonify({"ok": True, "selecionado": novo})

# ── EDITAR CAMPO ──────────────────────────────────────────
@app.route("/api/editar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_editar():
    # O operador marca o andamento da descarga (pedido do usuário, 19/10/2026);
    # o resto do card segue com coordenador/analista/gerente.
    CAMPOS_DO_OPERADOR = ("Descarga Iniciada", "Descarga Concluída")
    campo_pedido = (request.json or {}).get("campo", "")
    if not can_edit() and not (session.get("perfil") == "operador" and campo_pedido in CAMPOS_DO_OPERADOR):
        return jsonify({"error": "Sem permissão para editar"}), 403
    if session.get("espelhando") and not pode_gravar_espelhando():
        # Antes isto valia para todo mundo, porque o branch de Entregas
        # Frente gravava o recálculo no estado do OBSERVADOR em vez do ALVO
        # (BUG-9c). Esse defeito foi corrigido na origem — a rota resolve o
        # alvo por _usuario_efetivo() na leitura (:1373) e na regravação
        # (:1445) —, então desde 01/10/2026 Gerente/Desenvolvedor edita o CD
        # que está espelhando. Os demais perfis seguem em leitura.
        return jsonify({"error": "Modo espelho é somente leitura para o seu perfil."}), 403
    # can_edit() já filtra qualidade/operador (não estão na lista de perfis
    # que editam) — mantido aqui só o bloqueio de espelho manual, que é uma
    # situação à parte (admin/master observando alguém).
    d     = request.json or {}
    placa = str(d.get("placa","")).strip()
    nf    = str(d.get("nf","")).strip()
    campo = d.get("campo","")
    valor = d.get("valor","")

    st_edit = _estado_usuario(_usuario_efetivo())
    df = st_edit["df_consolidado"]

    # O card pode ser de REDESPACHO, que vive noutro DataFrame. Sem este
    # desvio a marcação virava um no-op silencioso: o campo existe no
    # consolidado, então nada dava erro, mas a máscara por placa não casava
    # nada e o clique simplesmente não fazia efeito.
    #
    # O TIPO vem do front, não é deduzido da placa: a mesma carreta pode estar
    # nos dois datasets (DEM2B34 e DEM3C45 estão, nos arquivos reais) e casar
    # só por placa fazia a edição do card de COLIGADA ser desviada pro
    # redespacho, deixando o consolidado intocado.
    df_red = st_edit.get("df_redespacho")
    if (str(d.get("tipo", "")).strip().lower() == "redespacho"
            and campo in ("Descarga Concluída", "Descarga Iniciada", "Observação")
            and df_red is not None and not df_red.empty):
        alvo = str(placa).strip()
        chave = alvo[len("AGUARDANDO:"):] if alvo.startswith("AGUARDANDO:") else ""
        col_placa = df_red["Placa"].astype(str).str.strip()
        m_red = (col_placa.eq("") & df_red.get("Instrução Origem", "").astype(str).str.strip().eq(chave)
                 if chave else col_placa.eq(alvo))
        if m_red.any():
            if campo not in df_red.columns:
                df_red[campo] = ""
            df_red.loc[m_red, campo] = valor
            if campo in ("Descarga Concluída", "Descarga Iniciada"):
                kw = ({"descarga_concluida": valor} if campo == "Descarga Concluída"
                      else {"descarga_iniciada": valor})
                for nf_red in df_red.loc[m_red, "Formulário"].astype(str):
                    if nf_red.strip() and nf_red.strip().lower() != "nan":
                        set_validacao(unidade="REDESPACHO", nf=nf_red.strip(),
                                      usuario=_usuario_efetivo(), **kw)
            return jsonify({"ok": True, "redespacho": True})

    if not df.empty and campo == "Descarga Iniciada" and campo not in df.columns:
        df["Descarga Iniciada"] = "Não"          # sessão de antes do campo existir
    if df.empty or campo not in df.columns:
        return jsonify({"error": "Sem dados ou campo inválido"}), 400

    POR_CARD = {"Regra Data CD","Entregas Frente","Entregas Frente Obs","Descarga Concluída","Descarga Iniciada",
                "Observação","Instrução Entrada"}
    # O card é identificado pelo Card ID (placa|local, ou SEM-PLACA:…) desde a
    # fase 3 (19/10/2026): pela placa, os cards CDA·CDB e CNT da mesma carreta
    # recebiam a mesma edição. A placa fica para tela antiga em cache.
    card_id = str(d.get("card_id", "") or "").strip()
    if campo in POR_CARD and card_id and "Card ID" in df.columns:
        mask = df["Card ID"].astype(str).str.strip().eq(card_id)
    elif campo in POR_CARD and placa:
        mask = df["Placa"].astype(str).str.strip().eq(placa)
    else:
        mask = df["NF"].astype(str).str.strip().eq(nf)
    if not mask.any():
        return jsonify({"error": "Card não encontrado. Atualize o painel."}), 404

    df.loc[mask, campo] = valor
    if campo in card_validacao.CAMPOS:
        for _, row in df[mask].iterrows():
            card_validacao.gravar(_usuario_efetivo(), row, campo, valor)

    # Persiste no banco (sempre no sandbox do usuário efetivo — o mesmo cujo
    # df acabou de ser editado acima, inclusive durante espelhamento)
    usuario_efetivo = _usuario_efetivo()

    if campo == "Regra Data CD":
        for _, row in df[mask].iterrows():
            unidade = str(row.get("Empresa Compra","")).strip()
            for nf_item in str(row.get("NF","")).split("/"):
                nf_item = nf_item.strip()
                if nf_item: set_validacao(unidade=unidade, nf=nf_item, usuario=usuario_efetivo, regra_cd=valor)

    if campo == "Observação" and placa:
        for _, row in df[mask].iterrows():
            for nf_item in str(row.get("NF","")).split("/"):
                nf_item = nf_item.strip()
                if nf_item: set_observacao(placa, nf_item, valor, usuario=usuario_efetivo)

    if campo == "Entregas Frente":
        for _, row in df[mask].iterrows():
            unidade = str(row.get("Empresa Compra","")).strip()
            for nf_item in str(row.get("NF","")).split("/"):
                nf_item = nf_item.strip()
                if nf_item: set_validacao(unidade=unidade, nf=nf_item, usuario=usuario_efetivo, entregas_frente=valor)
        # BUG-9c: estava sem usuario_efetivo, então durante espelho gravava
        # o recálculo no estado do OBSERVADOR em vez do ALVO — o alvo nunca
        # via a Estimativa Chegada/Status recalculados após alternar
        # Entregas Frente, e o painel pessoal do observador era sobrescrito
        # pelos dados do alvo por engano. Bloqueado na entrada da rota
        # (espelho é somente leitura), mas corrige de qualquer forma o
        # usuário direto (master editando a própria conta, sem espelhar,
        # onde usuario_efetivo == usuário logado — mesmo resultado, só mais
        # explícito e sem depender do default do parâmetro).
        st2 = _estado_usuario(usuario_efetivo)
        st2["df_consolidado"] = carretas_viagem.aplicar_no_df(aplicar_estimativas(df),
                                                              usuario_efetivo, "coligada")

    if campo == "Entregas Frente Obs":
        for _, row in df[mask].iterrows():
            unidade = str(row.get("Empresa Compra","")).strip()
            for nf_item in str(row.get("NF","")).split("/"):
                nf_item = nf_item.strip()
                if nf_item: set_validacao(unidade=unidade, nf=nf_item, usuario=usuario_efetivo, entregas_frente_obs=valor)

    if campo in ("Descarga Concluída", "Descarga Iniciada"):
        kw = {"descarga_concluida": valor} if campo == "Descarga Concluída" else {"descarga_iniciada": valor}
        for _, row in df[mask].iterrows():
            unidade = str(row.get("Empresa Compra","")).strip()
            for nf_item in str(row.get("NF","")).split("/"):
                nf_item = nf_item.strip()
                if nf_item: set_validacao(unidade=unidade, nf=nf_item, usuario=usuario_efetivo, **kw)

    registrar_log(session.get("user",""), "editar_campo", alvo=f"{campo}:{card_id or placa or nf}",
                  detalhe=f"valor={valor!r} usuario_efetivo={usuario_efetivo}")
    return jsonify({"ok": True})

# ── ITENS NF ─────────────────────────────────────────────
@app.route("/api/itens_nf")
@login_required
@modulo_required("monitoramento")
def api_itens_nf():
    nf = request.args.get("nf","")
    df = _estado_usuario(_usuario_efetivo())["df_itens"]
    if df.empty or not nf:
        return jsonify({"rows": []})
    itens = df[df["NF"].astype(str) == nf]
    cols  = [c for c in ["Cód Produto","Produto","Linha","Temperatura","Unidade",
                          "Quantidade","Volume","Pedido Compra","Local Estoque"]
             if c in itens.columns]
    rows  = itens[cols].fillna("").astype(str).to_dict(orient="records")
    return jsonify({"rows": rows, "nf": nf})

# ── ROTA MAPS ─────────────────────────────────────────────
@app.route("/api/monitoramento/fonte")
@login_required
def api_monitoramento_fonte():
    """Estado do interruptor + validade do painel offline."""
    usuario = str(session.get("user", "")).strip().lower()
    p, enviado, resta = _painel_offline(usuario)
    return jsonify({
        "automatico": _auto_monitoramento(usuario),
        "validade_horas": PAINEL_OFFLINE_HORAS,
        "offline": {
            "existe": bool(p),
            "arquivo": p.name if p else "",
            "enviado_em": enviado.isoformat(timespec="minutes") if enviado else "",
            "horas_restantes": round(resta, 1) if p else 0,
        },
    })


@app.route("/api/monitoramento/fonte", methods=["POST"])
@login_required
@csrf_required
def api_monitoramento_fonte_salvar():
    usuario = str(session.get("user", "")).strip().lower()
    automatico = bool((request.json or {}).get("automatico"))
    p, _enviado, _resta = _painel_offline(usuario)
    # Sem recusa: o interruptor é a escolha do usuário. Sem painel importado,
    # apenas avisa — o que ele desliga é a atualização automática.
    aviso = "" if (automatico or p) else ("Atualização automática desligada. Importe o Painel "
                                          "de Monitoramento para o painel usar os dados dele.")
    _set_auto_monitoramento(usuario, automatico)
    st = _states.get(usuario)
    if st and not st["df_itens"].empty:
        st["usou_opentech"] = automatico
        if automatico:
            _reprocessar_estado_opentech(usuario)      # volta ao painel OpenTech + Ravex
        elif p:
            _restaurar_estado_usuario(usuario)         # reconsolida com o painel importado
    registrar_log(usuario, "monitoramento_fonte",
                  detalhe="automática" if automatico else "painel importado")
    return jsonify({"ok": True, "automatico": automatico, "aviso": aviso})


# ── COORDENADAS DOS CDs (referência de destino no mapa) ───
@app.route("/api/cds/coordenadas")
@login_required
@modulo_required("monitoramento")
def api_cds_coordenadas():
    """Onde ficam os CDs, pro mapa do Abastecimento desenhar o destino das
    carretas. Não é posição de veículo — é o endereço do próprio CD, que o
    master já cadastra em /api/master/cds. Quem tem unidades vinculadas vê
    as suas; gerente/dev veem todas (mesma regra de /api/me)."""
    # mesma régua do mapa e do botão de rota: quem não vê localização
    # exata não recebe nem o ponto do CD (02/10/2026)
    if not pode_ver_localizacao_exata():
        return jsonify({"error": "Sem permissão pra ver localização exata."}), 403
    if session.get("perfil") in ("admin", "master"):
        permitidas = set()
    else:
        ups = session.get("unidades_permitidas", "")
        permitidas = {x.strip().upper() for x in ups.split(",") if x.strip()}
    cds = []
    for c in get_cds_localizacao():
        sigla = str(c.get("sigla", "")).strip().upper()
        if permitidas and sigla not in permitidas:
            continue
        lat = str(c.get("latitude", "")).strip()
        lon = str(c.get("longitude", "")).strip()
        if not lat or not lon:
            continue
        cds.append({"sigla": sigla, "lat": lat, "lon": lon})
    return jsonify({"cds": cds})


@app.route("/api/rota_maps")
@login_required
@modulo_required("monitoramento")
def api_rota_maps():
    if not pode_ver_localizacao_exata():
        return jsonify({"error": "Sem permissão pra ver localização exata."}), 403
    placa = request.args.get("placa","").strip()
    df    = _estado_usuario(_usuario_efetivo())["df_consolidado"]
    cd_pessoal = get_cd_usuario(str(session.get("user","")).strip().lower())

    row = df[df["Placa"].astype(str).str.strip().eq(placa)] if not df.empty else pd.DataFrame()
    if row.empty:
        df_r = _estado_usuario(_usuario_efetivo())["df_redespacho"]
        row  = df_r[df_r["Placa"].astype(str).str.strip().eq(placa)] if not df_r.empty else pd.DataFrame()
    if row.empty:
        return jsonify({"error": "Placa não encontrada"}), 404

    r   = row.iloc[0]
    lat = str(r.get("Latitude","")).strip().replace(",",".")
    lon = str(r.get("Longitude","")).strip().replace(",",".")

    # Prioriza coordenadas do CD cadastrado pelo master (Empresa Compra do card)
    # Fallback para configuração pessoal do usuário
    empresa_compra = str(r.get("Empresa Compra","")).strip()
    cd_cadastrado  = get_cd_localizacao(empresa_compra) if empresa_compra else {}
    lat_cd = str(cd_cadastrado.get("latitude","")).strip() if cd_cadastrado else ""
    lon_cd = str(cd_cadastrado.get("longitude","")).strip() if cd_cadastrado else ""

    if not lat_cd or not lon_cd:
        # Fallback: configuração pessoal do usuário
        lat_cd = str(cd_pessoal.get("latitude","")).strip()
        lon_cd = str(cd_pessoal.get("longitude","")).strip()

    if not all([lat, lon, lat_cd, lon_cd]):
        return jsonify({"error": "Coordenadas não disponíveis. Configure o CD e importe o Painel de Monitoramento."}), 400

    url = (f"https://www.google.com/maps/dir/?api=1"
           f"&origin={lat},{lon}&destination={lat_cd},{lon_cd}&travelmode=driving")
    return jsonify({"url": url})

# ── HISTÓRICO DE TEMPERATURA (MEL-4) ──────────────────────
@app.route("/api/temperatura/<placa>")
@login_required
@modulo_required("monitoramento")
def api_temperatura(placa):
    horas = request.args.get("horas", 48)
    try:
        horas = max(1, min(int(horas), 24 * 30))  # entre 1h e 30 dias
    except ValueError:
        horas = 48
    serie = obter_serie_temperatura(placa, horas=horas)
    return jsonify({
        "placa": placa,
        "serie": serie,
        "alerta_variacao": detectar_variacao_brusca(serie),
    })

# ── ADMIN — GESTÃO DE USUÁRIOS ───────────────────────────
@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html", user=session.get("nome", session.get("user")))

@app.route("/api/admin/usuarios")
@admin_required
def api_admin_usuarios():
    return jsonify({"usuarios": listar_usuarios()})

@app.route("/api/admin/criar_usuario", methods=["POST"])
@admin_required
@csrf_required
def api_admin_criar():
    d = request.json or {}
    ok, msg = criar_usuario(
        d.get("username",""), d.get("password",""),
        d.get("perfil","visualizador"),
        d.get("nome_exibicao",""),
        d.get("unidades_permitidas",""),
        d.get("modulos_permitidos",""),
        d.get("unidades_vinculadas",""),
        int(d.get("operador_call_center", 0) or 0),
    )
    if ok:
        registrar_log(session["user"], "criar_usuario", alvo=d.get("username",""),
                      detalhe=f"perfil={d.get('perfil','visualizador')}")
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)

@app.route("/api/admin/editar_usuario", methods=["POST"])
@admin_required
@csrf_required
def api_admin_editar():
    d = request.json or {}
    user_id = int(d.get("id",0))
    ok, msg = editar_usuario(
        user_id, d.get("nome_exibicao",""),
        d.get("perfil","visualizador"), d.get("unidades_permitidas",""),
        int(d.get("ativo",1)), d.get("nova_senha",""),
        d.get("modulos_permitidos",""),
        d.get("unidades_vinculadas",""),
        int(d.get("operador_call_center", 0) or 0),
    )
    if ok:
        with get_db() as db:
            row_alvo = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        senha_alterada = " senha_alterada" if d.get("nova_senha") else ""
        registrar_log(session["user"], "editar_usuario",
                      alvo=row_alvo["username"] if row_alvo else str(user_id),
                      detalhe=f"perfil={d.get('perfil','visualizador')} ativo={d.get('ativo',1)}{senha_alterada}")
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)

@app.route("/api/admin/excluir_usuario", methods=["POST"])
@admin_required
@csrf_required
def api_admin_excluir():
    d = request.json or {}
    user_id = int(d.get("id",0))
    with get_db() as db:
        row_alvo = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    ok, msg = excluir_usuario(user_id, session["user"])
    if ok:
        registrar_log(session["user"], "excluir_usuario",
                      alvo=row_alvo["username"] if row_alvo else str(user_id))
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)

@app.route("/api/admin/log_auditoria")
@admin_required
def api_admin_log_auditoria():
    usuario = request.args.get("usuario", "").strip().lower()
    acao    = request.args.get("acao", "").strip()
    try:
        dias = int(request.args.get("dias", 30))
    except ValueError:
        dias = 30
    entradas = listar_log_auditoria(usuario=usuario, acao=acao, dias=dias, limite=500)
    return jsonify({
        "entradas": entradas,
        "acoes": listar_acoes_log(),
        "usuarios": [u["username"] for u in listar_usuarios()],
    })


# ── MASTER — LOCALIZAÇÕES DOS CDs ─────────────────────────
@app.route("/api/master/cds", methods=["GET"])
@admin_required
def api_master_cds_listar():
    return jsonify(get_cds_localizacao())

@app.route("/api/master/cds", methods=["POST"])
@admin_required
@csrf_required
def api_master_cds_salvar():
    d = request.json or {}
    sigla = str(d.get("sigla", "")).strip().upper()
    lat = str(d.get("latitude", "")).strip().replace(",", ".")
    lon = str(d.get("longitude", "")).strip().replace(",", ".")
    if not sigla or not lat or not lon:
        return jsonify({"ok": False, "error": "Informe sigla, latitude e longitude."}), 400
    raio_txt = str(d.get("raio_chegada_km", "") or "").strip().replace(",", ".")
    raio = None
    if raio_txt:
        try:
            raio = float(raio_txt)
        except ValueError:
            raio = -1
        if not (0.1 <= raio <= 20):
            return jsonify({"ok": False, "error": "Raio de chegada entre 0,1 e 20 km."}), 400
    set_cd_localizacao(sigla, lat, lon, raio)
    registrar_log(session.get("user",""), "salvar_cd_localizacao", alvo=sigla,
                  detalhe=f"lat={lat} lon={lon} raio={raio if raio is not None else 'padrão'}")
    return jsonify({"ok": True})

@app.route("/api/master/cds/<int:cd_id>", methods=["DELETE"])
@admin_required
@csrf_required
def api_master_cds_excluir(cd_id):
    alvo = next((c["sigla"] for c in get_cds_localizacao() if c["id"] == cd_id), str(cd_id))
    del_cd_localizacao(cd_id)
    registrar_log(session.get("user",""), "excluir_cd_localizacao", alvo=alvo)
    return jsonify({"ok": True})

# ── MASTER — EMPRESA CONSOLIDADA ──────────────────────────
# Quais empresas dividem o MESMO LOCAL FÍSICO (CDA e CDB dividem endereço;
# CDE e CDF idem). Uma carreta que entrega em duas empresas do mesmo local
# descarrega num lugar só e vira UM card. Empresa não cadastrada fica sozinha.
# Restrito a admin/master — que é o perfil de gerente e administrador.

@app.route("/api/master/empresas-consolidadas", methods=["GET"])
@admin_required
def api_master_empcons_listar():
    return jsonify(get_empresas_consolidadas())


@app.route("/api/master/empresas-consolidadas", methods=["POST"])
@admin_required
@csrf_required
def api_master_empcons_salvar():
    d = request.json or {}
    sigla = str(d.get("sigla", "")).strip().upper()
    consolidada = str(d.get("consolidada", "")).strip().upper()
    if not sigla:
        return jsonify({"ok": False, "error": "Informe a empresa."}), 400
    if not consolidada:
        return jsonify({"ok": False, "error": "Informe a empresa consolidada."}), 400
    set_empresa_consolidada(sigla, consolidada)
    registrar_log(session.get("user", ""), "salvar_empresa_consolidada",
                  alvo=sigla, detalhe=f"consolidada={consolidada}")
    return jsonify({"ok": True})


@app.route("/api/master/empresas-consolidadas/<int:reg_id>", methods=["DELETE"])
@admin_required
@csrf_required
def api_master_empcons_excluir(reg_id):
    alvo = next((r["sigla"] for r in get_empresas_consolidadas() if r["id"] == reg_id), str(reg_id))
    del_empresa_consolidada(reg_id)
    registrar_log(session.get("user", ""), "excluir_empresa_consolidada", alvo=alvo)
    return jsonify({"ok": True})


# ── PREFERÊNCIAS ──────────────────────────────────────────

@app.route("/api/limpar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@bloquear_escrita
@csrf_required
def api_limpar():
    d       = request.json or {}
    usuario = str(session.get("user","")).strip().lower()
    # As marcações e o relatório são da conta EFETIVA (coordenador/analista
    # gravam na conta dona do CD; gerente espelhando grava no CD espelhado) —
    # limpar a conta logada respondia ok e não apagava nada (revisão 19/10/2026).
    # A seleção de redespacho segue na conta logada, que é onde o toggle grava.
    alvo    = _usuario_efetivo()
    if d.get("relatorio"):
        st = _estado_usuario(alvo)
        st["df_consolidado"] = pd.DataFrame()
        st["df_itens"]       = pd.DataFrame()
        st["df_redespacho"]  = pd.DataFrame()
        st["atualizado"]     = None
        try:
            with get_db() as db:
                db.execute("DELETE FROM sessao_dados WHERE usuario=?", (alvo,))
        except Exception:
            pass
    if d.get("validacoes"):
        try:
            with get_db() as db:
                db.execute("DELETE FROM validacoes WHERE usuario=?", (alvo,))
        except Exception:
            pass
    if d.get("observacoes"):
        try:
            with get_db() as db:
                db.execute("DELETE FROM observacoes WHERE usuario=?", (alvo,))
        except Exception:
            pass
    if d.get("validacoes") or d.get("observacoes"):
        try:
            card_validacao.limpar(alvo, validacoes=bool(d.get("validacoes")),
                                  observacoes=bool(d.get("observacoes")))
        except Exception:
            pass
        # a tela muda já, sem esperar o próximo ciclo do rastreio
        try:
            st = _estado_usuario(alvo)
            df = st["df_consolidado"]
            if not df.empty:
                df = df.copy()
                if d.get("validacoes"):
                    for col, padrao in (("Regra Data CD", "Não Verificado"), ("Entregas Frente", "Não Verificado"),
                                        ("Entregas Frente Obs", ""), ("Descarga Concluída", "Não"),
                                        ("Descarga Iniciada", "Não")):
                        df[col] = padrao
                if d.get("observacoes"):
                    df["Observação"] = ""
                df = carretas_viagem.aplicar_no_df(aplicar_estimativas(df), alvo, "coligada")
                st["df_consolidado"] = df
                salvar_sessao(df, st["df_itens"], st["df_redespacho"], alvo,
                              df_controle=st.get("df_controle"), usou_opentech=st.get("usou_opentech", False))
        except Exception as e:
            print(f"[Limpar] Cards não refeitos: {e}")
    if d.get("redespacho"):
        try:
            with get_db() as db:
                db.execute("DELETE FROM redespacho_sel WHERE usuario=?", (usuario,))
        except Exception:
            pass
    itens_limpos = ",".join(k for k in ("relatorio","validacoes","observacoes","redespacho") if d.get(k))
    registrar_log(usuario, "limpar_dados", alvo=alvo, detalhe=itens_limpos)
    return jsonify({"ok": True})

@app.route("/api/espelho/usuarios")
@login_required
def api_espelho_usuarios():
    # Sem @modulo_required("monitoramento") de propósito: essa rota hoje
    # também alimenta o seletor de espelho do Armazém e do Xsense (pedido do
    # usuário 24/08/2026) — um operador com acesso só a esses módulos (sem
    # "monitoramento") ficava bloqueado aqui com 403 mesmo podendo espelhar
    # (a própria /api/espelho/iniciar já libera perfil operador). A lista em
    # si não expõe nada específico do Monitoramento, só usuário+timestamp.
    eu = str(session.get("user","")).strip().lower()
    todos = get_usuarios_com_dados()
    if session.get("perfil") in ("admin", "master"):
        return jsonify({"usuarios": [u for u in todos if u["usuario"] != eu]})
    # Extensão limitada do espelho pra papéis com escopo de CD (29/08/2026):
    # só os donos das unidades EXTRAS liberadas pro cadastro dessa pessoa
    # (cobertura temporária) entram na lista — nunca a lista toda de
    # usuários, isso continua exclusivo de admin/master.
    donos = {conta_canonica_do_cd(sigla) for sigla in unidades_extras_permitidas_sessao()}
    donos.discard("")
    donos.discard(eu)
    return jsonify({"usuarios": [u for u in todos if u["usuario"] in donos]})

@app.route("/api/espelho/iniciar", methods=["POST"])
@login_required
@csrf_required
def api_espelho_iniciar():
    # Sem @modulo_required("monitoramento") — mesmo motivo de
    # /api/espelho/usuarios acima: essa rota agora também é usada pelo
    # Armazém/Xsense. A checagem de perfil logo abaixo já é o controle de
    # acesso de verdade aqui.
    #
    # "operador" saiu da lista de quem pode espelhar manualmente (redesenho
    # de papéis, 24/08/2026) — antes era o nível padrão (podia espelhar
    # outros usuários com interseção de unidades), agora é o nível
    # restrito, preso à própria conta canônica do CD dele via
    # usuario_efetivo(); deixar espelhar QUALQUER usuário seria uma forma
    # de escapar do sandbox. Extensão LIMITADA (29/08/2026): coordenador/
    # analista/qualidade/operador podem espelhar, mas só a conta dona de
    # uma unidade que está na lista de EXTRAS liberadas pro cadastro dela
    # (cobertura temporária de outro CD) — nunca um usuário qualquer, isso
    # continua exclusivo de admin/master.
    perfil = session.get("perfil", "")
    d    = request.json or {}
    alvo = str(d.get("usuario","")).strip().lower()
    eu   = str(session.get("user","")).strip().lower()
    if not alvo or alvo == eu:
        return jsonify({"error": "Usuário inválido"}), 400
    if perfil not in ("admin", "master"):
        extras = set(unidades_extras_permitidas_sessao())
        if not extras:
            return jsonify({"error": "Sem permissão para espelhar"}), 403
        alvo_vinculadas = set(cds_da_conta_canonica(alvo))
        if not (alvo_vinculadas & extras):
            return jsonify({"error": "Essa conta não está entre as unidades liberadas pra você visualizar."}), 403
    if session.get("espelhando"):
        try: remover_espelho(eu, session["espelhando"])
        except: pass
    session["espelhando"] = alvo
    try: registrar_espelho(eu, alvo)
    except: pass
    # Se o painel do alvo ainda não está em memória neste processo (restart
    # recente, alvo sem abrir o próprio painel), dispara a restauração agora —
    # o front chama carregarDados() logo em seguida e, se vier vazio, fica
    # aguardando com o banner de restauração (mesmo fluxo do init).
    if _estado_usuario(alvo)["df_consolidado"].empty:
        threading.Thread(target=_restaurar_estado_usuario, args=(alvo,), daemon=True).start()
    registrar_log(eu, "espelho_iniciar", alvo=alvo)
    return jsonify({"ok": True, "espelhando": alvo})

@app.route("/api/espelho/sair", methods=["POST"])
@login_required
@csrf_required
def api_espelho_sair():
    eu   = str(session.get("user","")).strip().lower()
    alvo = session.pop("espelhando", "")
    if alvo:
        try: remover_espelho(eu, alvo)
        except: pass
        registrar_log(eu, "espelho_sair", alvo=alvo)
    return jsonify({"ok": True})

@app.route("/api/espelho/heartbeat", methods=["POST"])
@login_required
@csrf_required
def api_espelho_heartbeat():
    """Renova o registro de espelhamento enquanto o observador está com o
    painel do alvo aberto. Sem heartbeat periódico, get_observadores() expira
    o registro sozinho (ver BUG-4)."""
    eu   = str(session.get("user","")).strip().lower()
    alvo = session.get("espelhando", "")
    if alvo:
        try: heartbeat_espelho(eu, alvo)
        except: pass
    return jsonify({"ok": True, "espelhando": alvo})

@app.route("/api/preferencias", methods=["GET","POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_preferencias():
    # usuario_efetivo (não a conta logada crua): config do CD é dado
    # compartilhado da conta canônica, mesma lógica de qualquer outro dado
    # de Coordenador/Analista/Qualidade/Operador (redesenho de papéis,
    # 24/08/2026) — sem isso, cada login individual editava uma cópia
    # própria e nunca vista por ninguém.
    usuario = _usuario_efetivo()
    if request.method == "POST":
        if somente_leitura():
            return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
        d = request.json or {}
        set_cd_usuario(usuario, d.get("nome","CDA"), d.get("latitude",""),
               d.get("longitude",""), d.get("fator_rodoviario",1.25),
               horarios_corte=d.get("horarios_corte",{}), nome2=d.get("nome2",""))
        # Recalcula estimativas do usuário com o novo horário de corte
        st = _states.get(usuario)
        if st is not None and not st["df_consolidado"].empty:
            _load_schedule(get_cd_usuario(usuario))
            st["df_consolidado"] = carretas_viagem.aplicar_no_df(
                aplicar_estimativas(st["df_consolidado"]), usuario, "coligada")
            st["atualizado"] = datetime.now(_BRT).isoformat(timespec="seconds")
        return jsonify({"ok": True})
    return jsonify(get_cd_usuario(usuario))

# ── MASTER: CREDENCIAIS OPENTECH ─────────────────────────

@app.route("/api/master/opentech", methods=["GET"])
@login_required
@modulo_required("monitoramento")
def api_master_opentech_get():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    return jsonify(get_opentech_config())

@app.route("/api/master/opentech", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_opentech_set():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    d = request.json or {}
    set_opentech_config(
        usuario=d.get("usuario"),
        senha=d.get("senha") or None,
        intervalo_min=d.get("intervalo_min"),
    )
    senha_alterada = " senha_alterada" if d.get("senha") else ""
    registrar_log(session.get("user",""), "config_opentech",
                  detalhe=f"usuario_opentech={d.get('usuario','')} intervalo_min={d.get('intervalo_min','')}{senha_alterada}")
    return jsonify({"ok": True})

@app.route("/api/master/xsense", methods=["GET"])
@login_required
@modulo_required("monitoramento")
def api_master_xsense_get():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    return jsonify(get_xsense_config())

@app.route("/api/master/xsense", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_xsense_set():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    d = request.json or {}
    set_xsense_config(
        usuario=d.get("usuario"),
        senha=d.get("senha") or None,
        node_id=d.get("node_id"),
    )
    senha_alterada = " senha_alterada" if d.get("senha") else ""
    registrar_log(session.get("user",""), "config_xsense",
                  detalhe=f"usuario_xsense={d.get('usuario','')}{senha_alterada}")
    return jsonify({"ok": True})

@app.route("/api/master/xsense/testar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_xsense_testar():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    creds = get_xsense_credenciais()
    ok, msg = testar_conexao_xsense(creds.get("usuario",""), creds.get("senha",""))
    return jsonify({"ok": ok, "msg": msg})

# ── IA do Expedito (Gemini) ──────────────────────────────────────────
# A chave nunca volta em texto puro: a tela recebe has_chave e os 4 últimos
# caracteres, o bastante para a pessoa reconhecer qual chave está lá.

@app.route("/api/master/expedito/ia", methods=["GET"])
@login_required
@modulo_required("expedito")
def api_expedito_ia_get():
    if not is_master(session.get("user","")) and session.get("perfil") != "admin":
        return jsonify({"error": "Acesso negado"}), 403
    from app.services.expedito_ia import MODELOS_SUGERIDOS, MODELO_PADRAO
    cfg = get_expedito_ia_config()
    cfg["modelos"] = [{"id": i, "rotulo": r} for i, r in MODELOS_SUGERIDOS]
    cfg["modelo_padrao"] = MODELO_PADRAO
    return jsonify(cfg)


@app.route("/api/master/expedito/ia", methods=["POST"])
@login_required
@modulo_required("expedito")
@csrf_required
def api_expedito_ia_set():
    if not is_master(session.get("user","")) and session.get("perfil") != "admin":
        return jsonify({"error": "Acesso negado"}), 403
    d = request.json or {}
    if d.get("limpar"):
        limpar_expedito_ia_chave()
        registrar_log(session.get("user",""), "config_expedito_ia", detalhe="chave_removida")
        return jsonify({"ok": True})
    set_expedito_ia_config(chave=d.get("chave") or None,
                           modelo=d.get("modelo"),
                           ativo=d.get("ativo"))
    # o log conta O QUE mudou, nunca o valor
    registrar_log(session.get("user",""), "config_expedito_ia",
                  detalhe=("chave_alterada" if d.get("chave") else "sem_alteracao"))
    return jsonify({"ok": True})


@app.route("/api/master/expedito/ia/testar", methods=["POST"])
@login_required
@modulo_required("expedito")
@csrf_required
def api_expedito_ia_testar():
    """Valida a chave com uma pergunta mínima, sem dado de operação nenhum."""
    if not is_master(session.get("user","")) and session.get("perfil") != "admin":
        return jsonify({"error": "Acesso negado"}), 403
    from app.services.expedito_ia import testar_chave
    cfg = get_expedito_ia_config()
    ok, msg, _info = testar_chave(get_expedito_ia_chave(), cfg.get("modelo", ""))
    set_expedito_ia_config(teste_ok=ok)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/master/xsense/telegram", methods=["GET"])
@login_required
@modulo_required("monitoramento")
def api_master_xsense_telegram_get():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    return jsonify(get_xsense_telegram_config())

@app.route("/api/master/xsense/telegram", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_xsense_telegram_set():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    d = request.json or {}
    tok = str(d.get("token") or "").strip()
    if tok:
        from app.database import get_telegram_bot_token, get_telegram_bot_config
        tok_carretas = get_telegram_bot_token("carretas_telegram")
        id_carretas = get_telegram_bot_config("carretas_telegram").get("bot_id", "")
        if (tok_carretas and tok == tok_carretas) or (id_carretas and tok.split(":")[0] == id_carretas):
            return jsonify({"ok": False, "error": "Este é o token do bot de carretas. "
                                                   "Use um bot próprio para a temperatura."}), 400
    set_xsense_telegram_config(token=d.get("token") or None, bot_username=d.get("bot_username"))
    token_alterado = " token_alterado" if d.get("token") else ""
    # registra o menu "/" nativo (setMyCommands) — existia a função mas
    # nunca era chamada em lugar nenhum (achado 26/08/2026 ao testar um bot
    # novo e o menu não aparecer); melhor esforço, não bloqueia o salvar
    # se o Telegram falhar aqui
    try:
        _definir_comandos_telegram(get_xsense_telegram_token())
    except Exception:
        pass
    registrar_log(session.get("user",""), "config_xsense_telegram", detalhe=f"{token_alterado}".strip() or "sem_alteracao")
    return jsonify({"ok": True})

@app.route("/api/master/captcha", methods=["GET"])
@login_required
@modulo_required("monitoramento")
def api_master_captcha_get():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    return jsonify(get_captcha_config())

@app.route("/api/master/captcha", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_captcha_set():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    d = request.json or {}
    try:
        ativo_apos = int(d.get("ativo_apos")) if d.get("ativo_apos") not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"error": "\"A partir da tentativa\" precisa ser um número."}), 400
    set_captcha_config(site_key=d.get("site_key"), secret_key=d.get("secret_key") or None, ativo_apos=ativo_apos)
    registrar_log(session.get("user",""), "config_captcha",
                 detalhe="secret_alterada" if d.get("secret_key") else "sem_alteracao_na_secret")
    return jsonify({"ok": True})

@app.route("/api/master/xsense/telegram/testar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_xsense_telegram_testar():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    token = get_xsense_telegram_token()
    ok, msg, info = testar_bot_telegram(token)
    if ok and info.get("username"):
        set_xsense_telegram_config(bot_username=info["username"])
    return jsonify({"ok": ok, "msg": msg})

@app.route("/api/master/xsense/telegram/webhook", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_xsense_telegram_webhook():
    """Liga/desliga o modo webhook do bot (resposta instantânea em vez de
    polling a cada 1 min pelo cron). Ver web/xsense.py:telegram_webhook
    pro endpoint que o Telegram efetivamente chama."""
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    from app.database import get_xsense_telegram_webhook_secret
    from app.services.telegram_alertas import configurar_webhook, remover_webhook

    d = request.json or {}
    token = get_xsense_telegram_token()
    if not token:
        return jsonify({"error": "Configure e salve o token do bot primeiro."}), 400

    if d.get("ativar"):
        url_base = str(d.get("url_base", "")).strip().rstrip("/")
        if not url_base.startswith("https://"):
            return jsonify({"error": "Informe a URL pública com https:// (ex.: https://seu-dominio.com.br)."}), 400
        secret = get_xsense_telegram_webhook_secret()
        ok, msg = configurar_webhook(token, url_base + "/api/xsense/telegram/webhook", secret)
        if ok:
            set_xsense_telegram_config(webhook_ativo=True)
        registrar_log(session.get("user",""), "config_xsense_telegram_webhook", detalhe=f"ativar url={url_base} ok={ok}")
        return jsonify({"ok": ok, "msg": msg})
    else:
        ok, msg = remover_webhook(token)
        set_xsense_telegram_config(webhook_ativo=False)
        registrar_log(session.get("user",""), "config_xsense_telegram_webhook", detalhe=f"desativar ok={ok}")
        return jsonify({"ok": ok, "msg": msg or "Webhook removido — volte a rodar o cron de polling."})

@app.route("/api/master/xsense/telegram/site_url", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_xsense_telegram_site_url():
    """Salva a URL pública do site separado do webhook (26/08/2026, pro
    botão /painel do Mini App) — precisa funcionar mesmo em polling (sem
    webhook ativo), caso do homolog, que não tem HTTPS público estável pro
    Telegram chamar de volta."""
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    url = str((request.json or {}).get("url", "")).strip().rstrip("/")
    if not url.startswith("https://") and not url.startswith("http://localhost"):
        return jsonify({"error": "Informe a URL pública com https:// (ex.: https://seu-dominio.com.br)."}), 400
    set_xsense_telegram_config(site_url=url)
    registrar_log(session.get("user",""), "config_xsense_telegram_site_url", detalhe=url)
    return jsonify({"ok": True})

@app.route("/api/master/ravex", methods=["GET"])
@login_required
@modulo_required("monitoramento")
def api_master_ravex_get():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    return jsonify(get_ravex_config())

@app.route("/api/master/ravex", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_ravex_set():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    d = request.json or {}
    antes = get_ravex_credenciais()
    set_ravex_config(
        usuario=d.get("usuario"),
        senha=d.get("senha") or None,
        intervalo_min=d.get("intervalo_min"),
    )
    # credencial nova: a espera acumulada por logins que falharam com a
    # antiga deixa de valer (o intervalo mínimo entre logins continua)
    depois = get_ravex_credenciais()
    if (depois.get("usuario"), depois.get("senha")) != (antes.get("usuario"), antes.get("senha")):
        from app.services import ravex_sessao
        ravex_sessao.zerar_espera()
    senha_alterada = " senha_alterada" if d.get("senha") else ""
    registrar_log(session.get("user",""), "config_ravex",
                  detalhe=f"usuario_ravex={d.get('usuario','')} intervalo_min={d.get('intervalo_min','')}{senha_alterada}")
    return jsonify({"ok": True})

@app.route("/api/master/ravex/testar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@rate_limited("ravex_testar", max_chamadas=3, janela_seg=300)
@csrf_required
def api_master_ravex_testar():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    # O teste faz login pela tela: entra na mesma política de logins da
    # sessão compartilhada (a Ravex bloqueia logins repetidos — 13/11/2026).
    # Usa o mesmo lock do Chromium do cron e reserva o login antes de abrir
    # o navegador (3ª revisão, 14/11/2026: cron e botão no mesmo minuto
    # logavam duas vezes).
    from app.services import ravex_sessao
    from app.services.ravex_automation import registrar_resultado_login_navegador
    from app.utils.chromium_lock import adquirir_lock_chromium, liberar_lock_chromium
    liberado, motivo = ravex_sessao.login_liberado("Chromium")
    if not liberado:
        return jsonify({"ok": False, "msg": f"Teste não executado: {motivo}"})
    lock = adquirir_lock_chromium()
    if lock is None:
        return jsonify({"ok": False, "msg": "Teste não executado: o navegador está em uso por outra "
                                            "atualização. Tente novamente em alguns minutos."})
    info_login: dict = {}
    ok, msg, marca = False, "", 0.0
    try:
        marca, motivo = ravex_sessao.reservar_login("Chromium")
        if not marca:
            return jsonify({"ok": False, "msg": f"Teste não executado: {motivo}"})
        creds = get_ravex_credenciais()
        ok, msg = testar_conexao_ravex(creds.get("usuario",""), creds.get("senha",""), tentativas=1, info=info_login)
    finally:
        liberar_lock_chromium(lock)
        if marca:
            registrar_resultado_login_navegador(info_login, msg, marca)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/api/master/ravex/atualizar_agora", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@rate_limited("ravex_atualizar_agora", max_chamadas=3, janela_seg=60)
@csrf_required
def api_master_ravex_atualizar_agora():
    """Dispara a atualização Ravex em processo separado (mesmo script usado
    pelo cron), mesmo padrão da OpenTech (ver BUG-5/MEL-14)."""
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    script = str(Path(__file__).parent.parent / "scripts" / "atualizar_ravex.py")
    try:
        # Saída vai pro MESMO cron.log do agendamento (antes ia pro journal do
        # systemd e o disparo manual ficava "invisível" pra quem acompanha o
        # log — confusão real do usuário em 04/08/2026). -u = sem buffer, pra
        # linha aparecer na hora e na ordem certa.
        log_path = _DATA_DIR / "ravex" / "cron.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log_f:
            subprocess.Popen(
                [sys.executable, "-u", script, "--forcar"],
                cwd=str(Path(__file__).parent.parent),
                stdout=log_f, stderr=subprocess.STDOUT,
            )
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Erro ao disparar atualização: {e}"}), 500
    return jsonify({
        "ok": True,
        "msg": "Atualização disparada em segundo plano.",
        "ravex": get_ravex_config(),
    })

@app.route("/api/master/opentech/testar", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@csrf_required
def api_master_opentech_testar():
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    creds = get_opentech_credenciais()
    ok, msg = testar_conexao_opentech(creds.get("usuario",""), creds.get("senha",""))
    return jsonify({"ok": ok, "msg": msg})

@app.route("/api/master/opentech/atualizar_agora", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@rate_limited("opentech_atualizar_agora", max_chamadas=3, janela_seg=60)
@csrf_required
def api_master_opentech_atualizar_agora():
    """
    Dispara a atualização OpenTech em processo separado (mesmo script usado
    pelo cron — ver BUG-5) em vez de rodar o Playwright dentro deste worker,
    o que travaria o sistema por 30-90s. A resposta é imediata; o resultado
    real chega via /api/opentech/status (polling do front) quando o processo
    terminar e persistir o painel em disco.
    """
    if not is_master(session.get("user","")):
        return jsonify({"error": "Acesso negado"}), 403
    script = str(Path(__file__).parent.parent / "scripts" / "atualizar_opentech.py")
    try:
        # Mesmo padrão do disparo manual da Ravex: saída no cron.log, sem buffer.
        log_path = _DATA_DIR / "opentech" / "cron.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log_f:
            subprocess.Popen(
                [sys.executable, "-u", script, "--forcar"],
                cwd=str(Path(__file__).parent.parent),
                stdout=log_f, stderr=subprocess.STDOUT,
            )
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Erro ao disparar atualização: {e}"}), 500
    return jsonify({
        "ok": True,
        "msg": "Atualização disparada em segundo plano — acompanhe o status acima.",
        "opentech": get_opentech_config(),
    })

@app.route("/api/opentech/status")
@login_required
@modulo_required("monitoramento")
def api_opentech_status():
    cfg = get_opentech_config()
    return jsonify({
        "configurado": cfg.get("has_senha", False) and bool(cfg.get("usuario")),
        "status": cfg.get("status",""),
        "ultima_atualizacao": cfg.get("ultima_atualizacao",""),
        "atualizando": cfg.get("atualizando", False),
    })

@app.route("/api/ravex/status")
@login_required
@modulo_required("monitoramento")
def api_ravex_status():
    cfg = get_ravex_config()
    return jsonify({
        "configurado": cfg.get("has_senha", False) and bool(cfg.get("usuario")),
        "status": cfg.get("status",""),
        "ultima_atualizacao": cfg.get("ultima_atualizacao",""),
        "atualizando": cfg.get("atualizando", False),
    })


# ── EXPORTAR ─────────────────────────────────────────────
def _sanitizar_formula_excel(df):
    """Previne formula injection em exportacoes Excel."""
    PREFIXOS_PERIGOSOS = ("=", "+", "-", "@")
    def _fix(v):
        if isinstance(v, str) and v.startswith(PREFIXOS_PERIGOSOS):
            return "'" + v
        return v
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].apply(_fix)
    return df


def _unidades_upper(valor) -> set:
    """{"CDA","CDB"} a partir de "CDA · CDB".

    Desde que o card virou a carreta física, "Empresa Compra" pode trazer
    várias unidades. Comparar a string inteira com `isin` fazia o card sumir
    ao filtrar por uma delas — inclusive no filtro de PERMISSÃO, onde um
    usuário restrito a CDA perdia o card CDA·CDB."""
    return {u.strip().upper() for u in str(valor or "").split("·") if u.strip()}


def _df_filtrado(unidade_param="", df=None):
    if df is None: df = _estado_usuario(_usuario_efetivo())["df_consolidado"]
    if df.empty: return df
    # Unidades permitidas pela sessão (restrição de perfil) — admin/master
    # nunca são restritos (mesma regra de get_unidades_sessao()).
    if session.get("perfil") in ("admin", "master"):
        permitidas_sessao = set()
    else:
        ups = session.get("unidades_permitidas", "")
        permitidas_sessao = {u.strip().upper() for u in ups.split(",") if u.strip()} if ups else set()
    uns = [u.strip() for u in unidade_param.split(",") if u.strip()]
    if uns:
        filtro = set(u.upper() for u in uns)
        # Se o usuário tiver restrição de unidade, só mostra a interseção
        if permitidas_sessao:
            filtro = filtro & permitidas_sessao
        df = df[df["Empresa Compra"].map(lambda v: bool(filtro & _unidades_upper(v)))].copy()
    elif permitidas_sessao:
        # Sem filtro de parâmetro mas com restrição de sessão — aplica restrição
        df = df[df["Empresa Compra"].map(
            lambda v: bool(permitidas_sessao & _unidades_upper(v)))].copy()
    return df.sort_values(["Empresa Compra","Empresa","Placa","NF"], kind="stable").reset_index(drop=True)

def _gerar_e_servir(nome_base, gerar_fn):
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    ext = Path(nome_base).suffix
    out = SAIDA_DIR / f"{Path(nome_base).stem}_{ts}_{secrets.token_hex(4)}{ext}"
    gerar_fn(out)
    # o link leva a posse do arquivo (assinatura de arquivo + usuário); a
    # lista no cookie era apagada por chamadas atrasadas (03/11/2026)
    return jsonify({"url": url_saida(out.name)})

@app.route("/saida/<filename>")
@login_required
def download_saida(filename):
    # sem exigir o módulo do painel: o link assinado já diz o arquivo e o
    # usuário, e o Armazém também serve as imagens por aqui (03/11/2026)
    # Garante que o arquivo é do diretório saida (evita path traversal)
    safe_name = os.path.basename(filename)
    safe = SAIDA_DIR / safe_name
    if not safe.exists():
        return jsonify({"error": "Arquivo não encontrado"}), 404
    # Verifica se o arquivo foi gerado para este usuário: o link assinado; a
    # lista na sessão vale para links gerados antes de 03/11/2026
    if not link_valido(safe_name, request.args.get("k")) \
            and safe_name not in session.get("arquivos_gerados", []):
        return jsonify({"error": "Acesso negado"}), 403
    return send_file(safe, as_attachment=True)

@app.route("/api/exportar_imagem")
@login_required
@modulo_required("monitoramento")
def api_exportar_imagem():
    """Gera as DUAS imagens — coligadas e redespacho — separadas.

    Voltaram a ser painéis distintos (usuário, 04/09/2026: juntos ficou
    "poluído e bagunçado"). O tipo vai no TÍTULO de cada imagem; a etiqueta
    lateral por card foi removida. A tela oferece copiar/baixar cada uma.
    """
    unidade = request.args.get("unidade", "")
    st = _estado_usuario(_usuario_efetivo())

    df = _df_filtrado(unidade, st["df_consolidado"])
    df_red = st.get("df_redespacho")
    tem_red = df_red is not None and not df_red.empty
    # aba Outras operações: entra como a terceira imagem quando tem card
    # (pedido do usuário 20/10/2026) — falha nela não derruba as outras duas
    try:
        cards_outras = _outras_operacoes.painel(_usuario_efetivo())["cards"]
    except Exception as exc:
        print(f"[Exportar] Outras operações fora da exportação: {exc}")
        cards_outras = []

    if df.empty and not tem_red and not cards_outras:
        return jsonify({"error": "Sem dados para exportar"}), 400

    # monta as páginas e renderiza todas num Chromium só (03/11/2026: um
    # Chromium por imagem levava ~3 min na VM, com o worker único parado)
    pedidos = []          # (tipo, nome base, html, largura, obrigatória)
    try:
        if not df.empty:
            pedidos.append(("Transferências Coligadas", "Painel_Coligadas.png",
                            *html_painel(df, titulo_extra=" — COLIGADAS"), True))
        if tem_red:
            pedidos.append(("Redespacho", "Painel_Redespacho.png",
                            *html_painel(pd.DataFrame(), df_redespacho=df_red, titulo_extra=" — REDESPACHO"), True))
    except Exception as exc:
        print(f"[Exportar] Erro: {exc}")
        return jsonify({"error": "Erro interno ao processar. Tente novamente."}), 500
    if cards_outras:
        try:      # falha nesta não derruba as imagens de coligadas e redespacho
            pedidos.append(("Outras operações", "Painel_Outras_Operacoes.png", *html_outras(cards_outras), False))
        except Exception as exc:
            print(f"[Exportar] Imagem de Outras operações não gerada: {exc}")
    if not pedidos:
        return jsonify({"error": "Sem dados para exportar"}), 400
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    destinos = [SAIDA_DIR / f"{Path(nome).stem}_{ts}_{secrets.token_hex(8)}{Path(nome).suffix}"
                for _, nome, _, _, _ in pedidos]
    try:
        feitos = htmls_to_pngs_cada([(html, dest, larg) for (_, _, html, larg, _), dest in zip(pedidos, destinos)])
    except Exception as exc:
        print(f"[Exportar] Erro: {exc}")
        return jsonify({"error": "Erro interno ao processar. Tente novamente."}), 500
    saidas = []
    for (tipo, _, _, _, obrigatoria), feito in zip(pedidos, feitos):
        if isinstance(feito, Exception):
            print(f"[Exportar] Imagem de {tipo} não gerada: {feito}")
            if obrigatoria:
                return jsonify({"error": "Erro interno ao processar. Tente novamente."}), 500
            continue
        saidas.append({"tipo": tipo, "url": url_saida(feito.name)})

    if not saidas:
        return jsonify({"error": "Sem dados para exportar"}), 400
    # `url` mantido pra não quebrar quem ainda espera o formato antigo
    return jsonify({"imagens": saidas, "url": saidas[0]["url"]})

@app.route("/api/exportar_excel")
@login_required
@modulo_required("monitoramento")
def api_exportar_excel():
    df = _df_filtrado(request.args.get("unidade",""), _estado_usuario(_usuario_efetivo())["df_consolidado"])
    if df.empty:
        return jsonify({"error": "Sem dados para exportar"}), 400
    # colunas de apoio da tela (AE bruta do rastreio, carimbo ISO, minutos do
    # instante do cálculo): a planilha leva Viagem, Chegada/Saída CD e Presença
    df = df.drop(columns=[c for c in ("AE", "No CD Desde", "Aguardando Min", "Idas CD", "Sem Sinal CD",
                                      "Card ID")
                          if c in df.columns])
    try:
        df_safe = _sanitizar_formula_excel(df.copy())
        return _gerar_e_servir("Painel_Recebimento.xlsx",
                               lambda p: exportar_excel(df_safe, str(p)))
    except Exception as exc:
        return jsonify({"error": "Erro interno ao processar. Tente novamente."}), 500

# ── TEMPO DE DESCARGA ─────────────────────────────────────
# Painel de carretas, fase 2 (18/10/2026): da chegada no raio do CD ao
# "Término Desembarque" do painel de entrada do WMS — app/services/descarga_service.py.

def _escopo_descarga(gravar: bool = False) -> list[str]:
    """Empresas do tempo de descarga para a sessão. Vazio = todas (gerente).
    Ler: as unidades permitidas mais as empresas do Local da conta (CDA e
    CDB dividem o pátio). Gravar: as empresas do Local da conta — unidade
    liberada só para consulta (cobertura) não recebe painel."""
    if session.get("perfil") in ("admin", "master"):
        return []
    try:
        from web.produtividade import empresas_do_local
        do_local = [e.upper() for e in empresas_do_local(_usuario_efetivo())]
    except Exception:
        do_local = []
    permitidas = [u.strip().upper() for u in get_unidades_sessao()]
    if gravar:
        return do_local or permitidas
    junto = sorted(set(do_local) | set(permitidas))
    return junto


def _periodo_descarga():
    """(de, ate) em AAAA-MM-DD a partir da query string; padrão: os últimos
    7 dias até hoje. Período invertido é desvirado; mais de um ano é cortado."""
    from datetime import date as _date
    hoje = datetime.now(_BRT).date()
    try:
        ate = _date.fromisoformat(request.args.get("ate") or hoje.isoformat())
        de = _date.fromisoformat(request.args.get("de") or (ate - timedelta(days=6)).isoformat())
    except ValueError:
        return None, None
    if de > ate:
        de, ate = ate, de
    if (ate - de).days > 366:
        de = ate - timedelta(days=366)
    return de.isoformat(), ate.isoformat()


@app.route("/api/descarga")
@login_required
@modulo_required("monitoramento")
@rate_limited("descarga_relatorio", max_chamadas=40, janela_seg=60)
def api_descarga():
    de, ate = _periodo_descarga()
    if de is None:
        return jsonify({"error": "Período inválido."}), 400
    rel = descarga_service.relatorio(de, ate, _escopo_descarga())
    rel["pode_editar"] = bool(can_edit())
    # a lista de tipos fora da conta vale para todos os CDs: gerente altera
    rel["pode_editar_tipos"] = session.get("perfil") in ("admin", "master") and not somente_leitura()
    return jsonify(rel)


@app.route("/api/descarga/painel", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@bloquear_escrita
@rate_limited("descarga_painel", max_chamadas=10, janela_seg=60)
@csrf_required
def api_descarga_painel():
    """Upload do painel de entrada do WMS (um ou mais arquivos)."""
    if not can_edit():
        return jsonify({"ok": False, "error": "Sem permissão para carregar o painel."}), 403
    arquivos = [f for f in request.files.getlist("arquivo") if f and f.filename]
    if not arquivos:
        return jsonify({"ok": False, "error": "Selecione o painel de entrada do WMS."}), 400
    permitidas = set(_escopo_descarga(gravar=True))
    usuario = session.get("user", "")
    total = {"lidas": 0, "novas": 0, "atualizadas": 0, "ignoradas": 0}
    erros = []
    for f in arquivos[:10]:
        sufixo = Path(f.filename).suffix.lower()
        if sufixo not in (".xls", ".xlsx"):
            erros.append(f"{f.filename}: formato não aceito (use .xls ou .xlsx).")
            continue
        tmpdir = Path(tempfile.mkdtemp())
        try:
            caminho = tmpdir / f"entrada{sufixo}"
            f.save(caminho)
            registros = descarga_service.ler_painel_entrada(str(caminho))
        except ValueError as e:
            erros.append(f"{f.filename}: {e}")
            continue
        except Exception as e:
            print(f"[Descarga] Erro ao ler {f.filename}: {e}")
            erros.append(f"{f.filename}: não foi possível ler o arquivo.")
            continue
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        if permitidas:
            fora = [r for r in registros if r["empresa"] not in permitidas]
            total["ignoradas"] += len(fora)
            registros = [r for r in registros if r["empresa"] in permitidas]
        r = descarga_service.gravar(registros, usuario)
        for k in ("lidas", "novas", "atualizadas"):
            total[k] += r[k]
    # instruções do redespacho carregado agora também saem da conta
    try:
        df_red = _estado_usuario(_usuario_efetivo())["df_redespacho"]
        if df_red is not None and not df_red.empty and "Instrução Entrada" in df_red.columns:
            descarga_service.registrar_instrucoes_redespacho(df_red["Instrução Entrada"].tolist())
    except Exception as e:
        print(f"[Descarga] Instruções de redespacho não registradas: {e}")
    # o card passa a mostrar "em descarga"/"descarga concluída" já agora,
    # sem esperar o próximo ciclo do rastreio
    try:
        alvo = _usuario_efetivo()
        st = _estado_usuario(alvo)
        if not st["df_consolidado"].empty:
            st["df_consolidado"] = carretas_viagem.aplicar_no_df(st["df_consolidado"], alvo, "coligada")
    except Exception as e:
        print(f"[Descarga] Cards não atualizados após o painel: {e}")
    registrar_log(usuario, "carregar_painel_entrada", alvo=", ".join(f.filename for f in arquivos)[:200],
                  detalhe=f"lidas={total['lidas']} novas={total['novas']} "
                          f"atualizadas={total['atualizadas']} ignoradas={total['ignoradas']}")
    return jsonify({"ok": total["lidas"] > 0 or not erros, **total, "erros": erros})


@app.route("/api/descarga/tipos", methods=["POST"])
@login_required
@modulo_required("monitoramento")
@bloquear_escrita
@csrf_required
def api_descarga_tipos():
    """Tipos de movimentação que ficam FORA do tempo de descarga (redespacho)."""
    if session.get("perfil") not in ("admin", "master"):
        return jsonify({"ok": False, "error": "A lista vale para todos os CDs e é alterada pelo gerente."}), 403
    tipos = (request.json or {}).get("excluidos", [])
    if not isinstance(tipos, list):
        return jsonify({"ok": False, "error": "Lista inválida."}), 400
    salvos = descarga_service.salvar_tipos_excluidos([str(t)[:80] for t in tipos])
    registrar_log(session.get("user", ""), "descarga_tipos_excluidos", detalhe=" | ".join(salvos)[:300])
    return jsonify({"ok": True, "excluidos": salvos})


@app.route("/api/descarga/excel")
@login_required
@modulo_required("monitoramento")
@rate_limited("descarga_excel", max_chamadas=6, janela_seg=60)
def api_descarga_excel():
    de, ate = _periodo_descarga()
    if de is None:
        return jsonify({"error": "Período inválido."}), 400
    rel = descarga_service.relatorio(de, ate, _escopo_descarga())
    if not rel["itens"] and not rel["sem_chegada"] and not rel["em_aberto"]:
        return jsonify({"error": "Sem descargas no período."}), 400

    def _min(v):
        return "" if v is None else v

    viagens = pd.DataFrame([{
        "Aut. embarque / viagem": x["viagem"], "Placa": x["placa"], "Empresas": x["empresas"],
        "Instruções": " / ".join(i["instrucao"] for i in x["instrucoes"]),
        "Chegada no CD": x["chegada"], "Início desembarque": x["inicio"],
        "Término desembarque": x["termino"],
        "Aguardando (min)": _min(x["espera_min"]), "Desembarque (min)": _min(x["desembarque_min"]),
        "Tempo de descarga (min)": _min(x["total_min"]), "Tempo de descarga": x["total"],
        "Média de instruções": x["media_de"], "Tempo fora do CD (min)": x["fora_min"] or "",
        "Saídas e retornos": " | ".join(f"saiu {i['saiu']} · retornou {i['voltou']}" for i in x["idas"]),
    } for x in rel["itens"]])
    instrucoes = pd.DataFrame([{
        "Aut. embarque / viagem": x["viagem"], "Placa": x["placa"], "Instrução": i["instrucao"],
        "Empresa": i["empresa"], "Chegada no CD": x["chegada"], "Início desembarque": i["inicio"],
        "Término desembarque": i["termino"], "Aguardando (min)": _min(i["espera_min"]),
        "Desembarque (min)": _min(i["desembarque_min"]), "Tempo de descarga (min)": i["total_min"],
    } for x in rel["itens"] for i in x["instrucoes"]])
    sem = pd.DataFrame([{"Instrução": s["instrucao"], "Empresa": s["empresa"], "Placa": s["placa"],
                         "Início desembarque": s["inicio"], "Término desembarque": s["termino"],
                         "Motivo": s["motivo"]} for s in rel["sem_chegada"]])
    aberto = pd.DataFrame([{"Instrução": s["instrucao"], "Empresa": s["empresa"], "Placa": s["placa"],
                            "Início desembarque": s["inicio"], "Situação no WMS": s["status_op"]}
                           for s in rel["em_aberto"]])

    def gerar(caminho):
        with pd.ExcelWriter(caminho, engine="openpyxl") as w:
            for nome, d in (("Descargas", viagens), ("Instruções", instrucoes),
                            ("Sem chegada registrada", sem), ("Em aberto", aberto)):
                if d.empty:
                    continue
                _sanitizar_formula_excel(d).to_excel(w, index=False, sheet_name=nome)
                ws = w.sheets[nome]
                ws.freeze_panes = "A2"
                for col in ws.columns:
                    larg = max(len(str(c.value or "")) for c in col)
                    ws.column_dimensions[col[0].column_letter].width = min(max(12, larg + 2), 48)

    return _gerar_e_servir(f"Tempo_Descarga_{de}_a_{ate}.xlsx", gerar)


# ── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    import socket
    print("\n" + "="*54)
    print("  Monitoramento CD Demo — v31  (SQLite)")
    print("="*54)
    print(f"  Local:  http://localhost:5000")
    try:
        ip = socket.gethostbyname(socket.gethostname())
        print(f"  Rede:   http://{ip}:5000")
    except: pass
    print("  Banco:  data/monitoramento.db")
    print("="*54 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
