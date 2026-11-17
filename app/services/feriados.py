"""
Feriados nacionais — usado para não prever descarga em dia que o CD não abre.

**Calculado, não listado.** Parte dos feriados é móvel e depende da Páscoa; uma
lista chumbada expira na virada do ano em silêncio, e o sintoma seria uma
previsão de descarga errada que ninguém percebe. O `dateutil` já vem instalado
(vem junto do pandas), então não há dependência nova. Também foi descartada a
ideia de consultar API (BrasilAPI e afins): acrescenta chamada de rede num
cálculo que roda a cada carregamento, a VM é uma e2-micro que já vai pra swap,
e o modo de falha é justamente o silencioso.

**Carnaval x Corpus Christi** (regra do usuário, 03/09/2026): vale **Carnaval**
nas cidades carnavalescas e **Corpus Christi** nas demais — nunca os dois no
mesmo lugar. Juridicamente nenhum dos dois é feriado nacional (são ponto
facultativo), mas na prática o CD para, então entram. Estadual e municipal
ficam de fora por ora.
"""
from __future__ import annotations

from datetime import date, timedelta

from dateutil.easter import easter

# (mês, dia) -> nome. Só os fixos; os móveis saem da Páscoa.
_FIXOS = {
    (1, 1):   "Confraternização Universal",
    (4, 21):  "Tiradentes",
    (5, 1):   "Dia do Trabalho",
    (9, 7):   "Independência",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2):  "Finados",
    (11, 15): "Proclamação da República",
    (12, 25): "Natal",
}

_cache: dict[tuple[int, bool], dict[date, str]] = {}


def feriados_do_ano(ano: int, carnavalesca: bool = False) -> dict[date, str]:
    """{data: nome} dos feriados do ano. `carnavalesca` troca Corpus Christi
    por Carnaval."""
    chave = (ano, carnavalesca)
    if chave in _cache:
        return _cache[chave]

    pascoa = easter(ano)
    dias = {date(ano, m, d): nome for (m, d), nome in _FIXOS.items()}
    dias[pascoa - timedelta(days=2)] = "Sexta-feira Santa"
    if carnavalesca:
        dias[pascoa - timedelta(days=47)] = "Carnaval"
    else:
        dias[pascoa + timedelta(days=60)] = "Corpus Christi"

    _cache[chave] = dias
    return dias


def nome_do_feriado(d: date, carnavalesca: bool = False) -> str | None:
    """Nome do feriado nessa data, ou None se for dia comum."""
    return feriados_do_ano(d.year, carnavalesca).get(d)


def eh_feriado(d: date, carnavalesca: bool = False) -> bool:
    return d in feriados_do_ano(d.year, carnavalesca)
