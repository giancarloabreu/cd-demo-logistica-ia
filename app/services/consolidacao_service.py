import pandas as pd

from app.database import (get_cd, get_cd_usuario, get_cds_localizacao,
                          get_todas_validacoes, get_mapa_consolidacao)
from app.services.distance_service import calcular_km_estimado
from app.utils.number_utils import format_int_br
from app.services.arrival_estimator import aplicar_estimativas, _load_schedule


def _first_non_empty(series):
    for value in series:
        if value is not None and str(value).strip() != "" and str(value).strip().lower() != "nan":
            return value
    return ""


def _buscar_instrucao(row, controle_df):
    """Todas as instruções de entrada do card, separadas por " / ".

    Cruza por **Pedido + Carga** (`Empresa Compra` no faturado ↔ `Pedido
    Comercial` no controle). Antes cruzava por NF e por pedido, mas as duas
    resolviam coluna errada e nunca casaram nada — só o fallback de placa
    rodava (achado 04/09/2026, com os arquivos reais).

    A placa segue como último recurso, e é um recurso RUIM: uma carreta pode
    entregar em vários CDs na mesma viagem e o filtro por placa varre o
    controle inteiro sem separar CD, colando no card do CDA a instrução do CDB.
    Com Pedido+Carga isso não acontece — cada par aponta para exatamente uma
    instrução nos dados reais.
    """
    if controle_df is None or controle_df.empty or "Instrução Entrada" not in controle_df.columns:
        return ""

    achadas = []

    def _coletar(series):
        for value in series:
            s = str(value).strip()
            if s and s.lower() != "nan" and s not in achadas:
                achadas.append(s)

    # 1. Por Pedido + Carga — a chave do recebimento
    chaves = [tuple(par.split("~", 1)) for par in str(row.get("Chaves", "")).split("|")
              if "~" in par]
    if chaves and {"Pedido Compra", "Carga"} <= set(controle_df.columns):
        ped = controle_df["Pedido Compra"].astype(str).str.strip()
        car = controle_df["Carga"].astype(str).str.strip()
        for p, c in chaves:
            found = controle_df[(ped == p.strip()) & (car == c.strip())]
            if not found.empty:
                _coletar(found["Instrução Entrada"])

    # 2. Fallback por placa — só se o pedido não resolveu nada
    placa = str(row.get("Placa", "")).strip()
    if not achadas and placa:
        for col in ["Placa Cruzamento", "Placa"]:
            if col in controle_df.columns:
                found = controle_df[controle_df[col].astype(str).str.strip() == placa]
                if not found.empty:
                    _coletar(found["Instrução Entrada"])

    return " / ".join(achadas)


def consolidar(estoque_itens_df, controle_df=None, monitoramento_df=None, usuario="") -> pd.DataFrame:
    cd_usuario = get_cd_usuario(usuario) if usuario else get_cd()

    if estoque_itens_df is None or estoque_itens_df.empty:
        return pd.DataFrame()

    # Cria coluna NF:Pedido para preservar mapeamento após groupby
    estoque_itens_df["_NF_Ped"] = estoque_itens_df.apply(
        lambda r: f"{str(r.get('NF','')).strip()}:{str(r.get('Pedido Compra','')).strip()}", axis=1
    )
    # par Pedido~Carga por linha — o groupby agrega várias linhas num card e
    # uma carreta costuma levar mais de um pedido (6 dos 10 cards nos arquivos
    # reais), então a chave precisa sobreviver ao agrupamento
    estoque_itens_df["_Chave"] = estoque_itens_df.apply(
        lambda r: f"{str(r.get('Pedido Compra','')).strip()}~{str(r.get('Carga','')).strip()}", axis=1
    )

    # ------------------------------------------------------------------
    # Identidade do card (regras do usuário, 04/09/2026)
    #
    # A carreta faz UMA viagem por dia (carregar/descarregar é moroso), e uma
    # mesma carreta pode entregar em vários CDs na mesma viagem. O card é a
    # CARRETA FÍSICA, não a dupla carreta+CD: se ela leva CDA e CDB, é um card
    # só identificando as duas unidades — antes viravam dois cards.
    #
    # Sem placa (compra de produto de terceiro, compra de devolução, e outras
    # situações — metade das linhas do controle nos arquivos reais): a placa
    # não pode agrupar, senão todas as entregas sem placa colapsam num card só.
    # Aí a identidade é a INSTRUÇÃO: dois pedidos sem placa com a mesma
    # instrução são um recebimento; com instruções diferentes, são dois.
    inst_por_chave = {}
    if controle_df is not None and not controle_df.empty             and {"Pedido Compra", "Carga", "Instrução Entrada"} <= set(controle_df.columns):
        for _, r in controle_df.iterrows():
            i = str(r["Instrução Entrada"]).strip()
            if i and i.lower() != "nan":
                inst_por_chave.setdefault(
                    (str(r["Pedido Compra"]).strip(), str(r["Carga"]).strip()), i)

    # Só junta unidades no mesmo card quando elas são a mesma EMPRESA
    # CONSOLIDADA, ou seja, o mesmo local físico — CDA e CDB dividem o
    # endereço, então a carreta descarrega ali e é um card só; CNT fica em
    # outro lugar e não pode se misturar. Ver `empresa_consolidada` no
    # database.py. Empresa não cadastrada resolve para ela mesma.
    _mapa = get_mapa_consolidacao()

    def _conta_de(sigla) -> str:
        s = str(sigla).strip().upper()
        return _mapa.get(s, s) if s else ""

    def _card_de(r):
        placa = str(r.get("Placa", "")).strip()
        if placa:
            return f"{placa}|{_conta_de(r.get('Empresa Compra', ''))}"
        chave = (str(r.get("Pedido Compra", "")).strip(), str(r.get("Carga", "")).strip())
        inst = inst_por_chave.get(chave, "")
        return f"SEM-PLACA:{inst}" if inst else f"SEM-PLACA:{chave[0]}~{chave[1]}"

    estoque_itens_df["_Card"] = estoque_itens_df.apply(_card_de, axis=1)

    def _juntar(sep):
        return lambda s: sep.join(sorted(set(
            str(x) for x in s if str(x).strip() and str(x).strip().lower() != "nan")))

    consolidado = estoque_itens_df.groupby(["_Card"], dropna=False).agg(
        # identidade do card: a tela edita por ela (fase 3, 19/10/2026)
        **{"Card ID": ("_Card", "first")},
        Empresa=("Empresa", _first_non_empty),
        Placa=("Placa", _first_non_empty),
        **{
            # várias unidades no mesmo card quando a carreta entrega em mais de um CD
            "Empresa Compra": ("Empresa Compra", _juntar(" · ")),
            "Data Faturamento": ("Data Faturamento", _first_non_empty),
            "Pedido Compra": ("Pedido Compra", _first_non_empty),
            "NF Pedidos": ("_NF_Ped", lambda s: "|".join(dict.fromkeys(x for x in s if x and ":" in x))),
            "Chaves": ("_Chave", lambda s: "|".join(dict.fromkeys(x for x in s if x and "~" in x))),
            "Transportadora": ("Transportadora", _first_non_empty),
            "NF": ("NF", lambda s: "/".join(sorted(set(str(x) for x in s if str(x).strip() and str(x).lower() != "nan")))),
            "Local Estoque": ("Local Estoque", lambda s: " · ".join(sorted(set(str(x) for x in s if str(x).strip() and str(x).lower() != "nan")))),
            "Temperatura": ("Temperatura", lambda s: " / ".join(sorted(set(str(x) for x in s if str(x).strip() and str(x).lower() != "nan")))),
            "Unidade": ("Unidade", lambda s: " / ".join(sorted(set(str(x) for x in s if str(x).strip() and str(x).lower() != "nan")))),
            "Quantidade": ("Quantidade", "sum"),
            "Volume": ("Volume", "sum"),
        }
    ).reset_index(drop=True)

    consolidado["Instrução Entrada"] = consolidado.apply(lambda row: _buscar_instrucao(row, controle_df), axis=1)

    if monitoramento_df is not None and not monitoramento_df.empty:
        consolidado = consolidado.merge(
            monitoramento_df,
            left_on="Placa",
            right_on="Placa Cruzamento",
            how="left"
        )
    else:
        consolidado["Última Posição"] = ""
        consolidado["Cidade Atual"] = ""
        consolidado["Latitude"] = ""
        consolidado["Longitude"] = ""
        consolidado["Tipo Cruzamento"] = ""
        consolidado["Temperatura Atual"] = None
        consolidado["Ignição"] = ""
        consolidado["Situação Veículo"] = ""
        consolidado["Fonte Rastreio"] = ""

    # Distância real por CD de destino (Empresa Compra) — fallback para CD do usuário
    # Carrega todas as localizações de CDs de uma vez (evita 1 query por linha)
    cds_loc = {cd["sigla"].strip().upper(): cd for cd in get_cds_localizacao()}

    km_calculado = []
    for _, row in consolidado.iterrows():
        # o card pode ter mais de um CD ("CDA · CDB"); o KM que interessa a
        # quem olha a tela é até o CD DELE, então ele tem prioridade
        unidades = [u.strip() for u in str(row.get("Empresa Compra", "")).split("·") if u.strip()]
        minha = str(cd_usuario.get("sigla", "")).strip().upper()
        escolhida = next((u for u in unidades if u.upper() == minha), unidades[0] if unidades else "")
        cd_destino = cds_loc.get(escolhida.upper(), {}) if escolhida else {}
        if not cd_destino:
            cd_destino = cd_usuario  # fallback
        km = calcular_km_estimado(
            row.get("Latitude", ""),
            row.get("Longitude", ""),
            cd_destino.get("latitude", ""),
            cd_destino.get("longitude", ""),
            cd_usuario.get("fator_rodoviario", 1.25),
        )
        km_calculado.append(km)

    consolidado["KM Restante"] = [
        format_int_br(km) if str(km).strip() else ""
        for km in km_calculado
    ]

    # As marcações gravadas (regra de data, entregas na frente, observação,
    # descarga) são reaplicadas depois, pela viagem: app/services/card_validacao.py.
    # Aqui ficava uma busca por (placa, unidade, NF) que nunca casava.
    consolidado["Regra Data CD"] = "Não Verificado"
    consolidado["Entregas Frente"] = "Não Verificado"
    consolidado["Observação"] = ""

    _load_schedule(cd_usuario)
    consolidado = aplicar_estimativas(consolidado)

    ordem = [
        "Empresa",
        "Placa",
        "Data Faturamento",
        "Pedido Compra",
        "NF Pedidos",
        "NF",
        "Transportadora",
        "Empresa Compra",
        "Local Estoque",
        "Temperatura",
        "Unidade",
        "Quantidade",
        "Volume",
        "KM Restante",
        "Tempo Estimado",
        "Estimativa Chegada",
        "Cidade Atual",
        "Observação",
        "Regra Data CD",
        "Entregas Frente",
        "Instrução Entrada",
        "Última Posição",
        "Latitude",
        "Longitude",
        "Tipo Cruzamento",
        "Status Estimativa",
        "Temperatura Atual",
        "Ignição",
        "Situação Veículo",
        "Fonte Rastreio",
        "AE",
        "Card ID",
    ]

    for col in ordem:
        if col not in consolidado.columns:
            consolidado[col] = ""

    consolidado["Quantidade"] = pd.to_numeric(consolidado["Quantidade"], errors="coerce").fillna(0).round(0).astype(int).map(format_int_br)
    consolidado["Volume"] = pd.to_numeric(consolidado["Volume"], errors="coerce").fillna(0).round(0).astype(int).map(format_int_br)

    # Limpa colunas de telemetria (placas sem match no monitoramento ficam NaN/None)
    consolidado["Temperatura Atual"] = pd.to_numeric(consolidado["Temperatura Atual"], errors="coerce")
    consolidado["Ignição"] = consolidado["Ignição"].fillna("").astype(str).replace("nan", "")
    consolidado["Situação Veículo"] = consolidado["Situação Veículo"].fillna("").astype(str).replace("nan", "")
    consolidado["AE"] = consolidado["AE"].fillna("").astype(str).replace("nan", "")

    consolidado = consolidado.sort_values(by=["Placa", "NF"], kind="stable").reset_index(drop=True)

    return consolidado[ordem]
