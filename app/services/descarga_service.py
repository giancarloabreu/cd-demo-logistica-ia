"""
Tempo de descarga das carretas — fase 2 do painel de carretas (18/10/2026).

Regra do usuário:
  tempo de descarga = da CHEGADA da carreta ao CD até o "Término Desembarque"
  (leitura da última caixa) do painel de entrada do WMS, carregado todo dia.

- A chegada é a do NOSSO sistema (estadia no raio do CD, ver
  app/services/carretas_viagem.py), contada da PRIMEIRA chegada da visita: se a
  carreta saiu e voltou, o tempo fora aparece à parte, não é descontado.
- Uma carreta com instrução no CDA e outra no CDB descarrega uma vez só: o
  tempo da viagem é a MÉDIA das instruções dela.
- Redespacho não entra. Ele sai por dois caminhos: o tipo de movimentação
  (lista editável na tela, padrão = tudo que tiver "REDESP" no nome) e as
  instruções de entrada que já apareceram num card de redespacho
  (`redespacho_instrucao`, gravada sempre que a Fila Redespacho é lida).
- O relatório é por viagem, identificada pela AE da OpenTech (ou pelo RVX da
  Ravex).

O painel de entrada tem cabeçalho em dois níveis ("Grupo > Detalhe") e a
posição das colunas muda entre relatórios: tudo é achado pelo NOME exato.
Nome de conferente e de motorista não são lidos.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

import pandas as pd

from app.database import get_db, get_preferencia, set_preferencia
from app.services.carretas_viagem import (_agora, _dt, _hm, _txt, garantir_tabelas,
                                          local_de)

# nome interno -> "grupo > detalhe" (comparação sem acento, sem espaço)
COLUNAS = {
    "instrucao":   "Escala > Instrução",
    "data_op":     "Escala > Data Operação",
    "nf":          "Escala > Nota Fiscal",
    "caixas":      "Escala > Caixas",
    "temperatura": "Escala > Temperatura",
    "tipo_mov":    "Escala > Tipo Movimentação",
    "placa":       "Veículo > Placa",
    "transportadora": "Veículo > Transportadora",
    "origem":      "Veículo > Terceiro",
    "status_veic": "Status > Status Veículo",
    "status_op":   "Status > Status Operação",
    "peso_prev":   "Previsto > Peso Líquido",
    "peso_real":   "Realizado > Peso Líquido",
    "chegada_wms": "Estadia > Chegada",
    "inicio":      "Operação > Início Desembarque",
    "termino":     "Operação > Término Desembarque",
    "finalizacao": "Operação > Finalização Desembarque",
    "fechamento":  "Faturamento > Fechamento Status",
    "doca":        "Doca > Nome",
    "empresa":     "Empresa > Sigla",
}
OBRIGATORIAS = ("instrucao", "placa", "empresa", "inicio", "termino")
_DATAS = ("data_op", "chegada_wms", "inicio", "termino", "finalizacao", "fechamento")
_NUMEROS = ("caixas", "peso_prev", "peso_real")

# Uma chegada só vale para a descarga se foi até 3 dias antes do término: além
# disso é outra viagem da mesma placa (a carreta faz uma viagem por dia).
JANELA_CHEGADA_H = 72
# A carreta precisa estar no CD quando o desembarque começa: visita encerrada
# mais de 2 h antes do início é de uma passagem anterior, não desta descarga.
FOLGA_SAIDA_H = 2

CHAVE_TIPOS = "descarga_tipos_excluidos"
TIPOS_PADRAO = ["REDESP"]

_tabelas_ok: set = set()


def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9>]", "", t.lower())


def _texto(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def _garantir(db) -> None:
    from app import database
    chave = str(database.DB_PATH)
    garantir_tabelas(db)
    if chave in _tabelas_ok:
        return
    db.executescript("""
        CREATE TABLE IF NOT EXISTS descarga_wms (
            empresa        TEXT NOT NULL,
            instrucao      TEXT NOT NULL,
            placa          TEXT NOT NULL DEFAULT '',
            data_op        TEXT NOT NULL DEFAULT '',
            nf             TEXT NOT NULL DEFAULT '',
            caixas         REAL,
            temperatura    TEXT NOT NULL DEFAULT '',
            tipo_mov       TEXT NOT NULL DEFAULT '',
            transportadora TEXT NOT NULL DEFAULT '',
            origem         TEXT NOT NULL DEFAULT '',
            status_veic    TEXT NOT NULL DEFAULT '',
            status_op      TEXT NOT NULL DEFAULT '',
            peso_prev      REAL,
            peso_real      REAL,
            chegada_wms    TEXT,
            inicio         TEXT,
            termino        TEXT,
            finalizacao    TEXT,
            fechamento     TEXT,
            doca           TEXT NOT NULL DEFAULT '',
            carregado_em   TEXT NOT NULL,
            carregado_por  TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (empresa, instrucao)
        );
        CREATE INDEX IF NOT EXISTS idx_descarga_wms_termino ON descarga_wms(termino);
        CREATE INDEX IF NOT EXISTS idx_descarga_wms_placa ON descarga_wms(placa);

        CREATE TABLE IF NOT EXISTS redespacho_instrucao (
            instrucao TEXT PRIMARY KEY,
            visto_em  TEXT NOT NULL
        );
    """)
    _tabelas_ok.add(chave)


# ---------------------------------------------------------------------------
# Leitura do painel de entrada
# ---------------------------------------------------------------------------

def ler_painel_entrada(caminho: str) -> list[dict]:
    """Registros do painel de entrada do WMS, um por instrução. Levanta
    ValueError com mensagem para a tela se o arquivo não for esse painel."""
    from app.services.excel_reader import read_excel_flexible
    df = read_excel_flexible(caminho)
    por_nome = {_norm(c): c for c in df.columns}
    faltando = [COLUNAS[k] for k in OBRIGATORIAS if _norm(COLUNAS[k]) not in por_nome]
    if faltando:
        raise ValueError("arquivo não reconhecido como painel de entrada do WMS. "
                         + ("Coluna ausente: " if len(faltando) == 1 else "Colunas ausentes: ")
                         + ", ".join(f"“{c}”" for c in faltando) + ".")
    dados = pd.DataFrame({k: (df[por_nome[_norm(a)]] if _norm(a) in por_nome
                              else pd.Series([None] * len(df), index=df.index))
                          for k, a in COLUNAS.items()})
    for c in _DATAS:
        # valor a valor: com o vetor inteiro o pandas deduz o formato pelo
        # primeiro ("…:58.080000") e anula os que vêm sem fração ("…:53:00")
        dados[c] = dados[c].map(lambda x: pd.to_datetime(x, errors="coerce"))
    for c in _NUMEROS:
        dados[c] = pd.to_numeric(dados[c], errors="coerce")

    registros = []
    for _, r in dados.iterrows():
        instr = _texto(r["instrucao"]).split(".")[0]
        empresa = _texto(r["empresa"]).upper()
        if not instr or not empresa:
            continue
        reg = {"instrucao": instr, "empresa": empresa}
        for c in COLUNAS:
            if c in ("instrucao", "empresa"):
                continue
            v = r[c]
            if c in _DATAS:
                reg[c] = None if pd.isna(v) else v.strftime(
                    "%Y-%m-%d" if c == "data_op" else "%Y-%m-%d %H:%M:%S")
            elif c in _NUMEROS:
                reg[c] = None if pd.isna(v) else float(v)
            else:
                reg[c] = _texto(v)
        reg["placa"] = re.sub(r"[^A-Z0-9]", "", reg["placa"].upper())
        reg["nf"] = reg["nf"].split(".")[0] if re.fullmatch(r"\d+\.0", reg["nf"]) else reg["nf"]
        reg["data_op"] = reg["data_op"] or ""
        registros.append(reg)
    return registros


def gravar(registros: list[dict], usuario: str = "") -> dict:
    """Grava/atualiza por (empresa, instrução). O painel do dia seguinte traz
    o término que faltava; um painel mais antigo não apaga um término já
    gravado."""
    agora = _txt(_agora())
    novas = atualizadas = 0
    with get_db() as db:
        _garantir(db)
        for r in registros:
            ant = db.execute("SELECT * FROM descarga_wms WHERE empresa=? AND instrucao=?",
                             (r["empresa"], r["instrucao"])).fetchone()
            campos = {k: r.get(k) for k in COLUNAS if k not in ("empresa", "instrucao")}
            if ant is not None:
                for k in ("inicio", "termino", "finalizacao", "fechamento", "chegada_wms"):
                    if not campos.get(k) and ant[k]:
                        campos[k] = ant[k]
                atualizadas += 1
            else:
                novas += 1
            cols = ["empresa", "instrucao", *campos.keys(), "carregado_em", "carregado_por"]
            vals = [r["empresa"], r["instrucao"], *campos.values(), agora, usuario]
            db.execute(
                f"INSERT OR REPLACE INTO descarga_wms ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", vals)
    return {"lidas": len(registros), "novas": novas, "atualizadas": atualizadas}


def registrar_instrucoes_redespacho(instrucoes) -> None:
    """Toda instrução de entrada que aparece num card de redespacho fica
    marcada: depois que a carga é recebida ela some da Fila, mas o painel de
    entrada do WMS ainda a traz no dia seguinte."""
    lista = []
    for i in instrucoes or []:
        for parte in re.split(r"[/|,;]", str(i or "")):
            p = _texto(parte).split(".")[0]
            if p:
                lista.append(p)
    if not lista:
        return
    agora = _txt(_agora())
    with get_db() as db:
        _garantir(db)
        db.executemany("INSERT OR IGNORE INTO redespacho_instrucao (instrucao, visto_em) VALUES (?,?)",
                       [(p, agora) for p in dict.fromkeys(lista)])


def tipos_excluidos() -> list[str]:
    v = get_preferencia(CHAVE_TIPOS, None)
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else list(TIPOS_PADRAO)


def salvar_tipos_excluidos(tipos: list[str]) -> list[str]:
    lista = list(dict.fromkeys(str(t).strip() for t in (tipos or []) if str(t).strip()))[:30]
    set_preferencia(CHAVE_TIPOS, lista)
    return lista


def _tipo_fora(tipo: str, excluidos: list[str]) -> bool:
    t = _norm(tipo)
    return any(_norm(x) and _norm(x) in t for x in excluidos)


# ---------------------------------------------------------------------------
# Chegada da carreta para cada instrução
# ---------------------------------------------------------------------------

def _visita_da_instrucao(db, placa: str, local: str, inicio: datetime | None,
                         termino: datetime) -> tuple[list[dict], str]:
    """(estadias da visita, motivo se não achou)."""
    if not placa:
        return [], "sem placa no painel"
    ini_busca = _txt(termino - timedelta(hours=JANELA_CHEGADA_H + 48))
    rows = [dict(r) for r in db.execute(
        """SELECT * FROM carretas_estadia
           WHERE (placa=? OR placa_carreta=? OR placa_veiculo=?) AND local=?
             AND chegada_em >= ? AND chegada_em <= ?
           ORDER BY chegada_em""",
        (placa, placa, placa, local, ini_busca, _txt(termino))).fetchall()]
    if not rows:
        return [], "sem chegada registrada no raio do CD"
    por_visita: dict = {}
    for e in rows:
        por_visita.setdefault(e["visita"] or e["id"], []).append(e)
    # visita que começou por último antes do término
    es = max(por_visita.values(), key=lambda v: v[0]["chegada_em"])
    # estadias da mesma visita que começaram antes da janela de busca
    vid = es[0]["visita"]
    if vid:
        es = [dict(r) for r in db.execute(
            "SELECT * FROM carretas_estadia WHERE visita=? AND chegada_em <= ? ORDER BY chegada_em",
            (vid, _txt(termino))).fetchall()] or es
    primeira = _dt(es[0]["chegada_em"])
    if primeira is None or termino - primeira > timedelta(hours=JANELA_CHEGADA_H):
        return [], "chegada registrada mais de 3 dias antes do término"
    ultima = es[-1]
    if ultima["saida_em"] and ultima["motivo_saida"] == "saiu":
        saiu = _dt(ultima["saida_em"])
        ref = inicio or termino
        if saiu and saiu < ref - timedelta(hours=FOLGA_SAIDA_H):
            return [], "veículo fora do CD no início do desembarque"
    return es, ""


def _fora_min(es: list[dict], ate: datetime) -> tuple[float, list[dict]]:
    fora, idas = 0.0, []
    for a, b in zip(es, es[1:]):
        s, v = _dt(a["saida_em"]), _dt(b["chegada_em"])
        if s and v and v > s and a["motivo_saida"] == "saiu" and v <= ate:
            fora += (v - s).total_seconds() / 60
            idas.append({"saiu": s.strftime("%d/%m %H:%M"), "voltou": v.strftime("%d/%m %H:%M"),
                         "fora": _hm((v - s).total_seconds() / 60)})
    return fora, idas


def _viagem_da_visita(db, es: list[dict], placa: str, nfs: list[str], termino: datetime) -> str:
    """AE (ou RVX) da viagem: pela NF do painel, se vier; senão a AE gravada
    na chegada; senão o RVX mais recente da placa antes do término."""
    for nf in nfs:
        r = db.execute("SELECT viagem FROM carretas_viagem_nf WHERE placa=? AND nf=?",
                       (placa, nf)).fetchone()
        if r:
            return r["viagem"]
    for e in es:
        if e["ae"]:
            return e["ae"]
    r = db.execute("""SELECT seq FROM carretas_rvx WHERE placa=? AND criado_em <= ?
                      ORDER BY seq DESC LIMIT 1""", (placa, _txt(termino))).fetchone()
    return f"RVX-{r['seq']:06d}" if r else ""


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

def _dm(s) -> str:
    d = _dt(s)
    return d.strftime("%d/%m %H:%M") if d else ""


def relatorio(de: str, ate: str, empresas: list[str] | None = None) -> dict:
    """Descargas com término entre `de` e `ate` (AAAA-MM-DD, inclusive).
    `empresas` vazio = todas (perfil sem restrição de unidade)."""
    excluidos = tipos_excluidos()
    ini = f"{de} 00:00:00"
    fim = f"{ate} 23:59:59"
    filtro = {e.strip().upper() for e in (empresas or []) if e.strip()}
    from app.database import get_mapa_consolidacao
    mapa = get_mapa_consolidacao()

    with get_db() as db:
        _garantir(db)
        redesp = {r["instrucao"] for r in db.execute("SELECT instrucao FROM redespacho_instrucao")}
        linhas = [dict(r) for r in db.execute(
            """SELECT * FROM descarga_wms
               WHERE (termino BETWEEN ? AND ?)
                  OR (termino IS NULL AND data_op BETWEEN ? AND ?)""",
            (ini, fim, de, ate)).fetchall()]
        if filtro:
            linhas = [r for r in linhas if r["empresa"] in filtro]

        tipos: dict = {}
        viagens: dict = {}
        sem_chegada, em_aberto = [], []
        fora_conta = 0
        for r in linhas:
            t = r["tipo_mov"] or "(sem tipo)"
            info = tipos.setdefault(t, {"tipo": t, "instrucoes": 0,
                                        "considerado": not _tipo_fora(t, excluidos)})
            info["instrucoes"] += 1
            if not info["considerado"] or r["instrucao"] in redesp:
                fora_conta += 1
                continue
            base = {"instrucao": r["instrucao"], "empresa": r["empresa"], "placa": r["placa"],
                    "inicio": _dm(r["inicio"]), "termino": _dm(r["termino"]),
                    "status_op": r["status_op"], "data_op": r["data_op"]}
            termino = _dt(r["termino"])
            if termino is None:
                em_aberto.append(base)
                continue
            inicio = _dt(r["inicio"])
            local = local_de(r["empresa"], mapa)
            es, motivo = _visita_da_instrucao(db, r["placa"], local, inicio, termino)
            if not es:
                sem_chegada.append({**base, "motivo": motivo})
                continue
            chegada = _dt(es[0]["chegada_em"])
            total = (termino - chegada).total_seconds() / 60
            espera = (inicio - chegada).total_seconds() / 60 if inicio and inicio >= chegada else None
            desemb = (termino - inicio).total_seconds() / 60 if inicio and termino >= inicio else None
            chave = es[0]["visita"] or es[0]["id"]
            v = viagens.setdefault(chave, {
                "visita": chave, "placa": r["placa"], "local": local,
                "chegada": es[0]["chegada_em"], "_es": es, "instrucoes": [], "_nfs": []})
            # as estadias foram lidas até o término desta instrução; fica a
            # leitura da que terminou por último (tempo fora até o fim)
            if v["instrucoes"] and r["termino"] > max(i["_termino"] for i in v["instrucoes"]):
                v["_es"] = es
            v["instrucoes"].append({**base, "total_min": round(total), "espera_min":
                                    None if espera is None else round(espera),
                                    "desembarque_min": None if desemb is None else round(desemb),
                                    "_termino": r["termino"], "_inicio": r["inicio"]})
            if r["nf"]:
                v["_nfs"].append(r["nf"])

        itens = []
        for v in viagens.values():
            ins = v["instrucoes"]
            ult_termino = max(i["_termino"] for i in ins)
            prim_inicio = min((i["_inicio"] for i in ins if i["_inicio"]), default=None)
            fora, idas = _fora_min(v["_es"], _dt(ult_termino))

            def media(campo):
                xs = [i[campo] for i in ins if i[campo] is not None]
                return round(sum(xs) / len(xs)) if xs else None
            total = media("total_min")
            itens.append({
                "viagem": _viagem_da_visita(db, v["_es"], v["placa"], v["_nfs"], _dt(ult_termino)),
                "placa": v["placa"],
                "empresas": " · ".join(sorted({i["empresa"] for i in ins})),
                "instrucoes": [{k: x for k, x in i.items() if not k.startswith("_")} for i in ins],
                "chegada": _dm(v["chegada"]),
                "inicio": _dm(prim_inicio),
                "termino": _dm(ult_termino),
                "_termino": ult_termino,
                "total_min": total,
                "total": _hm(total) if total is not None else "",
                "espera_min": media("espera_min"),
                "desembarque_min": media("desembarque_min"),
                "fora_min": round(fora),
                "fora": _hm(fora) if fora else "",
                "idas": idas,
                "media_de": len(ins),
            })
        itens.sort(key=lambda x: x["_termino"], reverse=True)
        for x in itens:
            x.pop("_termino", None)

    def media_de(campo):
        xs = [x[campo] for x in itens if x[campo] is not None]
        return round(sum(xs) / len(xs)) if xs else None

    por_empresa = {}
    for x in itens:
        for i in x["instrucoes"]:
            por_empresa.setdefault(i["empresa"], []).append(i["total_min"])
    media_total = media_de("total_min")
    with get_db() as db:
        _garantir(db)
        onde, args = "", []
        if filtro:
            onde = f"WHERE empresa IN ({','.join('?' * len(filtro))})"
            args = sorted(filtro)
        ult = db.execute(f"""SELECT carregado_em, carregado_por, COUNT(*) n FROM descarga_wms
                             {onde}
                             GROUP BY carregado_em, carregado_por
                             ORDER BY carregado_em DESC LIMIT 1""", args).fetchone()
    return {
        "de": de, "ate": ate,
        "media_min": media_total,
        "media": _hm(media_total) if media_total is not None else "",
        "media_espera_min": media_de("espera_min"),
        "media_desembarque_min": media_de("desembarque_min"),
        "descargas": len(itens),
        "por_empresa": [{"empresa": e, "media_min": round(sum(v) / len(v)),
                         "media": _hm(sum(v) / len(v)), "instrucoes": len(v)}
                        for e, v in sorted(por_empresa.items())],
        "itens": itens,
        "em_aberto": em_aberto,
        "sem_chegada": sem_chegada,
        "fora_da_conta": fora_conta,
        "tipos": sorted(tipos.values(), key=lambda t: -t["instrucoes"]),
        "tipos_excluidos": excluidos,
        "ultimo_painel": ({"em": _dm(ult["carregado_em"]), "por": ult["carregado_por"],
                           "instrucoes": ult["n"]} if ult else None),
    }


def situacao_do_card(instrucoes: list[str], empresas: list[str]) -> dict:
    """Situação da descarga das instruções de um card, pelo último painel de
    entrada carregado: {"inicio", "termino"} (o término só quando TODAS as
    instruções terminaram). Vazio se o painel ainda não trouxe nenhuma."""
    instrs = [i for i in dict.fromkeys(_texto(x).split(".")[0] for x in instrucoes) if i]
    if not instrs:
        return {}
    emp = {e.strip().upper() for e in empresas if e.strip()}
    with get_db() as db:
        _garantir(db)
        marca = ",".join("?" * len(instrs))
        rows = [dict(r) for r in db.execute(
            f"SELECT empresa, instrucao, inicio, termino FROM descarga_wms WHERE instrucao IN ({marca})",
            instrs).fetchall()]
    if emp:
        rows = [r for r in rows if r["empresa"] in emp] or rows
    if not rows:
        return {}
    inicios = [r["inicio"] for r in rows if r["inicio"]]
    terminos = [r["termino"] for r in rows if r["termino"]]
    achadas = {r["instrucao"] for r in rows}
    todas = achadas >= set(instrs) and len(terminos) == len(rows)
    return {
        "inicio": min(inicios) if inicios else "",
        "termino": max(terminos) if todas and terminos else "",
    }
