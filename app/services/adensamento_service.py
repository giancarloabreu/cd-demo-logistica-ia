"""
Serviço de Adensamento — Monitoramento CD Demo
Lê o relatório 'Produto Paletizado' (TXT, tab-separado, cp1252) e sugere
EMPILHAR PALETES INTEIROS num só (dois ou mais viram um), respeitando:
  - mesmo Local de Estoque + Temperatura + Código
  - quantidade máxima de caixas por código (cadastro)
  - peso máximo por código (cadastro)
  - janela de data de produção por temperatura (FEFO)
Nunca fraciona um palete de origem. Sobras (paletes fracos) podem virar
paletes MISTOS (vários códigos, mesmo local/temperatura/janela).
"""
from __future__ import annotations
import datetime as _dt
import re
from collections import defaultdict

from app.services.paletizado_colunas import achar_cabecalho, resolver_colunas, valor, ler_linhas_txt

# As colunas saem do CABECALHO (paletizado_colunas.py) — o layout do
# relatorio varia entre empresas e os indices fixos de antes so valiam pro
# export do CD Demo (comparacao CDA x outra unidade, 01/09/2026).
# "peso" aqui e o Peso BRUTO (era o indice 24), diferente do Painel do
# Armazem, que usa o liquido nos indicadores gerais. Isso NAO e inconsistencia:
# o usuario confirmou (01/09/2026) que o box tem limite de PESO independente da
# quantidade de caixas, entao o que restringe o adensamento e o peso real que
# vai em cima do rack — com embalagem e palete. Nao troque pro liquido.
_MIN_COLS = 22
_TOL_PESO = 0.1  # kg — ignora ruído de arredondamento


# ── leitura ──────────────────────────────────────────────────────────
def _peso(s):
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _data(s):
    s = (s or "").strip()
    try:
        return _dt.datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def _split_endereco(valor):
    valor = (valor or "").strip()
    if not valor:
        return "", ""
    if "-" in valor:
        cam, _, box = valor.partition("-")
        return cam.strip(), box.strip()
    return valor, ""


def parse_txt(path):
    caixas = []
    todas = ler_linhas_txt(path)        # UTF-8 ou cp1252, detectado

    idx_header = achar_cabecalho(todas)
    if idx_header is None:
        return []
    col = resolver_colunas(todas[idx_header].rstrip("\n").split("\t"))

    for linha in todas[idx_header + 1:]:
        p = linha.rstrip("\n").split("\t")
        if len(p) < _MIN_COLS:
            continue
        cod = valor(p, col, "produto_codigo")
        pal = valor(p, col, "palete")
        cb = valor(p, col, "cb")
        if not cod or not pal or not cb:
            continue
        camara, box = _split_endereco(valor(p, col, "box"))
        altura = valor(p, col, "altura")
        endereco = "-".join(x for x in (camara, box, altura) if x)
        caixas.append({
            "cb": cb, "codigo": cod, "nome": valor(p, col, "produto_nome"),
            "temperatura": valor(p, col, "temperatura"),
            "camara": camara, "box": box, "altura": altura,
            "endereco_origem": endereco,
            "tipo": valor(p, col, "tipo"), "rua": valor(p, col, "rua"),
            "palete_origem": pal, "peso": _peso(valor(p, col, "peso_bruto")),
            "local_cod": valor(p, col, "local_codigo"),
            "local_nome": valor(p, col, "local_nome"),
            "producao": _data(valor(p, col, "producao")),
        })
    return caixas


# ── unidades-palete e empacotamento (paletes inteiros) ───────────────
def _unidades_de(caixas):
    """Cada unidade é um PALETE FÍSICO inteiro (todas as caixas do palete),
    com a classificação de código/local/temperatura (para saber se pode adensar)."""
    u = {}
    for c in caixas:
        d = u.get(c["palete_origem"])
        if d is None:
            d = {"palete": c["palete_origem"], "endereco": c["endereco_origem"],
                 "camara": c["camara"], "rua": c["rua"], "box": c["box"], "altura": c["altura"],
                 "caixas": [], "peso": 0.0, "dmin": None, "dmax": None,
                 "codigos": set(), "locais": set(), "temps": set()}
            u[c["palete_origem"]] = d
        d["caixas"].append(c)
        d["peso"] += c["peso"]
        d["codigos"].add(c["codigo"])
        d["locais"].add(c["local_cod"])
        d["temps"].add(c["temperatura"])
        dd = c["producao"]
        if dd:
            d["dmin"] = dd if d["dmin"] is None else min(d["dmin"], dd)
            d["dmax"] = dd if d["dmax"] is None else max(d["dmax"], dd)
    for d in u.values():
        uniforme = len(d["codigos"]) == 1 and len(d["locais"]) == 1 and len(d["temps"]) == 1
        d["single_cod"] = next(iter(d["codigos"])) if len(d["codigos"]) == 1 else None
        d["uniforme"] = uniforme
        d["local_cod"] = next(iter(d["locais"])) if len(d["locais"]) == 1 else ""
        d["temperatura"] = next(iter(d["temps"])) if len(d["temps"]) == 1 else ""
    return list(u.values())


def _span_ok(dmin, dmax, u, rng):
    if rng is None:
        return True
    lo, hi = dmin, dmax
    if u["dmin"]:
        lo = u["dmin"] if lo is None else min(lo, u["dmin"])
    if u["dmax"]:
        hi = u["dmax"] if hi is None else max(hi, u["dmax"])
    if lo is None or hi is None:
        return True
    return (hi - lo).days <= rng


def _chave_box(camara, box, altura):
    return (str(camara or "").strip().upper(),
            str(box or "").strip().upper(),
            str(altura or "").strip().upper())


def _empacotar_unidades(unidades, qtd_max, peso_max, rng, boxes=None):
    """First-Fit Decreasing sobre paletes inteiros. Nunca divide um palete.

    "boxes" é o cadastro de posições com capacidade reduzida (meia posição):
    {(camara,box,altura) normalizados: quantidade_permitida}. Operacionalmente,
    quando 2+ paletes de origem viram 1 destino, o palete que fica PARADO no
    lugar (recebendo os outros em cima) é sempre o maior do grupo — o próprio
    algoritmo já ordena por tamanho decrescente, então é sempre o primeiro
    palete de cada novo "destino". O limite efetivo daquele destino passa a
    ser o menor entre o limite do produto (qtd_max) e o limite cadastrado pra
    posição física de origem desse palete-âncora (se houver cadastro pra ela)."""
    boxes = boxes or {}
    unidades = sorted(unidades, key=lambda x: -len(x["caixas"]))
    dests = []
    for u in unidades:
        nu = len(u["caixas"])
        # Entre os destinos onde o palete cabe, o da MESMA RUA vem primeiro,
        # depois o da mesma câmara (22/10/2026: juntar palete da rua A com o
        # da rua F fazia o operador ir e voltar na câmara). Continua First-Fit
        # dentro de cada faixa de distância.
        alvo, melhor = None, None
        for i, d in enumerate(dests):
            if (len(d["caixas"]) + nu <= d["qtd_max_efetivo"] and
                    (not peso_max or d["peso"] + u["peso"] <= peso_max + _TOL_PESO) and
                    _span_ok(d["dmin"], d["dmax"], u, rng)):
                chave = (_distancia(d["unidades"][0], u), i)
                if melhor is None or chave < melhor:
                    alvo, melhor = d, chave
        if alvo is None:
            cx0 = u["caixas"][0]
            limite_posicao = boxes.get(_chave_box(cx0["camara"], cx0["box"], cx0["altura"]))
            qtd_max_efetivo = min(qtd_max, limite_posicao) if limite_posicao else qtd_max
            alvo = {"caixas": [], "peso": 0.0, "dmin": None, "dmax": None, "unidades": [],
                    "qtd_max_efetivo": qtd_max_efetivo}
            dests.append(alvo)
        alvo["unidades"].append(u)
        alvo["caixas"].extend(u["caixas"])
        alvo["peso"] += u["peso"]
        if u["dmin"]:
            alvo["dmin"] = u["dmin"] if alvo["dmin"] is None else min(alvo["dmin"], u["dmin"])
        if u["dmax"]:
            alvo["dmax"] = u["dmax"] if alvo["dmax"] is None else max(alvo["dmax"], u["dmax"])
    return dests


def _distancia(a, b) -> int:
    """0 = mesma câmara e rua; 1 = mesma câmara; 2 = outra câmara."""
    mesma_camara = str(a.get("camara") or "").upper() == str(b.get("camara") or "").upper()
    if mesma_camara and str(a.get("rua") or "").upper() == str(b.get("rua") or "").upper():
        return 0
    return 1 if mesma_camara else 2


def _nat(valor):
    """Ordem natural: "B3" antes de "B10"; números e letras comparáveis."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t)
            for t in re.split(r"(\d+)", str(valor or "").strip().upper()) if t]


def _ordem_empilhamento(dest, boxes):
    """Paletes do destino de BAIXO para CIMA e se a ordem ideal foi possível.

    A produção mais velha fica EM CIMA (usuário, 22/10/2026): palete A de
    01/10 e palete B de 02/10 → B é a base, que fica parada no endereço, e
    as caixas de A vão por cima — a separação pega primeiro o mais velho.
    A base é o palete de produção mais nova cuja posição comporta o total
    (meia posição cadastrada); sem nenhuma, fica a âncora do empacotamento
    (o maior, já validado) e a tela avisa que a ordem não pôde ser seguida."""
    us = dest["unidades"]
    if len(us) < 2:
        return list(us), True

    def nova(u):        # mais nova primeiro; sem data conta como a mais nova
        return (u["dmin"] or _dt.date.max, len(u["caixas"]))
    total = len(dest["caixas"])
    candidatas = sorted(us, key=nova, reverse=True)
    base = next((u for u in candidatas
                 if not (lim := (boxes or {}).get(_chave_box(u["camara"], u["box"], u["altura"])))
                 or total <= lim), None)
    ideal = base is candidatas[0]
    if base is None:
        base, ideal = us[0], False
    resto = sorted((u for u in us if u is not base), key=nova, reverse=True)
    if ideal and resto and base["dmin"] and resto[0]["dmin"] and resto[0]["dmin"] > base["dmin"]:
        ideal = False
    return [base] + resto, ideal


def _chave_rota(dest):
    """Endereço da base: câmara → rua → box → altura (a lista segue a rua)."""
    b = dest["ordem"][0]
    c0 = dest["caixas"][0]
    return (_nat(b["camara"]), _nat(b["rua"]), _nat(b["box"]), _nat(b["altura"]),
            c0["local_nome"], c0["temperatura"], c0["codigo"])


def _range_por_temp(config, temp):
    t = (temp or "").upper()
    if "CONG" in t:
        return config.get("range_congelado", 30)
    if "RESF" in t:
        return config.get("range_resfriado", 5)
    return config.get("range_ambiente", 5)


def _resumo_pallet(dest, seq):
    # de baixo para cima: a base (fica no endereço) e os que vão por cima,
    # do mais novo ao mais velho (topo)
    ordem = dest["ordem"]
    pos = {id(u): i for i, u in enumerate(ordem)}
    por_palete = {u["palete"]: pos[id(u)] for u in ordem}
    caixas = sorted(dest["caixas"],
                    key=lambda c: (por_palete.get(c["palete_origem"], 0), c["endereco_origem"]))
    codigos = sorted({c["codigo"] for c in caixas})
    nomes = sorted({c["nome"] for c in caixas})
    camaras = sorted({c["camara"] for c in caixas if c["camara"]})
    tipo = "Único" if len(codigos) == 1 else "Misto"

    n_origens = len(ordem)

    def papel(i):
        if n_origens < 2:
            return ""
        return "base" if i == 0 else ("topo" if i == n_origens - 1 else "meio")
    origens_detalhe = [{"palete": u["palete"], "endereco": u["endereco"],
                        "qtd": len(u["caixas"]), "peso": round(u["peso"], 1),
                        "rua": u["rua"], "papel": papel(i),
                        "producao": u["dmin"].strftime("%d/%m/%Y") if u["dmin"] else ""}
                       for i, u in enumerate(ordem)]
    # texto da CAPA: de baixo para cima, pronto pra executar
    def _item(o):
        prod = f" · prod {o['producao'][:5]}" if o["producao"] else ""
        return f"{o['palete']} @ {o['endereco'] or 's/end'} ({o['qtd']} cx{prod})"
    if n_origens >= 2:
        origem_capa = ("BASE " + _item(origens_detalhe[0]) + "  ↑  "
                       + "  ↑  ".join(_item(o) for o in origens_detalhe[1:]) + " [topo]")
    else:
        origem_capa = _item(origens_detalhe[0])

    dmin = dest["dmin"].strftime("%d/%m/%Y") if dest["dmin"] else ""
    dmax = dest["dmax"].strftime("%d/%m/%Y") if dest["dmax"] else ""
    return {
        "pallet_destino": seq, "tipo": tipo,
        "local_cod": caixas[0]["local_cod"], "local_nome": caixas[0]["local_nome"],
        "temperatura": caixas[0]["temperatura"], "camara": ", ".join(camaras),
        "codigo": ", ".join(codigos), "nome": " | ".join(nomes),
        "qtd_caixas": len(caixas), "peso_total": round(dest["peso"], 3),
        "producao_min": dmin, "producao_max": dmax,
        "n_origens": n_origens, "acao": n_origens >= 2,
        "origens_capa": origem_capa, "origens_detalhe": origens_detalhe,
        # endereço da base (onde o palete final fica) e a rua da rota
        "rua": ordem[0]["rua"], "endereco_destino": ordem[0]["endereco"],
        "ordem_ideal": dest.get("ordem_ideal", True),
        "caixas": [{
            "cb": c["cb"], "codigo": c["codigo"], "nome": c["nome"],
            "camara": c["camara"], "box": c["box"], "altura": c["altura"],
            "endereco_origem": c["endereco_origem"], "rua": c["rua"],
            "palete_origem": c["palete_origem"], "peso": round(c["peso"], 3),
            "producao": c["producao"].strftime("%d/%m/%Y") if c["producao"] else "",
        } for c in caixas],
    }


def montar_adensamento(caixas, cadastro, config, boxes=None):
    """
    cadastro: {codigo: {'qtd_max': int, 'peso_max': float, 'completo': bool}}
    config:   range_* / misto_qtd_max / misto_peso_max / misto_threshold_pct
    boxes:    {(camara,box,altura) normalizados: quantidade_permitida} —
              posições com capacidade reduzida (meia posição); opcional.
    """
    boxes = boxes or {}
    misto_qtd = int(config.get("misto_qtd_max", 25) or 25)
    misto_peso = float(config.get("misto_peso_max", 0) or 0)
    thr = float(config.get("misto_threshold_pct", 60) or 60) / 100.0

    # pendências: códigos no TXT sem cadastro completo
    codigos_txt = {}
    for c in caixas:
        codigos_txt.setdefault(c["codigo"], c)
    pendencias = [{"codigo": k, "nome": v["nome"], "temperatura": v["temperatura"]}
                  for k, v in codigos_txt.items()
                  if not (cadastro.get(k) and cadastro[k].get("completo"))]

    # separa quem tem cadastro completo — trabalhando com PALETES FÍSICOS inteiros
    unidades = _unidades_de(caixas)
    grupos = defaultdict(list)     # (local,temp,cod) -> [unidades de código único]
    multicodigo = []               # paletes já mistos/picking -> NÃO TOCAR
    sem_cadastro_cx = 0
    for u in unidades:
        if u["single_cod"] and u["uniforme"]:
            cod = u["single_cod"]
            reg = cadastro.get(cod)
            if reg and reg.get("completo"):
                grupos[(u["local_cod"], u["temperatura"], cod)].append(u)
            else:
                sem_cadastro_cx += len(u["caixas"])   # pendência de cadastro
        else:
            multicodigo.append(u)   # multi-código/multi-local: fica como está

    unicos, sobra = [], []   # sobra = unidades-palete liberadas p/ misto
    for (loc, temp, cod), unidades_g in grupos.items():
        reg = cadastro[cod]
        qtd_max = int(reg["qtd_max"])
        peso_max = float(reg["peso_max"])
        rng = _range_por_temp(config, temp)
        for d in _empacotar_unidades(unidades_g, qtd_max, peso_max, rng, boxes):
            # destino fraco (abaixo do limiar) libera seus paletes inteiros p/ misto
            if qtd_max and len(d["caixas"]) < thr * qtd_max:
                sobra.extend(d["unidades"])
            else:
                unicos.append(d)

    # mistos: empilha paletes inteiros de códigos diferentes (mesmo local+temp+janela)
    mistos = []
    gmisto = defaultdict(list)
    for u in sobra:
        c0 = u["caixas"][0]
        gmisto[(c0["local_cod"], c0["temperatura"])].append(u)
    for (loc, temp), unidades in gmisto.items():
        rng = _range_por_temp(config, temp)
        mistos.extend(_empacotar_unidades(unidades, misto_qtd, misto_peso, rng, boxes))

    # monta saída numerada: primeiro as ações (2+ paletes), na ROTA —
    # câmara → rua → box da base (22/10/2026: termina a rua A antes de ir
    # para a B, sem ir e voltar). Único e misto entram na mesma rota.
    for d in unicos + mistos:
        d["ordem"], d["ordem_ideal"] = _ordem_empilhamento(d, boxes)
    pallets = []
    seq = 0
    acoes = [d for d in unicos + mistos if len(d["unidades"]) >= 2]
    resto = [d for d in unicos + mistos if len(d["unidades"]) < 2]
    for d in sorted(acoes, key=_chave_rota) + sorted(resto, key=_chave_rota):
        seq += 1
        pallets.append(_resumo_pallet(d, seq))

    # integridade: destino que EMPILHA 2+ paletes não pode exceder o limite
    violacoes, alertas_cadastro = [], []
    for pl in pallets:
        if pl["tipo"] == "Misto":
            lim_q, lim_p = misto_qtd, misto_peso
        else:
            reg = cadastro.get(pl["codigo"], {})
            lim_q, lim_p = int(reg.get("qtd_max", 0)), float(reg.get("peso_max", 0))
        excede_q = lim_q and pl["qtd_caixas"] > lim_q
        excede_p = lim_p and pl["peso_total"] > lim_p + _TOL_PESO
        if excede_q or excede_p:
            if pl["n_origens"] >= 2:
                violacoes.append(pl["pallet_destino"])
            else:
                alertas_cadastro.append(pl["codigo"])

    n_unidades_in = sum(len(v) for v in grupos.values())
    caixas_adensadas = sum(len(u["caixas"]) for v in grupos.values() for u in v)
    consolidacoes = sum(1 for pl in pallets if pl["acao"])
    resumo = {
        "total_caixas": len(caixas),
        "caixas_adensadas": caixas_adensadas,
        "paletes_origem": n_unidades_in,
        "paletes_sugeridos": len(pallets),
        "reducao": n_unidades_in - len(pallets),
        "consolidacoes": consolidacoes,
        "intocados": len(pallets) - consolidacoes,
        "paletes_unicos": len(unicos),
        "paletes_mistos": len(mistos),
        "paletes_multicodigo": len(multicodigo),
        "caixas_sem_cadastro": sem_cadastro_cx,
        "qtd_pendencias": len(pendencias),
        "violacoes": len(violacoes),
        "alertas_cadastro": len(set(alertas_cadastro)),
    }
    return {"pallets": pallets, "pendencias": pendencias, "resumo": resumo,
            "alertas_codigos": sorted(set(alertas_cadastro)),
            "codigos_txt": [{"codigo": k, "nome": v["nome"],
                             "temperatura": v["temperatura"]}
                            for k, v in codigos_txt.items()]}
