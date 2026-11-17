"""
Leitura de .xlsx por streaming (zip + iterparse), sem carregar a planilha.

Criado para a Ferramenta de validação (11/11/2026): o export "estoque total"
do ERP tem 89 mil linhas x 62 colunas (24 MB). O pandas/openpyxl normal leva
minutos e centenas de MB de memória na e2-micro; o openpyxl read_only levou
~5 min no notebook. Lendo o XML direto, o mesmo arquivo sai em ~1 min com
memória baixa (só a lista de textos compartilhados fica em memória).
"""
from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _coluna(ref: str) -> int:
    n = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            n = n * 26 + ord(ch) - 64
        else:
            break
    return n - 1


def _primeira_planilha(z: zipfile.ZipFile) -> str:
    """Caminho da 1ª aba, pelo workbook.xml + rels (o nome nem sempre é sheet1)."""
    nomes = set(z.namelist())
    try:
        with z.open("xl/workbook.xml") as f:
            wb = ET.parse(f).getroot()
        folha = wb.find(f"{_NS}sheets/{_NS}sheet")
        rid = folha.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        with z.open("xl/_rels/workbook.xml.rels") as f:
            rels = ET.parse(f).getroot()
        for r in rels.iter(f"{_NS_REL}Relationship"):
            if r.get("Id") == rid:
                alvo = r.get("Target").lstrip("/")
                alvo = alvo if alvo.startswith("xl/") else "xl/" + alvo
                if alvo in nomes:
                    return alvo
    except Exception:
        pass
    folhas = sorted(n for n in nomes if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    if not folhas:
        raise ValueError("O arquivo não tem planilha legível.")
    return folhas[0]


def linhas_xlsx(path):
    """Gera uma lista de valores (str, float ou None) por linha da 1ª aba."""
    with zipfile.ZipFile(path) as z:
        textos: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as f:
                for _, el in ET.iterparse(f, events=("end",)):
                    if el.tag == _NS + "si":
                        textos.append("".join(t.text or "" for t in el.iter(_NS + "t")))
                        el.clear()
        with z.open(_primeira_planilha(z)) as f:
            linha: dict = {}
            for _, el in ET.iterparse(f, events=("end",)):
                tag = el.tag
                if tag == _NS + "c":
                    ref = el.get("r")
                    i = _coluna(ref) if ref else len(linha)
                    tipo = el.get("t")
                    v = el.find(_NS + "v")
                    if tipo == "s":
                        val = textos[int(v.text)] if v is not None and v.text else None
                    elif tipo == "inlineStr":
                        val = "".join(x.text or "" for x in el.iter(_NS + "t"))
                    elif tipo in ("str", "e"):
                        val = v.text if v is not None else None
                    elif tipo == "b":
                        val = (v.text == "1") if v is not None else None
                    elif v is not None and v.text is not None:
                        try:
                            val = float(v.text)
                        except ValueError:
                            val = v.text
                    else:
                        val = None
                    linha[i] = val
                    el.clear()
                elif tag == _NS + "row":
                    yield [linha.get(j) for j in range(max(linha) + 1)] if linha else []
                    linha = {}
                    el.clear()


# ── leitura rápida, em texto (Painel do Armazém, 12/11/2026) ─────────
# O upload do Armazém roda dentro da requisição (limite de 120 s na VM) e o
# Produto Paletizado em Excel tem 61 mil linhas x 52 colunas (104 MB de XML).
# O iterparse acima levou 60 a 78 s no notebook. Aqui o XML é cortado com
# str.split/find (rodam em C), sem árvore; e, depois que quem lê decide as
# colunas que usa (filtro["colunas"], definido ao ver o cabeçalho), cada
# linha busca direto essas células em vez de percorrer as 52.
# Cada célula sai como o relatório sairia em TXT: texto como está, número
# sem separador de milhar e com vírgula decimal, data dd/mm/aaaa (pelo
# formato da célula — o Excel guarda data como número de série).

import codecs as _codecs
import datetime as _dt
import re as _re
from html import unescape as _unescape

# formatos de data/hora embutidos do Excel (numFmtId)
_FMT_DATA = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
_RE_FMT_LIMPA = _re.compile(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.')


def _formato_e_data(codigo: str) -> bool:
    """Formato personalizado de data/hora: tem d, m, y, h ou s fora de texto
    entre aspas, de [cor]/[$-416] e de caracteres escapados."""
    t = _RE_FMT_LIMPA.sub("", codigo or "").lower()
    if not t or t == "general":
        return False
    return any(ch in t for ch in "dmyhs")


def _estilos_data(z: zipfile.ZipFile) -> set:
    """Índices de estilo (atributo s da célula) com formato de data."""
    if "xl/styles.xml" not in z.namelist():
        return set()
    with z.open("xl/styles.xml") as f:
        raiz = ET.parse(f).getroot()
    custom = {}
    nf = raiz.find(f"{_NS}numFmts")
    if nf is not None:
        for el in nf.iter(f"{_NS}numFmt"):
            try:
                custom[int(el.get("numFmtId"))] = el.get("formatCode") or ""
            except (TypeError, ValueError):
                pass
    datas = set()
    xfs = raiz.find(f"{_NS}cellXfs")
    if xfs is not None:
        for i, xf in enumerate(xfs.findall(f"{_NS}xf")):
            try:
                fid = int(xf.get("numFmtId") or 0)
            except ValueError:
                continue
            if fid in custom:
                if _formato_e_data(custom[fid]):
                    datas.add(i)
            elif fid in _FMT_DATA:
                datas.add(i)
    return datas


def _base_datas(z: zipfile.ZipFile) -> _dt.datetime:
    try:
        wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
    except KeyError:
        wb = ""
    if _re.search(r'date1904="(1|true)"', wb):
        return _dt.datetime(1904, 1, 1)
    return _dt.datetime(1899, 12, 30)


def _num_texto(f: float) -> str:
    if f != f:
        return ""
    if f.is_integer() and abs(f) < 1e15:
        return str(int(f))
    r = repr(f)
    if "e" in r or "E" in r:
        r = format(f, ".15g")
    return r.replace(".", ",")


def _data_texto(serial: float, base: _dt.datetime) -> str:
    try:
        d = base + _dt.timedelta(days=serial)
    except (OverflowError, ValueError):
        return _num_texto(serial)
    # arredonda ao segundo (a fração de hora vem com resíduo binário)
    d = (d + _dt.timedelta(milliseconds=500)).replace(microsecond=0)
    if 0 <= serial < 1:                    # célula só de hora (sem dia)
        return d.strftime("%H:%M:%S")
    if d.hour or d.minute or d.second:
        return d.strftime("%d/%m/%Y %H:%M:%S")
    return d.strftime("%d/%m/%Y")


class PlanilhaInvalida(ValueError):
    """Estrutura da planilha fora de qualquer relatório real (linha sem fim
    ou coluna além do limite do Excel) — protege a memória da VM."""


# limites do leitor: o Excel vai até a coluna XFD (16.384); uma linha do
# Produto Paletizado tem poucos KB de XML
_MAX_COLUNAS = 16384
_MAX_LINHA_XML = 16 * 1024 * 1024


def _sem_quebra(s: str) -> str:
    """Tab e quebra de linha viram espaço (o TXT separa campos por tab).
    Para texto já decodificado (ElementTree, xlrd)."""
    if "\t" in s or "\n" in s or "\r" in s:
        s = s.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return s


def _limpa(s: str) -> str:
    """Texto cru do XML (&amp; etc.): decodifica uma vez e tira as quebras."""
    if "&" in s:
        s = _unescape(s)
    return _sem_quebra(s)


def _atributo(cab: str, nome: str):
    k = cab.find(f" {nome}=\"")
    if k < 0:
        return None
    k += len(nome) + 3
    return cab[k:cab.find('"', k)]


def _letras(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def linhas_planilha_texto(path, filtro: dict | None = None):
    """Gera uma lista de textos por linha da 1ª aba (ver bloco acima).

    `filtro`: dicionário de quem lê. Quando ele ganha "colunas" (índices),
    as linhas seguintes trazem só essas colunas preenchidas (as demais
    vazias), com largura filtro.get("largura")."""
    filtro = filtro if filtro is not None else {}
    with zipfile.ZipFile(path) as z:
        textos: list = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as f:
                for _, el in ET.iterparse(f, events=("end",)):
                    if el.tag == _NS + "si":
                        textos.append(_sem_quebra("".join(t.text or "" for t in el.iter(_NS + "t"))))
                        el.clear()
        estilos_data = _estilos_data(z)
        base = _base_datas(z)
        colunas: dict = {}

        def col(ref: str) -> int:
            letras = ref.rstrip("0123456789")
            i = colunas.get(letras)
            if i is None:
                n = 0
                for ch in letras:
                    n = n * 26 + ord(ch) - 64
                if not 0 < n <= _MAX_COLUNAS:
                    raise PlanilhaInvalida(f"coluna fora do limite do Excel: {letras[:10]}")
                i = colunas[letras] = n - 1
            return i

        # as combinações de estilo/tipo e os valores se repetem muito (datas
        # de produção, paletes, códigos): guardar a conversão evita refazer
        tipos: dict = {}
        convertidos: dict = {}

        def numero_ou_data(v: str, e_data: bool) -> str:
            chave = (v, e_data)
            r = convertidos.get(chave)
            if r is None:
                try:
                    f = float(v)
                except ValueError:
                    r = _limpa(v)
                else:
                    r = _data_texto(f, base) if e_data else _num_texto(f)
                if len(convertidos) > 300000:
                    convertidos.clear()
                convertidos[chave] = r
            return r

        def celula(p: str, i0: int = 0):
            """Célula que começa em p[i0:] (logo depois de '<c'). Devolve
            (ref, valor); valor None quando a célula não tem conteúdo."""
            fim_cab = p.find(">", i0)
            cab = p[i0:fim_cab]
            k = cab.find(' r="')
            if k >= 0:
                e = cab.find('"', k + 4)
                ref = cab[k + 4:e]
                resto = cab[:k] + cab[e + 1:] if k else cab[e + 1:]
            else:
                ref, resto = None, cab
            info = tipos.get(resto)
            if info is None:
                s = _atributo(resto, "s")
                info = tipos[resto] = (resto.endswith("/"), _atributo(resto, "t"),
                                       bool(s and s.isdigit() and int(s) in estilos_data))
            fechada, tipo, e_data = info
            if fechada:
                return ref, None
            if tipo == "inlineStr":
                fim = p.find("</c>", fim_cab)
                partes = []
                for t in p[fim_cab:fim if fim >= 0 else len(p)].split("<t")[1:]:
                    a = t.find(">")
                    if t[:1] in (">", " ") and t[a - 1] != "/":
                        partes.append(t[a + 1:t.find("</t>")])
                return ref, _limpa("".join(partes))
            if p.startswith("<v>", fim_cab + 1):
                a = fim_cab + 1                    # o caso comum: <c ...><v>…</v></c>
            else:
                a = p.find("<v>", fim_cab)         # fórmula antes do valor, ou sem valor
                fim = p.find("</c>", fim_cab)
                if a < 0 or (0 <= fim < a):
                    return ref, None
            v = p[a + 3:p.find("</v>", a)]
            if tipo == "s":
                return ref, (textos[int(v)] if v else "")
            if tipo is None or tipo == "n":
                return ref, numero_ou_data(v, e_data)
            if tipo in ("str", "e"):
                return ref, _limpa(v)
            if tipo == "b":
                return ref, ("1" if v == "1" else "0")
            if tipo == "d":                        # data ISO (OOXML estrito)
                try:
                    return ref, _dt.datetime.fromisoformat(v[:19]).strftime("%d/%m/%Y")
                except ValueError:
                    return ref, _limpa(v)
            return ref, numero_ou_data(v, e_data)  # tipo desconhecido: como número

        def completa(corpo: str) -> list:
            celulas: dict = {}
            prox = 0
            for p in corpo.split("<c")[1:]:
                if not p or p[0] not in " />":
                    continue                       # outra tag que começa com "c"
                ref, val = celula(p)
                i = col(ref) if ref else prox
                prox = i + 1
                celulas[i] = "" if val is None else val   # vazia mantém a largura
            return [celulas.get(c, "") for c in range(max(celulas) + 1)] if celulas else []

        def parcial(corpo: str, rnum: str, cols: list, largura: int) -> list:
            out = [""] * largura
            pos = 0
            for i, letras in cols:
                chave = f'<c r="{letras}{rnum}"'
                k = corpo.find(chave, pos)
                if k < 0 and pos:
                    k = corpo.find(chave)          # fora de ordem no XML
                if k < 0:
                    continue                       # célula ausente = vazia
                pos = k
                _ref, val = celula(corpo, k + 2)
                if val is not None:
                    out[i] = val
            return out

        def linhas_de(trecho: str):
            """Trecho até um </row>: linhas vazias <row .../> antes e a linha
            aberta que termina neste </row>."""
            k = trecho.rfind("<row")
            if k < 0:
                return
            for _ in range(trecho.count("<row", 0, k)):
                yield []                           # <row .../> sem células
            j = trecho.find(">", k)
            if trecho[j - 1] == "/":
                yield []
                return
            corpo = trecho[j + 1:]
            sel = filtro.get("colunas")
            rnum = _atributo(trecho[k:j], "r") if sel else None
            if sel and rnum and '<c r="' in corpo:
                cols = filtro.get("_cols")
                if cols is None or filtro.get("_sel") is not sel:
                    cols = filtro["_cols"] = [(i, _letras(i)) for i in sorted(sel)]
                    filtro["_sel"] = sel
                largura = max(filtro.get("largura") or 0, cols[-1][0] + 1 if cols else 0)
                yield parcial(corpo, rnum, cols, largura)
            else:
                yield completa(corpo)

        with z.open(_primeira_planilha(z)) as f:
            dec = _codecs.getincrementaldecoder("utf-8")()
            resto = ""
            while True:
                bloco = f.read(1 << 22)
                resto += dec.decode(bloco, final=not bloco)
                partes = resto.split("</row>")
                resto = partes.pop()               # linha incompleta: espera o próximo bloco
                if len(resto) > _MAX_LINHA_XML:
                    raise PlanilhaInvalida("linha da planilha acima do limite de tamanho")
                for trecho in partes:
                    yield from linhas_de(trecho)
                if not bloco:
                    break
            # depois do último </row>: só linhas vazias <row .../>
            for _ in resto.split("<row")[1:]:
                yield []


def linhas_xls_texto(path):
    """.xls antigo, no mesmo formato de texto de linhas_planilha_texto. O
    xlrd lê o arquivo inteiro (o .xls tem no máximo 65 mil linhas)."""
    import pandas as pd
    df = pd.read_excel(path, header=None, dtype=object, engine="xlrd",
                       keep_default_na=False, na_filter=False)
    for r in df.itertuples(index=False):
        linha = []
        for v in r:
            if v is None or (isinstance(v, float) and v != v):
                linha.append("")
            elif isinstance(v, bool):
                linha.append("1" if v else "0")
            elif isinstance(v, _dt.datetime):
                linha.append(v.strftime("%d/%m/%Y %H:%M:%S") if (v.hour or v.minute or v.second)
                             else v.strftime("%d/%m/%Y"))
            elif isinstance(v, _dt.date):
                linha.append(v.strftime("%d/%m/%Y"))
            elif isinstance(v, (int, float)):
                linha.append(_num_texto(float(v)))
            elif str(v) == "NaT":
                linha.append("")
            else:
                linha.append(_sem_quebra(str(v)))
        while linha and linha[-1] == "":
            linha.pop()
        yield linha
