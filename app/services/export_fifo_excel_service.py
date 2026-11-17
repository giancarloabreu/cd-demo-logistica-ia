"""
Exporta o relatório de Devolução + Próx. Vencimento (FIFO) em .xlsx, no
layout da planilha que o comercial já recebe hoje por e-mail pra montar
pedido de venda (Tipo Estoque | Local Estoque | Cód. Reserva | Desc.
Reserva | Produto | Dias Mín. | Dias Máx. | Peso Líquido, com subtotal por
seção e Total Geral no final) — ver app/services/fifo_service.py pra
como as linhas são agrupadas.
"""
from __future__ import annotations
import io
from datetime import date

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from app.services.fifo_service import TIPO_DEVOLUCAO, TIPO_VENCIMENTO

COLS_BASE = ["Tipo Estoque", "Local Estoque", "Cód. Reserva", "Desc. Reserva",
            "Produto", "Dias Mín.", "Dias Máx.", "Peso Líquido"]

_COR_HEADER = "1F4E78"
_COR_DEVOLUCAO = "DDEBF7"
_COR_VENCIMENTO = "FFFFFF"
_COR_SUBTOTAL = "B4C7E7"
_COR_TOTAL_GERAL = "8EA9DB"
_BORDA = Border(bottom=Side(style="thin", color="D0D5DD"))


def _linha_grupo(ws, g: dict, cor: str, n_cols: int, multi_empresa: bool):
    produto = f'{g["produto_codigo"]} {g["produto_nome"]}'.strip()
    valores = [g["tipo_estoque"], g["local"]]
    if multi_empresa:
        valores.append(g.get("empresa") or None)
    valores += [g["reserva_codigo"] or None, g["reserva_nome"] or None,
                produto, g["dias_min"], g["dias_max"], round(g["peso_liquido"])]
    ws.append(valores)
    row = ws.max_row
    fill = PatternFill("solid", fgColor=cor)
    col_dias_min = n_cols - 2
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = fill
        cell.border = _BORDA
        if col >= col_dias_min:
            cell.alignment = Alignment(horizontal="center")


def _linha_subtotal(ws, rotulo: str, peso_total: float, n_cols: int):
    valores = [rotulo] + [None] * (n_cols - 2) + [round(peso_total)]
    ws.append(valores)
    row = ws.max_row
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=n_cols - 1)
    fill = PatternFill("solid", fgColor=_COR_SUBTOTAL)
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = fill
        cell.font = Font(bold=True)
    ws.cell(row=row, column=n_cols).alignment = Alignment(horizontal="center")


def exportar_excel(relatorio: dict, data_ref: date) -> bytes:
    # Coluna Empresa só entra quando o dia tem dado de 2+ empresas de
    # verdade (ex.: CDA+CDB) — pra maioria dos CDs a planilha sai
    # idêntica à de sempre, no mesmo layout que o comercial já recebe
    # (redesenho Local×Empresa, 29/08/2026).
    todas_linhas = relatorio["devolucao"] + relatorio["vencimento"]
    multi_empresa = len({g.get("empresa") for g in todas_linhas if g.get("empresa")}) > 1
    cols = COLS_BASE[:2] + (["Empresa"] if multi_empresa else []) + COLS_BASE[2:]
    n_cols = len(cols)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Devolução e Vencimento"

    ws.append([f"Devolução & Próx. Vencimento — {data_ref.strftime('%d/%m/%Y')}"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    titulo = ws.cell(row=1, column=1)
    titulo.font = Font(bold=True, size=13)
    ws.append([])

    ws.append(cols)
    header_fill = PatternFill("solid", fgColor=_COR_HEADER)
    col_centralizada = n_cols - 3
    for cell in ws[3]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center" if cell.column > col_centralizada else "left")

    if relatorio["devolucao"]:
        for g in relatorio["devolucao"]:
            _linha_grupo(ws, g, _COR_DEVOLUCAO, n_cols, multi_empresa)
        _linha_subtotal(ws, f"{TIPO_DEVOLUCAO} Total", relatorio["devolucao_total"], n_cols)

    if relatorio["vencimento"]:
        for g in relatorio["vencimento"]:
            _linha_grupo(ws, g, _COR_VENCIMENTO, n_cols, multi_empresa)
        _linha_subtotal(ws, f"{TIPO_VENCIMENTO} Total", relatorio["vencimento_total"], n_cols)

    ws.append(["Total Geral"] + [None] * (n_cols - 2) + [round(relatorio["total_geral"])])
    row = ws.max_row
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=n_cols - 1)
    fill = PatternFill("solid", fgColor=_COR_TOTAL_GERAL)
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = fill
        cell.font = Font(bold=True, size=12)
    ws.cell(row=row, column=n_cols).alignment = Alignment(horizontal="center")

    larguras_base = [16, 26, 12, 20, 46, 10, 10, 13]
    larguras = larguras_base[:2] + ([14] if multi_empresa else []) + larguras_base[2:]
    for i, w in enumerate(larguras, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws.freeze_panes = "A4"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
