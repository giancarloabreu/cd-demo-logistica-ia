"""
Abastecimento do picking — eleição de pallets (Expedito, 06/10/2026).

A regra, nas palavras do usuário: "se tem 9 toneladas de capa de file para
sair, ela precisa eleger 9 toneladas (de preferencia os paletes com a maior
quantidade de caixas), que atenda aos criterios de local de estoque, reserva,
produto, empresa, data de amarração (minimo e maximo) e sugerir descer essas
9 toneladas (preferencialmente pallets fechados)".

E o limite que define o desenho inteiro: "é como se ela precisasse analisar
que tem 8 pallets no estoque e esses 8 pallets todas as caixas estão elegidas
para separação, nao é eu pegar e remontar no picking, ai seria a mesma coisa
de fazer a separação".

Por isso a UNIDADE AQUI É O PALLET, não a caixa. O cálculo olha cada pallet
do estoque, mede quanto dele atende à demanda de amanhã e desce inteiro o que
passar do aproveitamento mínimo. Caixa solta só entra no pallet MISTO, que é
a exceção para produto de pouca saída na noite.

A ordem de descida respeita o FEFO: dentro da janela do pedido, o mais velho
primeiro — o WMS obriga o separador a começar por ele, então o pallet velho
precisa estar no picking ANTES do novo, sob pena de travar a separação no meio
da noite esperando empilhadeira (regra explicada pelo usuário 06/10/2026, com
o exemplo da capa de filé: 100 kg de 35 dias saem antes dos 900 kg de 10).
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict

PARAMETROS_PADRAO = {
    # quanto do pallet precisa estar elegível para descer inteiro. O usuário:
    # "o sistema vai identificar que 98% do palete pode ser separado e manda
    # descer" — abaixo disso, as caixas elegíveis viram candidatas a misto.
    "aproveitamento_min_pct": 98.0,
    # pallet misto: os dois tetos são configuráveis porque a estrutura do
    # picking muda ("coloque na parte dos parametros para eu definir 20
    # caixas, por exemplo")
    "misto_caixas_max": 15,
    "misto_codigos_max": 15,
    # Demanda até este peso vai para o pallet misto em vez de baixar pallet
    # inteiro: "itens que pediram 80 kg, 60 kg, 20 kg... podem ser agrupados"
    # (usuário, 14/10/2026). No follow up de 09/10, 15 das 74 demandas tinham
    # 20 kg ou menos. 0 desliga.
    "misto_demanda_max_kg": 100.0,
    # O TXT é a foto de hoje e a carreta sai na madrugada: a idade já é medida
    # na data do EMBARQUE, que é o dia seguinte — o "mais um dia de produção"
    # está embutido aí. Este parâmetro soma dias ALÉM disso, para o CD cujo
    # corte da madrugada cai depois (padrão 0, para não contar o dia duas
    # vezes — ele existia valendo 1 e não era aplicado em lugar nenhum).
    "dias_producao_extra": 0,
}


def _p(p: dict, k: str):
    return (p or {}).get(k, PARAMETROS_PADRAO[k])


def _data(v):
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def agrupar_demanda(linhas: list[dict]) -> list[dict]:
    """Junta as linhas do follow up por (empresa, local, temperatura, produto).

    Cada linha do follow up é um item de pedido; o abastecimento acontece por
    produto no local. A janela que vale para o grupo é a MAIS FOLGADA
    (maior limite máximo e menor mínimo): é ela que define o que pode descer.
    As janelas individuais seguem em `pedidos`, porque a alocação pallet a
    pallet precisa saber qual pedido aceita qual idade.
    """
    grupos: dict[tuple, dict] = {}
    for l in linhas:
        k = (l["empresa"], l["local"], l["temperatura"], str(l["produto"]))
        g = grupos.setdefault(k, {
            "empresa": l["empresa"], "local": l["local"], "temperatura": l["temperatura"],
            "produto": str(l["produto"]), "nome": l.get("nome", ""),
            "kg": 0.0, "caixas": 0.0, "pedidos": [],
            "limite_min": None, "limite_max": None, "reservas": set(),
            "reservas_aceitas": set(), "reservas_nome": set(),
        })
        g["kg"] += float(l.get("kg") or 0)
        g["caixas"] += float(l.get("caixas") or 0)
        g["pedidos"].append(l)
        lmin = float(l.get("limite_min") or 0)
        lmax = l.get("limite_max")
        lmax = float(lmax) if lmax not in (None, "") else None
        g["limite_min"] = lmin if g["limite_min"] is None else min(g["limite_min"], lmin)
        if lmax is not None:
            g["limite_max"] = lmax if g["limite_max"] is None else max(g["limite_max"], lmax)
        # `reservas` é o que OBRIGA (Exige Reserva = Sim); as demais entram em
        # `reservas_aceitas`, que o estoque reservado pode atender sem excluir
        # o estoque livre. Decidido pelo CÓDIGO, não pelo nome: o nome é para
        # a tela, e um follow up pode trazer o código sem o nome.
        cod = str(l.get("reserva_codigo") or "")
        if cod:
            g["reservas_aceitas"].add(cod)
            if l.get("exige_reserva"):
                g["reservas"].add(cod)
        if l.get("reserva"):
            g["reservas_nome"].add(str(l["reserva"]))
    for g in grupos.values():
        g["kg"] = round(g["kg"], 3)
        # "Volume Estimado" do follow up é FRACIONÁRIO: expressa o pedido em
        # equivalentes de caixa (12 kg de um produto cuja caixa tem 13,245 kg
        # aparece como 0,9), não em caixas inteiras. Arredondar para o mais
        # próximo em cada grupo engolia caixa — no arquivo de 06/10 a conta
        # fechava 3,6 caixas a menos que a soma das linhas. Arredonda para
        # CIMA, porque meia caixa de demanda ainda exige uma caixa no chão.
        g["caixas_equivalentes"] = round(g["caixas"], 2)
        g["caixas"] = math.ceil(g["caixas"] - 0.0001)
        g["reservas"] = sorted(g["reservas"])
        g["reservas_aceitas"] = sorted(g["reservas_aceitas"])
        g["reservas_nome"] = sorted(g["reservas_nome"])
    return sorted(grupos.values(), key=lambda g: (-g["kg"], g["produto"]))


def conferir_idade_do_arquivo(linhas_txt: list[dict], data_embarque: _dt.date,
                              hoje: _dt.date | None = None, tolerancia_dias: int = 10) -> dict:
    """O TXT parece ser do dia? (achado ao vivo, 06/10/2026)

    Rodando um TXT antigo contra um follow up atual, 33 itens e 11,4 t
    apareceram como "fora da janela do pedido" — a idade de cada caixa é
    medida contra a data de embarque, então um arquivo de dias atrás
    envelhece o estoque inteiro e derruba item que na verdade está bom.
    O erro é silencioso: o plano sai, só sai errado.

    O TXT não carrega a data de exportação, então o sinal é a produção MAIS
    RECENTE do arquivo. A tolerância é de 10 dias, calibrada em arquivos
    reais: o TXT de 07/10 trazia produção mais nova de 02/10 (5 dias, e era
    o arquivo do dia), enquanto o arquivo velho trazia 28/08 — 39 dias. Com
    tolerância de 2 dias o aviso disparava no arquivo certo, e aviso que
    grita à toa é aviso que ninguém lê. Heurística, e a tela diz que é.
    """
    hoje = hoje or data_embarque - _dt.timedelta(days=1)
    producoes = [c for c in (r.get("producao") for r in linhas_txt) if c]
    if not producoes:
        return {"suspeito": False, "producao_mais_recente": None}
    recente = max(_data(d) for d in producoes)
    dias = (hoje - recente).days
    return {
        "suspeito": dias > tolerancia_dias,
        "producao_mais_recente": recente.isoformat(),
        "dias_desde_a_producao_mais_recente": dias,
        "aviso": (f"A produção mais recente do estoque é de {recente.strftime('%d/%m')}, "
                  f"{dias} dias atrás. Se o TXT não for o de hoje, as idades saem maiores "
                  f"e itens bons aparecem fora da janela do pedido."
                  if dias > tolerancia_dias else ""),
    }


def montar_pallets(linhas_txt: list[dict]) -> list[dict]:
    """Agrupa as linhas do TXT por PALETE — uma linha do TXT é uma caixa.

    Pallet sem identificação de palete não é unidade de movimentação: cada
    caixa dessas entra solta, candidata a misto.
    """
    por_palete: dict[tuple, dict] = {}
    soltas = []
    for r in linhas_txt:
        palete = str(r.get("palete") or "").strip()
        base = {
            "local": r.get("local", ""), "temperatura": r.get("temperatura", ""),
            "codigo": str(r.get("codigo") or ""), "nome": r.get("nome", ""),
            "peso_liquido": float(r.get("peso_liquido") or 0),
            "producao": _data(r.get("producao")), "validade": _data(r.get("validade")),
            # código, para casar com o follow up (ver _elegivel): a sigla
            # não é chave única no TXT
            "reserva": str(r.get("reserva_codigo") or "").strip(),
            "reserva_nome": str(r.get("reserva_nome") or "").strip(),
            "bloqueio": str(r.get("bloqueio_motivos") or "").strip(),
            "enderecado": bool(r.get("endereçado", r.get("enderecado", True))),
            "camara": r.get("camara", ""), "rua": r.get("rua", ""), "box": r.get("box", ""),
            "altura": r.get("altura", ""), "profundidade": r.get("profundidade", ""),
            "no_picking": bool(r.get("no_picking")),
        }
        if not palete:
            soltas.append({**base, "palete": "", "caixas": 1})
            continue
        k = (base["local"], palete)
        p = por_palete.setdefault(k, {
            "palete": palete, "local": base["local"], "temperatura": base["temperatura"],
            "camara": base["camara"], "rua": base["rua"], "box": base["box"],
            "altura": base["altura"], "profundidade": base["profundidade"],
            "enderecado": base["enderecado"], "no_picking": base["no_picking"],
            "caixas": [],
        })
        # o número do pallet vai na caixa: a lista do misto diz de onde tirar
        p["caixas"].append({**base, "palete": palete})
    pallets = []
    for p in por_palete.values():
        cx = p["caixas"]
        p["total_caixas"] = len(cx)
        p["peso_kg"] = round(sum(c["peso_liquido"] for c in cx), 3)
        p["codigos"] = sorted({c["codigo"] for c in cx})
        p["producao_mais_velha"] = min((c["producao"] for c in cx if c["producao"]), default=None)
        pallets.append(p)
    return pallets, soltas


def _elegivel(caixa: dict, grupo: dict, data_embarque: _dt.date, p: dict) -> bool:
    """A caixa atende àquela demanda? Local, produto, reserva, bloqueio e
    idade dentro da janela do pedido — a idade medida na data do EMBARQUE."""
    if caixa["codigo"] != grupo["produto"]:
        return False
    if caixa["local"] != grupo["local"] or caixa["temperatura"] != grupo["temperatura"]:
        return False
    if caixa["bloqueio"]:
        return False
    # Reserva casa nos DOIS sentidos (usuário, 06/10/2026: "se o pedido tiver
    # reserva, o produto tem que ter reserva"): pedido reservado é atendido
    # exclusivamente por produto da mesma reserva, e produto reservado não
    # entra no pool dos pedidos livres.
    if grupo["reservas"]:
        # reserva obrigatória: atende exclusivamente quem tem a mesma
        if caixa["reserva"] not in grupo["reservas"]:
            return False
    elif caixa["reserva"]:
        # estoque reservado entra no pedido que carrega aquela reserva,
        # mesmo quando ela não obriga; nunca no pedido de outra reserva
        if caixa["reserva"] not in grupo.get("reservas_aceitas", []):
            return False
    if caixa["producao"] is None:
        return False
    idade = (data_embarque - caixa["producao"]).days + int(_p(p, "dias_producao_extra"))
    if idade < (grupo["limite_min"] or 0):
        return False
    if grupo["limite_max"] is not None and idade > grupo["limite_max"]:
        return False
    return True


def _diagnosticar_falta(g: dict, pallets: list[dict], data_embarque, p: dict,
                        teve_candidato: bool) -> dict:
    """Por que faltou — com o que existe no estoque, não um "não deu".

    Rodando os arquivos reais de 06/10 apareceram três situações com a MESMA
    mensagem: item sem nada no armazém, item com estoque fora da janela do
    cliente, e item cujo estoque está em OUTRO local do mesmo CD. As três
    pedem ação diferente, e a terceira é transferência entre locais — por
    isso cada uma ganhou nome próprio.
    """
    extra = int(_p(p, "dias_producao_extra"))
    mesmo_codigo = [c for pal in pallets for c in pal["caixas"] if c["codigo"] == g["produto"]]
    if not mesmo_codigo:
        return {"motivo": "sem estoque deste item no armazém", "causa": "sem_estoque"}

    no_local = [c for c in mesmo_codigo
                if c["local"] == g["local"] and c["temperatura"] == g["temperatura"]]
    if not no_local:
        outros = sorted({c["local"] for c in mesmo_codigo})
        return {"motivo": f"estoque está em outro local: {', '.join(outros)}",
                "causa": "outro_local", "locais_com_estoque": outros}

    livres = [c for c in no_local if not c["bloqueio"]]
    if not livres:
        return {"motivo": f"as {len(no_local)} caixa(s) do local estão bloqueadas",
                "causa": "bloqueado"}

    idades = sorted({(data_embarque - c["producao"]).days + extra
                     for c in livres if c["producao"]})
    if idades:
        dentro = [i for i in idades
                  if i >= (g["limite_min"] or 0)
                  and (g["limite_max"] is None or i <= g["limite_max"])]
        if not dentro:
            faixa = f"{idades[0]} a {idades[-1]}" if len(idades) > 1 else str(idades[0])
            janela = (f"{g['limite_min']:.0f} a {g['limite_max']:.0f}"
                      if g["limite_max"] is not None else f"a partir de {g['limite_min']:.0f}")
            return {"motivo": f"estoque com {faixa} dias de produção, fora da janela do pedido "
                              f"({janela} dias)",
                    "causa": "fora_da_janela", "idades_no_local": idades[:12]}

    reservados = [c for c in livres if c["reserva"]]
    if reservados and not g["reservas"]:
        return {"motivo": f"as caixas do local estão reservadas "
                          f"({', '.join(sorted({c['reserva'] for c in reservados}))})",
                "causa": "reservado"}
    return {"motivo": ("estoque elegível menor que a demanda da noite" if teve_candidato
                       else "sem pallet elegível no estoque"),
            "causa": "insuficiente"}


def endereco(x: dict) -> str:
    """Endereço do pallet no formato do CD, câmara-box-altura: "C5-B06-1"
    (usuário, 15/10/2026 — o plano não mostrava a altura do pallet a baixar).
    O box do Produto Paletizado já traz câmara e rua ("C5-B06"); sem box, vale
    o que houver de câmara e rua."""
    box = str(x.get("box") or "").strip()
    base = box or f"{x.get('camara', '')}-{x.get('rua', '')}".strip("-")
    altura = str(x.get("altura") or "").strip()
    return f"{base}-{altura}" if base and altura else base


def chave_box(box) -> str:
    """Box comparável entre o TXT e o que o analista digita ("c5-f28 ")."""
    return "".join(str(box or "").upper().split())


def _em_posicao_de_misto(pal: dict, boxes_misto: set) -> bool:
    return bool(boxes_misto) and chave_box(pal.get("box")) in boxes_misto


def _no_chao(pal: dict, posicoes_picking: set) -> bool:
    """O pallet já está em face de picking?

    Produto que já está no chão não desce de novo — o que o painel precisa
    dizer é quais pallets PERMANECEM lá (usuário, 06/10/2026).

    Duas fontes, nesta ordem. A coluna "Tipo" do Produto Paletizado já
    responde isso sozinha: "2-Picking" é face de picking, "1-Estocagem" é
    estoque alto (achado ao confrontar o painel de Abastecimento real,
    08/10/2026 — antes isso dependia do cadastro de posições e, sem cadastro,
    NENHUM pallet era reconhecido no chão e a lista "permanece" saía vazia).
    O cadastro de posições continua valendo como complemento, para CD que
    enderece picking fora dessa marcação.
    """
    if pal.get("no_picking"):
        return True
    if not posicoes_picking:
        return False
    chave = (str(pal.get("box", "")).upper(), str(pal.get("altura", "")),
             str(pal.get("profundidade", "")))
    return chave in posicoes_picking


def eleger(demanda: list[dict], pallets: list[dict], soltas: list[dict],
           data_embarque: _dt.date, p: dict | None = None,
           posicoes_picking: set | None = None,
           boxes_misto: set | None = None) -> dict:
    """O plano de descida: quais pallets, nessa ordem, e o que sobra.

    Para cada demanda, mede cada pallet (quantas caixas dele servem), elege
    os que passam do aproveitamento mínimo preferindo o MAIS VELHO e, entre
    idades iguais, o de MAIOR número de caixas — "de preferencia os paletes
    com a maior quantidade de caixas". O que a demanda ainda pedir depois
    dos pallets inteiros vira pallet misto.
    """
    p = p or {}
    minimo = float(_p(p, "aproveitamento_min_pct"))
    cx_max, cod_max = int(_p(p, "misto_caixas_max")), int(_p(p, "misto_codigos_max"))
    limite_misto = float(_p(p, "misto_demanda_max_kg") or 0)

    posicoes_picking = posicoes_picking or set()
    # Posição de pallet MISTO no picking: o pallet que está lá carrega vários
    # códigos e não "permanece no chão" atendendo a demanda de um deles, nem
    # desce (usuário, 14/10/2026: "na sugestão de manter no chão, ele pega
    # pallets mistos e de picking"). Sai do plano inteiro.
    boxes_misto = {chave_box(b) for b in (boxes_misto or set()) if chave_box(b)}
    ignorados_misto = {(pal["local"], pal["palete"]) for pal in pallets
                       if _em_posicao_de_misto(pal, boxes_misto)}
    pallets = [pal for pal in pallets if (pal["local"], pal["palete"]) not in ignorados_misto]
    # empresa de cada local, pela demanda (CDA e CDB têm TXT próprio, e o
    # local de estoque começa pela sigla): é o que o filtro da tela usa
    empresa_do_local = {g["local"]: g.get("empresa", "") for g in demanda}

    def empresa_de(local):
        if local in empresa_do_local:
            return empresa_do_local[local]
        return str(local or "").split(" ")[0].upper()
    # A identidade do pallet é (local, palete), igual ao agrupamento de
    # montar_pallets. Guardar só o número fazia o MESMO número em outro local
    # ser tratado como já usado: no TXT do CDA de 08/10/2026, 40 números
    # aparecem em mais de um local (o palete 3 em três), e cada um deles era
    # descartado em silêncio, virando falta de estoque falsa (achado na
    # revisão pré-deploy).
    usados: set[tuple] = set()
    descer, faltas, sobras_misto, itens, permanecer = [], [], [], [], []

    for g in demanda:
        falta_kg, falta_cx = g["kg"], g["caixas"]
        candidatos = []
        for pal in pallets:
            if (pal["local"], pal["palete"]) in usados:
                continue
            elegiveis = [c for c in pal["caixas"] if _elegivel(c, g, data_embarque, p)]
            if not elegiveis:
                continue
            aproveitamento = len(elegiveis) / pal["total_caixas"] * 100
            candidatos.append({
                "pallet": pal, "elegiveis": elegiveis, "aproveitamento": round(aproveitamento, 1),
                "kg": round(sum(c["peso_liquido"] for c in elegiveis), 3),
                "producao": min((c["producao"] for c in elegiveis), default=None),
            })
        # FEFO manda: mais velho primeiro. Empate de idade decide pelo pallet
        # mais cheio, que é o que o usuário pediu e o que rende menos viagem.
        candidatos.sort(key=lambda c: (c["producao"] or _dt.date.max, -len(c["elegiveis"])))

        # O que JÁ está no chão abate a demanda antes de qualquer descida, e
        # entra na lista de "permanecer" para ninguém mandar descer de novo.
        for c in list(candidatos):
            if not _no_chao(c["pallet"], posicoes_picking):
                continue
            candidatos.remove(c)
            pal = c["pallet"]
            usados.add((pal["local"], pal["palete"]))
            permanecer.append({
                "empresa": g.get("empresa", ""),
                "palete": pal["palete"], "local": pal["local"], "temperatura": pal["temperatura"],
                "produto": g["produto"], "nome": g["nome"], "caixas": len(c["elegiveis"]),
                "kg": c["kg"], "aproveitamento": c["aproveitamento"],
                "endereco": endereco(pal),
                "motivo": "já está em posição de picking e atende à demanda — permanece no chão",
            })
            falta_kg -= c["kg"]
            falta_cx -= len(c["elegiveis"])

        # Demanda pequena não baixa pallet inteiro: as caixas que ela pede,
        # na ordem do FEFO, vão para o pallet misto junto com outras demandas
        # pequenas do mesmo local e temperatura.
        # O misto comporta até `misto_caixas_max` caixas (15): demanda que
        # não cabe nele baixa pallet, mesmo leve (usuário, 14/10/2026).
        pequena = 0.001 < falta_kg <= limite_misto and falta_cx <= cx_max
        if pequena:
            # No misto a escolha é CAIXA a caixa, não pallet a pallet: a mais
            # velha que ainda cabe na janela de idade do pedido, venha de que
            # pallet vier (usuário, 14/10/2026: "sugerir inclusive a data mais
            # velha dentro das amarrações do pedido"). Empate de produção vai
            # pela validade mais curta.
            caixas_ord = sorted((cx for c in candidatos for cx in c["elegiveis"]),
                                key=lambda cx: (cx["producao"] or _dt.date.max,
                                                cx.get("validade") or _dt.date.max))
            escolhidas, kg_esc = [], 0.0
            for cx in caixas_ord:
                if kg_esc >= falta_kg - 0.001:
                    break
                escolhidas.append({**cx, "janela_pedido": (g["limite_min"], g["limite_max"])})
                kg_esc += cx["peso_liquido"]
            if escolhidas:
                sobras_misto.append({"grupo": g, "pallet": None, "caixas": escolhidas,
                                     "aproveitamento": None, "pequena": True})
                falta_kg -= round(kg_esc, 3)
                falta_cx -= len(escolhidas)
        for c in ([] if pequena else candidatos):
            if falta_kg <= 0.001:
                break
            pal = c["pallet"]
            if c["aproveitamento"] >= minimo and not pequena:
                usados.add((pal["local"], pal["palete"]))
                descer.append({
                    "empresa": g.get("empresa", ""),
                    "tipo": "inteiro", "palete": pal["palete"], "local": pal["local"],
                    "temperatura": pal["temperatura"], "produto": g["produto"], "nome": g["nome"],
                    "caixas": len(c["elegiveis"]), "kg": c["kg"],
                    "aproveitamento": c["aproveitamento"],
                    "producao": c["producao"].isoformat() if c["producao"] else None,
                    "idade_no_embarque": (data_embarque - c["producao"]).days if c["producao"] else None,
                    "camara": pal["camara"], "rua": pal["rua"], "box": pal["box"],
                    "altura": pal["altura"], "profundidade": pal.get("profundidade"),
                    "endereco": endereco(pal), "enderecado": pal["enderecado"],
                    "motivo": (f"{c['aproveitamento']:.0f}% do pallet atende à demanda"
                               + (f" · produzido em {c['producao'].strftime('%d/%m')}, "
                                  f"{(data_embarque - c['producao']).days} dias no embarque"
                                  if c["producao"] else "")
                               + (" · pallet sem endereço, recebido recente" if not pal["enderecado"] else "")),
                })
                falta_kg -= c["kg"]
                falta_cx -= len(c["elegiveis"])
            else:
                # Pallet pouco aproveitado: as caixas elegíveis viram
                # candidatas a misto em vez de descer um pallet quase todo
                # inútil.
                #
                # Entram SÓ as caixas que a demanda ainda pede. Este ramo não
                # descontava a falta, então o laço nunca chegava ao `break`:
                # todo pallet de baixo aproveitamento despejava todas as
                # caixas elegíveis no misto, e um pedido de 1.000 kg descia
                # 5.000 kg (achado ao conferir o abatimento do misto,
                # 08/10/2026). As caixas já vêm na ordem FEFO.
                escolhidas, kg_esc = [], 0.0
                for cx in c["elegiveis"]:
                    if kg_esc >= falta_kg - 0.001:
                        break
                    escolhidas.append(cx)
                    kg_esc += cx["peso_liquido"]
                if not escolhidas:
                    continue
                sobras_misto.append({"grupo": g, "pallet": pal, "caixas": escolhidas,
                                     "aproveitamento": c["aproveitamento"],
                                     "pequena": pequena})
                falta_kg -= round(kg_esc, 3)
                falta_cx -= len(escolhidas)

        # O operador decide descer mais ou menos: a tela mostra TODOS os
        # candidatos com o aproveitamento de cada um, e ele pode trocar um
        # pallet cheio por dois de 50% (usuário, 06/10/2026).
        itens.append({
            # pedidos e clientes da demanda: a tela acha o item pelo número do
            # pedido que o analista tem em mãos (usuário, 14/10/2026 — os
            # pedidos do follow "não apareciam" porque a busca não os via)
            "pedidos": sorted({str(l.get("pedido") or "") for l in g.get("pedidos", [])} - {""}),
            "clientes": sorted({str(l.get("cliente") or "") for l in g.get("pedidos", [])} - {""})[:40],
            "misto_por_demanda": pequena,
            "produto": g["produto"], "nome": g["nome"], "local": g["local"],
            "temperatura": g["temperatura"], "empresa": g["empresa"],
            "kg_demanda": g["kg"], "caixas_demanda": g["caixas"],
            "limite_min": g["limite_min"], "limite_max": g["limite_max"],
            "reservas": g["reservas"],
            "kg_no_chao": round(sum(x["kg"] for x in permanecer
                                    if x["produto"] == g["produto"] and x["local"] == g["local"]), 3),
            "kg_sugerido": round(g["kg"] - max(falta_kg, 0), 3),
            "kg_falta": round(max(falta_kg, 0), 3),
            "cobertura_pct": round((g["kg"] - max(falta_kg, 0)) / g["kg"] * 100, 1) if g["kg"] else None,
            "candidatos": [{
                "palete": c["pallet"]["palete"],
                "caixas": len(c["elegiveis"]),
                "caixas_no_pallet": c["pallet"]["total_caixas"],
                "kg": c["kg"],
                "aproveitamento": c["aproveitamento"],
                "producao": c["producao"].isoformat() if c["producao"] else None,
                "idade_no_embarque": (data_embarque - c["producao"]).days if c["producao"] else None,
                "endereco": endereco(c["pallet"]),
                "sugerido": (c["pallet"]["local"], c["pallet"]["palete"]) in usados,
                "inteiro": c["aproveitamento"] >= minimo,
            } for c in candidatos],
        })

        if falta_kg > 0.001:
            faltas.append({
                "empresa": g.get("empresa", ""),
                "produto": g["produto"], "nome": g["nome"], "local": g["local"],
                "temperatura": g["temperatura"], "kg_faltando": round(falta_kg, 3),
                "caixas_faltando": max(int(falta_cx), 0),
                **_diagnosticar_falta(g, pallets, data_embarque, p, bool(candidatos)),
            })

    # Caixa solta (linha de TXT sem palete) ia direto para o misto SEM passar
    # por _elegivel: caixa bloqueada, reservada para outro pedido, fora da
    # janela de idade ou de produto que nem tem saída amanhã podia descer para
    # a face de picking (achado na revisão pré-deploy, 08/10/2026). As sobras
    # de pallet já vinham filtradas; as soltas, não.
    soltas_elegiveis = [c for c in (soltas or [])
                        if any(_elegivel(c, g, data_embarque, p) for g in demanda)]
    mistos = _montar_mistos(sobras_misto, soltas_elegiveis, cx_max, cod_max, data_embarque)
    for m in mistos:
        m["empresa"] = empresa_de(m["local"])

    # A falta e a cobertura eram calculadas no laço acima, ANTES de os mistos
    # existirem: demanda atendida por pallet misto continuava aparecendo como
    # falta de estoque, e a barra de cobertura mostrava menos do que realmente
    # desce (achado na revisão pré-deploy, 08/10/2026). Abate aqui, pelo
    # (local, temperatura, código) de cada componente.
    kg_misto: dict[tuple, float] = {}
    for m in mistos:
        for cmp in m["componentes"]:
            k = (m["local"], m["temperatura"], cmp["codigo"])
            kg_misto[k] = kg_misto.get(k, 0.0) + (cmp["peso_kg"] or 0.0)
    if kg_misto:
        for it in itens:
            k = (it["local"], it["temperatura"], it["produto"])
            abate = kg_misto.get(k, 0.0)
            if not abate or not it["kg_falta"]:
                continue
            it["kg_falta"] = round(max(it["kg_falta"] - abate, 0.0), 3)
            it["kg_sugerido"] = round(it["kg_demanda"] - it["kg_falta"], 3)
            it["cobertura_pct"] = (round((it["kg_demanda"] - it["kg_falta"])
                                         / it["kg_demanda"] * 100, 1)
                                   if it["kg_demanda"] else None)
        restantes = []
        for f in faltas:
            k = (f["local"], f["temperatura"], f["produto"])
            sobra = round(f["kg_faltando"] - kg_misto.get(k, 0.0), 3)
            if sobra > 0.001:
                f["kg_faltando"] = sobra
                restantes.append(f)
        faltas = restantes
    descer.extend(mistos)

    return {
        "data_embarque": data_embarque.isoformat(),
        "itens": itens,
        "descer": descer,
        "permanecer": permanecer,
        "faltas": faltas,
        "resumo": {
            "permanecem_no_chao": len(permanecer),
            "pallets_inteiros": sum(1 for d in descer if d["tipo"] == "inteiro"),
            "pallets_mistos": sum(1 for d in descer if d["tipo"] == "misto"),
            "movimentacoes": len(descer),
            "kg_total": round(sum(d["kg"] for d in descer), 3),
            "caixas_total": sum(d["caixas"] for d in descer),
            "demandas": len(demanda),
            "faltas": len(faltas),
            "demandas_em_misto": sum(1 for it in itens if it["misto_por_demanda"]),
            "limite_misto_kg": limite_misto,
            "pallets_em_posicao_misto": len(ignorados_misto),
        },
    }


def _montar_mistos(sobras, soltas, cx_max: int, cod_max: int, data_embarque) -> list[dict]:
    """Junta caixa solta e sobra de pallet pouco aproveitado em pallets mistos.

    Restrições duras: mesmo local e mesma temperatura (câmara diferente não se
    mistura fisicamente). Tetos configuráveis: caixas e códigos distintos.
    """
    por_destino: dict[tuple, list] = defaultdict(list)
    for s in sobras:
        for c in s["caixas"]:
            por_destino[(c["local"], c["temperatura"])].append(c)
    for c in soltas:
        por_destino[(c["local"], c["temperatura"])].append(c)

    mistos = []
    for (local, temp), caixas in sorted(por_destino.items()):
        # Cada item fica INTEIRO num pallet só, sempre que couber: o operador
        # monta o misto item a item, e um código partido em dois pallets vira
        # duas idas ao mesmo endereço. Item maior que o pallet é dividido em
        # blocos de `cx_max`. Os blocos entram do maior para o menor no
        # primeiro pallet com espaço (caixas e códigos) — o que fecha menos
        # pallets com até 15 caixas cada.
        caixas.sort(key=lambda c: (c["producao"] or _dt.date.max, c["codigo"]))
        por_codigo: dict[str, list] = defaultdict(list)
        for c in caixas:
            por_codigo[c["codigo"]].append(c)
        blocos = []
        for cod, lst in por_codigo.items():
            for i in range(0, len(lst), max(cx_max, 1)):
                blocos.append(lst[i:i + max(cx_max, 1)])
        # maior primeiro; empate pelo mais velho, para o FEFO pesar na ordem
        blocos.sort(key=lambda b: (-len(b), b[0]["producao"] or _dt.date.max, b[0]["codigo"]))
        pallets_m: list[list] = []
        for b in blocos:
            for pm in pallets_m:
                cods = {c["codigo"] for c in pm}
                if len(pm) + len(b) <= cx_max and (b[0]["codigo"] in cods or len(cods) < cod_max):
                    pm.extend(b)
                    break
            else:
                pallets_m.append(list(b))
        # o pallet com o produto mais velho desce primeiro
        for pm in sorted(pallets_m, key=lambda pm: min((c["producao"] or _dt.date.max) for c in pm)):
            pm.sort(key=lambda c: (c["producao"] or _dt.date.max, c["codigo"]))
            mistos.append(_fechar_misto(pm, local, temp, data_embarque))
    return mistos


def _fechar_misto(caixas: list[dict], local: str, temp: str, data_embarque) -> dict:
    codigos = sorted({c["codigo"] for c in caixas})
    producao = min((c["producao"] for c in caixas if c["producao"]), default=None)
    return {
        "tipo": "misto", "palete": "", "local": local, "temperatura": temp,
        "produto": " / ".join(codigos[:3]) + (" …" if len(codigos) > 3 else ""),
        "nome": f"{len(codigos)} código(s)", "caixas": len(caixas),
        "kg": round(sum(c["peso_liquido"] for c in caixas), 3),
        "aproveitamento": 100.0,
        "producao": producao.isoformat() if producao else None,
        "idade_no_embarque": (data_embarque - producao).days if producao else None,
        "componentes": [{"codigo": c["codigo"], "nome": c["nome"],
                         "peso_kg": round(c["peso_liquido"], 3),
                         "producao": c["producao"].isoformat() if c["producao"] else None,
                         "origem": endereco(c)}
                        for c in caixas],
        "motivo": f"{len(caixas)} caixa(s) de {len(codigos)} código(s) de demanda pequena na noite",
        # o mesmo código aparece uma vez por caixa em `componentes`; a lista
        # de separação do operador quer o total por código
        "itens": _itens_do_misto(caixas, data_embarque),
    }


def _itens_do_misto(caixas: list[dict], data_embarque=None) -> list[dict]:
    por_cod: dict[str, dict] = {}
    for c in caixas:
        it = por_cod.setdefault(c["codigo"], {
            "codigo": c["codigo"], "nome": c["nome"], "caixas": 0, "kg": 0.0,
            "origens": [], "producao": None})
        it["caixas"] += 1
        it["kg"] = round(it["kg"] + c["peso_liquido"], 3)
        origem = endereco(c)
        palete = str(c.get("palete") or "")
        rotulo = f"{palete} ({origem})" if palete and origem else (palete or origem)
        if rotulo and rotulo not in it["origens"]:
            it["origens"].append(rotulo)
        prod = c["producao"].isoformat() if c["producao"] else None
        if prod and (it["producao"] is None or prod < it["producao"]):
            it["producao"] = prod
            it["idade_no_embarque"] = ((data_embarque - c["producao"]).days
                                       if data_embarque and c["producao"] else None)
        if c.get("janela_pedido"):
            lmin, lmax = c["janela_pedido"]
            it["janela_pedido"] = {"min_dias": lmin, "max_dias": lmax}
    return sorted(por_cod.values(), key=lambda x: (-x["kg"], x["codigo"]))
