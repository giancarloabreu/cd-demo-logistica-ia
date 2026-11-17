"""
Expedito — situação dos veículos da noite (08/10/2026).

Responde a uma pergunta da operação: das carretas que a noite tem para
carregar, quais já estão no CD e quais ainda estão na estrada.

Por que a POSIÇÃO não vem do painel do WMS: antes de carregar, a coluna
"Status > Veículo" mostra "Chegada Prevista" para todas, porque o WMS não tem
integração com a Ravex (usuário, 08/10/2026). Quem sabe onde o veículo está é
o rastreio, atualizado a cada 20 minutos — a base unificada de OpenTech +
Ravex (app/services/monitoramento_unificado.py).

Depois do embarque o WMS sabe, sim: a coluna vira "Em Viagem" e o embarque
ganha fim (painel de 09/10/2026). Carreta carregada sai da conta de "no CD"
e "a caminho" — sem isso, as 31 carretas que já tinham saído apareciam "a
caminho" do CD na manhã seguinte.

A régua de "chegou" é a mesma do painel de Abastecimento — distância até o
CD — para a mesma placa não aparecer "no CD" numa tela e "a caminho" na outra.

O HORÁRIO de chegada, não. `arrival_estimator.estimar_chegada` é o modelo do
recebimento: fornecedor que vem DESCARREGAR, dirigindo das 07h às 18h, com
almoço e janela de descarga. Usado aqui, uma carreta a 195 km às 20h12 "chegava"
às 10h15 do dia seguinte (apontado pelo usuário, 08/10/2026). A carreta da
expedição roda à noite para CARREGAR: a conta é distância ÷ velocidade, a partir
de agora.

Este módulo não conhece Flask: recebe as placas e devolve a situação.
"""
from __future__ import annotations

import math

# Até esta distância a carreta é tratada como JÁ NO CD. O rastreio oscila
# algumas centenas de metros com o veículo parado no pátio, e exigir km
# exatamente zero faria a carreta "sair e voltar" do indicador a cada
# atualização.
RAIO_PATIO_KM = 1.0

# Mesma velocidade média do estimador do recebimento, para os dois painéis
# não divergirem no tempo de estrada — o que muda é não haver janela de
# trabalho nem de descarga.
VELOCIDADE_KMH = 60

NO_CD = "no_cd"
A_CAMINHO = "a_caminho"
SEM_RASTREIO = "sem_rastreio"
CARREGADO = "carregado"


def _norm_placa(v) -> str:
    return "".join(c for c in str(v or "").upper() if c.isalnum())


def _coord(v):
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f != 0.0 else None


def _temp(v):
    """Temperatura do baú, ou None. A Ravex manda "*" e a OpenTech manda vazio
    sem leitura; com outra placa com temperatura, o pandas transforma a coluna
    em número e o vazio em NaN — e o NaN ia cru para o JSON, que o navegador
    recusa: "Não foi possível ler a operação" no outro PC (09/10/2026)."""
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(f, 1) if math.isfinite(f) else None


def posicao_das_placas(df_frota, placas) -> dict:
    """{PLACA: {latitude, longitude, cidade, temperatura, fonte}} — o que o
    rastreio sabe de cada placa pedida."""
    alvo = {_norm_placa(p) for p in placas if _norm_placa(p)}
    achadas: dict[str, dict] = {}
    if df_frota is None or getattr(df_frota, "empty", True) or not alvo:
        return achadas
    colunas = set(df_frota.columns)
    cols_placa = [c for c in ("Placa Cruzamento", "Placa Carreta", "Placa Veículo")
                  if c in colunas]
    for linha in df_frota.to_dict("records"):
        for c in cols_placa:
            placa = _norm_placa(linha.get(c))
            if placa in alvo and placa not in achadas:
                achadas[placa] = {
                    "latitude": _coord(linha.get("Latitude")),
                    "longitude": _coord(linha.get("Longitude")),
                    "cidade": str(linha.get("Cidade Atual") or "").strip(),
                    "temperatura": _temp(linha.get("Temperatura Atual")),
                    "ultima_posicao": str(linha.get("Última Posição") or "").strip(),
                    "fonte": str(linha.get("Fonte Rastreio") or "").strip(),
                }
                break
    return achadas


def situacao(instrucoes: list[dict], df_frota, cd_lat, cd_lon,
             agora=None) -> dict:
    """Situação dos veículos das instruções da noite.

    `instrucoes`: o `detalhe` do retrato — precisa de `placa`, `instrucao`,
    `falta_t` e `estado`; `sep_t`, `emb_t` e `carregado` alimentam o card
    da placa. Devolve o resumo para o indicador e a lista por veículo,
    ordenada por quem está mais perto de carregar.
    """
    import datetime as _dt
    from app.services.distance_service import calcular_km_estimado, coordenadas_validas
    agora = agora or _dt.datetime.now()

    por_placa: dict[str, dict] = {}
    for x in instrucoes or []:
        placa = _norm_placa(x.get("placa"))
        if not placa:
            continue
        d = por_placa.setdefault(placa, {
            "placa": placa, "instrucoes": [], "falta_t": 0.0, "prev_t": 0.0,
            "sep_t": 0.0, "emb_t": 0.0, "estados": set(), "itens": [],
            "carregadas": 0, "carregou_em": [],
        })
        d["instrucoes"].append(x.get("instrucao"))
        d["itens"].append({"empresa": x.get("empresa", ""), "instrucao": x.get("instrucao")})
        d["falta_t"] += float(x.get("falta_t") or 0)
        d["prev_t"] += float(x.get("prev_t") or 0)
        d["sep_t"] += float(x.get("sep_t") or 0)
        d["emb_t"] += float(x.get("emb_t") or 0)
        d["estados"].add(x.get("estado"))
        if x.get("carregado"):
            d["carregadas"] += 1
            if x.get("carregou_em"):
                d["carregou_em"].append(x["carregou_em"])

    # carreta carregada não precisa de rastreio: já saiu
    carregadas = {p for p, d in por_placa.items() if d["carregadas"] == len(d["instrucoes"])}
    posicoes = posicao_das_placas(df_frota, [p for p in por_placa if p not in carregadas])
    tem_cd = coordenadas_validas(cd_lat, cd_lon)

    itens = []
    for placa, d in por_placa.items():
        pos = posicoes.get(placa)
        km = None
        if pos and tem_cd and coordenadas_validas(pos["latitude"], pos["longitude"]):
            km = calcular_km_estimado(pos["latitude"], pos["longitude"], cd_lat, cd_lon)
        if placa in carregadas:
            hora = max(d["carregou_em"]) if d["carregou_em"] else None
            estado, chegada = CARREGADO, None
            rotulo = f"Carregou às {hora[11:16]}" if hora else "Carregado"
            km = None
        elif km is None:
            estado, rotulo, chegada = SEM_RASTREIO, "Sem rastreio", None
        elif km <= RAIO_PATIO_KM:
            estado, rotulo, chegada = NO_CD, "No CD", None
        else:
            estado = A_CAMINHO
            minutos = int(round(km / VELOCIDADE_KMH * 60))
            hora = agora + _dt.timedelta(minutes=minutos)
            h, m = divmod(minutos, 60)
            estrada = f"{h}h{m:02d}" if h else f"{m} min"
            # o dia só aparece quando a chegada vira a data
            quando = hora.strftime("%H:%M") if hora.date() == agora.date()                 else hora.strftime("%d/%m %H:%M")
            chegada = f"≈ {quando} · {estrada} de estrada"
            rotulo = f"A {km:,.0f} km".replace(",", ".")
        prev = d["prev_t"]
        itens.append({
            "placa": placa,
            "instrucoes": sorted(str(i) for i in d["instrucoes"] if i),
            "itens": d["itens"],
            "falta_t": round(d["falta_t"], 3),
            "prev_t": round(prev, 3),
            "sep_t": round(d["sep_t"], 3),
            "emb_t": round(d["emb_t"], 3),
            "pct_sep": round(min(d["sep_t"] / prev * 100, 100.0), 1) if prev > 0 else None,
            "pct_emb": round(min(d["emb_t"] / prev * 100, 100.0), 1) if prev > 0 else None,
            "estado": estado,
            "rotulo": rotulo,
            "km": round(km, 1) if km is not None else None,
            "chegada": chegada,
            "cidade": (pos or {}).get("cidade", ""),
            "temperatura": (pos or {}).get("temperatura"),
            "fonte": (pos or {}).get("fonte", ""),
            "concluida": d["estados"] == {"concluida"},
            # etapa da separação da carreta: alguma instrução em andamento,
            # todas na fila, ou tudo separado — a tela ordena por isso
            "separacao": ("andamento" if "andamento" in d["estados"]
                          else ("fila" if "fila" in d["estados"] else "concluida")),
        })

    # quem está no CD primeiro, e dentro de cada grupo quem tem mais a
    # carregar; as carregadas por último
    ordem = {NO_CD: 0, A_CAMINHO: 1, SEM_RASTREIO: 2, CARREGADO: 3}
    itens.sort(key=lambda x: (ordem.get(x["estado"], 9), -x["falta_t"], x["placa"]))

    pendentes = [x for x in itens if not x["concluida"]]
    return {
        "veiculos": itens,
        "resumo": {
            "total": len(itens),
            "no_cd": sum(1 for x in itens if x["estado"] == NO_CD),
            "a_caminho": sum(1 for x in itens if x["estado"] == A_CAMINHO),
            "sem_rastreio": sum(1 for x in itens if x["estado"] == SEM_RASTREIO),
            "carregados": sum(1 for x in itens if x["estado"] == CARREGADO),
            "t_carregado": round(sum(x["emb_t"] for x in itens if x["estado"] == CARREGADO), 3),
            # tonelada que ainda falta carregar e já tem veículo no pátio: é a
            # que a operação pode atacar agora
            "t_no_cd": round(sum(x["falta_t"] for x in pendentes
                                 if x["estado"] == NO_CD), 3),
            "t_a_caminho": round(sum(x["falta_t"] for x in pendentes
                                     if x["estado"] == A_CAMINHO), 3),
            "sem_cd_cadastrado": not tem_cd,
        },
    }
