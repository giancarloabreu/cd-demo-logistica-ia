"""
Módulo "Produtividade de Separação" (16/09/2026).

Dois relatórios do WMS viram UM registro por instrução (ver
app/services/produtividade_parser.py): o PAINEL diz quem separou e quanto,
o RESUMO diz quando a instrução foi liberada. O cálculo está em
app/services/produtividade_calc.py — ritmo, aproveitamento e demanda.

**CD = empresa consolidada.** CDA e CDB dividem o mesmo local, então a tela
mostra os dois juntos quando o CD escolhido é CDA. O cadastro é o mesmo do
Abastecimento (tabela `empresa_consolidada`).

**Escopo por empresa, não por CD.** Cada usuário vê só as empresas que tem
permitidas (usuário, 15/09): quem tem só CDB e abre o CD Demo enxerga só a
fatia CDB. Nomes de separador e conferente aparecem pra todos os perfis,
sempre dentro desse mesmo escopo — isso reverte a medição "cega" de 01/09.

**Banco único, gravação por coluna.** Painel e resumo chegam em uploads
diferentes e cada um é dono das suas colunas; um não apaga o que o outro
gravou. Campo que existe nos dois fica com o último valor NÃO vazio. A
prioridade fica com o MAIOR valor dos dois — o 999 que o usuário marca à
mão, em qualquer um dos arquivos, não pode ser desfeito pelo outro.

Decorators de auth duplicados de propósito, mesmo padrão de giro.py e
mapa_estoque.py — evita import circular com server.py.
"""
from __future__ import annotations

import datetime as _dt
import hmac
import json
import math
import os
import re
import tempfile
from functools import wraps

from flask import Blueprint, jsonify, render_template, request, session

from app.database import get_db, get_mapa_consolidacao, registrar_log
from app.services.produtividade_calc import FUNCOES, PARAMETROS_PADRAO, calcular, projetar
from app.services.produtividade_parser import TIPO_PAINEL, ler_arquivo
from app.utils.access_control import (cds_da_conta_canonica, cd_do_usuario,
                                      modulo_required, tem_acesso_modulo,
                                      usuario_efetivo)

produtividade_bp = Blueprint("produtividade", __name__)

_TZ_BRT = _dt.timezone(_dt.timedelta(hours=-3))


def _agora() -> str:
    return _dt.datetime.now(_TZ_BRT).strftime("%Y-%m-%d %H:%M:%S")


def _hoje() -> str:
    return _dt.datetime.now(_TZ_BRT).strftime("%Y-%m-%d")


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


def admin_required(f):
    @wraps(f)
    def d(*a, **k):
        if session.get("perfil") not in ("admin", "master"):
            return jsonify({"error": "Acesso restrito a gerentes e administradores."}), 403
        return f(*a, **k)
    return d


def _empresas_permitidas() -> set[str] | None:
    """Empresas que a sessão pode ver. None = todas (admin/master, ou
    cadastro sem unidade, que é a convenção do sistema)."""
    if session.get("perfil") in ("admin", "master"):
        return None
    raw = session.get("unidades_permitidas", "") or ""
    u = {x.strip().upper() for x in raw.split(",") if x.strip()}
    return u or None


def _pode_ver_empresa(emp: str) -> bool:
    p = _empresas_permitidas()
    return p is None or (emp or "").strip().upper() in p


def _consolidada(emp: str, mapa: dict | None = None) -> str:
    m = mapa if mapa is not None else get_mapa_consolidacao()
    e = (emp or "").strip().upper()
    return m.get(e, e)


def empresas_do_local(conta: str | None = None) -> list[str]:
    """Empresas que operam no MESMO LOCAL da conta em uso.

    CDA e CDB dividem o Local, consolidam em CDA, e a noite é UMA operação
    para as duas (usuário, 08/10/2026). Duas fontes, unidas: as siglas que
    apontam para a conta canônica (cd_conta_canonica) e as que consolidam nas
    mesmas empresas (empresa_consolidada). Lista vazia = a conta não é a de
    um Local (admin/master fora do espelho).

    Não passa pelo filtro de permissão por empresa de propósito: quem está
    nesta conta trabalha no Local inteiro — é a mesma régua do Armazém, que
    já mostra CDA e CDB juntas para a conta do Local.
    """
    conta = (conta or usuario_efetivo() or "").strip().lower()
    base = {e.strip().upper() for e in cds_da_conta_canonica(conta) if e and e.strip()}
    if not base:
        return []
    mapa = get_mapa_consolidacao()
    consolidadoras = {_consolidada(e, mapa) for e in base}
    base |= consolidadoras
    base |= {sig for sig, cons in mapa.items() if cons in consolidadoras}
    return sorted(base)


def consolidadora(empresas, mapa: dict | None = None) -> str:
    """A empresa que representa um conjunto do mesmo Local: a consolidadora
    comum (CDB -> CDA); sem mapa, a que consolida em si mesma."""
    m = mapa if mapa is not None else get_mapa_consolidacao()
    emps = sorted({(e or "").strip().upper() for e in empresas if e})
    if not emps:
        return ""
    cons = {_consolidada(e, m) for e in emps}
    if len(cons) == 1:
        return cons.pop()
    return next((e for e in emps if _consolidada(e, m) == e), emps[0])


def empresas_para_upload() -> list[str]:
    """A quem um painel SEM a coluna Empresa pode pertencer — um nome por LOCAL.

    O painel exportado sem a coluna é a operação do Local inteiro. Para a
    conta de um Local a resposta é uma só, a consolidadora (CDA), e a tela
    não pergunta nada. Pergunta quando a sessão enxerga mais de um Local —
    admin fora do espelho, por exemplo —, porque aí a dúvida é real: o
    arquivo pode ser do CDA ou de outra unidade.
    """
    mapa = get_mapa_consolidacao()
    local = empresas_do_local()
    if local:
        return [consolidadora(local, mapa)]
    fontes = [cd_do_usuario()] if cd_do_usuario() else []
    if not fontes:
        with get_db() as db:
            fontes = [r["empresa"] for r in db.execute(
                "SELECT DISTINCT empresa FROM prod_instrucao "
                "WHERE empresa IS NOT NULL AND empresa != ''").fetchall()]
    # um nome por Local: quem divide a conta canônica, ou consolida no mesmo
    # lugar, entra como a consolidadora
    from app.utils.access_control import conta_canonica_do_cd
    grupos: dict[str, set] = {}
    for e in fontes:
        e = (e or "").strip().upper()
        if not e or not _pode_ver_empresa(e):
            continue
        grupos.setdefault(conta_canonica_do_cd(e) or _consolidada(e, mapa), set()).add(e)
    return sorted({consolidadora(g, mapa) for g in grupos.values()})


def preencher_empresa(info: dict, escolhida: str = "") -> str | None:
    """Completa a empresa das linhas que vieram sem ela.

    Devolve None quando deu certo, ou a frase que explica por que não deu —
    quem chama decide entre recusar o arquivo ou pedir a escolha na tela.
    """
    vazias = [r for r in info.get("registros") or [] if not r.get("empresa")]
    if not vazias:
        return None
    possiveis = empresas_para_upload()
    escolhida = (escolhida or "").strip().upper()
    if escolhida:
        # CDB escolhida (ou lembrada pelo navegador) vira a consolidadora do
        # Local dela — o painel sem a coluna não separa as duas
        escolhida = consolidadora([escolhida])
        if escolhida not in possiveis:
            return f"Sem acesso à empresa {escolhida}."
        alvo = escolhida
    elif len(possiveis) == 1:
        alvo = possiveis[0]
    elif not possiveis:
        return ("O painel não traz a coluna Empresa e não há empresa cadastrada "
                "para esta conta.")
    else:
        return ("O painel não traz a coluna Empresa. Informe a empresa do arquivo "
                f"({', '.join(possiveis)}).")
    # A instrução que já está no banco por um painel COM a coluna mantém a
    # empresa dela. Gravar tudo na consolidadora criava a chave CDA|26838 ao
    # lado da CDB|26838 verdadeira, e a cópia congelada aparecia como "em
    # andamento" na manhã seguinte — 86% numa noite 100% concluída (09/10/2026).
    irmas = sorted(_irmas_do_local(alvo))
    with get_db() as db:
        for r in vazias:
            r["empresa"] = _empresa_existente(db, r, irmas) or alvo
    return None


def _irmas_do_local(emp: str, mapa: dict | None = None) -> set:
    """Empresas do mesmo Local que `emp` (CDA -> {CDA, CDB}): quem divide a
    conta canônica ou consolida no mesmo lugar."""
    from app.utils.access_control import conta_canonica_do_cd
    emp = (emp or "").strip().upper()
    if not emp:
        return set()
    m = mapa if mapa is not None else get_mapa_consolidacao()
    irmas = {emp}
    conta = conta_canonica_do_cd(emp)
    if conta:
        irmas |= set(empresas_do_local(conta))
    cons = _consolidada(emp, m)
    irmas |= {cons} | {sig for sig, c in m.items() if c == cons}
    return {e.strip().upper() for e in irmas if e and e.strip()}


def _empresa_existente(db, reg: dict, empresas) -> str | None:
    """Empresa com que a mesma instrução já está gravada, entre `empresas`,
    a até um dia de distância — o número da instrução é único dentro do Local
    (CDA e CDB numeram na mesma sequência: 26822–26847 CDB, 26848–26854 CDA
    no painel de 09/10)."""
    if not empresas or not reg.get("instrucao") or not reg.get("data_op"):
        return None
    marcas = ",".join("?" * len(empresas))
    row = db.execute(
        f"SELECT empresa FROM prod_instrucao WHERE instrucao=? AND empresa IN ({marcas}) "
        f"AND data_op BETWEEN date(?, '-1 day') AND date(?, '+1 day') "
        f"ORDER BY atualizado_em DESC LIMIT 1",
        [str(reg["instrucao"]), *empresas, reg["data_op"], reg["data_op"]]).fetchone()
    return row["empresa"] if row else None


def _fundir(db, origem: str, destino: str, instrucao: str) -> None:
    """Junta a linha origem|instrução na destino|instrução e apaga a origem.

    Os dados vêm da carga mais RECENTE, com o vazio completado pela outra; a
    prioridade fica com a maior (o 999 à mão não se perde) e a marcação de
    "desconsiderar" segue a instrução.
    """
    a = db.execute("SELECT * FROM prod_instrucao WHERE empresa=? AND instrucao=?",
                   (origem, instrucao)).fetchone()
    if a is None:
        return
    b = db.execute("SELECT * FROM prod_instrucao WHERE empresa=? AND instrucao=?",
                   (destino, instrucao)).fetchone()
    if b is None:
        db.execute("UPDATE prod_instrucao SET empresa=? WHERE empresa=? AND instrucao=?",
                   (destino, origem, instrucao))
    else:
        a, b = dict(a), dict(b)
        if str(a.get("atualizado_em") or "") > str(b.get("atualizado_em") or ""):
            novo, velho = a, b
        else:
            novo, velho = b, a
        cols = [c for c in b if c not in ("empresa", "instrucao")]
        junto = {c: (novo[c] if novo.get(c) not in (None, "") else velho.get(c)) for c in cols}
        junto["prioridade"] = max(float(a.get("prioridade") or 0), float(b.get("prioridade") or 0))
        for c in ("tem_painel", "tem_resumo"):
            if c in junto:
                junto[c] = max(int(a.get(c) or 0), int(b.get(c) or 0))
        db.execute(f"UPDATE prod_instrucao SET {', '.join(c + '=?' for c in cols)} "
                   f"WHERE empresa=? AND instrucao=?",
                   [junto[c] for c in cols] + [destino, instrucao])
        db.execute("DELETE FROM prod_instrucao WHERE empresa=? AND instrucao=?",
                   (origem, instrucao))
    db.execute("UPDATE OR IGNORE prod_desconsiderar SET empresa=? WHERE empresa=? AND instrucao=?",
               (destino, origem, instrucao))
    db.execute("DELETE FROM prod_desconsiderar WHERE empresa=? AND instrucao=?",
               (origem, instrucao))


def _fundir_copias_do_local(db, registros: list[dict]) -> int:
    """Antes de gravar: a mesma instrução gravada com OUTRA empresa do mesmo
    Local (o painel sem a coluna Empresa, carregado antes) é fundida na
    empresa que chega agora. Devolve quantas cópias foram fundidas."""
    mapa = get_mapa_consolidacao()
    cache: dict[str, list] = {}
    fundidas = 0
    for r in registros:
        emp = (r.get("empresa") or "").strip().upper()
        if not emp or not r.get("instrucao") or not r.get("data_op"):
            continue
        if emp not in cache:
            cache[emp] = sorted(_irmas_do_local(emp, mapa) - {emp})
        outras = cache[emp]
        if not outras:
            continue
        marcas = ",".join("?" * len(outras))
        for o in db.execute(
                f"SELECT empresa FROM prod_instrucao WHERE instrucao=? AND empresa IN ({marcas}) "
                f"AND data_op BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
                [str(r["instrucao"]), *outras, r["data_op"], r["data_op"]]).fetchall():
            _fundir(db, o["empresa"], emp, str(r["instrucao"]))
            fundidas += 1
    return fundidas


def fundir_duplicadas_gravadas(db) -> int:
    """Limpeza das cópias que já estão no banco (rodada no boot; sem cópia,
    não faz nada). A chave que fica é a da empresa que NÃO é a consolidadora
    — só a coluna Empresa do painel produz CDB; a consolidadora pode ter sido
    preenchida na falta dela."""
    mapa = get_mapa_consolidacao()
    pares = db.execute(
        "SELECT a.empresa ea, b.empresa eb, a.instrucao FROM prod_instrucao a "
        "JOIN prod_instrucao b ON a.instrucao = b.instrucao AND a.empresa < b.empresa "
        "WHERE abs(julianday(a.data_op) - julianday(b.data_op)) <= 1").fetchall()
    n = 0
    for p in pares:
        ea, eb = p["ea"], p["eb"]
        if eb not in _irmas_do_local(ea, mapa):
            continue
        cons_a, cons_b = _consolidada(ea, mapa) == ea, _consolidada(eb, mapa) == eb
        origem, destino = (ea, eb) if cons_a and not cons_b else (eb, ea)
        _fundir(db, origem, destino, p["instrucao"])
        n += 1
    return n


def _empresas_do_cd(cd: str) -> list[str]:
    """Empresas que compõem o CD (consolidado) E que o usuário pode ver."""
    mapa = get_mapa_consolidacao()
    cd = (cd or "").strip().upper()
    with get_db() as db:
        todas = {r["empresa"] for r in db.execute(
            "SELECT DISTINCT empresa FROM prod_instrucao").fetchall()}
    do_cd = {e for e in todas if _consolidada(e, mapa) == cd} | {cd}
    return sorted(e for e in do_cd if _pode_ver_empresa(e))


# ── banco ────────────────────────────────────────────────────────────
# Colunas de cada relatório. As que aparecem nos DOIS ficam em _COMPARTILHADAS
# e seguem a regra "último valor não vazio".
_COLS_PAINEL = ("separador", "sep_cod", "conferente", "conf_cod", "cliente",
                "tipo_carga", "frete", "doca", "prev_kg", "sep_kg", "sep_vol",
                "emb_kg", "emb_vol", "sep_ini", "sep_fim", "calcula_prod",
                "status_op", "status_veic")
# transf_pedido/contratacao/faturamento/prev_viagem entram aqui para a tela
# de SLAs nascer com histórico: essas datas só existem no arquivo do dia.
_COLS_RESUMO = ("entregas", "cidade", "uf", "km", "liberacao",
                "transf_pedido", "contratacao", "faturamento", "prev_viagem")
# ult_caixa e fech_status vêm nos DOIS relatórios (30/09/2026)
_COMPARTILHADAS = ("data_op", "sku", "escalas", "tipo_op", "placa",
                   "temperatura", "emb_ini", "emb_fim", "ult_caixa", "fech_status")
_TODAS = _COLS_PAINEL + _COLS_RESUMO + _COMPARTILHADAS + ("prioridade",)
_NUMERICAS = {"prev_kg", "sep_kg", "sep_vol", "emb_kg", "emb_vol", "entregas",
              "km", "sku", "escalas", "prioridade"}

# rótulo exibido no popup e teto aceito por parâmetro
_PARAM_META = {
    "noturno_inicio": ("Início do turno noturno", 23),
    "noturno_fim": ("Término do turno noturno", 23),
    "turno_h": ("Duração do turno", 24),
    "separacao_inicio": ("Início da separação", 23),
    "separacao_fim": ("Término da separação", 23),
    "embarque_inicio": ("Início do embarque", 23),
    "embarque_fim": ("Término do embarque", 23),
    "refeicao_min": ("Intervalo de refeição", 600),
    "trabalho_continuo_min": ("Trabalho contínuo antes da pausa térmica", 600),
    "pausa_termica_min": ("Pausa térmica", 600),
    "sku_complexa": ("Instrução complexa a partir de", 10000),
    "onda_intervalo_min": ("Intervalo máximo dentro da mesma onda", 1440),
    "docas_disponiveis": ("Docas disponíveis", 500),
    "caminhoes_por_doca": ("Caminhões por doca no turno", 50),
    "caminhoes_por_conferente": ("Caminhões por conferente no turno", 500),
}


def init_produtividade_db():
    cols = ",\n            ".join(
        f"{c} {'REAL' if c in _NUMERICAS else 'TEXT'}" for c in _TODAS)
    with get_db() as db:
        db.executescript(f"""
        CREATE TABLE IF NOT EXISTS prod_instrucao (
            empresa    TEXT NOT NULL,
            instrucao  TEXT NOT NULL,
            {cols},
            tem_painel INTEGER NOT NULL DEFAULT 0,
            tem_resumo INTEGER NOT NULL DEFAULT 0,
            atualizado_em TEXT,
            PRIMARY KEY (empresa, instrucao)
        );
        CREATE INDEX IF NOT EXISTS idx_prod_data ON prod_instrucao(empresa, data_op);

        -- Parâmetros por CD (empresa consolidada): turno, refeição, pausa
        -- térmica, régua de complexidade. JSON pra poder crescer sem migração.
        CREATE TABLE IF NOT EXISTS prod_parametros (
            cd            TEXT PRIMARY KEY,
            dados         TEXT NOT NULL,
            atualizado_em TEXT,
            usuario       TEXT
        );

        -- Carreta desconsiderada à mão pela tela (usuário, 16/09/2026). Fica
        -- separada da instrução de propósito: um reenvio do arquivo NÃO pode
        -- desfazer a marcação.
        CREATE TABLE IF NOT EXISTS prod_desconsiderar (
            empresa   TEXT NOT NULL,
            instrucao TEXT NOT NULL,
            motivo    TEXT,
            usuario   TEXT,
            quando    TEXT,
            PRIMARY KEY (empresa, instrucao)
        );

        -- Apontamento do efetivo, por dia e turno (30/09/2026). O WMS diz
        -- quem separou, não quem estava na casa: auxiliar não aparece em
        -- relatório nenhum, e não há lista de presença. Daí o apontamento
        -- curto, digitado pelo usuário, que fecha o H-H do dia.
        CREATE TABLE IF NOT EXISTS prod_apontamento (
            cd        TEXT NOT NULL,
            data      TEXT NOT NULL,
            turno     TEXT NOT NULL,
            funcao    TEXT NOT NULL,
            previsto  INTEGER,
            presente  INTEGER,
            entrada   TEXT,
            usuario   TEXT,
            atualizado_em TEXT,
            PRIMARY KEY (cd, data, turno, funcao)
        );
        """)
        # Coluna nova em base que já existe: ALTER simples, sem tocar na PK.
        existentes = {r["name"] for r in db.execute("PRAGMA table_info(prod_instrucao)")}
        for c in _TODAS:
            if c not in existentes:
                db.execute(f"ALTER TABLE prod_instrucao ADD COLUMN {c} "
                           f"{'REAL' if c in _NUMERICAS else 'TEXT'}")
    # Cópias CDA|n e CDB|n da mesma instrução (painel sem a coluna Empresa
    # carregado antes do completo, 08/10/2026). Fora do bloco acima para uma
    # falha aqui não impedir o boot.
    try:
        with get_db() as db:
            n = fundir_duplicadas_gravadas(db)
        if n:
            print(f"[DB] Produtividade: {n} cópia(s) da mesma instrução fundida(s)")
    except Exception as exc:
        print(f"[DB] AVISO: limpeza de cópias de instrução falhou: {exc}")


def _gravar(db, tipo: str, registros: list[dict]) -> int:
    proprias = _COLS_PAINEL if tipo == TIPO_PAINEL else _COLS_RESUMO
    colunas = ("empresa", "instrucao") + proprias + _COMPARTILHADAS + ("prioridade",)
    marca = "tem_painel" if tipo == TIPO_PAINEL else "tem_resumo"

    sets = [f"{c}=excluded.{c}" for c in proprias]
    # compartilhada: último valor NÃO vazio vence
    sets += [f"{c}=COALESCE(NULLIF(excluded.{c}, ''), {c})" for c in _COMPARTILHADAS]
    # prioridade: o maior vence — o 999 marcado à mão não pode ser desfeito
    sets.append("prioridade=MAX(COALESCE(excluded.prioridade, 0), COALESCE(prioridade, 0))")
    sets += [f"{marca}=1", "atualizado_em=excluded.atualizado_em"]

    sql = (f"INSERT INTO prod_instrucao ({', '.join(colunas)}, {marca}, atualizado_em) "
           f"VALUES ({', '.join('?' * len(colunas))}, 1, ?) "
           f"ON CONFLICT(empresa, instrucao) DO UPDATE SET {', '.join(sets)}")
    agora = _agora()
    linhas = []
    for r in registros:
        if not r.get("empresa") or not r.get("instrucao"):
            continue
        linhas.append(tuple(r.get(c) if c in r else None for c in colunas) + (agora,))
    _fundir_copias_do_local(db, registros)
    db.executemany(sql, linhas)
    return len(linhas)


def _parametros_do_cd(cd: str) -> dict:
    with get_db() as db:
        row = db.execute("SELECT dados FROM prod_parametros WHERE cd=?", (cd,)).fetchone()
    base = dict(PARAMETROS_PADRAO)
    if row:
        try:
            salvos = json.loads(row["dados"])
            # chave que saiu do modelo (ex.: conferentes_por_doca) e valor não
            # numérico/infinito gravado por requisição manual são ignorados
            base.update({k: v for k, v in salvos.items()
                         if k in PARAMETROS_PADRAO and isinstance(v, (int, float))
                         and math.isfinite(v)})
        except (ValueError, TypeError, AttributeError):
            pass
    return base


def _marcacoes(empresas: list[str]) -> dict:
    """{"EMPRESA|INSTRUCAO": {motivo, usuario, quando}} das desconsideradas à mão."""
    if not empresas:
        return {}
    with get_db() as db:
        rows = db.execute(
            f"SELECT empresa, instrucao, motivo, usuario, quando FROM prod_desconsiderar "
            f"WHERE empresa IN ({','.join('?' * len(empresas))})", empresas).fetchall()
    return {f'{r["empresa"]}|{r["instrucao"]}': {"motivo": r["motivo"] or "",
            "usuario": r["usuario"] or "", "quando": r["quando"] or ""} for r in rows}


def _desconsideradas(empresas: list[str]) -> set:
    return set(_marcacoes(empresas))


def _registros(empresas: list[str], de: str, ate: str) -> list[dict]:
    """Instruções COM Painel. A que chegou pelo Resumo e ainda espera o Painel
    do dia não tem separador nem peso — entraria nos indicadores com zero e
    diluiria o período (revisão de 16/09/2026)."""
    if not empresas:
        return []
    with get_db() as db:
        rows = db.execute(
            f"SELECT * FROM prod_instrucao WHERE empresa IN "
            f"({','.join('?' * len(empresas))}) AND tem_painel=1 AND data_op BETWEEN ? AND ?",
            [*empresas, de, ate]).fetchall()
    return [dict(r) for r in rows]


def _pendentes_painel(empresas: list[str], de: str, ate: str) -> int:
    if not empresas:
        return 0
    with get_db() as db:
        r = db.execute(
            f"SELECT COUNT(*) n FROM prod_instrucao WHERE empresa IN "
            f"({','.join('?' * len(empresas))}) AND tem_painel=0 AND data_op BETWEEN ? AND ?",
            [*empresas, de, ate]).fetchone()
    return r["n"] if r else 0


def _ultimo_dia(empresas: list[str]) -> str | None:
    if not empresas:
        return None
    with get_db() as db:
        r = db.execute(
            f"SELECT MAX(data_op) m FROM prod_instrucao WHERE tem_painel=1 AND empresa IN "
            f"({','.join('?' * len(empresas))})", empresas).fetchone()
    return r["m"] if r else None


class PeriodoInvalido(ValueError):
    pass


def _periodo():
    """(de, ate) validados. Datas fora de 2000–2100 chegam do campo de data
    enquanto o ano ainda está sendo digitado ("0002-09-01") e derrubavam o
    cálculo do período anterior com OverflowError."""
    de = (request.args.get("de") or "").strip()
    ate = (request.args.get("ate") or "").strip() or _hoje()
    if not de:
        de = ate[:8] + "01"
    try:
        d0, d1 = _dt.date.fromisoformat(de), _dt.date.fromisoformat(ate)
    except ValueError:
        raise PeriodoInvalido("Período inválido. Informe as datas no formato dia/mês/ano.")
    if not (2000 <= d0.year <= 2100 and 2000 <= d1.year <= 2100):
        raise PeriodoInvalido("Período inválido. Informe datas entre 2000 e 2100.")
    if d0 > d1:
        raise PeriodoInvalido("A data inicial é posterior à data final.")
    return d0.isoformat(), d1.isoformat()


def _apontamentos(cd: str, de: str, ate: str) -> list[dict]:
    """Efetivo apontado no período. Lista vazia é resposta legítima: o
    apontamento é opcional e o painel funciona sem ele."""
    with get_db() as db:
        rows = db.execute(
            "SELECT data, turno, funcao, previsto, presente, entrada, usuario, atualizado_em "
            "FROM prod_apontamento WHERE cd=? AND data BETWEEN ? AND ? "
            "ORDER BY data, turno, funcao", (cd, de, ate)).fetchall()
    return [dict(r) for r in rows]


def _turno_arg():
    turno = (request.args.get("turno") or "noturno").strip().lower()
    return turno if turno in ("noturno", "diurno", "todos") else "noturno"


@produtividade_bp.errorhandler(PeriodoInvalido)
def _erro_periodo(e):
    return jsonify({"error": str(e)}), 400


# ── páginas ──────────────────────────────────────────────────────────
@produtividade_bp.route("/produtividade")
def pagina():
    from flask import redirect, url_for
    if not session.get("user"):
        return redirect(url_for("login_page"))
    if not tem_acesso_modulo("produtividade"):
        return redirect(url_for("index"))
    return render_template("produtividade.html", user=session.get("user"),
                           pode_editar=session.get("perfil") in ("admin", "master"))


# ── API ──────────────────────────────────────────────────────────────
@produtividade_bp.route("/api/produtividade/cds")
@login_required
@modulo_required("produtividade")
def api_cds():
    """CDs consolidados que o usuário pode ver. Inclui os do cadastro dele,
    pra um CD novo aparecer antes do primeiro upload."""
    mapa = get_mapa_consolidacao()
    with get_db() as db:
        ult_emp = {r["empresa"]: r["m"] for r in db.execute(
            "SELECT empresa, MAX(data_op) m FROM prod_instrucao WHERE tem_painel=1 "
            "GROUP BY empresa").fetchall()}
    com_dado = set(ult_emp)
    raw = (session.get("unidades_permitidas", "") or "") + "," + \
          (session.get("unidades_vinculadas", "") or "")
    do_cadastro = {u.strip().upper() for u in raw.split(",") if u.strip()}
    visiveis = {_consolidada(e, mapa) for e in (com_dado | do_cadastro) if _pode_ver_empresa(e)}
    com = sorted({_consolidada(e, mapa) for e in com_dado if _pode_ver_empresa(e)})
    ultimo = {}
    for e, m in ult_emp.items():
        if _pode_ver_empresa(e):
            c = _consolidada(e, mapa)
            ultimo[c] = max(ultimo.get(c) or "", m or "") or None
    return jsonify({"cds": sorted(visiveis) or ["CDA"], "com_dado": com, "ultimo_dia": ultimo})


@produtividade_bp.route("/api/produtividade/upload", methods=["POST"])
@login_required
@modulo_required("produtividade")
@admin_required
@csrf_required
def api_upload():
    """Vários arquivos numa tacada — painéis diários e resumo, misturados.
    O tipo de cada um é descoberto pelo cabeçalho."""
    arquivos = [a for a in request.files.getlist("arquivo") if a and a.filename]
    if not arquivos:
        return jsonify({"error": "Escolha ao menos um arquivo."}), 400

    resultado, erros = [], []
    lidos = []
    for arq in arquivos:
        sufixo = os.path.splitext(arq.filename)[1].lower() or ".xls"
        fd, caminho = tempfile.mkstemp(suffix=sufixo)
        os.close(fd)
        try:
            arq.save(caminho)
            info = ler_arquivo(caminho)
        except ValueError as e:
            erros.append(f"{arq.filename}: {e}")
            continue
        except Exception:                      # arquivo corrompido etc.
            erros.append(f"{arq.filename}: não foi possível ler o arquivo "
                         f"(formato não suportado ou arquivo corrompido).")
            continue
        finally:
            try:
                os.unlink(caminho)
            except OSError:
                pass
        falta_empresa = preencher_empresa(info, request.form.get("empresa", ""))
        if falta_empresa:
            erros.append(f"{arq.filename}: {falta_empresa}")
            continue
        sem_acesso = {r["empresa"] for r in info["registros"]
                      if not _pode_ver_empresa(r["empresa"])}
        if sem_acesso:
            emp = sorted(sem_acesso)
            erros.append(f"{arq.filename}: sem acesso "
                         f"{'à empresa' if len(emp) == 1 else 'às empresas'} {', '.join(emp)}.")
            continue
        lidos.append((arq.filename, info))

    # Grava o PAINEL antes do resumo: o painel é quem cria a linha com peso;
    # a ordem não muda o resultado (a gravação é por coluna), mas deixa o
    # log mais legível.
    lidos.sort(key=lambda x: 0 if x[1]["tipo"] == TIPO_PAINEL else 1)
    with get_db() as db:
        for nome, info in lidos:
            n = _gravar(db, info["tipo"], info["registros"])
            dias = sorted({r["data_op"] for r in info["registros"] if r.get("data_op")})
            resultado.append({"arquivo": nome, "tipo": info["tipo"], "instrucoes": n,
                              "de": dias[0] if dias else None,
                              "ate": dias[-1] if dias else None,
                              "faltando": info["faltando"]})

        # quantas instruções ainda estão só com um dos dois relatórios
        incompletas = db.execute(
            "SELECT SUM(tem_painel=1 AND tem_resumo=0) sem_resumo, "
            "       SUM(tem_painel=0 AND tem_resumo=1) sem_painel "
            "FROM prod_instrucao").fetchone()

    registrar_log(session.get("user", ""), "produtividade_upload",
                  detalhe=f"arquivos={len(arquivos)} ok={len(resultado)} erros={len(erros)}")
    return jsonify({"ok": bool(resultado), "arquivos": resultado, "erros": erros,
                    "sem_resumo": incompletas["sem_resumo"] or 0,
                    "sem_painel": incompletas["sem_painel"] or 0})


@produtividade_bp.route("/api/produtividade/dados")
@login_required
@modulo_required("produtividade")
def api_dados():
    cd = (request.args.get("cd") or "").strip().upper()
    if not cd:
        return jsonify({"error": "Informe o CD."}), 400
    turno = _turno_arg()
    de, ate = _periodo()
    empresas = _empresas_do_cd(cd)
    if not empresas:
        return jsonify({"error": "Você não tem acesso a esse CD."}), 403
    params = _parametros_do_cd(cd)
    marc = _marcacoes(empresas)
    filtro_turno = "" if turno == "todos" else turno
    out = calcular(_registros(empresas, de, ate), params, filtro_turno, set(marc),
                   _apontamentos(cd, de, ate))
    for x in out["instrucoes"]:
        m = marc.get(f'{x["empresa"]}|{x["instrucao"]}')
        if m:
            x["marcacao"] = m
    out.update({"cd": cd, "empresas": empresas, "de": de, "ate": ate,
                "pendentes_painel": _pendentes_painel(empresas, de, ate),
                "ultimo_dia": _ultimo_dia(empresas)})

    # Mesmo número de dias imediatamente antes — base da variação dos KPIs.
    out["anterior"] = None
    try:
        d0, d1 = _dt.date.fromisoformat(de), _dt.date.fromisoformat(ate)
        n = (d1 - d0).days + 1
        ant_ate, ant_de = d0 - _dt.timedelta(days=1), d0 - _dt.timedelta(days=n)
        regs_ant = _registros(empresas, ant_de.isoformat(), ant_ate.isoformat())
        if regs_ant:
            res_ant = calcular(regs_ant, params, filtro_turno, set(marc))["resumo"]
            if res_ant["instrucoes"]:
                out["anterior"] = {"de": ant_de.isoformat(), "ate": ant_ate.isoformat(), **res_ant}
    except (ValueError, OverflowError):
        pass
    return jsonify(out)


@produtividade_bp.route("/api/produtividade/projecao")
@login_required
@modulo_required("produtividade")
def api_projecao():
    cd = (request.args.get("cd") or "").strip().upper()
    empresas = _empresas_do_cd(cd)
    if not empresas:
        return jsonify({"error": "Você não tem acesso a esse CD."}), 403
    def numero(nome, vazio):
        txt = (request.args.get(nome) or "").strip().replace(",", ".")
        if not txt:
            return vazio
        v = float(txt)                      # ValueError tratado abaixo
        if not math.isfinite(v) or v < 0 or v > 100000:
            raise ValueError
        return v

    try:
        toneladas = numero("toneladas", 0.0)
    except ValueError:
        return jsonify({"error": "Informe as toneladas a separar com um número válido."}), 400
    try:
        redespacho = numero("redespacho", None)
    except ValueError:
        return jsonify({"error": "Informe o redespacho previsto em toneladas, com um número válido."}), 400
    cam = (request.args.get("caminhoes") or "").strip()
    if cam and not re.fullmatch(r"[0-9]{1,5}", cam):
        return jsonify({"error": "Informe o número de caminhões com um valor inteiro."}), 400
    caminhoes = int(cam) if cam and int(cam) > 0 else None
    turno = _turno_arg()
    de, ate = _periodo()
    params = _parametros_do_cd(cd)
    calc = calcular(_registros(empresas, de, ate), params,
                    "" if turno == "todos" else turno, _desconsideradas(empresas))
    return jsonify(projetar(toneladas, calc["base_projecao"], params, caminhoes,
                            redespacho, calc["por_dia"]))


@produtividade_bp.route("/api/produtividade/parametros")
@login_required
@modulo_required("produtividade")
def api_parametros():
    cd = (request.args.get("cd") or "").strip().upper()
    if not _empresas_do_cd(cd):
        return jsonify({"error": "Você não tem acesso a esse CD."}), 403
    return jsonify({"cd": cd, "parametros": _parametros_do_cd(cd),
                    "padrao": PARAMETROS_PADRAO})


@produtividade_bp.route("/api/produtividade/parametros", methods=["POST"])
@login_required
@modulo_required("produtividade")
@admin_required
@csrf_required
def api_parametros_salvar():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400
    cd = str(d.get("cd", "")).strip().upper()
    if not _empresas_do_cd(cd):
        return jsonify({"error": "Você não tem acesso a esse CD."}), 403
    novos = {}
    enviados = d.get("parametros") or {}
    if not isinstance(enviados, dict):
        return jsonify({"error": "Parâmetros em formato inválido."}), 400
    for k, padrao in PARAMETROS_PADRAO.items():
        if k not in enviados:
            continue
        rotulo, teto = _PARAM_META.get(k, (k, 100000))
        try:
            v = float(str(enviados[k]).replace(",", "."))
        except (TypeError, ValueError):
            return jsonify({"error": f"Informe um valor numérico em “{rotulo}”."}), 400
        if not math.isfinite(v):
            return jsonify({"error": f"Informe um valor numérico em “{rotulo}”."}), 400
        if v < 0:
            return jsonify({"error": f"“{rotulo}” não pode ser negativo."}), 400
        if v > teto:
            return jsonify({"error": f"“{rotulo}” aceita no máximo {teto}."}), 400
        novos[k] = int(round(v)) if isinstance(padrao, int) else v
    atual = _parametros_do_cd(cd)
    atual.update(novos)
    if atual["turno_h"] <= 0:
        return jsonify({"error": "A duração do turno deve ser maior que zero."}), 400
    for atividade, rotulo in (("separacao", "separação"), ("embarque", "embarque")):
        if atual[f"{atividade}_inicio"] == atual[f"{atividade}_fim"]:
            return jsonify({"error": f"O início e o término da {rotulo} não podem ser a mesma hora."}), 400
    if atual["noturno_inicio"] == atual["noturno_fim"]:
        return jsonify({"error": "O início e o término do turno noturno devem ser diferentes."}), 400
    if atual["trabalho_continuo_min"] <= 0:
        return jsonify({"error": "“Trabalho contínuo antes da pausa térmica” deve ser maior que zero."}), 400
    with get_db() as db:
        db.execute(
            "INSERT INTO prod_parametros(cd, dados, atualizado_em, usuario) VALUES (?,?,?,?) "
            "ON CONFLICT(cd) DO UPDATE SET dados=excluded.dados, "
            "atualizado_em=excluded.atualizado_em, usuario=excluded.usuario",
            (cd, json.dumps(atual), _agora(), session.get("user", "")))
    registrar_log(session.get("user", ""), "produtividade_parametros",
                  alvo=cd, detalhe=json.dumps(novos, ensure_ascii=False))
    # Ao contrário do Giro, aqui NÃO precisa reenviar arquivo: o banco guarda
    # a instrução crua e o cálculo roda na hora com o parâmetro atual.
    return jsonify({"ok": True, "parametros": atual})


@produtividade_bp.route("/api/produtividade/desconsiderar", methods=["POST"])
@login_required
@modulo_required("produtividade")
@admin_required
@csrf_required
def api_desconsiderar():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400
    emp = str(d.get("empresa", "")).strip().upper()
    inst = str(d.get("instrucao", "")).strip()
    if not emp or not inst:
        return jsonify({"error": "Informe a empresa e a instrução."}), 400
    if not _pode_ver_empresa(emp):
        return jsonify({"error": "Você não tem acesso a essa empresa."}), 403
    with get_db() as db:
        if d.get("desconsiderar"):
            db.execute(
                "INSERT INTO prod_desconsiderar(empresa, instrucao, motivo, usuario, quando) "
                "VALUES (?,?,?,?,?) ON CONFLICT(empresa, instrucao) DO UPDATE SET "
                "motivo=excluded.motivo, usuario=excluded.usuario, quando=excluded.quando",
                (emp, inst, str(d.get("motivo", ""))[:200], session.get("user", ""), _agora()))
        else:
            db.execute("DELETE FROM prod_desconsiderar WHERE empresa=? AND instrucao=?",
                       (emp, inst))
    registrar_log(session.get("user", ""), "produtividade_desconsiderar",
                  alvo=f"{emp}/{inst}",
                  detalhe=("marcou" if d.get("desconsiderar") else "desmarcou")
                          + (f" — {d.get('motivo')}" if d.get("motivo") else ""))
    return jsonify({"ok": True})


# ── APONTAMENTO DO EFETIVO ───────────────────────────────────────────
# O WMS diz quem separou e quem conferiu, não quem estava na casa — e o
# apoio não aparece em relatório nenhum. Sem lista de presença (usuário,
# 23/09/2026), o efetivo do dia é digitado aqui e vira o H-H do painel.
_HORA_RE = re.compile(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


@produtividade_bp.route("/api/produtividade/apontamento")
@login_required
@modulo_required("produtividade")
def api_apontamento():
    cd = (request.args.get("cd") or "").strip().upper()
    if not cd:
        return jsonify({"error": "Informe o CD."}), 400
    if not _empresas_do_cd(cd):
        return jsonify({"error": "Você não tem acesso a esse CD."}), 403
    de, ate = _periodo()
    return jsonify({"cd": cd, "de": de, "ate": ate, "funcoes": list(FUNCOES),
                    "apontamentos": _apontamentos(cd, de, ate),
                    # mesma régua do admin_required abaixo, pra tela não
                    # oferecer um campo que o POST vai recusar
                    "pode_editar": session.get("perfil") in ("admin", "master")})


@produtividade_bp.route("/api/produtividade/apontamento", methods=["POST"])
@login_required
@modulo_required("produtividade")
@admin_required
@csrf_required
def api_apontamento_salvar():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({"error": "Requisição em formato inválido."}), 400
    cd = str(d.get("cd", "")).strip().upper()
    if not cd or not _empresas_do_cd(cd):
        return jsonify({"error": "Você não tem acesso a esse CD."}), 403
    data = str(d.get("data", "")).strip()
    try:
        _dt.date.fromisoformat(data)
    except ValueError:
        return jsonify({"error": "Informe a data do apontamento (dia inteiro, formato AAAA-MM-DD)."}), 400
    turno = str(d.get("turno", "")).strip().lower()
    if turno not in ("noturno", "diurno"):
        return jsonify({"error": "Informe o turno: noturno ou diurno."}), 400

    linhas = d.get("funcoes")
    if not isinstance(linhas, dict):
        return jsonify({"error": "Informe o efetivo por função."}), 400

    def inteiro(v, rotulo):
        txt = str(v if v is not None else "").strip()
        if txt == "":
            return None
        if not re.fullmatch(r"[0-9]{1,3}", txt):
            raise ValueError(f"{rotulo} deve ser um número inteiro de 0 a 999.")
        return int(txt)

    gravar, apagar = [], []
    agora, usuario = _agora(), session.get("user", "")
    for funcao, valores in linhas.items():
        f = str(funcao).strip().lower()
        if f not in FUNCOES:
            return jsonify({"error": f"Função desconhecida: {funcao}."}), 400
        if not isinstance(valores, dict):
            return jsonify({"error": f"Informe previsto e presente para {f}."}), 400
        try:
            previsto = inteiro(valores.get("previsto"), "Efetivo previsto")
            presente = inteiro(valores.get("presente"), "Efetivo presente")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        entrada = str(valores.get("entrada") or "").strip()
        if entrada and not _HORA_RE.match(entrada):
            return jsonify({"error": "Informe o horário de entrada no formato HH:MM."}), 400
        if previsto is None and presente is None and not entrada:
            apagar.append(f)          # linha em branco apaga o apontamento daquela função
        else:
            gravar.append((cd, data, turno, f, previsto, presente, entrada, usuario, agora))

    with get_db() as db:
        if gravar:
            db.executemany(
                "INSERT INTO prod_apontamento(cd, data, turno, funcao, previsto, presente, "
                "entrada, usuario, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(cd, data, turno, funcao) DO UPDATE SET "
                "previsto=excluded.previsto, presente=excluded.presente, "
                "entrada=excluded.entrada, usuario=excluded.usuario, "
                "atualizado_em=excluded.atualizado_em", gravar)
        for f in apagar:
            db.execute("DELETE FROM prod_apontamento WHERE cd=? AND data=? AND turno=? AND funcao=?",
                       (cd, data, turno, f))
    registrar_log(usuario, "produtividade_apontamento", alvo=f"{cd}/{data}/{turno}",
                  detalhe=json.dumps(linhas, ensure_ascii=False)[:400])
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT data, turno, funcao, previsto, presente, entrada, usuario, atualizado_em "
            "FROM prod_apontamento WHERE cd=? AND data=? AND turno=? ORDER BY funcao",
            (cd, data, turno)).fetchall()]
    return jsonify({"ok": True, "apontamentos": rows})
