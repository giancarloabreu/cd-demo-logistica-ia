"""
Resolve as colunas do relatório "Produto Paletizado" pelo CABEÇALHO, em vez
de cravar índices fixos (01/09/2026).

**Por que isso existe.** Os dois parsers do relatório (`armazem_service` e
`mapa_estoque_ocupacao`) liam por posição fixa, com uma justificativa que
era válida: os nomes de coluna se repetem — "Código" aparece 4x e "Nome"
3x — então nome sozinho não serve de chave. Só que o layout NÃO é estável
entre empresas: comparando um export do CD Demo com um de outra unidade
(anexados pelo usuário 01/09/2026), o arquivo do CDD tem 55 colunas contra
62 do CDA, e o que muda é:

  - o bloco de classificação (índices 0 a 6) sai em ordem diferente, porque
    segue a "Estrutura" escolhida na hora de gerar o relatório;
  - o NOME do produto cai no índice 12 no CDD e no 10 no CDA;
  - o CDA tem uma coluna "Saída Estoque" (38) que o CDD não tem, o que
    empurra Endereçamento, Validade e tudo depois em 1 posição;
  - o CDD não tem as colunas finais (Lote, Sequencial, Banda...).

Resultado prático: o produto do CDD aparecia vazio no Mapa de Estoque
(índice 10 estava em branco) e a validade vinha de outra coluna.

**Como resolve.** Colunas de nome único casam direto. As repetidas são
resolvidas por ÂNCORA — a posição relativa a uma coluna de nome único que
fica ao lado, e essa vizinhança é estável nos dois arquivos:

  - "Código"/"Nome" do PRODUTO  → os últimos antes de "CB"
  - "Nome" do LOCAL DE ESTOQUE  → o imediatamente antes de "Rastreabilidade"
  - Código/Nome/Sigla da RESERVA → contados pra trás a partir de "Sigla"

Coluna que não existir no arquivo simplesmente não entra no mapa — quem
chama trata como ausente (é o caso de "Lote" no export do CDD).
"""
from __future__ import annotations
import codecs
import re
import unicodedata

# nome normalizado do cabeçalho -> chave usada pelos parsers. Só entram
# aqui as colunas de nome ÚNICO no arquivo; as repetidas vêm por âncora.
_UNICAS = {
    "temperatura": "temperatura",
    "tipodeproduto": "tipo_produto",
    "linha": "linha",
    "destino": "destino",
    "familia": "familia",
    "familiasubgrupo": "familia_sub",
    "maturado": "maturado",
    "cb": "cb",
    "contramarca": "contra_marca",
    "camara": "camara",
    "box": "box",
    "tipo": "tipo",
    "rua": "rua",
    "palete": "palete",
    "altura": "altura",
    "profundidade": "profundidade",
    "temperaturamedida": "temperatura_medida",
    "usuario": "usuario",
    "pesobruto": "peso_bruto",
    "pesoliquido": "peso_liquido",
    "pesomedio": "peso_medio",
    "hab": "hab",
    "periodo": "periodo",
    "ciclo": "ciclo",
    "loteembarque": "lote_embarque",
    "rastreabilidade": "rastreabilidade",
    "pecas": "pecas",
    "status": "status",
    "producao": "producao",
    "saidaestoque": "saida_estoque",
    "embalagem": "embalagem",
    "enderecamento": "enderecamento",
    "validade": "validade",
    "pedidocomercial": "pedido_comercial",
    "terceiro": "terceiro",
    "carga": "carga",
    "instrucao": "instrucao",
    "sifprodutor": "sif_produtor",
    "certsanitario": "cert_sanitario",
    "notafiscal": "nota_fiscal",
    "unidade": "unidade",
    "area": "area",
    "media": "media",
    "lote": "lote",
    "sequencial": "sequencial",
    "banda": "banda",
    "codhablitacao": "cod_habilitacao",
    "siglahabilitacao": "sigla_habilitacao",
}


def normalizar(texto: str) -> str:
    """'Peso Líquido ' -> 'pesoliquido'; 'Cód. Hablitação' -> 'codhablitacao'."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return "".join(c for c in t.lower() if c.isalnum())


def detectar_codificacao(path) -> str:
    """Codificação do TXT, pela amostra do início (20/10/2026).

    Antes o arquivo era aberto sempre em cp1252. Exportado num computador
    configurado em UTF-8, o cabeçalho continuava sendo achado (Box, Altura e
    Profundidade não têm acento), mas "Peso Líquido", "Produção",
    "Endereçamento" e "Saída Estoque" viravam outra coisa — peso zerado e
    datas vazias, sem erro nenhum. Foi o que fez o Expedito não gerar plano
    depois da troca de máquina. O cabeçalho tem acento e fica no começo do
    arquivo, então a amostra decide com segurança."""
    with open(path, "rb") as f:
        amostra = f.read(65536)
    if amostra.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if amostra.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if amostra and amostra.count(b"\x00") > len(amostra) // 4:   # UTF-16 sem BOM
        return "utf-16-le" if amostra[1:2] == b"\x00" else "utf-16-be"
    try:
        amostra.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        if len(amostra) == 65536 and e.start >= len(amostra) - 3:
            return "utf-8"                    # a amostra cortou um caractere ao meio
        return "cp1252"


def ler_linhas_txt(path) -> list[str]:
    """readlines() do TXT na codificação certa (ver detectar_codificacao)."""
    with open(path, encoding=detectar_codificacao(path), errors="replace") as f:
        return f.readlines()


def estilo_numero(pesos, paletes=()) -> str:
    """"br" (1.234,56) ou "ponto" (1234.56), olhando o arquivo inteiro.

    O relatório real sai no padrão brasileiro: peso "31,000" e palete
    "123.456". Num computador com outro formato regional o peso viria
    "31.000" — lido no padrão brasileiro, 31 toneladas por caixa. Vírgula no
    peso decide pelo brasileiro; ponto com casas diferentes de 3, ou palete
    com vírgula de milhar, decide pelo ponto. Sem pista, fica o brasileiro
    (o comportamento de sempre)."""
    for v in pesos:
        v = str(v or "").strip()
        if "," in v:
            return "br"
        if "." in v:
            inteiro, dec = v.split(".", 1)
            if "." in dec or len(dec) != 3 or len(inteiro.lstrip("-")) > 3 or inteiro.lstrip("-").startswith("0"):
                return "ponto" if "." not in dec else "br"
    for v in paletes:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", str(v or "").strip()):
            return "ponto"
    return "br"


def numero(texto, estilo: str = "br") -> float:
    t = str(texto or "").strip()
    if not t:
        return 0.0
    try:
        if estilo == "ponto":
            return float(t.replace(",", ""))
        return float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def estilo_data(valores) -> str:
    """"dmy" (23/06/2026, o do relatório) ou "mdy" (6/23/2026). Decide pelo
    primeiro valor com dia maior que 12 numa das posições."""
    for v in valores:
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", str(v or ""))
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            return "dmy"
        if b > 12:
            return "mdy"
    return "dmy"


def data(texto, estilo: str = "dmy"):
    """Data do relatório: dd/mm/aaaa (ou mm/dd conforme o arquivo), dd/mm/aa
    e aaaa-mm-dd, com ou sem hora. None quando não dá para ler."""
    import datetime as _dt
    t = str(texto or "").strip()
    if not t:
        return None
    try:
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
        if m:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t)
        if m:
            a, b, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
            ano = ano + 2000 if ano < 100 else ano
            dia, mes = (b, a) if estilo == "mdy" else (a, b)
            return _dt.date(ano, mes, dia)
    except ValueError:
        return None
    return None


# Campos que a tela de mapeamento oferece (e que o Expedito exige). As
# colunas de nome repetido ("Código", "Nome") aparecem na tela numeradas —
# "Nome (2)" — na ordem do arquivo.
CAMPOS_MAPEAVEIS = {
    "temperatura":    ("Temperatura", True),
    "local_nome":     ("Local de estoque (nome)", True),
    "produto_codigo": ("Código do produto", False),
    "produto_nome":   ("Nome do produto", False),
    "tipo_produto":   ("Tipo de produto", False),
    "palete":         ("Palete", False),
    "tipo":           ("Tipo do endereço (Picking / Estocagem)", False),
    "camara":         ("Câmara", False),
    "rua":            ("Rua", False),
    "box":            ("Box", False),
    "altura":         ("Altura", False),
    "profundidade":   ("Profundidade", False),
    "peso_bruto":     ("Peso bruto", False),
    "peso_liquido":   ("Peso líquido", False),
    "producao":       ("Produção", False),
    "validade":       ("Validade", False),
    "enderecamento":  ("Endereçamento", False),
    "reserva_codigo": ("Reserva (código)", False),
    "reserva_nome":   ("Reserva (nome)", False),
    "reserva_sigla":  ("Reserva (sigla)", False),
}


def _nomes_tela(cabecalho: list[str]) -> list[str]:
    from app.services.mapeamento_colunas import nomes_unicos
    return nomes_unicos([str(c).strip() for c in cabecalho])


def _resolvedor_mapeamento(colunas: list[str]) -> dict:
    """Para a tela de mapeamento: {campo: coluna} achado pelo cabeçalho.
    `colunas` já vem com os nomes de tela (repetidas numeradas)."""
    originais = [re.sub(r" \(\d+\)$", "", c) for c in colunas]
    col = resolver_colunas(originais)
    return {campo: colunas[i] for campo, i in col.items() if campo in CAMPOS_MAPEAVEIS and i < len(colunas)}


def _registrar_tipo():
    from app.services import mapeamento_colunas
    mapeamento_colunas.registrar_tipo("produto_paletizado", {
        "rotulo": "Produto Paletizado (TXT de estoque)",
        "campos": {k: (rot, [], obrig, []) for k, (rot, obrig) in CAMPOS_MAPEAVEIS.items()},
        "resolvedor": _resolvedor_mapeamento,
    })


def resolver_com_mapeamento(cabecalho: list[str], obrigatorios=("temperatura", "local_nome"),
                            arquivo: str = "") -> dict[str, int]:
    """resolver_colunas + o que o usuário salvou na tela de mapeamento (vale
    primeiro) + a checagem dos obrigatórios. Levanta ColunasFaltando."""
    from app.services import mapeamento_colunas
    _registrar_tipo()
    col = resolver_colunas(cabecalho)
    tela = _nomes_tela(cabecalho)
    try:
        mapeamento_colunas.lembrar_arquivo("produto_paletizado", tela, arquivo)
    except Exception as e:
        print(f"[Mapeamento] cabeçalho do TXT não guardado: {e}")
    # O mapeamento é global e "Nome (2)" muda de sentido entre os layouts das
    # empresas: o salvo preenche só o que a leitura pelo cabeçalho não achou
    # — nunca troca o que ela achou (revisão 21/10/2026: um mapeamento salvo
    # no Expedito de um CD estragava o Armazém de outro)
    for campo, nome in mapeamento_colunas.mapeamentos_salvos("produto_paletizado").items():
        if campo not in col and nome in tela:
            col[campo] = tela.index(nome)
    falta = [{"campo": c, "rotulo": CAMPOS_MAPEAVEIS.get(c, (c,))[0]} for c in obrigatorios if c not in col]
    if falta:
        raise mapeamento_colunas.ColunasFaltando("produto_paletizado", falta, tela)
    return col


def achar_cabecalho(linhas: list[str], limite: int = 60) -> int | None:
    """Índice da linha de cabeçalho — a que traz Box, Altura e Profundidade.

    Procura por NOME em qualquer posição (antes exigia 'Box' na coluna 16 e
    'Altura' na 20, o que só valia pro layout do CDA). O número de linhas de
    metadado antes do cabeçalho também varia: 6 no CDA, 7 no CDD, porque o
    export do CDD traz uma linha a mais ("Local de Estoque"). O limite subiu
    de 20 para 60 linhas (20/10/2026): um filtro a mais na exportação
    acrescenta linhas de metadado."""
    for i, linha in enumerate(linhas[:limite]):
        nomes = {normalizar(c) for c in linha.rstrip("\n").split("\t")}
        if {"box", "altura", "profundidade"} <= nomes:
            return i
    return None


def resolver_colunas(cabecalho: list[str]) -> dict[str, int]:
    """Mapa {chave: índice} a partir da linha de cabeçalho já separada."""
    nomes = [normalizar(c) for c in cabecalho]
    col: dict[str, int] = {}

    # 1) nomes únicos, direto. Se repetir (não deveria), fica a 1ª ocorrência.
    for i, nome in enumerate(nomes):
        chave = _UNICAS.get(nome)
        if chave and chave not in col:
            col[chave] = i

    # 2) repetidas, por âncora
    i_cb = col.get("cb")
    if i_cb is not None:
        codigos = [i for i, n in enumerate(nomes) if n == "codigo" and i < i_cb]
        nomes_prod = [i for i, n in enumerate(nomes) if n == "nome" and i < i_cb]
        if codigos:
            col["produto_codigo"] = codigos[-1]
        if nomes_prod:
            col["produto_nome"] = nomes_prod[-1]

    # reserva: o trio Código/Nome/Sigla no fim. "Sigla" sozinho é único
    # ("Sigla Habilitação" normaliza diferente), então serve de âncora.
    i_sigla = col.get("sigla")
    if i_sigla is None:
        siglas = [i for i, n in enumerate(nomes) if n == "sigla"]
        i_sigla = siglas[0] if siglas else None
    if i_sigla is not None and i_sigla >= 2:
        col["reserva_sigla"] = i_sigla
        col["reserva_nome"] = i_sigla - 1
        col["reserva_codigo"] = i_sigla - 2

    i_rastro = col.get("rastreabilidade")
    if i_rastro is not None:
        anteriores = [i for i, n in enumerate(nomes) if n == "nome" and i < i_rastro]
        # o "Nome" do local de estoque é o que fica colado em Rastreabilidade
        if anteriores and anteriores[-1] != col.get("produto_nome"):
            col["local_nome"] = anteriores[-1]
    if "local_nome" not in col:
        # Sem "Rastreabilidade" (coluna suprimida no ERP para caber na
        # exportação em TXT — export do CDB de 11/11/2026, 35 colunas): o
        # "Nome" do local é o único entre o do produto e o trio da reserva.
        # Com mais de um candidato não arrisca: fica para o mapeamento.
        # O local vem antes dos blocos Estoque/Data em todos os layouts
        # conhecidos; a reserva, depois. Limitar o fim por eles impede que,
        # sem a "Sigla", o Nome da reserva seja tomado pelo do local.
        inicio = col.get("produto_nome", i_cb if i_cb is not None else len(nomes))
        fim = min([col[k] for k in ("reserva_codigo", "pecas", "status", "producao",
                                    "embalagem", "enderecamento", "validade") if k in col]
                  or [len(nomes)])
        candidatos = [i for i, n in enumerate(nomes) if n == "nome" and inicio < i < fim]
        if len(candidatos) == 1:
            col["local_nome"] = candidatos[0]
    if "local_nome" in col:
        codigos = [i for i, n in enumerate(nomes) if n == "codigo" and i < col["local_nome"]]
        if codigos and codigos[-1] != col.get("produto_codigo"):
            col["local_codigo"] = codigos[-1]

    return col


def valor(campos: list[str], col: dict[str, int], chave: str, padrao: str = "") -> str:
    """Lê um campo com bounds-check — a exportação corta tabs finais, então
    uma linha pode ter menos colunas do que o cabeçalho prometeu."""
    i = col.get(chave)
    if i is None or i >= len(campos):
        return padrao
    return campos[i].strip()
