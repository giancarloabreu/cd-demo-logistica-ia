"""
Módulo "Monitoramento de Entregas" (13/11/2026).

Um veículo (viagem) por linha e uma bolinha por entrega (cliente na
viagem), com a cor da situação das notas e anel branco para baixa fora do
raio do cliente. Regras e coleta em app/services/entregas_ravex.py.

A tela nunca chama a Ravex: lê o banco, atualizado no horário e no
intervalo dos Parâmetros (padrão 06:00 às 20:00, a cada 5 min). Desde
15/11/2026 quem atualiza é a extensão do Chrome "Coletor Ravex" dos
operadores (app/services/entregas_coletor.py, rotas em
web/entregas_coleta.py); a coleta pela VM (scripts/atualizar_ravex.py)
fica desligada enquanto o Cloudflare da Ravex barrar o servidor. O filtro
de unidades é de cada login (lembra a última escolha) e define o que é
coletado; o filtro de veículos muda a tela, nunca a coleta.

Decorators de auth duplicados de propósito (mesmo padrão de validacao.py)
— evita import circular com server.py.
"""
from __future__ import annotations

import datetime as _dt
import hmac
import io
import zipfile
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, session

from app.database import get_db, registrar_log
from app.services import entregas_coletor as ec
from app.services import entregas_ravex as er
from app.services import ravex_posicoes
from app.services import ravex_sessao
from app.utils.access_control import modulo_required, pode_ver_localizacao_exata

entregas_bp = Blueprint("entregas", __name__)

_TZ_BRT = _dt.timezone(_dt.timedelta(hours=-3))
MAX_DIAS_PERIODO = 62
SITUACOES = ("todas", "abertas", "finalizadas")
DIR_EXTENSAO = Path(__file__).parent.parent / "extensao" / "coletor-ravex"


def init_entregas_db() -> None:
    from app.services.entregas_pnp import conexao_posicoes
    with get_db() as db:
        er.garantir_tabelas(db)
        ec.garantir_tabelas(db)
    # histórico de posições: banco próprio (data/entregas_posicoes.db)
    with conexao_posicoes():
        pass


# ── auth ─────────────────────────────────────────────────────────────
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


def _usuario() -> str:
    """Filtro salvo por login (não pela conta canônica do CD): cada pessoa
    lembra a própria escolha."""
    return str(session.get("user", "")).strip().lower()


def _eh_gestor() -> bool:
    return session.get("perfil") in ("admin", "master")


def _siglas_do_usuario() -> list[str]:
    raw = ",".join([session.get("unidades_vinculadas", "") or "", session.get("unidades_permitidas", "") or ""])
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _siglas_visiveis() -> set[str] | None:
    """Unidades que a sessão pode VER — mesma regra do Mapa de Estoque,
    da Produtividade e do Xsense (revisão de 13/11/2026): admin/master
    enxergam todas; os demais, as siglas de `unidades_permitidas` (que já
    incluem as vinculadas); vazio = todas, convenção do cadastro."""
    if _eh_gestor():
        return None
    raw = session.get("unidades_permitidas", "") or ""
    s = {u.strip().upper() for u in raw.split(",") if u.strip()}
    return s or None


def _unidades_visiveis(db) -> list[dict]:
    vis = _siglas_visiveis()
    return [u for u in er.listar_unidades(db) if vis is None or (u["sigla"] or "").upper() in vis]


def _so_visiveis(db, ids: list[int]) -> list[int]:
    if _siglas_visiveis() is None:
        return ids
    ok = {u["id"] for u in _unidades_visiveis(db)}
    return [i for i in ids if i in ok]


def _coleta_visivel(db) -> dict:
    """Estado da coleta sem as unidades que a sessão não enxerga (a linha
    de status mostrava o id cru das unidades de outros CDs)."""
    c = er.estado_coleta()
    c["unidades"] = _so_visiveis(db, list(c.get("unidades") or []))
    return c


def _hoje() -> str:
    return _dt.datetime.now(_TZ_BRT).strftime("%Y-%m-%d")


def _data(s: str) -> str | None:
    try:
        return _dt.date.fromisoformat((s or "").strip()).isoformat()
    except ValueError:
        return None


def _ids(texto: str) -> list[int]:
    out = []
    for p in (texto or "").split(","):
        p = p.strip()
        if p.isdigit() and int(p) not in out:
            out.append(int(p))
    return out[:80]


# ── página ───────────────────────────────────────────────────────────
@entregas_bp.route("/entregas")
@modulo_required("entregas")
def pagina():
    return render_template("entregas.html", user=session.get("user"))


# ── API ──────────────────────────────────────────────────────────────
@entregas_bp.route("/api/entregas/estado")
@login_required
@modulo_required("entregas")
def api_estado():
    usuario = _usuario()
    with get_db() as db:
        catalogo = bool(er.listar_unidades(db))
        unidades = _unidades_visiveis(db)
        sel = er.filtro_do_usuario(db, usuario)
        primeira_vez = sel is None
        if primeira_vez:
            padrao = _so_visiveis(db, er.unidades_padrao(db, _siglas_do_usuario()))
            sel = er.salvar_filtro(db, usuario, padrao) if padrao else []
        else:
            visiveis = _so_visiveis(db, sel)
            # permissão reduzida desde a última escolha (conferido contra o
            # catálogo: sem nenhuma unidade visível o filtro também é reduzido)
            if catalogo and visiveis != sel:
                sel = er.salvar_filtro(db, usuario, visiveis)
            else:
                er.registrar_acesso(db, usuario)
        coletadas = er.unidades_coletadas(db)
        coleta = _coleta_visivel(db)
        placas = er.placas_do_usuario(db, usuario)
        vm = ec.situacao_vm()
        # coleta pela extensão do Chrome (15/11/2026): quando cada unidade foi
        # atualizada e quais ficaram sem extensão no ar
        frescor = {str(u): f for u, f in ec.frescor_unidades(db, sel).items()}
        descobertas = [] if vm["ativo"] else ec.sem_cobertura(db, sel)
        meu = ec.situacao_coletor(db, usuario)
        pedidos = {} if vm["ativo"] else {str(u): em for u, em in ec.pedidos_pendentes(db, sel).items()}
    par = er.parametros()
    out = {
        "unidades": unidades,
        "selecionadas": sel,
        "placas": placas,
        "primeira_vez": primeira_vez,
        "coletadas": sorted(coletadas),
        "coleta": coleta,
        "coleta_vm": vm["ativo"],
        "frescor": frescor,
        "pedidos": pedidos,
        "sem_cobertura": descobertas,
        "meu_coletor": {k: meu.get(k) for k in ("conectado", "online", "situacao", "erro", "erro_em",
                                                "erro_recente", "pausa_ate", "visto_em")},
        "catalogo_vazio": not catalogo,
        "hoje": _hoje(),
        "intervalo_min": par["intervalo_min"],
        "parametros": par,
        "no_horario": er.no_horario(par),
        "pode_editar": _eh_gestor(),
        "catalogo": er.situacao_catalogo(),
        "hora": _dt.datetime.now(_TZ_BRT).strftime("%H:%M"),
    }
    if _eh_gestor():
        out["sessao_ravex"] = ravex_sessao.situacao()
        out["parametros_alterados"] = er.alteracao_parametros()
        # a parada não prevista é medida pela grade de posição: o intervalo
        # dela (Configurações › Ravex) aparece junto do parâmetro da PNP
        from app.database import get_ravex_config
        out["intervalo_grade_min"] = get_ravex_config().get("intervalo_min", 20)
    return jsonify(out)


@entregas_bp.route("/api/entregas/filtro", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_filtro():
    bruto = (request.get_json(silent=True) or {}).get("unidades") or []
    if not isinstance(bruto, list):
        return jsonify({"error": "Lista de unidades inválida."}), 400
    ids = _ids(",".join(str(x) for x in bruto))
    with get_db() as db:
        sel = er.salvar_filtro(db, _usuario(), _so_visiveis(db, ids))
        coletadas = er.unidades_coletadas(db)
    return jsonify({"ok": True, "selecionadas": sel,
                    "novas": [u for u in sel if u not in coletadas]})


@entregas_bp.route("/api/entregas/atualizar", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_atualizar():
    """Pede a atualização das unidades que a pessoa está vendo (pedido do
    usuário 15/11/2026: ao entrar, ver a situação do momento). Chamado ao
    abrir o painel e pelo botão Atualizar; a próxima conferência de uma
    extensão no ar (até 30 s) consulta a Ravex."""
    bruto = (request.get_json(silent=True) or {}).get("unidades") or []
    if not isinstance(bruto, list):
        return jsonify({"error": "Lista de unidades inválida."}), 400
    ids = _ids(",".join(str(x) for x in bruto[:200]))
    if ec.vm_coleta_ativa():
        return jsonify({"ok": True, "pedidas": [], "recentes": [], "coleta_vm": True})
    with get_db() as db:
        sel = er.filtro_do_usuario(db, _usuario()) or []
        alvo = [u for u in _so_visiveis(db, ids) if u in sel]
        r = ec.pedir_atualizacao(db, alvo)
        r["sem_cobertura"] = ec.sem_cobertura(db, r["pedidas"])
    return jsonify({"ok": True, **r})


@entregas_bp.route("/api/entregas/parametros", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_parametros():
    """Horário e intervalos da coleta (pedido do usuário 14/11/2026: nada
    fixo no código). Gerente (admin/master) edita; vale para todos."""
    if not _eh_gestor():
        return jsonify({"error": "Parâmetros editados pelo gerente."}), 403
    d = request.get_json(silent=True) or {}
    p, erro = er.salvar_parametros(d, _usuario())
    if not p:
        return jsonify({"error": erro}), 400
    registrar_log(session.get("user", ""), "config_entregas",
                  detalhe=f"coleta {p['inicio']}-{p['fim']} a cada {p['intervalo_min']} min; "
                          f"produtos a cada {p['intervalo_itens_min']} min; "
                          f"PNP {p['pnp_min']} min fora de {p['pnp_raio_m']} m do cliente")
    return jsonify({"ok": True, "parametros": p, "no_horario": er.no_horario(p),
                    "parametros_alterados": er.alteracao_parametros()})


@entregas_bp.route("/api/entregas/catalogo", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_pedir_catalogo():
    """Lista de unidades atualizada na próxima coleta (a tela nunca chama
    a Ravex; o coletor faz isso no ciclo dele)."""
    if not _eh_gestor():
        return jsonify({"error": "Atualização da lista feita pelo gerente."}), 403
    er.pedir_catalogo(_usuario())
    return jsonify({"ok": True, "catalogo": er.situacao_catalogo()})


@entregas_bp.route("/api/entregas/painel")
@login_required
@modulo_required("entregas")
def api_painel():
    hoje = _hoje()
    de = _data(request.args.get("de", "")) or hoje
    ate = _data(request.args.get("ate", "")) or de
    if ate < de:
        de, ate = ate, de
    if (_dt.date.fromisoformat(ate) - _dt.date.fromisoformat(de)).days > MAX_DIAS_PERIODO:
        return jsonify({"error": f"Período máximo de {MAX_DIAS_PERIODO} dias."}), 400
    situacao = request.args.get("situacao", "todas")
    if situacao not in SITUACOES:
        situacao = "todas"
    busca = (request.args.get("busca") or "").strip()[:40]
    with get_db() as db:
        if "unidades" in request.args:
            unidades = _ids(request.args.get("unidades", ""))
        else:
            unidades = er.filtro_do_usuario(db, _usuario()) or []
        unidades = _so_visiveis(db, unidades)
        # a atualização automática da tela mantém o filtro na coleta
        er.registrar_acesso(db, _usuario(), minimo_seg=3600)
        # Fecha a gravação antes da consulta: o UPDATE acima abre uma
        # transação de escrita, e a análise de PNP de um período longo leva
        # segundos — sem o commit a grade da extensão (cron) esperava a trava
        # e falhava com "database is locked" (revisão 15/11/2026).
        db.commit()
        placas = er.placas_do_usuario(db, _usuario())
        r = er.consultar_painel(db, unidades, de, ate, situacao, busca, placas)
        coleta = _coleta_visivel(db)
    r.update({"de": de, "ate": ate, "situacao": situacao, "coleta": coleta, "placas": placas})
    return jsonify(r)


@entregas_bp.route("/api/entregas/veiculos", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_veiculos():
    """Filtro de veículos do login (lista vazia = todos)."""
    bruto = (request.get_json(silent=True) or {}).get("placas")
    if not isinstance(bruto, list):
        return jsonify({"error": "Lista de veículos inválida."}), 400
    with get_db() as db:
        placas = er.salvar_placas(db, _usuario(), [str(x) for x in bruto][:er.MAX_PLACAS_FILTRO * 2])
    return jsonify({"ok": True, "placas": placas})


# ── Minha conta Ravex: extensão do Chrome (15/11/2026) ───────────────
@entregas_bp.route("/api/entregas/coletor")
@login_required
@modulo_required("entregas")
def api_coletor():
    with get_db() as db:
        out = {"meu": ec.situacao_coletor(db, _usuario()), "coleta_vm": ec.situacao_vm(),
               "pode_editar": _eh_gestor()}
        if _eh_gestor():
            out["coletores"] = ec.listar_coletores(db)
    return jsonify(out)


@entregas_bp.route("/api/entregas/coletor/codigo", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_coletor_codigo():
    """Nova chave de coleta do login. A tela monta o código de conexão com o
    endereço do sistema que o operador está usando; a chave anterior deixa
    de valer."""
    with get_db() as db:
        chave = ec.gerar_chave(db, _usuario())
    registrar_log(session.get("user", ""), "coletor_ravex", detalhe="código de conexão da extensão gerado")
    return jsonify({"ok": True, "chave": chave, "prefixo": ec.PREFIXO_CODIGO})


@entregas_bp.route("/api/entregas/coletor/desconectar", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_coletor_desconectar():
    d = request.get_json(silent=True) or {}
    alvo = str(d.get("usuario") or "").strip().lower() or _usuario()
    if alvo != _usuario() and not _eh_gestor():
        return jsonify({"error": "Desconexão de outro login feita pelo gerente."}), 403
    with get_db() as db:
        ec.revogar(db, alvo)
    registrar_log(session.get("user", ""), "coletor_ravex", detalhe=f"extensão desconectada ({alvo})")
    return jsonify({"ok": True})


@entregas_bp.route("/api/entregas/coletor/grade", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_coletor_grade():
    """Extensão que também coleta a grade de posição (Abastecimento CD)."""
    if not _eh_gestor():
        return jsonify({"error": "Definido pelo gerente."}), 403
    d = request.get_json(silent=True) or {}
    alvo = str(d.get("usuario") or "").strip().lower() or _usuario()
    with get_db() as db:
        if not ec.definir_grade(db, alvo, bool(d.get("ativo"))):
            return jsonify({"error": "Extensão não conectada para este login."}), 404
    registrar_log(session.get("user", ""), "coletor_ravex",
                  detalhe=f"grade de posição {'ligada' if d.get('ativo') else 'desligada'} ({alvo})")
    return jsonify({"ok": True})


@entregas_bp.route("/api/entregas/coleta-servidor", methods=["POST"])
@login_required
@modulo_required("entregas")
@csrf_required
def api_coleta_servidor():
    """Liga/desliga a coleta pela VM (desligada desde 15/11/2026: o
    Cloudflare da Ravex barra o IP do servidor)."""
    if not _eh_gestor():
        return jsonify({"error": "Definido pelo gerente."}), 403
    ativo = bool((request.get_json(silent=True) or {}).get("ativo"))
    vm = ec.definir_vm_coleta(ativo, _usuario())
    registrar_log(session.get("user", ""), "config_entregas",
                  detalhe=f"coleta pelo servidor {'ligada' if ativo else 'desligada'}")
    return jsonify({"ok": True, "coleta_vm": vm})


@entregas_bp.route("/api/entregas/extensao.zip")
@login_required
@modulo_required("entregas")
def api_extensao_zip():
    """Pacote da extensão para instalar no Chrome (Carregar sem compactação)."""
    if not DIR_EXTENSAO.is_dir():
        return jsonify({"error": "Pacote da extensão indisponível no servidor."}), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(DIR_EXTENSAO.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                z.write(p, Path("coletor-ravex") / p.relative_to(DIR_EXTENSAO))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="coletor-ravex.zip")


@entregas_bp.route("/api/entregas/nf")
@login_required
@modulo_required("entregas")
def api_buscar_nf():
    termo = (request.args.get("termo") or "").strip()[:60]
    if not any(c.isdigit() for c in termo):
        return jsonify({"error": "Informe o número ou a chave da nota fiscal."}), 400
    with get_db() as db:
        if "unidades" in request.args:
            unidades = _ids(request.args.get("unidades", ""))
        else:
            unidades = er.filtro_do_usuario(db, _usuario()) or []
        achados = er.buscar_nf(db, _so_visiveis(db, unidades), termo)
    return jsonify({"resultados": achados})


@entregas_bp.route("/api/entregas/entrega")
@login_required
@modulo_required("entregas")
def api_entrega():
    viagem = (request.args.get("viagem") or "").strip()[:40]
    cliente = (request.args.get("cliente") or "").strip()[:120]
    if not viagem or not cliente:
        return jsonify({"error": "Informe a viagem e o cliente."}), 400
    with get_db() as db:
        v = db.execute("SELECT unidade_id FROM entregas_viagens WHERE id_viagem=?", (viagem,)).fetchone()
        restrito = _siglas_visiveis() is not None
        # viagem sem unidade reconhecida (nome fora do catálogo) fica fechada
        # para quem tem restrição de unidade
        if not v or (restrito and (v["unidade_id"] is None or not _so_visiveis(db, [v["unidade_id"]]))):
            return jsonify({"error": "Entrega não encontrada."}), 404
        d = er.detalhe_entrega(db, viagem, cliente)
        traj = er.trajeto_viagem(db, viagem) if d else None
    if not d:
        return jsonify({"error": "Entrega não encontrada."}), 404
    d["mapa"]["veiculo"] = _posicao_do_veiculo(d["viagem"]["placa"], d["viagem"]["aberta"], d["viagem"]["corrente"])
    d["trajeto"] = _trajeto_visivel(traj)
    return jsonify(d)


@entregas_bp.route("/api/entregas/viagem")
@login_required
@modulo_required("entregas")
def api_viagem():
    """Dados da viagem (bandeiras de início e fim e triângulo de PNP da
    lista, pedido do usuário 15/11/2026): horários, local do início e do
    fim, paradas não previstas e o mapa com os clientes."""
    viagem = (request.args.get("viagem") or "").strip()[:40]
    if not viagem:
        return jsonify({"error": "Informe a viagem."}), 400
    with get_db() as db:
        v = db.execute("SELECT * FROM entregas_viagens WHERE id_viagem=?", (viagem,)).fetchone()
        restrito = _siglas_visiveis() is not None
        if not v or (restrito and (v["unidade_id"] is None or not _so_visiveis(db, [v["unidade_id"]]))):
            return jsonify({"error": "Viagem não encontrada."}), 404
        corrente = er.viagem_corrente(db, v)
        mapa = er.pontos_da_viagem(db, viagem, corrente)
        traj = er.trajeto_viagem(db, viagem)
        resumo = er.resumo_viagem(v, corrente)
        # clique na placa (16/11/2026): temperatura e situação de agora
        agora = er.agora_da_viagem(db, v, mapa["entregas"], (traj or {}).get("pnp") or [], corrente)
    ents = mapa.pop("entregas")
    mapa["veiculo"] = _posicao_do_veiculo(resumo["placa"], resumo["aberta"], resumo["corrente"])
    return jsonify({"viagem": resumo, "sequencia": ents, "mapa": mapa, "trajeto": _trajeto_visivel(traj),
                    "agora": agora, "total": len(ents), "realizadas": sum(1 for e in ents if e["realizada"])})


def _trajeto_visivel(t: dict | None) -> dict:
    """Pontas e PNPs da viagem; sem latitude, longitude e endereço para
    quem não vê localização exata (Operador de armazém — regra de
    24/08/2026). Os horários e a duração das paradas seguem visíveis."""
    t = dict(t or {"pnp": [], "inicio": None, "fim": None})
    if pode_ver_localizacao_exata():
        t["restrito"] = False
        return t

    def limpa(d):
        return {k: v for k, v in d.items() if k not in ("lat", "lon", "endereco")} if d else d
    t.update({"inicio": limpa(t.get("inicio")), "fim": limpa(t.get("fim")),
              "pnp": [limpa(p) for p in t.get("pnp") or []], "restrito": True})
    return t


def _posicao_do_veiculo(placa: str, aberta: bool, corrente: bool) -> dict:
    """Posição do veículo no mapa do detalhe (pedido do usuário 15/11/2026),
    da grade de posição da Ravex. Operador de armazém não recebe latitude e
    longitude de veículo (regra de 24/08/2026, pode_ver_localizacao_exata;
    operador de call center recebe, 15/11/2026). Viagem finalizada, ou
    aberta na Ravex com o veículo já em viagem posterior ou fora da janela
    acompanhada (er.viagem_corrente), não mostra a posição atual: o veículo
    pode estar atendendo outra viagem, até de outra unidade (revisão
    15/11/2026)."""
    if not pode_ver_localizacao_exata():
        return {"situacao": "restrito"}
    if not aberta:
        return {"situacao": "finalizada"}
    if not corrente:
        return {"situacao": "outra_viagem"}
    p = ravex_posicoes.posicao(placa)
    if not p:
        return {"situacao": "sem_posicao", "grade_em": ravex_posicoes.grade_em()}
    try:
        idade = (er._agora() - _dt.datetime.strptime(p["em"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    except ValueError:
        idade = None
    return {"situacao": "ok", "lat": p["lat"], "lon": p["lon"], "em": p["em"], "grade_em": p.get("grade_em", ""),
            "idade_min": None if idade is None else max(0, round(idade)), "endereco": p.get("endereco", ""),
            "ignicao": p.get("ignicao", "")}
