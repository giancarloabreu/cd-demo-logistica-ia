"""
Expedito — copiloto da expedição (02/10/2026).

Lê o painel do WMS quantas vezes o analista quiser durante a noite e devolve
o RETRATO da operação naquele instante: o que já saiu, o que falta, em que
ritmo a equipe está indo, se o cenário encaminha para fechar dentro do
horário, quem está ocioso e qual instrução entregar a quem.

Por que o retrato existe separado da conversa com a IA (decisão do usuário,
02/10/2026): a inteligência adaptativa fica no raciocínio em cima de números
MEDIDOS, não no lugar deles. Sem esse chão o modelo chuta nome de gente e
tonelada; com ele, a sugestão é verificável e a tela funciona mesmo quando a
API externa está fora do ar ou a cota acabou.

Nada aqui conhece internet. O envio para a IA — sempre com CODINOME no lugar
do nome da pessoa — é responsabilidade da camada web.
"""
from __future__ import annotations

import datetime as _dt

from app.services.produtividade_calc import (_dt_, _num, _p, horas_liquidas,
                                             motivo_expurgo, turno_de)

# Instrução sem separação iniciada e sem peso separado está NA FILA; com
# início e sem fim está EM ANDAMENTO; com fim (ou com embarque encerrado)
# está CONCLUÍDA. O painel do WMS é tirado a qualquer hora da noite, então
# os três estados convivem no mesmo arquivo.
FILA, ANDAMENTO, CONCLUIDA = "fila", "andamento", "concluida"

# Abaixo disto de horas-pessoa medidas na noite, o ritmo medido é ruído: três
# separadores com 1 a 3 minutos de trabalho cada projetaram "atraso" às 19h31
# de 08/10/2026. Até lá a capacidade vem do ritmo histórico de quem está
# presente.
HORAS_PESSOA_MINIMAS = 1.0


def _hm(d) -> str | None:
    return d.strftime("%Y-%m-%d %H:%M") if d else None


# Marcos de fim, em qualquer um dos quais a instrução está pronta. Só o fim
# da separação não basta: há instrução que embarca sem passar pelo picking
# (26853 em 09/10: sem início de separação, embarque às 05h30).
_MARCOS_FIM = ("sep_fim", "ult_caixa", "emb_fim", "fech_status")


def _fim_da_instrucao(linha: dict, agora: _dt.datetime) -> _dt.datetime | None:
    """O primeiro marco de fim que já tinha acontecido em `agora`."""
    marcos = [d for d in (_dt_(linha.get(c)) for c in _MARCOS_FIM) if d and d <= agora]
    return min(marcos) if marcos else None


def _status_vale(linha: dict, agora: _dt.datetime) -> bool:
    """O status do WMS não tem horário: vale para o instante da carga em
    diante, nunca para reler um momento anterior a ela."""
    carga = _dt_(linha.get("atualizado_em"))
    return carga is not None and carga <= agora


def _status(linha: dict, campo: str) -> str:
    return str(linha.get(campo) or "").strip().upper()


def carregou_em(linha: dict, agora: _dt.datetime):
    """Quando a carreta desta instrução terminou de carregar — o datetime do
    fim do embarque (ou do fechamento), True quando o WMS diz "Em Viagem" sem
    horário, None se ainda não carregou em `agora`."""
    marcos = [d for d in (_dt_(linha.get(c)) for c in ("emb_fim", "fech_status"))
              if d and d <= agora]
    if marcos:
        return min(marcos)
    if _status_vale(linha, agora) and (_status(linha, "status_veic") == "EM VIAGEM"
                                       or _status(linha, "status_op") == "FATURADO"):
        return True
    return None


def _estado_instrucao(linha: dict, agora: _dt.datetime) -> str:
    """Estado NO INSTANTE `agora`, não no fim do arquivo.

    O painel carregado às 2h traz horários que, para a foto das 22h30, ainda
    não tinham acontecido. Sem olhar o instante, uma releitura do histórico
    mostraria a noite inteira como concluída desde o começo — e o gerente
    perderia justamente a sequência que ele quer consultar.

    Concluída: algum marco de fim já aconteceu, ou o WMS diz "Faturado" na
    carga que vale para este instante. Com só o fim da separação e da última
    caixa, a noite de 09/10 ficou em 86% com tudo faturado e em viagem.
    """
    ini = _dt_(linha.get("sep_ini"))
    if _fim_da_instrucao(linha, agora):
        return CONCLUIDA
    if _status_vale(linha, agora) and _status(linha, "status_op") == "FATURADO":
        return CONCLUIDA
    if (ini and ini <= agora) or (not ini and _num(linha.get("sep_kg")) > 0):
        return ANDAMENTO
    return FILA


def _inicio_do_turno(agora: _dt.datetime, turno: str, p: dict,
                     dia_operacao: _dt.date | None = None) -> _dt.datetime:
    """Abertura do turno — âncora do tempo decorrido e da janela.

    Com `dia_operacao`, a âncora é esse dia, e não o relógio. É o que permite
    o gerente reler uma noite de semanas atrás: ancorar em `agora` colocaria
    a janela em cima de hoje e o retrato daquela noite sairia vazio (achado
    ao rodar a bateria depois de a janela passar a recortar, 08/10/2026).
    """
    h = int(_p(p, "noturno_fim")) if turno == "diurno" else int(_p(p, "noturno_inicio"))
    if dia_operacao is not None:
        return _dt.datetime.combine(dia_operacao, _dt.time(hour=h))
    ini = agora.replace(hour=h, minute=0, second=0, microsecond=0)
    if ini > agora:
        ini -= _dt.timedelta(days=1)
    return ini


def _fim_previsto_do_turno(inicio: _dt.datetime, p: dict) -> _dt.datetime:
    return inicio + _dt.timedelta(hours=float(_p(p, "turno_h")))


def retrato(registros: list[dict], presentes: list[str], agora: _dt.datetime,
            p: dict | None = None, turno: str = "noturno",
            desconsideradas: set | None = None,
            dia_operacao=None, ritmo_historico: dict | None = None,
            carimbo: str | None = None, fim_previsto=None) -> dict:
    """Foto da operação no instante `agora`.

    `registros`: instruções do dia, como saem do banco (o painel pode estar
    iniciado ou não). `presentes`: nomes dos separadores em casa agora —
    sem essa lista não há sugestão, porque recomendar carga para quem faltou
    é pior que não recomendar nada. `carimbo`: a data de operação do WMS
    desta noite — instrução sem horário nenhum só entra com esse carimbo.
    """
    p = p or {}
    descons = desconsideradas or set()
    presentes = [str(n).strip().upper() for n in presentes if str(n).strip()]
    if isinstance(dia_operacao, str) and dia_operacao:
        try:
            dia_operacao = _dt.date.fromisoformat(dia_operacao[:10])
        except ValueError:
            dia_operacao = None
    inicio_turno = _inicio_do_turno(agora, turno, p, dia_operacao or None)
    # O fim que a projeção da separação persegue é o fim previsto da
    # separação cadastrado no Expedito, quando houver. Comparar com o fim do
    # turno da Produtividade dava "0h38 além do turno" (02:00) enquanto a
    # análise e a IA diziam "22 min de folga" até 03:00 — dois relógios para
    # a mesma separação (usuário, 15/10/2026).
    fim_turno = fim_previsto or _fim_previsto_do_turno(inicio_turno, p)

    instrucoes, expurgadas, fora_da_janela, tiradas = [], 0, 0, []
    for r in registros:
        if turno and turno_de(r, p) != turno:
            continue
        # A instrução pertence a ESTA ocorrência do turno?
        #
        # O WMS carimba a noite que COMEÇA em 08/10 como data de operação
        # 09/10 (é a data em que ela termina), então filtrar pelo carimbo
        # perdia a operação inteira: no painel real de 08/10 às 19:30, as 5
        # instruções com 16,9 t previstas vinham como 09/10 e ficavam
        # invisíveis, enquanto sobravam na tela 24 instruções da noite
        # anterior (apontado pelo usuário, 08/10/2026 — "falta projetar a
        # operação toda"). Quem decide agora é a JANELA: noites consecutivas
        # distam 24 h, então o início da separação separa uma da outra sem
        # ambiguidade. Instrução ainda sem início é trabalho que vem, e fica.
        ref = _dt_(r.get("sep_ini")) or _dt_(r.get("emb_ini"))
        if ref is not None and not (inicio_turno - _dt.timedelta(hours=1)
                                    <= ref < inicio_turno + _dt.timedelta(hours=23)):
            fora_da_janela += 1
            continue
        # Sem horário nenhum, a janela não decide: entra a do carimbo desta
        # noite. Antes ficava qualquer instrução sem início dos dois dias
        # buscados — o teste de um dia anterior seguia na fila (09/10/2026).
        if ref is None and carimbo and str(r.get("data_op") or "")[:10] != carimbo:
            fora_da_janela += 1
            continue
        motivo = motivo_expurgo(r, descons)
        if motivo:
            expurgadas += 1
            if motivo == "MANUAL":
                tiradas.append({"empresa": r.get("empresa", ""), "instrucao": r.get("instrucao"),
                                "placa": r.get("placa") or "",
                                "prev_t": round(_num(r.get("prev_kg")) / 1000, 3)})
            continue
        prev_kg, sep_kg = _num(r.get("prev_kg")), _num(r.get("sep_kg"))
        linha = {
            "instrucao": r.get("instrucao"), "empresa": r.get("empresa", ""),
            "separador": (r.get("separador") or "").strip().upper(),
            "conferente": (r.get("conferente") or "").strip().upper(),
            "doca": r.get("doca") or "", "cliente": r.get("cliente") or "",
            "cidade": r.get("cidade") or "", "placa": r.get("placa") or "",
            "sku": _num(r.get("sku")), "escalas": _num(r.get("escalas")),
            "prioridade": _num(r.get("prioridade")),
            "prev_t": round(prev_kg / 1000, 3), "sep_t": round(sep_kg / 1000, 3),
            "sep_ini": r.get("sep_ini"), "sep_fim": r.get("sep_fim"),
            "ult_caixa": r.get("ult_caixa"), "prev_viagem": r.get("prev_viagem"),
            "liberacao": r.get("liberacao"),
            "emb_t": round(_num(r.get("emb_kg")) / 1000, 3),
            "emb_ini": r.get("emb_ini"), "emb_fim": r.get("emb_fim"),
            "fech_status": r.get("fech_status"),
            "status_op": r.get("status_op") or "", "status_veic": r.get("status_veic") or "",
        }
        linha["estado"] = _estado_instrucao(r, agora)
        if linha["estado"] == FILA:
            # ainda não começou neste instante: o peso do arquivo é do futuro
            linha["sep_t"] = 0.0
        emb_ini = _dt_(r.get("emb_ini"))
        if linha["estado"] == FILA or (emb_ini and emb_ini > agora):
            linha["emb_t"] = 0.0
        carregou = carregou_em(r, agora)
        linha["carregado"] = carregou is not None
        linha["carregou_em"] = _hm(carregou) if isinstance(carregou, _dt.datetime) else None
        # Feito por instrução: o previsto quando concluída; antes disso, o
        # maior entre separado e embarcado. Em 09/10, 19,4 t embarcaram sem
        # passar pelo picking — somar só o separado deixava a noite em 80%.
        if linha["estado"] == CONCLUIDA:
            linha["feito_t"] = linha["prev_t"]
        else:
            linha["feito_t"] = round(max(linha["sep_t"], linha["emb_t"]), 3)
        linha["falta_t"] = round(max(linha["prev_t"] - linha["feito_t"], 0.0), 3)
        if linha["estado"] == CONCLUIDA:
            linha["falta_t"] = 0.0
        # Separação e embarque têm janelas e equipes próprias (usuário,
        # 14/10/2026): o que falta em cada etapa sai separado. A separação
        # acaba no fim da separação (ou quando a instrução fechou); o
        # embarque, quando a carreta carregou.
        sep_fim = _dt_(r.get("sep_fim"))
        sep_pronta = linha["estado"] == CONCLUIDA or (sep_fim is not None and sep_fim <= agora)
        linha["falta_sep_t"] = 0.0 if sep_pronta else round(max(linha["prev_t"] - linha["sep_t"], 0.0), 3)
        linha["falta_emb_t"] = (0.0 if linha["carregado"]
                                else round(max(linha["prev_t"] - linha["emb_t"], 0.0), 3))
        instrucoes.append(linha)

    por_estado = {e: [x for x in instrucoes if x["estado"] == e]
                  for e in (FILA, ANDAMENTO, CONCLUIDA)}
    falta_t = round(sum(x["falta_t"] for x in instrucoes), 3)
    # o RITMO é do picking: tonelada separada por hora-pessoa
    feito_t = round(sum(x["sep_t"] for x in instrucoes), 3)
    pronto_t = round(sum(x["feito_t"] for x in instrucoes), 3)

    # ── ritmo REAL da noite até agora ──────────────────────────────────
    # horas de quem efetivamente separou (janela de cada um, limitada ao
    # turno), não horas de relógio: quem entrou mais tarde não infla a conta
    janelas = {}
    for x in instrucoes:
        ini, fim = _dt_(x["sep_ini"]), _dt_(x["sep_fim"]) or agora
        if not ini or ini > agora or not x["separador"]:
            continue
        fim = min(fim, agora)
        a, b = janelas.get(x["separador"], (ini, fim))
        janelas[x["separador"]] = (min(a, ini), max(b, fim))
    horas_equipe = 0.0
    for nome, (a, b) in janelas.items():
        bruto = max((b - a).total_seconds() / 3600, 0.0)
        horas_equipe += horas_liquidas(min(bruto, float(_p(p, "turno_h"))), p)
    # horas_equipe soma as janelas INDIVIDUAIS, então esta divisão já entrega
    # tonelada por HORA-PESSOA — mesma unidade do RITMO do painel de
    # Produtividade (2,45 t/h do melhor separador em 29/09). Dividir de novo
    # pelo tamanho da equipe foi um erro meu, que projetava 6 h de atraso numa
    # noite tranquila (corrigido 02/10/2026).
    ritmo_pessoa = round(feito_t / horas_equipe, 3) if horas_equipe > 0 else None

    # ── projeção de término ────────────────────────────────────────────
    # Duas bases possíveis, e a tela diz qual está em uso:
    #  - MEDIDO: ritmo da noite x gente presente, quando já há horas-pessoa
    #    suficientes para o número não ser ruído;
    #  - HISTÓRICO: soma do ritmo de turnos anteriores de cada presente, no
    #    começo do turno. Sem nenhuma das duas, não projeta.
    capacidade_t_h, base = None, None
    termino, folga_min, risco = None, None, "sem_base"
    if presentes and ritmo_pessoa and horas_equipe >= HORAS_PESSOA_MINIMAS:
        # ritmo por hora-pessoa x gente presente = capacidade da equipe agora
        capacidade_t_h, base = round(ritmo_pessoa * len(presentes), 3), "medido"
    elif presentes and ritmo_historico:
        conhecidos = [ritmo_historico[n] for n in presentes if ritmo_historico.get(n)]
        if conhecidos:
            # presente sem histórico entra com a mediana dos que têm, em vez
            # de zero — gente nova não é gente parada
            mediana = sorted(conhecidos)[len(conhecidos) // 2]
            capacidade_t_h = round(sum(ritmo_historico.get(n) or mediana
                                       for n in presentes), 3)
            base = "historico"
    falta_sep_t = round(sum(x["falta_sep_t"] for x in instrucoes), 3)
    if capacidade_t_h:
        if capacidade_t_h > 0:
            # a equipe de SEPARAÇÃO projeta o que falta separar; o embarque
            # tem projeção própria, com os conferentes (expedito_analise)
            horas_restantes = falta_sep_t / capacidade_t_h
            termino = agora + _dt.timedelta(hours=horas_restantes)
            folga_min = round((fim_turno - termino).total_seconds() / 60)
            risco = "no_horario" if folga_min >= 45 else ("apertado" if folga_min >= 0 else "atraso")
    if not falta_t:
        # Turno fechado: o término é a hora em que a operação REALMENTE
        # acabou — a última caixa bipada, ou o fim da última separação. Antes
        # eu devolvia o instante do retrato, e reabrir a tela no dia seguinte
        # mostrava "terminou às 15:30" com 12 h além do turno (07/10/2026).
        fins = [d for d in (_dt_(x["ult_caixa"]) or _dt_(x["emb_fim"]) or _dt_(x["sep_fim"])
                            or _dt_(x["fech_status"]) for x in instrucoes) if d and d <= agora]
        termino = max(fins) if fins else agora
        risco = "concluido"
        folga_min = round((fim_turno - termino).total_seconds() / 60)

    # ── quem está livre agora ──────────────────────────────────────────
    ocupados = {x["separador"] for x in por_estado[ANDAMENTO] if x["separador"]}
    livres = [n for n in presentes if n not in ocupados]
    # presente que ainda não apareceu em instrução nenhuma na noite
    sem_registro = [n for n in presentes if n not in janelas]
    # separador que aparece no painel e não foi marcado como presente
    fora_da_lista = sorted({x["separador"] for x in por_estado[ANDAMENTO]
                            if x["separador"] and x["separador"] not in presentes})

    docas_ocupadas = sorted({x["doca"] for x in por_estado[ANDAMENTO] if x["doca"]})
    docas_param = int(_p(p, "docas_disponiveis") or 0)

    return {
        "momento": _hm(agora),
        "turno": turno,
        "inicio_turno": _hm(inicio_turno),
        "fim_previsto_turno": _hm(fim_turno),
        "fim_base": "separacao_prevista" if fim_previsto else "turno",
        "decorrido_min": round((agora - inicio_turno).total_seconds() / 60),
        "instrucoes": {
            "total": len(instrucoes),
            "fila": len(por_estado[FILA]),
            "andamento": len(por_estado[ANDAMENTO]),
            "concluidas": len(por_estado[CONCLUIDA]),
            "expurgadas": expurgadas,
            "fora_da_janela": fora_da_janela,
            "carregadas": sum(1 for x in instrucoes if x["carregado"]),
        },
        "tiradas": tiradas,
        "toneladas": {
            "previstas": round(sum(x["prev_t"] for x in instrucoes), 3),
            "separadas": feito_t,
            "embarcadas": round(sum(x["emb_t"] for x in instrucoes), 3),
            # concluído = previsto das prontas + o feito das que seguem abertas
            "concluidas": pronto_t,
            "faltam": falta_t,
            "faltam_separar": falta_sep_t,
            "faltam_embarcar": round(sum(x["falta_emb_t"] for x in instrucoes), 3),
            "pct_concluido": (round(pronto_t / (pronto_t + falta_t) * 100, 1)
                              if (pronto_t + falta_t) else None),
        },
        "ritmo": {
            "t_h_por_pessoa": ritmo_pessoa,
            "horas_pessoa_ate_agora": round(horas_equipe, 2),
            "pessoas_com_registro": len(janelas),
        },
        "projecao": {
            "capacidade_t_h": capacidade_t_h,
            "base": base,
            "termino_estimado": _hm(termino),
            "folga_min": folga_min,
            "risco": risco,
        },
        "equipe": {
            "presentes": presentes,
            "ocupados": sorted(ocupados & set(presentes)),
            "livres": livres,
            "sem_registro_na_noite": sem_registro,
            "no_painel_fora_da_lista": fora_da_lista,
        },
        "docas": {
            "em_uso": docas_ocupadas,
            "disponiveis": docas_param or None,
            "livres": (docas_param - len(docas_ocupadas)) if docas_param else None,
        },
        "detalhe": instrucoes,
    }


def desempenho_historico(registros_historicos: list[dict], p: dict | None = None) -> dict:
    """Ritmo medido de cada separador em noites anteriores: {NOME: t/h}.

    É o que permite dizer "esta instrução pesada rende mais com o João" em
    vez de distribuir no sorteio. Usa a mediana por pessoa-dia, mesma régua
    do painel de Produtividade.
    """
    p = p or {}
    por_pessoa: dict[str, list[float]] = {}
    for r in registros_historicos:
        nome = (r.get("separador") or "").strip().upper()
        ini, fim = _dt_(r.get("sep_ini")), _dt_(r.get("sep_fim"))
        sep_t = _num(r.get("sep_kg")) / 1000
        if not nome or not ini or not fim or sep_t <= 0:
            continue
        bruto = (fim - ini).total_seconds() / 3600
        liq = horas_liquidas(min(max(bruto, 0.0), float(_p(p, "turno_h"))), p)
        if liq > 0:
            por_pessoa.setdefault(nome, []).append(sep_t / liq)
    saida = {}
    for nome, valores in por_pessoa.items():
        v = sorted(valores)
        meio = len(v) // 2
        saida[nome] = round(v[meio] if len(v) % 2 else (v[meio - 1] + v[meio]) / 2, 3)
    return saida


def sugerir_alocacao(foto: dict, desempenho: dict | None = None,
                     limite_sku_complexa: float = 30.0) -> list[dict]:
    """Quem pega qual instrução da fila, com o motivo escrito.

    Regra, nesta ordem: instrução mais urgente primeiro (prioridade do WMS,
    depois previsão de viagem, depois peso); para cada uma, o separador livre
    com melhor ajuste — ritmo medido alto para carga pesada ou complexa.
    Cada sugestão carrega a JUSTIFICATIVA, porque a tela precisa defender a
    recomendação diante do analista, não pedir fé.
    """
    desempenho = desempenho or {}
    livres = list(foto["equipe"]["livres"])
    fila = [x for x in foto["detalhe"] if x["estado"] == FILA]

    def urgencia(x):
        viagem = _dt_(x.get("prev_viagem"))
        return (-x["prioridade"], viagem or _dt.datetime.max, -x["falta_t"])

    fila.sort(key=urgencia)
    # mais rápido primeiro: a fila está ordenada por urgência, então o melhor
    # ritmo vai para a carga mais urgente
    livres.sort(key=lambda n: -(desempenho.get(n) or 0))

    sugestoes = []
    for x in fila:
        if not livres:
            sugestoes.append({
                "instrucao": x["instrucao"], "separador": None,
                "falta_t": x["falta_t"], "sku": x["sku"], "doca": x["doca"],
                "motivo": "Sem separador livre no momento — esta instrução espera a próxima liberação.",
            })
            continue
        nome = livres.pop(0)
        ritmo = desempenho.get(nome)
        complexa = x["sku"] >= limite_sku_complexa
        partes = []
        if ritmo:
            partes.append(f"ritmo medido de {ritmo:.2f} t/h")
        if complexa:
            partes.append(f"instrução complexa ({int(x['sku'])} SKUs)")
        if x["prioridade"] >= 100:
            partes.append(f"prioridade {int(x['prioridade'])} no WMS")
        if x["falta_t"]:
            partes.append(f"{x['falta_t']:.1f} t a separar")
        sugestoes.append({
            "instrucao": x["instrucao"], "separador": nome,
            "falta_t": x["falta_t"], "sku": x["sku"], "doca": x["doca"],
            "cliente": x["cliente"], "cidade": x["cidade"],
            "motivo": " · ".join(partes) or "disponível agora",
        })
    return sugestoes


def codinomes(presentes: list[str]) -> dict:
    """{NOME REAL: "Separador A"} — estável dentro da mesma lista.

    O nome da pessoa nunca sai da VM: o que vai para a IA é o codinome, e a
    resposta é traduzida de volta antes de chegar na tela (decisão do
    usuário, 02/10/2026, para o envio não carregar dado pessoal de
    empregado identificado). A ordem da lista de presentes define as letras,
    e por isso o mapa é refeito a cada dia.
    """
    letras = []
    for i in range(len(presentes)):
        nome, n = "", i
        while True:
            nome = chr(ord("A") + n % 26) + nome
            n = n // 26 - 1
            if n < 0:
                break
        letras.append(nome)
    return {nome: f"Separador {letras[i]}" for i, nome in enumerate(presentes)}


def anonimizar(foto: dict, sugestoes: list[dict], mapa: dict) -> dict:
    """Retrato pronto para sair da VM: nomes trocados por codinome."""
    def tr(nome):
        return mapa.get(nome, "Separador ?") if nome else None

    equipe = {k: ([tr(n) for n in v] if isinstance(v, list) else v)
              for k, v in foto["equipe"].items()}
    detalhe = [{**x, "separador": tr(x["separador"]), "conferente": None}
               for x in foto["detalhe"]]
    return {
        **{k: v for k, v in foto.items() if k not in ("equipe", "detalhe")},
        "equipe": equipe,
        "detalhe": detalhe,
        "sugestoes": [{**s, "separador": tr(s["separador"])} for s in sugestoes],
    }
