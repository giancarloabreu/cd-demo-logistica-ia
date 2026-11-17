"""
Exporta painel de redespacho como PNG via Playwright.
Sem coluna de Estimativa Chegada na imagem (conforme solicitado).
"""
from __future__ import annotations
from datetime import datetime
import zoneinfo as _zi
_BRT = _zi.ZoneInfo('America/Sao_Paulo')
from pathlib import Path

import pandas as pd

from app.utils.paths import get_output_path
from app.utils.number_utils import format_int_br
from app.services.screenshot_service import html_to_png

# Sem "Estimativa Chegada" na imagem
COLS = [
    ("Placa",             "Placa",          88, "center"),
    ("Instrução Entrada", "Instrução",      108, "center"),
    ("KM Restante",       "KM",              54, "right"),
    ("Tempo Estimado",    "Tempo",           70, "center"),
    ("Qtd NFs",           "NFs",             46, "center"),
    ("NFs",               "Notas Fiscais",  380, "left"),
]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
  font-size:12px;line-height:1.35;
  background:linear-gradient(135deg,#e8f4fd 0%,#dce8f5 40%,#e4dff5 70%,#f0eafa 100%);
  padding:14px;
}
.ph{
  display:flex;align-items:center;justify-content:space-between;
  background:rgba(23,50,77,.95);border-radius:12px 12px 0 0;
  padding:10px 16px;color:#fff;
}
.ph h1{font-size:13px;font-weight:700}
.ph .ts{font-size:10px;color:rgba(255,255,255,.5)}
table{border-collapse:collapse;width:100%;background:rgba(255,255,255,.5)}
thead th{
  background:rgba(23,50,77,.88);color:#fff;font-weight:600;
  font-size:10px;padding:6px 6px;text-align:center;white-space:nowrap;
  border-right:1px solid rgba(255,255,255,.1);
}
tbody td{
  padding:6px 6px;border:1px solid rgba(205,218,230,.55);
  vertical-align:middle;color:#1c2a38;
}
.ra td{background:rgba(255,255,255,.75)}
.rb td{background:rgba(240,246,253,.75)}
.tot td{
  background:rgba(210,228,246,.9)!important;
  font-weight:700;color:#17467a;
  border-top:2px solid rgba(100,150,220,.4)!important;
}
.placa{font-weight:700;color:#17324d;text-align:center!important}
/* Fábrica expediu, falta faturar a instrução "virtual" — sem placa e sem
   instrução. Dizer isso é melhor que deixar a célula vazia. */
.badge-aguarda{display:inline-block;padding:2px 8px;border-radius:99px;
  background:rgba(230,160,30,.16);color:#8a5a00;font-weight:700;font-size:8.5px;
  line-height:1.25}
.badge{
  display:inline-block;padding:3px 14px;border-radius:99px;
  background:rgba(0,100,220,.85);color:#fff;
  font-weight:700;font-size:12px;letter-spacing:.4px;
}
.badge-empty{color:#8e8e93;font-style:italic}
.nfs{color:#4a5568;font-size:11px}
.footer{
  background:rgba(23,50,77,.95);border-radius:0 0 12px 12px;
  padding:10px 16px;color:#fff;
}
.ft{font-size:12px;font-weight:700}
</style>
"""

def _s(v):
    if v is None: return ""
    try:
        if pd.isna(v): return ""
    except: pass
    s = str(v).strip()
    return "" if s.lower() in ("nan","none") else s

def _first(grp, col):
    if col not in grp.columns: return ""
    for v in grp[col]:
        s = _s(v)
        if s: return s
    return ""

def _preparar(df):
    # Mesma regra da tela: sem placa, agrupa pela INSTRUÇÃO ORIGEM (a que a
    # planta usa para carregar — todas as NFs dela são o redespacho de um
    # veículo). Antes essas linhas eram descartadas por `if not placa` e
    # sumiam da imagem exportada.
    df = df.copy()
    df["_Card"] = [
        _s(r.get("Placa")) or ("AGUARDANDO:" + _s(r.get("Instrução Origem")))
        for _, r in df.iterrows()
    ]
    grupos = []
    for chave_card, grp in df.groupby("_Card", sort=False, dropna=False):
        chave_card = _s(chave_card)
        if not chave_card: continue
        aguardando = chave_card.startswith("AGUARDANDO:")
        placa = "Aguardando faturamento" if aguardando else chave_card
        fcol = next((c for c in ["Formulário","Formulario","NF"] if c in grp.columns), None)
        nfs  = [_s(v) for v in grp[fcol].dropna() if _s(v)] if fcol else []
        grupos.append({
            "Placa":             placa,
            "Aguardando":        aguardando,
            "Instrução Entrada": _first(grp, "Instrução Entrada"),
            "KM Restante":       _first(grp, "KM Restante"),
            "Tempo Estimado":    _first(grp, "Tempo Estimado"),
            "Qtd NFs":           str(len(nfs)),
            "NFs":               " / ".join(nfs),
        })
    return grupos

def exportar_imagem_redespacho(df: pd.DataFrame, output_path: str | None = None) -> Path:
    if output_path is None:
        output_path = get_output_path("Painel_Redespacho.png")
    output_path = Path(output_path)
    if df is None or df.empty:
        raise ValueError("DataFrame de redespacho vazio.")

    grupos = _preparar(df)
    if not grupos:
        raise ValueError("Nenhum grupo encontrado.")

    now       = datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")
    total_nfs = sum(int(g.get("Qtd NFs", 0) or 0) for g in grupos)
    thead     = "".join(f'<th style="width:{w}px;min-width:{w}px">{lbl}</th>' for _,lbl,w,_ in COLS)

    rows = ""
    for i, g in enumerate(grupos):
        rc    = "ra" if i % 2 == 0 else "rb"
        inst  = _s(g.get("Instrução Entrada", ""))
        badge = (f'<span class="badge">{inst}</span>' if inst
                 else ('<span class="badge-aguarda">Aguardando faturar Instrução de Entrada</span>'
                       if g.get("Aguardando") else '<span class="badge-empty">—</span>'))
        cells = ""
        for field, lbl, w, al in COLS:
            val = g.get(field, "")
            if field == "Placa":
                cells += f'<td class="placa">{val}</td>'
            elif field == "Instrução Entrada":
                cells += f'<td style="text-align:center">{badge}</td>'
            elif field == "NFs":
                cells += f'<td class="nfs">{val}</td>'
            else:
                cells += f'<td style="text-align:{al}">{_s(val)}</td>'
        rows += f'<tr class="{rc}">{cells}</tr>\n'

    # Total
    tot_cells = "".join(
        f'<td style="text-align:center">'
        f'{str(len(grupos))+" veículos" if f=="Placa" else str(total_nfs) if f=="Qtd NFs" else "Total: "+str(total_nfs)+" NF(s)" if f=="NFs" else ""}'
        f'</td>'
        for f,_,_,_ in COLS
    )
    rows += f'<tr class="tot">{tot_cells}</tr>\n'

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">{CSS}</head><body>
<div class="ph"><h1>PAINEL DE REDESPACHO — CD Demo</h1><span class="ts">Gerado: {now}</span></div>
<table><thead><tr>{thead}</tr></thead><tbody>{rows}</tbody></table>
<div class="footer">
  <div class="ft">TOTAL &nbsp;|&nbsp; {len(grupos)} veículo(s) &nbsp;|&nbsp; {total_nfs} NF(s) &nbsp;|&nbsp; Redespacho CD Demo</div>
</div></body></html>"""

    total_w = sum(w for _,_,w,_ in COLS) + 40
    return html_to_png(html, output_path, width=max(800, total_w))
