"""
Regra de data do CD pelo Romaneio Detalhado (16/11/2026).

Quando o card da coligada fica com "Regra de Data do CD = Não", o usuário
carrega o Romaneio Detalhado da carreta (uma linha por caixa, com produção e
validade) e o sistema diz quais produtos estão fora da data de produção
permitida, por NF, com o preço, e monta o e-mail para o segmento da empresa
compradora (varejo ou atacado).

  1. Leitura pelo NOME do cabeçalho (mapeamento_colunas, tipo
     "romaneio_detalhado") — o layout muda entre empresas.
  2. Cada caixa é ligada à NF do card pela QUANTIDADE, porque o romaneio não
     traz NF nem placa:
       a. NF no próprio romaneio, quando a coluna existe;
       b. o grupo "Pedido-Carga" do romaneio casa com a NF cujos produtos,
          gramas e caixas são idênticos (carne de peso variável: o peso
          líquido com 3 casas funciona como impressão digital), ou com a
          soma das NFs do mesmo pedido de compra;
       c. produto a produto: produto de uma NF só vai direto; produto em
          várias NFs é separado pelos paletes (antes, o palete que já tem
          produto de uma NF fica nela), aceito quando a soma em gramas e
          caixas fecha de UM jeito só;
       d. o que sobra fica para o usuário escolher na tela. Nunca por
          palpite: NF errada manda o e-mail para o segmento errado.
  3. Regra: vida útil = validade − produção; idade = dia da análise −
     produção; fora quando a idade passa do permitido (padrão 33,3% da vida
     útil, em dias inteiros arredondados: 60 dias → 20, 75 → 25). Decisão
     do usuário em 16/11/2026: a idade conta até o dia da análise.
  4. E-mail: um por segmento, .eml pronto para o Outlook clássico
     (X-Unsent: 1), com destinatários e cópia dos Parâmetros.
  5. Resumo do dia: as carretas analisadas na data, um e-mail por segmento
     (retrato para a gerência; o e-mail por veículo segue para agir na carga).

Caso real que guiou o desenho: DEM1A23 (13/11/2026), 3 NFs da CDD — 182090
para o CDA (atacado), 182088 e 182089 para o CDB (varejo). Os produtos 258,
8427 e 8741 estão em mais de uma NF e o 258 tem preço diferente em cada uma;
o preço sai sempre de NF + produto. A "Classificação Preço" do Controle diz
ATACADO também nos pedidos do CDB — por isso o segmento vem dos Parâmetros.
"""
from __future__ import annotations

import html
import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP

from app.database import get_db
from app.services import mapeamento_colunas as mc
from app.services.excel_reader import read_excel_flexible

PCT_PADRAO = 33.3
SEGMENTOS = ("Varejo", "Atacado")
EMPRESAS_PADRAO = {"Varejo": ["CDB"], "Atacado": ["CDA"]}
DIAS_GUARDADOS = 120          # análise mais antiga que isso é apagada
MAX_BLOCOS = 32               # acima disso a separação por palete não é tentada
MAX_DESTINATARIOS = 30

METODOS = {
    "romaneio": "NF informada no romaneio",
    "grupo": "pedido do romaneio igual à NF (produtos, gramas e caixas)",
    "grupo_pedido": "pedido do romaneio igual às NFs do mesmo pedido",
    "produto": "produto presente em uma NF da carreta",
    "palete": "separado pelos paletes (soma exata de gramas e caixas)",
    "escolha": "NF escolhida na tela",
}

TIPO = "romaneio_detalhado"
mc.registrar_tipo(TIPO, {
    "rotulo": "Romaneio Detalhado (regra de data do CD)",
    "campos": {
        "produto":      ("Código do produto", ["Produto > Código", "Produto > Cód Produto", "Cód Produto",
                                                "Código Produto", "Código do Produto"], True,
                         ["^produto>Código", "^produto>Cód Produto"]),
        "nome":         ("Descrição do produto", ["Produto > Nome", "Produto > Descrição", "Produto > Produto",
                                                   "Descrição Produto", "Nome Produto"], False,
                         ["^produto>Nome", "^produto>Descrição"]),
        "producao":     ("Data de produção", ["Data > Produção", "Data Produção", "Data de Produção", "Produção",
                                               "Data > Fabricação", "Fabricação"], True, ["Produção", "Fabricação"]),
        "validade":     ("Data de validade", ["Data > Validade", "Data Validade", "Data de Validade", "Validade",
                                               "Vencimento"], True, ["Validade", "Vencimento"]),
        "peso_liquido": ("Peso líquido da caixa (kg)", ["Peso > Líquido", "Peso Líquido", "Líquido"], True, ["Líquido"]),
        "pedido_carga": ("Pedido-carga", ["Pedido Comercial > Pedido-Carga", "Pedido-Carga", "Pedido Carga"], False,
                         ["Pedido-Carga"]),
        "palete":       ("Palete", ["Palete > Código", "Código Palete", "Palete"], False, ["^palete>Código"]),
        "caixa":        ("Código de barras da caixa", ["Volume > Código de Barras", "Código de Barras", "Etiqueta"],
                         False, ["Código de Barras"]),
        "nf":           ("Nota fiscal (quando o romaneio traz)", ["Nota Fiscal > Número", "Nota Fiscal", "Número NF",
                                                                   "NF"], False, []),
    },
    "silenciosos": ("nome", "pedido_carga", "palete", "caixa", "nf"),
})

_tabelas_ok: set = set()


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------

def garantir_tabelas(db) -> None:
    from app import database
    chave = str(database.DB_PATH)
    if chave in _tabelas_ok:
        return
    db.executescript("""
        CREATE TABLE IF NOT EXISTS romaneio_regra_config (
            conta          TEXT PRIMARY KEY,
            cfg            TEXT NOT NULL DEFAULT '{}',
            atualizado_por TEXT NOT NULL DEFAULT '',
            atualizado_em  TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS romaneio_regra_analise (
            conta        TEXT NOT NULL,
            card_id      TEXT NOT NULL,
            placa        TEXT NOT NULL DEFAULT '',
            nfs          TEXT NOT NULL DEFAULT '',
            origem       TEXT NOT NULL DEFAULT '',
            arquivo      TEXT NOT NULL DEFAULT '',
            referencia   TEXT NOT NULL DEFAULT '',
            pct          REAL NOT NULL DEFAULT 33.3,
            caixas       TEXT NOT NULL DEFAULT '[]',
            linhas       TEXT NOT NULL DEFAULT '[]',
            atribuicao   TEXT NOT NULL DEFAULT '{}',
            escolhas     TEXT NOT NULL DEFAULT '{}',
            avisos       TEXT NOT NULL DEFAULT '[]',
            resumo       TEXT NOT NULL DEFAULT '{}',
            usuario      TEXT NOT NULL DEFAULT '',
            analisado_em TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (conta, card_id)
        );
    """)
    _tabelas_ok.add(chave)


def _agora() -> datetime:
    from app.services.carretas_viagem import _agora as agora
    return agora()


def _carimbo() -> str:
    return _agora().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# texto, número e data
# ---------------------------------------------------------------------------

def _txt(v) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    if t.lower() in ("nan", "none", "nat"):
        return ""
    if re.fullmatch(r"-?\d+\.0+", t):
        t = t.split(".")[0]
    return t


def _codigo(v) -> str:
    """258.0 -> '258'; '000258' -> '258'; texto livre fica como está."""
    t = _txt(v)
    if t.isdigit():
        return t.lstrip("0") or "0"
    return t


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    t = _txt(v).replace(" ", "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _data(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "to_pydatetime"):
        try:
            return v.to_pydatetime().date()
        except (ValueError, TypeError):
            return None
    t = _txt(v)
    if not t:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", t) and 20000 < float(t) < 80000:      # serial do Excel
        return (datetime(1899, 12, 30) + timedelta(days=float(t))).date()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                "%Y-%m-%dT%H:%M:%S", "%d/%m/%y"):
        try:
            return datetime.strptime(t[:19] if "T" in t or "-" in t[:5] else t, fmt).date()
        except ValueError:
            continue
    return None


def _iso(t: str) -> date | None:
    try:
        return date.fromisoformat(t) if t else None
    except ValueError:
        return None


def _br(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _g(kg) -> int:
    return int(round((kg or 0) * 1000))


def _norm_emp(v) -> str:
    t = unicodedata.normalize("NFKD", _txt(v))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


def permitido(vida: int, pct: float) -> int:
    """Dias de idade permitidos: pct da vida útil, arredondado para o dia
    inteiro mais próximo (33,3% de 60 = 19,98 → 20; de 75 = 24,975 → 25).
    Sem o arredondamento, "33,3%" deixaria de fora a caixa com exatamente
    1/3 da vida (20 de 60 dias) — o relatório real tinha uma."""
    return int(math.floor(vida * pct / 100.0 + 0.5 + 1e-9))


# ---------------------------------------------------------------------------
# parâmetros (por conta do CD)
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s;,<>\"']+@[^@\s;,<>\"']+\.[^@\s;,<>\"']+$")


def _lista_emails(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        partes = [str(x) for x in v]
    else:
        partes = re.split(r"[;,\s]+", str(v or ""))
    out = []
    for p in partes:
        p = p.strip()
        if not p:
            continue
        if not _EMAIL.match(p) or len(p) > 120:
            raise ValueError(f"E-mail inválido: “{p[:60]}”.")
        if p.lower() not in [x.lower() for x in out]:
            out.append(p)
    if len(out) > MAX_DESTINATARIOS:
        raise ValueError(f"Informe no máximo {MAX_DESTINATARIOS} e-mails por campo.")
    return out


def _lista_empresas(v) -> list[str]:
    partes = v if isinstance(v, (list, tuple)) else re.split(r"[;,/\s]+", str(v or ""))
    out = []
    for p in partes:
        e = _norm_emp(p)[:20]
        if e and e not in out:
            out.append(e)
    return out[:20]


def config(conta: str) -> dict:
    with get_db() as db:
        garantir_tabelas(db)
        r = db.execute("SELECT cfg, atualizado_por, atualizado_em FROM romaneio_regra_config WHERE conta=?",
                       (conta,)).fetchone()
    try:
        cfg = json.loads(r["cfg"]) if r else {}
    except (TypeError, ValueError):
        cfg = {}
    segs = cfg.get("segmentos") or {}
    out = {"pct": float(cfg.get("pct") or PCT_PADRAO), "segmentos": {},
           "salvo": bool(r), "atualizado_por": r["atualizado_por"] if r else "",
           "atualizado_em": r["atualizado_em"] if r else ""}
    for s in SEGMENTOS:
        d = segs.get(s) or {}
        out["segmentos"][s] = {
            "empresas": list(d.get("empresas") if r else EMPRESAS_PADRAO[s]),
            "para": list(d.get("para") or []),
            "cc": list(d.get("cc") or []),
            # resumo do dia: em branco, vai para os destinatários do segmento
            "resumo_para": list(d.get("resumo_para") or []),
            "resumo_cc": list(d.get("resumo_cc") or []),
        }
    return out


def validar_config(dados: dict) -> dict:
    if not isinstance(dados, dict):
        raise ValueError("Parâmetros inválidos.")
    pct = _num(dados.get("pct"))
    if pct is None or not (1 <= pct <= 100):
        raise ValueError("Informe o percentual da vida útil entre 1 e 100.")
    pct = round(pct, 2)
    segs_in = dados.get("segmentos") or {}
    segs, vistas = {}, {}
    for s in SEGMENTOS:
        d = segs_in.get(s) or {}
        empresas = _lista_empresas(d.get("empresas"))
        for e in empresas:
            if e in vistas:
                raise ValueError(f"A empresa {e} está no varejo e no atacado. Deixe em um segmento.")
            vistas[e] = s
        try:
            para = _lista_emails(d.get("para"))
            cc = _lista_emails(d.get("cc"))
        except ValueError as e:
            raise ValueError(f"{s}: {e}")
        try:
            r_para = _lista_emails(d.get("resumo_para"))
            r_cc = _lista_emails(d.get("resumo_cc"))
        except ValueError as e:
            raise ValueError(f"{s} (resumo do dia): {e}")
        if r_cc and not r_para:
            raise ValueError(f"{s} (resumo do dia): informe o Para ou deixe a cópia em branco "
                             "(em branco, o resumo vai para os destinatários do segmento).")
        segs[s] = {"empresas": empresas, "para": para, "cc": cc, "resumo_para": r_para, "resumo_cc": r_cc}
    return {"pct": pct, "segmentos": segs}


def salvar_config(conta: str, dados: dict, usuario: str) -> dict:
    cfg = validar_config(dados)
    with get_db() as db:
        garantir_tabelas(db)
        db.execute("""INSERT INTO romaneio_regra_config (conta, cfg, atualizado_por, atualizado_em)
                      VALUES (?,?,?,?)
                      ON CONFLICT(conta) DO UPDATE SET cfg=excluded.cfg,
                      atualizado_por=excluded.atualizado_por, atualizado_em=excluded.atualizado_em""",
                   (conta, json.dumps(cfg, ensure_ascii=False), usuario, _carimbo()))
    return config(conta)


def segmento_de_empresa(cfg: dict) -> dict:
    out = {}
    for s, d in (cfg.get("segmentos") or {}).items():
        for e in d.get("empresas") or []:
            out[_norm_emp(e)] = s
    return out


# ---------------------------------------------------------------------------
# leitura
# ---------------------------------------------------------------------------

def ler_romaneio(caminho: str, nome_arquivo: str = "", avisos: list | None = None) -> list[dict]:
    """Uma entrada por caixa: p (produto), n (nome), g (pedido-carga), pal
    (palete), cb (código de barras), nf, pr/va (produção/validade ISO), kg.
    Levanta ColunasFaltando (tela abre o mapeamento) ou ValueError."""
    df = read_excel_flexible(caminho)
    df.columns = mc.nomes_unicos(df.columns)
    col = mc.resolver_arquivo(TIPO, df.columns, nome_arquivo, avisos)

    def val(r, campo):
        c = col.get(campo)
        return r[c] if c else None

    caixas, vistos, repetidas = [], set(), 0
    for _, r in df.iterrows():
        prod = _codigo(val(r, "produto"))
        if not prod:                       # linha de total ou em branco
            continue
        cb = _txt(val(r, "caixa"))
        if cb:
            if cb in vistos:
                repetidas += 1
                continue
            vistos.add(cb)
        pr, va = _data(val(r, "producao")), _data(val(r, "validade"))
        caixas.append({
            "p": prod, "n": _txt(val(r, "nome"))[:80], "g": _txt(val(r, "pedido_carga"))[:40],
            "pal": _txt(val(r, "palete"))[:40], "cb": cb[:60], "nf": _codigo(val(r, "nf"))[:20],
            "pr": pr.isoformat() if pr else "", "va": va.isoformat() if va else "",
            "kg": round(_num(val(r, "peso_liquido")) or 0.0, 3),
        })
    if not caixas:
        raise ValueError("O arquivo não tem caixas com código de produto. Confirme que é o Romaneio Detalhado.")
    if repetidas and avisos is not None:
        avisos.append(f"{repetidas} linha(s) com código de barras repetido no romaneio: cada caixa foi contada uma vez.")
    return caixas


def _nf(v) -> str:
    return _codigo(v)


def card_e_linhas(df_col, df_itens, card_id: str) -> tuple[dict, list[dict]]:
    """Dados do card (placa, NFs, origem) e as linhas das NFs dele no Estoque
    em Trânsito (produto, kg, caixas, preço, empresa compradora, pedido)."""
    from app.utils.text_utils import normalize_plate
    if df_col is None or getattr(df_col, "empty", True) or "Card ID" not in df_col.columns:
        raise ValueError("Card não encontrado. Atualize o painel.")
    rows = df_col[df_col["Card ID"].astype(str).str.strip() == card_id]
    if rows.empty:
        raise ValueError("Card não encontrado. Atualize o painel.")
    nfs: list[str] = []
    for v in rows["NF"].astype(str) if "NF" in rows.columns else []:
        for n in v.split("/"):
            n = _nf(n)
            if n and n not in nfs:
                nfs.append(n)
    f = rows.iloc[0]
    placa = normalize_plate(_txt(f.get("Placa", "")))
    card = {"card_id": card_id, "placa": placa, "nfs": nfs, "origem": _txt(f.get("Empresa", ""))}
    if not nfs or df_itens is None or getattr(df_itens, "empty", True) or "NF" not in df_itens.columns:
        return card, []
    itens = df_itens[df_itens["NF"].map(_nf).isin(nfs)]
    if placa and "Placa" in itens.columns:
        pl = itens["Placa"].astype(str).map(normalize_plate)
        itens = itens[(pl == placa) | (pl == "")]
    tem_preco = "Preço" in itens.columns
    linhas = []
    for _, r in itens.iterrows():
        linhas.append({
            "nf": _nf(r.get("NF")), "p": _codigo(r.get("Cód Produto")), "n": _txt(r.get("Produto"))[:80],
            "kg": round(_num(r.get("Quantidade")) or 0.0, 3), "cx": int(round(_num(r.get("Volume")) or 0)),
            "preco": _num(r.get("Preço")) if tem_preco else None,
            "empresa": _norm_emp(r.get("Empresa Compra")), "pedido": _txt(r.get("Pedido Compra")),
            "carga": _txt(r.get("Carga")), "local": _txt(r.get("Local Estoque"))[:60],
            "emissao": _txt(r.get("Data Faturamento")), "origem": _txt(r.get("Empresa")),
        })
    return card, linhas


def completar_precos(linhas: list[dict], df_arquivo) -> int:
    """Preço das linhas a partir de outro DataFrame do Estoque (o painel
    restaurado de antes desta versão não tem a coluna). Devolve quantas
    linhas ganharam preço."""
    if df_arquivo is None or getattr(df_arquivo, "empty", True) or "Preço" not in df_arquivo.columns:
        return 0
    precos = {}
    for _, r in df_arquivo.iterrows():
        p = _num(r.get("Preço"))
        if p is not None:
            precos.setdefault((_nf(r.get("NF")), _codigo(r.get("Cód Produto"))), p)
    n = 0
    for l in linhas:
        if l.get("preco") is None and (l["nf"], l["p"]) in precos:
            l["preco"] = precos[(l["nf"], l["p"])]
            n += 1
    return n


# ---------------------------------------------------------------------------
# romaneio → NF pela quantidade
# ---------------------------------------------------------------------------

def _subconjuntos(blocos: list, livres: list, alvo: tuple, limite: int) -> list[tuple]:
    """Subconjuntos de `livres` (índices de blocos (gramas, caixas)) cuja soma
    é exatamente `alvo`. Meio a meio (meet-in-the-middle): 2 × 2^(n/2)."""
    meio = len(livres) // 2
    a, b = livres[:meio], livres[meio:]

    def todas(lst):
        out = [((0, 0), ())]
        for j in lst:
            g, c = blocos[j]
            out += [((s[0] + g, s[1] + c), m + (j,)) for s, m in out]
        return out

    mapa: dict = {}
    for s, m in todas(a):
        mapa.setdefault(s, []).append(m)
    sols = []
    for s, m in todas(b):
        for m2 in mapa.get((alvo[0] - s[0], alvo[1] - s[1]), ()):
            sols.append(m2 + m)
            if len(sols) >= limite:
                return sols
    return sols


def _particionar(blocos: list, alvos: list, limite: int = 2) -> list[list[tuple]]:
    """Formas de repartir TODOS os blocos entre os alvos, com soma exata em
    cada um. Devolve até `limite` soluções (cada uma: [(k, blocos do alvo k)])."""
    sols: list = []

    def rec(livres, k, atual):
        if len(sols) >= limite:
            return
        if k == len(alvos) - 1:
            s = (sum(blocos[j][0] for j in livres), sum(blocos[j][1] for j in livres))
            if s == tuple(alvos[k]):
                sols.append(atual + [(k, tuple(livres))])
            return
        for sub in _subconjuntos(blocos, livres, tuple(alvos[k]), 64):
            usados = set(sub)
            rec([j for j in livres if j not in usados], k + 1, atual + [(k, sub)])
            if len(sols) >= limite:
                return

    rec(list(range(len(blocos))), 0, [])
    return sols


def atribuir(caixas: list[dict], linhas: list[dict]) -> dict:
    """{"nf": [nf por caixa], "metodo": [...], "pendentes": [blocos para a
    tela escolher], "nao_identificados": [índices sem NF possível]}."""
    n = len(caixas)
    nf_de, metodo = [""] * n, [""] * n
    alvo: dict = {}
    pedido_nf: dict = {}
    for l in linhas:
        a = alvo.setdefault((l["nf"], l["p"]), [0, 0])
        a[0] += _g(l["kg"])
        a[1] += int(l["cx"] or 0)
        pedido_nf.setdefault(l["nf"], (l["empresa"], l["pedido"], l["carga"]))
    nfs = sorted({nf for nf, _ in alvo})
    sig_nf = {nf: {p: tuple(v) for (f, p), v in alvo.items() if f == nf} for nf in nfs}
    resto = {k: list(v) for k, v in alvo.items()}

    def marcar(i, nf, como):
        nf_de[i], metodo[i] = nf, como
        r = resto.get((nf, caixas[i]["p"]))
        if r is not None:
            r[0] -= _g(caixas[i]["kg"])
            r[1] -= 1

    def assinatura(idx):
        s: dict = {}
        for i in idx:
            v = s.setdefault(caixas[i]["p"], [0, 0])
            v[0] += _g(caixas[i]["kg"])
            v[1] += 1
        return {p: tuple(v) for p, v in s.items()}

    def soma_sig(conj):
        s: dict = {}
        for nf in conj:
            for p, (g, c) in sig_nf[nf].items():
                v = s.setdefault(p, [0, 0])
                v[0] += g
                v[1] += c
        return {p: tuple(v) for p, v in s.items()}

    def igual(a, b):
        return a.keys() == b.keys() and all(abs(a[p][0] - b[p][0]) <= 1 and a[p][1] == b[p][1] for p in a)

    # a. NF no próprio romaneio
    for i, c in enumerate(caixas):
        if c.get("nf") and c["nf"] in sig_nf:
            marcar(i, c["nf"], "romaneio")
    livres = set(nfs) - {nf_de[i] for i in range(n) if nf_de[i]}

    # b. grupo do romaneio = NF inteira, 1 para 1
    grupos: dict = {}
    for i, c in enumerate(caixas):
        if not nf_de[i] and c.get("g"):
            grupos.setdefault(c["g"], []).append(i)
    sig_g = {g: assinatura(idx) for g, idx in grupos.items()}
    cand = {g: [nf for nf in sorted(livres) if igual(s, sig_nf[nf])] for g, s in sig_g.items()}
    reservado: dict = {}
    mudou = True
    while mudou:
        mudou = False
        for g in sorted(cand):
            if g in reservado:
                continue
            cs = [nf for nf in cand[g] if nf in livres]
            if len(cs) != 1:
                continue
            outros = [g2 for g2 in cand if g2 != g and g2 not in reservado and cs[0] in cand[g2]]
            if outros:
                continue
            for i in grupos[g]:
                marcar(i, cs[0], "grupo")
            livres.discard(cs[0])
            reservado[g] = {cs[0]}
            mudou = True

    # b2. grupo = soma das NFs do mesmo pedido de compra (pedido em 2 NFs)
    por_pedido: dict = {}
    for nf in sorted(livres):
        if pedido_nf[nf][1]:
            por_pedido.setdefault(pedido_nf[nf], []).append(nf)
    conjuntos = [tuple(v) for v in por_pedido.values() if len(v) >= 2]
    restricao: dict = {}
    for g in sorted(grupos):
        if g in reservado:
            continue
        hits = [cj for cj in conjuntos if all(nf in livres for nf in cj) and igual(sig_g[g], soma_sig(cj))]
        if len(hits) != 1:
            continue
        rivais = [g2 for g2 in grupos if g2 != g and g2 not in reservado and igual(sig_g[g2], soma_sig(hits[0]))]
        if rivais:
            continue
        reservado[g] = set(hits[0])
        restricao[g] = frozenset(hits[0])
        livres -= set(hits[0])

    # c. produto a produto
    pools: dict = {}
    for i, c in enumerate(caixas):
        if nf_de[i]:
            continue
        pools.setdefault((c["p"], restricao.get(c.get("g") or "")), []).append(i)

    def candidatos(p, r):
        base = r if r is not None else livres
        return sorted(nf for nf in base if (nf, p) in resto and resto[(nf, p)][1] > 0)

    def chave_pool(k):
        return (k[0], ",".join(sorted(k[1])) if k[1] else "")

    # c1. produto de uma NF só (repete: uma atribuição pode resolver outra)
    mudou = True
    while mudou:
        mudou = False
        for k in sorted(pools, key=chave_pool):
            idx = [i for i in pools[k] if not nf_de[i]]
            if not idx:
                continue
            cs = candidatos(*k)
            if len(cs) == 1:
                como = "grupo_pedido" if k[1] else "produto"
                for i in idx:
                    marcar(i, cs[0], como)
                mudou = True

    # c2. produto em várias NFs: separa pelos paletes
    pendentes, nao_ident = [], []
    for k in sorted(pools, key=chave_pool):
        p, restr = k
        idx = [i for i in pools[k] if not nf_de[i]]
        if not idx:
            continue
        cs = candidatos(p, restr)
        if not cs:
            nao_ident.extend(idx)
            continue
        if all(caixas[i].get("pal") for i in idx):
            rot = lambda i: "pal:" + caixas[i]["pal"]          # noqa: E731
        elif all(caixas[i].get("g") for i in idx):
            rot = lambda i: "ped:" + caixas[i]["g"]            # noqa: E731
        else:
            rot = lambda i: "todos"                            # noqa: E731
        blocos_idx: dict = {}
        for i in idx:
            blocos_idx.setdefault(rot(i), []).append(i)
        nomes = sorted(blocos_idx)
        somas = [(sum(_g(caixas[i]["kg"]) for i in blocos_idx[b]), len(blocos_idx[b])) for b in nomes]
        alvos = [tuple(resto[(nf, p)]) for nf in cs]
        resolvido = False
        if len(nomes) <= MAX_BLOCOS and len(nomes) >= len(cs):
            # palete que já tem caixa ligada a uma NF candidata fica nela
            fixo = {}
            for j, b in enumerate(nomes):
                if not b.startswith("pal:"):
                    continue
                pal = b[4:]
                nfs_pal = {nf_de[i] for i, c in enumerate(caixas) if c.get("pal") == pal and nf_de[i]}
                if len(nfs_pal) == 1 and next(iter(nfs_pal)) in cs:
                    fixo[j] = cs.index(next(iter(nfs_pal)))
            tentativas = [fixo, {}] if fixo else [{}]
            for fx in tentativas:
                alvo_rest = [list(a) for a in alvos]
                for j, kk in fx.items():
                    alvo_rest[kk][0] -= somas[j][0]
                    alvo_rest[kk][1] -= somas[j][1]
                if any(a[1] < 0 for a in alvo_rest):
                    continue
                soltos = [j for j in range(len(nomes)) if j not in fx]
                sols = _particionar([somas[j] for j in soltos], [tuple(a) for a in alvo_rest])
                if len(sols) != 1:
                    continue
                for kk, sub in sols[0]:
                    for jj in sub:
                        for i in blocos_idx[nomes[soltos[jj]]]:
                            marcar(i, cs[kk], "palete")
                for j, kk in fx.items():
                    for i in blocos_idx[nomes[j]]:
                        marcar(i, cs[kk], "palete")
                resolvido = True
                break
        if resolvido:
            continue
        for b in nomes:
            bid = f"{p}|{b}" + (f"|{','.join(sorted(restr))}" if restr else "")
            pendentes.append({"id": bid, "p": p, "bloco": b, "idx": blocos_idx[b], "opcoes": cs})
    return {"nf": nf_de, "metodo": metodo, "pendentes": pendentes, "nao_identificados": sorted(nao_ident)}


# ---------------------------------------------------------------------------
# análise guardada
# ---------------------------------------------------------------------------

def analisar(conta: str, card: dict, linhas: list[dict], caixas: list[dict], arquivo: str,
             usuario: str, avisos: list[str] | None = None, referencia: date | None = None) -> dict:
    """Liga as caixas às NFs, guarda e devolve o resultado para a tela."""
    if not linhas:
        raise ValueError("As NFs deste card não estão no Estoque em Trânsito carregado. "
                         "Importe o Estoque em Trânsito de novo e repita a análise.")
    cfg = config(conta)
    ref = referencia or _agora().date()
    at = atribuir(caixas, linhas)
    # guardado enxuto: nome uma vez por produto (não por caixa) e sem o
    # código de barras, que serve à leitura (caixa repetida) e não depois
    at["nomes"] = {}
    for c in caixas:
        if c.get("n"):
            at["nomes"].setdefault(c["p"], c["n"])
    caixas = [{k: v for k, v in c.items() if k not in ("n", "cb")} for c in caixas]
    avs = list(avisos or [])
    if not any(l.get("preco") is not None for l in linhas):
        avs.append("O Estoque em Trânsito carregado não tem a coluna de preço: o e-mail sai sem valores. "
                   "Importe o relatório de novo para trazer o preço.")
    with get_db() as db:
        garantir_tabelas(db)
        limite = (_agora() - timedelta(days=DIAS_GUARDADOS)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("DELETE FROM romaneio_regra_analise WHERE analisado_em < ?", (limite,))
        db.execute("""INSERT OR REPLACE INTO romaneio_regra_analise
                      (conta, card_id, placa, nfs, origem, arquivo, referencia, pct, caixas, linhas,
                       atribuicao, escolhas, avisos, resumo, usuario, analisado_em)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (conta, card["card_id"], card.get("placa", ""), "/".join(card.get("nfs") or []),
                    card.get("origem", ""), arquivo[:120], ref.isoformat(), cfg["pct"],
                    json.dumps(caixas, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(linhas, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(at, ensure_ascii=False, separators=(",", ":")),
                    "{}", json.dumps(avs, ensure_ascii=False), "{}", usuario, _carimbo()))
    res = resultado(conta, card["card_id"])
    return res


def _linha_analise(conta: str, card_id: str):
    with get_db() as db:
        garantir_tabelas(db)
        return db.execute("SELECT * FROM romaneio_regra_analise WHERE conta=? AND card_id=?",
                          (conta, card_id)).fetchone()


def escolher(conta: str, card_id: str, bloco: str, nf: str, usuario: str) -> dict:
    r = _linha_analise(conta, card_id)
    if not r:
        raise ValueError("Análise não encontrada. Carregue o romaneio de novo.")
    at = json.loads(r["atribuicao"])
    pend = {b["id"]: b for b in at.get("pendentes", [])}
    if bloco not in pend:
        raise ValueError("Bloco não encontrado. Atualize a análise.")
    nf = _nf(nf)
    if nf and nf not in pend[bloco]["opcoes"]:
        raise ValueError("Esta NF não é uma das opções do bloco.")
    esc = json.loads(r["escolhas"] or "{}")
    if nf:
        esc[bloco] = nf
    else:
        esc.pop(bloco, None)
    with get_db() as db:
        db.execute("UPDATE romaneio_regra_analise SET escolhas=? WHERE conta=? AND card_id=?",
                   (json.dumps(esc, ensure_ascii=False), conta, card_id))
    return resultado(conta, card_id)


def resultado(conta: str, card_id: str, cfg: dict | None = None) -> dict | None:
    """Análise guardada, com a regra aplicada e os parâmetros atuais
    (segmento e destinatários). None quando o card não tem análise."""
    r = _linha_analise(conta, card_id)
    if not r:
        return None
    cfg = cfg or config(conta)
    caixas = json.loads(r["caixas"])
    linhas = json.loads(r["linhas"])
    at = json.loads(r["atribuicao"])
    esc = json.loads(r["escolhas"] or "{}")
    ref = _iso(r["referencia"]) or _agora().date()
    pct = float(r["pct"] or PCT_PADRAO)
    seg_emp = segmento_de_empresa(cfg)

    nf_de = list(at["nf"])
    metodo = list(at["metodo"])
    pend_por_caixa: dict = {}
    for b in at.get("pendentes", []):
        escolhida = esc.get(b["id"], "")
        for i in b["idx"]:
            if escolhida:
                nf_de[i], metodo[i] = escolhida, "escolha"
            else:
                pend_por_caixa[i] = b

    # NF → dados (empresa, pedido, preço por produto)
    info_nf: dict = {}
    preco: dict = {}
    for l in linhas:
        d = info_nf.setdefault(l["nf"], {"nf": l["nf"], "empresa": l["empresa"], "pedido": l["pedido"],
                                          "carga": l["carga"], "local": l["local"], "emissao": l["emissao"],
                                          "origem": l["origem"], "cx": 0, "kg": 0.0, "produtos": {}})
        d["cx"] += int(l["cx"] or 0)
        d["kg"] += l["kg"] or 0
        pp = d["produtos"].setdefault(l["p"], {"kg": 0.0, "valor": 0.0, "com_preco": True, "n": l["n"]})
        pp["kg"] += l["kg"] or 0
        if l.get("preco") is None:
            pp["com_preco"] = False
        else:
            pp["valor"] += (l["kg"] or 0) * l["preco"]
    # local de estoque do pedido de compra: vem por linha da NF (uma NF pode ter mais de um)
    local_np = {}
    for l in linhas:
        local_np.setdefault((l["nf"], l["p"]), []).append(l.get("local") or "")
    local_np = {k: _juntar_txt(v) for k, v in local_np.items()}
    for nf, d in info_nf.items():
        d["local"] = _juntar_txt(l.get("local") or "" for l in linhas if l["nf"] == nf)
        d["segmento"] = seg_emp.get(d["empresa"], "")
        for p, pp in d["produtos"].items():
            # mesma NF e produto em duas linhas: preço médio ponderado pelo kg
            preco[(nf, p)] = (round(pp["valor"] / pp["kg"], 4) if pp["com_preco"] and pp["kg"] else None)
    nome_nf = {(l["nf"], l["p"]): l["n"] for l in linhas}

    # regra por caixa
    fora_idx, sem_data_idx = [], []
    aval = []
    for i, c in enumerate(caixas):
        pr, va = _iso(c["pr"]), _iso(c["va"])
        if not pr or not va or va <= pr:
            aval.append(None)
            sem_data_idx.append(i)
            continue
        vida = (va - pr).days
        idade = (ref - pr).days
        perm = permitido(vida, pct)
        aval.append((vida, idade, perm))
        if idade > perm:
            fora_idx.append(i)

    nomes_rom = at.get("nomes") or {}

    def nome(i):
        c = caixas[i]
        return c.get("n") or nomes_rom.get(c["p"]) or nome_nf.get((nf_de[i], c["p"]), "")

    # linhas fora da regra: NF × produto × produção × validade
    grupos: dict = {}
    for i in fora_idx:
        c = caixas[i]
        k = (nf_de[i], c["p"], c["pr"], c["va"])
        g = grupos.setdefault(k, {"idx": [], "kg": 0.0})
        g["idx"].append(i)
        g["kg"] += c["kg"]
    fora = []
    for (nf, p, pr, va), g in grupos.items():
        vida, idade, perm = aval[g["idx"][0]]
        inf = info_nf.get(nf, {})
        pz = preco.get((nf, p)) if nf else None
        kg = round(g["kg"], 3)
        fora.append({
            "nf": nf, "empresa": inf.get("empresa", ""), "segmento": inf.get("segmento", ""),
            "pedido": inf.get("pedido", ""), "carga": inf.get("carga", ""),
            "local": local_np.get((nf, p), "") if nf else "",
            "p": p, "nome": nome(g["idx"][0]), "cx": len(g["idx"]), "kg": kg,
            "producao": _br(_iso(pr)), "validade": _br(_iso(va)), "vida": vida, "idade": idade,
            "permitido": perm, "consumido": round(100.0 * idade / vida, 1) if vida else None,
            "limite": _br(_iso(pr) + timedelta(days=perm)) if _iso(pr) else "",
            "preco": pz, "valor": round(kg * pz, 2) if pz is not None else None,
            "pendente": bool(not nf and any(i in pend_por_caixa for i in g["idx"])),
        })

    def _ordem_cod(p):
        return (0, int(p), "") if p.isdigit() else (1, 0, p)
    fora.sort(key=lambda x: (x["nf"] == "", x["nf"], _ordem_cod(x["p"]), x["producao"][6:] + x["producao"][3:5]
                             + x["producao"][:2]))

    # por NF
    nfs_out = []
    rom_por_nf: dict = {}
    for i, c in enumerate(caixas):
        d = rom_por_nf.setdefault(nf_de[i], {"cx": 0, "g": 0, "fora_cx": 0, "fora_kg": 0.0, "metodos": {}})
        d["cx"] += 1
        d["g"] += _g(c["kg"])
        if metodo[i]:
            d["metodos"][metodo[i]] = d["metodos"].get(metodo[i], 0) + 1
    for i in fora_idx:
        d = rom_por_nf[nf_de[i]]
        d["fora_cx"] += 1
        d["fora_kg"] += caixas[i]["kg"]
    valor_nf: dict = {}
    for f in fora:
        if f["nf"] and f["valor"] is not None:
            valor_nf[f["nf"]] = valor_nf.get(f["nf"], 0.0) + f["valor"]
    for nf in sorted(info_nf):
        inf = info_nf[nf]
        rom = rom_por_nf.get(nf, {"cx": 0, "g": 0, "fora_cx": 0, "fora_kg": 0.0, "metodos": {}})
        nfs_out.append({
            "nf": nf, "empresa": inf["empresa"], "segmento": inf["segmento"], "pedido": inf["pedido"],
            "carga": inf["carga"], "local": inf["local"], "emissao": inf["emissao"],
            "cx": inf["cx"], "kg": round(inf["kg"], 3), "cx_romaneio": rom["cx"], "kg_romaneio": round(rom["g"] / 1000, 3),
            "confere": rom["cx"] == inf["cx"] and abs(rom["g"] - _g(inf["kg"])) <= 1,
            "fora_cx": rom["fora_cx"], "fora_kg": round(rom["fora_kg"], 3),
            "fora_valor": round(valor_nf.get(nf, 0.0), 2) if nf in valor_nf else None,
            "metodos": [{"metodo": m, "texto": METODOS.get(m, m), "cx": q}
                        for m, q in sorted(rom["metodos"].items(), key=lambda kv: -kv[1])],
        })

    # pendentes (blocos sem NF) e não identificados
    fora_set = set(fora_idx)
    pend_out = []
    for b in at.get("pendentes", []):
        idx = b["idx"]
        rot = b["bloco"]
        rotulo = ("Palete " + rot[4:] if rot.startswith("pal:") else
                  "Pedido do romaneio " + rot[4:] if rot.startswith("ped:") else "Todas as caixas do produto")
        pend_out.append({
            "id": b["id"], "p": b["p"], "nome": nome(idx[0]), "bloco": rotulo, "cx": len(idx),
            "kg": round(sum(caixas[i]["kg"] for i in idx), 3),
            "fora_cx": sum(1 for i in idx if i in fora_set),
            "crit_cx": sum(1 for i in idx if aval[i] and aval[i][1] * 100.0 > PCT_CRITICO * aval[i][0] + 1e-9),
            "opcoes": [{"nf": nf, "empresa": info_nf.get(nf, {}).get("empresa", ""),
                        "segmento": info_nf.get(nf, {}).get("segmento", "")} for nf in b["opcoes"]],
            "escolha": esc.get(b["id"], ""),
        })
    nao_ident: dict = {}
    for i in at.get("nao_identificados", []):
        c = caixas[i]
        d = nao_ident.setdefault(c["p"], {"p": c["p"], "nome": nome(i), "cx": 0, "kg": 0.0, "fora_cx": 0})
        d["cx"] += 1
        d["kg"] = round(d["kg"] + c["kg"], 3)
        if i in fora_set:
            d["fora_cx"] += 1
    sem_data: dict = {}
    for i in sem_data_idx:
        c = caixas[i]
        d = sem_data.setdefault((nf_de[i], c["p"]), {"nf": nf_de[i], "p": c["p"], "nome": nome(i), "cx": 0, "kg": 0.0})
        d["cx"] += 1
        d["kg"] = round(d["kg"] + c["kg"], 3)

    # segmentos: o que entra em cada e-mail e o que impede
    pend_fora_segs: set = set()
    pend_fora_sem_seg = False
    for i in fora_idx:
        b = pend_por_caixa.get(i)
        if not b:
            continue
        for nf in b["opcoes"]:
            s = info_nf.get(nf, {}).get("segmento", "")
            if s:
                pend_fora_segs.add(s)
            else:
                pend_fora_sem_seg = True
    empresas_sem_seg = sorted({d["empresa"] for d in info_nf.values() if not d["segmento"] and d["empresa"]})
    segs = {}
    for s in SEGMENTOS:
        d = cfg["segmentos"][s]
        linhas_s = [f for f in fora if f["segmento"] == s]
        bloqueio = ""
        if not linhas_s:
            bloqueio = "Nenhum produto do segmento fora da regra."
        elif s in pend_fora_segs:
            bloqueio = "Há caixas fora da regra sem NF definida. Escolha a NF na lista de caixas a confirmar."
        elif not d["para"]:
            bloqueio = f"Cadastre os destinatários do {s.lower()} em Parâmetros."
        segs[s] = {
            "empresas": d["empresas"], "para": d["para"], "cc": d["cc"],
            "nfs": sorted({f["nf"] for f in linhas_s}),
            "cx": sum(f["cx"] for f in linhas_s), "kg": round(sum(f["kg"] for f in linhas_s), 3),
            "valor": (round(sum(f["valor"] for f in linhas_s), 2)
                      if linhas_s and all(f["valor"] is not None for f in linhas_s) else None),
            "bloqueio": bloqueio,
        }

    total_rom_g = sum(_g(c["kg"]) for c in caixas)
    total_nf_g = sum(_g(l["kg"]) for l in linhas)
    total_nf_cx = sum(int(l["cx"] or 0) for l in linhas)
    avisos = list(json.loads(r["avisos"] or "[]"))
    if len(caixas) != total_nf_cx or abs(total_rom_g - total_nf_g) > len(linhas):
        avisos.insert(0, f"O romaneio não fecha com as NFs do card ({len(caixas)} cx / {_kg_br(total_rom_g / 1000)} kg "
                         f"no romaneio; {total_nf_cx} cx / {_kg_br(total_nf_g / 1000)} kg nas NFs). "
                         "Confira se o romaneio é desta carreta e se está completo.")
    if empresas_sem_seg:
        avisos.append("Empresa sem segmento nos Parâmetros: " + ", ".join(empresas_sem_seg)
                      + ". Os produtos dela não entram em nenhum e-mail.")
    if pend_fora_sem_seg and not empresas_sem_seg:
        avisos.append("Há caixas a confirmar com NF de empresa sem segmento.")

    out = {
        "card_id": r["card_id"], "placa": r["placa"], "origem": r["origem"],
        "nfs_card": [x for x in (r["nfs"] or "").split("/") if x],
        "arquivo": r["arquivo"], "usuario": r["usuario"], "analisado_em": r["analisado_em"],
        "referencia": _br(ref), "pct": pct,
        "totais": {"romaneio_cx": len(caixas), "romaneio_kg": round(total_rom_g / 1000, 3),
                   "nfs_cx": total_nf_cx, "nfs_kg": round(total_nf_g / 1000, 3),
                   "fora_cx": len(fora_idx), "fora_kg": round(sum(caixas[i]["kg"] for i in fora_idx), 3),
                   "sem_data_cx": len(sem_data_idx)},
        "nfs": nfs_out, "fora": fora, "pendentes": pend_out,
        "nao_identificados": sorted(nao_ident.values(), key=lambda x: x["p"]),
        "sem_data": sorted(sem_data.values(), key=lambda x: (x["nf"], x["p"])),
        "segmentos": segs, "avisos": avisos,
    }
    resumo = {"fora_cx": len(fora_idx), "total_cx": len(caixas),
              "pendentes_fora": sum(1 for i in fora_idx if i in pend_por_caixa),
              "nfs": out["nfs_card"], "analisado_em": r["analisado_em"], "referencia": out["referencia"]}
    if json.loads(r["resumo"] or "{}") != resumo:
        with get_db() as db:
            db.execute("UPDATE romaneio_regra_analise SET resumo=? WHERE conta=? AND card_id=?",
                       (json.dumps(resumo, ensure_ascii=False), conta, card_id))
    return out


def resumos(conta: str) -> dict:
    """{card_id: resumo} das análises da conta — para o selo no card."""
    with get_db() as db:
        garantir_tabelas(db)
        rows = db.execute("SELECT card_id, resumo FROM romaneio_regra_analise WHERE conta=?", (conta,)).fetchall()
    out = {}
    for r in rows:
        try:
            d = json.loads(r["resumo"] or "{}")
        except ValueError:
            d = {}
        if d:
            out[r["card_id"]] = d
    return out


# ---------------------------------------------------------------------------
# e-mail (.eml para o Outlook clássico)
# ---------------------------------------------------------------------------

def _kg_br(v) -> str:
    return f"{v:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _rs_br(v) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _int_br(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def _pct_br(v) -> str:
    t = f"{v:.1f}".replace(".", ",")
    return t[:-2] if t.endswith(",0") else t


def _faixa(a, b, fmt=str) -> str:
    """'31' quando igual; '31 a 32' quando varia."""
    return fmt(a) if a == b else f"{fmt(a)} a {fmt(b)}"


def _faixa_datas(datas: list) -> str:
    """'15/10/2026'; '15/10 a 16/10/2026' no mesmo ano; datas completas se o ano muda."""
    ds = sorted({d for d in datas if d}, key=lambda s: (s[6:], s[3:5], s[:2]))
    if not ds:
        return ""
    a, b = ds[0], ds[-1]
    if a == b:
        return a
    return f"{a[:5]} a {b}" if a[6:] == b[6:] else f"{a} a {b}"


# acima deste % da vida útil consumida o produto vai em vermelho, no topo do e-mail
PCT_CRITICO = 80.0


def _critico(f: dict) -> bool:
    """Mais de PCT_CRITICO% da vida útil consumida (conta exata, sem o arredondamento da tela)."""
    return bool(f["vida"]) and f["idade"] * 100.0 > PCT_CRITICO * f["vida"] + 1e-9


def _condensar(linhas: list) -> list:
    """Uma linha por NF + produto + máximo permitido (o e-mail não precisa de uma
    linha por data de produção): soma caixas, kg e valor; produção, validade,
    vida, idade e % da vida viram intervalo. As caixas acima de PCT_CRITICO%
    da vida ficam numa linha própria (crítica). Ordem: por local de estoque (sem
    intercalar locais); em cada local, críticas primeiro e depois do produto
    mais velho (maior idade) para o mais novo."""
    grupos: dict = {}
    for f in linhas:
        g = grupos.setdefault((f["nf"], f["p"], f["permitido"], _critico(f)), [])
        g.append(f)
    out = []
    for (nf, p, perm, crit), fs in grupos.items():
        vidas = [f["vida"] for f in fs]
        idades = [f["idade"] for f in fs]
        cons = [f["consumido"] for f in fs if f["consumido"] is not None]
        valores = [f["valor"] for f in fs]
        out.append({
            "nf": nf, "pedido": fs[0]["pedido"], "carga": fs[0]["carga"], "empresa": fs[0]["empresa"],
            "local": fs[0].get("local", ""),
            "p": p, "nome": next((f["nome"] for f in fs if f["nome"]), ""), "permitido": perm,
            "critico": crit,
            "cx": sum(f["cx"] for f in fs), "kg": round(sum(f["kg"] for f in fs), 3),
            "producao": _faixa_datas([f["producao"] for f in fs]),
            "validade": _faixa_datas([f["validade"] for f in fs]),
            "vida": _faixa(min(vidas), max(vidas)),
            "idade": _faixa(min(idades), max(idades)),
            "consumido": (_faixa(min(cons), max(cons), _pct_br) + "%") if cons else "",
            "preco": fs[0]["preco"],
            "valor": round(sum(valores), 2) if all(v is not None for v in valores) else None,
            "_idade_max": max(idades), "_cons_max": max(cons) if cons else 0.0,
        })

    def _ordem_cod(p):
        return (0, int(p), "") if p.isdigit() else (1, 0, p)
    out.sort(key=lambda x: (x["local"] == "", x["local"], not x["critico"], -x["_idade_max"], -x["_cons_max"],
                            x["nf"] == "", x["nf"], _ordem_cod(x["p"]), x["permitido"]))
    return out


def montar_email(res: dict, segmento: str) -> tuple[str, bytes]:
    """(nome do arquivo, conteúdo .eml). Levanta ValueError se o segmento
    está bloqueado (NF a confirmar, sem destinatário, sem produto)."""
    if segmento not in SEGMENTOS:
        raise ValueError("Segmento inválido.")
    seg = res["segmentos"][segmento]
    if seg["bloqueio"]:
        raise ValueError(seg["bloqueio"])
    linhas = _condensar([f for f in res["fora"] if f["segmento"] == segmento])
    nfs = seg["nfs"]
    placa = res["placa"] or "sem placa"
    empresas = sorted({f["empresa"] for f in linhas if f["empresa"]})
    info_nf = {n["nf"]: n for n in res["nfs"]}
    emissoes = sorted({info_nf[nf]["emissao"] for nf in nfs if info_nf.get(nf, {}).get("emissao")})
    pct = _pct_br(res["pct"])

    assunto = (f"Produtos fora da regra de data | {placa} | NF {', '.join(nfs)} | {segmento}"
               + (f" ({', '.join(empresas)})" if empresas else ""))
    origem = f" (origem {res['origem']})" if res.get("origem") else ""
    emitidas = ((", emitidas em " if len(nfs) > 1 else ", emitida em ") + ", ".join(emissoes)) if emissoes else ""
    intro = (f"Na conferência da carreta {placa}{origem}, NF {', '.join(nfs)}{emitidas}, os produtos abaixo "
             f"estão fora da regra de data de produção do CD: o máximo permitido é de {pct}% da vida útil "
             f"consumida na data da análise ({res['referencia']}).")
    total = f"Total fora da regra: {_int_br(seg['cx'])} caixas · {_kg_br(seg['kg'])} kg"
    if seg["valor"] is not None:
        total += f" · R$ {_rs_br(seg['valor'])}"
    total += "."

    cab = ["NF", "Pedido", "Local de estoque", "Código", "Produto", "Caixas", "Peso líq. (kg)", "Produção", "Validade",
           "Vida útil (dias)", "Idade (dias)", "% da vida", "R$/kg", "Valor (R$)"]
    # subtotal por NF (o comercial confere nota a nota): a lista segue a idade, não a NF
    por_nf = []
    if len(nfs) > 1:
        for nf in nfs:
            da_nf = [f for f in linhas if f["nf"] == nf]
            if not da_nf:
                continue
            v = [f["valor"] for f in da_nf]
            ped = da_nf[0]["pedido"] + (f"-{da_nf[0]['carga']}" if da_nf[0]["carga"] else "")
            loc = (info_nf.get(nf) or {}).get("local", "")
            por_nf.append(f"NF {nf} (pedido {ped}" + (f" · {loc}" if loc else "") + "): "
                          f"{_int_br(sum(f['cx'] for f in da_nf))} caixas · "
                          f"{_kg_br(sum(f['kg'] for f in da_nf))} kg"
                          + (f" · R$ {_rs_br(sum(v))}" if all(x is not None for x in v) else "") + ".")
    n_crit = sum(1 for f in linhas if f["critico"])
    pct_crit = _pct_br(PCT_CRITICO)
    varios_locais = len({f["local"] for f in linhas}) > 1
    ordem_txt = ("Lista por local de estoque e, em cada local, do produto mais velho para o mais novo."
                 if varios_locais else "Lista do produto mais velho para o mais novo.")
    crit_txt = (f"Em vermelho, no topo{' de cada local' if varios_locais else ''}: "
                f"mais de {pct_crit}% da vida útil consumida.")
    legenda = ordem_txt + (" " + crit_txt if n_crit else "")

    vermelho = "#c0392b"

    def est(extra: str = "", cor: str = "") -> str:
        return ('style="border:1px solid #c8ccd2;padding:4px 7px;font:12px Calibri,Arial,sans-serif'
                + (f";color:{cor}" if cor else "") + (f";{extra}" if extra else "") + '"')
    th = ('style="border:1px solid #c8ccd2;padding:5px 7px;font:bold 12px Calibri,Arial,sans-serif;'
          'background:#1f3a5f;color:#ffffff;text-align:left"')
    tot_s = ('style="border:1px solid #c8ccd2;padding:5px 7px;font:bold 12px Calibri,Arial,sans-serif;'
             'background:#eef1f5;text-align:right"')
    tot_l = ('style="border:1px solid #c8ccd2;padding:5px 7px;font:bold 12px Calibri,Arial,sans-serif;'
             'background:#eef1f5"')
    corpo = []
    for f in linhas:
        # acima de PCT_CRITICO%: linha inteira em vermelho e negrito
        cor = vermelho if f["critico"] else ""
        neg = ";font-weight:bold" if f["critico"] else ""
        td, tdc = est(neg.lstrip(";"), cor), est("white-space:nowrap" + neg, cor)
        tdn = est("text-align:right;white-space:nowrap" + neg, cor)
        ped = f["pedido"] + (f"-{f['carga']}" if f["carga"] else "")
        # máximo permitido em destaque logo após o nome do produto
        produto = f"{html.escape(f['nome'])} <b>(máx. {f['permitido']} dias)</b>"
        cel = [(f["nf"], tdc), (ped, tdc), (f["local"] or "—", tdc), (f["p"], tdc), None, (_int_br(f["cx"]), tdn),
               (_kg_br(f["kg"]), tdn), (f["producao"], tdc), (f["validade"], tdc), (f["vida"], tdn),
               (f["idade"], tdn), (f["consumido"], tdn),
               (_rs_br(f["preco"]) if f["preco"] is not None else "—", tdn),
               (_rs_br(f["valor"]) if f["valor"] is not None else "—", tdn)]
        corpo.append("<tr>" + "".join(
            f"<td {td}>{produto}</td>" if c is None else f"<td {c[1]}>{html.escape(str(c[0]))}</td>"
            for c in cel) + "</tr>")
    corpo.append(
        f"<tr><td {tot_l} colspan=\"5\">Total</td><td {tot_s}>{_int_br(seg['cx'])}</td>"
        f"<td {tot_s}>{_kg_br(seg['kg'])}</td><td {tot_l} colspan=\"6\"></td>"
        f"<td {tot_s}>{_rs_br(seg['valor']) if seg['valor'] is not None else '—'}</td></tr>")
    p = 'style="font:13px Calibri,Arial,sans-serif;margin:0 0 10px"'
    p_nf = 'style="font:13px Calibri,Arial,sans-serif;margin:0 0 3px"'
    p_leg = 'style="font:12px Calibri,Arial,sans-serif;margin:12px 0 4px;color:#555555"'
    leg_html = html.escape(ordem_txt)
    if n_crit:
        leg_html += f" <span style=\"color:{vermelho};font-weight:bold\">{html.escape(crit_txt)}</span>"
    corpo_html = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        f"<p {p}>Prezados,</p>"
        f"<p {p}>{html.escape(intro)}</p>"
        f"<p {p if not por_nf else p_nf}><b>{html.escape(total)}</b></p>"
        + "".join(f"<p {p_nf}>{html.escape(t)}</p>" for t in por_nf)
        + f"<p {p_leg}>{leg_html}</p>"
        "<table style=\"border-collapse:collapse;margin:0 0 14px\" cellspacing=\"0\" cellpadding=\"0\">"
        "<tr>" + "".join(f"<th {th}>{html.escape(c)}</th>" for c in cab) + "</tr>"
        + "".join(corpo) + "</table>"
        f"<p {p}>Atenciosamente,</p>"
        "</body></html>")

    texto = ["Prezados,", "", intro, "", total] + por_nf + ["", legenda, ""]
    for f in linhas:
        texto.append((f"[MAIS DE {pct_crit}% DA VIDA] " if f["critico"] else "")
                     + f"NF {f['nf']} | local {f['local'] or '—'} | {f['p']} {f['nome']} (máx. {f['permitido']} dias) | "
                     f"{_int_br(f['cx'])} cx | "
                     f"{_kg_br(f['kg'])} kg | produção {f['producao']} | validade {f['validade']} | "
                     f"idade {f['idade']} de {f['vida']} dias ({f['consumido']} da vida) | "
                     f"R$/kg {_rs_br(f['preco']) if f['preco'] is not None else '—'} | "
                     f"R$ {_rs_br(f['valor']) if f['valor'] is not None else '—'}")
    texto += ["", "Atenciosamente,"]

    msg = EmailMessage()
    msg["To"] = ", ".join(seg["para"])
    if seg["cc"]:
        msg["Cc"] = ", ".join(seg["cc"])
    msg["Subject"] = assunto
    msg["X-Unsent"] = "1"          # o Outlook abre como rascunho, pronto para enviar
    msg.set_content("\n".join(texto))
    msg.add_alternative(corpo_html, subtype="html")
    nome = re.sub(r"[^A-Za-z0-9 _.-]", "", f"Regra de data - {placa} - {segmento}.eml")
    return nome, msg.as_bytes(policy=SMTP)


# ---------------------------------------------------------------------------
# resumo do dia: todas as carretas com romaneio analisado na data
# ---------------------------------------------------------------------------
# Pedido de 16/11/2026: o e-mail por veículo continua sendo o que o comercial
# usa para agir na carga; o resumo é o retrato do dia (gerência), um por
# segmento, com uma linha por carreta e os produtos acima de PCT_CRITICO%.
# "Dia" = dia da análise (a mesma data em que a idade é contada).

DIAS_RESUMO = 31              # dias com análise listados na tela do resumo


def _juntar_txt(vals) -> str:
    """Textos distintos em ordem alfabética, separados por " / ". Aceita valores
    já juntados (" / "): o mesmo conjunto de locais vira sempre o mesmo texto,
    sem repetir e sem depender da ordem das linhas."""
    partes = set()
    for v in vals:
        for x in str(v or "").split(" / "):
            if x.strip():
                partes.add(x.strip())
    return " / ".join(sorted(partes))


def dias_com_analise(conta: str) -> list[dict]:
    """Dias com romaneio analisado (mais recente primeiro) e quantas carretas."""
    with get_db() as db:
        garantir_tabelas(db)
        rows = db.execute("""SELECT referencia, COUNT(*) AS n FROM romaneio_regra_analise
                             WHERE conta=? AND referencia<>'' GROUP BY referencia
                             ORDER BY referencia DESC LIMIT ?""", (conta, DIAS_RESUMO)).fetchall()
    return [{"dia": r["referencia"], "dia_br": _br(_iso(r["referencia"])), "carretas": r["n"]}
            for r in rows if _iso(r["referencia"])]


def destinatarios_resumo(cfg_seg: dict) -> tuple[list, list, bool]:
    """(para, cc, próprios?): os do resumo do dia quando cadastrados; senão os do segmento."""
    if cfg_seg.get("resumo_para"):
        return list(cfg_seg["resumo_para"]), list(cfg_seg.get("resumo_cc") or []), True
    return list(cfg_seg.get("para") or []), list(cfg_seg.get("cc") or []), False


def resumo_dia(conta: str, dia: date, cfg: dict | None = None) -> dict:
    """Carretas com análise no dia, por segmento: uma linha por carreta
    (NFs do segmento) e a lista dos produtos acima de PCT_CRITICO%."""
    cfg = cfg or config(conta)
    with get_db() as db:
        garantir_tabelas(db)
        ids = [r["card_id"] for r in db.execute(
            "SELECT card_id FROM romaneio_regra_analise WHERE conta=? AND referencia=? ORDER BY analisado_em",
            (conta, dia.isoformat())).fetchall()]
    carretas = []
    for cid in ids:
        try:
            res = resultado(conta, cid, cfg)
        except Exception as e:      # uma análise com problema não derruba o resumo
            print(f"[Romaneio] Análise {cid} fora do resumo do dia: {e}")
            continue
        if res:
            carretas.append(res)

    segs = {}
    for s in SEGMENTOS:
        linhas, criticos = [], []
        for res in carretas:
            nfs_s = [n for n in res["nfs"] if n["segmento"] == s]
            if not nfs_s:
                continue
            fora_s = [f for f in res["fora"] if f["segmento"] == s]
            crit_s = [f for f in fora_s if _critico(f)]
            # caixa fora da regra sem NF definida, com NF possível neste segmento
            pend_s = [p for p in res["pendentes"]
                      if not p["escolha"] and any(o["segmento"] == s for o in p["opcoes"])]
            pend = sum(p["fora_cx"] for p in pend_s)
            pend_crit = sum(p.get("crit_cx", 0) for p in pend_s)
            sit = []
            if pend:
                sit.append(f"{_int_br(pend)} cx fora da regra com NF a confirmar"
                           + (f" ({_int_br(pend_crit)} acima de {_pct_br(PCT_CRITICO)}% da vida)" if pend_crit else ""))
            # NF que é opção de bloco a confirmar fica sem essas caixas até a
            # escolha: a diferença dela é da escolha pendente, não do romaneio
            nfs_pend = {o["nf"] for p in res["pendentes"] if not p["escolha"] for o in p["opcoes"]}
            if any(not n["confere"] and n["nf"] not in nfs_pend for n in nfs_s):
                sit.append("romaneio diferente da NF")
            valores = [f["valor"] for f in fora_s]
            # base = caixas da NF (com caixa a confirmar, o romaneio da NF fica incompleto até a escolha)
            cx = sum(int(n["cx"] or 0) for n in nfs_s)
            fora_cx = sum(f["cx"] for f in fora_s)
            linhas.append({
                "card_id": res["card_id"], "placa": res["placa"], "origem": res["origem"],
                "nfs": [n["nf"] for n in nfs_s],
                "empresas": _juntar_txt(n["empresa"] for n in nfs_s),
                "local": _juntar_txt(n["local"] for n in nfs_s),
                "cx": cx, "fora_cx": fora_cx,
                "fora_pct": round(100.0 * fora_cx / cx, 1) if cx else None,
                "fora_kg": round(sum(f["kg"] for f in fora_s), 3),
                "valor": round(sum(valores), 2) if all(v is not None for v in valores) else None,
                "crit_cx": sum(f["cx"] for f in crit_s),
                "pend_crit_cx": pend_crit,
                "destaque": bool(crit_s) or pend_crit > 0,     # vermelho: acima de PCT_CRITICO%, com ou sem NF
                "consumido_max": max((f["consumido"] for f in fora_s if f["consumido"] is not None), default=None),
                "situacao": "; ".join(sit),
                "usuario": res["usuario"], "analisado_em": res["analisado_em"],
            })
            for c in _condensar(crit_s):
                c["placa"] = res["placa"]
                c["card_id"] = res["card_id"]
                criticos.append(c)
        # por local (sem intercalar); em cada local, carreta com crítico primeiro e mais caixas fora no topo
        linhas.sort(key=lambda l: (l["local"] == "", l["local"], not l["destaque"], -l["fora_cx"], l["placa"]))
        # críticos: por local e, dentro dele, na ordem das carretas da tabela (sem intercalar placas)
        rank = {l["card_id"]: i for i, l in enumerate(linhas)}
        criticos.sort(key=lambda c: (c["local"] == "", c["local"], rank.get(c["card_id"], 0),
                                     -c["_idade_max"], -c["_cons_max"], c["nf"], c["p"]))
        cs = cfg["segmentos"][s]
        para, cc, proprios = destinatarios_resumo(cs)
        com_fora = [l for l in linhas if l["fora_cx"]]
        valores = [l["valor"] for l in com_fora]
        cx_tot = sum(l["cx"] for l in linhas)
        fora_tot = sum(l["fora_cx"] for l in linhas)
        tot = {"carretas": len(linhas), "com_fora": len(com_fora), "cx": cx_tot, "fora_cx": fora_tot,
               "fora_pct": round(100.0 * fora_tot / cx_tot, 1) if cx_tot else None,
               "fora_kg": round(sum(l["fora_kg"] for l in linhas), 3),
               "valor": round(sum(valores), 2) if all(v is not None for v in valores) else None,
               "crit_cx": sum(l["crit_cx"] for l in linhas),
               "pend_crit_cx": sum(l["pend_crit_cx"] for l in linhas),
               "com_situacao": sum(1 for l in linhas if l["situacao"])}
        bloqueio = ""
        if not linhas:
            bloqueio = "Nenhuma carreta do segmento com romaneio analisado neste dia."
        elif not para:
            bloqueio = f"Cadastre os destinatários do {s.lower()} em Parâmetros."
        segs[s] = {"empresas": cs["empresas"], "para": para, "cc": cc, "destinatarios_proprios": proprios,
                   "carretas": linhas, "criticos": criticos, "totais": tot, "bloqueio": bloqueio}
    # NF de empresa fora dos segmentos: não entra em e-mail nenhum, mas a tela avisa
    sem_segmento = []
    for res in carretas:
        nfs_x = [n for n in res["nfs"] if not n["segmento"]]
        if nfs_x:
            sem_segmento.append({"card_id": res["card_id"], "placa": res["placa"],
                                 "nfs": [n["nf"] for n in nfs_x],
                                 "empresas": _juntar_txt(n["empresa"] for n in nfs_x)})
    return {"dia": dia.isoformat(), "dia_br": _br(dia), "pcts": sorted({res["pct"] for res in carretas}),
            "carretas": len(carretas), "segmentos": segs, "sem_segmento": sem_segmento}


def montar_email_resumo(rd: dict, segmento: str) -> tuple[str, bytes]:
    """(nome do arquivo, .eml) do resumo do dia de um segmento."""
    if segmento not in SEGMENTOS:
        raise ValueError("Segmento inválido.")
    seg = rd["segmentos"][segmento]
    if seg["bloqueio"]:
        raise ValueError(seg["bloqueio"])
    t = seg["totais"]
    dia = rd["dia_br"]
    empresas = ", ".join(seg["empresas"])
    pct_crit = _pct_br(PCT_CRITICO)
    pcts = " / ".join(_pct_br(p) + "%" for p in (rd["pcts"] or [PCT_PADRAO]))

    assunto = (f"Resumo do dia | Regra de data do CD | {dia} | {segmento}" + (f" ({empresas})" if empresas else ""))
    intro = (f"Segue o resumo da regra de data de produção do CD das carretas com romaneio analisado em {dia}: "
             f"o máximo permitido é de {pcts} da vida útil consumida na data da análise. "
             "O detalhe de cada carreta está no e-mail por veículo.")
    total = (f"{_int_br(t['carretas'])} {'carreta analisada' if t['carretas'] == 1 else 'carretas analisadas'} · "
             f"{_int_br(t['com_fora'])} com produto fora da regra · {_int_br(t['fora_cx'])} "
             f"{'caixa' if t['fora_cx'] == 1 else 'caixas'} fora · {_kg_br(t['fora_kg'])} kg")
    if t["valor"] is not None and t["fora_cx"]:
        total += f" · R$ {_rs_br(t['valor'])}"
    if t["crit_cx"]:
        total += f" · {_int_br(t['crit_cx'])} com mais de {pct_crit}% da vida útil"
    if t["pend_crit_cx"]:
        total += f" · {_int_br(t['pend_crit_cx'])} com mais de {pct_crit}% da vida útil e NF a confirmar"
    destaque = bool(t["crit_cx"] or t["pend_crit_cx"])
    total += "."
    varios_locais = len({l["local"] for l in seg["carretas"]}) > 1
    ordem_txt = ("Carretas por local de estoque e, em cada local, da que tem mais caixas fora para a que tem menos."
                 if varios_locais else "Carretas da que tem mais caixas fora para a que tem menos.")
    crit_txt = (f"Em vermelho, no topo{' de cada local' if varios_locais else ''}: carreta com produto acima de "
                f"{pct_crit}% da vida útil consumida.")

    vermelho = "#c0392b"

    def est(extra: str = "", cor: str = "") -> str:
        return ('style="border:1px solid #c8ccd2;padding:4px 7px;font:12px Calibri,Arial,sans-serif'
                + (f";color:{cor}" if cor else "") + (f";{extra}" if extra else "") + '"')
    th = ('style="border:1px solid #c8ccd2;padding:5px 7px;font:bold 12px Calibri,Arial,sans-serif;'
          'background:#1f3a5f;color:#ffffff;text-align:left"')
    tot_s = ('style="border:1px solid #c8ccd2;padding:5px 7px;font:bold 12px Calibri,Arial,sans-serif;'
             'background:#eef1f5;text-align:right;white-space:nowrap"')
    tot_l = ('style="border:1px solid #c8ccd2;padding:5px 7px;font:bold 12px Calibri,Arial,sans-serif;'
             'background:#eef1f5"')

    def celulas(cel) -> str:
        return "".join(f"<td {s}>{html.escape(str(v))}</td>" for v, s in cel)

    # carretas
    cab = ["Placa", "Origem", "NF", "Local de estoque", "Caixas", "Caixas fora", "% fora", "Peso fora (kg)",
           "Valor fora (R$)", f"Acima de {pct_crit}% (cx)", "Maior % da vida", "Situação"]
    corpo = []
    for l in seg["carretas"]:
        cor = vermelho if l["destaque"] else ""
        neg = ";font-weight:bold" if l["destaque"] else ""
        td, tdc = est(neg.lstrip(";"), cor), est("white-space:nowrap" + neg, cor)
        tdn = est("text-align:right;white-space:nowrap" + neg, cor)
        tem = l["fora_cx"] > 0
        sit = l["situacao"] or ("Dentro da regra" if not tem else "—")
        corpo.append("<tr>" + celulas([
            (l["placa"] or "sem placa", tdc), (l["origem"] or "—", tdc), (", ".join(l["nfs"]), td),
            (l["local"] or "—", tdc), (_int_br(l["cx"]), tdn), (_int_br(l["fora_cx"]), tdn),
            ((_pct_br(l["fora_pct"]) + "%") if l["fora_pct"] is not None else "—", tdn),
            (_kg_br(l["fora_kg"]) if tem else "—", tdn),
            (_rs_br(l["valor"]) if tem and l["valor"] is not None else "—", tdn),
            (_int_br(l["crit_cx"]) if l["crit_cx"] else "—", tdn),
            ((_pct_br(l["consumido_max"]) + "%") if tem and l["consumido_max"] is not None else "—", tdn),
            (sit, td)]) + "</tr>")
    corpo.append(
        f"<tr><td {tot_l} colspan=\"4\">Total</td><td {tot_s}>{_int_br(t['cx'])}</td>"
        f"<td {tot_s}>{_int_br(t['fora_cx'])}</td>"
        f"<td {tot_s}>{(_pct_br(t['fora_pct']) + '%') if t['fora_pct'] is not None else '—'}</td>"
        f"<td {tot_s}>{_kg_br(t['fora_kg']) if t['fora_cx'] else '—'}</td>"
        f"<td {tot_s}>{_rs_br(t['valor']) if t['valor'] is not None and t['fora_cx'] else '—'}</td>"
        f"<td {tot_s}>{_int_br(t['crit_cx']) if t['crit_cx'] else '—'}</td><td {tot_l} colspan=\"2\"></td></tr>")

    # produtos acima de PCT_CRITICO%
    crit_html = ""
    if seg["criticos"]:
        cab2 = ["Placa", "NF", "Local de estoque", "Código", "Produto", "Caixas", "Peso líq. (kg)", "Produção",
                "Idade (dias)", "% da vida", "Valor (R$)"]
        tdv, tdvc = est("font-weight:bold", vermelho), est("white-space:nowrap;font-weight:bold", vermelho)
        tdvn = est("text-align:right;white-space:nowrap;font-weight:bold", vermelho)
        linhas2 = []
        for c in seg["criticos"]:
            produto = f"{html.escape(c['nome'])} (máx. {c['permitido']} dias)"
            linhas2.append(
                "<tr>" + celulas([(c["placa"] or "sem placa", tdvc), (c["nf"], tdvc), (c["local"] or "—", tdvc),
                                  (c["p"], tdvc)])
                + f"<td {tdv}>{produto}</td>"
                + celulas([(_int_br(c["cx"]), tdvn), (_kg_br(c["kg"]), tdvn), (c["producao"], tdvc),
                           (c["idade"], tdvn), (c["consumido"], tdvn),
                           (_rs_br(c["valor"]) if c["valor"] is not None else "—", tdvn)]) + "</tr>")
        crit_html = (
            f"<p style=\"font:bold 13px Calibri,Arial,sans-serif;margin:16px 0 4px;color:{vermelho}\">"
            f"Produtos com mais de {pct_crit}% da vida útil consumida</p>"
            "<table style=\"border-collapse:collapse;margin:0 0 14px\" cellspacing=\"0\" cellpadding=\"0\">"
            "<tr>" + "".join(f"<th {th}>{html.escape(c)}</th>" for c in cab2) + "</tr>" + "".join(linhas2)
            + "</table>")

    p = 'style="font:13px Calibri,Arial,sans-serif;margin:0 0 10px"'
    p_leg = 'style="font:12px Calibri,Arial,sans-serif;margin:12px 0 4px;color:#555555"'
    leg_html = html.escape(ordem_txt)
    if destaque:
        leg_html += f" <span style=\"color:{vermelho};font-weight:bold\">{html.escape(crit_txt)}</span>"
    corpo_html = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        f"<p {p}>Prezados,</p>"
        f"<p {p}>{html.escape(intro)}</p>"
        f"<p {p}><b>{html.escape(total)}</b></p>"
        f"<p {p_leg}>{leg_html}</p>"
        "<table style=\"border-collapse:collapse;margin:0 0 14px\" cellspacing=\"0\" cellpadding=\"0\">"
        "<tr>" + "".join(f"<th {th}>{html.escape(c)}</th>" for c in cab) + "</tr>"
        + "".join(corpo) + "</table>"
        + crit_html
        + f"<p {p}>Atenciosamente,</p>"
        "</body></html>")

    texto = ["Prezados,", "", intro, "", total, "", ordem_txt + (" " + crit_txt if destaque else ""), ""]
    for l in seg["carretas"]:
        tem = l["fora_cx"] > 0
        texto.append(
            (f"[MAIS DE {pct_crit}% DA VIDA] " if l["destaque"] else "")
            + f"{l['placa'] or 'sem placa'} | origem {l['origem'] or '—'} | NF {', '.join(l['nfs'])} | "
            f"local {l['local'] or '—'} | {_int_br(l['fora_cx'])} de {_int_br(l['cx'])} cx fora"
            + (f" | {_kg_br(l['fora_kg'])} kg | R$ {_rs_br(l['valor']) if l['valor'] is not None else '—'}"
               if tem else "")
            + (f" | {_int_br(l['crit_cx'])} cx acima de {pct_crit}%" if l["crit_cx"] else "")
            + f" | {l['situacao'] or ('dentro da regra' if not tem else '—')}")
    if seg["criticos"]:
        texto += ["", f"Produtos com mais de {pct_crit}% da vida útil consumida:"]
        for c in seg["criticos"]:
            texto.append(f"{c['placa'] or 'sem placa'} | NF {c['nf']} | local {c['local'] or '—'} | {c['p']} "
                         f"{c['nome']} (máx. {c['permitido']} dias) | {_int_br(c['cx'])} cx | {_kg_br(c['kg'])} kg | "
                         f"produção {c['producao']} | idade {c['idade']} ({c['consumido']} da vida) | "
                         f"R$ {_rs_br(c['valor']) if c['valor'] is not None else '—'}")
    texto += ["", "Atenciosamente,"]

    msg = EmailMessage()
    msg["To"] = ", ".join(seg["para"])
    if seg["cc"]:
        msg["Cc"] = ", ".join(seg["cc"])
    msg["Subject"] = assunto
    msg["X-Unsent"] = "1"          # o Outlook abre como rascunho, pronto para enviar
    msg.set_content("\n".join(texto))
    msg.add_alternative(corpo_html, subtype="html")
    nome = re.sub(r"[^A-Za-z0-9 _.-]", "", f"Resumo regra de data - {dia.replace('/', '-')} - {segmento}.eml")
    return nome, msg.as_bytes(policy=SMTP)
