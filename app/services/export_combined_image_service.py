from pathlib import Path
import textwrap
import tempfile
import math

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image

from app.utils.paths import get_output_path
from app.services.export_image_service import exportar_imagem as exportar_imagem_coligadas


HEADER_BG = "#17324D"
HEADER_FG = "#FFFFFF"
GRID = "#D4DADF"
ROW_BG = "#FFFFFF"
DETAIL_BG = "#F8FBFD"
GROUP_BORDER = "#C9D8C5"

C_NO_CD = "#C6E0B4"
C_SAME_DAY = "#FFE699"
C_NEXT_BUSINESS = "#FFC000"
C_FUTURE = "#F4CCCC"
C_FRONT = "#C00000"


COLS = [
    "Nome Redespacho",
    "Empresa Origem",
    "Placa",
    "Pedido Entrada",
    "Instrução Entrada",
    "KM Restante",
    "Tempo Estimado",
    "Estimativa Chegada",
    "Qtde NFs",
]

COL_WIDTHS = {
    "Nome Redespacho": 2.45,
    "Empresa Origem": 1.55,
    "Placa": 1.0,
    "Pedido Entrada": 1.15,
    "Instrução Entrada": 1.35,
    "KM Restante": 0.95,
    "Tempo Estimado": 1.05,
    "Estimativa Chegada": 1.45,
    "Qtde NFs": 0.85,
}

WRAP_WIDTH = {
    "Nome Redespacho": 30,
    "Empresa Origem": 18,
    "Placa": 12,
    "Pedido Entrada": 12,
    "Instrução Entrada": 14,
    "KM Restante": 10,
    "Tempo Estimado": 12,
    "Estimativa Chegada": 18,
    "Qtde NFs": 8,
}


def _safe_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def _wrap(value, width=18, max_lines=2):
    text = _safe_text(value).strip()
    if not text:
        return ""

    lines = textwrap.wrap(text, width=width, break_long_words=False, replace_whitespace=False)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, width - 3)] + "..."

    return "\n".join(lines)


def _wrap_unlimited(value, width=160):
    text = _safe_text(value).strip()
    if not text:
        return ""
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=False, replace_whitespace=False))


def _selected_only(df):
    if df is None or df.empty or "Selecionado" not in df.columns:
        return pd.DataFrame()
    return df[df["Selecionado"].astype(str).str.strip().str.lower().isin(["sim", "true", "1", "x", "selecionado"])].copy()


def _first(group, col):
    if col not in group.columns:
        return ""
    for value in group[col]:
        text = _safe_text(value).strip()
        if text and text.lower() != "nan":
            return value
    return ""


def _status_color(estimativa):
    est = _safe_text(estimativa).strip()
    if est == "Veículo no CD":
        return C_NO_CD, "#000000"
    if est == "Entregas na frente":
        return C_FRONT, "#FFFFFF"
    if est.startswith("Chega hoje") and "descarga" not in est:
        return C_SAME_DAY, "#000000"
    if "descarga" in est:
        return C_NEXT_BUSINESS, "#000000"
    if est.startswith("Chega "):
        return C_FUTURE, "#000000"
    return ROW_BG, "#000000"


def _draw_rect(ax, x, y, w, h, facecolor, edgecolor=GRID, lw=0.28):
    ax.add_patch(Rectangle((x, y), w, h, facecolor=facecolor, edgecolor=edgecolor, linewidth=lw))


def _draw_text(ax, x, y, w, h, text, fontsize=6.8, color="#111827", weight="normal", ha="center"):
    tx = x + w / 2 if ha == "center" else x + 0.05
    ax.text(
        tx,
        y + h / 2,
        text,
        ha=ha,
        va="center",
        fontsize=fontsize,
        color=color,
        weight=weight,
        linespacing=1.08,
    )


def _prepare_summary(df):
    df = _selected_only(df)
    if df.empty:
        return pd.DataFrame()

    rows = []
    for nome, group in df.groupby("Nome Redespacho", sort=False, dropna=False):
        nfs = [
            _safe_text(x).strip()
            for x in group.get("Formulário", pd.Series(dtype=str)).dropna()
            if _safe_text(x).strip() and _safe_text(x).strip().lower() != "nan"
        ]

        rows.append({
            "Nome Redespacho": _safe_text(nome).strip(),
            "Empresa Origem": _first(group, "Empresa Origem"),
            "Placa": _first(group, "Placa"),
            "Pedido Entrada": _first(group, "Pedido Entrada"),
            "Instrução Entrada": _first(group, "Instrução Entrada"),
            "KM Restante": _first(group, "KM Restante"),
            "Tempo Estimado": _first(group, "Tempo Estimado"),
            "Estimativa Chegada": _first(group, "Estimativa Chegada"),
            "Qtde NFs": len(nfs),
            "NFs": " / ".join(nfs),
        })

    return pd.DataFrame(rows)


def _detail_height(nf_text):
    wrapped = _wrap_unlimited(nf_text, width=155)
    lines = max(1, wrapped.count("\n") + 1)
    return 0.48 + (lines - 1) * 0.28


def exportar_imagem_redespacho(redespachos_df, output_path=None):
    if output_path is None:
        output_path = get_output_path("Painel_Redespacho.png")
    output_path = Path(output_path)

    df = _prepare_summary(redespachos_df)
    if df.empty:
        raise ValueError("Nenhum redespacho selecionado para exportar.")

    cols = [c for c in COLS if c in df.columns]
    col_widths = [COL_WIDTHS.get(c, 1.0) for c in cols]
    total_width = sum(col_widths)

    header_h = 0.85
    main_h = 0.84
    gap_h = 0.12

    detail_heights = []
    for _, row in df.iterrows():
        detail_text = f"Total NFs: {row.get('Qtde NFs', '')}  |  NFs: {row.get('NFs', '')}"
        detail_heights.append(_detail_height(detail_text))

    table_h = header_h + sum(main_h + detail_heights[i] + gap_h for i in range(len(df)))

    fig_width = max(18, total_width * 1.12)
    fig_height = max(4.0, min(table_h * 0.48 + 1.5, 22))

    fig = plt.figure(figsize=(fig_width, fig_height), facecolor="white")
    ax = fig.add_axes([0.015, 0.08, 0.97, 0.80])
    ax.set_xlim(0, total_width)
    ax.set_ylim(0, table_h)
    ax.axis("off")

    title_ax = fig.add_axes([0.015, 0.90, 0.97, 0.08])
    title_ax.axis("off")
    title_ax.text(0.5, 0.5, "REDESPACHOS SELECIONADOS", ha="center", va="center",
                  fontsize=14, weight="bold", color=HEADER_BG)

    x_positions = {}
    x = 0
    for col, w in zip(cols, col_widths):
        x_positions[col] = (x, w)
        x += w

    # Cabeçalho
    y = table_h - header_h
    for col in cols:
        x, w = x_positions[col]
        _draw_rect(ax, x, y, w, header_h, HEADER_BG, HEADER_BG, 0.35)
        _draw_text(ax, x, y, w, header_h, _wrap(col, 16, 2), fontsize=7.1, color=HEADER_FG, weight="bold")

    y -= main_h

    for row_idx, (_, row) in enumerate(df.iterrows()):
        # Linha principal
        for col in cols:
            x, w = x_positions[col]
            value = row.get(col, "")
            face = ROW_BG
            text_color = "#111827"
            weight = "normal"

            if col == "Estimativa Chegada":
                face, text_color = _status_color(value)
                if text_color == "#FFFFFF":
                    weight = "bold"

            _draw_rect(ax, x, y, w, main_h, face, GRID, 0.26)
            _draw_text(
                ax,
                x,
                y,
                w,
                main_h,
                _wrap(value, WRAP_WIDTH.get(col, 18), 2),
                fontsize=6.8,
                color=text_color,
                weight=weight,
            )

        # Linha de detalhe, abaixo, ocupando tudo
        detail_text = f"Total NFs: {row.get('Qtde NFs', '')}  |  NFs: {row.get('NFs', '')}"
        detail_display = _wrap_unlimited(detail_text, width=155)
        detail_h = detail_heights[row_idx]
        detail_y = y - detail_h

        _draw_rect(ax, 0, detail_y, total_width, detail_h, DETAIL_BG, GROUP_BORDER, 0.28)
        _draw_text(
            ax,
            0,
            detail_y,
            total_width,
            detail_h,
            detail_display,
            fontsize=6.25,
            color="#17324D",
            weight="bold",
            ha="left",
        )

        y = detail_y - gap_h - main_h

    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


def combinar_imagens(coligadas_path, redespacho_path, output_path=None):
    if output_path is None:
        output_path = get_output_path("Painel_Recebimento_Completo.png")
    output_path = Path(output_path)

    img1 = Image.open(coligadas_path).convert("RGB")
    img2 = Image.open(redespacho_path).convert("RGB")
    width = max(img1.width, img2.width)
    margin = 50
    height = img1.height + img2.height + margin
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(img1, ((width - img1.width) // 2, 0))
    canvas.paste(img2, ((width - img2.width) // 2, img1.height + margin))
    canvas.save(output_path)
    return output_path


def exportar_imagem_completa(coligadas_df, redespachos_df=None):
    selected = _selected_only(redespachos_df) if redespachos_df is not None else pd.DataFrame()
    if selected.empty:
        return exportar_imagem_coligadas(coligadas_df)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        col_path = tmp / "coligadas.png"
        red_path = tmp / "redespacho.png"
        exportar_imagem_coligadas(coligadas_df, str(col_path))
        exportar_imagem_redespacho(redespachos_df, str(red_path))
        return combinar_imagens(col_path, red_path)
