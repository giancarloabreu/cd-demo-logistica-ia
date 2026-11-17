"""
Coleta da Ravex pela extensão do Chrome "Coletor Ravex" (15/11/2026).

Por quê: a Ravex fica atrás do Cloudflare, que barra o IP da VM (nuvem) em
todas as consultas — Python e Chromium headless (incidente de 11/11/2026).
Do navegador do operador, em rede comum, a mesma consulta passa. A
extensão roda no Chrome do operador, usa a sessão da Ravex que ele já tem
aberta (lê o token da própria página; nunca a senha) e entrega o
resultado cru ao servidor. Nada de evasão: é a sessão real do operador,
no ritmo dos Parâmetros da tela.

Divisão do trabalho:
- o SERVIDOR decide o que coletar (`planejar`, rota /api/coletor/plano) e
  grava pelo mesmo caminho da coleta antiga (entregas_ravex.ler_csv,
  gravar_notas, gravar_itens, gravar_catalogo);
- a EXTENSÃO executa as consultas de leitura permitidas (lista de
  unidades, grade de posição, relatórios de notas e de itens e, desde a
  versão 1.1.0, a Grid Operacional com a eficiência do frio) e devolve o
  resultado (`receber`, rota /api/coletor/resultado).

Sem posse por unidade (decisão do usuário 15/11/2026: vários operadores
cuidam de veículos diferentes da mesma unidade). O relatório da Ravex vem
por unidade inteira e a gravação é UPSERT, então qualquer operador que
puxa a unidade traz todos os veículos dela. O que evita carga repetida é
o FRESCOR: a unidade só é pedida a um operador quando ninguém a atualizou
dentro do intervalo, e a reserva (6 min) impede que duas extensões peçam a
mesma unidade no mesmo minuto.

Grade de posição (Abastecimento CD): a extensão marcada com "grade" (conta
principal, logada o dia todo) manda a grade; o servidor guarda o JSON em
data/ravex/entrada/ e o cron da Ravex (scripts/atualizar_ravex.py) aplica
no painel — o processamento (cidade por coordenada, presença das
carretas, avisos) fica fora do Gunicorn, como sempre foi.

Coleta pela VM: desligada por padrão (preferência `ravex_vm_coleta_ativa`),
sem apagar o código; volta a valer se a Ravex liberar o IP da VM.
"""
from __future__ import annotations

from app.utils.paths import DATA_DIR as _DATA_DIR
import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from app.database import get_db, get_preferencia, get_ravex_config, set_preferencia
from app.services import entregas_ravex as er

PREFIXO_CODIGO = "MCD1."            # código de conexão = MCD1.<base64url {"s": servidor, "k": chave}>
RESERVA_SEG = 6 * 60                # tarefa entregue a uma extensão fica reservada a ela
ESPERA_APOS_ERRO_SEG = 5 * 60       # erro da própria tarefa (formato, recusa): nova tentativa depois disso
ONLINE_SEG = 3 * 60                 # extensão sem contato há mais que isso = fora do ar
FOLGA_SEG = 30                      # extensão confere a cada 30 s: intervalo de 5 min não pode virar 5,5
MIN_DEVIDA_SEG = 30                 # piso do intervalo (Parâmetros de 1 min = toda conferência da extensão)
PEDIDO_MIN_SEG = 60                 # atualização pedida na tela: unidade atualizada há menos que isso fica
PEDIDO_VALIDADE_SEG = 10 * 60       # pedido sem extensão no ar perde a validade
MAX_UNIDADES_TAREFA = 60
MAX_LINHAS_LISTA = 20000
PAUSA_POR_ERRO = {"bloqueio": 600, "limite": 600, "sessao": 120}   # pausa a extensão inteira
# erro que espera ESPERA_APOS_ERRO_SEG na própria tarefa; "rede" inclui o tempo
# limite e o HTTP 5xx da Ravex (sem espera, o relatório pesado voltaria a
# cada minuto). Os demais (sessao, bloqueio, limite) pausam a extensão.
ERROS_DA_TAREFA = ("formato", "recusa", "tamanho", "interno", "rede")
ROTULO_ERRO = {
    "sessao": "Sessão da Ravex encerrada neste navegador.",
    "bloqueio": "A Ravex recusou a consulta (bloqueio de segurança).",
    "limite": "A Ravex pediu pausa nas consultas (limite de requisições).",
    "recusa": "A Ravex recusou a consulta.",
    "formato": "A Ravex respondeu fora do formato esperado.",
    "rede": "Falha de comunicação com a Ravex.",
    "tamanho": "Resposta da Ravex acima do limite.",
    "interno": "Erro inesperado na extensão.",
}
SITUACOES_RAVEX = ("ok", "sem_aba", "sem_sessao", "ocupado", "teste")
TIPOS = ("catalogo", "grade", "notas", "itens", "operacional")
# Eficiência do frio (Viagens › Grid Operacional, 16/11/2026): a lista da
# Ravex traz todas as unidades da conta; a tarefa é por unidade, como as
# notas, para não repetir a consulta entre operadores. Viagem finalizada
# ganha uma leitura depois do fim (a Grid lista as finalizadas à parte),
# por até FINAL_JANELA_H horas.
INTERVALO_OPERACIONAL_MIN = 10
FINAL_JANELA_H = 6
VERSAO_MIN_OPERACIONAL = (1, 1, 0)   # extensão anterior não conhece a consulta
DIR_EXTENSAO = Path(__file__).parent.parent.parent / "extensao" / "coletor-ravex"

PREF_VM = "ravex_vm_coleta_ativa"
DIR_ENTRADA = _DATA_DIR / "ravex" / "entrada"
ARQ_GRADE = DIR_ENTRADA / "grade_extensao.json"
GRADE_VALIDADE_SEG = 30 * 60

_RE_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


# ── tabelas ──────────────────────────────────────────────────────────
def garantir_tabelas(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS ravex_coletores (
            usuario          TEXT PRIMARY KEY,
            chave_hash       TEXT UNIQUE,
            criado_em        TEXT NOT NULL DEFAULT '',
            grade            INTEGER NOT NULL DEFAULT 0,
            unidades_ravex   TEXT NOT NULL DEFAULT '',
            catalogo_em      TEXT NOT NULL DEFAULT '',
            visto_em         TEXT NOT NULL DEFAULT '',
            situacao         TEXT NOT NULL DEFAULT '',
            versao           TEXT NOT NULL DEFAULT '',
            erro             TEXT NOT NULL DEFAULT '',
            erro_em          TEXT NOT NULL DEFAULT '',
            pausa_ate        TEXT NOT NULL DEFAULT '',
            ultima_coleta_em TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ravex_coleta_tarefas (
            tarefa        TEXT PRIMARY KEY,
            feito_em      TEXT NOT NULL DEFAULT '',
            feito_por     TEXT NOT NULL DEFAULT '',
            reservado_por TEXT NOT NULL DEFAULT '',
            reservado_ate TEXT NOT NULL DEFAULT '',
            erro          TEXT NOT NULL DEFAULT '',
            erro_em       TEXT NOT NULL DEFAULT '',
            pedido_em     TEXT NOT NULL DEFAULT ''
        );
    """)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(ravex_coleta_tarefas)")}
    if "pedido_em" not in cols:
        db.execute("ALTER TABLE ravex_coleta_tarefas ADD COLUMN pedido_em TEXT NOT NULL DEFAULT ''")


# ── chave de coleta (uma por login) ──────────────────────────────────
def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def gerar_chave(db, usuario: str) -> str:
    """Nova chave para o login (a anterior deixa de valer). O servidor
    guarda o hash; a chave aparece uma vez, dentro do código de conexão."""
    chave = secrets.token_urlsafe(32)
    db.execute("""INSERT INTO ravex_coletores(usuario, chave_hash, criado_em) VALUES(?,?,?)
                  ON CONFLICT(usuario) DO UPDATE SET chave_hash=excluded.chave_hash,
                    criado_em=excluded.criado_em, visto_em='', situacao='', erro='', erro_em='', pausa_ate=''""",
               (usuario, _hash(chave), er._agora_txt()))
    return chave


def revogar(db, usuario: str) -> None:
    db.execute("UPDATE ravex_coletores SET chave_hash=NULL, situacao='', erro='', erro_em='', pausa_ate='' "
               "WHERE usuario=?", (usuario,))
    db.execute("UPDATE ravex_coleta_tarefas SET reservado_por='', reservado_ate='' WHERE reservado_por=?", (usuario,))


def identificar(db, chave: str) -> dict | None:
    """Extensão dona da chave, com o cadastro do login. Login desativado ou
    sem o módulo de Entregas deixa de coletar na hora."""
    chave = (chave or "").strip()
    if not (30 <= len(chave) <= 100) or not re.fullmatch(r"[A-Za-z0-9_-]+", chave):
        return None
    row = db.execute("""SELECT c.*, u.perfil, u.unidades_permitidas, u.modulos_permitidos, u.nome_exibicao
                        FROM ravex_coletores c JOIN users u ON lower(u.username)=c.usuario
                        WHERE c.chave_hash=? AND u.ativo=1""", (_hash(chave),)).fetchone()
    if not row:
        return None
    c = dict(row)
    if c["perfil"] not in ("admin", "master"):
        mods = [m.strip() for m in (c["modulos_permitidos"] or "").split(",") if m.strip()]
        if mods and "entregas" not in mods:
            return None
    return c


def definir_grade(db, usuario: str, ativo: bool) -> bool:
    cur = db.execute("UPDATE ravex_coletores SET grade=? WHERE usuario=? AND chave_hash IS NOT NULL",
                     (1 if ativo else 0, usuario))
    return bool(cur.rowcount)


# ── coleta pela VM (desligada por padrão) ────────────────────────────
def situacao_vm() -> dict:
    v = get_preferencia(PREF_VM, {})
    v = v if isinstance(v, dict) else {}
    return {"ativo": bool(v.get("ativo")), "por": v.get("por", ""), "em": v.get("em", "")}


def vm_coleta_ativa() -> bool:
    return situacao_vm()["ativo"]


def definir_vm_coleta(ativo: bool, usuario: str) -> dict:
    set_preferencia(PREF_VM, {"ativo": bool(ativo), "por": usuario, "em": er._agora_txt()})
    return situacao_vm()


# ── unidades de cada extensão ────────────────────────────────────────
def _siglas_permitidas(c: dict) -> set[str] | None:
    """Mesma regra da tela: admin/master enxergam todas; os demais, as
    siglas de unidades_permitidas (vazio = todas)."""
    if c.get("perfil") in ("admin", "master"):
        return None
    s = {u.strip().upper() for u in (c.get("unidades_permitidas") or "").split(",") if u.strip()}
    return s or None


def unidades_do_coletor(db, c: dict) -> list[int]:
    """Unidades (CDs do catálogo) que a conta da Ravex da extensão enxerga e
    que o login do sistema pode ver."""
    vis = set(er._ids_csv(c.get("unidades_ravex") or ""))
    if not vis:
        return []
    perm = _siglas_permitidas(c)
    return sorted(u["id"] for u in er.listar_unidades(db)
                  if u["id"] in vis and (perm is None or (u["sigla"] or "").upper() in perm))


# ── tarefas: frescor e reserva ───────────────────────────────────────
def _tarefas(db, nomes: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(nomes), 400):
        lote = nomes[i:i + 400]
        q = ",".join("?" * len(lote))
        for r in db.execute(f"SELECT * FROM ravex_coleta_tarefas WHERE tarefa IN ({q})", lote):
            out[r["tarefa"]] = dict(r)
    return out


def _pedido_pendente(t: dict | None, agora_ts: float) -> bool:
    """Atualização pedida na tela (abertura do painel ou botão) ainda não
    atendida e dentro da validade."""
    return bool(t) and bool(t.get("pedido_em")) and t["pedido_em"] > (t["feito_em"] or "") and \
        agora_ts - er._ts(t["pedido_em"]) <= PEDIDO_VALIDADE_SEG


def _devida(t: dict | None, intervalo_seg: float, agora_ts: float) -> bool:
    if t is None:
        return True
    if t["reservado_ate"] and er._ts(t["reservado_ate"]) > agora_ts:
        return False                       # em andamento em alguma extensão
    if t["erro_em"] and agora_ts - er._ts(t["erro_em"]) < ESPERA_APOS_ERRO_SEG:
        return False
    if _pedido_pendente(t, agora_ts):
        intervalo_seg = min(intervalo_seg, MIN_DEVIDA_SEG)
    return agora_ts - er._ts(t["feito_em"]) >= intervalo_seg


def _txt(ts: float) -> str:
    """Inverso exato de er._ts: o ts vem de er._agora().timestamp() (hora de
    SP sem fuso, lida como hora local do sistema). Sem fuso aqui também — com
    er._TZ o texto saía 3 h atrasado na VM (UTC) e reserva, pausa e espera
    dos produtos nasciam vencidas (revisão de 15/11/2026)."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _reservada_por(db, nome: str, usuario: str) -> bool:
    """A tarefa foi entregue a esta extensão no plano (e ainda não concluída)."""
    t = _tarefas(db, [nome]).get(nome)
    return bool(t) and t["reservado_por"] == usuario


def _reservar(db, nomes: list[str], usuario: str, agora_ts: float) -> None:
    ate = _txt(agora_ts + RESERVA_SEG)
    db.executemany("""INSERT INTO ravex_coleta_tarefas(tarefa, reservado_por, reservado_ate) VALUES(?,?,?)
                      ON CONFLICT(tarefa) DO UPDATE SET reservado_por=excluded.reservado_por,
                        reservado_ate=excluded.reservado_ate""",
                   [(n, usuario, ate) for n in nomes])


def _concluir(db, nomes: list[str], usuario: str) -> None:
    agora = er._agora_txt()
    db.executemany("""INSERT INTO ravex_coleta_tarefas(tarefa, feito_em, feito_por) VALUES(?,?,?)
                      ON CONFLICT(tarefa) DO UPDATE SET feito_em=excluded.feito_em, feito_por=excluded.feito_por,
                        reservado_por='', reservado_ate='', erro='', erro_em=''""",
                   [(n, agora, usuario) for n in nomes])


def _liberar(db, nomes: list[str], usuario: str, erro: str = "") -> None:
    """Devolve as tarefas reservadas a ESTA extensão (a de outra extensão
    fica como está). erro = espera de ESPERA_APOS_ERRO_SEG na tarefa."""
    if erro:
        db.executemany("UPDATE ravex_coleta_tarefas SET reservado_por='', reservado_ate='', erro=?, erro_em=? "
                       "WHERE tarefa=? AND reservado_por=?", [(erro[:300], er._agora_txt(), n, usuario) for n in nomes])
    else:
        db.executemany("UPDATE ravex_coleta_tarefas SET reservado_por='', reservado_ate='' "
                       "WHERE tarefa=? AND reservado_por=?", [(n, usuario) for n in nomes])


def frescor_unidades(db, ids: list[int]) -> dict[int, dict]:
    """Última atualização das notas de cada unidade (quando e por quem)."""
    nomes = [f"notas:{u}" for u in ids]
    out = {}
    for nome, t in _tarefas(db, nomes).items():
        if t["feito_em"]:
            out[int(nome.split(":", 1)[1])] = {"em": t["feito_em"], "por": t["feito_por"]}
    return out


def pedir_atualizacao(db, ids: list[int]) -> dict:
    """Atualização pedida na tela (ao abrir o painel ou pelo botão): as
    unidades atualizadas há mais de 1 min entram na próxima conferência de
    uma extensão no ar (até 30 s), mesmo fora do horário dos Parâmetros.
    Unidade recente fica como está — vários operadores abrindo a tela ao
    mesmo tempo não multiplicam as consultas à Ravex."""
    agora = er._agora()
    ts = agora.timestamp()
    agora_txt = agora.strftime("%Y-%m-%d %H:%M:%S")
    nomes = {u: f"notas:{u}" for u in ids}
    tm = _tarefas(db, list(nomes.values()))
    pedidas, recentes = [], []
    for u in ids:
        t = tm.get(nomes[u])
        if t and t["feito_em"] and ts - er._ts(t["feito_em"]) < PEDIDO_MIN_SEG:
            recentes.append(u)
            continue
        if not _pedido_pendente(t, ts):
            db.execute("""INSERT INTO ravex_coleta_tarefas(tarefa, pedido_em) VALUES(?,?)
                          ON CONFLICT(tarefa) DO UPDATE SET pedido_em=excluded.pedido_em""", (nomes[u], agora_txt))
        pedidas.append(u)
    return {"pedidas": pedidas, "recentes": recentes}


def pedidos_pendentes(db, ids: list[int]) -> dict[int, str]:
    """Unidades com atualização pedida e ainda não atendida (horário do pedido)."""
    ts = er._agora().timestamp()
    out = {}
    for nome, t in _tarefas(db, [f"notas:{u}" for u in ids]).items():
        if _pedido_pendente(t, ts):
            out[int(nome.split(":", 1)[1])] = t["pedido_em"]
    return out


def _tarefa_itens(db, c: dict, cand: list[int], par: dict, agora_ts: float) -> dict | None:
    """Relatório de produtos das unidades com nota ainda sem produto (mesma
    regra da coleta antiga: até 3 tentativas, com 15 min entre elas)."""
    iv = max(par["intervalo_itens_min"] * 60 - FOLGA_SEG, 60)
    nomes = {u: f"itens:{u}" for u in cand}
    tm = _tarefas(db, list(nomes.values()))
    devidas = [u for u in cand if _devida(tm.get(nomes[u]), iv, agora_ts)]
    if not devidas:
        return None
    limite_tent = _txt(agora_ts - 15 * 60)
    marc = ",".join("?" * len(devidas))
    pend = db.execute(f"""SELECT v.unidade_id AS u, MIN(v.dia_coleta) AS d FROM entregas_nfs n
                          JOIN entregas_viagens v ON v.id_viagem=n.id_viagem
                          WHERE v.unidade_id IN ({marc}) AND v.dia_coleta>=? AND n.itens_qtd=0
                            AND (n.itens_em='' OR (n.itens_tentativas<? AND n.itens_em<=?))
                          GROUP BY v.unidade_id""",
                      (*devidas, (er._hoje() - timedelta(days=er.DIAS_MAX_ATRAS)).isoformat(),
                       er.TENTATIVAS_ITENS, limite_tent)).fetchall()
    if not pend:
        return None
    u_itens = sorted(r["u"] for r in pend)[:MAX_UNIDADES_TAREFA]
    ini = min(r["d"] for r in pend if r["u"] in u_itens)
    _reservar(db, [nomes[u] for u in u_itens], c["usuario"], agora_ts)
    return {"tipo": "itens", "unidades": u_itens, "inicio": ini, "fim": er._hoje().isoformat(),
            "relatorio": dict(er.RELATORIO_ITENS)}


def _versao(txt: str) -> tuple[int, ...]:
    partes = re.findall(r"\d+", str(txt or ""))[:3]
    return tuple(int(p) for p in partes) + (0,) * (3 - len(partes))


def versao_publicada() -> str:
    """Versão da extensão no pacote para baixar (manifest.json)."""
    try:
        return str(json.loads((DIR_EXTENSAO / "manifest.json").read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _tarefa_operacional(db, c: dict, cand: list[int], par: dict, agora_ts: float) -> dict | None:
    """Eficiência do frio das viagens iniciadas das unidades: em aberto a
    cada INTERVALO_OPERACIONAL_MIN (ou o intervalo das notas, se maior) e,
    para a viagem finalizada ainda sem leitura depois do fim, uma consulta
    das finalizadas (no dobro do intervalo)."""
    if not cand:
        return None
    iv = max(max(INTERVALO_OPERACIONAL_MIN, par["intervalo_min"]) * 60 - FOLGA_SEG, 60)
    hoje = er._hoje()
    limite = (hoje - timedelta(days=er.DIAS_MAX_ATRAS)).isoformat()
    desde_fim = _txt(agora_ts - FINAL_JANELA_H * 3600)[:16]
    marc = ",".join("?" * len(cand))
    rows = db.execute(f"""SELECT unidade_id AS u,
                            MIN(CASE WHEN aberta=1 THEN dia_coleta END) AS d_ab,
                            MIN(CASE WHEN aberta=0 THEN dia_coleta END) AS d_fin
                          FROM entregas_viagens
                          WHERE unidade_id IN ({marc}) AND inicio_real<>'' AND ausente_desde=''
                            AND dia_coleta>=? AND dia_coleta<>''
                            AND (aberta=1 OR (fim_real<>'' AND fim_real>=?
                                              AND (efet_temp_em=''
                                                   OR substr(efet_temp_em,1,16)<=substr(fim_real,1,16))))
                          GROUP BY unidade_id""", (*cand, limite, desde_fim)).fetchall()
    # consulta regular em andamento na unidade: a das finalizadas espera por ela
    em_andamento = {int(n.split(":", 1)[1]) for n, t in _tarefas(db, [f"operacional:{r['u']}" for r in rows]).items()
                    if t["reservado_ate"] and er._ts(t["reservado_ate"]) > agora_ts}
    for fin, prefixo, col, iv_t in ((False, "operacional", "d_ab", iv), (True, "operacional_fim", "d_fin", 2 * iv)):
        alvo = {r["u"]: r[col] for r in rows if r[col] and not (fin and r["u"] in em_andamento)}
        nomes = {u: f"{prefixo}:{u}" for u in alvo}
        tm = _tarefas(db, list(nomes.values()))
        devidas = sorted(u for u in alvo if _devida(tm.get(nomes[u]), iv_t, agora_ts))[:MAX_UNIDADES_TAREFA]
        if not devidas:
            continue
        # um dia antes da viagem mais antiga: a data da Grid não é
        # necessariamente a mesma da coleta das notas
        ini = max(date.fromisoformat(min(alvo[u] for u in devidas)) - timedelta(days=1),
                  hoje - timedelta(days=er.DIAS_MAX_ATRAS + 1))
        if not fin:
            ini = min(ini, hoje - timedelta(days=2))      # período padrão da tela da Ravex
        _reservar(db, [nomes[u] for u in devidas], c["usuario"], agora_ts)
        return {"tipo": "operacional", "unidades": devidas, "inicio": ini.isoformat(),
                "fim": hoje.isoformat(), "finalizadas": fin}
    return None


# ── plano: o que esta extensão coleta agora ──────────────────────────
def planejar(c: dict, ravex: str, versao: str = "") -> dict:
    """Chamado a cada 30 s pela extensão. `ravex` = situação da aba da
    Ravex no navegador (ok, sem_aba, sem_sessao, ocupado; teste = conferência
    do código de conexão, sem tarefa)."""
    ravex = ravex if ravex in SITUACOES_RAVEX else "sem_aba"
    agora = er._agora()
    ts = agora.timestamp()
    usuario = c["usuario"]
    resp = {"ok": True, "usuario": c.get("nome_exibicao") or usuario, "tarefas": [], "intervalo_seg": 60}
    vm = vm_coleta_ativa()
    par = er.parametros()
    pedido = get_preferencia(er.PREF_PEDIDO_CATALOGO, {})
    pedido_em = pedido.get("em", "") if isinstance(pedido, dict) else ""
    intervalo_grade = int(get_ravex_config().get("intervalo_min", 20) or 20)

    with get_db() as db:
        db.execute("UPDATE ravex_coletores SET visto_em=?, versao=?, situacao=? WHERE usuario=?",
                   (er._agora_txt(), str(versao or "")[:20], ravex, usuario))
        if ravex != "ok":
            return resp
        if vm:
            resp["mensagem"] = "Coleta feita pelo servidor."
            return resp
        if c.get("pausa_ate") and er._ts(c["pausa_ate"]) > ts:
            resp["pausa"] = True
            resp["mensagem"] = f"Consultas pausadas até {c['pausa_ate'][11:16]}." + \
                (f" {c['erro']}" if c.get("erro") else "")
            return resp
        tarefas: list[dict] = []

        # 1) lista de unidades da conta da Ravex: 1x por dia, na primeira
        # conexão ou quando o gerente pede na tela
        nome_cat = f"catalogo:{usuario}"
        cat_em = c.get("catalogo_em") or ""
        if (not cat_em or cat_em[:10] != agora.date().isoformat() or pedido_em > cat_em) and \
                _devida(_tarefas(db, [nome_cat]).get(nome_cat), 0, ts):
            _reservar(db, [nome_cat], usuario, ts)
            tarefas.append({"tipo": "catalogo"})

        # 2) grade de posição (Abastecimento CD), na extensão marcada para isso
        if c.get("grade"):
            iv = max(intervalo_grade * 60 - FOLGA_SEG, 60)
            if _devida(_tarefas(db, ["grade"]).get("grade"), iv, ts):
                _reservar(db, ["grade"], usuario, ts)
                tarefas.append({"tipo": "grade"})

        # 3) notas (e, depois delas, os produtos), no horário dos Parâmetros;
        # fora dele, só a unidade com atualização pedida na tela
        no_horario = er.no_horario(par, agora)
        cand = sorted(set(unidades_do_coletor(db, c)) & set(er.unidades_para_coletar(db)))
        if cand:
            nomes = {u: f"notas:{u}" for u in cand}
            tm = _tarefas(db, list(nomes.values()))
            if not no_horario:
                cand = [u for u in cand if _pedido_pendente(tm.get(nomes[u]), ts)]
            if cand:
                iv = max(par["intervalo_min"] * 60 - FOLGA_SEG, MIN_DEVIDA_SEG)
                devidas = [u for u in cand if _devida(tm.get(nomes[u]), iv, ts)]
                # unidade com atualização pedida na tela vai primeiro
                devidas.sort(key=lambda u: not _pedido_pendente(tm.get(nomes[u]), ts))
                devidas = sorted(devidas[:MAX_UNIDADES_TAREFA])
                if devidas:
                    _reservar(db, [nomes[u] for u in devidas], usuario, ts)
                    ini, fim = er._periodo_notas(db, devidas)
                    tarefas.append({"tipo": "notas", "unidades": devidas, "inicio": ini, "fim": fim,
                                    "relatorio": dict(er.RELATORIO_NF)})
                else:
                    t_it = _tarefa_itens(db, c, cand, par, ts)
                    if t_it:
                        tarefas.append(t_it)

        # 4) eficiência do frio (Grid Operacional), nas mesmas unidades e no
        # mesmo horário das notas; extensão anterior à 1.1.0 não recebe
        if cand and _versao(versao) >= VERSAO_MIN_OPERACIONAL:
            t_op = _tarefa_operacional(db, c, cand, par, ts)
            if t_op:
                tarefas.append(t_op)
        resp["tarefas"] = tarefas
    return resp


# ── resultado enviado pela extensão ──────────────────────────────────
def _limpo(s, n: int = 300) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(s or "")).strip()[:n]


def _periodo_valido(corpo: dict) -> tuple[str, str] | None:
    ini, fim = str(corpo.get("inicio") or ""), str(corpo.get("fim") or "")
    if not (_RE_DATA.fullmatch(ini) and _RE_DATA.fullmatch(fim)):
        return None
    try:
        d_ini, d_fim = date.fromisoformat(ini), date.fromisoformat(fim)
    except ValueError:
        return None
    hoje = er._hoje()
    if d_ini > d_fim or d_fim > hoje + timedelta(days=1) or d_ini < hoje - timedelta(days=er.DIAS_MAX_ATRAS + 4):
        return None
    return ini, fim


def _unidades_validas(db, c: dict, bruto) -> list[int] | None:
    if not isinstance(bruto, list) or not bruto or len(bruto) > MAX_UNIDADES_TAREFA:
        return None
    try:
        ids = sorted({int(x) for x in bruto})
    except (TypeError, ValueError):
        return None
    permitidas = set(unidades_do_coletor(db, c))
    return ids if ids and all(i in permitidas for i in ids) else None


def _nomes(tipo: str, usuario: str, unidades: list[int] | None, finalizadas: bool = False) -> list[str]:
    if tipo == "catalogo":
        return [f"catalogo:{usuario}"]
    if tipo == "grade":
        return ["grade"]
    if tipo == "operacional" and finalizadas:
        tipo = "operacional_fim"
    return [f"{tipo}:{u}" for u in (unidades or [])]


def _gravar_entrada_grade(dados: list, usuario: str) -> None:
    DIR_ENTRADA.mkdir(parents=True, exist_ok=True)
    tmp = ARQ_GRADE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"recebido_em": er._agora_txt(), "por": usuario, "dados": dados},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ARQ_GRADE)


def _atualizar_estado_coleta(campos: dict) -> None:
    """Linha de situação do painel (entregas_ravex.PREF_COLETA), a mesma que
    a coleta da VM preenchia. Fora da transação do banco (set_preferencia
    abre outra conexão)."""
    if not campos:
        return
    est = er._estado_coleta()
    est.update(campos)
    try:
        set_preferencia(er.PREF_COLETA, est)
    except Exception:
        pass


def receber(c: dict, corpo: dict) -> tuple[int, dict]:
    """Grava o resultado de uma tarefa. Devolve (status HTTP, resposta); a
    resposta pode trazer tarefas seguintes (produtos depois das notas)."""
    tipo = corpo.get("tipo")
    if tipo not in TIPOS:
        return 400, {"error": "Tipo de resultado desconhecido."}
    usuario = c["usuario"]
    nome = c.get("nome_exibicao") or usuario
    agora = er._agora()
    ts = agora.timestamp()
    par = er.parametros()
    estado: dict = {}
    resposta: dict = {"ok": True, "tarefas": []}

    with get_db() as db:
        unidades = None
        periodo = None
        if tipo in ("notas", "itens", "operacional"):
            unidades = _unidades_validas(db, c, corpo.get("unidades"))
            periodo = _periodo_valido(corpo)
            if unidades is None or periodo is None:
                return 400, {"error": "Unidades ou período fora do plano desta extensão."}
        nomes = _nomes(tipo, usuario, unidades, corpo.get("finalizadas") is True)
        # grade e lista de unidades valem para todos: só da extensão a que o
        # plano pediu (a grade, só da marcada pelo gerente) — revisão 15/11
        if tipo == "grade" and not c.get("grade"):
            return 403, {"error": "Esta extensão não está marcada para a grade de posição."}
        if tipo in ("catalogo", "grade") and not _reservada_por(db, nomes[0], usuario):
            return 409, {"error": "Resultado não pedido no plano desta extensão."}

        # ── falha relatada pela extensão
        if not corpo.get("ok"):
            erro = str(corpo.get("erro") or "interno")
            erro = erro if erro in ROTULO_ERRO else "interno"
            msg = _limpo(corpo.get("mensagem")) or ROTULO_ERRO[erro]
            # o texto livre fica no cartão da própria extensão; a linha de
            # situação do painel (vista por todos) leva o rótulo fixo
            publico = f"{ROTULO_ERRO[erro]} (extensão de {nome})"
            _liberar(db, nomes, usuario, msg if erro in ERROS_DA_TAREFA else "")
            pausa = PAUSA_POR_ERRO.get(erro, 0)
            db.execute("UPDATE ravex_coletores SET erro=?, erro_em=?, pausa_ate=? WHERE usuario=?",
                       (msg, er._agora_txt(), _txt(ts + pausa) if pausa else "", usuario))
            if tipo == "notas":
                estado = {"em": er._agora_txt(), "ok": False, "mensagem": publico}
            elif tipo == "itens":
                estado = {"itens_em": er._agora_txt(), "itens_erro": publico}
            elif tipo == "catalogo":
                estado = {"catalogo_tentativa_em": er._agora_txt(), "catalogo_erro": publico}
            resposta["registrado"] = True
            return_code = 200
        else:
            return_code, resposta, estado = _gravar_sucesso(db, c, tipo, corpo, unidades, periodo, nomes,
                                                            par, ts, nome)
    _atualizar_estado_coleta(estado)
    return return_code, resposta


def _catalogo_confiavel(c: dict) -> bool:
    """Quem pode mudar o catálogo de unidades de todos (nome, sigla, se é
    CD): a extensão marcada pelo gerente para a grade (conta principal) e
    os logins admin/master. As demais registram as unidades que a própria
    conta enxerga, entre as que o catálogo já tem."""
    return bool(c.get("grade")) or c.get("perfil") in ("admin", "master")


def _linhas_das_unidades(db, tipo: str, linhas: list[dict], unidades: list[int],
                         confiavel: bool = False) -> list[dict]:
    """Linhas do relatório que pertencem às unidades pedidas. Linha de outra
    unidade fica de fora, e também a de viagem ATIVA gravada em outra
    unidade: o relatório da extensão não grava fora do que o login pode
    ver. Viagem que mudou de unidade na Ravex passa para a nova depois que
    a antiga a marca como ausente (ou na hora, pela extensão confiável)."""
    alvo = set(unidades)
    vids = sorted({r["id_viagem"] for r in linhas if r.get("id_viagem")})
    dono: dict[str, tuple] = {}
    for i in range(0, len(vids), 400):
        lote = vids[i:i + 400]
        q = ",".join("?" * len(lote))
        for r in db.execute(f"SELECT id_viagem, unidade_id, ausente_desde FROM entregas_viagens "
                            f"WHERE id_viagem IN ({q})", lote):
            dono[r["id_viagem"]] = (r["unidade_id"], r["ausente_desde"])
    if tipo == "itens":
        return [r for r in linhas if (dono.get(r.get("id_viagem")) or (None,))[0] in alvo]
    mapa = {er._norm(r["nome"]): r["id"] for r in db.execute("SELECT id, nome FROM entregas_unidades")}
    out = []
    for r in linhas:
        if mapa.get(er._norm(r.get("unidade"))) not in alvo:
            continue
        d = dono.get(r.get("id_viagem"))
        if d and d[0] is not None and d[0] not in alvo and not d[1] and not confiavel:
            continue
        out.append(r)
    return out


def _gravar_sucesso(db, c, tipo, corpo, unidades, periodo, nomes, par, ts, nome):
    usuario = c["usuario"]
    agora_txt = er._agora_txt()
    if tipo == "catalogo":
        dados = corpo.get("dados")
        if not isinstance(dados, list) or not dados or len(dados) > MAX_LINHAS_LISTA:
            _liberar(db, nomes, usuario, "Lista de unidades vazia ou fora do formato.")
            return 400, {"error": "Lista de unidades vazia ou fora do formato."}, {}
        validos = [d for d in dados if isinstance(d, dict)]
        ids = []
        for d in validos:
            try:
                ids.append(int(d.get("Id")))
            except (TypeError, ValueError):
                continue
        existentes = {r["id"] for r in db.execute("SELECT id FROM entregas_unidades")}
        global_gravado = _catalogo_confiavel(c) or not existentes
        n = er.gravar_catalogo(db, validos) if global_gravado else len(set(ids))
        # unidades_ravex guarda TODOS os ids da conta (unidades_do_coletor já
        # cruza com o catálogo e com a permissão do login): unidade nova vale
        # para o operador assim que a extensão confiável a grava no catálogo
        db.execute("UPDATE ravex_coletores SET unidades_ravex=?, catalogo_em=?, erro='', pausa_ate='' "
                   "WHERE usuario=?", (",".join(str(i) for i in sorted(set(ids))), agora_txt, usuario))
        _concluir(db, nomes, usuario)
        # a linha "Lista de unidades" dos Parâmetros só muda quando o catálogo
        # de todos foi gravado (senão o pedido do gerente parecia atendido)
        est = {"catalogo_em": agora_txt, "catalogo_erro": "", "catalogo_tentativa_em": agora_txt} \
            if global_gravado else {}
        return 200, {"ok": True, "unidades": n, "tarefas": []}, est

    if tipo == "grade":
        dados = corpo.get("dados")
        if not isinstance(dados, list) or len(dados) > MAX_LINHAS_LISTA:
            _liberar(db, nomes, usuario, "Grade de posição fora do formato.")
            return 400, {"error": "Grade de posição fora do formato."}, {}
        _gravar_entrada_grade([d for d in dados if isinstance(d, dict)], usuario)
        db.execute("UPDATE ravex_coletores SET erro='', pausa_ate='', ultima_coleta_em=? WHERE usuario=?",
                   (agora_txt, usuario))
        _concluir(db, nomes, usuario)
        return 200, {"ok": True, "veiculos": len(dados), "tarefas": []}, {}

    if tipo == "operacional":
        dados = corpo.get("dados")
        if not isinstance(dados, list) or len(dados) > MAX_LINHAS_LISTA:
            _liberar(db, nomes, usuario, "Grid Operacional fora do formato.")
            return 400, {"error": "Grid Operacional fora do formato."}, {}
        n = er.gravar_eficiencia(db, [d for d in dados if isinstance(d, dict)], unidades)
        db.execute("UPDATE ravex_coletores SET erro='', pausa_ate='', ultima_coleta_em=? WHERE usuario=?",
                   (agora_txt, usuario))
        _concluir(db, nomes, usuario)
        return 200, {"ok": True, "viagens": n, "tarefas": []}, {}

    texto = corpo.get("csv")
    if not isinstance(texto, str) or not texto.strip():
        _liberar(db, nomes, usuario, "Relatório veio vazio.")
        return 400, {"error": "Relatório veio vazio."}, {}
    if len(texto) > er.LIMITE_BYTES:
        _liberar(db, nomes, usuario, "Relatório acima do limite.")
        return 400, {"error": "Relatório acima do limite."}, {}
    ini, fim = periodo
    try:
        if tipo == "notas":
            linhas = er.ler_csv(texto, er.COLUNAS_NF, er.OBRIGATORIAS_NF)
        else:
            linhas = er.ler_csv(texto, er.COLUNAS_ITENS, er.OBRIGATORIAS_ITENS)
    except er.FormatoInesperado as e:
        msg = str(e)[:300]
        _liberar(db, nomes, usuario, msg)
        est = {"em": agora_txt, "ok": False, "mensagem": msg} if tipo == "notas" else \
            {"itens_em": agora_txt, "itens_erro": msg}
        return 200, {"ok": False, "error": msg, "tarefas": []}, est
    linhas = _linhas_das_unidades(db, tipo, linhas, unidades, _catalogo_confiavel(c))

    db.execute("UPDATE ravex_coletores SET erro='', pausa_ate='', ultima_coleta_em=? WHERE usuario=?",
               (agora_txt, usuario))
    if tipo == "itens":
        r = er.gravar_itens(db, linhas, unidades, ini, fim)
        _concluir(db, nomes, usuario)
        return 200, {"ok": True, **r, "tarefas": []}, {"itens_em": agora_txt, "itens_erro": ""}

    r = er.gravar_notas(db, linhas, unidades, ini, fim)
    db.executemany("INSERT OR IGNORE INTO entregas_unidade_coleta(unidade_id, desde) VALUES(?,?)",
                   [(u, agora_txt) for u in unidades])
    _concluir(db, nomes, usuario)
    # unidades com notas recentes (para a linha "Unidades na coleta" do painel)
    janela = 2 * par["intervalo_min"] * 60 + 120
    frescas = sorted(u for u, f in frescor_unidades(db, er.unidades_para_coletar(db)).items()
                     if ts - er._ts(f["em"]) <= janela)
    seguinte = _tarefa_itens(db, c, unidades, par, ts)
    est = {"em": agora_txt, "ok": True, "ultima_ok_em": agora_txt, "periodo": [ini, fim], "unidades": frescas,
           "mensagem": f"{r.get('viagens', 0)} viagens e {r.get('notas', 0)} notas ({ini} a {fim}), "
                       f"pela extensão de {nome}."}
    return 200, {"ok": True, **r, "tarefas": [seguinte] if seguinte else []}, est


# ── grade recebida → painel (cron) ───────────────────────────────────
def processar_grade_recebida() -> tuple[bool, str]:
    """Aplica no painel a última grade enviada por extensão. Roda no cron da
    Ravex (processo separado do Gunicorn). Grade com mais de 30 min fica de
    fora (posição velha não pode parecer atual)."""
    if not ARQ_GRADE.exists():
        return True, ""
    proc = ARQ_GRADE.with_suffix(".processando")
    try:
        os.replace(ARQ_GRADE, proc)
    except FileNotFoundError:
        return True, ""
    try:
        d = json.loads(proc.read_text(encoding="utf-8"))
        dados = d.get("dados") if isinstance(d, dict) else None
        if not isinstance(dados, list):
            return False, "Grade recebida em formato inesperado."
        rec = str(d.get("recebido_em") or "")
        if er._agora().timestamp() - er._ts(rec) > GRADE_VALIDADE_SEG:
            return False, f"Grade recebida às {rec[11:16] or '—'} descartada (mais de 30 min)."
        from app.services.ravex_automation import aplicar_grade
        ok, msg = aplicar_grade(dados)
        return ok, f"Grade da extensão ({d.get('por', '')}, {rec[11:16]}) aplicada: {msg}."
    except Exception as e:
        return False, f"Erro ao aplicar a grade recebida: {type(e).__name__}: {str(e)[:200]}"
    finally:
        try:
            proc.unlink()
        except FileNotFoundError:
            pass


# ── tela ─────────────────────────────────────────────────────────────
def _online(visto_em: str, agora_ts: float) -> bool:
    return bool(visto_em) and agora_ts - er._ts(visto_em) <= ONLINE_SEG


def _erro_recente(erro_em: str, agora_ts: float) -> bool:
    """Falha nos últimos 15 min (calculado aqui: o relógio do navegador de
    outro fuso, como Cuiabá, não serve para comparar com a hora de SP)."""
    return bool(erro_em) and agora_ts - er._ts(erro_em) <= 15 * 60


def situacao_coletor(db, usuario: str) -> dict:
    """Card "Minha conta Ravex" da tela de Entregas."""
    row = db.execute("""SELECT c.*, u.perfil, u.unidades_permitidas, u.nome_exibicao FROM ravex_coletores c
                        LEFT JOIN users u ON lower(u.username)=c.usuario WHERE c.usuario=?""", (usuario,)).fetchone()
    if not row or not row["chave_hash"]:
        return {"conectado": False}
    c = dict(row)
    ts = er._agora().timestamp()
    cat = {u["id"]: u for u in er.listar_unidades(db)}
    ids_ravex = er._ids_csv(c["unidades_ravex"])
    cobertas = unidades_do_coletor(db, c)
    atual = versao_publicada()
    return {
        "conectado": True, "criado_em": c["criado_em"], "visto_em": c["visto_em"],
        "online": _online(c["visto_em"], ts), "situacao": c["situacao"], "versao": c["versao"],
        "versao_atual": atual,
        "desatualizada": bool(atual and c["versao"]) and _versao(c["versao"]) < _versao(atual),
        "erro": c["erro"], "erro_em": c["erro_em"], "erro_recente": _erro_recente(c["erro_em"], ts),
        "pausa_ate": c["pausa_ate"] if c["pausa_ate"] and er._ts(c["pausa_ate"]) > ts else "",
        "ultima_coleta_em": c["ultima_coleta_em"], "catalogo_em": c["catalogo_em"], "grade": bool(c["grade"]),
        "unidades_ravex": sorted((cat[i]["sigla"] for i in ids_ravex if i in cat), key=str),
        "unidades_coleta": sorted((cat[i]["sigla"] for i in cobertas if i in cat), key=str),
    }


def listar_coletores(db) -> list[dict]:
    """Extensões conectadas (visão do gerente)."""
    ts = er._agora().timestamp()
    cat = {u["id"]: u["sigla"] for u in er.listar_unidades(db)}
    versao_txt = versao_publicada()
    atual = _versao(versao_txt)
    out = []
    for r in db.execute("""SELECT c.*, u.perfil, u.unidades_permitidas, u.nome_exibicao FROM ravex_coletores c
                           LEFT JOIN users u ON lower(u.username)=c.usuario
                           WHERE c.chave_hash IS NOT NULL ORDER BY c.usuario"""):
        c = dict(r)
        out.append({"usuario": c["usuario"], "nome": c["nome_exibicao"] or c["usuario"],
                    "online": _online(c["visto_em"], ts), "visto_em": c["visto_em"], "situacao": c["situacao"],
                    "erro": c["erro"], "erro_em": c["erro_em"], "erro_recente": _erro_recente(c["erro_em"], ts),
                    "pausa_ate": c["pausa_ate"] if c["pausa_ate"] and er._ts(c["pausa_ate"]) > ts else "",
                    "grade": bool(c["grade"]), "ultima_coleta_em": c["ultima_coleta_em"],
                    "versao": c["versao"], "versao_atual": versao_txt,
                    "desatualizada": bool(c["versao"]) and atual > (0, 0, 0) and _versao(c["versao"]) < atual,
                    "sem_operacional": bool(c["versao"]) and _versao(c["versao"]) < VERSAO_MIN_OPERACIONAL,
                    "unidades": sorted((cat[i] for i in unidades_do_coletor(db, c) if i in cat), key=str)})
    return out


def sem_cobertura(db, ids: list[int]) -> list[int]:
    """Unidades da lista que nenhuma extensão no ar coleta agora."""
    if not ids:
        return []
    ts = er._agora().timestamp()
    cobertas: set[int] = set()
    for r in db.execute("""SELECT c.*, u.perfil, u.unidades_permitidas FROM ravex_coletores c
                           JOIN users u ON lower(u.username)=c.usuario
                           WHERE c.chave_hash IS NOT NULL AND u.ativo=1"""):
        c = dict(r)
        if _online(c["visto_em"], ts) and c["situacao"] in ("ok", "ocupado"):
            cobertas.update(unidades_do_coletor(db, c))
    return [i for i in ids if i not in cobertas]
