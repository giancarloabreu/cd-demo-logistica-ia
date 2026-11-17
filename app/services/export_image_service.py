"""
Exporta painel de coligadas como PNG de cards via Playwright.
Visual idêntico ao navegador — Liquid Glass, fontes Inter.
"""
from __future__ import annotations
from datetime import datetime
import zoneinfo as _zi
_BRT = _zi.ZoneInfo('America/Sao_Paulo')
from pathlib import Path

import pandas as pd

from app.utils.paths import get_output_path
from app.utils.number_utils import parse_int_br, format_int_br
from app.services.screenshot_service import html_to_png

STATUS_TAG = {
    "no_cd":            ("est-no-cd",  "No CD"),
    "no_cd_no_horario": ("est-no-cd",  "No CD"),
    "chega_no_horario": ("est-hoje",   None),
    "chega_apos_corte": ("est-corte",  None),
    "chegada_futura":   ("est-futura", None),
    "entregas_frente":  ("est-entregas","Entregas na frente"),
}
STATUS_CARD = {
    "no_cd":"st-no-cd","no_cd_no_horario":"st-no-cd",
    "chega_no_horario":"st-hoje","chega_apos_corte":"st-corte",
    "chegada_futura":"st-futura","entregas_frente":"st-entregas",
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Inter',-apple-system,sans-serif;font-size:12px;line-height:1.35;
  background:linear-gradient(135deg,#e8f4fd 0%,#dce8f5 40%,#e4dff5 70%,#f0eafa 100%);
  padding:14px;
}
.ph{display:flex;align-items:center;justify-content:space-between;
  background:rgba(23,50,77,.95);border-radius:12px 12px 0 0;
  padding:10px 16px;color:#fff;margin-bottom:12px;border-radius:12px}
.ph h1{font-size:14px;font-weight:700}.ph .ts{font-size:10px;color:rgba(255,255,255,.5)}

.cards-grid{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:12px;width:100%}

/* Cards de REDESPACHO — mesmo formato visual das coligadas, num painel
   próprio. Estas regras foram perdidas junto com a remoção da etiqueta
   lateral e as NFs viraram uma linha só, vazando do card (05/09/2026). */
.red-inst{display:inline-block;padding:3px 12px;border-radius:99px;
  background:rgba(0,100,220,.85);color:#fff;font-weight:700;font-size:11px}
.red-aguarda{display:inline-block;padding:3px 10px;border-radius:99px;
  background:rgba(230,160,30,.16);color:#8a5a00;font-weight:700;font-size:9.5px}
.red-linha{display:flex;justify-content:space-between;gap:10px;margin:8px 0 6px}
.red-lbl{font-size:8.5px;text-transform:uppercase;letter-spacing:.5px;
  color:rgba(26,26,46,.45);font-weight:700;margin-bottom:2px}
.red-val{font-size:11px;font-weight:600;color:#1c2a38}
/* NF + PEDIDO DE ENTRADA (filial) em 4 colunas — uma carreta passa de 30
   notas e em coluna única o card virava uma tira ilegível. */
.red-nfs{column-count:4;column-gap:8px;font-size:8.5px;color:#41506b;
  line-height:1.65;margin-top:2px}
.red-nfs .rnf{display:block;break-inside:avoid;white-space:nowrap}
.red-nfs .rnf b{font-weight:600;color:#1c2a38}
.red-nfs .rnf s{text-decoration:none;color:rgba(26,26,46,.3);margin:0 2px}
.red-nfs .rnf i{font-style:normal;color:rgba(26,26,46,.5)}
/* Feriado no caminho da carreta, logo abaixo da estimativa */
.card-feriado{font-size:9px;font-weight:700;color:#8a4800;
  background:rgba(255,159,10,.12);border:1px solid rgba(255,159,10,.28);
  border-radius:7px;padding:2px 7px;margin-bottom:6px;display:inline-block}
/* Descarga concluída fica apagada também na imagem */
.col-card.card-concluida{opacity:.5;filter:grayscale(35%)}

.col-card{
  background:rgba(255,255,255,0.62);
  border:1px solid rgba(255,255,255,0.28);border-radius:14px;
  padding:13px 16px;width:370px;
  box-shadow:0 4px 20px rgba(0,0,0,0.11);
  display:flex;flex-direction:column;gap:0;
}
.st-no-cd   {border-top:3px solid #22C55E}
.st-hoje    {border-top:3px solid #38BDF8}
.st-corte   {border-top:3px solid #FB923C}
.st-futura  {border-top:3px solid #F87171}
.st-entregas{border-top:3px solid #7C1D2F;background:rgba(124,29,47,.06)!important}
.st-sem-inst{border-left:3px solid rgba(230,160,30,.8)}

.card-head{display:flex;align-items:center;gap:7px;margin-bottom:9px}
.card-placa{font-size:13px;font-weight:700;letter-spacing:.4px;
  background:rgba(23,50,77,.08);padding:3px 9px;border-radius:7px;color:#17324d}
.card-emp-group{display:flex;align-items:center;gap:5px;flex:1;min-width:0;overflow:hidden}
.card-emp{font-size:11px;color:rgba(26,26,46,.55);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-km{font-size:12px;font-weight:700;color:#1c2a38;white-space:nowrap;display:flex;align-items:center;gap:5px}
.cd-badge{display:inline-block;padding:1px 7px;border-radius:99px;font-size:9px;font-weight:700;
  letter-spacing:.3px;background:rgba(23,50,77,.1);color:#17324d;border:1px solid rgba(23,50,77,.18)}
.cd-badges{display:inline-flex;flex-direction:column;gap:2px;vertical-align:middle;align-items:stretch}
.cd-badges .cd-badge{text-align:center}

.card-est{margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:6px}
.card-entfrente-txt{flex:1;font-size:11px;color:#7C1D2F;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
.card-telemetria{display:flex;align-items:center;gap:6px;font-size:11px;white-space:nowrap}
.telem-temp{font-weight:700;padding:1px 7px;border-radius:99px}
.telem-temp.temp-ok{background:rgba(34,197,94,.18);color:#15803d}
.telem-temp.temp-alerta{background:#DC2626;color:#fff}
.telem-temp.temp-neutro{background:rgba(0,0,0,.06);color:#8e8e93}
.est-tag{display:inline-block;padding:2px 7px;border-radius:99px;font-size:10px;font-weight:600}
.est-no-cd   {background:rgba(34,197,94,.18);color:#15803d}
.est-hoje    {background:rgba(56,189,248,.18);color:#0369a1}
.est-corte   {background:rgba(251,146,60,.2);color:#c2410c}
.est-futura  {background:rgba(248,113,113,.18);color:#b91c1c}
.est-entregas{background:#7C1D2F;color:#fff;font-weight:700}
.est-sem     {background:rgba(0,0,0,.05);color:#8e8e93}

.card-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px 12px;margin-bottom:9px}
.card-field{display:flex;flex-direction:column;gap:1px}
.cf-label{font-size:9px;font-weight:600;color:rgba(26,26,46,.48);text-transform:uppercase;letter-spacing:.4px}
.cf-val{font-size:11px;color:#1c2a38;line-height:1.3}
.regra-sim{color:#1a7a36;font-weight:700}
.regra-nao{color:#c0392b;font-weight:700}
.muted{color:#8e8e93}

.card-instrucao{padding:6px 9px;border-radius:8px;margin-bottom:7px;
  background:rgba(0,122,255,.07);border:1px solid rgba(0,122,255,.18);
  font-size:12px;color:#1c2a38}
.card-instrucao strong{color:#007AFF;font-size:14px}
.card-sem-inst{padding:5px 9px;border-radius:8px;margin-bottom:7px;
  background:rgba(255,159,10,.1);border:1px solid rgba(255,159,10,.25);
  font-size:10px;color:#8a4800;font-style:italic}

.card-nfs{font-size:11px;color:#1c2a38;
  padding:5px 0;border-top:1px solid rgba(255,255,255,.35);line-height:1.6}
.nf-ped{color:#555;font-size:10px}
.card-fat-date{font-size:10px;color:#4a5568;margin-top:4px;opacity:.8}

.card-obs{font-size:10px;color:#4a5568;margin-top:6px;
  padding-top:5px;border-top:1px solid rgba(255,255,255,.35);
  font-style:italic;line-height:1.35}
.card-obs.has-obs{font-style:normal;color:#1c2a38}
.card-foot{margin-top:7px;padding-top:6px;border-top:1px solid rgba(0,0,0,.08);display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.card-foot-right{display:flex;gap:6px;align-items:center;margin-left:auto}
.card-fonte-logo{height:18px;width:auto;max-width:72px;object-fit:contain;flex:none}
.card-pill{font-size:11px;font-weight:700;background:rgba(0,0,0,.06);padding:2px 8px;border-radius:99px;color:#333}
.card-locais{font-size:10px;color:#666;margin-top:3px;line-height:1.4}
.card-locais strong{color:#333;font-weight:600}
.card-temp{display:inline-flex;align-items:center;gap:3px;font-size:11px;font-weight:700;padding:2px 9px;border-radius:99px}
.temp-congelado{background:#dbeafe;color:#1d4ed8;border:1px solid #93c5fd}
.temp-resfriado{background:#fff7ed;color:#c2410c;border:1px solid #fdba74}
.temp-ambiente{background:#dcfce7;color:#15803d;border:1px solid #86efac}

.legend-bar{
  background:rgba(255,255,255,.72);border-radius:10px;
  padding:8px 14px;margin-bottom:10px;
  border:1px solid rgba(200,215,230,.6);
  display:flex;align-items:center;gap:6px;
}
.legend-title{font-size:10px;font-weight:700;color:#17324d;
  margin-right:6px;white-space:nowrap;letter-spacing:.3px}
.footer{background:rgba(23,50,77,.95);border-radius:12px;
  padding:10px 16px;color:#fff;margin-top:4px}
.ft{font-size:12px;font-weight:700}
.legend{display:flex;gap:13px;align-items:center;flex-wrap:wrap;
  font-size:10px;color:#2c3e50}
.leg{display:flex;align-items:center;gap:5px;font-weight:500}
.ld{width:12px;height:12px;border-radius:3px;
  border:1px solid rgba(0,0,0,.15);flex-shrink:0}
</style>
"""

# Logos das fontes de rastreio embutidas como data URI — o HTML da exportação
# é injetado direto no Chromium (set_content), sem servidor por trás, então
# `src="/static/img/..."` não resolve e a logo simplesmente não aparecia no
# PNG (diferente do card no navegador, que busca do Flask). Cache em módulo:
# lê cada arquivo uma única vez por processo.
_LOGOS_B64: dict = {}


# Cores fixas só para as unidades que o pessoal já reconhece; qualquer outra
# ganha cor gerada a partir da própria sigla. Empresas novas aparecem a
# qualquer momento e não dá para depender de alguém editar CSS antes.
_CORES_UNIDADE = {
    "cda": "background:rgba(0,122,255,.12);color:#0055cc;border-color:rgba(0,122,255,.25)",
    "dmg": "background:rgba(52,199,89,.13);color:#1a7a36;border-color:rgba(52,199,89,.28)",
    "bau": "background:rgba(255,159,10,.13);color:#8a4800;border-color:rgba(255,159,10,.28)",
    "alfa": "background:rgba(175,82,222,.12);color:#6b2fa0;border-color:rgba(175,82,222,.25)",
}


def _cor_unidade(sigla: str) -> str:
    """Mesmo hash do corUnidade() no index.html — se mudar um, mude o outro,
    senão a etiqueta da tela e a da imagem exportada saem de cores diferentes
    para a mesma empresa."""
    k = "".join(c for c in str(sigla or "").lower() if c.isalnum())
    if k in _CORES_UNIDADE:
        return _CORES_UNIDADE[k]
    h = 0
    for c in k:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    t = (h * 137) % 360
    return f"background:hsl({t} 85% 93%);color:hsl({t} 70% 30%);border-color:hsl({t} 60% 78%)"


def _km_num(v) -> float:
    """KM como número pra ordenar; sem valor vai pro fim, como na tela."""
    t = str(v or "").replace(".", "").strip()
    try:
        return float(t) if t else 999999.0
    except ValueError:
        return 999999.0


def _etiquetas_unidade(valor) -> str:
    """Uma etiqueta por unidade, EMPILHADAS. Uma carreta pode entregar em várias
    empresas do mesmo local (CDA e CDB dividem endereço) e aí o card é um só —
    o consolidador manda "CDA · CDB"."""
    unidades = [u.strip() for u in str(valor or "").split("·") if u.strip()]
    if not unidades:
        return ""
    marcas = [f'<span class="cd-badge" style="{_cor_unidade(u)}">{_s(u)}</span>' for u in unidades]
    if len(marcas) == 1:
        return marcas[0]
    return '<span class="cd-badges">' + "".join(marcas) + "</span>"


def _logo_data_uri(nome_arquivo: str) -> str:
    if nome_arquivo not in _LOGOS_B64:
        try:
            import base64
            p = Path(__file__).parent.parent.parent / "web" / "static" / "img" / nome_arquivo
            _LOGOS_B64[nome_arquivo] = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
        except Exception:
            _LOGOS_B64[nome_arquivo] = ""
    return _LOGOS_B64[nome_arquivo]

def _logo_fonte_html(fonte) -> str:
    """Mesma lógica de renderFonteRastreioLogo do index.html."""
    f = str(fonte or "").strip()
    arquivo = {"OpenTech": "opentech.png", "Ravex": "ravex.png"}.get(f)
    if not arquivo:
        return ""
    uri = _logo_data_uri(arquivo)
    if not uri:
        return ""
    return f'<img class="card-fonte-logo" src="{uri}" alt="{f}">'

def _s(v):
    if v is None: return ""
    try:
        if pd.isna(v): return ""
    except: pass
    s = str(v).strip()
    return "" if s.lower() in ("nan","none") else s

def _datas_faturamento_html(nfs_rows):
    datas = list(dict.fromkeys(_s(row.get("Data Faturamento", "")) for row in nfs_rows if _s(row.get("Data Faturamento", ""))))
    if not datas:
        return ""
    return f'<div class="card-fat-date">&#128197; Faturado em: {", ".join(datas)}</div>'

def _locais_unicos(nfs_rows):
    seen, locais = set(), []
    for row in nfs_rows:
        raw = str(row.get("Local Estoque","")).strip()
        if not raw or raw.lower() == "nan": continue
        for part in raw.replace("/", ",").split(","):
            loc = part.strip()
            if loc and loc.upper() not in seen:
                seen.add(loc.upper()); locais.append(loc)
    return locais

def _temp_badges_html(nfs_rows):
    tipos = set()
    for row in nfs_rows:
        t = str(row.get("Temperatura","")).strip().upper()
        if t and t != "NAN":
            for x in t.split("/"):
                s = x.strip()
                if s: tipos.add(s)
    badges = []
    for t in sorted(tipos):
        cls = "temp-congelado" if "CONGELADO" in t else "temp-resfriado" if "RESFRIADO" in t else "temp-ambiente"
        icon = "&#10052;" if "CONGELADO" in t else "&#127777;" if "RESFRIADO" in t else "&#127807;"
        badges.append(f'<span class="card-temp {cls}">{icon} {t}</span>')
    return " ".join(badges)

def _sum(df, col):
    if col not in df.columns: return 0
    return sum(parse_int_br(v) or 0 for v in df[col])

def _classificar_temperatura(temp_atual, tipo_temp):
    """Retorna (ok, classe) — ok: True/False/None (None = neutro)."""
    if temp_atual is None:
        return None, "temp-neutro"
    try:
        t = float(temp_atual)
    except (TypeError, ValueError):
        return None, "temp-neutro"

    tipo = str(tipo_temp or "").upper()
    if "CONGELADO" in tipo:
        return (t <= -12, "temp-ok" if t <= -12 else "temp-alerta")
    if "RESFRIADO" in tipo:
        ok = 0 <= t <= 4
        return (ok, "temp-ok" if ok else "temp-alerta")
    return None, "temp-neutro"


def _render_telemetria(f):
    temp_atual = f.get("Temperatura Atual")
    tipo_temp  = f.get("Temperatura", "")
    ignicao    = str(f.get("Ignição", "")).strip()
    situacao   = str(f.get("Situação Veículo", "")).strip()

    partes = []

    if temp_atual is not None and str(temp_atual).strip() not in ("", "nan", "None"):
        try:
            t_num = float(temp_atual)
            t_str = f"{t_num:.1f}°C"
        except (TypeError, ValueError):
            t_str = f"{temp_atual}°C"
        _, cls = _classificar_temperatura(temp_atual, tipo_temp)
        partes.append(f'<span class="telem-temp {cls}">🌡 {t_str}</span>')

    if ignicao == "Ligado":
        partes.append('<span title="Ignição ligada">🔑</span>')
    elif ignicao == "Desligado":
        partes.append('<span title="Ignição desligada">🔘</span>')

    if situacao == "Movimento":
        partes.append('<span title="Em movimento" style="color:#15803d">➡️</span>')

    if not partes:
        return ""
    return f'<span class="card-telemetria">{"".join(partes)}</span>'


def _nf_com_pedido(nf_list, nfs_rows):
    """Mini tabela NF | Pedido no card PNG."""
    # Usa NF Pedidos (formato NF1:PED1|NF2:PED2) para mapeamento preciso por NF
    nf_pedido = {}
    for row in nfs_rows:
        nf_peds = str(row.get("NF Pedidos", "") or "").strip()
        if nf_peds:
            for par in nf_peds.split("|"):
                pts = par.split(":")
                if len(pts) == 2 and pts[0].strip() and pts[1].strip():
                    nf_pedido[pts[0].strip()] = pts[1].strip()
    if not any(nf_pedido.get(nf) for nf in nf_list):
        return " · ".join(nf_list)
    linhas = []
    for nf in nf_list:
        ped = nf_pedido.get(nf, "—")
        linhas.append(f"<tr><td style='padding:0 8px 1px 0'>{nf}</td><td class='nf-ped'>{ped}</td></tr>")
    cabecalho = "<tr><th style='font-size:9px;color:#888;text-align:left;padding:0 8px 2px 0'>NF</th><th style='font-size:9px;color:#888;text-align:left'>Pedido</th></tr>"
    return f"<table style='border-collapse:collapse;margin-top:2px'>{cabecalho}{''.join(linhas)}</table>"


def _regra_html(v):
    if v == "Sim":  return '<span class="regra-sim">✓ Sim</span>'
    if v == "Não":  return '<span class="regra-nao">✗ Não</span>'
    return f'<span class="muted">{v or "—"}</span>'

def _build_html(df: pd.DataFrame, df_redespacho=None, titulo_extra: str = "") -> str:
    """Monta a imagem de UM tipo por vez.

    Coligadas e redespacho voltaram a ser painéis separados (usuário,
    04/09/2026: misturados ficou "poluído e bagunçado"). O tipo agora é dito
    no TÍTULO, e não mais por etiqueta lateral em cada card.
    """
    cards_red, veic_red, nfs_red = _cards_redespacho(df_redespacho)
    rodape_red = (f" &nbsp;|&nbsp; Redespacho: {format_int_br(veic_red)} veíc. · "
                  f"{format_int_br(nfs_red)} NF(s)") if cards_red else ""
    now  = datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")
    veic = len(set(df["Placa"].dropna().astype(str).str.strip()) - {""}) if "Placa" in df.columns else 0
    peso = _sum(df, "Quantidade"); vols = _sum(df, "Volume")
    # No painel só de redespacho não há peso nem volume — mostrar "0 kg / 0"
    # ali é ruído, não informação.
    peso_vol = (f" &nbsp;|&nbsp; Peso: {format_int_br(peso)} kg &nbsp;|&nbsp; "
                f"Volumes: {format_int_br(vols)}") if (peso or vols) else ""

    # Totais de descarga prevista para hoje
    if "Status Estimativa" in df.columns:
        df_hoje = df[df["Status Estimativa"].isin(["chega_no_horario","no_cd_no_horario"])]
    else:
        df_hoje = df.iloc[0:0]
    veic_hoje = len(df_hoje)
    peso_hoje = _sum(df_hoje, "Quantidade")

    # Agrupa por placa
    byPlaca = {}; placaOrder = []
    for _, row in df.iterrows():
        # Card ID (placa|local) desde a fase 3: pela placa, os cards CDA·CDB e
        # CNT da mesma carreta viravam um só, com as marcações de um no outro
        p = _s(row.get("Card ID","")) or _s(row.get("Placa",""))
        if not p: continue
        if p not in byPlaca: byPlaca[p] = []; placaOrder.append(p)
        byPlaca[p].append(row.to_dict())

    # Ordena por KM (veículo no CD primeiro, depois crescente, sem KM por último)
    def _km_key(placa):
        raw = _s(byPlaca[placa][0].get("KM Restante","")).replace(".","").replace(",","").strip()
        if raw == "": return 999999
        try: return int(raw)
        except: return 999999
    placaOrder.sort(key=_km_key)

    # Lista de (km, concluída, html) em vez de string: coligada e redespacho
    # entram na MESMA ordenação da tela, em vez de o redespacho ficar sempre
    # no fim da imagem (04/09/2026).
    cards = []
    for placa in placaOrder:
        nfs_rows = byPlaca[placa]
        f = nfs_rows[0]
        st   = _s(f.get("Status Estimativa",""))
        sem  = not _s(f.get("Instrução Entrada","")).strip()
        inst = _s(f.get("Instrução Entrada",""))
        est  = _s(f.get("Estimativa Chegada",""))
        km   = _s(f.get("KM Restante",""))
        obs  = _s(f.get("Observação",""))
        regra   = _s(f.get("Regra Data CD",""))
        entregas= _s(f.get("Entregas Frente",""))
        entregasObs = _s(f.get("Entregas Frente Obs",""))
        nfList  = list(dict.fromkeys(nf.strip() for r in nfs_rows for nf in _s(r.get("NF","")).split("/") if nf.strip()))

        et_cls  = STATUS_TAG.get(st, ("est-sem",""))[0]
        feriado_col = _s(f.get("Feriado", ""))
        concluida_col = _s(f.get("Descarga Concluída", "")) == "Sim"
        card_cls = "col-card " + STATUS_CARD.get(st,"") + (" st-sem-inst" if sem else "")

        ent_html = _regra_html(entregas)
        entfrente_html = (f'<span class="card-entfrente-txt">{entregasObs}</span>'
                           if entregas == "Sim" and entregasObs else _render_telemetria(f))

        cards.append((_km_num(km), concluida_col, f"""
<div class="{card_cls}">
  <div class="card-head">
    <span class="card-placa">{_s(f.get("Placa","")) or "Sem placa"}</span>
    <span class="card-emp-group">
      <span class="card-emp">{_s(f.get("Empresa",""))}</span>
      {_temp_badges_html(nfs_rows)}
    </span>
    <span class="card-km">
      {km+" km" if km else ""}
      {_etiquetas_unidade(f.get("Empresa Compra",""))}
    </span>
  </div>
  {(f'<div class="card-est"><span class="est-tag {et_cls}">{est}</span>{entfrente_html}</div>') if (est or entfrente_html) else ""}
  {f'<div class="card-feriado">&#10071; Feriado: {feriado_col}</div>' if feriado_col else ""}
  <div class="card-grid">
    <div class="card-field">
      <span class="cf-label">Cidade</span>
      <span class="cf-val">{_s(f.get("Cidade Atual","")) or "—"}</span>
    </div>
    <div class="card-field">
      <span class="cf-label">Tempo</span>
      <span class="cf-val">{_s(f.get("Tempo Estimado","")) or "—"}</span>
    </div>
    <div class="card-field">
      <span class="cf-label">Regra CD</span>
      <span class="cf-val">{_regra_html(regra)}</span>
    </div>
    <div class="card-field">
      <span class="cf-label">Entregas Frente</span>
      <span class="cf-val">{ent_html}</span>
    </div>
    <div class="card-field">
      <span class="cf-label">Unidade</span>
      <span class="cf-val">{_s(f.get("Empresa Compra","")) or "—"}</span>
    </div>
    <div class="card-field">
      <span class="cf-label">Transportadora</span>
      <span class="cf-val" style="font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:130px;display:block">
        {_s(f.get("Transportadora","")) or "—"}
      </span>
    </div>
  </div>
  {f'<div class="card-instrucao">📋 Instrução: <strong>{inst}</strong></div>' if inst else '<div class="card-sem-inst">⚠ Sem instrução de entrada</div>'}
  <div class="card-nfs"><strong>{len(nfList)} NF(s):</strong> {_nf_com_pedido(nfList, nfs_rows)}</div>
  {_datas_faturamento_html(nfs_rows)}
  {f'<div class="card-obs has-obs">{obs}</div>' if obs else ""}
  <div class="card-foot">
    <span class="card-foot-right">
      {_logo_fonte_html(f.get("Fonte Rastreio"))}
      <span class="card-pill">&#9878; {format_int_br(sum(parse_int_br(r.get("Quantidade","")) or 0 for r in nfs_rows))} kg</span>
      <span class="card-pill">&#128230; {format_int_br(sum(parse_int_br(r.get("Volume","")) or 0 for r in nfs_rows))} vol</span>
    </span>
  </div>
  {('<div class="card-locais"><strong>Local:</strong> ' + ' / '.join(_locais_unicos(nfs_rows)) + '</div>') if _locais_unicos(nfs_rows) else ''}

</div>"""))

    leg_items = [
        ("#22C55E","No CD"),("#38BDF8","Chega hoje"),
        ("#FB923C","Descarga Próx. dia útil"),("#F87171","2+ dias"),("#7C1D2F","Entregas na frente"),
    ]
    legend = "".join(f'<div class="leg"><div class="ld" style="background:{c}"></div>{l}</div>' for c,l in leg_items)

    # Coligada e redespacho na MESMA ordem da tela: por distância, com
    # descarga concluída no fim. Antes a imagem concatenava os dois blocos e o
    # redespacho caía sempre no final, independente do KM.
    todos = list(cards) + list(cards_red)
    todos.sort(key=lambda c: (1 if c[1] else 0, c[0]))
    grade = "".join(c[2] for c in todos)

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">{CSS}</head><body>
<div class="ph"><h1>MONITORAMENTO DE RECEBIMENTO{titulo_extra} — CD Demo</h1><span class="ts">Gerado: {now}</span></div>
<div class="legend-bar"><span class="legend-title">Legenda:</span><div class="legend">{legend}</div></div>
<div class="cards-grid">{grade}</div>
<div class="footer" style="display:flex;justify-content:space-between;align-items:center;gap:16px">
  <div class="ft">TOTAL GERAL &nbsp;|&nbsp; Veículos: {format_int_br(veic + veic_red)}{peso_vol}{rodape_red}</div>
  <div class="ft" style="color:#fff">Previstos hoje: {format_int_br(veic_hoje)} veíc. · {format_int_br(peso_hoje)} kg</div>
</div></body></html>"""

def _cards_redespacho(df_red) -> tuple[str, int, int]:
    """Cards de redespacho no mesmo formato visual das coligadas.

    Agrupa por placa; sem placa, pela INSTRUÇÃO ORIGEM — a que a planta usa
    para carregar, então todas as NFs dela são o redespacho de um veículo.
    Sem placa e sem instrução de entrada significa que a fábrica expediu e
    falta faturar a instrução "virtual" (regra do usuário, 04/09/2026)."""
    if df_red is None or getattr(df_red, "empty", True):
        return "", 0, 0

    df_red = df_red.copy()
    df_red["_Card"] = [
        _s(r.get("Placa")) or ("AGUARDANDO:" + _s(r.get("Instrução Origem")))
        for _, r in df_red.iterrows()
    ]
    fcol = next((c for c in ["Formulário", "Formulario", "NF"] if c in df_red.columns), None)

    cards, veiculos, total_nfs = [], 0, 0
    for chave, grp in df_red.groupby("_Card", sort=False, dropna=False):
        chave = _s(chave)
        if not chave:
            continue
        aguardando = chave.startswith("AGUARDANDO:")
        placa = "Aguardando faturamento" if aguardando else chave
        nfs = [_s(v) for v in grp[fcol].dropna() if _s(v)] if fcol else []
        nfs = list(dict.fromkeys(nfs))
        veiculos += 1
        total_nfs += len(nfs)

        def _p(col):
            for v in grp.get(col, []):
                t = _s(v)
                if t:
                    return t
            return ""

        inst = _p("Instrução Entrada")
        km, tempo = _p("KM Restante"), _p("Tempo Estimado")
        est, exped = _p("Estimativa Chegada"), _p("Empresa Expedidora")
        feriado, cidade = _p("Feriado"), _p("Cidade Atual")
        concluida = _p("Descarga Concluída") == "Sim"

        # NF + pedido FILIAL ao lado, como no card de coligada
        pcol = "Pedido Filial" if "Pedido Filial" in grp.columns else None
        pares = []
        if fcol:
            for _, r in grp.iterrows():
                n_ = _s(r[fcol])
                if not n_:
                    continue
                p_ = _s(r[pcol]) if pcol else ""
                pares.append((n_, p_))
        pares = list(dict.fromkeys(pares))
        nfs_html = "".join(
            f'<span class="rnf"><b>{n_}</b>{f"<s>-</s><i>{p_}</i>" if p_ else ""}</span>'
            for n_, p_ in pares)

        est_cls = STATUS_TAG.get(_p("Status Estimativa"), ("est-sem", ""))[0]
        logo = _logo_fonte_html(_p("Fonte Rastreio"))
        inst_html = (f'<div class="card-instrucao">&#128203; Instrução: <strong>{inst}</strong></div>'
                     if inst else
                     '<div class="card-sem-inst">&#9888; Aguardando faturar Instrução de Entrada</div>')
        cards.append((_km_num(km), concluida, f"""
<div class="col-card {STATUS_CARD.get(_p('Status Estimativa'),'')}{' st-sem-inst' if not inst else ''}{' card-concluida' if concluida else ''}">
  <div class="card-head">
    <span class="card-placa">{placa}</span>
    <span class="card-emp-group"><span class="card-emp">{exped}</span></span>
    <span class="card-km">{(km + " km &nbsp;") if km else ""}{_etiquetas_unidade(_p("Unidade"))}</span>
  </div>
  {f'<div class="card-est"><span class="est-tag {est_cls}">{est}</span></div>' if est else ""}
  {f'<div class="card-feriado">&#10071; Feriado: {feriado}</div>' if feriado else ""}
  <div class="card-grid">
    <div class="card-field"><span class="cf-label">Cidade</span>
      <span class="cf-val">{cidade or "—"}</span></div>
    <div class="card-field"><span class="cf-label">Tempo</span>
      <span class="cf-val">{tempo or "—"}</span></div>
  </div>
  {inst_html}
  <div class="card-nfs"><strong>{len(pares)} NF(s):</strong>
    <span style="font-size:8.5px;color:rgba(26,26,46,.45)">NF - pedido de entrada</span></div>
  <div class="red-nfs">{nfs_html}</div>
  {f'<div class="card-actions" style="justify-content:flex-end">{logo}</div>' if logo else ""}
</div>"""))
    return cards, veiculos, total_nfs


def html_painel(df: pd.DataFrame, df_redespacho=None, titulo_extra: str = "") -> tuple[str, int]:
    """HTML e largura da imagem do painel (coligadas e/ou redespacho). A
    exportação do painel renderiza as imagens juntas num Chromium só."""
    tem_red = df_redespacho is not None and not getattr(df_redespacho, "empty", True)
    if (df is None or df.empty) and not tem_red:
        raise ValueError("DataFrame vazio.")
    if df is None or df.empty:
        # painel só de redespacho — não há coligadas a ordenar
        df = pd.DataFrame(columns=["Empresa Compra", "Empresa", "Placa", "NF"])
    else:
        df = df.sort_values(["Empresa Compra","Empresa","Placa","NF"], kind="stable").reset_index(drop=True)
    html = _build_html(df, df_redespacho, titulo_extra)
    # Largura para acomodar ~4 cards por linha (coligadas + redespacho na mesma grade)
    n = len(set(df["Placa"].dropna().astype(str)) - {""})
    if df_redespacho is not None and not getattr(df_redespacho, "empty", True):
        n += df_redespacho["Placa"].astype(str).str.strip().replace("", None).nunique() + 2
    cols = min(4, max(1, n))
    width = cols * 386 + 40
    return html, max(900, width)


def exportar_imagem(df: pd.DataFrame, output_path: str | None = None,
                    df_redespacho=None, titulo_extra: str = "") -> Path:
    if output_path is None:
        output_path = get_output_path("Painel_Recebimento.png")
    html, largura = html_painel(df, df_redespacho, titulo_extra)
    return html_to_png(html, Path(output_path), width=largura)


# ---------------------------------------------------------------------------
# Outras operações (aba própria do painel, 20/10/2026) — um card por AE, a
# partir dos cards já montados por app/services/outras_operacoes.py
# ---------------------------------------------------------------------------

_CSS_OUTRAS = """<style>
.est-desemb{background:rgba(250,204,21,.28);color:#854d0e;font-weight:700}
.oo-ae{font-size:10px;font-weight:700;padding:2px 8px;border-radius:99px;
  background:rgba(23,50,77,.1);color:#17324d;border:1px solid rgba(23,50,77,.2);white-space:nowrap}
.oo-linha{font-size:10.5px;color:#41506b;margin-bottom:5px;line-height:1.35}
.oo-linha b{color:#1c2a38;font-weight:600}
</style>"""

_ROTULO_ETAPA = {
    "desembarque": ("est-desemb", "Em desembarque"),
    "concluida": ("est-no-cd", "Descarga concluída"),
    "saiu": ("est-sem", "Saiu do destino"),
    "sem_rastreio": ("est-sem", "Sem rastreio pela AE"),
}


def _telemetria_outras(c: dict) -> dict:
    """Campos da telemetria da coligada a partir do card. O Controle não diz
    se a carga é congelada ou resfriada: a faixa é deduzida da própria leitura
    — dentro de uma faixa válida fica verde; fora, neutra (sem alerta, que
    dependeria do tipo do produto)."""
    tipos = c.get("tipo_temperatura") or []
    if len(tipos) == 1:
        tipo = tipos[0]                 # pelo nome do produto: alerta como na coligada
    elif tipos:
        tipo = ""                       # carga mista: sem alerta
    else:
        tipo = ""
        try:
            t = float(str(c.get("temperatura", "")).replace(",", "."))
            tipo = "CONGELADO" if t <= -12 else ("RESFRIADO" if 0 <= t <= 4 else "")
        except ValueError:
            pass
    return {"Temperatura Atual": c.get("temperatura") or None, "Temperatura": tipo,
            "Ignição": c.get("ignicao", ""), "Situação Veículo": c.get("situacao_veiculo", "")}


def _card_outras(c: dict) -> tuple:
    e = lambda v: _s(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    etapa = c.get("etapa", "")
    if etapa in _ROTULO_ETAPA:
        cls, txt = _ROTULO_ETAPA[etapa]
    elif etapa == "no_destino":
        cls, txt = "est-no-cd", "No destino" + (f" desde {c.get('Chegada CD')}" if c.get("Chegada CD") else "")
    else:
        cls, txt = STATUS_TAG.get(c.get("status_estimativa", ""), ("est-sem", ""))[0], c.get("estimativa", "")
    espera = ""
    if etapa == "no_destino" and str(c.get("Aguardando Min", "")).strip().isdigit():
        m = int(c["Aguardando Min"])
        espera = f" · aguardando há {m // 60}h{m % 60:02d}" if m >= 60 else f" · aguardando há {m} min"
    placas = c.get("placa") or "Sem placa"      # só o reboque (21/10/2026)
    nfs = c.get("nfs") or []
    telem = _render_telemetria(_telemetria_outras(c))
    instr = " / ".join(c.get("instrucoes") or [])
    card_cls = "col-card" + (" card-concluida" if etapa == "concluida" else "") + \
               (" st-no-cd" if etapa in ("no_destino", "desembarque") else "")
    html = f"""
<div class="{card_cls}">
  <div class="card-head">
    <span class="card-placa">{e(placas)}</span>
    <span class="card-emp-group"><span class="oo-ae">AE {e(c.get("ae"))}</span>
      <span class="card-emp">{e(c.get("filial"))}</span>
      {_temp_badges_html([{"Temperatura": " / ".join(c.get("tipo_temperatura") or [])}])}</span>
    <span class="card-km">{(e(c.get("km")) + " km") if c.get("km") else ""}</span>
  </div>
  {f'<div class="card-est"><span class="est-tag {cls}">{e(txt)}{e(espera)}</span>{telem}</div>' if (txt or telem) else ""}
  <div class="card-grid">
    <div class="card-field"><span class="cf-label">Cidade</span><span class="cf-val">{e(c.get("cidade")) or "—"}</span></div>
    <div class="card-field"><span class="cf-label">Tempo</span><span class="cf-val">{e(c.get("tempo")) or "—"}</span></div>
    <div class="card-field"><span class="cf-label">Terceiro</span><span class="cf-val">{e(c.get("terceiro")) or "—"}</span></div>
    <div class="card-field"><span class="cf-label">Transportadora</span><span class="cf-val">{e(c.get("transportadora")) or "—"}</span></div>
  </div>
  {f'<div class="oo-linha"><b>Operação:</b> {e(c.get("operacao"))}</div>' if c.get("operacao") else ""}
  {f'<div class="card-instrucao">&#128203; Instrução: <strong>{e(instr)}</strong></div>' if instr else ""}
  <div class="card-nfs"><strong>{len(nfs)} NF(s):</strong> {e(" / ".join(nfs[:12]))}{" …" if len(nfs) > 12 else ""}</div>
  {f'<div class="card-fat-date">&#128197; Faturado em: {e(c.get("data_faturamento"))}</div>' if c.get("data_faturamento") else ""}
  <div class="card-foot"><span class="card-foot-right">
    {_logo_fonte_html(c.get("fonte"))}
    <span class="card-pill">&#9878; {format_int_br(c.get("peso_kg") or 0)} kg</span>
    <span class="card-pill">&#128230; {format_int_br(c.get("volume") or 0)} vol</span>
  </span></div>
</div>"""
    return etapa, html


def html_outras(cards: list, titulo_extra: str = " — OUTRAS OPERAÇÕES") -> tuple[str, int]:
    """HTML e largura da imagem da aba Outras operações, na mesma ordem da
    tela (desembarque → no destino → em trânsito → concluída)."""
    if not cards:
        raise ValueError("Sem cards de outras operações.")
    now = datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")
    grade = "".join(_card_outras(c)[1] for c in cards)
    peso = sum(c.get("peso_kg") or 0 for c in cards)
    vols = sum(c.get("volume") or 0 for c in cards)
    no_destino = sum(1 for c in cards if c.get("etapa") in ("no_destino", "desembarque"))
    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">{CSS}{_CSS_OUTRAS}</head><body>
<div class="ph"><h1>MONITORAMENTO DE RECEBIMENTO{titulo_extra} — CD Demo</h1><span class="ts">Gerado: {now}</span></div>
<div class="cards-grid">{grade}</div>
<div class="footer" style="display:flex;justify-content:space-between;align-items:center;gap:16px">
  <div class="ft">TOTAL &nbsp;|&nbsp; Veículos: {format_int_br(len(cards))} &nbsp;|&nbsp; Peso: {format_int_br(peso)} kg &nbsp;|&nbsp; Volumes: {format_int_br(vols)}</div>
  <div class="ft" style="color:#fff">No destino: {format_int_br(no_destino)} veíc.</div>
</div></body></html>"""
    cols = min(4, max(1, len(cards)))
    return html, max(900, cols * 386 + 40)


def exportar_imagem_outras(cards: list, output_path: str | None = None,
                           titulo_extra: str = " — OUTRAS OPERAÇÕES") -> Path:
    if output_path is None:
        output_path = get_output_path("Painel_Outras_Operacoes.png")
    html, largura = html_outras(cards, titulo_extra)
    return html_to_png(html, Path(output_path), width=largura)
