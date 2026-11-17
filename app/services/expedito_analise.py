"""
Expedito — análise da operação da noite (14/10/2026).

Pedido do usuário: "o Expedito deve ser capaz de analisar o painel da noite e
dizer o que houve, qual foi o término da operação (horário do fechamento de
status) e analisar os gargalos", olhando separação e embarque cada um na sua
janela — "fim da separação previsto: X / fim do embarque previsto: Y" — e o
horário de corte final (05h30): a separação pode passar do expediente dela e a
operação ainda fechar no corte, porque o que conta no fim é separação +
embarque até o corte.

O cálculo é determinístico e roda em cima do `detalhe` do retrato
(app/services/expedito.py). A IA recebe esta análise já pronta — em codinome
— e raciocina em cima dela; a tela mostra a mesma análise sem depender da IA.

Os textos não carregam nome de pessoa nem número de instrução: quem é quem
fica nos campos `pessoa` e `instrucoes`, que a tela mostra com o nome real e
a máscara da IA troca por codinome e referência.
"""
from __future__ import annotations

import datetime as _dt

from app.services.produtividade_calc import _dt_, _num, _p

# Campos da janela da noite, em HH:MM; hora a partir do meio-dia é da véspera
# do embarque, antes do meio-dia é do dia do embarque. Os HORÁRIOS não moram
# aqui: vêm dos Parâmetros do Expedito, e a noite pode ajustar na lista de
# presença (usuário, 14/10/2026: "não vamos soldar a informação no código").
# Campo não cadastrado fica None e a análise diz que falta o cadastro.
CHAVES_JANELA = ("sep_inicio", "sep_fim", "emb_inicio", "emb_fim", "corte")
ROTULOS_JANELA = {"sep_inicio": "início da separação", "sep_fim": "fim da separação",
                  "emb_inicio": "início do embarque", "emb_fim": "fim do embarque",
                  "corte": "corte final"}

def hora_valida(v) -> str | None:
    t = str(v or "").strip()
    try:
        h, m = t.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return f"{h:02d}:{m:02d}"
    return None


def quando(hhmm: str, dia_embarque: _dt.date) -> _dt.datetime | None:
    """HH:MM da janela -> data e hora. A noite rotulada pelo embarque em D
    começa em D-1: 19:00 é D-1, 03:00 e 05:30 são D."""
    t = hora_valida(hhmm)
    if not t:
        return None
    h, m = (int(x) for x in t.split(":"))
    dia = dia_embarque - _dt.timedelta(days=1) if h >= 12 else dia_embarque
    return _dt.datetime.combine(dia, _dt.time(h, m))


def _hm(d) -> str | None:
    return d.strftime("%H:%M") if isinstance(d, _dt.datetime) else None


def _min(a, b) -> int | None:
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 60)


def _mediana(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _n(v, casas: int = 1) -> str:
    """Número com vírgula decimal."""
    return f"{float(v or 0):.{casas}f}".replace(".", ",")


def _dur(minutos) -> str:
    m = int(round(abs(minutos or 0)))
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


def _ate(d, agora):
    return d if d and d <= agora else None


def _janelas_por_pessoa(itens, campo, ini_k, fim_k, peso_k, agora, p_turno_h=12.0):
    """{pessoa: {"ini", "fim", "t", "instrucoes", "horas"}} — a janela vai do
    primeiro início ao último fim (ou agora), limitada a `p_turno_h`."""
    saida = {}
    for x in itens:
        nome = (x.get(campo) or "").strip().upper()
        ini = _ate(_dt_(x.get(ini_k)), agora)
        if not nome or not ini:
            continue
        fim = _ate(_dt_(x.get(fim_k)), agora) or agora
        d = saida.setdefault(nome, {"ini": ini, "fim": fim, "t": 0.0, "instrucoes": 0})
        d["ini"], d["fim"] = min(d["ini"], ini), max(d["fim"], fim)
        d["t"] += float(x.get(peso_k) or 0)
        d["instrucoes"] += 1
    for d in saida.values():
        d["horas"] = round(min(max((d["fim"] - d["ini"]).total_seconds() / 3600, 0), p_turno_h), 2)
        d["t_h"] = round(d["t"] / d["horas"], 2) if d["horas"] >= 0.25 else None
        d["t"] = round(d["t"], 3)
    return saida


def ondas(itens, intervalo_min: int = 10) -> list[dict]:
    """Liberações do resumo do painel agrupadas em ondas: liberações até
    `intervalo_min` da anterior são a mesma onda (regra da Produtividade)."""
    # chave só pela hora: duas liberações no mesmo segundo compararam o dict
    # da instrução e quebravam a ordenação (achado na bateria, 14/10/2026)
    lib = sorted(((_dt_(x.get("liberacao")), x) for x in itens if _dt_(x.get("liberacao"))),
                 key=lambda t: t[0])
    grupos = []
    for t, x in lib:
        if grupos and (t - grupos[-1]["ultima"]).total_seconds() / 60 <= intervalo_min:
            g = grupos[-1]
        else:
            g = {"inicio": t, "ultima": t, "itens": []}
            grupos.append(g)
        g["ultima"] = t
        g["itens"].append(x)
    saida = []
    for i, g in enumerate(grupos, start=1):
        inicios = [_dt_(x.get("sep_ini")) for x in g["itens"] if _dt_(x.get("sep_ini"))]
        saida.append({
            "onda": i,
            "liberada": _hm(g["inicio"]),
            "instrucoes": [x.get("instrucao") for x in g["itens"]],
            "qtd": len(g["itens"]),
            "t": round(sum(float(x.get("prev_t") or 0) for x in g["itens"]), 3),
            # da liberação até a primeira instrução da onda começar a separar
            "espera_inicio_min": _min(g["inicio"], min(inicios)) if inicios else None,
            "intervalo_anterior_min": (_min(grupos[i - 2]["ultima"], g["inicio"]) if i > 1 else None),
        })
    return saida


def historico_embarque(registros: list[dict]) -> dict:
    """O que as noites anteriores dizem do embarque, para a noite que ainda
    não mediu: a defasagem entre o fim da separação e o fim do embarque, o
    tempo da última caixa ao fechamento de status e o ritmo de cada
    conferente (t embarcada por hora, mediana por pessoa-noite)."""
    lags, fech, por_pessoa = [], [], {}
    for r in registros or []:
        sf, ef = _dt_(r.get("sep_fim")), _dt_(r.get("emb_fim"))
        if sf and ef and ef >= sf:
            lags.append(_min(sf, ef))
        uc, fs = _dt_(r.get("ult_caixa")) or ef, _dt_(r.get("fech_status"))
        if uc and fs and fs >= uc:
            fech.append(_min(uc, fs))
        conf = (r.get("conferente") or "").strip().upper()
        ei = _dt_(r.get("emb_ini"))
        if conf and ei and ef and ef > ei:
            k = (conf, str(r.get("data_op") or "")[:10])
            d = por_pessoa.setdefault(k, {"ini": ei, "fim": ef, "t": 0.0})
            d["ini"], d["fim"] = min(d["ini"], ei), max(d["fim"], ef)
            d["t"] += _num(r.get("emb_kg")) / 1000
    ritmos = []
    for d in por_pessoa.values():
        h = (d["fim"] - d["ini"]).total_seconds() / 3600
        if h >= 0.25 and d["t"] > 0:
            ritmos.append(d["t"] / h)
    return {"lag_embarque_min": _mediana(lags), "fechamento_min": _mediana(fech),
            "ritmo_conferente_t_h": round(_mediana(ritmos), 3) if ritmos else None,
            "noites": len({k[1] for k in por_pessoa})}


def analisar(foto: dict, dia_embarque, janela: dict | None = None,
             conferentes_presentes: list | None = None, p: dict | None = None,
             historico: dict | None = None) -> dict:
    """Análise da noite no instante do retrato (`foto["momento"]`)."""
    p = p or {}
    if isinstance(dia_embarque, str):
        dia_embarque = _dt.date.fromisoformat(dia_embarque[:10])
    jan = {k: hora_valida((janela or {}).get(k)) for k in CHAVES_JANELA}
    prev = {k: (quando(v, dia_embarque) if v else None) for k, v in jan.items()}
    faltam_horarios = [ROTULOS_JANELA[k] for k in CHAVES_JANELA if not jan[k]]
    agora = _dt_(foto.get("momento")) or _dt.datetime.now()
    itens = [x for x in (foto.get("detalhe") or [])]
    turno_h = float(p.get("turno_h") or 12)

    # ── marcos medidos ────────────────────────────────────────────────
    def marco(campo, f):
        v = [d for d in (_ate(_dt_(x.get(campo)), agora) for x in itens) if d]
        return f(v) if v else None

    falta_sep = round(sum(float(x.get("falta_sep_t") or 0) for x in itens), 3)
    falta_emb = round(sum(float(x.get("falta_emb_t") or 0) for x in itens), 3)
    todas_carregadas = bool(itens) and all(x.get("carregado") for x in itens)
    concluida = bool(itens) and all(x.get("estado") == "concluida" for x in itens) and todas_carregadas
    marcos = {
        "inicio_separacao": _hm(marco("sep_ini", min)),
        "fim_separacao": _hm(marco("sep_fim", max)) if not falta_sep else None,
        "inicio_embarque": _hm(marco("emb_ini", min)),
        "fim_embarque": _hm(marco("emb_fim", max)) if todas_carregadas else None,
        "ultima_caixa": _hm(marco("ult_caixa", max)),
        # término da operação = o fechamento de status da última instrução
        "fechamento_status": _hm(marco("fech_status", max)) if concluida else None,
    }

    # ── ritmo por função ──────────────────────────────────────────────
    seps = _janelas_por_pessoa(itens, "separador", "sep_ini", "sep_fim", "sep_t", agora, turno_h)
    confs = _janelas_por_pessoa(itens, "conferente", "emb_ini", "emb_fim", "emb_t", agora, turno_h)
    projecao = foto.get("projecao") or {}

    # separação: medida ou projetada pela capacidade do retrato
    if not falta_sep:
        fim_sep = marco("sep_fim", max)
        sep_base = "medido"
    elif projecao.get("capacidade_t_h"):
        # o mesmo término que o card mostra: o retrato projeta com o instante
        # exato, e refazer a conta aqui com o minuto arredondado dava 04:16
        # num lugar e 04:17 no outro
        fim_sep = (_dt_(projecao.get("termino_estimado"))
                   or agora + _dt.timedelta(hours=falta_sep / projecao["capacidade_t_h"]))
        sep_base = "projetado"
    else:
        fim_sep, sep_base = None, "sem_base"

    # embarque: conferentes presentes (ou os que embarcaram na noite) no ritmo
    # medido de cada um; e nunca antes da separação acabar
    # Sem medição na noite, vale o histórico das noites anteriores: o
    # embarque ficava com o mesmo horário da separação (02:38 para tudo,
    # usuário 15/10/2026) porque a defasagem entre as duas caía para zero.
    hist = historico or {}
    lags = [_min(_dt_(x.get("sep_fim")), _dt_(x.get("emb_fim"))) for x in itens
            if x.get("carregado") and _dt_(x.get("sep_fim")) and _dt_(x.get("emb_fim"))]
    lag = _mediana([v for v in lags if v is not None and v >= 0])
    lag_base = "medido"
    if lag is None:
        lag, lag_base = hist.get("lag_embarque_min"), "historico"
    ritmos_conf = [d["t_h"] for d in confs.values() if d["t_h"]]
    ritmo_conf, ritmo_base = _mediana(ritmos_conf), "medido"
    if not ritmo_conf:
        ritmo_conf, ritmo_base = hist.get("ritmo_conferente_t_h"), "historico"
    presentes_conf = [n for n in (conferentes_presentes or []) if n] or list(confs)
    cap_emb = round(ritmo_conf * len(presentes_conf), 3) if ritmo_conf and presentes_conf else None
    if todas_carregadas:
        fim_emb, emb_base = marco("emb_fim", max) or marco("ult_caixa", max), "medido"
    else:
        candidatos = []
        if cap_emb:
            # embarque que ainda não começou parte do início previsto dele
            inicio_emb = marco("emb_ini", min) or prev.get("emb_inicio") or agora
            partida = max(agora, inicio_emb)
            candidatos.append(partida + _dt.timedelta(hours=falta_emb / cap_emb))
        if fim_sep and lag is not None:
            # e nunca antes de a separação acabar mais a defasagem de sempre
            candidatos.append(fim_sep + _dt.timedelta(minutes=lag))
        fim_emb = max(candidatos) if candidatos else None
        emb_base = "projetado" if fim_emb else "sem_base"

    def antes(a, b) -> bool:
        """a até b, no minuto (05:30:40 conta como 05:30)."""
        return _min(b, a) is not None and _min(b, a) <= 0

    def veredito(fim, limite, rotulo, artigo, fim_operacao=None):
        """Dentro do previsto da etapa; fora dele mas com a operação
        (separação + embarque) fechando até o corte; ou além do corte."""
        verbo = "terminou" if fim and fim <= agora else "termina"
        if not fim:
            return {"situacao": "sem_base",
                    "texto": f"Sem base para projetar o fim {artigo} {rotulo}."}
        if not limite:
            return {"situacao": "sem_janela",
                    "texto": f"{rotulo.capitalize()} {verbo} às {_hm(fim)}. O fim previsto {artigo} "
                             f"{rotulo} não está cadastrado em Parâmetros."}
        if antes(fim, limite):
            folga = _min(fim, limite)
            return {"situacao": "dentro", "folga_min": folga,
                    "texto": f"{rotulo.capitalize()} {verbo} às {_hm(fim)}, dentro do previsto "
                             f"({_hm(limite)})" + (f", com {_dur(folga)} de folga." if folga else ".")}
        excesso = _min(limite, fim) if limite else None
        corte = prev["corte"]
        fim_op = fim_operacao or fim
        fecha = "fecharam" if fim_op <= agora else "fecham"
        if corte and antes(fim_op, corte):
            return {"situacao": "fora_dentro_corte", "excesso_min": excesso,
                    "texto": f"{rotulo.capitalize()} {verbo} às {_hm(fim)}, {_dur(excesso)} depois do "
                             f"previsto ({_hm(limite)}), mas separação e embarque {fecha} às "
                             f"{_hm(fim_op)}, dentro do corte das {_hm(corte)}."}
        if not corte:
            return {"situacao": "fora_previsto", "excesso_min": excesso,
                    "texto": f"{rotulo.capitalize()} {verbo} às {_hm(fim)}, {_dur(excesso)} depois do "
                             f"previsto ({_hm(limite)}). O corte final não está cadastrado em "
                             f"Parâmetros."}
        alem = _min(corte, fim_op)
        return {"situacao": "alem_do_corte", "excesso_min": excesso, "alem_corte_min": alem,
                "texto": f"{rotulo.capitalize()} {verbo} às {_hm(fim)}, {_dur(excesso)} depois do "
                         f"previsto ({_hm(limite)}); separação e embarque {fecha} às {_hm(fim_op)}, "
                         f"{_dur(alem)} além do corte das {_hm(corte)}." if corte else
                         f"{rotulo.capitalize()} {verbo} às {_hm(fim)}, {_dur(excesso)} depois do previsto."}

    # a separação pode passar do expediente dela e a operação ainda fechar no
    # corte: o que decide é o fim de separação + embarque
    v_sep = veredito(fim_sep, prev["sep_fim"], "separação", "da", fim_emb)
    v_emb = veredito(fim_emb, prev["emb_fim"], "embarque", "do", fim_emb)

    # ── medições para o Expedito ──────────────────────────────────────
    # O julgamento do que é gargalo fica com o Expedito (usuário, 14/10/2026:
    # "deixe a análise de gargalos para o Expedito, não engesse no código").
    # Aqui só se MEDE — sem limite fixo de "quanto é demais".
    lista_ondas = ondas(itens, int(p.get("onda_intervalo_min") or 10))

    # Intervalos de cada separador entre uma instrução e a próxima, já sem a
    # refeição e as pausas térmicas (usuário, 14/10/2026: "tá desconsiderando
    # horário de janta?"). As durações vêm dos parâmetros do CD na
    # Produtividade. A refeição sai do maior intervalo da pessoa na noite; as
    # pausas térmicas que a jornada dela prevê saem dos demais, do maior para
    # o menor. Quanto do intervalo livre foi esperando a liberação da próxima
    # instrução também é medido.
    refeicao = float(_p(p, "refeicao_min") or 0)
    continuo = float(_p(p, "trabalho_continuo_min") or 0)
    pausa = float(_p(p, "pausa_termica_min") or 0)
    intervalos = []
    por_sep: dict[str, list] = {}
    for x in itens:
        n = (x.get("separador") or "").strip().upper()
        if n and _ate(_dt_(x.get("sep_ini")), agora):
            por_sep.setdefault(n, []).append(x)
    for nome, lst in por_sep.items():
        lst.sort(key=lambda x: _dt_(x.get("sep_ini")))
        gaps = []
        for a, b in zip(lst, lst[1:]):
            fim_a, ini_b = _dt_(a.get("sep_fim")), _dt_(b.get("sep_ini"))
            gap = _min(fim_a, ini_b)
            if gap is None or gap <= 0:
                continue
            lib_b = _dt_(b.get("liberacao"))
            espera_lib = min(gap, _min(fim_a, lib_b)) if lib_b and fim_a and lib_b > fim_a else 0
            gaps.append({"min": gap, "espera_lib": espera_lib, "de": _hm(fim_a), "ate": _hm(ini_b)})
        ini_p = _dt_(lst[0].get("sep_ini"))
        fim_p = max((d for d in (_dt_(x.get("sep_fim")) for x in lst) if d), default=None)
        jornada = _min(ini_p, min(fim_p, agora)) if ini_p and fim_p else 0
        pausas = (jornada // (continuo + pausa)) * pausa if continuo > 0 and pausa > 0 and jornada else 0.0
        gaps.sort(key=lambda g: -g["min"])
        resto_pausa, descontado = pausas, 0.0
        for k, g in enumerate(gaps):
            abate = min(g["min"], refeicao) if k == 0 and refeicao > 0 else 0.0
            if resto_pausa > 0:
                extra = min(g["min"] - abate, pausa, resto_pausa)
                abate += extra
                resto_pausa -= extra
            g["livre_min"] = round(max(g["min"] - abate, 0))
            descontado += abate
        livres = [g for g in gaps if g["livre_min"] > 0]
        intervalos.append({
            "pessoa": nome, "jornada_min": jornada,
            "pausas_descontadas_min": round(descontado),
            "livre_total_min": sum(g["livre_min"] for g in livres),
            "aguardando_liberacao_min": round(sum(min(g["livre_min"], g["espera_lib"]) for g in livres)),
            "maiores": [{"min": g["livre_min"], "de": g["de"], "ate": g["ate"]}
                        for g in sorted(livres, key=lambda g: -g["livre_min"])[:3]],
        })
    intervalos.sort(key=lambda o: -o["livre_total_min"])

    # início da separação contra o previsto
    ini_sep = marco("sep_ini", min)
    atraso_ini = _min(prev["sep_inicio"], ini_sep) if ini_sep and prev["sep_inicio"] else None

    # espera entre o fim da separação e o início do embarque, por instrução
    esperas = sorted(((_min(_dt_(x.get("sep_fim")), _ate(_dt_(x.get("emb_ini")), agora)), x.get("instrucao"))
                      for x in itens), key=lambda t: -(t[0] or 0))
    esperas = [(e, i_) for e, i_ in esperas if e is not None and e > 0]

    # da última caixa ao fechamento de status, por instrução
    fech = sorted(((_min(_dt_(x.get("ult_caixa")) or _dt_(x.get("emb_fim")),
                         _ate(_dt_(x.get("fech_status")), agora)), x.get("instrucao")) for x in itens),
                  key=lambda t: -(t[0] or 0))
    fech = [(f_, i_) for f_, i_ in fech if f_ is not None and f_ >= 0]

    medicoes = {
        "inicio_separacao_vs_previsto_min": atraso_ini,
        "espera_separacao_embarque": {
            "mediana_min": _mediana([e for e, _ in esperas]),
            "instrucoes": [{"instrucao": i_, "min": e} for e, i_ in esperas[:10]],
        },
        "fechamento_apos_ultima_caixa": {
            "mediana_min": _mediana([f_ for f_, _ in fech]),
            "instrucoes": [{"instrucao": i_, "min": f_} for f_, i_ in fech[:10]],
        },
        "intervalos_livres_total_min": sum(o["livre_total_min"] for o in intervalos),
        "aguardando_liberacao_total_min": sum(o["aguardando_liberacao_min"] for o in intervalos),
    }

    # o término da operação é o fechamento de status; o corte vale para
    # separação + embarque, mas a tela mostra a distância entre os dois
    # término = fim do embarque + o tempo até o fechamento de status
    fech_med = _mediana([f_ for f_, _ in fech]) if fech else None
    fech_min = fech_med if fech_med is not None else hist.get("fechamento_min")
    if concluida:
        termino_dt = marco("fech_status", max)
    elif fim_emb and fech_min is not None:
        termino_dt = fim_emb + _dt.timedelta(minutes=fech_min)
    else:
        termino_dt = fim_emb
    apos_corte = _min(prev["corte"], termino_dt) if prev["corte"] and termino_dt else None

    return {
        "momento": _hm(agora),
        "concluida": concluida,
        "janela": jan,
        "faltam_horarios": faltam_horarios,
        "marcos": marcos,
        "termino_operacao": marcos["fechamento_status"] or _hm(termino_dt),
        "termino_base": "fechamento_status" if marcos["fechamento_status"] else emb_base,
        "termino_inclui_fechamento_min": None if concluida else fech_min,
        "termino_apos_corte_min": apos_corte if apos_corte and apos_corte > 0 else 0,
        "separacao": {**v_sep, "fim": _hm(fim_sep), "base": sep_base, "previsto": jan["sep_fim"],
                      "falta_t": falta_sep},
        "embarque": {**v_emb, "fim": _hm(fim_emb), "base": emb_base, "previsto": jan["emb_fim"],
                     "falta_t": falta_emb, "capacidade_t_h": cap_emb, "ritmo_conferente_t_h": ritmo_conf,
                     "conferentes": len(presentes_conf),
                     "ritmo_base": ritmo_base if ritmo_conf else None,
                     "defasagem_separacao_min": round(lag) if lag is not None else None,
                     "defasagem_base": lag_base if lag is not None else None},
        "corte": jan["corte"],
        "ondas": lista_ondas,
        "ondas_disponiveis": bool(lista_ondas),
        "intervalos": intervalos[:15],
        "pausas_consideradas": {"refeicao_min": refeicao, "pausa_termica_min": pausa,
                                "a_cada_min": continuo},
        "medicoes": medicoes,
        "produtividade": {
            "separadores": sorted(({"pessoa": n, **{k: d[k] for k in ("t", "horas", "t_h")},
                                    "qtd_instrucoes": d["instrucoes"],
                                    "inicio": _hm(d["ini"]), "fim": _hm(d["fim"])}
                                   for n, d in seps.items()), key=lambda x: -(x["t"] or 0)),
            "conferentes": sorted(({"pessoa": n, **{k: d[k] for k in ("t", "horas", "t_h")},
                                    "qtd_instrucoes": d["instrucoes"],
                                    "inicio": _hm(d["ini"]), "fim": _hm(d["fim"])}
                                   for n, d in confs.items()), key=lambda x: -(x["t"] or 0)),
        },
    }


def resposta_calculada(analise: dict, foto: dict) -> str:
    """Resposta em markdown montada só com os números, para quando a IA não
    responde a tempo: o Expedito nunca devolve "sem resposta"."""
    t = foto.get("toneladas") or {}
    inst = foto.get("instrucoes") or {}
    linhas = []
    if analise.get("concluida"):
        linhas.append(f"**Operação concluída — fechamento de status às {analise['marcos']['fechamento_status']}.**")
    else:
        linhas.append(f"**{_n(t.get('pct_concluido'), 0)}% concluído às {analise.get('momento')}; "
                      f"término projetado às {analise.get('termino_operacao') or '—'}.**")
    linhas.append(f"- {analise['separacao']['texto']}")
    linhas.append(f"- {analise['embarque']['texto']}")
    linhas.append(f"- {inst.get('total', 0)} instrução(ões), {_n(t.get('previstas'))} t previstas, "
                  f"{_n(t.get('separadas'))} t separadas no picking e {_n(t.get('embarcadas'))} t "
                  f"embarcadas.")
    m = analise.get("medicoes") or {}
    esp = (m.get("espera_separacao_embarque") or {}).get("mediana_min")
    fec = (m.get("fechamento_apos_ultima_caixa") or {}).get("mediana_min")
    if esp is not None:
        linhas.append(f"- Espera entre o fim da separação e o início do embarque: {_dur(esp)} (mediana).")
    if fec is not None:
        linhas.append(f"- Da última caixa ao fechamento de status: {_dur(fec)} (mediana).")
    if m.get("intervalos_livres_total_min"):
        linhas.append(f"- Intervalos entre instruções, já sem refeição e pausas térmicas: "
                      f"{_dur(m['intervalos_livres_total_min'])} somados na equipe.")
    if not analise.get("ondas_disponiveis"):
        linhas.append("> O resumo do painel (ondas de liberação) não foi carregado para este dia; "
                      "a análise de espera por liberação fica de fora.")
    return "\n".join(linhas)
