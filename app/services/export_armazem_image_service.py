"""
Exporta o dashboard do Painel do Armazém como PNG via Playwright — mesmo
padrão de app/services/export_image_service.py (HTML+CSS estático,
renderizado num Chromium headless). Espelha em Python a mesma lógica de
gráficos do front (web/templates/armazem.html: barra empilhada, pizza via
conic-gradient, mini-grids, tabela resumo) pra gerar a imagem sem depender
de JS/estado do navegador do usuário.
"""
from __future__ import annotations
from datetime import datetime, date
import zoneinfo as _zi
_BRT = _zi.ZoneInfo('America/Sao_Paulo')
from pathlib import Path

from app.utils.paths import get_output_path
from app.services.screenshot_service import htmls_to_pngs

TEMP_LABEL = {"RESFRIADO": "Resfriado", "CONGELADO": "Congelado", "AMBIENTE": "Ambiente"}
TEMP_COR = {"RESFRIADO": "#38BDF8", "CONGELADO": "#0369a1", "AMBIENTE": "#F59E0B"}
TEMPERATURAS = ["RESFRIADO", "CONGELADO", "AMBIENTE"]

CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;font-size:13px;color:#1a1a2e;
  background:linear-gradient(135deg,#eef2f7 0%,#e6ecf5 60%,#eaf0f8 100%);padding:16px}
.header{display:flex;align-items:center;gap:12px;background:rgba(23,50,77,.95);border-radius:12px;
  padding:12px 18px;color:#fff;margin-bottom:14px}
.header h1{font-size:16px}
.header .rotulo{background:rgba(255,255,255,.15);border-radius:20px;padding:4px 12px;font-size:12px;font-weight:700}
.header .ts{margin-left:auto;font-size:11px;color:rgba(255,255,255,.6)}
.kpis{display:flex;gap:10px;margin-bottom:14px}
.kpi{flex:1;background:rgba(255,255,255,.6);border:1px solid rgba(255,255,255,.28);border-radius:10px;
  padding:10px 12px;text-align:center}
.kpi .v{font-size:22px;font-weight:800}
.kpi .l{font-size:11px;color:rgba(26,26,46,.58);margin-top:2px}
.card{background:rgba(255,255,255,.62);border:1px solid rgba(255,255,255,.28);border-radius:16px;
  box-shadow:0 4px 24px rgba(0,0,0,.1);padding:14px 16px;margin-bottom:14px}
.card h3{margin:0 0 10px;font-size:12px;font-weight:700;color:rgba(26,26,46,.58);text-transform:uppercase;letter-spacing:.4px}
.dash-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:14px}
.dash-grid.cols-2{grid-template-columns:repeat(2,1fr)}
.mini-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.mini-card{border:1.5px solid rgba(0,0,0,.14);border-radius:12px;padding:6px 3px 4px;
  background:rgba(255,255,255,.55);box-shadow:0 1px 5px rgba(0,0,0,.06);overflow:hidden}
.mini-title{font-size:11px;font-weight:700;color:rgba(26,26,46,.58);text-align:center;margin-bottom:4px}
.barchart{display:flex;align-items:flex-end;gap:14px;height:130px;padding:6px 4px 0}
.barchart.mini{height:100px;gap:3px;padding:6px 0 0}
.bar-col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;min-width:0}
.bar-val{font-size:11px;font-weight:700;margin-bottom:3px;white-space:nowrap}
.barchart.mini .bar-val{font-size:8px;margin-bottom:2px}
.bar-fill{width:100%;border-radius:6px 6px 0 0;min-height:2px}
.bar-label{font-size:10.5px;color:rgba(26,26,46,.58);margin-top:5px;text-align:center}
.barchart.mini .bar-label{font-size:8px;white-space:nowrap}
.stack-col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}
.stack-bar{width:100%;display:flex;flex-direction:column-reverse;border-radius:6px 6px 0 0;overflow:hidden}
.stack-seg{width:100%;min-height:2px}
.stack-seg-val{font-size:10px;font-weight:700;color:#fff;text-align:center;line-height:1.5}
.stack-total-val{font-size:11px;font-weight:700;margin-bottom:3px}
.ocup-wrap{padding:6px 4px;margin-bottom:8px}
.ocup-bar-bg{background:rgba(0,0,0,.06);border-radius:10px;height:24px;overflow:hidden;position:relative}
.ocup-bar-fill{height:100%;background:#007AFF;border-radius:10px 0 0 10px}
.ocup-bar-fill.alta{background:#ff3b30}
.ocup-txt{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700}
.ocup-head{display:flex;justify-content:space-between;margin-bottom:4px;font-size:12px}
.pie-wrap{display:flex;flex-direction:row;align-items:center;gap:14px;justify-content:center;flex-wrap:wrap}
.pie{width:120px;height:120px;border-radius:50%;flex-shrink:0}
.pie-legend{display:flex;flex-direction:column;gap:6px;font-size:11px;min-width:100px}
.pie-legend-item{display:flex;align-items:center;gap:6px}
.pie-dot{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.pie-legend-val{margin-left:auto;font-weight:700}
.peso-medio-col{display:flex;flex-direction:column;gap:8px}
.peso-medio-box{background:rgba(255,255,255,.55);border:1.5px solid rgba(0,0,0,.14);border-radius:10px;
  padding:7px 12px;min-width:150px;text-align:center;box-shadow:0 1px 5px rgba(0,0,0,.06)}
.peso-medio-box .v{font-size:15px;font-weight:800}
.peso-medio-box .l{font-size:9.5px;color:rgba(26,26,46,.58);margin-top:2px;text-transform:uppercase;letter-spacing:.3px}
.fin-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:10px}
.fin-item{background:rgba(255,255,255,.55);border:1.5px solid rgba(0,0,0,.14);border-radius:10px;
  padding:9px 11px;text-align:center;box-shadow:0 1px 5px rgba(0,0,0,.06)}
.fin-item .v{font-size:16px;font-weight:800;white-space:nowrap}
.fin-item .l{font-size:10.5px;color:rgba(26,26,46,.58);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:11px}
thead th{text-align:left;padding:6px 8px;border-bottom:2px solid rgba(255,255,255,.4);white-space:nowrap}
tbody td{padding:6px 8px;border-bottom:1px solid rgba(0,0,0,.05);white-space:nowrap}
tbody tr:last-child td{font-weight:700}
/* Evolução Mensal (QlikView) — mesmo desenho de web/templates/armazem.html
   (ver bloco .evol-* lá), pra imagem exportada bater com a tela ao vivo
   (pedido do usuário 22/08/2026: "o qlikview não está exportando como
   imagem"). border-vis é cinza de verdade — as bordas com --glass-b
   (branco translúcido) ficavam quase invisíveis numa tabela. */
.evol-block{margin-bottom:18px;padding:16px;border-radius:16px;
  background:rgba(255,255,255,.42);border:1.5px solid rgba(0,0,0,.14);box-shadow:0 2px 10px rgba(0,0,0,.06)}
.evol-block:last-child{margin-bottom:0}
.evol-block-title{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;flex-wrap:wrap;gap:6px}
.evol-block-title b{font-size:13px}
.evol-cap-label{font-size:12px;font-weight:700;color:#65A30D}
.evol-mini-title{font-size:11px;font-weight:700;color:rgba(26,26,46,.58);text-align:center;margin:14px 0 2px}
.evol-legend{display:flex;gap:14px;margin:8px 0 2px;padding-top:8px;border-top:1px solid rgba(0,0,0,.14);
  flex-wrap:wrap;font-size:11px;color:rgba(26,26,46,.58)}
.evol-legend .dot{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:-1px}
.evol-wide-table{border-collapse:collapse;font-size:9.5px;margin-top:10px}
.evol-wide-table th,.evol-wide-table td{padding:4px 2px;text-align:center;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;border-bottom:1px solid rgba(0,0,0,.14)}
.evol-wide-table th:first-child,.evol-wide-table td:first-child{text-align:left;font-weight:600;
  white-space:normal;padding-left:4px;border-right:1px solid rgba(0,0,0,.14)}
.evol-wide-table thead th{font-weight:700;color:rgba(26,26,46,.58);font-size:10px}
.evol-wide-table tbody tr:last-child td{font-weight:700}
</style>
"""


def _n(v) -> str:
    try:
        return f"{float(v):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "0"


def _n0(v) -> str:
    try:
        return f"{round(float(v)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def _moeda(v) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _bar_chart_html(itens: list[dict], mini: bool = False) -> str:
    max_val = max([i.get("val", 0) or 0 for i in itens] or [1]) or 1
    altura_max = 90 if mini else 110
    cols = ""
    for i in itens:
        h = max(2, round((i.get("val", 0) or 0) / max_val * altura_max))
        cols += f"""<div class="bar-col">
          <div class="bar-val">{_n(i.get('val',0))}</div>
          <div class="bar-fill" style="height:{h}px;background:{i.get('cor','#007AFF')}"></div>
          <div class="bar-label">{i.get('label','')}</div>
        </div>"""
    return f'<div class="barchart{" mini" if mini else ""}">{cols}</div>'


def _stacked_bar_chart_html(itens: list[dict]) -> str:
    max_val = max([i.get("total", 0) or 0 for i in itens] or [1]) or 1
    cols = ""
    for i in itens:
        segs = ""
        empilhado = len(i["segments"]) > 1  # ver mesma lógica em armazem.html renderStackedBarChart
        for s in i["segments"]:
            h = max(2, round((s.get("value", 0) or 0) / max_val * 110))
            val_html = f'<div class="stack-seg-val">{_n(s["value"])}</div>' if (empilhado and h > 16) else ""
            segs += f'<div class="stack-seg" style="height:{h}px;background:{s["cor"]}">{val_html}</div>'
        cols += f"""<div class="stack-col">
          <div class="stack-total-val">{_n(i.get('total',0))}</div>
          <div class="stack-bar">{segs}</div>
          <div class="bar-label">{i.get('label','')}</div>
        </div>"""
    # Legenda das séries nomeadas (o verde de "Terceiros", por exemplo) — a
    # mesma da tela: quem recebe a imagem por e-mail não adivinha a cor.
    vistos = {}
    for i in itens:
        for s in i["segments"]:
            if s.get("label") and s["label"] not in vistos:
                vistos[s["label"]] = s["cor"]
    legenda = ""
    if vistos:
        chips = "".join(
            f'<span style="margin:0 8px"><i style="display:inline-block;width:10px;height:10px;'
            f'border-radius:2px;background:{cor};margin-right:5px"></i>{nome}</span>'
            for nome, cor in vistos.items())
        legenda = ('<div style="text-align:center;margin-top:6px;font-size:11px;color:#64748b">'
                   f'{chips}</div>')
    return f'<div class="barchart">{cols}</div>{legenda}'


def _pie_chart_html(slices: list[dict], vazio: bool = False) -> str:
    peso_total = sum(s.get("peso", 0) for s in slices) or 1
    acc = 0
    stops = []
    for s in slices:
        de = acc / peso_total * 360
        acc += s.get("peso", 0)
        ate = acc / peso_total * 360
        stops.append(f"{s['cor']} {de:.2f}deg {ate:.2f}deg")
    bg = "rgba(0,0,0,.08)" if vazio else f"conic-gradient({', '.join(stops)})"
    legenda = "".join(
        f'<div class="pie-legend-item"><span class="pie-dot" style="background:{s["cor"]}"></span>'
        f'{s["label"]}<span class="pie-legend-val">{s["texto"]}</span></div>'
        for s in slices
    )
    return f"""<div class="pie-wrap">
      <div class="pie-legend">{legenda}</div>
      <div class="pie" style="background:{bg}"></div>
    </div>"""


def _ocupacao_html(m: dict) -> str:
    # mesma lógica de web/templates/armazem.html renderOcupacao — barra
    # horizontal, rótulo "% | ton" centralizado, vermelho acima de 90%
    # (revertido pro modelo original a pedido do usuário 22/08/2026)
    out = ""
    for temp in TEMPERATURAS:
        t = m["temperaturas"][temp]
        if t.get("compartilha_capacidade"):
            out += f"""<div class="ocup-wrap"><div class="ocup-head"><b>{TEMP_LABEL[temp]}</b>
              <span>{_n(t['estoque_fisico_ton'])} ton — compartilha a câmara do Resfriado</span></div></div>"""
            continue
        cap = t.get("capacidade_ton", 0) or 0
        pct = t.get("ocupacao_pct")
        if not cap:
            out += f"""<div class="ocup-wrap"><div class="ocup-head"><b>{TEMP_LABEL[temp]}</b>
              <span>Sem capacidade cadastrada — {_n(t['estoque_fisico_ton'])} ton</span></div></div>"""
            continue
        w = min(100, pct or 0)
        out += f"""<div class="ocup-wrap">
          <div class="ocup-head"><b>{TEMP_LABEL[temp]}</b><span>{_n(t['estoque_fisico_ton'])} / {_n(cap)} ton</span></div>
          <div class="ocup-bar-bg"><div class="ocup-bar-fill{' alta' if (pct or 0)>90 else ''}" style="width:{w}%"></div>
            <div class="ocup-txt">{_n(pct)}% | {_n(t['estoque_fisico_ton'])} ton</div></div>
        </div>"""
    return out


def _nz(v) -> str:
    return _n(v) if v else "—"


def _tabela_resumo_html(m: dict) -> str:
    cols = ["Temperatura", "Estoque", "Capacidade", "Ocupação", "Bloqueio", "Reservado",
           "1/3", "2/3", "3/3", "Fifo", "Vencido", "Endereçado", "Sem Endereço", "% Endereçado"]
    linhas = []
    for temp in TEMPERATURAS:
        t = m["temperaturas"][temp]
        ocup = "compartilha" if t.get("compartilha_capacidade") else \
               ("—" if t.get("ocupacao_pct") is None else _n(t["ocupacao_pct"]) + "%")
        linhas.append([
            TEMP_LABEL[temp], _nz(t["estoque_fisico_ton"]), _n(t["capacidade_ton"]) if t["capacidade_ton"] else "—",
            ocup, _nz(t["bloqueio_ton"]), _nz(t["reservado_ton"]),
            _nz(t["vida"]["1/3"]), _nz(t["vida"]["2/3"]), _nz(t["vida"]["3/3"]),
            _nz(t["fifo_ton"]), _nz(t["vida"]["Vencido"]), _nz(t["endereçado_ton"]), _nz(t["sem_endereco_ton"]),
            "—" if t.get("pct_enderecado") is None else _n(t["pct_enderecado"]) + "%",
        ])
    linhas.append([
        "Total", _nz(m["total_ton"]), "—", "—",
        _nz(sum(m["temperaturas"][t]["bloqueio_ton"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["reservado_ton"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["vida"]["1/3"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["vida"]["2/3"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["vida"]["3/3"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["fifo_ton"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["vida"]["Vencido"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["endereçado_ton"] for t in TEMPERATURAS)),
        _nz(sum(m["temperaturas"][t]["sem_endereco_ton"] for t in TEMPERATURAS)), "—",
    ])
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in linha) + "</tr>" for linha in linhas)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# ── Evolução Mensal (QlikView) — porta pra Python o mesmo desenho de
# web/templates/armazem.html (renderEvolucaoBlocos e funções _evol* daquele
# arquivo), pra imagem exportada bater com a tela ao vivo (pedido do
# usuário 22/08/2026). Larguras/paddings batem exatamente com o viewport
# fixo de html_to_png (width=1100): 1100 − 32 (padding do body) − 32
# (padding do .evol-block) = 1036px de área útil pro gráfico.
EVOL_TEMPS = ["TOTAL", "RESFRIADO", "CONGELADO", "AMBIENTE"]
EVOL_LABELS = {"atacado_ton": "Atacado", "varejo_ton": "Varejo", "devolucao_ton": "Devolução",
              "total": "Total", "terceiros_ton": "Terceiros"}
EVOL_ROTULOS_TABELA = [
    ("atacado_ton", "Atacado (Geral)"), ("varejo_ton", "Varejo (Geral)"),
    ("terceiros_ton", "Somente Terceiros"), ("devolucao_ton", "Devolução"),
    ("paletes", "Paletes"), ("total", "Total"), ("capacidade_ton", "Capacidade"),
]
DIA_SEMANA_ABREV = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"]
EVOL_LARGURA_GRAFICO = 1036
MES_LABEL = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def _evol_dados_por_dia(dias: dict, ano_mes: str, temp: str, dias_no_mes: int) -> list[dict]:
    somaveis = ["atacado_ton", "varejo_ton", "devolucao_ton", "terceiros_ton", "paletes"]
    out = []
    for dia in range(1, dias_no_mes + 1):
        por_temp = dias.get(f"{ano_mes}-{dia:02d}")
        v, tem_dado = None, False
        if por_temp:
            if temp == "TOTAL":
                temps = [t for t in ("RESFRIADO", "CONGELADO", "AMBIENTE") if por_temp.get(t)]
                if temps:
                    tem_dado = True
                    v = {c: sum((por_temp.get(t) or {}).get(c) or 0 for t in temps) for c in somaveis}
            elif por_temp.get(temp):
                tem_dado = True
                v = por_temp[temp]
        v = v or {}
        atacado = v.get("atacado_ton") or 0
        varejo = v.get("varejo_ton") or 0
        devolucao = v.get("devolucao_ton") or 0
        out.append({
            "dia": dia, "tem_dado": tem_dado,
            "atacado_ton": atacado, "varejo_ton": varejo, "devolucao_ton": devolucao,
            "terceiros_ton": v.get("terceiros_ton") or 0, "paletes": v.get("paletes"),
            # Total = Atacado+Varejo+Devolução, SEM Terceiros — confirmado
            # contra os números reais da planilha original (19/08/2026)
            "total": atacado + varejo + devolucao,
        })
    return out


def _evol_capacidade(capacidades: dict, ambiente_propria: bool, temp: str) -> float | None:
    """Capacidade pra linha de referência do gráfico — vem do cadastro de
    "Capacidade e Contrato" (mesma fonte usada em Ocupação Câmaras/
    Financeiro), NÃO do campo opcional "Capacidade" que o QlikView às vezes
    traz colado por dia. Achado do usuário 24/08/2026: "a capacidade está
    vinculada na janela de capacidade e contrato, mas o qlikview não está
    puxando essa informação" — o QlikView quase nunca traz esse campo
    preenchido (não é um dado que ele reporta de verdade), então a linha
    verde nunca aparecia mesmo com a capacidade cadastrada. `capacidades`
    já vem resolvida pela vigência certa (ver _ref_data_para_mes em
    web/armazem.py)."""
    if temp == "TOTAL":
        total = (capacidades.get("RESFRIADO") or 0) + (capacidades.get("CONGELADO") or 0)
        if ambiente_propria:
            total += capacidades.get("AMBIENTE") or 0
        return total or None
    if temp == "AMBIENTE" and not ambiente_propria:
        return None  # compartilha a câmara do Resfriado, não tem capacidade própria
    return capacidades.get(temp) or None


def _evol_picos(pontos: list[dict], modelo_pagamento: str) -> set[int]:
    com_dado = [p for p in pontos if p["tem_dado"]]
    if not com_dado:
        return set()
    def _max_dia(lista):
        return max(lista, key=lambda p: p["total"])["dia"]
    if modelo_pagamento == "quinzenal":
        q1 = [p for p in com_dado if p["dia"] <= 15]
        q2 = [p for p in com_dado if p["dia"] > 15]
        picos = []
        if q1:
            picos.append(_max_dia(q1))
        if q2:
            picos.append(_max_dia(q2))
        return set(picos)
    return {_max_dia(com_dado)}


def _evol_label_box(x: float, y: float, texto: str, cor: str, cor_texto: str, destaque: bool = False) -> str:
    w = max(16, len(texto) * 6.5 + (12 if destaque else 8))
    h = 17 if destaque else 14
    stroke = ' stroke="#1a1a2e" stroke-width="1.2"' if destaque else ""
    fs = 11 if destaque else 9.5
    ty = y - 3 - h / 2 + (4 if destaque else 3.5)
    return (f'<g><rect x="{x-w/2:.1f}" y="{y-h-3:.1f}" width="{w:.1f}" height="{h}" rx="3" fill="{cor}"{stroke}/>'
            f'<text x="{x:.1f}" y="{ty:.1f}" font-size="{fs}" font-weight="800" fill="{cor_texto}" text-anchor="middle">{texto}</text></g>')


def _evol_main_chart_svg(pontos: list[dict], cap: float | None, dias_no_mes: int, picos: set[int]) -> str:
    series = [
        {"campo": "atacado_ton", "cor": "#17324d", "cor_texto": "#fff"},
        {"campo": "varejo_ton", "cor": "#DC2626", "cor_texto": "#fff"},
        {"campo": "devolucao_ton", "cor": "#A78BFA", "cor_texto": "#1a1a2e"},
        {"campo": "total", "cor": "#F97316", "cor_texto": "#fff"},
    ]
    todos_valores = [(p[s["campo"]] or 0) for p in pontos for s in series] + ([cap] if cap else [])
    max_v = max([1] + todos_valores) * 1.1
    w, h, pad_l, pad_t, pad_b = EVOL_LARGURA_GRAFICO, 230, 40, 18, 22
    plot_w, plot_h = w - pad_l - 10, h - pad_t - pad_b

    def x_of(dia): return pad_l + ((dia - 1) / ((dias_no_mes - 1) or 1)) * plot_w
    def y_of(v): return pad_t + plot_h - (v / max_v) * plot_h

    grid = ""
    for frac in (0, 0.25, 0.5, 0.75, 1):
        v = max_v * frac
        y = y_of(v)
        grid += (f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-10}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
                 f'<text x="{pad_l-6}" y="{y+3:.1f}" font-size="9" fill="#8a8a9a" text-anchor="end">{_n0(v)}</text>')
    x_ticks = "".join(
        f'<text x="{x_of(dia):.1f}" y="{h-6}" font-size="8.5" fill="#8a8a9a" text-anchor="middle">{dia:02d}</text>'
        for dia in range(1, dias_no_mes + 1)
    )
    cap_linha = ""
    if cap:
        y = y_of(cap)
        cap_linha = f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-10}" y2="{y:.1f}" stroke="#65A30D" stroke-width="2"/>'
    linhas = ""
    for s in series:
        d = " ".join(f'{"M" if i == 0 else "L"}{x_of(p["dia"]):.1f},{y_of(p[s["campo"]] or 0):.1f}'
                     for i, p in enumerate(pontos))
        linhas += f'<path d="{d}" fill="none" stroke="{s["cor"]}" stroke-width="2"/>'
    pontos_svg = ""
    for s in series:
        for p in pontos:
            valor = p[s["campo"]] or 0
            cx, cy = x_of(p["dia"]), y_of(valor)
            eh_pico = s["campo"] == "total" and p["dia"] in picos
            raio = 4.5 if eh_pico else 2.5
            pontos_svg += f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{raio}" fill="{s["cor"]}"/>'
            if not p["tem_dado"] or (not valor and not eh_pico):
                continue
            texto = _n(valor) if eh_pico else _n0(valor)
            pontos_svg += _evol_label_box(cx, cy, texto, s["cor"], s["cor_texto"], eh_pico)

    legenda = "".join(
        f'<span><span class="dot" style="background:{s["cor"]}"></span>{EVOL_LABELS[s["campo"]]}</span>'
        for s in series
    )
    if cap:
        legenda += '<span><span class="dot" style="background:#65A30D"></span>Capacidade</span>'
    return f"""<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="width:100%;height:230px;display:block">
      {grid}{cap_linha}{linhas}{pontos_svg}{x_ticks}
    </svg>
    <div class="evol-legend">{legenda}</div>"""


def _evol_mini_chart_svg(pontos: list[dict], dias_no_mes: int) -> str:
    if not any(p["tem_dado"] and p["terceiros_ton"] for p in pontos):
        return ""
    cor = "#06B6D4"
    w, h, pad_l, pad_t, pad_b = EVOL_LARGURA_GRAFICO, 140, 40, 10, 20
    plot_w, plot_h = w - pad_l - 10, h - pad_t - pad_b
    max_v = max([1] + [p["terceiros_ton"] or 0 for p in pontos]) * 1.15

    def x_of(dia): return pad_l + ((dia - 1) / ((dias_no_mes - 1) or 1)) * plot_w
    def y_of(v): return pad_t + plot_h - (v / max_v) * plot_h

    grid = ""
    for frac in (0, 0.5, 1):
        v = max_v * frac
        y = y_of(v)
        grid += (f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-10}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
                 f'<text x="{pad_l-6}" y="{y+3:.1f}" font-size="9" fill="#8a8a9a" text-anchor="end">{_n0(v)}</text>')
    x_ticks = "".join(
        f'<text x="{x_of(dia):.1f}" y="{h-5}" font-size="8.5" fill="#8a8a9a" text-anchor="middle">{dia:02d}</text>'
        for dia in range(1, dias_no_mes + 1)
    )
    d = " ".join(f'{"M" if i == 0 else "L"}{x_of(p["dia"]):.1f},{y_of(p["terceiros_ton"] or 0):.1f}'
                for i, p in enumerate(pontos))
    pontos_svg = ""
    for p in pontos:
        valor = p["terceiros_ton"] or 0
        cx, cy = x_of(p["dia"]), y_of(valor)
        pontos_svg += f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.5" fill="{cor}"/>'
        if not p["tem_dado"] or not valor:
            continue
        pontos_svg += _evol_label_box(cx, cy, _n0(valor), cor, "#1a1a2e", False)
    return f"""<div class="evol-mini-title">Terceiros</div>
    <svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="width:100%;height:140px;display:block">
      {grid}<path d="{d}" fill="none" stroke="{cor}" stroke-width="2"/>{pontos_svg}{x_ticks}
    </svg>"""


def _evol_wide_table_html(pontos: list[dict], ano: int, mes: int, cap: float | None) -> str:
    # weekday(): segunda=0..domingo=6 — DIA_SEMANA_ABREV começa em domingo,
    # por isso o +1 % 7 (mesma conversão de web/templates/armazem.html,
    # que usa Date.getDay() do JS, já domingo=0)
    dia_semana_html = "".join(
        f'<th>{DIA_SEMANA_ABREV[(date(ano, mes, p["dia"]).weekday()+1)%7]}</th>'
        for p in pontos
    )
    dia_num_html = "".join(f'<th>{p["dia"]:02d}</th>' for p in pontos)

    linhas = ""
    for campo, label in EVOL_ROTULOS_TABELA:
        def get_val(p, campo=campo):
            return (cap if p["tem_dado"] else None) if campo == "capacidade_ton" else p[campo]
        com_dado = [p for p in pontos if p["tem_dado"] and get_val(p) is not None]
        media = (sum(get_val(p) for p in com_dado) / len(com_dado)) if com_dado else None
        celulas = ""
        for p in pontos:
            v = get_val(p)
            celulas += "<td></td>" if (not p["tem_dado"] or v is None or not v) else f"<td>{_n0(v)}</td>"
        linhas += f"<tr><td>{label}</td>{celulas}<td><b>{_n0(media) if media else ''}</b></td></tr>"

    # A imagem exportada é um screenshot estático (sem scroll de verdade) —
    # diferente da versão web, que rola horizontalmente dentro do
    # overflow-x:auto. Sem largura fixa a tabela cresce pelo conteúdo (32
    # colunas de dia + rótulo + total) e passa dos 1036px úteis, cortando a
    # última coluna fora da imagem (achado 24/08/2026, ao revisar as
    # imagens geradas pro pedido de quebrar em várias). table-layout:fixed
    # com colgroup força a tabela inteira a caber exatamente na largura do
    # gráfico acima dela.
    n_cols_dado = len(pontos) + 1  # dias + "Total Média"
    label_w = 118
    col_w = (EVOL_LARGURA_GRAFICO - label_w) / n_cols_dado
    colgroup = f'<col style="width:{label_w}px">' + f'<col style="width:{col_w:.1f}px">' * n_cols_dado

    return f"""<table class="evol-wide-table" style="table-layout:fixed;width:{EVOL_LARGURA_GRAFICO}px">
      <colgroup>{colgroup}</colgroup>
      <thead>
        <tr><th></th>{dia_semana_html}<th></th></tr>
        <tr><th>Temperatura</th>{dia_num_html}<th>Méd</th></tr>
      </thead>
      <tbody>{linhas}</tbody>
    </table>"""


def _evol_blocos_lista(dias: dict, ano_mes: str, modelo_pagamento: str,
                       capacidades: dict | None = None, ambiente_propria: bool = False) -> list[dict]:
    """Um item por temperatura com dado no mês (TOTAL/RESFRIADO/CONGELADO/
    AMBIENTE) — cada um vira uma imagem exportada separada (pedido do
    usuário 24/08/2026: imagem única ficava pequena demais ao colar num
    e-mail; quebrar em várias mantém o texto legível sem precisar reduzir)."""
    if not dias:
        return []
    import calendar
    ano, mes = (int(x) for x in ano_mes.split("-"))
    dias_no_mes = calendar.monthrange(ano, mes)[1]
    capacidades = capacidades or {}

    itens = []
    for temp in EVOL_TEMPS:
        pontos = _evol_dados_por_dia(dias, ano_mes, temp, dias_no_mes)
        if not any(p["tem_dado"] for p in pontos):
            continue
        cap = _evol_capacidade(capacidades, ambiente_propria, temp)
        label = "Total" if temp == "TOTAL" else TEMP_LABEL[temp]
        picos = _evol_picos(pontos, modelo_pagamento)
        cap_html = f'<span class="evol-cap-label">Capacidade {_n0(cap)} Ton.</span>' if cap else ""
        bloco_html = f"""<div class="evol-block">
          <div class="evol-block-title"><b>Estoque Médio {label} — {MES_LABEL[mes]}/{ano}</b>{cap_html}</div>
          {_evol_main_chart_svg(pontos, cap, dias_no_mes, picos)}
          {_evol_mini_chart_svg(pontos, dias_no_mes)}
          {_evol_wide_table_html(pontos, ano, mes, cap)}
        </div>"""
        itens.append({"temp": temp, "label": label, "html": bloco_html})
    return itens


def _build_html_painel(m: dict, rotulo: str = "") -> str:
    now = datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")

    estoque_itens = []
    for temp in TEMPERATURAS:
        t = m["temperaturas"][temp]
        segs = [{"value": t["estoque_ton"], "cor": TEMP_COR[temp]}]
        if t.get("importados_ton", 0) > 0:
            segs.append({"value": t["importados_ton"], "cor": "#1a7a36", "label": "Terceiros"})
        estoque_itens.append({"label": TEMP_LABEL[temp], "segments": segs, "total": t["estoque_fisico_ton"]})
    estoque_itens.append({"label": "Total", "segments": [{"value": m["total_ton"], "cor": "#17324d"}], "total": m["total_ton"]})

    pie_end_slices = [{"label": TEMP_LABEL[t], "cor": TEMP_COR[t], "peso": 1,
                       "texto": "—" if m["temperaturas"][t]["pct_enderecado"] is None
                                else _n(m["temperaturas"][t]["pct_enderecado"]) + "%"}
                      for t in TEMPERATURAS]

    bloqueio = sum(m["temperaturas"][t]["bloqueio_ton"] for t in TEMPERATURAS)
    reservado = sum(m["temperaturas"][t]["reservado_ton"] for t in TEMPERATURAS)
    total_rb = bloqueio + reservado
    pie_rb_slices = [
        {"label": "Bloqueio", "cor": "#DC2626", "peso": (bloqueio / total_rb) if total_rb else 0.5, "texto": _n(bloqueio) + " ton"},
        {"label": "Reservado", "cor": "#F97316", "peso": (reservado / total_rb) if total_rb else 0.5, "texto": _n(reservado) + " ton"},
    ]

    f = m.get("financeiro") or {}
    financeiro_html = ""
    if f.get("valor_contrato_ton"):
        financeiro_html = f"""<p style="margin:0 0 6px;color:rgba(26,26,46,.58);font-size:11px">
            Capacidade total contratada: {_n(f['capacidade_total_ton'])} ton</p>
          <div class="fin-grid">
            <div class="fin-item"><div class="v">{_moeda(f['r_previsto'])}</div><div class="l">R$ Previsto</div></div>
            <div class="fin-item"><div class="v">{_moeda(f['r_por_ton_previsto'])}</div><div class="l">R$/Ton Contratado</div></div>
            <div class="fin-item"><div class="v">{_moeda(f['r_por_ton_realizado'])}</div><div class="l">R$/Ton Realizado</div></div>
          </div>"""
    else:
        financeiro_html = '<p style="margin:0;color:rgba(26,26,46,.58);font-size:11px">Sem contrato cadastrado.</p>'

    mini_vida = "".join(
        f'<div class="mini-card"><div class="mini-title">{TEMP_LABEL[t]}</div>' +
        _bar_chart_html([
            {"label": "1/3", "val": m["temperaturas"][t]["vida"]["1/3"], "cor": "#38BDF8"},
            {"label": "2/3", "val": m["temperaturas"][t]["vida"]["2/3"], "cor": "#FB923C"},
            {"label": "3/3", "val": m["temperaturas"][t]["vida"]["3/3"], "cor": "#F87171"},
            {"label": "Venc.", "val": m["temperaturas"][t]["vida"]["Vencido"], "cor": "#7C1D2F"},
        ], mini=True) + "</div>"
        for t in TEMPERATURAS
    )
    mini_venc = "".join(
        f'<div class="mini-card"><div class="mini-title">{TEMP_LABEL[t]}</div>' +
        _bar_chart_html([
            {"label": label, "val": val, "cor": "#DC2626"}
            for label, val in m["temperaturas"][t]["vencimento_buckets"].items()
        ], mini=True) + "</div>"
        for t in TEMPERATURAS
    )

    # Peso médio/pallet (kg) — Estoque(ton)*1000/Paletes, Total + por
    # temperatura (só Resfriado/Congelado, mesmo recorte do pedido do
    # usuário 22/08/2026 — Ambiente fica de fora, estoque baixo demais pra
    # essa média fazer sentido)
    def _peso_medio_kg(ton, paletes):
        return (ton * 1000 / paletes) if paletes else None
    # BRUTO desde 01/09/2026 (pedido do usuário) — só aqui; o resto do
    # painel continua no líquido. `.get` com fallback pro líquido evita
    # quebrar caso a métrica venha de um cache gravado antes da mudança.
    peso_total = _peso_medio_kg(m.get("total_bruto_ton") or m["total_ton"], m["total_paletes"])
    peso_resf = _peso_medio_kg(
        m["temperaturas"]["RESFRIADO"].get("estoque_bruto_ton") or m["temperaturas"]["RESFRIADO"]["estoque_fisico_ton"],
        m["temperaturas"]["RESFRIADO"]["paletes"])
    peso_cong = _peso_medio_kg(
        m["temperaturas"]["CONGELADO"].get("estoque_bruto_ton") or m["temperaturas"]["CONGELADO"]["estoque_fisico_ton"],
        m["temperaturas"]["CONGELADO"]["paletes"])
    peso_medio_html = f"""<div class="peso-medio-col">
      <div class="peso-medio-box"><div class="v">{_n(peso_total) if peso_total is not None else '—'} kg</div><div class="l">Peso médio/pallet Total</div></div>
      <div class="peso-medio-box"><div class="v">{_n(peso_resf) if peso_resf is not None else '—'} kg</div><div class="l">Peso médio/pallet Resfriado</div></div>
      <div class="peso-medio-box"><div class="v">{_n(peso_cong) if peso_cong is not None else '—'} kg</div><div class="l">Peso médio/pallet Congelado</div></div>
    </div>"""

    rotulo_html = f'<span class="rotulo">{rotulo}</span>' if rotulo else ""

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">{CSS}</head><body>
<div class="header"><h1>🏭 PAINEL DO ARMAZÉM</h1>{rotulo_html}<span class="ts">Gerado: {now}</span></div>
<div class="kpis">
  <div class="kpi"><div class="v">{_n(m['total_ton'])} ton</div><div class="l">Estoque Total</div></div>
  <div class="kpi"><div class="v">{_n(m['total_paletes'])}</div><div class="l">Paletes</div></div>
  <div class="kpi"><div class="v">{_n(m['importados_ton'])} ton</div><div class="l">Terceiros</div></div>
  <div class="kpi"><div class="v">{_n(peso_total) if peso_total is not None else '—'} kg</div><div class="l">Peso médio/pallet</div></div>
</div>
<div class="dash-grid">
  <div class="card"><h3>Estoque Total (ton)</h3>{_stacked_bar_chart_html(estoque_itens)}</div>
  <div class="card"><h3>Ocupação Câmaras</h3>{_ocupacao_html(m)}</div>
  <div class="card"><h3>Financeiro</h3>{financeiro_html}</div>
</div>
<div class="dash-grid cols-2">
  <div class="card"><h3>% Endereçado</h3><div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
    {_pie_chart_html(pie_end_slices)}{peso_medio_html}</div></div>
  <div class="card"><h3>Reserva / Bloqueio</h3>{_pie_chart_html(pie_rb_slices, vazio=(total_rb==0))}</div>
</div>
<div class="dash-grid cols-2">
  <div class="card"><h3>Tempo de Vida</h3><div class="mini-grid">{mini_vida}</div></div>
  <div class="card"><h3>Estoque Por Vencimento</h3><div class="mini-grid">{mini_venc}</div></div>
</div>
<div class="card"><h3>Resumo por Temperatura</h3>{_tabela_resumo_html(m)}</div>
</body></html>"""


def _build_html_evol_bloco(bloco_html: str, rotulo: str, mes_label: str) -> str:
    """Uma única imagem por temperatura da Evolução Mensal (QlikView) — ver
    _evol_blocos_lista. Cabeçalho reduzido (sem KPIs) só pra dar contexto de
    quem/quando gerou quando a imagem for colada isolada num e-mail."""
    now = datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")
    rotulo_html = f'<span class="rotulo">{rotulo}</span>' if rotulo else ""
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">{CSS}</head><body>
<div class="header"><h1>🏭 EVOLUÇÃO MENSAL (QLIKVIEW) — {mes_label}</h1>{rotulo_html}<span class="ts">Gerado: {now}</span></div>
{bloco_html}
</body></html>"""


def exportar_imagens(metricas: dict, rotulo: str = "", out_dir: str | Path | None = None,
                     prefixo: str = "Painel_Armazem", qlikview_dias: dict | None = None,
                     qlikview_ano_mes: str = "", modelo_pagamento: str = "mensal",
                     capacidades: dict | None = None, ambiente_propria: bool = False) -> list[dict]:
    """Gera o painel como VÁRIAS imagens (uma pro painel principal — KPIs
    até Resumo por Temperatura — e uma por temperatura com dado na
    Evolução Mensal do QlikView) em vez de uma imagem única gigante.
    Pedido do usuário 24/08/2026: a imagem combinada ficava pequena/ilegível
    ao colar num e-mail (o cliente de e-mail encolhe pra caber na largura),
    e resolução mais alta (device_scale_factor=2) evita ficar granulada.
    Retorna [{"label": ..., "path": Path}, ...]."""
    out_dir = Path(out_dir) if out_dir else Path(get_output_path(f"{prefixo}.png")).parent
    resultados, itens = _montar_visao(metricas, rotulo, out_dir, prefixo, qlikview_dias,
                                      qlikview_ano_mes, modelo_pagamento, capacidades, ambiente_propria)
    # Um Chromium só pra todas as imagens (não um por imagem) — ver
    # htmls_to_pngs, achado 24/08/2026: separar em várias imagens tinha
    # deixado a exportação bem mais lenta (~9s cada, abrindo browser toda
    # vez).
    htmls_to_pngs(itens, width=1100, device_scale_factor=2)
    return resultados


def _montar_visao(metricas: dict, rotulo: str, out_dir: Path, prefixo: str,
                  qlikview_dias: dict | None, qlikview_ano_mes: str, modelo_pagamento: str,
                  capacidades: dict | None, ambiente_propria: bool) -> tuple[list[dict], list[tuple]]:
    """(imagens da visão, pares html→arquivo) — separado de exportar_imagens
    para várias visões caberem num Chromium só (ver exportar_imagens_visoes)."""
    resultados = [{"label": "Painel", "path": out_dir / f"{prefixo}_painel.png"}]
    itens = [(_build_html_painel(metricas, rotulo), resultados[0]["path"])]

    if qlikview_ano_mes:
        ano, mes = (int(x) for x in qlikview_ano_mes.split("-"))
        mes_label = f"{MES_LABEL[mes]}/{ano}"
        for item in _evol_blocos_lista(qlikview_dias or {}, qlikview_ano_mes, modelo_pagamento,
                                       capacidades, ambiente_propria):
            slug = item["temp"].lower()
            path = out_dir / f"{prefixo}_qlikview_{slug}.png"
            nome = "Estoque Total" if item["temp"] == "TOTAL" else item["label"]
            resultados.append({"label": f"QlikView — {nome}", "path": path})
            itens.append((_build_html_evol_bloco(item["html"], rotulo, mes_label), path))
    return resultados, itens


def exportar_imagens_visoes(visoes: list[dict], out_dir: str | Path | None = None,
                            prefixo: str = "Painel_Armazem") -> list[dict]:
    """Várias visões (consolidada, CDA, CDB) numa tacada, um Chromium só.

    Cada visão é um dict com: chave, titulo, metricas, rotulo, qlikview_dias,
    qlikview_ano_mes, modelo_pagamento, capacidades, ambiente_propria.
    O usuário manda um e-mail por visão (23/09/2026), então cada uma vira um
    grupo de imagens próprio, com o mesmo tamanho das demais.
    """
    out_dir = Path(out_dir) if out_dir else Path(get_output_path(f"{prefixo}.png")).parent
    grupos, todos_itens = [], []
    for v in visoes:
        pref = f"{prefixo}_{v['chave']}"
        imagens, itens = _montar_visao(v["metricas"], v.get("rotulo", ""), out_dir, pref,
                                       v.get("qlikview_dias"), v.get("qlikview_ano_mes", ""),
                                       v.get("modelo_pagamento", "mensal"), v.get("capacidades"),
                                       v.get("ambiente_propria", False))
        grupos.append({"chave": v["chave"], "titulo": v["titulo"], "imagens": imagens})
        todos_itens.extend(itens)
    htmls_to_pngs(todos_itens, width=1100, device_scale_factor=2)
    return grupos
