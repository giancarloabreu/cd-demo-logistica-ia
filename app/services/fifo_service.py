"""
Serviço do módulo "Devolução & Vencimento" (FIFO) — separa dois relatórios
que hoje o comercial monta manualmente numa planilha, a partir do mesmo TXT
Produto Paletizado já usado no Painel do Armazém (mesmo upload, sem reenviar
o arquivo — pedido do usuário 24/08/2026):

  - Devolução: tudo que está fisicamente num local de estoque classificado
    como "devolução" no cadastro de Locais de Estoque do Armazém, sem
    filtro de data (regra confirmada pelo usuário: "é tudo o que consta no
    local de estoque da devolução").
  - Próx. Vencimento: produtos cuja validade está dentro de uma janela de
    dias configurável pelo usuário (ex.: 15 ou 20), pra alimentar pedido de
    venda direcionado.

Cada linha do relatório final agrupa por (Tipo Estoque, Local, Reserva,
Produto): Peso Líquido somado, Dias Mín/Máx = menor/maior "dias de
produção" (data de referência − data de produção) entre os lotes do grupo,
com o Máx levando +5 dias de folga — regra confirmada pelo usuário
24/08/2026 comparando com a planilha real que ele já gera todo dia.
"""
from __future__ import annotations
import datetime as _dt

from app.services.armazem_service import _TIPO_EXCLUIDO_VIDA

TIPO_DEVOLUCAO = "Devolução"
TIPO_VENCIMENTO = "Próx. Vencimento"

# Janela de retenção pra persistir candidatos a Próx. Vencimento — generosa o
# bastante pra cobrir qualquer filtro de dias que o usuário escolher depois
# (15, 20, 30...) sem precisar reprocessar o TXT original só porque mudou o
# filtro num dia já salvo (o filtro de verdade é aplicado depois, em
# agrupar_relatorio, na hora de ver/exportar).
DIAS_RETENCAO_VENCIMENTO = 60


def filtrar_linhas_fifo(linhas: list[dict], locais_devolucao: set[str],
                        locais_desconsiderar: set[str], data_ref: _dt.date) -> list[dict]:
    """Do total de linhas do TXT (podem ser 30 mil+), separa só o que
    interessa pro relatório de Devolução + Próx. Vencimento — evita
    persistir o arquivo inteiro todo dia quando só uma fração é relevante
    aqui. Cada linha ganha um campo "tipo_estoque" (Devolução ou Próx.
    Vencimento — nunca os dois: local de devolução tem prioridade, não
    duplica a mesma linha física nos dois relatórios)."""
    candidatas = []
    for r in linhas:
        if r["local"] in locais_desconsiderar:
            continue
        # Mesmo critério de bloqueio de calcular_metricas (armazem_service):
        # sigla=="BLOQUEIO" (sinal antigo) OU algum motivo marcado com "X" no
        # grupo de colunas "Bloqueio" do TXT (sinal de verdade, achado
        # 24/08/2026). Guardado por linha pra poder ligar/desligar
        # "mostrar bloqueados" depois sem reprocessar o TXT (ver agrupar_relatorio).
        bloqueado = r["reserva_sigla"].strip().upper() == "BLOQUEIO" or bool(r["bloqueio_motivos"])
        if r["local"] in locais_devolucao:
            candidatas.append({**r, "tipo_estoque": TIPO_DEVOLUCAO, "bloqueado": bloqueado})
            continue
        if r["tipo_produto"].strip().upper() == _TIPO_EXCLUIDO_VIDA:
            continue  # mesma exclusão de Tempo de Vida no Armazém — não é um dado confiável de validade
        if not r["validade"]:
            continue
        dias_para_vencer = (r["validade"] - data_ref).days
        if dias_para_vencer <= DIAS_RETENCAO_VENCIMENTO:
            candidatas.append({**r, "tipo_estoque": TIPO_VENCIMENTO, "bloqueado": bloqueado})
    return candidatas


def agrupar_relatorio(linhas_persistidas: list[dict], data_ref: _dt.date, dias_filtro: int,
                      mostrar_bloqueados: bool = False, reservas_excluidas: frozenset = frozenset()) -> dict:
    """Agrupa as linhas persistidas em linhas de relatório (Tipo Estoque,
    Local, Cód./Desc. Reserva, Produto) — Devolução entra inteira, Próx.
    Vencimento só o que vence dentro de `dias_filtro` a partir de
    `data_ref` (a data do snapshot sendo visto, não necessariamente hoje —
    ver histórico no Calendário). `mostrar_bloqueados=False` (padrão) tira
    produto bloqueado do relatório — não pode ser vendido mesmo (pedido do
    usuário 24/08/2026). `reservas_excluidas` é um conjunto de
    reserva_codigo que o usuário marcou pra nunca aparecer (ex.: reserva de
    produto não localizado, que não dá pra vender de verdade)."""
    grupos: dict[tuple, dict] = {}
    for r in linhas_persistidas:
        if not mostrar_bloqueados and r.get("bloqueado"):
            continue
        if r["reserva_codigo"] in reservas_excluidas:
            continue
        if r["tipo_estoque"] == TIPO_VENCIMENTO:
            if not r["validade"]:
                continue
            dias_para_vencer = (r["validade"] - data_ref).days
            if dias_para_vencer > dias_filtro:
                continue
        empresa = r.get("empresa", "")
        # empresa entra na chave de agrupamento (redesenho Local×Empresa,
        # 29/08/2026) — sem isso, CDA e CDB com o mesmo código de produto
        # no mesmo local ficariam somadas numa linha só, misturando o
        # peso de duas empresas diferentes.
        chave = (r["tipo_estoque"], r["local"], r["reserva_codigo"], r["reserva_nome"],
                 r["produto_codigo"], r["produto_nome"], empresa)
        g = grupos.setdefault(chave, {
            "tipo_estoque": r["tipo_estoque"], "local": r["local"], "empresa": empresa,
            "reserva_codigo": r["reserva_codigo"], "reserva_nome": r["reserva_nome"],
            "produto_codigo": r["produto_codigo"], "produto_nome": r["produto_nome"],
            "peso_liquido": 0.0, "_producoes": [],
        })
        g["peso_liquido"] += r["peso_liquido"]
        if r["producao"]:
            g["_producoes"].append(r["producao"])

    linhas_devolucao, linhas_vencimento = [], []
    for g in grupos.values():
        dias = [(data_ref - p).days for p in g["_producoes"]]
        g["dias_min"] = min(dias) if dias else None
        g["dias_max"] = (max(dias) + 5) if dias else None
        del g["_producoes"]
        g["peso_liquido"] = round(g["peso_liquido"], 3)
        (linhas_devolucao if g["tipo_estoque"] == TIPO_DEVOLUCAO else linhas_vencimento).append(g)

    # Prioridade de venda: lote mais velho primeiro (maior Dias Mín) — não dá
    # pra reproduzir a ordem exata da planilha original só com um exemplo,
    # mas essa é a ordem mais útil pro objetivo do relatório (o que precisa
    # sair primeiro aparece no topo).
    chave_ordenacao = lambda g: (-(g["dias_min"] if g["dias_min"] is not None else -1), g["local"], g["produto_nome"])
    linhas_devolucao.sort(key=chave_ordenacao)
    linhas_vencimento.sort(key=chave_ordenacao)

    def total(lst):
        return round(sum(g["peso_liquido"] for g in lst), 3)

    return {
        "devolucao": linhas_devolucao, "devolucao_total": total(linhas_devolucao),
        "vencimento": linhas_vencimento, "vencimento_total": total(linhas_vencimento),
        "total_geral": round(total(linhas_devolucao) + total(linhas_vencimento), 3),
    }
