"""
Follow up de pedidos — a ponte entre o Excel do ERP e a demanda do picking
(Expedito, 06/10/2026).

O arquivo real tem 140 colunas e duas linhas de cabeçalho (a primeira nomeia
GRUPOS, a segunda as colunas), traz todas as empresas da companhia — inclusive
as que não são do CD — e mistura as duas pontas das transferências entre
coligadas. Este módulo recorta tudo isso e entrega a demanda no formato que
picking_abastecimento.agrupar_demanda consome.

Decisões que valem ouro aqui, todas confirmadas no arquivo real e com o
usuário entre 01 e 06/10/2026:

  - "Quantidade" NÃO é contagem de caixa: é o arredondamento de "Qtde"
    (bate em 945 de 945 linhas). Quem conta caixa é "Volume Estimado", cujo
    peso por volume é constante dentro de cada SKU nos 161 produtos do
    arquivo — uma etiqueta por caixa, como o usuário descreveu.
  - "Qtde" não está sempre em quilo: a unidade vem na coluna "Unidade"
    (KG, PC, CX, CXS). Peso sai sempre de "Peso Líquido Estimado".
  - Os limites de embarque são IDADE DESDE A PRODUÇÃO, em dias, e o vendedor
    pode pôr de 0 a n — resfriado chega a 60, congelado a 733. Não existe
    teto a validar.
  - Coligada: somente as SAÍDAS. O prefixo de "Operação Comercial" marca
    E- (entrada) e S- (saída), e a linha E- é o espelho da S- da outra
    empresa: contar as duas mandaria separar mercadoria que está chegando.
  - Bloqueio de pedido é escolha do usuário, não regra fixa: PRC e PRL caem
    na força-tarefa das 18:30, Status e Limite costumam não cair. O módulo
    devolve o CATÁLOGO do que apareceu para a tela de parâmetros marcar.

Nada é descartado em silêncio: cada corte aparece no funil, com a contagem.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import unicodedata

import pandas as pd

from app.services import mapeamento_colunas

# o que entra na conta quando o usuário ainda não configurou nada
PARAMETROS_PADRAO = {
    # "alguns vendedores utilizam data de embarque e outros utilizam data da
    # entrega" (usuário, 06/10/2026) — por isso o padrão olha as duas
    "usar_data_embarque": True,
    "usar_data_entrega": True,
    # situações de pedido aceitas além de Liberado. A força-tarefa libera
    # PRC e PRL por volta das 18:30; Status e Limite podem nem cair.
    "bloqueios_aceitos": ["PRC", "PRL"],
    # Tipo de venda (coluna "Operação Comercial"): o que entra na conta é
    # escolha do usuário, igual ao Giro. O parâmetro guarda o que fica FORA e
    # a tela mostra caixas do que entra — assim um tipo novo do ERP nasce
    # CONSIDERADO. Errar incluindo infla a demanda e o operador vê; errar
    # excluindo deixa pedido sem separar, que é o estrago grave.
    # Casa por pedaço do nome, sem ligar para caixa.
    # Padrão: redespacho fora, porque ele já sai pronto — um dos turnos sempre
    # deixa o redespacho montado (usuário, 08/10/2026).
    "tipos_venda_fora": ["Redespacho"],
    # Pedido desdobrado: mãe e filhas convivem no arquivo com peso nas duas.
    # Fora por padrão, porque a filha é que carrega a demanda.
    "considerar_pedido_mae": False,
    # Estágio do pedido ("Aguardando") que entra no abastecimento: APENAS
    # "02-Instrução" (usuário, 07/10/2026) — são os pedidos em que o
    # roteirizador ainda não mexeu, e por isso são os que o picking precisa
    # cobrir. Tudo depois disso já tem destino definido: transferência,
    # transporte e embarque já estão em movimento, e faturado e cancelado
    # saem antes, pela situação da entrega.
    "aguardando_aceitos": ["02-INSTRUÇÃO"],
}

_COLS = {
    "temperatura": "TEMPERATURA",
    "empresa": "Empresa de Entrega",
    "local_codigo": "Local Estoque",
    "local": "Nome Local Estoque",
    "produto": "Produto",
    "nome": "Nome Produto",
    "descricao": "Descrição Detalhada",
    "kg": "Peso Líquido Estimado",
    "caixas": "Volume Estimado",
    "qtde": "Qtde",
    "unidade": "Unidade",
    "pendente": "Qtde Pendente",
    "limite_min": "Limite Mínimo Embarque",
    "limite_max": "Limite Máximo Embarque",
    "data_embarque": "Data de Embarque",
    "data_entrega": "Data de Entrega",
    "situacao_entrega": "Situação da Entrega",
    "situacao_pedido": "Situação do Pedido",
    "cancelamento": "Data de Cancelamento",
    "operacao": "Operação Comercial",
    "pedido": "Pedido",
    "pedido_principal": "Pedido Principal",
    "entrega": "Entrega",
    "item": "Item",
    "cliente": "Nome do Terceiro",
    "classificacao": "Nome Classificação de Preço",
    "reserva": "Nome Reserva",
    "reserva_codigo": "Reserva",
    "exige_reserva": "Exige Reserva",
    "aguardando": "Aguardando",
}
_OBRIGATORIAS = ("temperatura", "empresa", "local", "produto", "kg", "caixas",
                 "data_embarque", "situacao_entrega")

# A tela de mapeamento de colunas (20/10/2026) mostra estes campos; o nome
# da coluna no ERP é o rótulo.
mapeamento_colunas.registrar_tipo("follow_up", {
    "rotulo": "Follow up de pedidos (Expedito)",
    "campos": {chave: (rot, [rot], chave in _OBRIGATORIAS, []) for chave, rot in _COLS.items()},
})


def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return "".join(c for c in t.lower() if c.isalnum())


def _texto(v) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "nat") else t


def _num(v) -> float:
    """Número da célula. Vindo como TEXTO (exportação em outro formato
    regional), "1.234,5" é lido no padrão brasileiro — float() devolvia 0 e a
    linha saía como "sem saldo a embarcar"."""
    if isinstance(v, str):
        t = v.strip().replace(" ", "")
        if "," in t and "." in t:
            # o último dos dois é o decimal: "1.234,5" e "1,234.5"
            t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
        elif "," in t:
            t = t.replace(",", ".") if t.count(",") == 1 else t.replace(",", "")
        v = t
    try:
        f = float(v)
        return 0.0 if pd.isna(f) else f
    except (TypeError, ValueError):
        return 0.0


def _dia(v):
    """Data da célula. Excel com data de verdade vem pronto; vindo como texto,
    "05/10/2026" é 5 de outubro — pd.to_datetime lia mês primeiro (10 de
    maio) e o pedido sumia do dia do embarque (20/10/2026)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.date()
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, (int, float)):
        # número de série do Excel (dias desde 30/12/1899)
        return _dt.date(1899, 12, 30) + _dt.timedelta(days=int(v)) if 20000 < v < 80000 else None
    t = str(v).strip()
    try:
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
        if m:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t)
        if m:
            ano = int(m.group(3))
            return _dt.date(ano + 2000 if ano < 100 else ano, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def _dias(serie: pd.Series) -> pd.Series:
    """_dia em toda a coluna (rápido quando o Excel já traz data)."""
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie.dt.date
    return serie.map(_dia)


def _vazio(serie: pd.Series) -> pd.Series:
    """Célula vazia — inclusive o texto vazio que algumas exportações trazem
    no lugar do vazio de verdade (isna() sozinho dava todas como canceladas)."""
    return serie.isna() | serie.astype(str).str.strip().str.lower().isin(["", "nan", "nat", "none"])


def _codigo_reserva(v) -> str:
    """O código da reserva como texto de dígitos — o ERP exporta float."""
    t = str(v if v is not None else "").strip()
    if not t or t.lower() in ("nan", "none"):
        return ""
    try:
        return str(int(float(t)))
    except (TypeError, ValueError):
        return t


def _linha_do_cabecalho(caminho: str) -> int:
    """Linha do cabeçalho: a que tem mais colunas obrigatórias pelo nome.

    No arquivo real é a SEGUNDA (a primeira nomeia grupos de coluna), mas
    uma exportação sem a linha de grupos, ou com título acima, deslocava
    tudo e nenhuma coluna era achada (20/10/2026). No empate, fica a segunda.
    """
    previa = pd.read_excel(caminho, header=None, nrows=12)
    salvos = {_norm(c) for c in mapeamento_colunas.mapeamentos_salvos("follow_up").values()}
    alvo = {_norm(_COLS[k]) for k in _OBRIGATORIAS} | salvos
    melhor, pontos = 1, -1
    for i in range(len(previa)):
        nomes = {_norm(x) for x in previa.iloc[i].tolist() if not pd.isna(x)}
        n = len(alvo & nomes)
        if n > pontos or (n == pontos and i == 1):
            melhor, pontos = i, n
    return melhor if pontos > 0 else 1


def ler(caminho: str) -> pd.DataFrame:
    """Lê o Excel do follow up. O cabeçalho é achado pelos nomes (no
    arquivo real, a SEGUNDA linha — a primeira nomeia grupos de coluna e é o
    que desambigua os nomes repetidos do fim do arquivo).

    As colunas são resolvidas pelo mapeamento de colunas: o que o usuário
    salvou na tela vale primeiro, depois o nome exato. Obrigatória que faltar
    levanta ColunasFaltando, que é um ValueError com a lista das colunas do
    arquivo — a tela do Expedito abre o mapeamento com ela.
    """
    try:
        df = pd.read_excel(caminho, header=_linha_do_cabecalho(caminho))
    except Exception as exc:
        raise ValueError(f"Não foi possível ler o follow up: {exc}") from exc
    df.columns = mapeamento_colunas.nomes_unicos(df.columns)
    df.attrs["mapa"] = mapeamento_colunas.resolver_arquivo(
        "follow_up", df.columns, os.path.basename(str(caminho)))
    return df


def tipos_de_venda(df: pd.DataFrame) -> list[dict]:
    """Os valores de "Operação Comercial" com a contagem de linhas.

    Alimenta as caixas de seleção da tela de parâmetros: o usuário decide com
    o número na frente, mesma ideia do catálogo do Giro.
    """
    m = df.attrs["mapa"]
    if not m.get("operacao"):
        return []
    vc = df[m["operacao"]].fillna("").astype(str).value_counts()
    return [{"valor": v, "linhas": int(n)} for v, n in vc.items() if str(v).strip()]


def catalogo(df: pd.DataFrame) -> dict:
    """O que existe no arquivo, para a tela de parâmetros decidir com o número
    na frente — mesma ideia do catálogo do Giro."""
    m = df.attrs["mapa"]

    def conta(chave):
        col = m.get(chave)
        if not col:
            return []
        s = df[col].dropna().astype(str).str.strip()
        return [{"valor": k, "linhas": int(v)} for k, v in s.value_counts().items()]

    return {
        "situacao_pedido": conta("situacao_pedido"),
        "aguardando": conta("aguardando"),
        "empresas": conta("empresa"),
        "locais": conta("local"),
        "classificacoes": conta("classificacao"),
        "datas_embarque": [{"valor": str(k)[:10], "linhas": int(v)}
                           for k, v in df[m["data_embarque"]].value_counts().items()],
    }


def _bloqueio_aceito(situacao: str, aceitos: list[str]) -> bool:
    """"Bloqueado (PRC, PRL)" é aceito quando TODOS os códigos dele estão
    marcados — um pedido travado também por Limite não cai às 18:30."""
    s = situacao.upper()
    if not s.startswith("BLOQUEADO"):
        return False
    dentro = s[s.find("(") + 1:s.rfind(")")] if "(" in s else ""
    codigos = [c.strip() for c in dentro.split(",") if c.strip()]
    if not codigos:
        return False
    marcados = {c.strip().upper() for c in aceitos}
    return all(c.upper() in marcados for c in codigos)


def demanda(caminho: str, data_alvo: _dt.date, empresas: list[str] | None = None,
            locais: list[str] | None = None, p: dict | None = None) -> dict:
    """Demanda do dia, pronta para agrupar_demanda, com o funil dos cortes.

    `empresas`/`locais`: o recorte do CD. O follow up vem com a companhia
    inteira, e quem define o escopo são os TXTs de estoque carregados — local
    que não tem TXT não entra, porque não há estoque para atender.
    """
    p = {**PARAMETROS_PADRAO, **(p or {})}
    df = ler(caminho)
    m = df.attrs["mapa"]
    funil = [{"etapa": "linhas do arquivo", "linhas": len(df)}]
    # catálogo do arquivo inteiro, antes dos cortes (a tela de parâmetros
    # usa) — evita ler o Excel de novo só para isso
    catalogo_tipos = tipos_de_venda(df)

    # Pedido mãe: quando o pedido é desdobrado, a mãe e as filhas convivem no
    # arquivo com peso nas duas, e a demanda do picking sairia em dobro. No
    # follow up de 07/10/2026 eram 18 mães com linha própria — 202,4 t
    # contadas duas vezes, 6 delas já em "02-Instrução", ou seja dentro do
    # filtro do abastecimento (apontado pelo usuário em 07/10/2026).
    #
    # O conjunto sai do arquivo INTEIRO, antes de qualquer corte: se a filha
    # cair num filtro (cancelada, outro dia, outra empresa), a mãe continua
    # sendo mãe e tem de sair do mesmo jeito.
    maes = set()
    if m.get("pedido_principal"):
        maes = set(pd.to_numeric(df[m["pedido_principal"]], errors="coerce")
                   .dropna().astype("int64").tolist())

    def corta(mascara, rotulo):
        nonlocal df
        antes = len(df)
        df = df[mascara]
        funil.append({"etapa": rotulo, "linhas": len(df), "saíram": antes - len(df)})

    # 1. coligada: somente as saídas (a entrada é o espelho)
    if m.get("operacao"):
        corta(~df[m["operacao"]].astype(str).str.upper().str.startswith("E-"),
              "fora as entradas de coligada (E-)")

    # 1a. tipo de venda recusado pelo usuário
    fora = [str(t).strip() for t in (p.get("tipos_venda_fora") or []) if str(t).strip()]
    if fora and m.get("operacao"):
        op = df[m["operacao"]].astype(str).str.upper()
        recusado = pd.Series(False, index=df.index)
        for t in fora:
            recusado |= op.str.contains(t.upper(), na=False, regex=False)
        corta(~recusado, "fora os tipos de venda recusados (" + ", ".join(fora) + ")")

    # 1b. o pedido desdobrado: fica a filha, sai a mãe
    if maes and m.get("pedido") and not p.get("considerar_pedido_mae"):
        corta(~pd.to_numeric(df[m["pedido"]], errors="coerce").isin(maes),
              "fora os pedidos mãe (o desdobramento carrega a demanda)")

    # 2. entrega cancelada e pedido cancelado
    corta(df[m["situacao_entrega"]].astype(str).str.strip().str.lower() == "pendente",
          "somente entrega pendente")
    if m.get("cancelamento"):
        corta(_vazio(df[m["cancelamento"]]), "fora as canceladas")

    # 3. situação do pedido: Liberado + os bloqueios marcados
    if m.get("situacao_pedido"):
        aceitos = p["bloqueios_aceitos"]
        sit = df[m["situacao_pedido"]].astype(str).str.strip()
        corta(sit.str.upper().eq("LIBERADO") | sit.map(lambda s: _bloqueio_aceito(s, aceitos)),
              f"liberado ou bloqueio aceito ({', '.join(aceitos) or 'nenhum'})")

    # 3b. estágio do pedido: o que já está em transporte ou embarcando não
    #     precisa de picking
    if m.get("aguardando") and p.get("aguardando_aceitos"):
        aceitos = {str(x).strip().upper() for x in p["aguardando_aceitos"]}
        corta(df[m["aguardando"]].astype(str).str.strip().str.upper().isin(aceitos),
              "estágio que ainda pede abastecimento")

    # 4. o dia: embarque e/ou entrega, conforme o parâmetro
    alvo = pd.Timestamp(data_alvo)
    mascara = pd.Series(False, index=df.index)
    if p["usar_data_embarque"]:
        mascara |= _dias(df[m["data_embarque"]]) == data_alvo
    if p["usar_data_entrega"] and m.get("data_entrega"):
        mascara |= _dias(df[m["data_entrega"]]) == data_alvo
    corta(mascara, f"embarque ou entrega em {data_alvo.strftime('%d/%m/%Y')}")

    # 5. o recorte do CD: empresas e locais com TXT carregado
    if empresas:
        alvo_emp = {str(e).strip().upper() for e in empresas}
        corta(df[m["empresa"]].astype(str).str.strip().str.upper().isin(alvo_emp),
              f"empresas do CD ({', '.join(sorted(alvo_emp))})")
    if locais:
        alvo_loc = {str(x).strip().upper() for x in locais}
        corta(df[m["local"]].astype(str).str.strip().str.upper().isin(alvo_loc),
              "locais com estoque carregado")

    linhas, sem_peso, parciais = [], 0, 0
    for _, r in df.iterrows():
        kg = _num(r[m["kg"]])
        caixas = _num(r[m["caixas"]])
        # Embarque PARCIAL: o que falta é "Qtde Pendente", não o pedido
        # inteiro. No arquivo de 07/10 são 8 linhas, duas delas com pendente
        # NEGATIVO (embarcou mais do que o pedido) — essas saem da conta.
        qtde, pendente = _num(r[m["qtde"]]) if m.get("qtde") else 0, None
        if m.get("pendente"):
            pendente = _num(r[m["pendente"]])
        if pendente is not None and qtde > 0 and abs(pendente - qtde) > 0.001:
            if pendente <= 0:
                sem_peso += 1
                continue
            fator = pendente / qtde
            kg, caixas, parciais = kg * fator, caixas * fator, parciais + 1
        if kg <= 0:
            sem_peso += 1
            continue
        lmax = r[m["limite_max"]] if m.get("limite_max") else None
        linhas.append({
            "empresa": _texto(r[m["empresa"]]).upper(),
            "local": _texto(r[m["local"]]).upper(),
            "local_codigo": _texto(r[m["local_codigo"]]) if m.get("local_codigo") else "",
            "temperatura": _texto(r[m["temperatura"]]).upper(),
            "produto": _texto(r[m["produto"]]).split(".")[0],
            "nome": _texto(r[m["nome"]]),
            "kg": round(kg, 3),
            "caixas": caixas,
            "unidade_pedido": _texto(r[m["unidade"]]) if m.get("unidade") else "",
            "limite_min": _num(r[m["limite_min"]]) if m.get("limite_min") else 0.0,
            "limite_max": None if (lmax is None or pd.isna(lmax)) else _num(lmax),
            "pedido": _texto(r[m["pedido"]]) if m.get("pedido") else "",
            "entrega": _texto(r[m["entrega"]]) if m.get("entrega") else "",
            "item": _texto(r[m["item"]]) if m.get("item") else "",
            "cliente": _texto(r[m["cliente"]]) if m.get("cliente") else "",
            "classificacao": _texto(r[m["classificacao"]]) if m.get("classificacao") else "",
            # A reserva casa pelo CÓDIGO, não pelo nome nem pela sigla: no
            # TXT duas reservas diferentes dividem a sigla "Dísponivel"
            # (16465-Varejo e 16463-VENDA DIRECIONADA), então sigla não é
            # chave. O ERP manda o código como float ("16879.0").
            "reserva": _texto(r[m["reserva"]]) if m.get("reserva") else "",
            "reserva_codigo": _codigo_reserva(r[m["reserva_codigo"]])
                              if m.get("reserva_codigo") else "",
            # "Exige Reserva" é a resposta do próprio ERP para "a reserva
            # obriga?". Em 08/10/2026, e-Commerce B2B vinha com 0-Não em 374
            # linhas: o pedido tem reserva mas aceita estoque livre. Tratar
            # toda reserva como obrigatória dava falta de estoque onde havia
            # estoque (57 grupos do CDB no confronto com o painel real).
            # Sem a coluna no arquivo, a reserva OBRIGA — é a regra que o
            # usuário deu em 06/10/2026 ("se o pedido tiver reserva, o
            # produto tem que ter reserva"). Com a coluna, vale o que o ERP
            # diz. Assim um follow up exportado sem esse campo continua no
            # comportamento restritivo, em vez de afrouxar em silêncio.
            "exige_reserva": (str(r[m["exige_reserva"]]).strip().startswith("1")
                              if m.get("exige_reserva") else True),
            "data_embarque": (_dia(r[m["data_embarque"]]) or data_alvo).isoformat(),
            "data_entrega": (_dia(r[m["data_entrega"]]).isoformat()
                             if m.get("data_entrega") and _dia(r[m["data_entrega"]]) else ""),
        })
    if sem_peso:
        funil.append({"etapa": "fora as linhas sem saldo a embarcar", "linhas": len(linhas),
                      "saíram": sem_peso})
    if parciais:
        funil.append({"etapa": f"{parciais} linha(s) com embarque parcial: entra o saldo pendente",
                      "linhas": len(linhas)})

    return {
        "linhas": linhas,
        "funil": funil,
        "tipos_venda": catalogo_tipos,
        "data_alvo": data_alvo.isoformat(),
        "resumo": {
            "linhas": len(linhas),
            "kg": round(sum(l["kg"] for l in linhas), 3),
            "caixas": int(round(sum(l["caixas"] for l in linhas))),
            "produtos": len({(l["local"], l["produto"]) for l in linhas}),
            "pedidos": len({(l["pedido"], l["entrega"]) for l in linhas}),
            "empresas": sorted({l["empresa"] for l in linhas}),
            "locais": sorted({l["local"] for l in linhas}),
        },
    }
