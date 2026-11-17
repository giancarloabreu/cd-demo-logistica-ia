"""
Mapeamento de colunas dos relatórios (20/10/2026).

Os relatórios vêm de sistemas diferentes e o layout muda: colunas trocam de
posição (o mesmo Controle de Distribuição tem as 221 colunas em ordens
diferentes no CDA e em outra unidade) e os nomes variam. Antes, cada leitor
achava as colunas do seu jeito — alguns por POSIÇÃO fixa (Estoque Faturado lia
16 campos por índice), outros por PEDAÇO do nome, que pega coluna errada sem
avisar ("Placa veículo" casava com "Tipo do veículo"). Coluna não achada
passava em silêncio, e a tela ficava vazia sem dizer por quê.

Aqui cada relatório declara os seus CAMPOS (nome interno, rótulo, nomes de
cabeçalho aceitos, se é obrigatório) e a resolução segue sempre a mesma ordem:

  1. mapeamento SALVO pelo usuário (tela "Mapeamento de colunas");
  2. nome EXATO do cabeçalho (sem acento, sem diferença de maiúscula);
  3. DETALHE de "Grupo > Detalhe", quando só uma coluna tem aquele detalhe;
  4. posição antiga (só nos leitores que já liam assim), marcada como aviso.

Nunca por pedaço do nome. O resultado diz, campo a campo, onde achou e como;
o que é obrigatório e não foi achado vira mensagem clara na tela, com a lista
das colunas do arquivo para o usuário escolher. O que ele escolher fica salvo
para todos os próximos arquivos daquele tipo.
"""
from __future__ import annotations

import json
import re
import unicodedata

from app.database import get_db

_tabelas_ok: set = set()


def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s*>\s*", ">", t.lower())
    return re.sub(r"[^a-z0-9>]", "", t)


def nomes_unicos(colunas) -> list[str]:
    """Cabeçalho repetido ganha " (2)", " (3)"… — o primeiro mantém o nome.
    Sem isso, df[nome] devolve várias colunas e o leitor quebra."""
    vistos: dict = {}
    out = []
    for c in colunas:
        c = str(c)
        n = vistos.get(c, 0) + 1
        vistos[c] = n
        out.append(c if n == 1 else f"{c} ({n})")
    return out


def _base(coluna) -> str:
    """Nome sem o sufixo de repetição (".1" do pandas, " (2)" de nomes_unicos)."""
    return _norm(re.sub(r"(\.\d+| \(\d+\))$", "", str(coluna)))


def _grupo(coluna) -> str:
    c = str(coluna)
    return _norm(c.split(">")[0]) if ">" in c else ""


def _detalhe(coluna) -> str:
    return _norm(str(coluna).split(">")[-1])


# ---------------------------------------------------------------------------
# Relatórios conhecidos
# ---------------------------------------------------------------------------
# campo: (rótulo, [nomes de cabeçalho aceitos], obrigatório, [detalhes aceitos])
TIPOS: dict = {
    "estoque_faturado": {
        "rotulo": "Estoque Faturado (painel de carretas)",
        "campos": {
            "empresa":        ("Empresa que expediu", ["Empresa Venda > Empresa", "Empresa Venda", "Empresa"], False, []),
            "data_fat":       ("Data de faturamento", ["Empresa Venda > Faturamento", "Data Faturamento", "Faturamento", "Data Emissão"], False, []),
            "nf":             ("Nota fiscal (número)", ["Empresa Venda > Número", "Número", "Numero", "NF", "Nota Fiscal"], True, []),
            "transportadora": ("Transportadora", ["Empresa Venda > Transportadora", "Transportadora"], False, ["Transportadora"]),
            "placa":          ("Placa", ["Empresa Venda > Placa", "Placa"], False, ["Placa"]),
            "empresa_compra": ("Empresa compradora (CD)", ["Empresa Compra > Empresa", "Empresa Compra", "Empresa Compradora", "Empresa Destino"], True, []),
            "local_estoque":  ("Local de estoque (compra)", ["Empresa Compra > Local de Estoque", "Local de Estoque", "Local Estoque"], False, []),
            "pedido_compra":  ("Pedido de compra", ["Empresa Compra > Pedido", "Pedido Compra", "Pedido de Compra"], False, []),
            "carga":          ("Carga (compra)", ["Empresa Compra > Carga", "Carga"], False, []),
            "cod_produto":    ("Código do produto", ["Produto > Cód Produto", "Cód Produto", "Cod Produto", "Código Produto"], False, ["Cód Produto"]),
            "temperatura":    ("Temperatura do produto", ["Produto > Temperatura", "Temperatura"], False, []),
            "linha":          ("Linha do produto", ["Produto > Linha", "Linha"], False, []),
            "produto":        ("Descrição do produto", ["Produto > Produto", "Produto", "Descrição Produto"], False, []),
            "unidade":        ("Unidade de medida", ["Produto > Unidade", "Unidade", "UN"], False, []),
            "quantidade":     ("Quantidade (kg)", ["Compra > Quantidade", "Quantidade", "Qtd", "Qtde"], False, []),
            "volume":         ("Volume (caixas)", ["Compra > Volume", "Volume", "Volumes"], False, []),
            # preço da NF (R$/kg) — e-mail da regra de data do romaneio (16/11/2026)
            "preco":          ("Preço (R$/kg)", ["Compra > Preço", "Preço", "Preco", "Preço Unitário"], False, []),
        },
        "silenciosos": ("preco",),
        # posição antiga (A=0): o leitor lia assim com 29 colunas ou mais
        "posicao_antiga": {"empresa": 0, "data_fat": 7, "nf": 13, "transportadora": 14, "placa": 15,
                           "empresa_compra": 16, "local_estoque": 17, "pedido_compra": 18, "carga": 21,
                           "cod_produto": 22, "temperatura": 23, "linha": 24, "produto": 25, "unidade": 26,
                           "quantidade": 27, "volume": 28},
        "posicao_minimo": 29,
    },
    "controle_distribuicao": {
        "rotulo": "Controle de Distribuição",
        "campos": {
            "pedido":           ("Pedido comercial", ["Pedido Comercial > Código"], False, []),
            "carga":            ("Carga", ["Pedido Comercial > Carga"], False, []),
            "instrucao_filial": ("Instrução filial (entrada)", ["Embarque/Desembarque(Fábrica) > Instrução Filial", "Instrução Filial", "Instrução de Entrada"], False, ["Instrução Filial", "Instrução de Entrada"]),
            "instrucao":        ("Instrução", ["Instrução > Código"], False, []),
            "ae":               ("Autorização de embarque (AE)", ["Instrução > Código AE (OpenTech)", "Código AE (OpenTech)", "Código AE", "Aut. de embarque"], False, ["Código AE (OpenTech)"]),
            "filial":           ("Empresa filial (sigla)", ["Empresa Filial > Sigla"], False, []),
            "operacao":         ("Operação comercial", ["Operação Comercial > Nome"], False, []),
            "terceiro":         ("Terceiro (nome)", ["Terceiro > Nome"], False, []),
            "terceiro_cidade":  ("Cidade do terceiro", ["Terceiro (Endereço) > Cidade"], False, []),
            "terceiro_uf":      ("UF do terceiro", ["Terceiro (Endereço) > UF"], False, []),
            "transportadora":   ("Transportadora", ["Transportadora > Nome"], False, []),
            "placa_cavalo":     ("Placa do cavalo", ["Veículo Cavalo > Placa", "Placa Veículo", "Placa Cavalo", "Cavalo"], False, []),
            "placa_reboque":    ("Placa da carreta (reboque)", ["Veículo Reboque > Placa", "Placa Carreta", "Placa Reboque", "Carreta"], False, []),
            "peso":             ("Peso (kg)", ["Embarque/Desembarque(Fábrica) > Peso", "Embarque Estimado (Carga) > Peso"], False, []),
            "volume":           ("Volume (caixas)", ["Embarque/Desembarque(Fábrica) > Volume", "Embarque Estimado (Carga) > Volume"], False, []),
            "nf":               ("Nota fiscal", ["Nota Fiscal > Formulário NF"], False, ["Formulário NF"]),
            "data_faturamento": ("Data de faturamento", ["Faturamento > Data Faturamento"], False, []),
            "previsao_entrega": ("Previsão de entrega", ["Previsão de Entrega (Carga) > Data Entrega"], False, []),
            "previsao_hora":    ("Hora prevista de entrega", ["Previsão de Entrega (Carga) > Hora Entrega"], False, []),
            # "CONG"/"RESF" no nome diz se a carga é congelada ou resfriada
            # (usuário, 21/10/2026) — é o que a aba Outras operações usa para
            # o alerta de temperatura
            "produto_primeiro": ("Primeiro produto do pedido", ["Produto (Pedido) > Produto (Primeiro)"], False, ["Produto (Primeiro)"]),
        },
    },
    "fila_redespacho": {
        "rotulo": "Fila Redespacho",
        "campos": {
            # sem a chave da NF nenhuma linha vira card (a chave decide se há
            # trânsito) e sem o nome do redespacho não há CD: os dois faltando
            # deixavam a aba vazia sem aviso
            "chave":            ("Chave da NF de origem", ["Instrução Origem > Chave NF", "Chave", "Chave NF-e", "Chave NFe", "Chave de Acesso", "Chave Acesso"], True, ["Chave NF", "Chave NFe", "Chave NF-e"]),
            "nome":             ("Empresa redespacho (nome com a sigla do CD)", ["Empresa Redespacho > Nome", "Nome Redespacho"], True, ["redespacho>Nome"]),
            "formulario":       ("Nota fiscal de origem (formulário)", ["Instrução Origem > Formulário NF", "Formulário", "Formulario", "NF", "Nota Fiscal"], False, ["Formulário NF"]),
            "origem":           ("Empresa de origem (que expediu)", ["Empresa Instrução > Nome", "Empresa Origem", "Filial Origem"], False, ["empresa+instrucao>Nome"]),
            "expedidora":       ("Empresa expedidora (quando vem separada da origem)", ["Empresa Expedidora", "Expedidor", "Filial Expedidora"], False, []),
            "placa":            ("Placa do veículo de entrada", ["Veículo Ped. Entrada > Placa", "Placa", "Placa Veículo", "Placa Carreta"], False, ["entrada>Placa"]),
            "pedido":           ("Pedido de entrada", ["Pedido Entrada > Código", "Pedido Entrada", "Pedido de Entrada", "Pedido Compra"], False, ["^pedido+entrada>Código"]),
            "instrucao_filial": ("Instrução filial (entrada)", ["Pedido Entrada > Instrução Filial", "Instrução Filial", "Instrução de Entrada", "Instrução Entrada", "Pedido Entrada > Instrução"], False, ["entrada>Instrução Filial"]),
            "instrucao_origem": ("Instrução de origem", ["Instrução > Origem"], False, ["instrucao>Origem"]),
            "pedido_filial":    ("Pedido filial", ["Pedido Entrada > Pedido Filial"], False, ["entrada>Pedido Filial"]),
            "latitude":         ("Latitude do veículo", ["Localização Veículo Rastreado > Latitude", "Latitude", "Latitude última posição"], False, ["Latitude"]),
            "longitude":        ("Longitude do veículo", ["Localização Veículo Rastreado > Longitude", "Longitude", "Longitude última posição"], False, ["Longitude"]),
        },
        # a empresa de origem era lida pela coluna T (índice 19)
        "posicao_antiga": {"origem": 19},
        # o relatório real não tem coluna própria de expedidora (vale a origem)
        "silenciosos": ("expedidora",),
        "posicao_minimo": 20,
    },
    "opentech": {
        "rotulo": "Relatório da OpenTech (painel de monitoração)",
        "campos": {
            # carreta OU cavalo: faltando as duas, nada é rastreado e a leitura
            # para (painel_monitoramento_service); com uma delas, segue
            "placa_carreta":   ("Placa da carreta", ["Placa carreta", "Placa Reboque", "Carreta"], False, []),
            "placa_veiculo":   ("Placa do cavalo (veículo)", ["Placa veículo", "Placa Cavalo", "Veículo"], False, []),
            "km":              ("Km restante", ["Km Restante"], False, []),
            "posicao":         ("Última posição", ["Última posição"], False, []),
            "ref_cidade":      ("Referência da posição (cidade)", ["Referência posição cidade", "Referência posição"], False, []),
            "latitude":        ("Latitude da última posição", ["Latitude última posição", "Latitude"], False, []),
            "longitude":       ("Longitude da última posição", ["Longitude última posição", "Longitude"], False, []),
            "temperatura":     ("Última temperatura recebida", ["Última temperatura recebida", "Temperatura"], False, []),
            "ignicao":         ("Situação da ignição", ["Situação ignição"], False, []),
            "sit_veiculo":     ("Situação do veículo", ["Situação do veículo"], False, []),
            "ae":              ("Autorização de embarque (AE)", ["Aut. de embarque", "Autorização de embarque", "AE"], False, []),
            "sit_viagem":      ("Situação da viagem", ["Situação viagem"], False, []),
        },
    },
    "estoque_redespacho": {
        "rotulo": "Estoque Redespacho",
        "campos": {
            "chave": ("Chave da NF (origem)", ["Origem > Chave NFE", "Chave NFE", "Chave NF-e", "Chave NFe", "Chave de Acesso", "Chave Acesso", "Chave"], True, ["Chave NFE", "Chave NF-e"]),
        },
    },
}


def garantir_tabelas(db) -> None:
    from app import database
    chave = str(database.DB_PATH)
    if chave in _tabelas_ok:
        return
    db.executescript("""
        CREATE TABLE IF NOT EXISTS mapeamento_coluna (
            tipo          TEXT NOT NULL,
            campo         TEXT NOT NULL,
            coluna        TEXT NOT NULL,
            atualizado_por TEXT NOT NULL DEFAULT '',
            atualizado_em TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (tipo, campo)
        );
        CREATE TABLE IF NOT EXISTS mapeamento_ultimo_arquivo (
            tipo        TEXT PRIMARY KEY,
            colunas     TEXT NOT NULL,
            arquivo     TEXT NOT NULL DEFAULT '',
            usuario     TEXT NOT NULL DEFAULT '',
            carregado_em TEXT NOT NULL DEFAULT ''
        );
    """)
    _tabelas_ok.add(chave)


def registrar_tipo(tipo: str, definicao: dict) -> None:
    """Outros módulos (Expedito) declaram os relatórios deles aqui."""
    TIPOS[tipo] = definicao


def mapeamentos_salvos(tipo: str) -> dict:
    with get_db() as db:
        garantir_tabelas(db)
        return {r["campo"]: r["coluna"] for r in db.execute(
            "SELECT campo, coluna FROM mapeamento_coluna WHERE tipo=?", (tipo,)).fetchall()}


def salvar(tipo: str, campo: str, coluna: str, usuario: str = "") -> None:
    """coluna vazia apaga o mapeamento salvo (volta ao automático)."""
    from app.services.carretas_viagem import _agora, _txt
    if tipo not in TIPOS or campo not in TIPOS[tipo]["campos"]:
        raise ValueError("Campo desconhecido.")
    with get_db() as db:
        garantir_tabelas(db)
        if not str(coluna or "").strip():
            db.execute("DELETE FROM mapeamento_coluna WHERE tipo=? AND campo=?", (tipo, campo))
            return
        db.execute("""INSERT INTO mapeamento_coluna (tipo, campo, coluna, atualizado_por, atualizado_em)
                      VALUES (?,?,?,?,?)
                      ON CONFLICT(tipo, campo) DO UPDATE SET coluna=excluded.coluna,
                      atualizado_por=excluded.atualizado_por, atualizado_em=excluded.atualizado_em""",
                   (tipo, campo, str(coluna), usuario, _txt(_agora())))


def lembrar_arquivo(tipo: str, colunas, arquivo: str = "", usuario: str = "") -> None:
    """Guarda as colunas do último arquivo de cada tipo, para a tela de
    mapeamento listar as opções sem pedir o arquivo de novo."""
    from app.services.carretas_viagem import _agora, _txt
    colunas_json = json.dumps([str(c) for c in colunas], ensure_ascii=False)
    with get_db() as db:
        garantir_tabelas(db)
        atual = db.execute("SELECT colunas, arquivo, carregado_em FROM mapeamento_ultimo_arquivo WHERE tipo=?",
                           (tipo,)).fetchone()
        if atual and atual["colunas"] == colunas_json and not usuario:
            # mesmo layout: atualiza nome e hora no máximo a cada 30 min (o
            # ciclo da OpenTech relê a cada 5 min — a tela mostrava o 1º arquivo)
            from datetime import timedelta
            limite = _txt(_agora() - timedelta(minutes=30))
            if atual["arquivo"] != arquivo or (atual["carregado_em"] or "") < limite:
                db.execute("UPDATE mapeamento_ultimo_arquivo SET arquivo=?, carregado_em=? WHERE tipo=?",
                           (arquivo, _txt(_agora()), tipo))
            return
        db.execute("""INSERT OR REPLACE INTO mapeamento_ultimo_arquivo
                      (tipo, colunas, arquivo, usuario, carregado_em) VALUES (?,?,?,?,?)""",
                   (tipo, colunas_json, arquivo, usuario, _txt(_agora())))


def ultimo_arquivo(tipo: str) -> dict:
    with get_db() as db:
        garantir_tabelas(db)
        r = db.execute("SELECT * FROM mapeamento_ultimo_arquivo WHERE tipo=?", (tipo,)).fetchone()
    if not r:
        return {}
    return {"colunas": json.loads(r["colunas"]), "arquivo": r["arquivo"], "usuario": r["usuario"],
            "carregado_em": r["carregado_em"]}


def resolver(tipo: str, colunas) -> dict:
    """{campo: {"coluna", "como", "obrigatorio", "rotulo"}} para as colunas do
    arquivo. "como": salvo | exato | detalhe | posicao | "" (não achada)."""
    defi = TIPOS[tipo]
    colunas = [str(c) for c in colunas]
    por_nome = {}
    for c in colunas:
        por_nome.setdefault(_norm(c), c)
    por_detalhe: dict = {}
    for c in colunas:
        por_detalhe.setdefault(_detalhe(c), []).append(c)
    bases: dict = {}
    for c in colunas:
        bases[_base(c)] = bases.get(_base(c), 0) + 1
    tem_posicao = bool(defi.get("posicao_antiga")) and len(colunas) >= defi.get("posicao_minimo", 10 ** 6)
    salvos = mapeamentos_salvos(tipo)
    # relatório com regra própria (o TXT do Produto Paletizado acha as colunas
    # repetidas pela vizinhança): ela substitui nome exato e detalhe
    automatico = defi["resolvedor"](colunas) if defi.get("resolvedor") else None
    usados: set = set()
    res = {}
    for campo, (rotulo, nomes, obrig, detalhes) in defi["campos"].items():
        coluna, como = None, ""
        s = salvos.get(campo)
        if automatico is not None:
            # regra própria (TXT): o salvo só preenche o que ela não achou —
            # mesma ordem de paletizado_colunas.resolver_com_mapeamento
            if automatico.get(campo):
                coluna, como = automatico[campo], "automatico"
            elif s and _norm(s) in por_nome:
                coluna, como = por_nome[_norm(s)], "salvo"
        elif s and _norm(s) in por_nome:
            coluna, como = por_nome[_norm(s)], "salvo"
        if coluna is None and automatico is None:
            for n in nomes:
                achada = por_nome.get(_norm(n))
                if achada is None or achada in usados:
                    continue
                # nome genérico ("Local de Estoque", "Carga") que se repete no
                # arquivo: com o cabeçalho de uma linha só, o Estoque Faturado
                # tem os dois — o de venda vem primeiro e é o errado. Quando há
                # a posição antiga para o campo, não escolhe e vai para ela
                # (revisão 21/10). Sem posição (ex.: "Qtde" e "Unidade" do
                # Follow up, que se repetem), fica o primeiro, como sempre foi
                if (">" not in n and bases.get(_base(achada), 0) > 1 and tem_posicao
                        and campo in defi["posicao_antiga"]):
                    continue
                coluna, como = achada, "exato"
                break
        if coluna is None:
            for d in detalhes:
                if ">" in d:
                    # "^pedido+entrada>Código": o detalhe dentro do grupo que
                    # tem essas palavras ("^" = o grupo começa por ela) —
                    # sobrevive ao grupo renomeado ("Pedido Entrada" ->
                    # "Pedido de Entrada") sem pegar "Veículo Pedido Entrada"
                    g, dd = d.split(">", 1)
                    toks = [t.strip() for t in g.split("+") if t.strip()]

                    def casa(grupo):
                        return grupo and all(grupo.startswith(_norm(t[1:])) if t.startswith("^")
                                             else _norm(t) in grupo for t in toks)
                    achadas = [c for c in por_detalhe.get(_norm(dd), [])
                               if c not in usados and casa(_grupo(c))]
                else:
                    achadas = [c for c in por_detalhe.get(_norm(d), []) if c not in usados]
                if len(achadas) == 1:
                    coluna, como = achadas[0], "detalhe"
                    break
        if coluna is None and defi.get("posicao_antiga") and len(colunas) >= defi.get("posicao_minimo", 10 ** 6):
            i = defi["posicao_antiga"].get(campo)
            if i is not None and i < len(colunas) and colunas[i] not in usados:
                coluna, como = colunas[i], "posicao"
        if coluna is not None:
            usados.add(coluna)
        res[campo] = {"coluna": coluna, "como": como, "obrigatorio": obrig, "rotulo": rotulo}
    return res


def resolver_arquivo(tipo: str, colunas, arquivo: str = "", avisos_out: list | None = None) -> dict:
    """O que os leitores chamam: resolve, guarda o cabeçalho para a tela de
    mapeamento e levanta ColunasFaltando se faltar obrigatório. Devolve
    {campo: coluna do arquivo ou None}."""
    colunas = [str(c) for c in colunas]
    try:
        lembrar_arquivo(tipo, colunas, arquivo)
    except Exception as e:                      # a leitura não depende disso
        print(f"[Mapeamento] cabeçalho de {tipo} não guardado: {e}")
    res = resolver(tipo, colunas)
    falta = faltando(res)
    if falta:
        raise ColunasFaltando(tipo, falta, colunas)
    if avisos_out is not None:
        avisos_out.extend(avisos(res, TIPOS[tipo].get("silenciosos", ())))
    return {campo: r["coluna"] for campo, r in res.items()}


def faltando(resolucao: dict) -> list[dict]:
    """Obrigatórios não achados."""
    return [{"campo": k, "rotulo": v["rotulo"]} for k, v in resolucao.items()
            if v["obrigatorio"] and not v["coluna"]]


def avisos(resolucao: dict, silenciosos=()) -> list[str]:
    """Campos lidos pela posição antiga e opcionais não achados (que ficam
    vazios). `silenciosos`: opcionais que costumam faltar sem prejuízo."""
    out = []
    for campo, v in resolucao.items():
        if v["como"] == "posicao":
            out.append(f"{v['rotulo']}: lida pela posição da coluna ({v['coluna']}) — confira o mapeamento")
        elif not v["coluna"] and not v["obrigatorio"] and campo not in silenciosos:
            out.append(f"{v['rotulo']}: coluna não encontrada no arquivo — o campo fica vazio")
    return out


class ColunasFaltando(ValueError):
    """Obrigatórios não achados: a tela abre o mapeamento com estes campos."""

    def __init__(self, tipo: str, campos: list[dict], colunas: list[str]):
        self.tipo, self.campos, self.colunas = tipo, campos, colunas
        nomes = ", ".join(f"“{c['rotulo']}”" for c in campos)
        super().__init__(f"{TIPOS[tipo]['rotulo']}: coluna não encontrada para {nomes}. "
                         "Indique no mapeamento de colunas qual coluna do arquivo corresponde.")

    def para_tela(self) -> dict:
        return {"error": str(self), "mapeamento": {"tipo": self.tipo, "faltando": self.campos,
                                                   "colunas": self.colunas}}


def descrever(tipo: str) -> dict:
    """Para a tela: campos do tipo, mapeamento salvo, como cada um foi achado
    no último arquivo e as colunas dele."""
    ult = ultimo_arquivo(tipo)
    res = resolver(tipo, ult.get("colunas", [])) if ult else {}
    salvos = mapeamentos_salvos(tipo)
    campos = []
    for campo, (rotulo, nomes, obrig, _d) in TIPOS[tipo]["campos"].items():
        r = res.get(campo, {})
        campos.append({"campo": campo, "rotulo": rotulo, "obrigatorio": obrig, "salvo": salvos.get(campo, ""),
                       "coluna": r.get("coluna") or "", "como": r.get("como", ""), "aceitos": nomes})
    return {"tipo": tipo, "rotulo": TIPOS[tipo]["rotulo"], "campos": campos, "ultimo_arquivo": ult}
