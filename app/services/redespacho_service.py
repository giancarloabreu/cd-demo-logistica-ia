import os

import pandas as pd

from app.services.excel_reader import read_excel_flexible
from app.services import mapeamento_colunas
from app.utils.text_utils import normalize_plate
from app.database import get_cd_usuario, get_todas_validacoes, get_cds_localizacao
from app.services.distance_service import calcular_km_estimado
from app.services.arrival_estimator import aplicar_estimativas
from app.database import get_todas_validacoes
from app.utils.number_utils import format_int_br
from app.services import monitoramento_unificado


def _first_existing(row, columns):
    for col in columns:
        if col and col in row.index:
            value = row.get(col, "")
            if value is not None and str(value).strip() and str(value).strip().lower() != "nan":
                return value
    return ""


# Unidade reservada na tabela `validacoes` para o redespacho — as NFs dele
# vivem noutro espaço de numeração e não devem se misturar às coligadas.
_UNIDADE_REDESPACHO = "REDESPACHO"


# Colunas que o parser entrega — é a lista que `result[ordem]` recorta no fim.
# Também serve de referência para o servidor detectar DataFrame de versão
# anterior e reprocessar sozinho (ver _rebuild_redespacho_se_antigo).
COLUNAS_REDESPACHO = [
    "Selecionado",
    "Nome Redespacho",
    "Formulário",
    "Chave",
    "Empresa Origem",
    "Empresa Expedidora",
    "Placa",
    "Pedido Entrada",
    "Instrução Entrada",
    "Instrução Origem",
    "Pedido Filial",
    "Descarga Concluída",
    "Descarga Iniciada",
    "KM Restante",
    "Tempo Estimado",
    "Estimativa Chegada",
    # `result[ordem]` no fim RECORTA as colunas, então tudo que o card
    # precisa tem que estar aqui. "Status Estimativa" ficava de fora e o
    # card de redespacho nascia sem a cor da previsão de chegada; "Cidade
    # Atual" idem, e o campo aparecia sempre vazio (04/09/2026).
    "Unidade",
    "Status Estimativa",
    "Cidade Atual",
    "Fonte Rastreio",
    "Feriado",
    "Latitude",
    "Longitude",
    # ID da viagem (AE da OpenTech ou RVX) e a chegada pelo raio do CD —
    # app/services/carretas_viagem.py (18/10/2026)
    "Viagem",
    "Presença",
    "No CD Desde",
    "Aguardando Min",
    "Chegada CD",
    "Saída CD",
    "Idas CD",
    "Fora CD",
    "Sem Sinal CD",
    "Descarga Início",
    "Descarga Término",
    ]


def _unidade_do_nome(nome) -> str:
    """Unidade dona do redespacho, tirada das 3 ÚLTIMAS letras do nome.

    "EMPRESA S.A. - Cidade - CD CDA" -> "CDA". O relatório não traz
    a sigla num campo próprio; ela vem no fim do nome da Empresa Redespacho
    (CDA, CDB, CDE, CDF, CNT, DPR...) — convenção confirmada pelo usuário em
    04/09/2026. Só aceita se as 3 últimas forem mesmo letras, pra não inventar
    sigla a partir de um nome fora do padrão.
    """
    t = str(nome or "").strip()
    sigla = t[-3:].upper()
    return sigla if len(sigla) == 3 and sigla.isalpha() else ""


def carregar_redespacho(path: str, usuario: str = '', chaves_recebidas: set | None = None,
                        avisos: list | None = None) -> pd.DataFrame:
    """
    chaves_recebidas: conjunto de "Chave" (NF-e) que já constam no relatório de
    Estoque Redespacho — ou seja, já foram fisicamente recebidas. Cards com
    essa chave são descartados da fila (ver FEAT-7 no BACKLOG).
    """
    df = read_excel_flexible(path)
    cd = get_cd_usuario(usuario)
    chaves_recebidas = chaves_recebidas or set()

    # Coordenadas do CD destino: prioriza cds_localizacao (mesma fonte que o
    # painel principal usa em consolidacao_service.py, cadastrada pelo master
    # em "Localizações dos CDs") — get_cd_usuario()/preferencias.cd é um
    # config antigo por usuário que na prática nunca tem lat/lon preenchido,
    # e é por isso que o card de redespacho ficava sem KM/Estimativa (25/07/2026).
    cds_loc = {c["sigla"].strip().upper(): c for c in get_cds_localizacao()}
    cd_destino = cds_loc.get(str(cd.get("nome", "")).strip().upper(), {})
    if cd_destino.get("latitude") and cd_destino.get("longitude"):
        cd = {**cd, "latitude": cd_destino["latitude"], "longitude": cd_destino["longitude"]}

    # Colunas pelo NOME (mapeamento_colunas.py, 20/10/2026). Antes, a empresa
    # de origem era a coluna T (índice 19) e o resto ia por pedaço do nome —
    # "Placa" pegava a primeira coluna com placa no nome, e sem a coluna da
    # chave TODAS as linhas eram descartadas sem aviso. Chave e nome do
    # redespacho agora são obrigatórios (ColunasFaltando abre o mapeamento).
    df.columns = mapeamento_colunas.nomes_unicos(df.columns)
    col = mapeamento_colunas.resolver_arquivo("fila_redespacho", df.columns, os.path.basename(str(path)), avisos)
    col_nome = col["nome"]
    col_formulario = col["formulario"]
    col_chave = col["chave"]
    col_origem = col["origem"]
    # sem coluna própria de expedidora, quem expediu é a empresa de origem
    col_expedidor = col["expedidora"] or col["origem"]
    col_placa = col["placa"]
    col_pedido = col["pedido"]
    # Pedido Entrada > Instrução Filial — confirmado pelo usuário 25/07/2026
    col_instrucao = col["instrucao_filial"]
    # Instrução ORIGEM — a que a planta usa para carregar. Todas as NFs com a
    # mesma instrução origem são o redespacho de UM veículo, então é ela que
    # agrupa o card quando ainda não há placa (usuário, 04/09/2026).
    col_ins_origem = col["instrucao_origem"]
    # Pedido FILIAL — número curto (ex.: 55441), que é o que o operador usa.
    # O pedido matriz existe ao lado ("Pedido Entrada > Código", 8 dígitos) mas
    # não vai pro card: o usuário pediu só o filial (03/09/2026).
    col_pedido_filial = col["pedido_filial"]

    col_lat = col["latitude"]
    col_lon = col["longitude"]

    rows = []

    # Lat/lon mais recentes do rastreio (OpenTech + Ravex), indexados por placa
    # (fallback se não encontrar) — ver MEL-14 no BACKLOG.
    #
    # Guarda também a FONTE (OpenTech/Ravex) e a cidade: o card de redespacho
    # mostra o logo do sistema de rastreio no rodapé, igual ao de coligada, e
    # antes só as coordenadas eram aproveitadas (pedido do usuário 04/09/2026).
    opentech_coords = {}
    fonte_por_placa = {}
    cidade_por_placa = {}
    try:
        df_ot = monitoramento_unificado.get_df_monitoramento_unificado()
        if not df_ot.empty and "Placa Cruzamento" in df_ot.columns:
            for _, ot_row in df_ot.iterrows():
                placa_ot = normalize_plate(str(ot_row.get("Placa Cruzamento","")))
                if not placa_ot:
                    continue
                fonte = str(ot_row.get("Fonte Rastreio", "")).strip()
                if fonte and fonte.lower() != "nan":
                    fonte_por_placa[placa_ot] = fonte
                cidade = str(ot_row.get("Cidade Atual", "")).strip()
                if cidade and cidade.lower() != "nan":
                    cidade_por_placa[placa_ot] = cidade
                lat_ot = str(ot_row.get("Latitude","")).strip().replace(",", ".")
                lon_ot = str(ot_row.get("Longitude","")).strip().replace(",", ".")
                if lat_ot and lon_ot and lat_ot.lower() != "nan" and lon_ot.lower() != "nan":
                    opentech_coords[placa_ot] = (lat_ot, lon_ot)
    except Exception:
        opentech_coords, fonte_por_placa, cidade_por_placa = {}, {}, {}

    for _, row in df.iterrows():
        nome = _first_existing(row, [col_nome])
        formulario = _first_existing(row, [col_formulario])
        chave = _first_existing(row, [col_chave])
        origem    = _first_existing(row, [col_origem])
        expedidor = _first_existing(row, [col_expedidor]) if col_expedidor else ""
        pedido = _first_existing(row, [col_pedido])

        if not any(str(x).strip() for x in [nome, formulario, chave, origem, pedido]):
            continue

        # ------------------------------------------------------------------
        # A CHAVE DA NF decide se existe trânsito de mercadoria (regra do
        # usuário, 04/09/2026). Medido na Fila Redespacho real, 154 linhas:
        #
        #   sem chave           -> instrução programada, a fábrica não expediu
        #                          ainda. NÃO gera card.                   (16)
        #   chave no estoque    -> já recebido no CD, aguardando
        #                          roteirização. NÃO gera card.            (18)
        #   chave fora do est.  -> em trânsito. GERA CARD.                (120)
        #
        # Antes só a segunda regra existia, então as 16 sem chave viravam card
        # de carreta que nem saiu da fábrica.
        # ------------------------------------------------------------------
        if not str(chave).strip():
            continue

        if str(chave).strip() in chaves_recebidas:
            continue

        placa = normalize_plate(_first_existing(row, [col_placa]))
        lat = str(_first_existing(row, [col_lat])).strip().replace(",", ".")
        lon = str(_first_existing(row, [col_lon])).strip().replace(",", ".")

        # Prioriza lat/lon do rastreio OpenTech (mais atualizado), se a placa for encontrada
        if placa in opentech_coords:
            lat, lon = opentech_coords[placa]

        km = calcular_km_estimado(
            lat,
            lon,
            cd.get("latitude", ""),
            cd.get("longitude", ""),
            cd.get("fator_rodoviario", 1.25),
        )

        selecionado = False  # seleção controlada pelo banco por usuário

        rows.append({
            "Selecionado": "Sim" if selecionado else "Não",
            "Nome Redespacho": nome,
            "Formulário": formulario,
            "Chave": chave,
            "Empresa Origem": origem,
            "Empresa Expedidora": expedidor,
            "Placa": placa,
            "Pedido Entrada": pedido,
            "Instrução Entrada": _first_existing(row, [col_instrucao]),
            "Instrução Origem": _first_existing(row, [col_ins_origem]),
            "Unidade": _unidade_do_nome(nome),
            "Fonte Rastreio": fonte_por_placa.get(normalize_plate(_first_existing(row, [col_placa])), ""),
            "Cidade Atual": cidade_por_placa.get(normalize_plate(_first_existing(row, [col_placa])), ""),
            "Pedido Filial": _first_existing(row, [col_pedido_filial]),
            "Latitude": lat,
            "Longitude": lon,
            "KM Restante": format_int_br(km) if str(km).strip() else "",
            "Entregas Frente": "Não Verificado",
        })

    result = pd.DataFrame(rows)

    if result.empty:
        return result

    result = aplicar_estimativas(result)

    # "Descarga concluída" do redespacho reaproveita a tabela `validacoes`,
    # sob a unidade reservada REDESPACHO para não colidir com as NFs das
    # coligadas. Sem isso a marcação sumiria a cada reimportação do arquivo.
    try:
        salvas = get_todas_validacoes(usuario)
        marcadas = {nf for (uni, nf), v in salvas.items()
                    if uni == _UNIDADE_REDESPACHO and str(v.get("descarga_concluida","")) == "Sim"}
        if marcadas and "Formulário" in result.columns:
            result["Descarga Concluída"] = [
                "Sim" if str(x).strip() in marcadas else ""
                for x in result["Formulário"]
            ]
        iniciadas = {nf for (uni, nf), v in salvas.items()
                     if uni == _UNIDADE_REDESPACHO and str(v.get("descarga_iniciada", "")) == "Sim"}
        if "Formulário" in result.columns:
            result["Descarga Iniciada"] = ["Sim" if str(x).strip() in iniciadas else ""
                                           for x in result["Formulário"]]
    except Exception as e:
        print(f"[Redespacho] Não consegui reaplicar 'descarga concluída': {e}")

    from app.services.carretas_viagem import aplicar_no_df
    result = aplicar_no_df(result, usuario, "redespacho")

    ordem = COLUNAS_REDESPACHO

    for col in ordem:
        if col not in result.columns:
            result[col] = ""

    return result[ordem]


def carregar_chaves_estoque_redespacho(path: str) -> set:
    """
    Lê o relatório "Estoque Redespacho" e retorna o conjunto de chaves de NF-e
    presentes nele. Presença da chave = o produto já foi recebido fisicamente
    no CD (está no estoque), mesmo que a Fila Redespacho ainda não tenha
    "Instrução de Entrada" processada — ver FEAT-7 no BACKLOG.
    """
    df = read_excel_flexible(path)
    df.columns = mapeamento_colunas.nomes_unicos(df.columns)
    # "Origem > Chave NFE" no relatório real; sem a coluna, ColunasFaltando
    # (antes devolvia vazio e nada era tirado da fila, em silêncio)
    col_chave = mapeamento_colunas.resolver_arquivo("estoque_redespacho", df.columns,
                                                    os.path.basename(str(path)))["chave"]
    return {
        str(v).strip() for v in df[col_chave].dropna()
        if str(v).strip() and str(v).strip().lower() != "nan"
    }
