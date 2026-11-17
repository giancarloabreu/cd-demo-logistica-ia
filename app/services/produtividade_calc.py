"""
Cálculo do painel "Produtividade de Separação".

Três números em vez de um — a partir da análise dos dias reais 01–15/09/2026:

  RITMO          quão rápido a pessoa trabalha QUANDO está trabalhando:
                 t/h e caixas/h sobre a janela ativa dela no turno.
  APROVEITAMENTO quanto do turno foi usado: janela ativa ÷ turno líquido.
  DEMANDA        quanto chegou pra fazer: toneladas por pessoa.

Por que não um número só: com o turno inteiro no denominador, a
"produtividade" vira só demanda. No dia 02/09 foram 4 pessoas pra 4,5 t
(1,13 t/pessoa) — o time não foi lento, não tinha o que separar.

Por que t/h E caixas/h: o t/h correlaciona +0,80 com o PESO da carga e só
+0,13 com SKU — mede a carga, não o esforço, e penaliza quem pega carga
leve e picada. Caixas/h cai pra +0,34 com o peso. Os rankings concordam no
topo e divergem no meio, então os dois aparecem lado a lado.

Por que janela com TETO: em 08/09 sete instruções aparecem com 26h de
separação — começaram dom 06 à noite e o fim foi carimbado seg 07, feriado.
Sem teto, uma parada vira "horas trabalhadas".

Pausa térmica (CLT art. 253, câmara fria): 20 min a cada 1h40 de trabalho
contínuo. É tempo PAGO, mas não é tempo produtivo — sai do denominador da
produtividade, não da folha. Tudo parametrizável por CD.

Funções puras sobre listas de dicts (um por instrução, ver
produtividade_parser.py) — sem banco, sem pandas: o volume é de centenas de
linhas por mês e a VM é uma e2-micro.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

PARAMETROS_PADRAO = {
    "noturno_inicio": 18,          # hora em que o turno noturno começa (usuário, 16/09)
    "noturno_fim": 6,              # hora em que termina (exclusive)
    "turno_h": 9.0,                # duração do turno — também é o TETO da janela ativa
    # Separação e embarque têm janelas PRÓPRIAS (usuário, 06/10/2026):
    # "adotaram um modelo onde os separadores entram mais cedo e saem em um
    # determinado horario, enquanto conferentes entram mais tarde e saem
    # depois... precisamos medir separação e embarque com seus dados
    # direcionados". Os padrões reproduzem o comportamento anterior (uma
    # janela só, de 9 h a partir da abertura do turno), então CD que não
    # configurar nada não muda de número.
    "separacao_inicio": 18,        # hora em que a separação começa
    "separacao_fim": 3,            # hora em que a separação termina
    "embarque_inicio": 18,         # hora em que o embarque começa
    "embarque_fim": 3,             # hora em que o embarque termina
    "refeicao_min": 60,
    "trabalho_continuo_min": 100,  # 1h40
    "pausa_termica_min": 20,
    "sku_complexa": 30,            # P80 dos dados reais (usuário, 16/09)
    # liberações até N min da anterior são a MESMA onda (usuário, 16/09:
    # "19:29 e 19:39 são da mesma onda")
    "onda_intervalo_min": 10,
    # ── projeção (usuário, 16/09: "12 docas e 12 conferentes é loucura; 1
    # conferente por doca é muito") ──
    "docas_disponiveis": 0,        # 0 = maior nº de docas usado num dia do período
    "caminhoes_por_doca": 3.0,     # giros por doca no turno; máx. observado 01–15/09 = 3,0
    "caminhoes_por_conferente": 6.0,  # 15/09: 3 conferentes para 18 caminhões
}

MOTIVOS_EXPURGO = {
    "999": "Prioridade 999",
    "FOB": "Frete FOB",
    "SEM_PLACA": "Sem placa",
    "FIC0000": "Placa FIC0000",
    "MANUAL": "Desconsiderada manualmente",
}


def _dt_(s):
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _num(v) -> float:
    try:
        f = float(v)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def _p(p: dict, k: str):
    return p.get(k, PARAMETROS_PADRAO[k]) if p else PARAMETROS_PADRAO[k]


def fracao_produtiva(p: dict) -> float:
    t = float(_p(p, "trabalho_continuo_min"))
    pausa = float(_p(p, "pausa_termica_min"))
    return t / (t + pausa) if t + pausa > 0 else 1.0


def horas_liquidas(bruto_h: float, p: dict) -> float:
    """Horas trabalhadas descontando refeição e pausa térmica.

    A refeição é descontada de forma contínua a partir de 4h de janela (quem
    trabalhou 2h não almoçou no meio): de 4h a 4h+refeição a janela líquida
    fica parada em 4h, e acima disso é bruto − refeição. O desconto em degrau
    fazia uma janela de 4h01 render MENOS horas que uma de 4h00 e inflava o
    ritmo de quem trabalhou mais (revisão de 16/09/2026)."""
    if bruto_h <= 0:
        return 0.0
    ref = float(_p(p, "refeicao_min")) / 60.0
    b = bruto_h - min(ref, max(bruto_h - 4.0, 0.0))
    return b * fracao_produtiva(p)


# Funções apontadas à mão. O WMS registra separador e conferente por
# instrução, mas não diz quem estava na casa — e o auxiliar não aparece em
# relatório nenhum. Como não há lista de presença (usuário, 23/09/2026), o
# efetivo vem de um apontamento curto digitado no dia.
FUNCOES = ("separador", "conferente", "auxiliar")
FUNCAO_LABEL = {"separador": "Separação", "conferente": "Conferência", "auxiliar": "Apoio"}


def _relogio_min(txt) -> int | None:
    """"20:30" -> 1230 minutos. Aceita "2030" e "20h30"; devolve None no que
    não for hora."""
    t = re.sub(r"[^0-9]", "", str(txt or ""))
    if len(t) == 3:
        t = "0" + t
    if len(t) != 4:
        return None
    h, m = int(t[:2]), int(t[2:])
    return h * 60 + m if h < 24 and m < 60 else None


# Cada função é medida na janela da SUA atividade: separador e apoio na
# janela de separação, conferente na de embarque (usuário, 06/10/2026).
ATIVIDADE_DA_FUNCAO = {"separador": "separacao", "auxiliar": "separacao",
                       "conferente": "embarque"}


def janela(p: dict, atividade: str = "separacao") -> tuple[int, float]:
    """(hora de abertura, duração em horas) da janela daquela atividade.

    A duração sai da diferença entre início e fim dando a volta na meia-noite
    (18h -> 3h = 9 h). Janela de duração zero (início igual ao fim) cai no
    `turno_h`, que é o parâmetro antigo — assim um CD que ainda não separou as
    duas janelas continua com o número de sempre.
    """
    ini = int(_p(p, f"{atividade}_inicio"))
    fim = int(_p(p, f"{atividade}_fim"))
    dur = (fim - ini) % 24
    return ini, float(dur or _p(p, "turno_h"))


def jornada_h(entrada, turno: str, p: dict, atividade: str = "separacao") -> float:
    """Horas de casa de quem entrou em `entrada` (HH:MM), limitadas à janela
    da atividade daquela função.

    O conferente entra mais tarde que o separador (usuário, 23/09/2026) e é
    isso que o apontamento registra; desde 06/10/2026 ele também é MEDIDO na
    janela de embarque, que termina depois da de separação. Entrada vazia =
    janela cheia. Entrada ANTES da abertura também conta janela cheia:
    ninguém ganha hora por chegar cedo, e sem essa regra um "17:50" numa
    janela que abre às 18h viraria quase 24 h de atraso.
    """
    ini, teto = janela(p, atividade)
    if turno == "diurno":
        ini = int(_p(p, "noturno_fim"))
    minutos = _relogio_min(entrada)
    if minutos is None:
        return teto
    atraso = (minutos - ini * 60) % (24 * 60)
    if atraso > teto * 60:
        return teto
    return max(teto - atraso / 60, 0.0)


def horas_homem(apontamentos, p: dict, turno: str = "noturno") -> dict:
    """H-H por dia a partir do apontamento do efetivo.

    Devolve as TOTAIS (jornada cheia de quem esteve presente) e, ao lado, as
    LÍQUIDAS (descontando refeição e pausa térmica) — o usuário quer as duas
    à vista pra comparar (23/09/2026). Dia sem apontamento fica de fora em
    vez de entrar zerado: zero diria "ninguém trabalhou", que é diferente de
    "ninguém apontou".
    """
    por_dia = {}
    for a in apontamentos or []:
        dia = (a.get("data") or "").strip()
        if not dia:
            continue
        t = (a.get("turno") or turno or "noturno").strip()
        if turno and t != turno:
            continue
        funcao = (a.get("funcao") or "").strip().lower()
        if funcao not in FUNCOES:
            continue
        presente = _num(a.get("presente"))
        previsto = _num(a.get("previsto"))
        jor = jornada_h(a.get("entrada"), t, p, ATIVIDADE_DA_FUNCAO.get(funcao, "separacao"))
        liq = horas_liquidas(jor, p)
        d = por_dia.setdefault(dia, {"dia": dia, "turno": t, "funcoes": {},
                                     "previsto": 0.0, "presente": 0.0,
                                     "hh_total": 0.0, "hh_liquida": 0.0})
        # soma em vez de substituir: vendo "todos os turnos", a mesma função
        # aparece duas vezes no dia (noturno e diurno)
        f = d["funcoes"].setdefault(funcao, {"previsto": 0.0, "presente": 0.0,
                                             "entradas": [], "hh_total": 0.0, "hh_liquida": 0.0})
        f["previsto"] += previsto
        f["presente"] += presente
        f["hh_total"] += presente * jor
        f["hh_liquida"] += presente * liq
        if (a.get("entrada") or "").strip():
            f["entradas"].append(a["entrada"].strip())
        d["previsto"] += previsto
        d["presente"] += presente
        d["hh_total"] += presente * jor
        d["hh_liquida"] += presente * liq
    saida = []
    for dia in sorted(por_dia):
        d = por_dia[dia]
        for f in d["funcoes"].values():
            f["entrada"] = " · ".join(dict.fromkeys(f.pop("entradas")))
            # jornada média de quem estava presente (com entradas diferentes
            # no mesmo dia, é a média ponderada pelo efetivo)
            f["jornada_h"] = round(f["hh_total"] / f["presente"], 2) if f["presente"] else None
            f["jornada_liquida_h"] = round(f["hh_liquida"] / f["presente"], 2) if f["presente"] else None
            f["hh_total"] = round(f["hh_total"], 2)
            f["hh_liquida"] = round(f["hh_liquida"], 2)
        d["hh_total"] = round(d["hh_total"], 2)
        d["hh_liquida"] = round(d["hh_liquida"], 2)
        d["absenteismo_pct"] = (round((d["previsto"] - d["presente"]) / d["previsto"] * 100, 1)
                                if d["previsto"] else None)
        saida.append(d)
    return {"dias": saida,
            "hh_total": round(sum(x["hh_total"] for x in saida), 2),
            "hh_liquida": round(sum(x["hh_liquida"] for x in saida), 2),
            "dias_apontados": len(saida)}


def motivo_expurgo(reg: dict, desconsideradas: set) -> str | None:
    """Regra do usuário (16/09/2026, a mesma dada pros SLAs em 01/09):
    frete FOB, sem placa ou placa FIC0000 contam como 999. Além disso, a
    prioridade 999 que ele marca à mão e a marcação manual pela tela."""
    # chave "EMPRESA|INSTRUCAO". CDA e CDB numeram na MESMA sequência (painel
    # de 09/10: CDB 26822–26847, CDA 26848–26854); a empresa entra na chave
    # porque Locais diferentes podem repetir o número
    if f'{reg.get("empresa", "")}|{reg.get("instrucao")}' in desconsideradas:
        return "MANUAL"
    if _num(reg.get("prioridade")) == 999:
        return "999"
    if "FOB" in str(reg.get("frete") or "").upper():
        return "FOB"
    placa = str(reg.get("placa") or "").upper()
    if not placa:
        return "SEM_PLACA"
    if placa == "FIC0000":
        return "FIC0000"
    return None


def turno_de(reg: dict, p: dict) -> str:
    """Pelo INÍCIO DA SEPARAÇÃO (usuário, 15/09). Redespacho puro não tem
    separação — cai pro início do embarque."""
    ref = _dt_(reg.get("sep_ini")) or _dt_(reg.get("emb_ini"))
    if ref is None:
        return "noturno"
    h = ref.hour
    ini, fim = int(_p(p, "noturno_inicio")), int(_p(p, "noturno_fim"))
    noturno = (h >= ini or h < fim) if ini > fim else (ini <= h < fim)
    return "noturno" if noturno else "diurno"


def _hm(d) -> str:
    return d.strftime("%Y-%m-%d %H:%M")


def _pct(parte: float, todo: float) -> float | None:
    return round(parte / todo * 100, 1) if todo else None


def _mediana(v):
    v = [x for x in v if x is not None]
    return round(median(v), 3) if v else None


def _quantil(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    i = (len(v) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return round(v[lo] + (v[hi] - v[lo]) * (i - lo), 3)


def calcular(registros: list[dict], p: dict | None = None, turno: str = "noturno",
             desconsideradas: set | None = None, apontamentos: list[dict] | None = None) -> dict:
    """Monta o painel inteiro a partir das instruções do período.

    `turno`: "noturno", "diurno" ou "" (todos).
    `apontamentos`: efetivo digitado no dia, que fecha o H-H (30/09/2026)."""
    p = p or {}
    descons = desconsideradas or set()
    teto = float(_p(p, "turno_h"))
    turno_liq = horas_liquidas(teto, p)
    limite_sku = float(_p(p, "sku_complexa"))

    instrucoes, validas, expurgadas = [], [], []
    for r in registros:
        mot = motivo_expurgo(r, descons)
        t = turno_de(r, p)
        sep_kg, emb_kg = _num(r.get("sep_kg")), _num(r.get("emb_kg"))
        # peso separado basta: 25056 e 25104 têm separação sem horário de
        # início e apareciam como "redespacho integral"
        tem_sep = sep_kg > 0
        linha = {
            "instrucao": r.get("instrucao"), "dia": r.get("data_op"),
            "empresa": r.get("empresa", ""), "turno": t,
            "separador": r.get("separador") or "", "conferente": r.get("conferente") or "",
            "sku": _num(r.get("sku")), "escalas": _num(r.get("escalas")),
            "entregas": _num(r.get("entregas")), "cliente": r.get("cliente") or "",
            "placa": r.get("placa") or "", "frete": r.get("frete") or "",
            "doca": r.get("doca") or "", "cidade": r.get("cidade") or "",
            "sep_t": round(sep_kg / 1000, 3), "emb_t": round(emb_kg / 1000, 3),
            # redespacho = embarcado − separado (usuário, 15/09); nunca negativo
            "red_t": round(max(emb_kg - sep_kg, 0.0) / 1000, 3),
            "sep_cx": _num(r.get("sep_vol")),
            "complexa": _num(r.get("sku")) >= limite_sku,
            "so_redespacho": not tem_sep and emb_kg > 0,
            "liberacao": r.get("liberacao"), "sep_ini": r.get("sep_ini"),
            "sep_fim": r.get("sep_fim"), "emb_ini": r.get("emb_ini"), "emb_fim": r.get("emb_fim"),
            # o embarque acaba duas vezes: última caixa bipada e fechamento
            # de status (usuário, 23/09) — a conta do dia mostra os dois
            "ult_caixa": r.get("ult_caixa"), "fech_status": r.get("fech_status"),
            "expurgo": mot, "expurgo_txt": MOTIVOS_EXPURGO.get(mot, ""),
        }
        instrucoes.append(linha)
        if turno and t != turno:
            continue
        (expurgadas if mot else validas).append((linha, r))

    # ── ritmo por pessoa-dia ───────────────────────────────────────────
    pd_ = defaultdict(lambda: {"t": 0.0, "cx": 0.0, "inst": 0, "ini": None, "fim": None,
                               "sku": [], "complexas": 0})
    for linha, r in validas:
        if linha["so_redespacho"] or not linha["separador"]:
            continue          # redespacho NUNCA é creditado a separador
        k = (linha["dia"], linha["separador"])
        a = pd_[k]
        a["t"] += linha["sep_t"]
        a["cx"] += linha["sep_cx"]
        a["inst"] += 1
        a["sku"].append(linha["sku"])
        a["complexas"] += int(linha["complexa"])
        ini, fim = _dt_(r.get("sep_ini")), _dt_(r.get("sep_fim"))
        if ini and (a["ini"] is None or ini < a["ini"]):
            a["ini"] = ini
        if fim and (a["fim"] is None or fim > a["fim"]):
            a["fim"] = fim

    pessoa_dia = []
    for (dia, nome), a in pd_.items():
        bruto = ((a["fim"] - a["ini"]).total_seconds() / 3600) if a["ini"] and a["fim"] else 0.0
        janela = min(max(bruto, 0.0), teto)
        liq = horas_liquidas(janela, p)
        pessoa_dia.append({
            "dia": dia, "separador": nome, "t": round(a["t"], 3), "cx": a["cx"],
            "inst": a["inst"], "complexas": a["complexas"],
            "sku_medio": round(sum(a["sku"]) / len(a["sku"]), 1) if a["sku"] else None,
            "janela_h": round(janela, 2), "janela_bruta_h": round(bruto, 2),
            "liq_h": round(liq, 2),
            "t_h": round(a["t"] / liq, 3) if liq > 0 else None,
            "cx_h": round(a["cx"] / liq, 1) if liq > 0 else None,
            "aproveitamento": round(liq / turno_liq * 100, 1) if turno_liq and liq > 0 else None,
            "sem_janela": liq <= 0,
            "teto_aplicado": bruto > teto,
        })

    # ── por dia (CD) ───────────────────────────────────────────────────
    dias = defaultdict(lambda: {"sep_t": 0.0, "emb_t": 0.0, "red_t": 0.0, "inst": 0,
                                "complexas": 0, "sku": [], "conf_ok": 0,
                                "placas": set(), "docas": set(), "confs": set(),
                                "emb_fim": [], "ult_caixa": [], "fech_status": []})
    for linha, _ in validas:
        d = dias[linha["dia"]]
        d["sep_t"] += linha["sep_t"]
        d["emb_t"] += linha["emb_t"]
        d["red_t"] += linha["red_t"]
        d["inst"] += 1
        d["complexas"] += int(linha["complexa"])
        if linha["sku"]:
            d["sku"].append(linha["sku"])
        d["conf_ok"] += int(bool(linha["conferente"]))
        if linha["conferente"]:
            d["confs"].add(linha["conferente"])
        if linha["placa"]:
            d["placas"].add(linha["placa"])
        if linha["doca"]:
            d["docas"].add(linha["doca"])
        for campo in ("emb_fim", "ult_caixa", "fech_status"):
            v = _dt_(linha[campo])
            if v:
                d[campo].append(v)

    por_dia = []
    for dia in sorted(dias):
        d = dias[dia]
        pp = [x for x in pessoa_dia if x["dia"] == dia]
        t_pessoas = sum(x["t"] for x in pp)
        n = len(pp)
        # ritmo e aproveitamento: pessoas COM janela (quem não tem horário
        # somaria toneladas sem somar horas)
        pj = [x for x in pp if x["liq_h"] > 0]
        t_rit = sum(x["t"] for x in pj)
        cx_rit = sum(x["cx"] for x in pj)
        liq_tot = sum(x["liq_h"] for x in pj)
        por_dia.append({
            "dia": dia, "sep_t": round(d["sep_t"], 3), "emb_t": round(d["emb_t"], 3),
            "red_t": round(d["red_t"], 3), "red_pct": _pct(d["red_t"], d["emb_t"]),
            "inst": d["inst"], "complexas": d["complexas"],
            "sku_medio": round(sum(d["sku"]) / len(d["sku"]), 1) if d["sku"] else None,
            "separadores": n,
            # toneladas creditadas a um separador x sem separador informado
            "sep_t_creditado": round(t_pessoas, 3),
            "sep_t_sem_separador": round(max(d["sep_t"] - t_pessoas, 0.0), 3),
            # ritmo do dia é PONDERADO (soma ÷ soma), não média de razões
            "t_h": round(t_rit / liq_tot, 3) if liq_tot else None,
            "cx_h": round(cx_rit / liq_tot, 1) if liq_tot else None,
            "aproveitamento": round(liq_tot / (len(pj) * turno_liq) * 100, 1) if pj and turno_liq else None,
            "demanda_t_pessoa": round(t_pessoas / n, 2) if n else None,
            "conferente_pct": _pct(d["conf_ok"], d["inst"]),
            "caminhoes": len(d["placas"]), "docas": len(d["docas"]),
            "conferentes": len(d["confs"]),
            # fim do dia: a última caixa bipada, o fechamento de status e a
            # distância entre os dois (é onde mora o tempo que o painel não
            # enxergava — 118 min em 29→30/09)
            "fim_embarque": _hm(max(d["emb_fim"])) if d["emb_fim"] else None,
            "ultima_caixa": _hm(max(d["ult_caixa"])) if d["ult_caixa"] else None,
            "fechamento_status": _hm(max(d["fech_status"])) if d["fech_status"] else None,
            "fechamento_min": (round((max(d["fech_status"]) - max(d["ult_caixa"])).total_seconds() / 60)
                               if d["fech_status"] and d["ult_caixa"] else None),
        })

    # ── por separador ──────────────────────────────────────────────────
    ps = defaultdict(list)
    for x in pessoa_dia:
        ps[x["separador"]].append(x)
    por_separador = []
    for nome, lst in ps.items():
        por_separador.append({
            "separador": nome, "dias": len(lst),
            "t": round(sum(x["t"] for x in lst), 3),
            "cx": sum(x["cx"] for x in lst),
            "inst": sum(x["inst"] for x in lst),
            "complexas": sum(x["complexas"] for x in lst),
            "t_h": _mediana([x["t_h"] for x in lst]),
            "cx_h": _mediana([x["cx_h"] for x in lst]),
            "aproveitamento": _mediana([x["aproveitamento"] for x in lst]),
            "sku_medio": _mediana([x["sku_medio"] for x in lst]),
        })
    for chave, pos in (("t_h", "pos_t"), ("cx_h", "pos_cx")):
        ordem = sorted((x for x in por_separador if x[chave] is not None),
                       key=lambda x: -x[chave])
        for i, x in enumerate(ordem, 1):
            x[pos] = i
    por_separador.sort(key=lambda x: -(x["t_h"] or 0))

    # ── por conferente ─────────────────────────────────────────────────
    pc = defaultdict(lambda: {"dias": set(), "emb_t": 0.0, "sem_red_t": 0.0, "inst": 0})
    for linha, _ in validas:
        nome = linha["conferente"] or "(não informado)"
        c = pc[nome]
        c["dias"].add(linha["dia"])
        c["emb_t"] += linha["emb_t"]
        c["sem_red_t"] += linha["emb_t"] - linha["red_t"]
        c["inst"] += 1
    por_conferente = sorted(
        ({"conferente": k, "dias": len(v["dias"]), "emb_t": round(v["emb_t"], 3),
          "sem_red_t": round(v["sem_red_t"], 3), "inst": v["inst"]} for k, v in pc.items()),
        key=lambda x: (x["conferente"] == "(não informado)", -x["emb_t"]))

    # ── ondas de liberação ─────────────────────────────────────────────
    # A liberação chega em rajadas: uma onda são as instruções liberadas com
    # até `onda_intervalo_min` de distância da ANTERIOR (encadeado). Agrupar
    # pelo minuto exato quebrava 20:32 e 20:41 em duas ondas.
    intervalo_s = float(_p(p, "onda_intervalo_min")) * 60
    lib_dia = defaultdict(list)
    sem_lib = {"inst": 0, "sep_t": 0.0, "emb_t": 0.0}
    for linha, r in validas:
        lib = _dt_(linha["liberacao"])
        if lib:
            # compara em minutos cheios, como a tela mostra (19:29:31 e
            # 19:39:55 são "19:29 e 19:39", a 10 min)
            lib_dia[linha["dia"]].append((lib.replace(second=0, microsecond=0), linha))
        else:
            sem_lib["inst"] += 1
            sem_lib["sep_t"] += linha["sep_t"]
            sem_lib["emb_t"] += linha["emb_t"]
    ondas = []
    for dia in sorted(lib_dia):
        itens = sorted(lib_dia[dia], key=lambda x: x[0])
        grupos, atual = [], []
        for it in itens:
            if atual and (it[0] - atual[-1][0]).total_seconds() > intervalo_s:
                grupos.append(atual)
                atual = []
            atual.append(it)
        if atual:
            grupos.append(atual)
        for n, g in enumerate(grupos, 1):
            ls = [x[1] for x in g]
            ini, fim = g[0][0], g[-1][0]
            inicios_sep = [d for d in (_dt_(x["sep_ini"]) for x in ls) if d and d >= ini]
            fins_emb = [d for d in (_dt_(x["emb_fim"]) for x in ls) if d]
            caixas = [d for d in (_dt_(x["ult_caixa"]) for x in ls) if d]
            fechs = [d for d in (_dt_(x["fech_status"]) for x in ls) if d]
            # Espera = do início do turno (ou da liberação, se veio depois) até
            # a 1ª separação. Liberação feita dias antes (08/09 foi liberada
            # na sexta 05/09) não é espera operacional.
            espera = espera_lib = None
            antecipada = False
            if inicios_sep:
                s0 = min(inicios_sep)
                h_ini = int(_p(p, "noturno_inicio")) if ls[0]["turno"] == "noturno" \
                    else int(_p(p, "noturno_fim"))
                t_ini = s0.replace(hour=h_ini, minute=0, second=0, microsecond=0)
                if t_ini > s0:
                    t_ini -= _dt.timedelta(days=1)
                antecipada = ini < t_ini
                espera = round((s0 - max(ini, t_ini)).total_seconds() / 60)
                espera_lib = round((s0 - ini).total_seconds() / 60)
            ondas.append({
                "dia": dia, "numero": n,
                "inicio": ini.strftime("%Y-%m-%d %H:%M"), "fim": fim.strftime("%Y-%m-%d %H:%M"),
                "inst": len(ls),
                "veiculos": len({x["placa"] for x in ls if x["placa"]}),
                "sep_t": round(sum(x["sep_t"] for x in ls), 3),
                "emb_t": round(sum(x["emb_t"] for x in ls), 3),
                "red_t": round(sum(x["red_t"] for x in ls), 3),
                "complexas": sum(1 for x in ls if x["complexa"]),
                "espera_min": espera,
                "espera_liberacao_min": espera_lib,
                "liberacao_antecipada": antecipada,
                "fim_embarque": max(fins_emb).strftime("%Y-%m-%d %H:%M") if fins_emb else None,
                "ultima_caixa": _hm(max(caixas)) if caixas else None,
                "fechamento_status": _hm(max(fechs)) if fechs else None,
            })

    # ── docas e embarque ───────────────────────────────────────────────
    emb_h = []
    docas_dia = defaultdict(set)
    carga = []
    for linha, _ in validas:
        a, b = _dt_(linha["emb_ini"]), _dt_(linha["emb_fim"])
        if a and b:
            h = (b - a).total_seconds() / 3600
            if 0 < h <= teto:            # mesmo teto: o feriado de 07/09 inflava pra 26h
                emb_h.append(h)
        if linha["doca"]:
            docas_dia[linha["dia"]].add(linha["doca"])
        if linha["emb_t"] > 0:
            carga.append(linha["emb_t"])

    ritmos = [x["t_h"] for x in pessoa_dia]
    # Ritmo de EQUIPE (soma ÷ soma de cada dia), não de pessoa: a projeção
    # dimensiona a equipe inteira, e a variação individual se compensa no
    # dia. Com o ritmo por pessoa o cenário pesado dava 21 separadores para
    # 60 t — nunca houve mais de 14 num dia.
    ritmos_dia = [x["t_h"] for x in por_dia if x["t_h"]]
    ritmo_base = _mediana(ritmos_dia)
    ritmo_cons = _quantil(ritmos_dia, 0.25)
    cam_doca = [x["caminhoes"] / x["docas"] for x in por_dia if x["docas"] and x["caminhoes"]]
    cam_conf = [x["caminhoes"] / x["conferentes"] for x in por_dia
                if x["conferentes"] and (x["conferente_pct"] or 0) >= 80]
    emb_dias = sum(x["emb_t"] for x in por_dia)
    base_projecao = {
        "ritmo_base": ritmo_base,
        "ritmo_conservador": ritmo_cons,
        "horas_liquidas_turno": round(turno_liq, 2),
        "turno_h": teto,
        "red_pct": _pct(sum(x["red_t"] for x in por_dia), emb_dias),
        "carga_caminhao_t": _mediana(carga),
        "docas_max_obs": max((x["docas"] for x in por_dia), default=0),
        "docas_mediana_obs": _mediana([x["docas"] for x in por_dia if x["docas"]]),
        "maior_equipe_obs": max((x["separadores"] for x in por_dia), default=0),
        "giros_obs": _mediana(cam_doca),
        "giros_max_obs": round(max(cam_doca), 2) if cam_doca else None,
        "cam_por_conf_obs": _mediana(cam_conf),
        "embarque_mediana_h": _mediana(emb_h),
        "dias": len(ritmos_dia),
        "amostra_pessoas_dia": len(pessoa_dia),
    }
    for x in por_dia:
        x["separadores_necessarios"] = _separadores(x["sep_t_creditado"], ritmo_base, turno_liq)

    # ── H-H do apontamento, casado com as toneladas do dia ─────────────
    hh = horas_homem(apontamentos, p, turno)
    hh_por_dia = {x["dia"]: x for x in hh["dias"]}
    t_apontado = 0.0
    for x in por_dia:
        a = hh_por_dia.get(x["dia"])
        x["apontamento"] = a
        if a:
            t_apontado += x["sep_t"]
            x["t_por_hh"] = round(x["sep_t"] / a["hh_total"], 3) if a["hh_total"] else None
            x["t_por_hh_liquida"] = round(x["sep_t"] / a["hh_liquida"], 3) if a["hh_liquida"] else None
        else:
            x["t_por_hh"] = x["t_por_hh_liquida"] = None
    # o t/H-H do período é soma ÷ soma (como o ritmo do dia), e só conta dia
    # que tem apontamento — senão a tonelada de um dia sem efetivo digitado
    # baixaria o indicador de graça
    hh["sep_t"] = round(t_apontado, 3)
    hh["t_por_hh"] = round(t_apontado / hh["hh_total"], 3) if hh["hh_total"] else None
    hh["t_por_hh_liquida"] = round(t_apontado / hh["hh_liquida"], 3) if hh["hh_liquida"] else None
    hh["dias_sem_apontamento"] = sum(1 for x in por_dia if not x["apontamento"])

    sep_tot = sum(x["sep_t"] for x, _ in validas)
    emb_tot = sum(x["emb_t"] for x, _ in validas)
    red_tot = sum(x["red_t"] for x, _ in validas)
    conf_ok = sum(1 for x, _ in validas if x["conferente"])

    return {
        "parametros": {k: _p(p, k) for k in PARAMETROS_PADRAO},
        "turno": turno,
        "horas_homem": hh,
        "resumo": {
            "instrucoes": len(validas), "expurgadas": len(expurgadas),
            "sep_t": round(sep_tot, 3), "emb_t": round(emb_tot, 3),
            "red_t": round(red_tot, 3), "red_pct": _pct(red_tot, emb_tot),
            "separadores": len(ps), "dias": len(dias),
            "t_h": _mediana(ritmos),
            "cx_h": _mediana([x["cx_h"] for x in pessoa_dia]),
            "aproveitamento": _mediana([x["aproveitamento"] for x in pessoa_dia]),
            "demanda_t_pessoa": _mediana([x["demanda_t_pessoa"] for x in por_dia]),
            "conferente_pct": _pct(conf_ok, len(validas)),
            "complexas": sum(1 for x, _ in validas if x["complexa"]),
            "teto_aplicado": sum(1 for x in pessoa_dia if x["teto_aplicado"]),
            "sem_janela": sum(1 for x in pessoa_dia if x["sem_janela"]),
            "sem_liberacao": {"inst": sem_lib["inst"], "sep_t": round(sem_lib["sep_t"], 3),
                              "emb_t": round(sem_lib["emb_t"], 3)},
            "sep_t_sem_separador": round(sum(x["sep_t_sem_separador"] for x in por_dia), 3),
        },
        "por_dia": por_dia,
        "por_separador": por_separador,
        "por_conferente": por_conferente,
        "pessoa_dia": sorted(pessoa_dia, key=lambda x: (x["dia"], x["separador"])),
        "ondas": ondas,
        "base_projecao": base_projecao,
        "resumo_equipe": {"t_h_mediana": ritmo_base, "horas_liquidas_turno": round(turno_liq, 2)},
        "instrucoes": sorted(instrucoes, key=lambda x: (x["dia"] or "", x["instrucao"] or "")),
    }


def _separadores(t: float, ritmo, liq) -> int | None:
    if not t or t <= 0:
        return 0 if t == 0 else None
    return max(1, math.ceil(t / (ritmo * liq))) if ritmo and liq else None


def _docas_disponiveis(base: dict, p: dict) -> tuple[int | None, bool]:
    """(docas, cadastradas). 0 no parâmetro = maior uso observado num dia —
    serve de ponto de partida, mas o CD deve cadastrar o número físico.
    None = nem cadastradas nem registradas (coluna doca vazia)."""
    d = int(_num(_p(p, "docas_disponiveis")))
    if d > 0:
        return d, True
    return (int(base.get("docas_max_obs") or 0) or None), False


def _pl(n, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _dimensionar(sep_t: float, red_t: float, base: dict, p: dict, caminhoes=None) -> dict:
    liq = base.get("horas_liquidas_turno") or 0
    emb_t = sep_t + red_t
    carga = base.get("carga_caminhao_t") or 0
    if caminhoes:
        cam, cam_est = int(caminhoes), False
    else:
        cam, cam_est = (max(1, math.ceil(emb_t / carga)) if carga and emb_t > 0 else None), True
    giros = _num(_p(p, "caminhoes_por_doca")) or None
    disp, disp_inf = _docas_disponiveis(base, p)
    cap = int(math.floor(disp * giros + 1e-9)) if disp and giros else None
    nec = max(1, math.ceil(cam / giros)) if cam and giros else None
    excede = bool(cam) and cap is not None and cam > cap
    # o que efetivamente cabe no turno — base de docas e conferentes
    cam_ok = min(cam, cap) if excede else cam
    t_ok = round(sep_t * cap / cam, 3) if excede else sep_t
    cpc = _num(_p(p, "caminhoes_por_conferente")) or None
    aloc = min(nec, disp) if nec and disp else nec
    conf_bruto = max(1, math.ceil(cam_ok / cpc)) if cam_ok and cpc else None
    # nunca mais conferentes que docas em operação
    conf = min(conf_bruto, aloc) if conf_bruto and aloc else conf_bruto
    conf_lim = bool(conf_bruto and aloc and conf_bruto > aloc)
    return {
        "sep_base": _separadores(sep_t, base.get("ritmo_base"), liq),
        "sep_cons": _separadores(sep_t, base.get("ritmo_conservador"), liq),
        "emb_t": emb_t, "cam": cam, "cam_est": cam_est, "carga": carga,
        "giros": giros, "disp": disp, "disp_inf": disp_inf, "capacidade": cap,
        "docas_nec": nec, "docas_aloc": aloc, "excede": excede,
        "ocupacao": round(cam / cap * 100, 4) if cam and cap else None,
        "cam_ok": cam_ok, "t_ok": t_ok,
        "sep_base_ok": _separadores(t_ok, base.get("ritmo_base"), liq),
        "sep_cons_ok": _separadores(t_ok, base.get("ritmo_conservador"), liq),
        "conf": conf, "conf_lim": conf_lim, "cpc": cpc,
    }


def _f(v, casas=1) -> str:
    """Número no formato brasileiro, arredondando meio para cima — o mesmo
    critério do toLocaleString da tela (o round do Python arredonda para o
    par e fazia o alerta mostrar 82% com o cartão em 83%)."""
    if v is None:
        return "—"
    q = Decimal(str(v)).quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)
    return f"{q:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _por_doca(g) -> str:
    return f"{_fn(g)} {'caminhão' if g == 1 else 'caminhões'} por doca"


def _fn(v) -> str:
    """Parâmetro: sem casa decimal quando inteiro."""
    return _f(v, 0 if v is not None and float(v).is_integer() else 1)


def projetar(toneladas: float, base: dict, p: dict | None = None,
             caminhoes: int | None = None, redespacho: float | None = None,
             por_dia: list | None = None) -> dict:
    """Dimensiona a equipe para X toneladas a SEPARAR.

    Separadores  = t ÷ (ritmo da equipe × horas líquidas do turno), com o
                   ritmo mediano dos dias (base) e o 1º quartil (conservador).
    Caminhões    = (t + redespacho) ÷ carga embarcada por caminhão (mediana).
                   Sem redespacho informado, usa a proporção do período — a
                   carga do caminhão inclui redespacho, então dividir apenas a
                   tonelada separada subestimava a frota.
    Docas        = caminhões ÷ giros por doca no turno, limitado às docas
                   disponíveis. Acima da capacidade, devolve o PLANO do que
                   cabe no turno e o excedente, em vez de números impossíveis.
    Conferentes  = caminhões que cabem ÷ caminhões por conferente, limitado
                   às docas em operação.

    Modelo escolhido em 16/09/2026 entre três propostas testadas contra os
    10 dias reais (01–15/09). Nos 5 dias de maior volume: separadores base
    nunca abaixo da equipe efetiva (horas líquidas ÷ turno), docas com erro
    médio de 0,4, sem alerta falso de capacidade.
    """
    p = p or {}
    t = max(float(toneladas or 0), 0.0)
    red_pct = base.get("red_pct")
    if redespacho is None:
        red_est = True
        red = t * red_pct / (100 - red_pct) if red_pct is not None and red_pct < 100 else 0.0
    else:
        red_est, red = False, max(float(redespacho), 0.0)
    # arredonda UMA vez: memória, alerta e cartão exibem o mesmo número
    red = round(red, 3)
    d = _dimensionar(t, red, base, p, caminhoes)
    liq = base.get("horas_liquidas_turno")

    memoria, alertas = [], []
    if t > 0 and d["sep_base"] is not None:
        memoria.append(
            f"<b>Separadores</b> = {_f(t)} t ÷ (ritmo da equipe × {_f(liq, 2)} h líquidas) → base: "
            f"{_f(t)} ÷ ({_f(base.get('ritmo_base'), 2)} × {_f(liq, 2)}) = <b>{d['sep_base']}</b> · "
            f"conservador: {_f(t)} ÷ ({_f(base.get('ritmo_conservador'), 2)} × {_f(liq, 2)}) "
            f"= <b>{d['sep_cons']}</b>")
        if red_est and red:
            memoria.append(
                f"<b>Redespacho</b> = {_f(t)} t × {_f(red_pct)}% ÷ {_f(100 - (red_pct or 0))}% "
                f"= <b>{_f(red)} t</b> (proporção do período)")
        if d["cam_est"] and d["cam"]:
            memoria.append(
                f"<b>Caminhões</b> = ({_f(t)} + {_f(red)}) t ÷ {_f(d['carga'])} t por caminhão "
                f"= <b>{d['cam']}</b>")
        if d["docas_nec"]:
            if d["disp"]:
                origem = ("cadastrada" if d["disp"] == 1 else "cadastradas") if d["disp_inf"] \
                    else "maior uso observado no período"
                disp_txt = (f"disponíveis: {d['disp']} ({origem}) · capacidade do turno: "
                            f"{d['disp']} × {_fn(d['giros'])} = {_pl(d['capacidade'], 'caminhão', 'caminhões')}")
            else:
                disp_txt = "docas disponíveis não informadas"
            memoria.append(
                f"<b>Docas</b> = {_pl(d['cam'], 'caminhão', 'caminhões')} ÷ {_por_doca(d['giros'])} "
                f"= <b>{d['docas_nec']}</b> · {disp_txt}")
        if d["conf"]:
            base_conf = (f"{_pl(d['cam_ok'], 'caminhão', 'caminhões')} dentro da capacidade"
                         if d["excede"] else _pl(d["cam_ok"], "caminhão", "caminhões"))
            memoria.append(
                f"<b>Conferentes</b> = {base_conf} ÷ {_fn(d['cpc'])} por conferente "
                f"= <b>{d['conf']}</b>"
                + (f" (limitado às {d['docas_aloc']} docas em operação)" if d["conf_lim"] else ""))

        if d["excede"]:
            alertas.append({"nivel": "erro", "texto":
                f"<b>Capacidade de docas excedida.</b> {_pl(d['disp'], 'doca', 'docas')} × "
                f"{_por_doca(d['giros'])} {'comporta' if d['disp'] == 1 else 'comportam'} "
                f"{_pl(d['capacidade'], 'caminhão', 'caminhões')} no turno; a projeção requer {d['cam']}. "
                f"Plano dentro da capacidade: <b>{_f(d['t_ok'])} t</b> a separar, "
                f"{_pl(d['cam_ok'], 'caminhão', 'caminhões')} e "
                f"{_pl(d['sep_base_ok'], 'separador', 'separadores')} (cenário base). "
                f"Excedente: {_f(t - d['t_ok'])} t em "
                f"{_pl(d['cam'] - d['cam_ok'], 'caminhão', 'caminhões')} — avaliar antecipação "
                f"para o turno anterior ou ampliação da janela de embarque."})
        elif d["ocupacao"] is not None and d["ocupacao"] >= 80:
            alertas.append({"nivel": "aviso", "texto":
                f"Ocupação de {_f(d['ocupacao'], 0)}% da capacidade de docas "
                f"({d['cam']} de {_pl(d['capacidade'], 'caminhão', 'caminhões')}). "
                f"Margem reduzida para atrasos de embarque."})
        med_docas = base.get("docas_mediana_obs")
        if d["docas_nec"] and med_docas and not d["excede"] and d["docas_nec"] > math.ceil(med_docas):
            alertas.append({"nivel": "aviso", "texto":
                f"A projeção requer {_pl(d['docas_nec'], 'doca', 'docas')}, acima da mediana do "
                f"período ({_fn(med_docas)} {'doca' if med_docas == 1 else 'docas'} em operação por dia)."})
        maior_eq = base.get("maior_equipe_obs")
        if maior_eq and d["sep_cons"] and d["sep_cons"] > maior_eq:
            alertas.append({"nivel": "aviso", "texto":
                f"O cenário conservador requer {d['sep_cons']} separadores, acima da maior "
                f"equipe escalada no período ({maior_eq})."})
        if not d["disp"]:
            alertas.append({"nivel": "aviso", "texto":
                "Docas disponíveis não cadastradas e sem registro de doca no período: "
                "a capacidade de docas não foi verificada."})
        elif not d["disp_inf"]:
            alertas.append({"nivel": "info", "texto":
                "Número de docas disponíveis não cadastrado para este CD. A capacidade considera o "
                "maior número de docas em operação observado no período; o cadastro é feito por "
                "gerentes e administradores em Parâmetros."})
        dias_base = base.get("dias") or 0
        if dias_base < 5:
            alertas.append({"nivel": "aviso", "texto":
                f"Base de cálculo com {_pl(dias_base, 'dia', 'dias')} de operação. Amplie o período "
                f"para obter uma referência mais estável."})
        maior = max((x["sep_t"] for x in (por_dia or [])), default=0)
        if maior and t > maior * 1.5:
            alertas.append({"nivel": "aviso", "texto":
                f"Volume {_f(t / maior * 100 - 100, 0)}% acima do dia de maior volume separado no "
                f"período ({_f(maior)} t). Projeções fora da faixa observada têm precisão menor."})

    # Aderência: aplica o modelo às toneladas separadas E ao redespacho REAIS
    # de cada dia e compara com o que a operação utilizou.
    backtest = []
    for x in por_dia or []:
        if not x.get("sep_t"):
            continue
        b = _dimensionar(x["sep_t"], x["red_t"], base, p)
        # separadores na mesma base dos escalados: toneladas creditadas a alguém
        cred = x.get("sep_t_creditado", x["sep_t"])
        backtest.append({
            "dia": x["dia"], "sep_t": x["sep_t"], "red_t": x["red_t"],
            "sep_t_sem_separador": x.get("sep_t_sem_separador", 0),
            "sep_real": x["separadores"],
            "sep_base": _separadores(cred, base.get("ritmo_base"), base.get("horas_liquidas_turno")),
            "sep_cons": _separadores(cred, base.get("ritmo_conservador"), base.get("horas_liquidas_turno")),
            "cam_real": x["caminhoes"], "cam_proj": b["cam"],
            "docas_real": x["docas"], "docas_proj": b["docas_aloc"],
        })

    return {
        "entrada": {"sep_t": t, "red_t": round(red, 3), "red_estimado": red_est,
                    "emb_t": round(d["emb_t"], 3)},
        "separadores": {"base": d["sep_base"], "conservador": d["sep_cons"]} if t > 0 else None,
        "caminhoes": {"valor": d["cam"], "estimado": d["cam_est"], "carga_ref_t": d["carga"]},
        "docas": {"necessarias": d["docas_nec"], "disponiveis": d["disp"],
                  "disponiveis_cadastradas": d["disp_inf"],
                  "alocadas": d["docas_aloc"], "giros": d["giros"],
                  "capacidade_caminhoes": d["capacidade"], "ocupacao": d["ocupacao"],
                  "excedente": d["excede"]},
        "plano": {"t": round(d["t_ok"], 3), "caminhoes": d["cam_ok"],
                  "separadores_base": d["sep_base_ok"], "separadores_conservador": d["sep_cons_ok"],
                  "t_excedente": round(t - d["t_ok"], 3),
                  "caminhoes_excedentes": (d["cam"] or 0) - (d["cam_ok"] or 0)} if d["excede"] else None,
        "conferentes": {"valor": d["conf"], "caminhoes_por_conferente": d["cpc"],
                        "limitado": d["conf_lim"]},
        "memoria": memoria, "alertas": alertas,
        "base": base, "backtest": backtest,
    }
