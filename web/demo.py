"""
Modo demonstração (DEMO_MODE=1).

A mesma aplicação que roda na operação, com três diferenças:

  1. ninguém precisa de login: quem abre o site entra como "visitante";
  2. só os módulos da demo ficam acessíveis (os outros dependem de
     integrações reais — rastreador, sensores, ERP — e saem da navegação);
  3. os dados são fictícios e gerados com as datas de hoje na primeira
     requisição de cada instância (demo/gerar_dados.py). O cálculo é o
     de produção: os arquivos de exemplo entram pelas MESMAS rotas de
     upload que o analista usa no dia a dia.

Nada aqui altera o comportamento com DEMO_MODE desligado.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
import secrets
import threading
import time
from pathlib import Path

from flask import (Blueprint, Response, jsonify, redirect, render_template,
                   request, send_file, session)

ATIVO = os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "sim", "yes")

USUARIO = "visitante"
SENHA = "demo"
NOME = "Visitante da demo"

# módulos que aparecem no menu lateral da demo
MODULOS_DEMO = ["monitoramento", "entregas", "xsense", "adensamento", "armazem", "fifo", "expedito"]

# rotas liberadas (prefixos). O resto devolve para a página da demo.
_LIBERADOS = (
    "/static", "/demo", "/favicon", "/robots.txt", "/login", "/api/login",
    # Abastecimento CD (painel das carretas, rota "/")
    "/api/dados", "/api/redespacho", "/api/processar", "/api/editar", "/api/itens_nf",
    "/api/monitoramento/fonte", "/api/cds/coordenadas", "/api/rota_maps", "/api/temperatura",
    "/api/romaneio_regra", "/api/preferencias", "/api/opentech/status", "/api/ravex/status",
    "/api/descarga", "/api/limpar", "/api/exportar_excel",
    "/entregas", "/api/entregas",
    "/xsense", "/api/xsense/camaras", "/api/xsense/dashboard", "/api/xsense/eventos",
    "/adensamento", "/api/adensamento",
    "/armazem", "/api/armazem",
    "/fifo", "/api/fifo",
    "/expedito", "/api/expedito",
    "/api/mapeamento",
    "/api/me", "/api/changelog", "/api/preferencia_ui", "/api/logout",
    "/saida",
)

demo_bp = Blueprint("demo", __name__)

_semeado = threading.Event()
_trava = threading.RLock()
_status = {"semeando": False, "erro": "", "segundos": 0.0, "em": ""}


# ── arquivos de exemplo ──────────────────────────────────────────────
def _pasta_planilhas() -> Path:
    from app.utils.paths import DATA_DIR
    p = DATA_DIR / "demo_planilhas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _planilhas() -> dict[str, Path]:
    """Gera (uma vez por dia) os arquivos de exemplo com as datas de hoje."""
    import sys
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    from demo import gerar_dados
    pasta = _pasta_planilhas()
    hoje = _hoje()
    marca = pasta / f".gerado_{hoje.isoformat()}"
    if not marca.exists():
        gerar_dados.gravar(pasta, hoje)
        marca.write_text("ok")
    return {p.name: p for p in pasta.iterdir() if p.is_file() and not p.name.startswith(".")}


def _hoje() -> _dt.date:
    from zoneinfo import ZoneInfo
    return _dt.datetime.now(ZoneInfo("America/Sao_Paulo")).date()


# ── usuário visitante ────────────────────────────────────────────────
def _garantir_usuario():
    from app.database import get_db, _criar_usuario_db
    with get_db() as db:
        row = db.execute("SELECT id FROM users WHERE username=?", (USUARIO,)).fetchone()
        if not row:
            _criar_usuario_db(db, USUARIO, SENHA, perfil="admin", nome_exibicao=NOME,
                              modulos_permitidos=",".join(MODULOS_DEMO))


def _abrir_sessao(sess):
    """Sessão do visitante — usada só pela carga inicial (test_client)."""
    sess.permanent = True
    sess["user"] = USUARIO
    sess["nome"] = NOME
    sess["perfil"] = "admin"
    sess["unidades_permitidas"] = ""
    sess["unidades_vinculadas"] = ""
    sess["modulos_permitidos"] = ",".join(MODULOS_DEMO)
    sess["operador_call_center"] = 0
    sess.setdefault("csrf_token", secrets.token_hex(32))


# ── carga inicial ────────────────────────────────────────────────────
def semear(app, forcar: bool = False) -> dict:
    """Passa os arquivos de exemplo pelas rotas reais de upload."""
    if (_semeado.is_set() and not forcar) or _status["semeando"]:
        return _status
    with _trava:
        if _semeado.is_set() and not forcar:
            return _status
        _status.update(semeando=True, erro="")
        t0 = time.time()
        try:
            _semear(app)
            _status["em"] = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")
        except Exception as exc:          # a demo abre mesmo se a carga falhar
            import traceback
            traceback.print_exc()
            _status["erro"] = str(exc)
        finally:
            _status.update(semeando=False, segundos=round(time.time() - t0, 1))
            _semeado.set()
    return _status


def _semear(app):
    from app.database import get_db
    _garantir_usuario()
    arqs = _planilhas()
    txt = arqs["produto_paletizado_ALFA.txt"].read_bytes()
    fup = arqs["follow_up_pedidos.xlsx"].read_bytes()

    cli = app.test_client()
    with cli.session_transaction() as s:
        _abrir_sessao(s)
        token = s["csrf_token"]
    h = {"X-CSRF-Token": token}

    def ok(resp, etapa):
        if resp.status_code >= 400:
            raise RuntimeError(f"{etapa}: HTTP {resp.status_code} {resp.get_data(as_text=True)[:300]}")
        return resp

    # a central de novidades da operação não aparece para o visitante
    from app.database import set_changelog_visto
    from web.server import CHANGELOG_VERSAO
    set_changelog_visto(USUARIO, CHANGELOG_VERSAO)

    # 0. Abastecimento CD: carretas faturadas para o CD e o rastreio delas
    from app.database import set_cd_usuario
    from demo.gerar_dados import CD_DEMO
    set_cd_usuario(USUARIO, CD_DEMO["nome"], CD_DEMO["latitude"], CD_DEMO["longitude"],
                   nome2=CD_DEMO["nome2"])
    ok(cli.post("/api/processar", headers=h, content_type="multipart/form-data",
                data={"estoque": (io.BytesIO(arqs["estoque_faturado.xlsx"].read_bytes()),
                                  "estoque_faturado.xlsx"),
                      "controle": (io.BytesIO(arqs["controle_distribuicao.xlsx"].read_bytes()),
                                   "controle_distribuicao.xlsx"),
                      "monitoramento": (io.BytesIO(arqs["painel_rastreio.xlsx"].read_bytes()),
                                        "painel_rastreio.xlsx")}),
       "painel das carretas")

    # histórico de temperatura do baú de cada carreta (o gráfico do card);
    # na operação é gravado a cada ciclo do rastreador
    from demo.gerar_dados import historico_temperatura_carretas
    from app.services.entregas_ravex import _agora as _agora_brt
    with get_db() as db:
        db.executemany("INSERT INTO historico_temperatura (placa, temperatura, timestamp) VALUES (?,?,?)",
                       historico_temperatura_carretas(_agora_brt()))

    # 0b. Entregas: viagens de distribuição do dia, com as notas, os itens e
    #     o trajeto. Na operação chegam pela extensão "Coletor" do Chrome e
    #     entram por estas mesmas funções de gravação.
    _semear_entregas()

    # 0c. Câmaras frias: leituras dos sensores dos últimos 30 dias. As das
    #     últimas horas passam pelo motor de detecção da operação
    #     (excursão sustentada / oscilação / transitório), que gera o estado
    #     de cada câmara e os eventos.
    _semear_camaras()

    # 1. Painel do Armazém: o local de devolução é classificado como tal
    #    (é o que alimenta o relatório de Devolução & Vencimento)
    ok(cli.post("/api/armazem/locais/classificar", json={"nome": "CD DEMO DEVOLUCAO",
                                                       "classificacao": "devolucao"}, headers=h),
       "classificar devolução")
    for nome in ("CD DEMO RESFRIADO", "CD DEMO CONGELADO"):
        ok(cli.post("/api/armazem/locais/classificar", json={"nome": nome, "classificacao": "varejo"},
                    headers=h), "classificar local")
    ok(cli.post("/api/armazem/capacidade", headers=h,
                json={"RESFRIADO": 140, "CONGELADO": 130, "AMBIENTE": 0,
                      "vigente_desde": _hoje().replace(day=1).isoformat()}),
       "capacidade das câmaras")
    ok(cli.post("/api/armazem/upload", headers=h, content_type="multipart/form-data",
                data={"arquivo": (io.BytesIO(txt), "produto_paletizado_ALFA.txt")}),
       "upload do armazém")

    # 2. Adensamento: cadastro de capacidade por código (caixas e peso
    #    máximos no palete), depois o mesmo TXT
    from demo.gerar_dados import PRODUTOS
    with get_db() as db:
        for cod, nome, temp, kg, cheio, _vida, _s in PRODUTOS:
            db.execute("""INSERT INTO adensamento_regras(usuario, codigo, nome, temperatura,
                                                         qtd_max, peso_max, completo)
                          VALUES(?,?,?,?,?,?,1)
                          ON CONFLICT DO NOTHING""",
                       ("", cod, nome, temp, cheio, round(cheio * (kg * 1.04 + 0.8) + 1, 1)))
    ok(cli.post("/api/adensamento/processar", headers=h, content_type="multipart/form-data",
                data={"relatorio": (io.BytesIO(txt), "produto_paletizado_ALFA.txt")}),
       "adensamento")

    # 3. Expedito: plano de abastecimento do picking para amanhã
    amanha = (_hoje() + _dt.timedelta(days=1)).isoformat()
    ok(cli.post("/api/expedito/picking", headers=h, content_type="multipart/form-data",
                data={"follow_up": (io.BytesIO(fup), "follow_up_pedidos.xlsx"),
                      "estoque": (io.BytesIO(txt), "produto_paletizado_ALFA.txt"),
                      "data": amanha}),
       "plano do picking")

    # 4. Expedito, acompanhamento da noite: o painel do WMS da noite que
    #    acabou de terminar (é o que a conversa com a IA analisa). Na demo a
    #    tela fica sempre nessa noite, sem virar para a seguinte às 18h.
    import json as _json
    with get_db() as db:
        db.execute("""INSERT INTO expedito_parametros(cd, dados) VALUES(?, ?)
                      ON CONFLICT(cd) DO UPDATE SET dados=excluded.dados""",
                   (USUARIO, _json.dumps({"hora_abertura_operacao": 24})))
    painel = arqs["painel_wms_noite.xlsx"].read_bytes()
    ok(cli.post("/api/expedito/noite", headers=h, content_type="multipart/form-data",
                data={"painel": (io.BytesIO(painel), "painel_wms_noite.xlsx"),
                      "turno": "noturno", "data": _hoje().isoformat()}),
       "painel da noite")


def _semear_entregas():
    from app.database import get_db
    from app.services import entregas_ravex as er
    from app.services.entregas_pnp import conexao_posicoes
    from app.utils.text_utils import normalize_plate
    from demo.gerar_dados import UNIDADE_ENTREGAS, entregas
    dados = entregas(er._agora())
    uid = UNIDADE_ENTREGAS["Id"]
    dia = dados["dia"].isoformat()
    agora = er._agora_txt()
    with get_db() as db:
        er.garantir_tabelas(db)
        er.gravar_catalogo(db, [UNIDADE_ENTREGAS])
        notas = er.ler_csv(dados["notas"], er.COLUNAS_NF, er.OBRIGATORIAS_NF)
        er.gravar_notas(db, notas, [uid], dia, dia)
        itens = er.ler_csv(dados["itens"], er.COLUNAS_ITENS, er.OBRIGATORIAS_ITENS)
        er.gravar_itens(db, itens, [uid], dia, dia)
        er.salvar_filtro(db, USUARIO, [uid])
        db.execute("INSERT OR REPLACE INTO entregas_unidade_coleta(unidade_id, desde) VALUES(?, ?)",
                   (uid, dia))
        try:
            db.execute("""INSERT OR REPLACE INTO ravex_coleta_tarefas(tarefa, feito_em, feito_por)
                          VALUES(?, ?, ?)""", (f"notas:{uid}", agora, "demo"))
            db.execute("""INSERT OR REPLACE INTO ravex_coleta_tarefas(tarefa, feito_em, feito_por)
                          VALUES(?, ?, ?)""", (f"itens:{uid}", agora, "demo"))
        except Exception:
            pass
    # situação da última coleta, como a extensão deixaria (fora do bloco
    # acima: set_preferencia abre outra conexão e o SQLite estaria travado)
    from app.database import set_preferencia
    set_preferencia(er.PREF_COLETA, {"em": agora, "ok": True, "ultima_ok_em": agora,
                                     "mensagem": "Demonstração: dados fictícios gerados para hoje",
                                     "periodo": [dia, dia], "unidades": [uid]})
    with conexao_posicoes() as cx:
        for placa, em, lat, lon, ignicao, temp in dados["posicoes"]:
            cx.execute("""INSERT INTO entregas_posicoes(placa, em, lat, lon, endereco, ignicao, visto_ate, temp)
                          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(placa, em) DO NOTHING""",
                       (normalize_plate(placa), em.strftime("%Y-%m-%d %H:%M:%S"), lat, lon, "",
                        ignicao, "", temp))


def _semear_camaras():
    from app.database import get_db
    from app.services.entregas_ravex import _agora as _agora_brt
    from demo.gerar_dados import CAMARAS, leituras_camaras
    from web.xsense import processar_leitura
    leituras = leituras_camaras(_agora_brt())
    corte = 12 * 6                       # últimas 12 h (leitura a cada 10 min)
    with get_db() as db:
        for cid, nome, tipo, tmin, tmax, duro, _alvo, _r in CAMARAS:
            db.execute("""INSERT OR REPLACE INTO xsense_camaras
                            (id, nome, rotulo, provider, ativo, alertas_ativos, tipo, local, temp_min, temp_max,
                             temp_limite_duro, n_sustentado, k_oscilacao, w_janela_min, cooldown_min, m_recuperacao)
                          VALUES (?,?,?,?,1,1,?,?,?,?,?,2,4,120,60,2)""",
                       (cid, nome, cid, "sensores", tipo, "CDA", tmin, tmax, duro))
            db.executemany("INSERT OR IGNORE INTO xsense_leituras(camara_id, dimensao, ts, valor) "
                           "VALUES (?, 'temperatura', ?, ?)",
                           [(cid, ts, v) for ts, v in leituras[cid][:-corte]])
    for cid, *_ in CAMARAS:
        for ts, v in leituras[cid][-corte:]:
            processar_leitura(cid, "temperatura", v, ts)


# ── integração com a aplicação ───────────────────────────────────────
def instalar(app):
    if not ATIVO:
        return
    app.register_blueprint(demo_bp)
    app.jinja_env.globals["DEMO_MODE"] = True

    @app.before_request
    def _demo_antes():
        caminho = request.path or "/"
        if caminho.startswith("/static"):
            return None
        if caminho in ("/api/index", "/api/index.py"):
            return redirect("/")
        # a carga dos dados de exemplo acontece na primeira visita (inclusive
        # na tela de login, para os dados estarem prontos ao entrar). A carga
        # usa o test_client, que passa por aqui de novo: não reentra.
        if not _semeado.is_set() and not _status["semeando"] and not caminho.startswith("/demo/status"):
            _garantir_usuario()
            semear(app)
        # Espelhar outro usuário não faz sentido na demo (só existe o
        # visitante): a lista volta vazia em vez de erro
        if caminho == "/api/espelho/usuarios":
            return jsonify({"usuarios": []})
        if caminho.startswith("/api/espelho/"):
            return jsonify({"ok": True})
        # A exportação em imagem usa um navegador headless (Playwright),
        # que não existe na hospedagem da demo
        if caminho in ("/api/armazem/exportar", "/api/exportar_imagem"):
            return jsonify({"error": "A exportação em imagem não está disponível na demonstração. "
                                     "Use o Excel."}), 400
        if caminho.startswith("/xsense/miniapp"):          # mini-app do Telegram: fora da demo
            return redirect("/xsense")
        if caminho.startswith(("/api/xsense/destinatarios", "/api/xsense/resumo")):
            return jsonify({"destinatarios": [], "pendentes": [], "texto": "",
                            "aviso": "Alertas por Telegram ficam fora da demonstração."})
        if caminho != "/" and not caminho.startswith(_LIBERADOS):
            if caminho.startswith("/api/"):
                return jsonify({"error": "Esta função não faz parte da demonstração."}), 404
            return redirect("/")
        # Rede de segurança: se a sessão se perder entre instâncias da
        # hospedagem, o visitante continua no módulo em vez de voltar ao login.
        # A entrada pela raiz continua sendo a tela de login.
        if (not session.get("user") and caminho not in ("/", "/login", "/api/login", "/api/logout")
                and not caminho.startswith("/demo")):
            _garantir_usuario()
            _abrir_sessao(session)
        return None

    @app.after_request
    def _demo_faixa(resp):
        # Entregas: na operação o aviso de "nenhuma extensão coletando" é
        # importante; na demo não existe extensão, os dados são gerados
        if request.path == "/api/entregas/estado" and resp.is_json:
            try:
                d = resp.get_json()
                d["sem_cobertura"] = []
                resp.set_data(__import__("json").dumps(d, ensure_ascii=False))
            except Exception:
                pass
        try:
            if (resp.mimetype == "text/html" and resp.status_code == 200
                    and not request.path.startswith("/demo") and not resp.direct_passthrough):
                html = resp.get_data(as_text=True)
                if "</body>" in html and "demo-faixa" not in html:
                    resp.set_data(html.replace("</body>", _FAIXA + "</body>", 1))
        except Exception:
            pass
        return resp


_FAIXA = """
<style>
  .demo-projeto{margin:0 10px 12px;padding:10px;border-radius:12px;background:rgba(0,122,255,.07);
                border:1px solid rgba(0,122,255,.18)}
  .demo-projeto .rot{font-size:10.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;
                     color:#64748b;margin:0 2px 6px}
  .demo-projeto a{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:9px;
                  text-decoration:none;color:inherit;font-size:13.5px;font-weight:600}
  .demo-projeto a:hover{background:rgba(0,122,255,.08)}
  .demo-projeto a.on{background:#007aff;color:#fff}
  .demo-projeto a small{display:block;font-weight:500;font-size:11px;opacity:.75}
</style>
<script>
document.addEventListener('DOMContentLoaded', function(){
  var nav = document.getElementById('sidebar-nav');
  if(!nav || document.getElementById('demo-projeto')) return;
  var box = document.createElement('div');
  box.id = 'demo-projeto'; box.className = 'demo-projeto';
  box.innerHTML = '<div class="rot">Projeto atual</div>' +
    '<a class="on" href="/">🚛<span>Monitoramento CDs<small>recebimento, entregas, armazém, expedição</small></span></a>' +
    '<a href="/demo/coletor">📱<span>Operação expedição<small>app do coletor: separação de redespacho</small></span></a>';
  nav.parentNode.insertBefore(box, nav);
});
</script>
<style>#btn-destinatarios,#btn-sincronizar,#btn-coletar-agora,#btn-importar-historico-todas,#sidebar-config-wrap,#btn-espelhar,#btn-exportar,[onclick^="abrirEspelho"],[onclick^="abrirColetor"]{display:none!important}</style>
<a id="demo-faixa" href="/demo" title="Sobre esta demonstração"
   style="position:fixed;right:14px;bottom:14px;z-index:99999;background:#111827;color:#fff;
          padding:8px 14px;border-radius:999px;font:600 12.5px system-ui,sans-serif;
          text-decoration:none;box-shadow:0 4px 14px rgba(0,0,0,.25);opacity:.92">
  Demo &middot; dados fict&iacute;cios &middot; sobre o projeto
</a>
"""


# ── páginas da demo ──────────────────────────────────────────────────
@demo_bp.route("/demo")
def pagina():
    from flask import current_app
    if not _semeado.is_set() and not _status["semeando"]:
        semear(current_app._get_current_object())
    return render_template("demo.html", status=_status, hoje=_hoje().strftime("%d/%m/%Y"),
                           repo=os.environ.get("DEMO_REPO_URL", "https://github.com/giancarloabreu/cd-demo-logistica-ia"))


@demo_bp.route("/demo/coletor")
def coletor():
    """Simulador do app Android do coletor (Separação Redespacho): roda todo
    no navegador, sem o servidor Kotlin — as regras de leitura são as do app."""
    return render_template("coletor.html")


@demo_bp.route("/demo/status")
def status():
    return jsonify({**_status, "pronto": _semeado.is_set()})


@demo_bp.route("/demo/planilhas/<nome>")
def baixar(nome):
    arqs = _planilhas()
    if nome not in arqs:
        return jsonify({"error": "arquivo não encontrado"}), 404
    return send_file(arqs[nome], as_attachment=True, download_name=nome)


@demo_bp.route("/demo/recarregar", methods=["POST"])
def recarregar():
    from flask import current_app
    tok = request.headers.get("X-CSRF-Token", "")
    if not tok or tok != session.get("csrf_token"):
        return jsonify({"error": "Requisição inválida."}), 403
    return jsonify(semear(current_app._get_current_object(), forcar=True))
