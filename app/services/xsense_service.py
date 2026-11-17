"""
Motor de detecção do módulo Sensores (monitoramento de câmaras frias).

Implementa a spec `xsense-monitoramento-spec.md` (§5 e §6): 3 detectores
(Excursão sustentada, Oscilação, Pico transitório) + evento de Recuperação,
rodando sobre uma máquina de estados com histerese e cooldown, pra só
alertar em TRANSIÇÃO de estado — nunca por leitura isolada.

Este módulo é puro Python (sem Flask/SQLite) de propósito: o motor de
detecção é a parte mais sensível a acertar (é o que evita spam de alerta
sem deixar passar problema real), então fica isolado e testável sem
precisar de banco nem do scraper do portal. web/xsense.py chama essas
funções e persiste o resultado.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_BRT = ZoneInfo("America/Sao_Paulo")
_FMT = "%Y-%m-%d %H:%M:%S"


def agora_brt_str() -> str:
    return datetime.now(_BRT).strftime(_FMT)


def fmt_duracao_min(minutos: int) -> str:
    """Formata uma duração em minutos pra leitura humana — 'X min' abaixo
    de 1h, 'XhYmin' a partir daí (evita algo como "normalizou após 125
    min", que ninguém lê de cabeça; pedido do usuário 14/08/2026)."""
    minutos = int(minutos)
    if minutos < 60:
        return f"{minutos} min"
    horas, mins = divmod(minutos, 60)
    return f"{horas}h{mins}min" if mins else f"{horas}h"


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, _FMT)


# ── tipos ────────────────────────────────────────────────────────────
@dataclass
class Leitura:
    ts: str            # "YYYY-MM-DD HH:MM:SS", BRT
    valor: float


@dataclass
class ConfigCamara:
    id: str
    nome: str
    minimo: float
    maximo: float
    limite_duro: float | None = None   # fora disso = alerta na 1ª leitura
    n_sustentado: int = 2               # leituras fora seguidas => excursão
    k_oscilacao: int = 4                # cruzamentos de fronteira...
    w_janela_min: int = 120             # ...dentro desta janela (minutos)
    cooldown_min: int = 60              # silêncio pós-alerta p/ mesma condição
    m_recuperacao: int = 2              # leituras dentro da faixa p/ normalizar


@dataclass
class EstadoCamara:
    """Espelha a linha de xsense_estado (uma por câmara+dimensão)."""
    estado: str = "NORMAL"              # NORMAL|EM_OBSERVACAO|ALERTANDO|RECUPERADO
    condicao_ativa: str | None = None   # EXCURSAO|OSCILACAO|None
    severidade: str | None = None       # HIGH|MEDIUM|LOW|None
    desde_ts: str | None = None
    ultima_leitura_ts: str | None = None
    ultimo_alerta_ts: str | None = None
    contador_fora: int = 0
    contador_dentro: int = 0


@dataclass
class Evento:
    tipo: str            # EXCURSAO|OSCILACAO|PICO|RECUPERACAO
    severidade: str       # HIGH|MEDIUM|LOW|INFO
    valor: float | None = None
    detalhe: str = ""
    notificar: bool = False   # se True, deve gerar push (respeitando cooldown)
    lembrete: bool = False    # True = câmara já estava ALERTANDO (não é o alerta inicial) —
                              # quem envia (web/xsense.py) usa isso pra mandar só texto, sem gráfico


ESTADOS = ("NORMAL", "EM_OBSERVACAO", "ALERTANDO", "RECUPERADO")
SEVERIDADE_ORDEM = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}


def _dentro_faixa(valor: float, cfg: ConfigCamara) -> bool:
    return cfg.minimo <= valor <= cfg.maximo


def _fora_limite_duro(valor: float, cfg: ConfigCamara) -> bool:
    if cfg.limite_duro is None:
        return False
    # limite_duro pode ser acima do máximo (ex.: CR3 max=4, duro=6) ou
    # abaixo do mínimo (ex.: congelados) — decide o lado pela posição
    # relativa ao próprio máximo/mínimo da faixa normal.
    if cfg.limite_duro > cfg.maximo:
        return valor > cfg.limite_duro
    return valor < cfg.limite_duro


def contar_cruzamentos(leituras: list[Leitura], cfg: ConfigCamara) -> int:
    """Quantas vezes a série entra/sai da faixa normal — captura oscilação
    mesmo quando cada excursão individual volta sozinha (não sustenta o
    suficiente pra virar EXCURSAO)."""
    cruzamentos = 0
    estado_anterior = None
    for leitura in leituras:
        dentro = _dentro_faixa(leitura.valor, cfg)
        if estado_anterior is not None and dentro != estado_anterior:
            cruzamentos += 1
        estado_anterior = dentro
    return cruzamentos


def _leituras_na_janela(leituras: list[Leitura], agora: datetime, janela_min: int) -> list[Leitura]:
    desde = agora - timedelta(minutes=janela_min)
    return [l for l in leituras if _parse_ts(l.ts) >= desde]


def avaliar(cfg: ConfigCamara, leituras_recentes: list[Leitura], estado: EstadoCamara) -> tuple[EstadoCamara, list[Evento]]:
    """
    Roda os 3 detectores + recuperação sobre a leitura mais recente,
    aplica a máquina de estados (histerese) e devolve (novo_estado,
    eventos). `leituras_recentes` deve vir ordenada da mais antiga pra
    mais nova e cobrir pelo menos `w_janela_min` minutos pra Detector 2
    funcionar direito.

    Eventos com `notificar=True` ainda passam pelo cooldown antes de virar
    push de verdade — isso é responsabilidade de quem persiste (ver
    aplicar_cooldown), não deste avaliador (mantém o motor de detecção
    puro/determinístico, fácil de testar).
    """
    if not leituras_recentes:
        return estado, []

    ultima = leituras_recentes[-1]
    agora = _parse_ts(ultima.ts)
    dentro = _dentro_faixa(ultima.valor, cfg)

    if dentro:
        estado.contador_dentro += 1
        estado.contador_fora = 0
    else:
        estado.contador_fora += 1
        estado.contador_dentro = 0
    estado.ultima_leitura_ts = ultima.ts

    eventos: list[Evento] = []
    estado_anterior = estado.estado

    # 1) EXCURSÃO (HIGH) — limite duro estoura na 1ª leitura; limite brando precisa sustentar
    duro = _fora_limite_duro(ultima.valor, cfg)
    excursao = duro or estado.contador_fora >= cfg.n_sustentado
    if excursao:
        eventos.append(Evento("EXCURSAO", "HIGH", valor=ultima.valor,
                              detalhe="fora do limite crítico" if duro else
                                      f"{estado.contador_fora} leituras fora seguidas"))

    # 2) OSCILAÇÃO (MEDIUM) — cruzamentos de fronteira na janela deslizante
    janela = _leituras_na_janela(leituras_recentes, agora, cfg.w_janela_min)
    cruz = contar_cruzamentos(janela, cfg)
    if cruz >= cfg.k_oscilacao:
        eventos.append(Evento("OSCILACAO", "MEDIUM", valor=ultima.valor,
                              detalhe=f"{cruz} oscilações em {cfg.w_janela_min} min"))

    # 3) PICO transitório (INFO, não notifica) — só quando NÃO há excursão/oscilação ativa
    tem_alerta_forte = any(e.tipo in ("EXCURSAO", "OSCILACAO") for e in eventos)
    if not dentro and not tem_alerta_forte and estado.estado == "NORMAL":
        eventos.append(Evento("PICO", "INFO", valor=ultima.valor, detalhe="saiu e voltou da faixa"))

    # Máquina de estados (histerese)
    tem_condicao_forte = any(e.tipo in ("EXCURSAO", "OSCILACAO") for e in eventos)
    if tem_condicao_forte:
        principal = max((e for e in eventos if e.tipo in ("EXCURSAO", "OSCILACAO")),
                        key=lambda e: SEVERIDADE_ORDEM[e.severidade])
        ja_alertando = estado.estado == "ALERTANDO"
        if estado.estado in ("NORMAL", "EM_OBSERVACAO", "RECUPERADO"):
            estado.estado = "ALERTANDO"
            estado.desde_ts = ultima.ts
        # notificar=True tanto na transição quanto sustentado — o motor
        # gera 1 Evento por ciclo de leitura enquanto a condição continua
        # (ver processar_leitura), e é o cooldown_min em aplicar_cooldown
        # que decide a cadência real do push: 1ª vez sem cooldown pra
        # avisar; enquanto ALERTANDO, só repete depois de cooldown_min
        # (efetivamente um lembrete periódico "ainda fora da faixa" —
        # pedido do usuário 29/08/2026, antes só notificava 1x na
        # transição e ficava mudo até normalizar).
        principal.notificar = True
        if ja_alertando:
            principal.lembrete = True
            minutos_fora = int((agora - _parse_ts(estado.desde_ts)).total_seconds() / 60) if estado.desde_ts else 0
            principal.detalhe = f"ainda fora da faixa há {fmt_duracao_min(minutos_fora)}"
        estado.condicao_ativa = principal.tipo
        estado.severidade = principal.severidade
    elif not dentro and estado.estado == "NORMAL":
        estado.estado = "EM_OBSERVACAO"
        estado.desde_ts = ultima.ts
    elif dentro and estado.estado == "EM_OBSERVACAO":
        # voltou sozinho, sem sustentar — já foi registrado como PICO acima
        estado.estado = "NORMAL"
        estado.condicao_ativa = None
        estado.severidade = None

    # RECUPERAÇÃO — só sai de ALERTANDO/RECUPERADO após m_recuperacao
    # leituras seguidas dentro da faixa (evita flapping)
    if estado.estado in ("ALERTANDO", "RECUPERADO") and dentro and estado.contador_dentro >= cfg.m_recuperacao:
        if estado.estado == "ALERTANDO":
            minutos = int((agora - _parse_ts(estado.desde_ts)).total_seconds() / 60) if estado.desde_ts else 0
            eventos.append(Evento("RECUPERACAO", "INFO", valor=ultima.valor,
                                  detalhe=f"normalizou após {fmt_duracao_min(minutos)}", notificar=True))
        estado.estado = "NORMAL"
        estado.condicao_ativa = None
        estado.severidade = None

    return estado, eventos


def aplicar_cooldown(evento: Evento, estado: EstadoCamara, cfg: ConfigCamara, agora_ts: str) -> bool:
    """True se o evento deve virar push de verdade agora (respeitando
    cooldown_min desde o último alerta da MESMA câmara+dimensão). Eventos
    RECUPERACAO não são sujeitos a cooldown — o usuário sempre quer saber
    que resolveu, mesmo que tenha alertado há pouco."""
    if not evento.notificar:
        return False
    if evento.tipo == "RECUPERACAO":
        return True
    if not estado.ultimo_alerta_ts:
        return True
    minutos_desde_ultimo = (_parse_ts(agora_ts) - _parse_ts(estado.ultimo_alerta_ts)).total_seconds() / 60
    return minutos_desde_ultimo >= cfg.cooldown_min
