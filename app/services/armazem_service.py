"""
Serviço do módulo "Painel do Armazém" (FEAT-10).

Lê o relatório "Produto Paletizado" (mesmo TXT já usado pelo Adensamento —
ver app/services/adensamento_service.py, mesmos índices de coluna
confirmados por leitura cruzada em 05/08/2026) e calcula os agregados que
hoje alimentam manualmente o e-mail "Painel do Armazém":
Estoque Total, Ocupação de Câmaras, Tempo de Vida, Estoque Por Vencimento,
% Endereçado e Reserva/Bloqueio.

Todas as fórmulas abaixo foram reverse-engineered das planilhas reais que
geram o e-mail hoje (`Painel Gestão Armazém CDA.xlsx`, aba `Painel - Geral`,
lida com as fórmulas do Excel visíveis, não só os valores) — ver BACKLOG.md
FEAT-10 pra detalhe de cada decisão.
"""
from __future__ import annotations
import datetime as _dt
import copy
import os
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

from app.services.paletizado_colunas import (achar_cabecalho, valor, ler_linhas_txt,
                                             resolver_com_mapeamento, estilo_numero,
                                             estilo_data, numero, normalizar, data as _data_txt)

# Mínimo de colunas pra uma linha ser candidata a dado. O rodapé e as
# linhas de página do relatório são bem mais curtos. 34 = o suficiente pra
# alcançar o "Nome" do local de estoque, que é obrigatório aqui; campos
# além disso são lidos com bounds-check (ver paletizado_colunas.valor).
_MIN_COLS = 34

# "Importados" = qualquer TIPO DE PRODUTO começando com "TERCEIRO" (lista
# fechada confirmada pelo usuário e pela fórmula real: TERCEIRO - NACIONAL,
# TERCEIRO - IMPORTADO, TERCEIRO - SUPER GELADOS — todas fisicamente
# CONGELADO). "Carne com Osso" é excluída de todo o cálculo de Tempo de
# Vida (achado na fórmula, não teria como adivinhar só pelos números).
_PREFIXO_IMPORTADO = "TERCEIRO"
_TIPO_EXCLUIDO_VIDA = "CARNE COM OSSO"
_FIFO_DIAS_LIMITE = 21  # Dias Vencimento < 21 => Fifo (fórmula real do Painel-Geral!L31)

TEMPERATURAS = ["RESFRIADO", "CONGELADO", "AMBIENTE"]

VENCIMENTO_BUCKETS = [
    ("0 - 5", 0, 5), ("6 - 10", 6, 10), ("11 - 15", 11, 15),
    ("16 - 20", 16, 20), ("21 - 25", 21, 25), ("26 - 30", 26, 30),
]


def _palete(v: str, estilo: str = "br") -> str:
    """O identificador do palete do jeito que o WMS mostra.

    A exportação traz o número com separador de milhar brasileiro: "135.451"
    é o palete 135451. Guardar o texto com o ponto fazia o identificador não
    casar com nada do WMS — no confronto com o painel de Abastecimento de
    08/10/2026 batiam 5 de 19 paletes em CDA e 0 de 101 em CDB. O
    agrupamento continuava certo (o texto é estável para o mesmo palete),
    mas o número que chegava ao operador estava errado e ele não acharia o
    palete no armazém.

    Remove o ponto apenas quando ele separa milhar (grupos de 3 dígitos),
    para não estragar um valor que por acaso venha decimal. No arquivo com
    formato regional de ponto decimal o milhar vem com vírgula (20/10/2026).
    """
    sep = "," if estilo == "ponto" else "."
    t = str(v or "").strip()
    if not t or sep not in t:
        return t
    sem = t.replace(sep, "")
    if sem.isdigit() and all(len(g) == 3 for g in t.split(sep)[1:]):
        return str(int(sem))
    return t


def _motivos_bloqueio_do_cabecalho(cabecalho, col):
    """Nomes das colunas de motivo de bloqueio (grupo "Bloqueio") e onde o
    grupo comeca, lidos do proprio cabecalho — a quantidade varia por
    exportacao (0, 1 ou varias) e a posicao de inicio depende de quantas
    colunas fixas aquele export trouxe (61 no CDA; o export do CDD nem tem
    o grupo). Por isso o inicio e derivado do maior indice conhecido, em
    vez da constante fixa de antes."""
    if not col:
        return [], len(cabecalho)
    inicio = max(col.values()) + 1
    return [c.strip() for c in cabecalho[inicio:]], inicio


# campos do relatório que parse_produto_paletizado lê (além do grupo
# Bloqueio, no fim). No Excel, só essas colunas são convertidas em texto.
_CAMPOS_LIDOS = ("temperatura", "tipo_produto", "produto_codigo", "produto_nome", "local_nome",
                 "palete", "tipo", "peso_bruto", "peso_liquido", "producao", "validade",
                 "enderecamento", "camara", "rua", "box", "altura", "profundidade",
                 "reserva_codigo", "reserva_nome", "reserva_sigla")


class PlanilhaIlegivel(ValueError):
    """Excel que não abre (corrompido, protegido por senha ou outro formato)."""


def _tipo_arquivo(path) -> str:
    """'xlsx', 'xls' ou 'txt', pelo conteúdo — o nome do arquivo temporário
    do upload não diz nada."""
    with open(path, "rb") as f:
        inicio = f.read(8)
    if inicio.startswith(b"PK\x03\x04"):
        return "xlsx"
    if inicio.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "xls"
    return "txt"


def _ler_relatorio(path, obrigatorios, campos=_CAMPOS_LIDOS):
    """(linhas de texto, índice do cabeçalho, cabeçalho, colunas).

    TXT: como sempre. Excel (12/11/2026, pedido do usuário — "às vezes o
    relatório em Excel exporta com mais facilidade"): as linhas viram o
    mesmo texto do TXT (separado por tabulação, número com vírgula decimal,
    data dd/mm/aaaa), então tudo o que vem depois é o mesmo código já
    testado. Cabeçalho ausente → índice None."""
    nome = os.path.basename(str(path))
    tipo = _tipo_arquivo(path)
    if tipo == "txt":
        # codificação detectada (UTF-8 ou cp1252) — ver paletizado_colunas
        todas = ler_linhas_txt(path)
        idx = achar_cabecalho(todas)
        if idx is None:
            return todas, None, None, None
        cab = todas[idx].rstrip("\n").split("\t")
        # o que o usuário salvou no mapeamento vale primeiro; obrigatório que
        # faltar levanta ColunasFaltando (a tela abre o mapeamento)
        return todas, idx, cab, resolver_com_mapeamento(cab, obrigatorios, nome)

    from app.utils.xlsx_stream import linhas_planilha_texto, linhas_xls_texto, PlanilhaInvalida
    filtro: dict = {}
    msg = ("O Excel não pôde ser aberto. Confira se o arquivo não está protegido por senha ou "
           "corrompido; se continuar, exporte o relatório em TXT.")
    if tipo == "xls":
        # .xls antigo (ou .xlsx protegido por senha, que também é OLE): o
        # pandas lê a planilha inteira de uma vez (limite de 65 mil linhas)
        try:
            fonte = list(linhas_xls_texto(path))
        except Exception as e:
            raise PlanilhaIlegivel(msg) from e
    else:
        fonte = linhas_planilha_texto(path, filtro)
    try:
        todas, idx, cab, col = [], None, None, None
        for campos in fonte:
            if idx is None:
                if len(todas) >= 60:            # mesmo limite do achar_cabecalho
                    return todas, None, None, None
                if {"box", "altura", "profundidade"} <= {normalizar(c) for c in campos}:
                    idx, cab = len(todas), list(campos)
                    col = resolver_com_mapeamento(cab, obrigatorios, nome)
                    _motivos, inicio = _motivos_bloqueio_do_cabecalho(cab, col)
                    filtro["colunas"] = ({col[k] for k in campos if k in col}
                                         | set(range(inicio, len(cab))))
                    filtro["largura"] = len(cab)
            todas.append("\t".join(campos) + "\n")
    except (zipfile.BadZipFile, KeyError, ET.ParseError, EOFError, PlanilhaInvalida) as e:
        raise PlanilhaIlegivel(msg) from e
    finally:
        if hasattr(fonte, "close"):
            fonte.close()                       # fecha o zip mesmo quando para no meio
    return todas, idx, cab, col


def parse_produto_paletizado(path, obrigatorios=("temperatura", "local_nome")) -> list[dict]:
    """Le o TXT (ou o Excel, ver _ler_relatorio) e devolve uma linha por
    item (mesmo grao do arquivo — uma linha pode ser uma caixa/produto
    dentro de um palete).

    As colunas sao resolvidas pelo CABECALHO (paletizado_colunas.py), nao
    por posicao fixa: o layout muda entre empresas — no export de Senador
    a outra unidade o "Nome" do produto esta 2 colunas a frente e nao existe a
    coluna "Saida Estoque", o que desloca Enderecamento/Validade/Reserva
    (comparado com os dois arquivos reais, 01/09/2026)."""
    linhas = []
    todas, idx_header, cabecalho, col = _ler_relatorio(path, obrigatorios)
    if idx_header is None:
        return []
    motivos_bloqueio, inicio_bloqueio = _motivos_bloqueio_do_cabecalho(cabecalho, col)

    # formato regional do arquivo (decimal e data), decidido pela amostra
    amostra = [l.rstrip("\n").split("\t") for l in todas[idx_header + 1: idx_header + 5001]]
    # mínimo pelo cabeçalho DESTE arquivo: com colunas suprimidas na
    # exportação o arquivo inteiro pode ter menos de _MIN_COLS colunas, e o
    # corte fixo descartaria todas as linhas sem erro nenhum (11/11/2026).
    # Rodapé e linhas de página continuam fora: chegam sem temperatura/local.
    min_cols = min(_MIN_COLS, max(col.get(k, 0) for k in ("temperatura", "local_nome")) + 1)
    # a amostra que decide o formato dos números passa pelo mesmo filtro do
    # laço: a linha de totais do rodapé (peso com separador de milhar) não
    # pode decidir o estilo — num arquivo em formato de ponto viraria "br"
    # e o peso sairia 1000x (revisão 11/11/2026)
    amostra = [p for p in amostra if len(p) >= min_cols
               and valor(p, col, "temperatura") and valor(p, col, "local_nome")]
    estilo_n = estilo_numero([valor(p, col, k) for p in amostra for k in ("peso_liquido", "peso_bruto")],
                             [valor(p, col, "palete") for p in amostra])
    estilo_d = estilo_data([valor(p, col, k) for p in amostra for k in ("producao", "validade")])
    del amostra

    for linha in todas[idx_header + 1:]:
        p = linha.rstrip("\n").split("\t")
        if len(p) < min_cols:
            continue
        temp = valor(p, col, "temperatura").upper()
        local = valor(p, col, "local_nome")
        if not temp or not local:
            continue
        # Cada motivo marcado com "X" naquela linha — o arquivo pode ter
        # menos colunas do que o cabecalho prometeu (tabs finais cortados
        # na exportacao), por isso o bounds-check em vez de indexar direto.
        bloqueio_motivos = [
            nome for i, nome in enumerate(motivos_bloqueio)
            if nome and inicio_bloqueio + i < len(p)
            and p[inicio_bloqueio + i].strip().upper() == "X"
        ]
        linhas.append({
            "temperatura": temp,
            "tipo_produto": valor(p, col, "tipo_produto"),
            "codigo": valor(p, col, "produto_codigo"),
            "nome": valor(p, col, "produto_nome"),
            "local": local,
            "palete": _palete(valor(p, col, "palete"), estilo_n),
            # "Tipo" do endereço: "2-Picking" é pallet que JÁ está em face de
            # picking, "1-Estocagem" está no estoque alto. É o próprio TXT
            # dizendo o que está no chão — antes o Expedito dependia das
            # posições cadastradas à mão para saber isso (08/10/2026).
            "tipo_endereco": valor(p, col, "tipo"),
            "no_picking": "picking" in valor(p, col, "tipo").lower(),
            # bruto entra SO no "Peso medio/pallet" (pedido do usuario
            # 01/09/2026); todo o resto do painel segue no liquido
            "peso_bruto": numero(valor(p, col, "peso_bruto"), estilo_n),
            "peso_liquido": numero(valor(p, col, "peso_liquido"), estilo_n),
            "producao": _data_txt(valor(p, col, "producao"), estilo_d),
            "validade": _data_txt(valor(p, col, "validade"), estilo_d),
            "endereçado": bool(valor(p, col, "enderecamento")),
            # Endereço físico — câmara, rua, box, altura e profundidade. O
            # Armazém não usa (ele mede volume, não posição), mas o Expedito
            # precisa para dizer de onde o pallet sai e para reconhecer o que
            # já está em face de picking (06/10/2026). São campos a mais no
            # mesmo dicionário: quem não usa, ignora.
            "camara": valor(p, col, "camara"),
            "rua": valor(p, col, "rua"),
            "box": valor(p, col, "box"),
            "altura": valor(p, col, "altura"),
            "profundidade": valor(p, col, "profundidade"),
            "reserva_codigo": valor(p, col, "reserva_codigo"),
            "reserva_nome": valor(p, col, "reserva_nome"),
            "reserva_sigla": valor(p, col, "reserva_sigla"),
            "bloqueio_motivos": bloqueio_motivos,
        })
    return linhas


def _e_importado(tipo_produto: str) -> bool:
    return tipo_produto.strip().upper().startswith(_PREFIXO_IMPORTADO)


def _vida_categoria(row) -> str | None:
    """1/3, 2/3, 3/3 ou None (sem data suficiente pra calcular). 'Vencido'
    fica implícito quando percentual >= 100% (validade já passou) — regra
    validada contra 1111+1121 linhas reais com <3% de ruído."""
    prod, val = row["producao"], row["validade"]
    if not prod or not val:
        return None
    total_dias = (val - prod).days
    if total_dias <= 0:
        return None
    decorridos = (_dt.date.today() - prod).days
    pct = decorridos / total_dias * 100
    if pct >= 100:
        return "Vencido"
    if pct < 100 / 3:
        return "1/3"
    if pct < 200 / 3:
        return "2/3"
    return "3/3"


def detectar_locais_novos(linhas: list[dict], locais_conhecidos: set[str]) -> list[str]:
    """Locais de estoque que aparecem no arquivo mas ainda não têm
    classificação cadastrada — precisam virar pendência."""
    vistos = []
    seen = set()
    for r in linhas:
        loc = r["local"]
        if loc not in locais_conhecidos and loc not in seen:
            seen.add(loc)
            vistos.append(loc)
    return vistos


def calcular_metricas(linhas: list[dict], capacidades: dict[str, float],
                      locais_desconsiderar: set[str], valor_contrato_ton: float = 0,
                      ambiente_modo: str = "consome_resfriado",
                      capacidade_contratada: dict[str, float] | None = None) -> dict:
    """
    Calcula todos os agregados do painel.
    `capacidades` = {"RESFRIADO":287, "CONGELADO":300, "AMBIENTE":0} (ton).
    `valor_contrato_ton` = R$/ton — UM valor só pro contrato inteiro, não um
    por temperatura (correção do usuário 08/08/2026: "eu fiz a conta em
    relação ao contrato total, não foi real por tonelada por temperatura" —
    é exatamente assim na planilha original: só a aba "Total" tem R$
    Previsto/R$ por Ton, as abas por temperatura não têm).
    `ambiente_modo` = "consome_resfriado" (Ambiente não tem câmara própria —
    fica dentro da câmara de Resfriado, consome a MESMA capacidade; é o caso
    do CD Demo hoje, onde Ambiente sempre aparece com Capacidade=0 na
    planilha) ou "capacidade_propria" (outros CDs têm contrato próprio pra
    Ambiente — pedido do usuário 08/08/2026, precisa ser configurável por
    CD/usuário, não fixo). `locais_desconsiderar` = nomes de local marcados
    como "Desconsiderar" (ex.: local de falta na compra) — sempre excluídos
    de tudo, igual à exclusão fixa CDA CB FALTA NA COMPRA na planilha original.
    """
    linhas = [r for r in linhas if r["local"] not in locais_desconsiderar]

    # estoque_fisico_ton = TUDO daquela temperatura, imports inclusos — é a
    # base de ocupação/vida/endereçamento/bloqueio (a planilha original usa
    # só o filtro de Temperatura nessas contas, sem excluir Importados).
    # importados_por_temp: rastreado POR TEMPERATURA (não só um total global)
    # pra generalizar o gráfico de barra empilhada — hoje só existe
    # Importados em Congelado, mas se um dia aparecer em Resfriado/Ambiente
    # o gráfico já sabe empilhar certo (pedido do usuário 08/08/2026: "essa
    # mesma estrutura pode ser replicada nas demais temperaturas, se caso
    # houver um momento de terceiros para outras temperaturas").
    estoque_fisico_ton = defaultdict(float)
    # peso BRUTO acumulado em paralelo — usado SÓ no "Peso médio/pallet"
    # (pedido do usuário 01/09/2026). Todo o resto do painel (ocupação,
    # vida, endereçamento, bloqueio, R$/ton) continua no líquido, que é o
    # que a planilha original usa e é o número que o pessoal compara.
    estoque_bruto_ton = defaultdict(float)
    importados_por_temp = defaultdict(float)
    paletes_por_temp = defaultdict(set)
    bloqueio_ton = defaultdict(float)
    reservado_ton = defaultdict(float)
    endereçado_ton = defaultdict(float)
    sem_endereco_ton = defaultdict(float)
    vida_ton = defaultdict(lambda: defaultdict(float))   # temp -> categoria -> ton
    fifo_ton = defaultdict(float)
    vencimento_buckets_ton = defaultdict(lambda: defaultdict(float))  # temp -> bucket -> ton

    for r in linhas:
        temp = r["temperatura"]
        if temp not in TEMPERATURAS:
            continue
        peso_ton = r["peso_liquido"] / 1000
        estoque_fisico_ton[temp] += peso_ton
        estoque_bruto_ton[temp] += r.get("peso_bruto", 0.0) / 1000
        if _e_importado(r["tipo_produto"]):
            importados_por_temp[temp] += peso_ton

        if r["palete"]:
            paletes_por_temp[temp].add(r["palete"])

        # Bloqueio: sigla=="BLOQUEIO" é o sinal antigo (mantido por
        # compatibilidade), mas o TXT real trouxe um grupo de colunas
        # "Bloqueio" separado (motivo marcado com "X") que é o sinal de
        # verdade hoje — nenhuma linha real tinha sigla=="BLOQUEIO", mas 149
        # tinham motivo marcado (achado do usuário 24/08/2026, TXT real
        # anexado). Bloqueio tem prioridade sobre reserva: um produto
        # bloqueado não deveria contar como "reservado pra venda".
        sigla = r["reserva_sigla"].strip().upper()
        if sigla == "BLOQUEIO" or r["bloqueio_motivos"]:
            bloqueio_ton[temp] += peso_ton
        elif sigla:
            reservado_ton[temp] += peso_ton

        if r["endereçado"]:
            endereçado_ton[temp] += peso_ton
        else:
            sem_endereco_ton[temp] += peso_ton

        tipo_upper = r["tipo_produto"].strip().upper()
        if tipo_upper != _TIPO_EXCLUIDO_VIDA:
            cat = _vida_categoria(r)
            if cat:
                vida_ton[temp][cat] += peso_ton
            if r["validade"]:
                dias_para_vencer = (r["validade"] - _dt.date.today()).days
                if dias_para_vencer < _FIFO_DIAS_LIMITE:
                    fifo_ton[temp] += peso_ton
                for label, dmin, dmax in VENCIMENTO_BUCKETS:
                    if dmin <= dias_para_vencer <= dmax:
                        vencimento_buckets_ton[temp][label] += peso_ton
                        break

    def r3(x): return round(x, 3)

    ambiente_propria = ambiente_modo == "capacidade_propria"

    temperaturas_out = {}
    for temp in TEMPERATURAS:
        cap = capacidades.get(temp, 0) or 0
        fisico = estoque_fisico_ton[temp]
        importado_temp = importados_por_temp[temp]
        # Estoque "exibido" no gráfico de barras = parte nacional (a barra
        # empilha Importados por cima no front, generalizado por temperatura
        # — ver renderStackedBar em armazem.html).
        exibido = fisico - importado_temp
        # Ocupação: se Ambiente NÃO tem câmara própria (padrão CDA), seu
        # estoque soma no numerador da ocupação do Resfriado (mesma câmara
        # física) e Ambiente em si não tem % de ocupação próprio.
        if temp == "RESFRIADO" and not ambiente_propria:
            fisico_ocupacao = fisico + estoque_fisico_ton["AMBIENTE"]
        elif temp == "AMBIENTE" and not ambiente_propria:
            fisico_ocupacao = None  # compartilha com Resfriado — não tem % isolado
        else:
            fisico_ocupacao = fisico
        temperaturas_out[temp] = {
            "estoque_ton": r3(exibido),
            "importados_ton": r3(importado_temp),
            "estoque_fisico_ton": r3(fisico),
            "estoque_bruto_ton": r3(estoque_bruto_ton[temp]),
            "capacidade_ton": cap,
            "ocupacao_pct": (r3(fisico_ocupacao / cap * 100) if cap else None) if fisico_ocupacao is not None else None,
            "compartilha_capacidade": (temp == "AMBIENTE" and not ambiente_propria),
            "bloqueio_ton": r3(bloqueio_ton[temp]),
            "reservado_ton": r3(reservado_ton[temp]),
            "paletes": len(paletes_por_temp[temp]),
            "endereçado_ton": r3(endereçado_ton[temp]),
            "sem_endereco_ton": r3(sem_endereco_ton[temp]),
            "pct_enderecado": r3(endereçado_ton[temp] / fisico * 100) if fisico else None,
            "vida": {cat: r3(vida_ton[temp].get(cat, 0)) for cat in ("1/3", "2/3", "3/3", "Vencido")},
            "fifo_ton": r3(fifo_ton[temp]),
            "vencimento_buckets": {label: r3(vencimento_buckets_ton[temp].get(label, 0))
                                   for label, _, _ in VENCIMENTO_BUCKETS},
        }

    total_fisico = sum(estoque_fisico_ton.values())
    # Capacidade FÍSICA total (câmara): Resfriado+Congelado sempre; Ambiente só
    # entra se tiver câmara própria (senão já está embutido no Resfriado).
    # Usada só pra exibição ("Capacidade" no painel) — NUNCA entra no R$
    # Previsto (redesenho Local×Empresa, 29/08/2026: a câmara é compartilhada
    # entre empresas do mesmo Local, então não representa o que UMA empresa
    # contratou).
    capacidade_total = capacidades.get("RESFRIADO", 0) + capacidades.get("CONGELADO", 0)
    if ambiente_propria:
        capacidade_total += capacidades.get("AMBIENTE", 0)

    # Capacidade CONTRATADA (o que ESSA empresa paga por mês) — é o que
    # entra no R$ Previsto, não a física. Sem capacidade_contratada
    # explícita (CD sem multi-empresa configurado, ou chamada antiga),
    # cai no mesmo valor da física — comportamento idêntico ao de antes
    # dessa mudança.
    if capacidade_contratada is not None:
        capacidade_contratada_total = capacidade_contratada.get("RESFRIADO", 0) + capacidade_contratada.get("CONGELADO", 0)
        if ambiente_propria:
            capacidade_contratada_total += capacidade_contratada.get("AMBIENTE", 0)
    else:
        capacidade_contratada_total = capacidade_total

    # R$ Previsto = capacidade CONTRATADA × valor/ton (fixo, independe da
    # ocupação real). R$/Ton Realizado = esse valor fixo diluído pelo
    # estoque de verdade desta empresa (todas as temperaturas somadas):
    # quanto mais cheio, menor o custo por tonelada armazenada — mesma
    # lógica da planilha original (só existe na aba "Total", não por
    # temperatura).
    r_previsto = capacidade_contratada_total * valor_contrato_ton

    return {
        "temperaturas": temperaturas_out,
        "importados_ton": r3(sum(importados_por_temp.values())),
        "total_ton": r3(total_fisico),
        "total_bruto_ton": r3(sum(estoque_bruto_ton.values())),
        "total_paletes": sum(len(s) for s in paletes_por_temp.values()),
        "financeiro": {
            "capacidade_total_ton": r3(capacidade_total),
            "capacidade_contratada_ton": r3(capacidade_contratada_total),
            "valor_contrato_ton": valor_contrato_ton,
            "r_previsto": r3(r_previsto),
            "r_por_ton_previsto": valor_contrato_ton,
            "r_por_ton_realizado": r3(r_previsto / total_fisico) if total_fisico else None,
        },
    }


def aplicar_capacidade(metricas: dict, capacidades: dict[str, float],
                       ambiente_propria: bool = False,
                       contratada: dict[str, float] | None = None) -> dict:
    """Troca o denominador da ocupação pelo da VISÃO que está na tela.

    O estoque já está calculado; o que muda entre ver "CDA", "CDB" ou as
    duas juntas é contra qual capacidade ele é medido. Antes a capacidade
    era resolvida uma vez, no processamento do TXT, com o Local inteiro —
    e ficava gravada igual nas três visões (achado do usuário em produção,
    01/10/2026: "seleciono CDB e o xx / xx continua mostrando CDA").

    Mesma regra de ocupação do calcular_metricas: sem câmara própria, o
    Ambiente soma no numerador do Resfriado e não tem % próprio.
    """
    m = copy.deepcopy(metricas)
    temps = m.get("temperaturas") or {}
    amb_fisico = (temps.get("AMBIENTE") or {}).get("estoque_fisico_ton", 0) or 0
    for temp, t in temps.items():
        cap = capacidades.get(temp, 0) or 0
        fisico = t.get("estoque_fisico_ton", 0) or 0
        if temp == "RESFRIADO" and not ambiente_propria:
            fisico_ocupacao = fisico + amb_fisico
        elif temp == "AMBIENTE" and not ambiente_propria:
            fisico_ocupacao = None
        else:
            fisico_ocupacao = fisico
        t["capacidade_ton"] = cap
        t["ocupacao_pct"] = (round(fisico_ocupacao / cap * 100, 3)
                             if cap and fisico_ocupacao is not None else None)
    total = (capacidades.get("RESFRIADO", 0) or 0) + (capacidades.get("CONGELADO", 0) or 0)
    if ambiente_propria:
        total += capacidades.get("AMBIENTE", 0) or 0
    fin = m.get("financeiro")
    if fin is not None:
        fin["capacidade_total_ton"] = round(total, 3)
        # O financeiro é da EMPRESA (o que ela contratou), e também ficava
        # congelado no snapshot: trocar de empresa não mudava o "Capacidade
        # total contratada" nem o R$ Previsto (usuário, 01/10/2026).
        # `contratada=None` deixa o financeiro intacto — é o caso do CD de
        # uma empresa só, que nunca teve essa dimensão.
        if contratada is not None:
            contratado_total = (contratada.get("RESFRIADO", 0) or 0) + (contratada.get("CONGELADO", 0) or 0)
            if ambiente_propria:
                contratado_total += contratada.get("AMBIENTE", 0) or 0
            valor = fin.get("valor_contrato_ton", 0) or 0
            r_previsto = contratado_total * valor
            estoque = m.get("total_ton", 0) or 0
            fin["capacidade_contratada_ton"] = round(contratado_total, 3)
            fin["r_previsto"] = round(r_previsto, 3)
            fin["r_por_ton_previsto"] = valor
            fin["r_por_ton_realizado"] = round(r_previsto / estoque, 3) if estoque else None
    return m


def merge_metricas(lista_metricas: list[dict]) -> dict:
    """Combina os agregados JÁ CALCULADOS de 2+ empresas do mesmo Local
    numa visão única "Todas" (redesenho Local×Empresa, 29/08/2026) — pedido
    do usuário depois de ver que o seletor só deixava ver uma OU outra,
    nunca as duas juntas. Soma tudo que é quantidade (estoque, paletes,
    capacidade CONTRATADA/financeiro); capacidade FÍSICA da câmara nunca
    soma — é uma só, compartilhada, todas as entradas trazem o mesmo
    valor (vem de _get_capacidades(usuario), sem dimensão de empresa).
    Essa é a única forma de calcular ocupação física correta: cada visão
    "por empresa" isolada SUBESTIMA a ocupação real, porque só enxerga o
    próprio estoque, nunca o da(s) outra(s) empresa(s) na mesma câmara."""
    if not lista_metricas:
        return metricas_zeradas({})
    if len(lista_metricas) == 1:
        return lista_metricas[0]

    def r3(x): return round(x, 3)

    temperaturas_out = {}
    for temp in TEMPERATURAS:
        entradas = [m["temperaturas"][temp] for m in lista_metricas]
        estoque_fisico_ton = sum(e["estoque_fisico_ton"] for e in entradas)
        cap = max((e["capacidade_ton"] or 0) for e in entradas)
        compartilha = entradas[0]["compartilha_capacidade"]
        enderecado_ton = sum(e["endereçado_ton"] for e in entradas)
        vida = {cat: r3(sum(e["vida"].get(cat, 0) for e in entradas)) for cat in ("1/3", "2/3", "3/3", "Vencido")}
        vencimento_buckets = {label: r3(sum(e["vencimento_buckets"].get(label, 0) for e in entradas))
                              for label, _, _ in VENCIMENTO_BUCKETS}
        temperaturas_out[temp] = {
            "estoque_ton": r3(sum(e["estoque_ton"] for e in entradas)),
            "importados_ton": r3(sum(e["importados_ton"] for e in entradas)),
            "estoque_fisico_ton": r3(estoque_fisico_ton),
            "capacidade_ton": cap,
            "ocupacao_pct": (r3(estoque_fisico_ton / cap * 100) if cap else None) if not compartilha else None,
            "compartilha_capacidade": compartilha,
            "bloqueio_ton": r3(sum(e["bloqueio_ton"] for e in entradas)),
            "reservado_ton": r3(sum(e["reservado_ton"] for e in entradas)),
            "paletes": sum(e["paletes"] for e in entradas),
            "endereçado_ton": r3(enderecado_ton),
            "sem_endereco_ton": r3(sum(e["sem_endereco_ton"] for e in entradas)),
            "pct_enderecado": r3(enderecado_ton / estoque_fisico_ton * 100) if estoque_fisico_ton else None,
            "vida": vida,
            "fifo_ton": r3(sum(e["fifo_ton"] for e in entradas)),
            "vencimento_buckets": vencimento_buckets,
        }

    total_ton = sum(m["total_ton"] for m in lista_metricas)
    fin = [m["financeiro"] for m in lista_metricas]
    capacidade_contratada_total = sum(f["capacidade_contratada_ton"] for f in fin)
    valor_contrato_ton = fin[0]["valor_contrato_ton"]  # taxa compartilhada, igual em todas
    r_previsto = sum(f["r_previsto"] for f in fin)

    return {
        "temperaturas": temperaturas_out,
        "importados_ton": r3(sum(m["importados_ton"] for m in lista_metricas)),
        "total_ton": r3(total_ton),
        "total_bruto_ton": r3(sum(m.get("total_bruto_ton", 0) or 0 for m in lista_metricas)),
        "total_paletes": sum(m["total_paletes"] for m in lista_metricas),
        "financeiro": {
            "capacidade_total_ton": fin[0]["capacidade_total_ton"],  # física, nunca soma
            "capacidade_contratada_ton": r3(capacidade_contratada_total),
            "valor_contrato_ton": valor_contrato_ton,
            "r_previsto": r3(r_previsto),
            "r_por_ton_previsto": valor_contrato_ton,
            "r_por_ton_realizado": r3(r_previsto / total_ton) if total_ton else None,
        },
    }


def metricas_zeradas(capacidades: dict[str, float], valor_contrato_ton: float = 0,
                     ambiente_modo: str = "consome_resfriado",
                     capacidade_contratada: dict[str, float] | None = None) -> dict:
    """Mesma forma de calcular_metricas, tudo zerado — usada quando há
    pendência de classificação de local (decisão do usuário: força a
    resolver a pendência em vez de arriscar mostrar número incompleto)."""
    ambiente_propria = ambiente_modo == "capacidade_propria"
    temperaturas_out = {}
    for temp in TEMPERATURAS:
        cap = capacidades.get(temp, 0) or 0
        compartilha = temp == "AMBIENTE" and not ambiente_propria
        temperaturas_out[temp] = {
            "estoque_ton": 0, "importados_ton": 0, "estoque_fisico_ton": 0,
            "estoque_bruto_ton": 0, "capacidade_ton": cap,
            "ocupacao_pct": (0 if cap else None) if not compartilha else None,
            "compartilha_capacidade": compartilha,
            "bloqueio_ton": 0, "reservado_ton": 0, "paletes": 0,
            "endereçado_ton": 0, "sem_endereco_ton": 0, "pct_enderecado": None,
            "vida": {"1/3": 0, "2/3": 0, "3/3": 0, "Vencido": 0}, "fifo_ton": 0,
            "vencimento_buckets": {label: 0 for label, _, _ in VENCIMENTO_BUCKETS},
        }
    capacidade_total = capacidades.get("RESFRIADO", 0) + capacidades.get("CONGELADO", 0)
    if ambiente_propria:
        capacidade_total += capacidades.get("AMBIENTE", 0)
    if capacidade_contratada is not None:
        capacidade_contratada_total = capacidade_contratada.get("RESFRIADO", 0) + capacidade_contratada.get("CONGELADO", 0)
        if ambiente_propria:
            capacidade_contratada_total += capacidade_contratada.get("AMBIENTE", 0)
    else:
        capacidade_contratada_total = capacidade_total
    return {
        "temperaturas": temperaturas_out, "importados_ton": 0, "total_ton": 0,
        "total_bruto_ton": 0, "total_paletes": 0,
        "financeiro": {
            "capacidade_total_ton": round(capacidade_total, 3),
            "capacidade_contratada_ton": round(capacidade_contratada_total, 3),
            "valor_contrato_ton": valor_contrato_ton, "r_previsto": 0,
            "r_por_ton_previsto": valor_contrato_ton, "r_por_ton_realizado": None,
        },
    }


def paletes_por_local(linhas: list[dict]) -> dict[str, int]:
    """Contagem de paletes distintos por Local de Estoque — base pro futuro
    Giro de Estoque (FEAT-10 fase 4)."""
    por_local = defaultdict(set)
    for r in linhas:
        if r["palete"]:
            por_local[r["local"]].add(r["palete"])
    return {loc: len(pals) for loc, pals in por_local.items()}


# ── QlikView — parser de colagem, formato LARGO (Fase 2) ────────────────
# Correção 09/08/2026: o formato real (usuário mandou print da planilha
# "08 - Estoque por temperatura") é PIVOTADO — linhas são métricas
# (Atacado/Varejo/Devolução/Total de Palates/Total ton armaz./Capacidade),
# colunas são os DIAS DO MÊS (1 a 31, com cabeçalho tipo "sáb 01"), mais
# colunas de comparação anual (2017...2026) e uma "Total Média" no fim —
# exatamente a estrutura das abas Resfriado/Congelado/Ambiente do arquivo
# original (cada aba = 1 temperatura, copiada/colada de cada vez). Isso é
# BEM diferente do formato "uma linha por dia" que eu tinha chutado antes
# (sem exemplo real) — refeito do zero pra bater com o que o usuário
# realmente tem na tela.
_QLIK_ROW_ALIASES = {
    "atacado": "atacado_ton", "atacado (geral)": "atacado_ton",
    "varejo": "varejo_ton", "varejo (geral)": "varejo_ton",
    "devolucao": "devolucao_ton",
    "somente terceiros": "terceiros_ton", "terceiros": "terceiros_ton", "importados": "terceiros_ton",
    "total de palates": "paletes", "total de paletes": "paletes", "paletes": "paletes",
    "capacidade": "capacidade_ton",
}
# Linhas reconhecidas mas não armazenadas (são somas derivadas das outras —
# recalculamos no front a partir de atacado+varejo+devolução+terceiros, ver
# armazem.html renderEvolucaoMensal — evita depender de bater exatamente
# com o "Total ton armaz." da planilha, que pode ter arredondamento próprio)
_QLIK_ROW_IGNORAR = {"total ton armaz.", "total ton armaz", "total", "estoque"}


def _norm_col(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().strip().lower()
    return s


def _parse_num_br(s: str):
    s = (s or "").strip()
    if not s or s in ("-", "—"):
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def _extrair_dia_do_cabecalho(celula: str) -> int | None:
    """'sáb 01' / '01' / '01/08' / 'dom  2' → 2 (ou None se não achar um
    número plausível de dia-do-mês). Rejeita anos (4 dígitos) de propósito
    — é assim que as colunas de comparação anual (2017...2026) são
    ignoradas sem precisar saber quantas vêm antes dos dias."""
    import re
    celula = celula.strip()
    if not celula or len(celula) >= 4 and celula.isdigit():  # ano de 4 dígitos: não é dia
        return None
    m = re.search(r"(?<!\d)([0-3]?\d)(?!\d)", celula)
    if not m:
        return None
    dia = int(m.group(1))
    return dia if 1 <= dia <= 31 else None


def parse_colagem_qlikview(texto: str, ano_mes: str, temperatura: str) -> dict:
    """
    Parser da colagem em formato LARGO (linhas=métricas, colunas=dias do
    mês). `ano_mes` = 'YYYY-MM' e `temperatura` = RESFRIADO/CONGELADO/
    AMBIENTE vêm de seletores na tela (não têm como estar no paste, já que
    a planilha original é uma aba por temperatura, sem coluna de data
    explícita — só "sáb 01" etc., que não diz o mês).

    Detecção do cabeçalho: escaneia a(s) primeira(s) linha(s) coladas
    procurando números plausíveis de dia-do-mês (1-31) em cada célula,
    ignorando anos de 4 dígitos e texto não-numérico (cobre tanto um
    cabeçalho de uma linha só quanto o caso de vir em duas linhas
    separadas — dia da semana numa, número do dia na outra, como o Excel
    de origem faz com células mescladas).

    Detecção das linhas de dado: primeira célula da linha comparada (sem
    acento/maiúscula) contra os rótulos conhecidos (Atacado, Varejo,
    Devolução, Total de Palates, Capacidade, Somente Terceiros).

    Retorna {"ok", "linhas":[{"data","temperatura",...}], "linhas_reconhecidas":
    [rótulos], "linhas_ignoradas": [rótulos não reconhecidos], "erros"}.
    Não grava no banco — quem chama decide (ver web/armazem.py).
    """
    from datetime import date
    import calendar

    linhas_texto = [l for l in texto.strip("\n").splitlines() if l.strip()]
    if len(linhas_texto) < 2:
        return {"ok": False, "erros": ["Cole pelo menos uma linha de cabeçalho (com os dias) e uma linha de dado."],
                "linhas_reconhecidas": [], "linhas_ignoradas": [], "linhas": []}

    try:
        ano, mes = (int(x) for x in ano_mes.split("-"))
        dias_no_mes = calendar.monthrange(ano, mes)[1]
    except (ValueError, AttributeError):
        return {"ok": False, "erros": [f"Mês/ano inválido: {ano_mes!r}."],
                "linhas_reconhecidas": [], "linhas_ignoradas": [], "linhas": []}

    sep = "\t" if "\t" in linhas_texto[0] else ","
    linhas_split = [l.split(sep) for l in linhas_texto]

    # Acha a coluna->dia em cada uma das 2 primeiras linhas; usa a que achar
    # mais (cobre cabeçalho de 1 linha OU de 2 linhas separadas — semana/dia).
    def _mapear(linha_cels):
        mapa = {}
        for i, cel in enumerate(linha_cels):
            dia = _extrair_dia_do_cabecalho(cel)
            if dia and 1 <= dia <= dias_no_mes and dia not in mapa.values():
                mapa[i] = dia
        return mapa

    mapa0 = _mapear(linhas_split[0])
    mapa1 = _mapear(linhas_split[1]) if len(linhas_split) > 1 else {}
    if len(mapa1) > len(mapa0):
        mapa_col_dia, primeira_linha_dado = mapa1, 2
    else:
        mapa_col_dia, primeira_linha_dado = mapa0, 1

    if not mapa_col_dia:
        return {"ok": False, "erros": [
            "Não consegui achar colunas de dia do mês (esperava algo tipo 'sáb 01', '01' ou '01/08') "
            "na(s) primeira(s) linha(s) coladas."],
            "linhas_reconhecidas": [], "linhas_ignoradas": [], "linhas": []}

    linhas_out, erros = [], []
    reconhecidas, ignoradas = [], []
    for n, cels in enumerate(linhas_split[primeira_linha_dado:], start=primeira_linha_dado+1):
        if not cels or not cels[0].strip():
            continue
        rotulo_raw = cels[0].strip()
        rotulo_norm = _norm_col(rotulo_raw)
        if rotulo_norm in _QLIK_ROW_IGNORAR:
            continue
        campo = _QLIK_ROW_ALIASES.get(rotulo_norm)
        if not campo:
            ignoradas.append(rotulo_raw)
            continue
        reconhecidas.append(rotulo_raw)
        for col_idx, dia in mapa_col_dia.items():
            if col_idx >= len(cels):
                continue
            valor = _parse_num_br(cels[col_idx])
            if valor is None:
                continue
            data_iso = date(ano, mes, dia).isoformat()
            registro = next((r for r in linhas_out if r["data"] == data_iso), None)
            if registro is None:
                registro = {"data": data_iso, "temperatura": temperatura}
                linhas_out.append(registro)
            registro[campo] = int(valor) if campo == "paletes" else valor

    if not linhas_out:
        erros.append("Nenhuma linha com rótulo reconhecido (Atacado/Varejo/Devolução/"
                     "Total de Palates/Capacidade) tinha valor numérico nas colunas de dia.")

    return {"ok": len(linhas_out) > 0, "linhas": linhas_out,
            "linhas_reconhecidas": sorted(set(reconhecidas)), "linhas_ignoradas": sorted(set(ignoradas)),
            "erros": erros}


# ── modelo de planilha .xlsx pra upload (alternativa ao paste) ─────────
# Mesma estrutura da planilha real do usuário (Temperatura, Tipo Local, dias
# do mês como colunas) — ao contrário do paste, aqui a Temperatura vem na
# própria linha, então um upload só cobre as 3 temperaturas de uma vez
# (pedido do usuário 19/08/2026: "sobe a planilha modelo com as informações"
# em vez de colar aba por aba). Linha (None,None,None) = separador em branco,
# só estético (bate com o arquivo de exemplo enviado pelo usuário).
_QLIK_LINHAS_MODELO = [
    ("RESFRIADO", "Atacado (Geral)", "atacado_ton"),
    ("RESFRIADO", "Varejo (Geral)", "varejo_ton"),
    ("RESFRIADO", "Somente Terceiros", "terceiros_ton"),
    ("RESFRIADO", "Devolução", "devolucao_ton"),
    ("CONGELADO", "Atacado (Geral)", "atacado_ton"),
    ("CONGELADO", "Varejo (Geral)", "varejo_ton"),
    ("CONGELADO", "Somente Terceiros", "terceiros_ton"),
    ("CONGELADO", "Devolução", "devolucao_ton"),
    ("AMBIENTE", "Atacado (Geral)", "atacado_ton"),
    ("AMBIENTE", "Varejo (Geral)", "varejo_ton"),
    ("AMBIENTE", "Somente Terceiros", "terceiros_ton"),
    ("AMBIENTE", "Devolução", "devolucao_ton"),
    (None, None, None),
    ("RESFRIADO", "Paletes", "paletes"),
    ("CONGELADO", "Paletes", "paletes"),
    ("AMBIENTE", "Paletes", "paletes"),
]


def gerar_modelo_qlikview(ano_mes: str, dados_existentes: dict | None = None) -> bytes:
    """Gera o .xlsx modelo pro upload do QlikView de um mês — cabeçalho com
    os dias do mês (1..N) e uma linha por (temperatura, tipo local), na
    mesma ordem/rótulos de _QLIK_LINHAS_MODELO. Se `dados_existentes`
    vier (formato de _qlikview_do_mes: {data: {temperatura: {...}}}),
    pré-preenche os valores já salvos — baixar de novo pra ATUALIZAR um
    mês existente edita em cima do que já tem, em vez de retypar tudo."""
    import calendar
    import io as _io
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    ano, mes = (int(x) for x in ano_mes.split("-"))
    dias_no_mes = calendar.monthrange(ano, mes)[1]
    dados_existentes = dados_existentes or {}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "QlikView"
    ws.append(["Temperatura", "Tipo Local", *range(1, dias_no_mes + 1)])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")

    for temperatura, tipo_local, campo in _QLIK_LINHAS_MODELO:
        if temperatura is None:
            ws.append([])
            continue
        linha = [temperatura, tipo_local]
        for dia in range(1, dias_no_mes + 1):
            data_str = f"{ano_mes}-{dia:02d}"
            valor = ((dados_existentes.get(data_str) or {}).get(temperatura) or {}).get(campo)
            linha.append(valor)
        ws.append(linha)

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 18
    for col in range(3, dias_no_mes + 3):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 8
    ws.freeze_panes = "C2"

    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_planilha_qlikview(conteudo: bytes, ano_mes: str) -> dict:
    """Parser do modelo .xlsx (upload) — mesma ideia de parse_colagem_qlikview,
    mas lendo direto da planilha em vez de heurística sobre texto colado, e
    cobrindo as 3 temperaturas de uma vez (a Temperatura vem na própria
    linha, não num seletor separado da tela). Retorna o mesmo formato de
    parse_colagem_qlikview: {"ok","linhas","linhas_reconhecidas",
    "linhas_ignoradas","erros"}."""
    import calendar
    import io as _io
    import openpyxl

    try:
        ano, mes = (int(x) for x in ano_mes.split("-"))
        dias_no_mes = calendar.monthrange(ano, mes)[1]
    except (ValueError, AttributeError):
        return {"ok": False, "erros": [f"Mês/ano inválido: {ano_mes!r}."],
                "linhas_reconhecidas": [], "linhas_ignoradas": [], "linhas": []}

    try:
        wb = openpyxl.load_workbook(_io.BytesIO(conteudo), data_only=True, read_only=True)
        ws = wb.worksheets[0]
    except Exception as e:
        return {"ok": False, "erros": [f"Não consegui abrir o arquivo — é um .xlsx válido? ({e})"],
                "linhas_reconhecidas": [], "linhas_ignoradas": [], "linhas": []}

    linhas_iter = ws.iter_rows(values_only=True)
    try:
        cabecalho = next(linhas_iter)
    except StopIteration:
        return {"ok": False, "erros": ["Planilha vazia."],
                "linhas_reconhecidas": [], "linhas_ignoradas": [], "linhas": []}

    # colunas de dia a partir da 3ª coluna — para no primeiro valor que não
    # for um inteiro plausível de dia (cobre a célula "..." do modelo de
    # exemplo, ou qualquer coisa depois dos dias)
    mapa_col_dia = {}
    for i, cel in enumerate(cabecalho[2:], start=2):
        if isinstance(cel, (int, float)) and 1 <= int(cel) <= dias_no_mes:
            mapa_col_dia[i] = int(cel)
        else:
            break

    if not mapa_col_dia:
        return {"ok": False, "erros": [
            "Não achei colunas de dia do mês (1, 2, 3...) na primeira linha da planilha."],
            "linhas_reconhecidas": [], "linhas_ignoradas": [], "linhas": []}

    por_dia_temp: dict[tuple[str, str], dict] = {}
    reconhecidas, ignoradas = [], []
    for row in linhas_iter:
        if not row or not row[0]:
            continue
        temperatura = str(row[0]).strip().upper()
        tipo_local = str(row[1] or "").strip()
        if temperatura not in TEMPERATURAS or not tipo_local:
            continue
        rotulo_norm = _norm_col(tipo_local)
        if rotulo_norm in _QLIK_ROW_IGNORAR:
            continue
        campo = _QLIK_ROW_ALIASES.get(rotulo_norm)
        if not campo:
            if tipo_local not in ignoradas:
                ignoradas.append(tipo_local)
            continue
        if tipo_local not in reconhecidas:
            reconhecidas.append(tipo_local)
        for col_idx, dia in mapa_col_dia.items():
            if col_idx >= len(row):
                continue
            valor = row[col_idx]
            if valor is None or valor == "":
                continue
            try:
                valor_num = float(valor)
            except (TypeError, ValueError):
                continue
            data_str = f"{ano_mes}-{dia:02d}"
            chave = (data_str, temperatura)
            registro = por_dia_temp.setdefault(chave, {"data": data_str, "temperatura": temperatura})
            registro[campo] = int(valor_num) if campo == "paletes" else valor_num

    linhas_out = list(por_dia_temp.values())
    if not linhas_out:
        return {"ok": False, "erros": [
            "Nenhuma linha com Temperatura/Tipo Local reconhecidos tinha valor numérico "
            "nas colunas de dia."], "linhas_reconhecidas": reconhecidas,
            "linhas_ignoradas": ignoradas, "linhas": []}

    return {"ok": True, "linhas": linhas_out, "linhas_reconhecidas": sorted(set(reconhecidas)),
           "linhas_ignoradas": sorted(set(ignoradas)), "erros": []}


# ── Detalhamento por clique (23/09/2026) ─────────────────────────────
# Clicar num número do painel (FIFO, Vencido, faixa de vencimento,
# Endereçado/Sem endereço) abre a lista de produtos que formaram aquele
# número. As regras vivem AQUI, ao lado de calcular_metricas, para não
# existirem duas versões da mesma conta — qualquer divergência entre a
# lista e o total seria um problema de confiança no painel.
CATEGORIAS_DETALHE = ("fifo", "vencido", "faixa", "enderecado", "sem_endereco",
                      "bloqueio", "reservado", "estoque")


def detalhar_linhas(linhas: list[dict], categoria: str, locais_desconsiderar: set[str],
                    temperatura: str | None = None, faixa: str | None = None) -> list[dict]:
    """Linhas cruas que compõem um número do painel, já ordenadas pelo que
    vence primeiro. `faixa` é o rótulo da faixa de vencimento ("6 - 10")."""
    hoje = _dt.date.today()
    fora = {(l or "").strip() for l in (locais_desconsiderar or set())}
    faixa_limites = next(((dmin, dmax) for label, dmin, dmax in VENCIMENTO_BUCKETS
                          if label == faixa), None)
    out = []
    for r in linhas:
        temp = r["temperatura"]
        if temp not in TEMPERATURAS or r["local"] in fora:
            continue
        if temperatura and temp != temperatura:
            continue
        dias = (r["validade"] - hoje).days if r["validade"] else None
        vida_cat = _vida_categoria(r)
        conta_vida = r["tipo_produto"].strip().upper() != _TIPO_EXCLUIDO_VIDA
        sigla = r["reserva_sigla"].strip().upper()
        bloqueado = sigla == "BLOQUEIO" or bool(r["bloqueio_motivos"])

        if categoria == "fifo":
            entra = conta_vida and dias is not None and dias < _FIFO_DIAS_LIMITE
        elif categoria == "vencido":
            entra = conta_vida and vida_cat == "Vencido"
        elif categoria == "faixa":
            entra = (conta_vida and dias is not None and faixa_limites is not None
                     and faixa_limites[0] <= dias <= faixa_limites[1])
        elif categoria == "enderecado":
            entra = bool(r["endereçado"])
        elif categoria == "sem_endereco":
            entra = not r["endereçado"]
        elif categoria == "bloqueio":
            entra = bloqueado
        elif categoria == "reservado":
            entra = bool(sigla) and not bloqueado
        else:                                   # estoque: tudo da temperatura
            entra = True
        if not entra:
            continue

        out.append({
            "temperatura": temp,
            "local": r["local"],
            "codigo": r["codigo"],
            "descricao": r["nome"],
            "peso_kg": round(r["peso_liquido"], 3),
            "validade": r["validade"].isoformat() if r["validade"] else "",
            "dias_vencer": dias,
            "vida": vida_cat or "",
            "palete": r.get("palete", ""),
            "enderecado": bool(r["endereçado"]),
            "reserva": r["reserva_nome"] or sigla,
            "bloqueado": bloqueado,
        })
    # o que vence primeiro no topo; sem validade vai para o fim
    out.sort(key=lambda x: (x["dias_vencer"] is None, x["dias_vencer"], -x["peso_kg"]))
    return out
