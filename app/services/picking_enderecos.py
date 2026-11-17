"""
Cadastro de posições de PICKING (nível 1) — Expedito, 06/10/2026.

O que é cadastrado aqui é ESPAÇO, não produto: "o endereço não será fixo,
senão vamos perder espaço de picking" (usuário, 06/10/2026). Cada noite o
Expedito aloca os códigos nas posições livres; o que fica guardado é quais
posições existem, onde ficam e quais estão ativas.

Endereço na língua do WMS, a mesma do TXT do Produto Paletizado e do mapa de
estoque: câmara, rua, box, altura e profundidade.

MEIA POSIÇÃO: dois meios paletes dividem a altura de um nível, então uma
posição marcada como meia vira DUAS posições de picking — metade A e metade
B. É a mesma regra que o mapa de estoque já aplica (ver _box_to_dict em
web/mapa_estoque.py, achado do usuário 05/09/2026), trazida para cá porque
no picking ela decide quantos códigos cabem na face.

Três caminhos de cadastro, todos pelo mesmo formato de planilha:
  - LOTE: sobe a planilha preenchida e cria o que não existe
  - ALTERAÇÃO EM LOTE: mesma planilha com a coluna Ação (incluir, alterar,
    remover), para mexer em muitas posições de uma vez
  - EDIÇÃO NA TELA: uma posição por vez, pela web

Este módulo não conhece banco nem Flask: lê, valida e devolve estrutura
pronta. Quem grava é a camada web.
"""
from __future__ import annotations

import io
import unicodedata

import pandas as pd

# A planilha tem UMA linha de cabeçalho (diferente do layout de boxes, que
# tem duas) — aqui cada linha é uma posição, sem agrupamento.
COLUNAS = ["Ação", "Local de Estoque", "Temperatura", "Câmara", "Rua", "Box",
           "Altura", "Profundidade", "Meia posição", "Ativa", "Observação"]
_OBRIGATORIAS = ("Local de Estoque", "Temperatura", "Câmara", "Rua", "Box", "Altura")

ACOES = ("incluir", "alterar", "remover")
TEMPERATURAS = ("RESFRIADO", "CONGELADO", "AMBIENTE")
METADES = ("A", "B")

# ── layout do WMS (planilha que o usuário exporta) ───────────────────
# Cabeçalho real: Codigo, Deposito, Rua, CB, Altura, Profundidade, TipoBox,
# PesoMaximo, Produto, Classificacao, LocalEstoque, Empresa.
COLUNAS_WMS = ["Codigo", "Deposito", "Rua", "CB", "Altura", "Profundidade",
               "TipoBox", "PesoMaximo", "Produto", "Classificacao",
               "LocalEstoque", "Empresa"]
_OBRIGATORIAS_WMS = ("CB", "Altura")

# TipoBox 2 = picking. Levantado na planilha real de 08/10/2026: 164 de 166
# linhas vêm com 2, e as 2 restantes com 7 (outro tipo de endereço, que não
# é face de picking e por isso entra inativo em vez de ser descartado —
# nenhuma linha sai em silêncio).
TIPOBOX_PICKING = "2"

# Câmaras em operação hoje (usuário, 08/10/2026: "hoje estamos so com a
# Camara 4, 5 e 8 ativas... demais podem ser desconsideradas"). A planilha
# traz o número da câmara dentro do CB ("C5-F28" é câmara 5), e não na
# coluna Deposito — nesse arquivo Deposito 5 era a câmara 5 e Deposito 6 era
# a câmara 8, então Deposito NÃO serve para isso.
CAMARAS_ATIVAS_PADRAO = ("4", "5", "8")

# Peso máximo que caracteriza MEIA posição (usuário, 08/10/2026: "o que
# tiver com peso maximo de 600 kg é meia posição"). Posição cheia vem com
# 2000 kg na planilha real.
PESO_MEIA_POSICAO = 600.0


def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return "".join(c for c in t.lower() if c.isalnum())


def _texto(v) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "nat") else t


def _inteiro(v, padrao: int = 1) -> int:
    try:
        return int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        return padrao


def _sim(v, padrao: bool = False) -> bool:
    t = _norm(v)
    if not t:
        return padrao
    return t in ("sim", "s", "1", "true", "x", "ativa", "ativo")


def chave(pos: dict) -> tuple:
    """Identidade de uma posição: BOX + nível + metade.

    Câmara e rua ficam FORA da chave de propósito. O TXT do Produto
    Paletizado traz `camara` como "RESF"/"CONG" e `rua` como letra ("E"),
    enquanto a planilha do WMS traz Deposito numérico e Rua numérica — nada
    disso casa entre as duas fontes (conferido em 08/10/2026). O que casa é
    o código do box ("C5-E08"), que já carrega câmara e rua dentro dele e é
    único no WMS. Chavear por câmara/rua fazia o cadastro não reconhecer
    NENHUMA posição do TXT.
    """
    return (str(pos.get("box", "")).upper(), int(pos.get("altura", 1) or 1),
            int(pos.get("profundidade", 1) or 1), pos.get("metade", ""))


def _camara_do_box(cb: str) -> str:
    """Número da câmara embutido no código do box: "C5-F28" -> "5".

    Exige o separador: "C5A01" não é endereço válido e devolver "5" daqui
    deixaria passar um box truncado como se fosse bom. E normaliza o zero à
    esquerda, porque "C04-A01" tem de casar com a câmara "4" da lista de
    ativas — comparar "04" com "4" como texto deixava a câmara inativa sem
    motivo (os dois achados na revisão pré-deploy, 08/10/2026).
    """
    t = str(cb or "").strip().upper()
    if not t.startswith("C") or "-" not in t:
        return ""
    num = ""
    for ch in t[1:]:
        if ch.isdigit():
            num += ch
        else:
            break
    if not num:
        return ""
    return str(int(num))


def _rua_do_box(cb: str) -> str:
    """Letra da rua no código do box: "C5-F28" -> "F" (é como o TXT escreve
    a rua, diferente da coluna Rua da planilha, que vem numérica)."""
    t = str(cb or "").strip().upper()
    if "-" not in t:
        return ""
    depois = t.split("-", 1)[1]
    return depois[0] if depois and depois[0].isalpha() else ""


def expandir_meia(pos: dict) -> list[dict]:
    """Posição marcada como meia vira duas: metade A e metade B.

    Mesma regra do mapa de estoque — dois meios paletes dividem a altura de
    um nível. Sem expandir, o Expedito enxergaria uma face onde cabem dois
    códigos como se coubesse um, e o cálculo de posições disponíveis sairia
    pela metade.
    """
    if not pos.get("meia"):
        return [{**pos, "metade": ""}]
    return [{**pos, "metade": m} for m in METADES]


def _parse_wms(df, por_nome, camaras_ativas) -> dict:
    """Layout exportado do WMS — é o formato que o usuário usa de verdade.

    O que a planilha diz e o que o cadastro conclui:
      CB            -> box (e, por dentro dele, câmara e rua)
      Altura        -> nível (o usuário já exporta filtrado no nível 1)
      Profundidade  -> profundidade
      TipoBox 2     -> face de picking; outro tipo entra INATIVO
      PesoMaximo    -> até 600 kg é meia posição, que vira metades A e B
      LocalEstoque  -> local, quando preenchido (na exportação real vem vazio)

    Câmara fora das ativas entra INATIVA em vez de ser descartada: o
    cadastro continua completo e ligar uma câmara nova depois é marcar
    "Ativa", sem subir planilha de novo.
    """
    ativas = {str(c).strip() for c in (camaras_ativas or ()) if str(c).strip()}

    def col(linha, nome, padrao=""):
        c = por_nome.get(_norm(nome))
        return _texto(linha[c]) if c else padrao

    posicoes, erros = [], []
    vistas: set[tuple] = set()
    for i, linha in df.iterrows():
        n_excel = i + 2
        cb = col(linha, "CB").upper()
        if not cb:
            continue                          # linha em branco no fim
        camara = _camara_do_box(cb)
        if not camara:
            erros.append({"linha": n_excel,
                          "motivo": f"Box “{cb}” fora do padrão Cn-XNN "
                                    f"(não deu para saber a câmara)"})
            continue
        altura = _inteiro(col(linha, "Altura"), 1)
        if altura < 1:
            erros.append({"linha": n_excel, "motivo": "Altura precisa ser 1 ou mais"})
            continue
        tipo = col(linha, "TipoBox")
        peso = col(linha, "PesoMaximo")
        try:
            peso_max = float(str(peso).replace(",", ".")) if peso else 0.0
        except ValueError:
            peso_max = 0.0
        # meia posição pelo peso; sem peso na planilha, assume posição cheia
        meia = bool(peso_max) and peso_max <= PESO_MEIA_POSICAO
        de_picking = (tipo == TIPOBOX_PICKING) if tipo else True
        camara_ativa = (camara in ativas) if ativas else True
        motivos = []
        if not de_picking:
            motivos.append(f"TipoBox {tipo}")
        if not camara_ativa:
            motivos.append(f"câmara {camara} fora de operação")
        base = {
            "acao": "incluir",
            "local": col(linha, "LocalEstoque").upper(),
            "temperatura": "",
            "camara": camara,
            "rua": _rua_do_box(cb),
            "box": cb,
            "altura": altura,
            "profundidade": _inteiro(col(linha, "Profundidade"), 1),
            "meia": meia,
            "ativa": de_picking and camara_ativa,
            "observacao": ("inativa: " + ", ".join(motivos))[:200] if motivos else "",
            "codigo_wms": col(linha, "Codigo"),
            "peso_maximo": peso_max or None,
        }
        for pos in expandir_meia(base):
            k = chave(pos)
            if k in vistas:
                erros.append({"linha": n_excel,
                              "motivo": f"Endereço repetido na planilha: {rotulo(pos)}"})
                continue
            vistas.add(k)
            posicoes.append(pos)

    if not posicoes and not erros:
        raise ValueError("Nenhuma posição encontrada na planilha.")
    contagem = {a: sum(1 for p in posicoes if p["acao"] == a) for a in ACOES}
    return {"posicoes": posicoes, "erros": erros, "acoes": contagem,
            "layout": "wms",
            "camaras_ativas": sorted(ativas),
            "inativas": sum(1 for p in posicoes if not p["ativa"])}


def parse_planilha(caminho: str, camaras_ativas=None) -> dict:
    """Lê a planilha de posições e devolve
    {"posicoes": [...], "erros": [{"linha", "motivo"}], "acoes": {...}}.

    Aceita DOIS layouts e decide pelo cabeçalho: o exportado do WMS (coluna
    CB) e o modelo próprio do Expedito (colunas Box/Câmara/Rua). Nunca
    descarta linha em silêncio: o que não entra sai na lista de erros com o
    número da linha do Excel, para o usuário corrigir sabendo onde.
    """
    try:
        abas = pd.read_excel(caminho, dtype=str, sheet_name=None)
    except Exception as exc:
        raise ValueError(f"Não foi possível ler a planilha: {exc}") from exc

    # Escolhe a aba pelo CABEÇALHO, não pela ordem. A planilha modelo tem a
    # aba de instruções na frente, e ler a primeira aba fazia o modelo
    # preenchido ser recusado como "fora do formato" (achado em 08/10/2026,
    # ao conferir a ida e volta do próprio modelo). A exportação do WMS tem
    # uma aba só e continua funcionando igual.
    # Entre as abas que têm cabeçalho de posição, vence a de MAIS linhas. A
    # primeira vencendo fazia a aba de exemplo da planilha modelo (3 linhas)
    # ganhar da exportação real colada ao lado, e os boxes de verdade eram
    # ignorados sem erro nenhum (achado na revisão pré-deploy, 08/10/2026).
    df, por_nome = None, {}
    candidatas = []
    for nome_aba, candidata in abas.items():
        mapa = {_norm(c): c for c in candidata.columns}
        if _norm("CB") in mapa or _norm("Box") in mapa:
            candidatas.append((len(candidata.index), nome_aba, candidata, mapa))
    if candidatas:
        candidatas.sort(key=lambda x: -x[0])
        df, por_nome = candidatas[0][2], candidatas[0][3]
    if df is None:
        primeira = next(iter(abas.values()), None)
        if primeira is None:
            raise ValueError("Planilha vazia.")
        df, por_nome = primeira, {_norm(c): c for c in primeira.columns}

    # o layout do WMS é reconhecido pela coluna CB, que o modelo próprio não tem
    if _norm("CB") in por_nome:
        faltando = [c for c in _OBRIGATORIAS_WMS if _norm(c) not in por_nome]
        if faltando:
            raise ValueError(
                "Planilha do WMS sem as colunas: "
                + ", ".join(f"“{c}”" for c in faltando) + ".")
        return _parse_wms(df, por_nome,
                          camaras_ativas if camaras_ativas is not None
                          else CAMARAS_ATIVAS_PADRAO)
    faltando = [c for c in _OBRIGATORIAS if _norm(c) not in por_nome]
    if faltando:
        raise ValueError(
            "Cabeçalho fora do formato esperado. Faltam as colunas: "
            + ", ".join(f"“{c}”" for c in faltando)
            + ". Baixe a planilha modelo para conferir."
        )

    def col(linha, nome, padrao=""):
        c = por_nome.get(_norm(nome))
        return _texto(linha[c]) if c else padrao

    posicoes, erros = [], []
    vistas: set[tuple] = set()
    for i, linha in df.iterrows():
        n_excel = i + 2                      # 1 do cabeçalho + base zero
        local = col(linha, "Local de Estoque").upper()
        if not local:
            continue                          # linha em branco no fim da planilha
        temp = col(linha, "Temperatura").upper()
        if temp not in TEMPERATURAS:
            erros.append({"linha": n_excel,
                          "motivo": f"Temperatura “{temp or '(vazia)'}” fora de "
                                    + ", ".join(TEMPERATURAS)})
            continue
        acao = _norm(col(linha, "Ação")) or "incluir"
        if acao not in ACOES:
            erros.append({"linha": n_excel,
                          "motivo": f"Ação “{acao}” desconhecida (use incluir, alterar ou remover)"})
            continue
        altura = _inteiro(col(linha, "Altura"), 1)
        if altura < 1:
            erros.append({"linha": n_excel, "motivo": "Altura precisa ser 1 ou mais"})
            continue
        base = {
            "acao": acao,
            "local": local,
            "temperatura": temp,
            "camara": col(linha, "Câmara").upper(),
            "rua": col(linha, "Rua").upper(),
            "box": col(linha, "Box").upper(),
            "altura": altura,
            "profundidade": _inteiro(col(linha, "Profundidade"), 1),
            "meia": _sim(col(linha, "Meia posição")),
            "ativa": _sim(col(linha, "Ativa"), padrao=True),
            "observacao": col(linha, "Observação")[:200],
        }
        if not base["box"] or not base["rua"]:
            erros.append({"linha": n_excel, "motivo": "Rua e Box são obrigatórios"})
            continue
        for pos in expandir_meia(base):
            k = chave(pos)
            if k in vistas:
                erros.append({"linha": n_excel,
                              "motivo": f"Endereço repetido na planilha: {rotulo(pos)}"})
                continue
            vistas.add(k)
            posicoes.append(pos)

    if not posicoes and not erros:
        raise ValueError("Nenhuma posição encontrada na planilha.")

    contagem = {a: sum(1 for p in posicoes if p["acao"] == a) for a in ACOES}
    return {"posicoes": posicoes, "erros": erros, "acoes": contagem}


def rotulo(pos: dict) -> str:
    """Endereço legível, como o operador lê na etiqueta."""
    partes = [pos.get("camara", ""), pos.get("rua", ""), pos.get("box", "")]
    base = "-".join(p for p in partes if p)
    nivel = f"N{pos.get('altura', 1)}"
    if pos.get("profundidade", 1) and int(pos.get("profundidade", 1)) > 1:
        nivel += f"P{pos['profundidade']}"
    if pos.get("metade"):
        nivel += f" ({pos['metade']})"
    return f"{base} {nivel}".strip()


def gerar_planilha_modelo(exemplos: list[dict] | None = None) -> bytes:
    """Planilha modelo, com instruções na primeira aba e exemplo preenchido.

    Mesmo espírito do modelo do mapa de estoque: o usuário baixa, preenche e
    sobe de volta. A MESMA planilha serve para cadastro em lote e para
    alteração em lote — o que muda é a coluna Ação.
    """
    exemplo = exemplos or [
        {"Codigo": 601, "Deposito": 5, "Rua": 6, "CB": "C5-F28", "Altura": 1,
         "Profundidade": 1, "TipoBox": 2, "PesoMaximo": 2000},
        {"Codigo": 602, "Deposito": 6, "Rua": 4, "CB": "C8-D28", "Altura": 1,
         "Profundidade": 1, "TipoBox": 2, "PesoMaximo": 2000},
        {"Codigo": 603, "Deposito": 5, "Rua": 5, "CB": "C5-E19", "Altura": 1,
         "Profundidade": 1, "TipoBox": 2, "PesoMaximo": 600},
    ]
    instrucoes = [
        ["Cadastro de posições de picking — Expedito"],
        [""],
        ["Este é o layout exportado do WMS. Exporte os boxes filtrando o NÍVEL 1,"],
        ["que é o picking, e suba o arquivo sem precisar remontar nada."],
        [""],
        ["Codigo", "Código do box no WMS. Vai guardado junto, para rastrear a origem"],
        ["Deposito", "Vem como está. A CÂMARA não sai daqui — ela é lida do CB"],
        ["Rua", "Vem como está (numérica). A rua usada no endereço é a letra do CB"],
        ["CB", "O código do box, ex.: C5-F28. É ele que identifica a posição:"],
        ["", "a câmara (5) e a rua (F) são lidas de dentro dele"],
        ["Altura", "Nível da posição. O picking é o nível 1"],
        ["Profundidade", "1 quando o box tem uma profundidade. Em branco vale 1"],
        ["TipoBox", "2 é face de picking. Outro tipo é cadastrado INATIVO, não descartado"],
        ["PesoMaximo", f"Até {PESO_MEIA_POSICAO:.0f} kg a posição é MEIA e vira duas (metades A e B)."],
        ["", "Posição cheia costuma vir com 2000 kg"],
        ["LocalEstoque", "Opcional. Quando vazio, a posição vale para o local do CD"],
        ["Empresa", "Opcional"],
        [""],
        [f"Câmaras em operação hoje: {', '.join(CAMARAS_ATIVAS_PADRAO)}. Box de outra câmara entra"],
        ["cadastrado e INATIVO — para ligar depois, basta marcar como ativa, sem subir planilha de novo."],
        [""],
        ["O endereço de cada código NÃO é fixo: o Expedito aloca os produtos nas posições livres a cada noite."],
        ["Posição meia conta como duas posições disponíveis."],
    ]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(instrucoes).to_excel(writer, sheet_name="Instruções",
                                          index=False, header=False)
        pd.DataFrame(exemplo, columns=COLUNAS_WMS).to_excel(
            writer, sheet_name="Boxes", index=False)
    return buffer.getvalue()


def aplicar(existentes: list[dict], planilha: dict) -> dict:
    """Confronta o que veio da planilha com o que já está cadastrado.

    Devolve {"incluir", "alterar", "remover", "sem_mudanca", "avisos"} — a
    camada web grava e mostra o resumo. Separar o cálculo da gravação deixa
    a tela avisar ANTES ("isto vai remover 14 posições"), em vez de o
    usuário descobrir depois.
    """
    atuais = {chave(p): p for p in existentes}
    incluir, alterar, remover, igual, avisos = [], [], [], [], []

    for pos in planilha["posicoes"]:
        k = chave(pos)
        atual = atuais.get(k)
        if pos["acao"] == "remover":
            if atual:
                remover.append(pos)
            else:
                avisos.append(f"{rotulo(pos)}: pedido de remoção de posição que não existe")
            continue
        if atual is None:
            if pos["acao"] == "alterar":
                avisos.append(f"{rotulo(pos)}: marcado como alterar, não existia — será incluído")
            incluir.append(pos)
            continue
        campos = ("temperatura", "ativa", "observacao", "meia")
        if any(atual.get(c) != pos.get(c) for c in campos):
            alterar.append(pos)
        else:
            igual.append(pos)

    return {"incluir": incluir, "alterar": alterar, "remover": remover,
            "sem_mudanca": igual, "avisos": avisos,
            "erros": planilha.get("erros", [])}


def resumo_capacidade(posicoes: list[dict]) -> dict:
    """Quantas faces de picking existem, por local e temperatura.

    É o teto que o Expedito usa para decidir quando montar pallet misto: sem
    saber quantas posições existem, não há como dizer que faltou espaço.
    """
    # Agrupa por CÂMARA junto com local e temperatura: no layout exportado do
    # WMS local e temperatura vêm vazios, e agrupar só por eles jogava as 164
    # faces num grupo sem rótulo. A câmara sempre existe (sai do CB) e é como
    # a operação fala do espaço — "câmara 4, 5 e 8".
    por_local: dict[tuple, dict] = {}
    inativas = 0
    for p in posicoes:
        if not p.get("ativa", True):
            inativas += 1
            continue
        k = (p.get("local", ""), p.get("camara", ""), p.get("temperatura", ""))
        d = por_local.setdefault(k, {"local": p.get("local", ""),
                                     "camara": p.get("camara", ""),
                                     "temperatura": p.get("temperatura", ""),
                                     "posicoes": 0, "meias": 0})
        d["posicoes"] += 1
        if p.get("metade"):
            d["meias"] += 1
    ativas = sum(d["posicoes"] for d in por_local.values())
    return {"por_local": sorted(por_local.values(),
                                key=lambda x: (x["local"], x["camara"], x["temperatura"])),
            "total": ativas, "ativas": ativas, "inativas": inativas,
            "cadastradas": ativas + inativas}
