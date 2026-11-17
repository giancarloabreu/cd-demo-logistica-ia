from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation

from app.utils.paths import get_output_path
from app.utils.number_utils import parse_int_br, format_int_br


HEADER_FILL = "17324D"
HEADER_FONT = "FFFFFF"
BLUE_PLATE = "9DC3E6"
GREEN = "C6EFCE"
RED = "FFC7CE"
YELLOW = "FFEB9C"
GROUP_A = "FFFFFF"
GROUP_B = "F6FAFD"
TOTAL_FILL = "EAF4E3"
EST_NO_CD = "C6EFCE"
EST_HOJE = "FFF2CC"
EST_APOS_CORTE = "F4B183"
EST_ENTREGAS = "7F0000"
EST_FUTURA = "F4CCCC"


def _first_non_empty(ws, col, start_row, end_row):
    for row in range(start_row, end_row + 1):
        value = ws.cell(row=row, column=col).value
        if value is not None and str(value).strip():
            return value
    return ""


def exportar_excel(df: pd.DataFrame, output_path: str | None = None) -> Path:
    if output_path is None:
        output_path = get_output_path("Painel_Recebimento.xlsx")

    output_path = Path(output_path)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Painel")

    wb = load_workbook(output_path)
    ws = wb["Painel"]

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    thin = Side(style="thin", color="EEF2F6")
    medium_blue = Side(style="thin", color="D2DEE8")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.row_dimensions[1].height = 24

    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.font = Font(color=HEADER_FONT, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    for col in range(1, ws.max_column + 1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = 15

    for letter, width in {
        "A": 14, "B": 14, "C": 14, "D": 16, "E": 16, "F": 12, "G": 34,
        "H": 14, "I": 22, "J": 14, "K": 10, "L": 12, "M": 12,
        "N": 14, "O": 24, "P": 36, "Q": 16, "R": 18, "S": 22,
    }.items():
        ws.column_dimensions[letter].width = width

    max_row = max(ws.max_row, 2)

    headers = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
    col_prioridade = headers.get("Prioridade")
    col_placa = headers.get("Placa")
    col_empresa = headers.get("Empresa")
    col_regra = headers.get("Regra Data CD")
    col_frente = headers.get("Entregas Frente")
    col_estimativa = headers.get("Estimativa Chegada")

    if col_prioridade:
        dv_prioridade = DataValidation(type="list", formula1='"Alta,Média,Baixa,Não Definida"', allow_blank=False)
        ws.add_data_validation(dv_prioridade)
        dv_prioridade.add(f"{ws.cell(2, col_prioridade).coordinate}:{ws.cell(max_row, col_prioridade).coordinate}")

    if col_regra and col_frente:
        dv_sim_nao = DataValidation(type="list", formula1='"Sim,Não,Não Verificado"', allow_blank=False)
        ws.add_data_validation(dv_sim_nao)
        dv_sim_nao.add(f"{ws.cell(2, col_regra).coordinate}:{ws.cell(max_row, col_regra).coordinate}")
        dv_sim_nao.add(f"{ws.cell(2, col_frente).coordinate}:{ws.cell(max_row, col_frente).coordinate}")

    last_plate = None
    group_index = 0
    group_ranges = []
    group_start = None

    for row in range(2, max_row + 1):
        ws.row_dimensions[row].height = 22
        empresa = str(ws.cell(row=row, column=col_empresa).value or "") if col_empresa else ""
        placa = str(ws.cell(row=row, column=col_placa).value or "") if col_placa else ""
        is_total = empresa.endswith(" Total")

        if not is_total and placa:
            if placa != last_plate:
                if group_start is not None:
                    group_ranges.append((group_start, row - 1, last_plate))
                group_start = row
                group_index += 1
                last_plate = placa
                row_border = Border(left=thin, right=thin, top=medium_blue, bottom=thin)
            else:
                row_border = border
        else:
            if group_start is not None:
                group_ranges.append((group_start, row - 1, last_plate))
                group_start = None
                last_plate = None
            row_border = border

        group_fill = GROUP_B if group_index % 2 == 0 else GROUP_A
        if is_total:
            group_fill = TOTAL_FILL

        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = row_border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.fill = PatternFill("solid", fgColor=group_fill)
            if is_total:
                cell.font = Font(bold=True)

        prioridade = str(ws.cell(row=row, column=col_prioridade).value or "") if col_prioridade else ""
        regra = str(ws.cell(row=row, column=col_regra).value or "") if col_regra else ""
        frente = str(ws.cell(row=row, column=col_frente).value or "") if col_frente else ""

        if col_placa and regra == "Sim":
            ws.cell(row=row, column=col_placa).fill = PatternFill("solid", fgColor=BLUE_PLATE)
            ws.cell(row=row, column=col_placa).font = Font(color="1F4E78", bold=True)

        if col_prioridade:
            c = ws.cell(row=row, column=col_prioridade)
            if prioridade == "Alta":
                c.fill = PatternFill("solid", fgColor=RED)
            elif prioridade == "Média":
                c.fill = PatternFill("solid", fgColor=YELLOW)
            elif prioridade == "Baixa":
                c.fill = PatternFill("solid", fgColor=GREEN)

        if col_regra:
            c = ws.cell(row=row, column=col_regra)
            if regra == "Sim":
                c.fill = PatternFill("solid", fgColor=GREEN)
            elif regra == "Não":
                c.fill = PatternFill("solid", fgColor=RED)

        if col_frente:
            c = ws.cell(row=row, column=col_frente)
            if frente == "Sim":
                c.fill = PatternFill("solid", fgColor=RED)
            elif frente == "Não":
                c.fill = PatternFill("solid", fgColor=GREEN)

        if col_estimativa:
            c = ws.cell(row=row, column=col_estimativa)
            est = str(c.value or "")
            if est == "Veículo no CD":
                c.fill = PatternFill("solid", fgColor=EST_NO_CD)
            elif est.startswith("Chega hoje") and "descarga" not in est:
                c.fill = PatternFill("solid", fgColor=EST_HOJE)
            elif "descarga" in est:
                c.fill = PatternFill("solid", fgColor=EST_APOS_CORTE)
            elif est == "Entregas na frente":
                c.fill = PatternFill("solid", fgColor=EST_ENTREGAS)
                c.font = Font(color="FFFFFF", bold=True)
            elif est.startswith("Chega "):
                c.fill = PatternFill("solid", fgColor=EST_FUTURA)

    if group_start is not None:
        group_ranges.append((group_start, max_row, last_plate))

    # Mescla de verdade no Excel os campos operacionais por carreta.
    merge_fields = ["Prioridade", "Empresa", "Placa", "Data Faturamento", "KM Restante", "Tempo Estimado", "Estimativa Chegada", "Cidade Atual", "Observação", "Regra Data CD", "Entregas Frente", "Instrução Entrada"]
    merge_cols = [headers.get(field) for field in merge_fields if headers.get(field)]

    for start_row, end_row, _placa in group_ranges:
        if end_row <= start_row:
            continue

        for col in merge_cols:
            value = _first_non_empty(ws, col, start_row, end_row)
            ws.cell(row=start_row, column=col).value = value

            for row in range(start_row + 1, end_row + 1):
                ws.cell(row=row, column=col).value = None

            ws.merge_cells(
                start_row=start_row,
                start_column=col,
                end_row=end_row,
                end_column=col,
            )

            cell = ws.cell(row=start_row, column=col)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            if headers.get("Empresa") == col or headers.get("Placa") == col:
                cell.font = Font(bold=True, color="17324D")

            if headers.get("Data Faturamento") == col:
                cell.font = Font(color="4A5568")

            if headers.get("Prioridade") == col:
                if value == "Alta":
                    cell.fill = PatternFill("solid", fgColor=RED)
                elif value == "Média":
                    cell.fill = PatternFill("solid", fgColor=YELLOW)
                elif value == "Baixa":
                    cell.fill = PatternFill("solid", fgColor=GREEN)

            if headers.get("Estimativa Chegada") == col:
                est = str(value or "")
                if est == "Veículo no CD":
                    cell.fill = PatternFill("solid", fgColor=EST_NO_CD)
                elif est.startswith("Chega hoje") and "descarga" not in est:
                    cell.fill = PatternFill("solid", fgColor=EST_HOJE)
                elif "descarga" in est:
                    cell.fill = PatternFill("solid", fgColor=EST_APOS_CORTE)
                elif est == "Entregas na frente":
                    cell.fill = PatternFill("solid", fgColor=EST_ENTREGAS)
                    cell.font = Font(color="FFFFFF", bold=True)
                elif est.startswith("Chega "):
                    cell.fill = PatternFill("solid", fgColor=EST_FUTURA)

            if headers.get("Regra Data CD") == col:
                regra = str(value or "")
                if regra == "Sim":
                    cell.fill = PatternFill("solid", fgColor=GREEN)
                elif regra == "Não":
                    cell.fill = PatternFill("solid", fgColor=RED)

            if headers.get("Entregas Frente") == col:
                frente = str(value or "")
                if frente == "Sim":
                    cell.fill = PatternFill("solid", fgColor=RED)
                elif frente == "Não":
                    cell.fill = PatternFill("solid", fgColor=GREEN)

            if headers.get("Estimativa Chegada") == col:
                est = str(value or "")
                if est == "Veículo no CD":
                    cell.fill = PatternFill("solid", fgColor=EST_NO_CD)
                elif est.startswith("Chega hoje") and "descarga" not in est:
                    cell.fill = PatternFill("solid", fgColor=EST_HOJE)
                elif "descarga" in est:
                    cell.fill = PatternFill("solid", fgColor=EST_APOS_CORTE)
                elif est == "Entregas na frente":
                    cell.fill = PatternFill("solid", fgColor=EST_ENTREGAS)
                    cell.font = Font(color="FFFFFF", bold=True)
                elif est.startswith("Chega "):
                    cell.fill = PatternFill("solid", fgColor=EST_FUTURA)


    # Linha TOTAL GERAL no Excel
    headers = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
    col_empresa = headers.get("Empresa")
    col_placa = headers.get("Placa")
    col_qtd = headers.get("Quantidade")
    col_vol = headers.get("Volume")

    normal_rows = []
    for row in range(2, ws.max_row + 1):
        empresa_value = str(ws.cell(row=row, column=col_empresa).value or "") if col_empresa else ""
        if "Total" not in empresa_value:
            normal_rows.append(row)

    placas = set()
    if col_placa:
        for row in normal_rows:
            placa = str(ws.cell(row=row, column=col_placa).value or "").strip()
            if placa:
                placas.add(placa)

    total_peso = 0
    total_volumes = 0

    if col_qtd:
        for row in normal_rows:
            total_peso += parse_int_br(ws.cell(row=row, column=col_qtd).value) or 0

    if col_vol:
        for row in normal_rows:
            total_volumes += parse_int_br(ws.cell(row=row, column=col_vol).value) or 0

    total_row = ws.max_row + 1
    ws.cell(row=total_row, column=1).value = "TOTAL GERAL"
    ws.cell(row=total_row, column=2).value = f"Veículos: {format_int_br(len(placas))}"
    if col_qtd:
        ws.cell(row=total_row, column=col_qtd).value = format_int_br(total_peso)
    if col_vol:
        ws.cell(row=total_row, column=col_vol).value = format_int_br(total_volumes)

    for col in range(1, ws.max_column + 1):
        cell = ws.cell(row=total_row, column=col)
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    wb.save(output_path)
    return output_path
