"""
Expedito — camada de IA (07/10/2026).

Fala com a API do Gemini e é o ÚNICO ponto do sistema que manda dado da
operação para fora da VM. Duas responsabilidades, nesta ordem de
importância:

1. MASCARAR. O plano gratuito do Gemini usa o conteúdo enviado para treinar
   os modelos e permite revisão humana das entradas e saídas — está escrito
   nos termos da própria API, e os termos recomendam não enviar informação
   pessoal ou sensível. Então nada que identifique pessoa, cliente ou
   veículo sai daqui: a tela vê nome, a IA vê codinome.
2. CONVERSAR. Monta a chamada, trata cota e queda de rede, e devolve o
   texto já traduzido de volta para os nomes reais.

Por que a máscara é uma LISTA DE PERMITIDOS, e não de proibidos
(decidido ao construir, 07/10/2026): `anonimizar()` do serviço do Expedito
troca separador e conferente e deixa o resto do retrato passar inteiro —
e o retrato carrega `cliente`, `cidade`, `placa` e o número da instrução.
Trocar dois campos e repassar o resto significa que todo campo novo que o
retrato ganhar amanhã vaza por omissão. Aqui a conta é invertida: o
contexto é montado campo por campo a partir do que é permitido, e
`conferir_vazamento()` ainda varre o JSON pronto procurando nome real
antes de deixar a requisição sair. Se achar, a chamada não acontece.

Nada nesta camada decide operação: o cálculo medido mora em
app/services/expedito.py e continua funcionando com a API fora do ar ou com
a cota estourada.
"""
from __future__ import annotations

import json as _json
import time as _time
import unicodedata as _ud

import requests

# O endpoint é por modelo. A chave vai no cabeçalho x-goog-api-key, não na
# query string — chave em URL acaba em log de proxy e de servidor.
_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
_TIMEOUT = 45
# A conversa roda em thread (não segura o worker do gunicorn), então pode
# esperar mais: 45 s cortava a análise de uma noite inteira, e a tela dizia
# "o Gemini não respondeu" (usuário, 14/10/2026). Uma segunda tentativa vai
# com o contexto enxuto; se ainda assim não vier, a web responde com a
# análise calculada.
_TIMEOUT_CONVERSA = 120
_TIMEOUT_SEGUNDA = 75

# Sobrecarga do lado do Google (503 "high demand", e 500/502/504) costuma
# passar em segundos: tenta de novo com espera curta. Persistindo, ou com a
# cota do modelo esgotada (429), passa para os outros modelos da lista — cada
# um tem fila e cota próprias. Toda pergunta voltava "sobrecarregado" com o
# 3.5 Flash (usuário, 15/10/2026).
_SOBRECARGA = (500, 502, 503, 504)
_ESPERAS_SOBRECARGA = (0, 3, 8)

# Classe Flash: é o que o plano gratuito atende. A documentação do Gemini
# recomenda 3.8 Flash ou 3.5 Flash-Lite para projeto novo (consultado em
# 07/10/2026); os modelos Pro saíram do plano gratuito em abril de 2026.
# Fica em parâmetro porque o nome do modelo muda mais rápido que o código.
MODELO_PADRAO = "gemini-3.8-flash"
MODELOS_SUGERIDOS = [
    ("gemini-3.8-flash", "3.8 Flash — melhor raciocínio do plano gratuito"),
    ("gemini-3.5-flash", "3.5 Flash — equilíbrio entre resposta e cota"),
    ("gemini-3.5-flash-lite", "3.5 Flash-Lite — cota diária maior, resposta mais simples"),
]

# Teto de saída. Os modelos 3.x raciocinam antes de responder e o raciocínio
# consome este mesmo teto: apertado demais, a resposta volta vazia com
# finishReason MAX_TOKENS. `conversar()` percebe esse caso e repete uma vez
# com o dobro, em vez de devolver silêncio para o analista.
_SAIDA_MAX = 2048


# Variantes acentuadas de cada letra, para casar "JOÃO" com "joao" nos dois
# sentidos. O analista digita do jeito que quer; a máscara tem de pegar.
_VARIANTES = {"a": "aáàâãä", "e": "eéèêë", "i": "iíìîï", "o": "oóòôõö",
              "u": "uúùûü", "c": "cç", "n": "nñ"}


def _sem_acento(s: str) -> str:
    return "".join(c for c in _ud.normalize("NFKD", s) if not _ud.combining(c))


def _chave_de_busca(s) -> str:
    """Forma comparável de um texto: sem acento, sem caixa, sem pontuação.

    Usada pela varredura de vazamento. Sem isso, "JOSÉ DA SILVA" no retrato
    e "Jose da Silva" no JSON passariam por textos diferentes.
    """
    t = _sem_acento(str(s or "")).casefold()
    return "".join(c if c.isalnum() else " " for c in t)


def letra_do_indice(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA. Mesma régua de expedito.codinomes(), aqui
    para a ampliação do mapa poder continuar de onde ele parou."""
    nome, n = "", i
    while True:
        nome = chr(ord("A") + n % 26) + nome
        n = n // 26 - 1
        if n < 0:
            break
    return nome


def ampliar_mapa(mapa: dict, nomes) -> tuple[dict, bool]:
    """Acrescenta ao mapa quem ainda não tem codinome, sem reletrar ninguém.

    O codinome precisa durar a noite inteira. A lista de presentes muda
    durante o turno (alguém sai às 23h, alguém entra cobrindo) e, se o mapa
    fosse recalculado a cada pergunta, o "Separador B" das 20h seria outra
    pessoa às 23h: a conversa perderia o fio e a tradução de volta passaria
    a apontar o nome errado. Por isso o mapa é gravado por (conta, dia de
    operação) e daqui em diante só cresce.

    Devolve (mapa, cresceu) — `cresceu` diz se vale regravar.
    """
    novo = dict(mapa or {})
    usados = set(novo.values())
    cresceu = False
    for nome in nomes or []:
        n = str(nome or "").strip().upper()
        if not n or n in novo:
            continue
        i = len(novo)
        while ("Separador " + letra_do_indice(i)) in usados:
            i += 1
        novo[n] = "Separador " + letra_do_indice(i)
        usados.add(novo[n])
        cresceu = True
    return novo, cresceu


def _padrao_tolerante(termo: str) -> str:
    """Regex que casa o termo sem ligar para acento.

    A caixa fica por conta do re.IGNORECASE de quem chama: aqui a classe de
    caracteres resolve o acento, e sem o flag o "J" de JOÃO não casaria com
    o "j" do padrão rebaixado.
    """
    import re
    partes = []
    for c in _sem_acento(str(termo)).casefold():
        if c in _VARIANTES:
            partes.append("[" + _VARIANTES[c] + _VARIANTES[c].upper() + "]")
        elif c.isalnum():
            partes.append(re.escape(c))
        elif c.isspace():
            partes.append(r"\s+")
        else:
            partes.append(re.escape(c))
    return "".join(partes)


def mascarar_texto(texto: str, mapa: dict) -> str:
    """Troca nome real por codinome no texto DIGITADO pelo analista.

    Sem isso existe o furo mais provável de todos: a pergunta é campo livre,
    e "o João pega a I3?" mandaria o nome do empregado para fora mesmo com o
    retrato todo mascarado. Troca o nome completo, e também cada pedaço do
    nome com mais de 3 letras — quem pergunta escreve o primeiro nome, não o
    nome de cadastro. Trocar demais é inofensivo; trocar de menos, não.
    """
    import re
    saida = str(texto or "")
    pessoas = {nome: cod for cod, nome in (mapa.get("pessoas") or {}).items()}
    instrucoes = {num: ref for ref, num in (mapa.get("instrucoes") or {}).items()}

    trocas = sorted(pessoas.items(), key=lambda kv: -len(kv[0]))
    for nome, cod in trocas:
        saida = re.sub(_padrao_tolerante(nome), cod, saida, flags=re.IGNORECASE)
    # pedaços do nome, do maior para o menor, com fronteira de palavra
    pedacos = []
    for nome, cod in pessoas.items():
        for t in str(nome).split():
            if len(t) > 3:
                pedacos.append((t, cod))
    for t, cod in sorted(pedacos, key=lambda kv: -len(kv[0])):
        saida = re.sub(r"\b" + _padrao_tolerante(t) + r"\b", cod, saida,
                       flags=re.IGNORECASE)
    for num, ref in sorted(instrucoes.items(), key=lambda kv: -len(kv[0])):
        saida = re.sub(r"\b" + re.escape(str(num)) + r"\b", ref, saida)
    return saida


# ── máscara ──────────────────────────────────────────────────────────
# Campos do `detalhe` do retrato que podem sair. O que NÃO está aqui não
# sai: cliente, cidade, placa, doca, conferente, o número real da instrução,
# previsão de viagem e liberação ficam todos na VM.
_DETALHE_PERMITIDO = ("estado", "sku", "escalas", "prioridade",
                      "prev_t", "sep_t", "falta_t",
                      # embarcado e carregado (09/10/2026): sem eles a IA via
                      # só o picking e respondia "80%" numa noite concluída
                      "emb_t", "feito_t", "carregado")


def _hora(valor) -> str | None:
    """Só a hora do carimbo. A data completa não ajuda a IA a raciocinar
    sobre o ritmo da noite e é mais um pedaço de contexto saindo."""
    t = str(valor or "")
    return t[11:16] if len(t) >= 16 else (t[:5] if ":" in t[:5] else None)


def mascarar(foto: dict, sugestoes: list, desempenho: dict,
             mapa_pessoas: dict) -> tuple[dict, dict]:
    """Devolve (contexto_para_a_ia, mapa_de_volta).

    `mapa_pessoas` é o {NOME: "Separador A"} de expedito.codinomes(). As
    instruções recebem o mesmo tratamento: viram "I1", "I2"... porque o
    número real da instrução é rastreável até o cliente e o pedido.
    """
    pessoas = dict(mapa_pessoas or {})

    def pess(nome):
        if not nome:
            return None
        # Quem aparece no painel e não está na lista de presentes também
        # ganha codinome: a IA precisa saber que existe mais alguém
        # operando, sem receber de quem se trata.
        if nome not in pessoas:
            pessoas[nome] = "Separador %d" % (len(pessoas) + 1)
        return pessoas[nome]

    instrucoes, ref_de = [], {}
    for i, x in enumerate(foto.get("detalhe") or [], start=1):
        ref = "I%d" % i
        if x.get("instrucao") is not None:
            ref_de[ref] = str(x["instrucao"])
        linha = {k: x.get(k) for k in _DETALHE_PERMITIDO if x.get(k) is not None}
        linha["ref"] = ref
        linha["separador"] = pess((x.get("separador") or "").strip().upper())
        linha["inicio"] = _hora(x.get("sep_ini"))
        linha["fim"] = _hora(x.get("sep_fim"))
        instrucoes.append(linha)

    eq = foto.get("equipe") or {}
    equipe = {k: [pess(n) for n in (v or [])] for k, v in eq.items()
              if isinstance(v, list)}

    docas = foto.get("docas") or {}
    contexto = {
        "momento": _hora(foto.get("momento")),
        "turno": foto.get("turno"),
        "turno_comeca": _hora(foto.get("inicio_turno")),
        "turno_fecha": _hora(foto.get("fim_previsto_turno")),
        "decorrido_min": foto.get("decorrido_min"),
        "instrucoes": foto.get("instrucoes"),
        "toneladas": foto.get("toneladas"),
        "ritmo": foto.get("ritmo"),
        "projecao": {**(foto.get("projecao") or {}),
                     "termino_estimado": _hora((foto.get("projecao") or {}).get("termino_estimado"))},
        "equipe": equipe,
        "docas": {"em_uso": len(docas.get("em_uso") or []),
                  "disponiveis": docas.get("disponiveis"),
                  "livres": docas.get("livres")},
        "detalhe": instrucoes,
        # ritmo histórico de cada um, em codinome. Nome de quem não está na
        # noite não entra: a IA não tem o que fazer com ele.
        "ritmo_historico": {pessoas[n]: v for n, v in (desempenho or {}).items()
                            if n in pessoas},
        "sugestao_calculada": [
            {"separador": pess((s.get("separador") or "").strip().upper()),
             "ref": next((l["ref"] for l in instrucoes
                          if ref_de.get(l["ref"]) == str(s.get("instrucao"))), None),
             "motivo": s.get("motivo")}
            for s in (sugestoes or [])
        ],
    }
    if foto.get("analise"):
        contexto["analise"] = _mascarar_analise(foto["analise"], pess,
                                                {v: k for k, v in ref_de.items()})
    return contexto, {"pessoas": {v: k for k, v in pessoas.items()},
                      "instrucoes": ref_de}


def _mascarar_analise(analise: dict, pess, ref_da_instrucao: dict) -> dict:
    """A análise da noite (expedito_analise) com codinome e referência. Os
    textos dela não carregam nome nem número de instrução: quem é quem está
    nos campos `pessoa`/`pessoas` e `instrucao`/`instrucoes`, trocados aqui
    em qualquer nível. Instrução que não está no retrato (tirada da operação)
    sai."""
    def ref(i):
        return ref_da_instrucao.get(str(i))

    def andar(v):
        if isinstance(v, list):
            return [andar(x) for x in v]
        if not isinstance(v, dict):
            return v
        d = {}
        for k, x in v.items():
            if k == "pessoa":
                d[k] = pess((x or "").strip().upper())
            elif k == "pessoas" and isinstance(x, list):
                d[k] = [pess((n or "").strip().upper()) for n in x]
            elif k == "instrucao":
                d[k] = ref(x)
            elif k == "instrucoes" and isinstance(x, list):
                if x and isinstance(x[0], dict):
                    d[k] = [y for y in (andar(i) for i in x) if y.get("instrucao")]
                else:
                    d[k] = [r for r in (ref(i) for i in x) if r]
            else:
                d[k] = andar(x)
        return d

    return andar(analise)


def revelar(texto: str, mapa: dict) -> str:
    """Traduz a resposta de volta: codinome vira nome, ref vira instrução.

    Troca do mais longo para o mais curto, senão "Separador A" quebraria
    dentro de "Separador AB" num CD com mais de 26 separadores.
    """
    saida = str(texto or "")
    for de, para in sorted((mapa.get("pessoas") or {}).items(),
                           key=lambda kv: -len(kv[0])):
        saida = saida.replace(de, para)
    for de, para in sorted((mapa.get("instrucoes") or {}).items(),
                           key=lambda kv: -len(kv[0])):
        saida = saida.replace(de, para)
    return saida


def termos_proibidos(foto: dict, desempenho: dict | None = None) -> list[str]:
    """Tudo que o retrato carrega e NÃO pode sair: nome de gente, nome de
    cliente, cidade e placa. É o gabarito da varredura."""
    fora = set()
    for x in (foto.get("detalhe") or []):
        for campo in ("separador", "conferente", "cliente", "cidade", "placa"):
            fora.add(str(x.get(campo) or ""))
    for lista in (foto.get("equipe") or {}).values():
        if isinstance(lista, list):
            fora.update(str(n or "") for n in lista)
    fora.update(str(n or "") for n in (desempenho or {}))
    # Palavra de 4 letras ou menos é ruído: sigla de empresa, "SP", número
    # de doca. Varrer isso daria alarme falso em texto legítimo.
    return sorted({t.strip() for t in fora if len(t.strip()) > 4})


def conferir_vazamento(contexto: dict, proibidos: list) -> list:
    """Última linha antes da rede: devolve os termos proibidos que aparecem
    no JSON pronto. Lista vazia é o esperado — qualquer retorno aqui é
    defeito de código, e a chamada deve ser abortada.

    Existe porque a máscara é feita por campo: se alguém acrescentar um
    campo ao retrato e esquecer desta camada, é aqui que o erro aparece,
    antes de o dado sair da VM e não depois.
    """
    alvo = _chave_de_busca(_json.dumps(contexto, ensure_ascii=False, default=str))
    achados = []
    for termo in proibidos:
        t = _chave_de_busca(termo).strip()
        if t and t in alvo:
            achados.append(termo)
    return achados


# ── instrução do sistema ─────────────────────────────────────────────
_COMUM = """Você é o Expedito, copiloto da expedição de um centro de distribuição
de alimentos congelados e refrigerados. Responde em português do Brasil, em tom
corporativo, direto, sem saudação e sem repetir a pergunta.

Regras que não se negociam:
- Os números do CONTEXTO são MEDIDOS no painel do WMS. Use esses. Nunca invente
  tonelada, horário, nome ou quantidade que não esteja ali. Quando falta dado
  para responder, diga o que falta.
- As pessoas aparecem como codinome ("Separador C") e as instruções como
  referência ("I7"). Escreva assim: a tela traduz para os nomes reais antes de
  mostrar ao usuário. Não tente adivinhar quem é quem.
- `ritmo.t_h_por_pessoa` é tonelada por hora-pessoa, e `projecao.capacidade_t_h`
  é esse ritmo multiplicado por quem está presente. Não misture as duas.
- Fale de risco com o número do lado: "fecha 40 min depois do turno" vale mais
  que "pode atrasar".
- `analise` traz MEDIÇÕES da noite, não julgamentos: término da operação (o
  fechamento de status da última instrução), separação e embarque contra a
  janela prevista de cada um e contra o corte final cadastrados (a separação
  pode passar do horário dela e a operação ainda fechar no corte, se
  separação + embarque terminarem até lá), ondas de liberação, intervalos de
  cada separador entre instruções (já sem refeição e pausas térmicas, e
  quanto foi esperando liberação), espera entre separação e embarque,
  tempo até o fechamento de status e produtividade por separador e por
  conferente.
- O que é GARGALO é você quem decide, comparando os números entre si e com o
  histórico: não existe limite fixo. Quando perguntarem o que houve ou como
  foi a noite, comece pelo término e pelo veredito da janela, depois os
  gargalos que você identificar, cada um com o número que o sustenta.
- Com `analise.ondas_disponiveis`, avalie se a liberação das ondas atrasou e
  deixou separador parado. Sem as ondas (o resumo do painel daquele dia não
  foi carregado na Produtividade), diga isso em uma linha e analise por
  outros caminhos: ritmo por pessoa e por função, espera entre separação e
  embarque, fila de doca, fechamento de status, distribuição da carga. Cruze
  os números e aponte o que explica o resultado.
- Seja breve e escaneável. A tela formata markdown, então use:
  * uma primeira linha em **negrito** com a decisão ou a resposta direta;
  * tópicos curtos (- item) para os motivos e os próximos passos;
  * tabela markdown quando comparar pessoas, instruções ou veículos;
  * uma linha começando com "> " para um alerta que exija ação.
  No máximo 8 linhas de texto; numa análise da noite ou com pedido de
  detalhe, até 15. Sem emoji, sem saudação, sem repetir a pergunta."""

_PAPEIS = {
    "analista": _COMUM + """

Você fala com o ANALISTA DE EXPEDIÇÃO, durante a operação. O que ele precisa é
decisão agora: quem pega a próxima instrução, o que destravar primeiro, se o
ritmo atual fecha o turno. `sugestao_calculada` é o que a conta determinística
já indicou — concorde, ajuste ou discorde dela, e diga por quê. Prefira sempre
apontar a próxima ação à descrição do cenário.""",
    "gerente": _COMUM + """

Você fala com a GERÊNCIA, sobre uma noite que já passou ou está terminando. O
que interessa é padrão de comportamento, não a próxima carga: onde a operação
perdeu tempo, se a distribuição de carga foi equilibrada, o que se repete em
relação ao ritmo histórico, e o que mudaria o resultado da próxima noite. Não
faça juízo sobre pessoa: compare desempenho pelo número e trate o codinome como
posição de trabalho, não como indivíduo.""",
}


def instrucao_do_sistema(papel: str) -> str:
    return _PAPEIS.get(papel, _PAPEIS["analista"])


# ── conversa ─────────────────────────────────────────────────────────
def _erro_amigavel(status: int, corpo: dict) -> str:
    msg = ((corpo.get("error") or {}).get("message") or "").strip()
    if status == 429:
        return ("A cota diária da IA do Expedito acabou. O painel calculado continua "
                "funcionando; a conversa volta quando a cota renovar.")
    if status in (401, 403):
        return "A IA do Expedito está sem acesso: a chave cadastrada em Configurações foi recusada."
    if status == 400 and "api key" in msg.lower():
        return "A IA do Expedito está sem acesso: a chave cadastrada em Configurações é inválida."
    if status == 404:
        return ("O modelo configurado não existe ou não está liberado para esta "
                "chave. Escolha outro em Configurações.")
    # 503 é sobrecarga do lado do Google ("This model is currently experiencing
    # high demand"): transitória, e chegava à tela em inglês, cru — o usuário
    # viu exatamente isso em 08/10/2026. 500 e 504 são falha do lado deles.
    if status == 503:
        return ("O serviço de IA do Expedito está sobrecarregado no momento; "
                "costuma passar em poucos minutos.")
    if status in (500, 502, 504):
        return ("O serviço de IA do Expedito não conseguiu responder agora (erro %d). "
                "Pergunte de novo em alguns minutos." % status)
    # qualquer outro: o motivo vai junto, mas com o código, para o suporte
    return ("O serviço de IA do Expedito recusou a pergunta (erro %d)%s" %
            (status, (": " + msg) if msg else "."))


def _texto_da_resposta(d: dict) -> tuple[str, str]:
    """Devolve (texto, finish_reason). Texto vazio é caso previsto."""
    cands = d.get("candidates") or []
    if not cands:
        bloqueio = (d.get("promptFeedback") or {}).get("blockReason")
        return "", ("BLOQUEADO:" + bloqueio if bloqueio else "SEM_CANDIDATO")
    c = cands[0]
    partes = ((c.get("content") or {}).get("parts") or [])
    texto = "".join(p.get("text", "") for p in partes).strip()
    return texto, c.get("finishReason") or ""


def conversar(chave: str, pergunta: str, contexto: dict, historico: list | None = None,
              papel: str = "analista", modelo: str = "",
              proibidos: list | None = None, saida_max: int = _SAIDA_MAX) -> tuple[bool, str, dict]:
    """(ok, texto, meta). O texto volta em CODINOME — quem chama traduz.

    `historico`: [{"papel": "usuario"|"expedito", "texto": "..."}] na ordem.
    `proibidos`: gabarito de termos.proibidos() para a varredura final.
    """
    if not chave:
        return False, ("A IA do Expedito não está configurada. Cadastre a chave "
                       "em Configurações para usar a conversa."), {}
    if not str(pergunta or "").strip():
        return False, "Escreva a pergunta.", {}

    def montar(com_historico: bool, ctx: dict):
        lista = []
        for m in (historico or []) if com_historico else []:
            texto = str(m.get("texto") or "").strip()
            if not texto:
                continue
            papel_api = "user" if m.get("papel") == "usuario" else "model"
            lista.append({"role": papel_api, "parts": [{"text": texto}]})
        lista.append({"role": "user", "parts": [{"text":
            "CONTEXTO (JSON, números medidos no painel):\n"
            + _json.dumps(ctx, ensure_ascii=False, default=str)
            + "\n\nPERGUNTA: " + str(pergunta).strip()}]})
        return lista

    conteudos = montar(True, contexto)
    # segunda tentativa: sem o histórico e sem a lista instrução a instrução
    # (a análise e os totais ficam) — é o que mais pesa no tempo de resposta
    enxuto = montar(False, {k: v for k, v in contexto.items() if k != "detalhe"})

    nome_modelo = (modelo or MODELO_PADRAO).strip() or MODELO_PADRAO
    modelos = [nome_modelo] + [m for m, _ in MODELOS_SUGERIDOS if m != nome_modelo]
    cab = {"x-goog-api-key": chave, "Content-Type": "application/json"}

    def corpo_da_chamada(teto: int, conteudo=None):
        return {
            "systemInstruction": {"parts": [{"text": instrucao_do_sistema(papel)}]},
            "contents": conteudo if conteudo is not None else conteudos,
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": teto},
        }

    # Varredura final sobre o PAYLOAD INTEIRO, não apenas sobre o contexto: a
    # pergunta digitada e o histórico da conversa saem na mesma requisição e
    # podem carregar nome real que a máscara não pegou. Falhar aqui é o
    # comportamento desejado — melhor a conversa não funcionar que o dado
    # identificado sair para um serviço que treina com o que recebe.
    vazou = conferir_vazamento(corpo_da_chamada(saida_max), proibidos or [])
    if vazou:
        return False, ("Envio bloqueado: a mensagem ainda carrega %d dado(s) "
                       "identificado(s). Nada foi enviado para fora."
                       % len(vazou)), {"vazamento": vazou}

    usado = nome_modelo

    def chamar(teto: int, conteudo=None, espera: int = _TIMEOUT_CONVERSA):
        corpo = corpo_da_chamada(teto, conteudo)
        r = requests.post(_API.format(modelo=usado), headers=cab, json=corpo, timeout=espera)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}

    def falha_conexao(e):
        return False, "falha de conexão com o serviço de IA (%s)" % type(e).__name__, \
            {"transitorio": True, "tentativas": tentativas}

    tentativas = []
    status, d = None, {}
    for usado in modelos:
        for pausa in _ESPERAS_SOBRECARGA:
            if pausa:
                _time.sleep(pausa)
            try:
                status, d = chamar(saida_max)
            except requests.Timeout:
                try:
                    status, d = chamar(saida_max, enxuto, _TIMEOUT_SEGUNDA)
                except requests.Timeout:
                    return False, ("a análise com IA passou de %d segundos"
                                   % (_TIMEOUT_CONVERSA + _TIMEOUT_SEGUNDA)), \
                        {"transitorio": True, "tentativas": tentativas}
                except Exception as e:
                    return falha_conexao(e)
            except Exception as e:
                return falha_conexao(e)
            if status not in _SOBRECARGA:
                break
        if status == 200:
            break
        tentativas.append({"modelo": usado, "status": status})
        # chave recusada ou pedido inválido não melhora em outro modelo
        if status not in _SOBRECARGA and status not in (429, 404):
            break

    if status != 200:
        msg = _erro_amigavel(status, d)
        if len(tentativas) > 1 and status in _SOBRECARGA + (429,):
            msg = ("Os %d modelos de IA disponíveis estão sobrecarregados ou sem cota no "
                   "momento; costuma passar em poucos minutos." % len(tentativas))
        # o modelo e o código vão junto: separam sobrecarga real de outro defeito
        msg += " (" + ", ".join("%s: erro %s" % (t["modelo"], t["status"]) for t in tentativas) + ")"
        return False, msg, {"status": status, "tentativas": tentativas,
                            "transitorio": status in (429,) + _SOBRECARGA}

    texto, fim = _texto_da_resposta(d)
    if not texto and fim == "MAX_TOKENS":
        # O raciocínio do modelo consumiu o teto antes de escrever a resposta.
        try:
            status, d = chamar(saida_max * 2)
            if status == 200:
                texto, fim = _texto_da_resposta(d)
        except Exception:
            pass
    if not texto:
        if fim.startswith("BLOQUEADO"):
            return False, ("O serviço de IA recusou responder a esta pergunta (%s)."
                           % fim.split(":", 1)[-1]), {"finish": fim, "transitorio": True}
        return False, ("o serviço de IA devolveu resposta vazia (%s)"
                       % (fim or "sem motivo")), {"finish": fim, "transitorio": True}

    uso = d.get("usageMetadata") or {}
    return True, texto, {
        "modelo": usado,
        # respondeu um modelo reserva porque o configurado estava ocupado
        "modelo_reserva": usado != nome_modelo,
        "tentativas": tentativas,
        "finish": fim,
        "tokens_entrada": uso.get("promptTokenCount"),
        "tokens_saida": uso.get("candidatesTokenCount"),
        "tokens_total": uso.get("totalTokenCount"),
    }


def testar_chave(chave: str, modelo: str = "") -> tuple[bool, str, dict]:
    """Valida a chave com uma pergunta mínima, sem dado de operação nenhum.
    Mesmo formato de retorno de telegram_alertas.testar_bot()."""
    if not chave:
        return False, "Chave não informada.", {}
    nome = (modelo or MODELO_PADRAO).strip() or MODELO_PADRAO
    try:
        r = requests.post(
            _API.format(modelo=nome),
            headers={"x-goog-api-key": chave, "Content-Type": "application/json"},
            json={"contents": [{"role": "user", "parts": [{"text": "Responda apenas: ok"}]}],
                  "generationConfig": {"maxOutputTokens": 512, "temperature": 0}},
            timeout=_TIMEOUT)
        try:
            d = r.json()
        except ValueError:
            d = {}
    except Exception as e:
        return False, "Erro de conexão com o Gemini: %s" % e, {}
    if r.status_code != 200:
        return False, _erro_amigavel(r.status_code, d), {"status": r.status_code}
    texto, fim = _texto_da_resposta(d)
    # Resposta vazia com teto estourado ainda prova que a chave vale: a
    # requisição foi aceita, autenticada e cobrada da cota.
    if not texto and fim != "MAX_TOKENS":
        return False, "O Gemini respondeu sem texto (%s)." % (fim or "sem motivo"), {}
    return True, "Chave válida no modelo %s." % nome, {"modelo": nome}


# ── limite diário da demo pública ─────────────────────────────────────
# Na demo (DEMO_MODE=1) a chave é do autor e o site é aberto: um teto de
# chamadas por dia protege a cota. Estourado, a conversa responde com a
# análise calculada da noite (o mesmo caminho de quando a API cai).
import os as _os

if _os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "sim", "yes"):
    _conversar_original = conversar

    def conversar(chave, pergunta, contexto, historico=None, papel="analista",
                  modelo="", proibidos=None, saida_max=_SAIDA_MAX):
        import datetime as _d
        from app.database import get_preferencia, set_preferencia
        limite = int(_os.environ.get("GEMINI_LIMITE_DIA", "40") or 40)
        hoje = _d.date.today().isoformat()
        uso = get_preferencia("demo_ia_uso", {}) or {}
        n = int(uso.get(hoje, 0))
        if n >= limite:
            return False, "limite diário de perguntas da demonstração atingido", {"transitorio": True}
        set_preferencia("demo_ia_uso", {hoje: n + 1})
        return _conversar_original(chave, pergunta, contexto, historico, papel,
                                   modelo, proibidos, saida_max)
