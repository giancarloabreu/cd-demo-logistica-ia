"""
Geocodificação reversa (lat/lon -> "Cidade/UF") usando o Nominatim
(OpenStreetMap), gratuito e sem chave de API — ver MEL-14 no BACKLOG.

Usado só pra Ravex, que não traz cidade no relatório (só lat/lon), ao
contrário da OpenTech. Não usa nenhuma biblioteca externa nova (só
urllib da stdlib) pra não precisar reinstalar dependência na VM.

Política de uso do Nominatim (obrigatória): no máximo 1 requisição por
segundo, com um User-Agent identificando a aplicação. Por isso:
- Cache em banco por coordenada arredondada (~111m) — ver app/database.py
  get_cidade_cache/set_cidade_cache. Veículos parados no mesmo lugar (ex.:
  "no CD") nunca batem no serviço de novo depois da primeira vez.
- Limite de 1 req/s controlado localmente (só entre chamadas que realmente
  precisam ir à internet, não entre acertos de cache).
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.parse

from app.database import get_cidade_cache, set_cidade_cache

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT = "CDDemo/1.0 (demonstracao)"
_INTERVALO_MIN_SEG = 1.1  # Nominatim exige <= 1 req/s; margem de segurança

_ultima_chamada = 0.0


def _arredondar(valor: float, casas: int = 3) -> float:
    return round(float(valor), casas)


def _consultar_nominatim(lat: float, lon: float) -> str:
    """Chama o Nominatim de verdade. Retorna 'Cidade/UF' ou '' se não achar
    ou der erro (nunca lança exceção — geocodificação é best-effort)."""
    global _ultima_chamada

    espera = _INTERVALO_MIN_SEG - (time.time() - _ultima_chamada)
    if espera > 0:
        time.sleep(espera)

    params = urllib.parse.urlencode({
        "format": "jsonv2",
        "lat": f"{lat}",
        "lon": f"{lon}",
        "zoom": "10",
        "addressdetails": "1",
    })
    url = f"{_NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _ultima_chamada = time.time()
    except Exception:
        _ultima_chamada = time.time()
        return ""

    endereco = data.get("address", {}) if isinstance(data, dict) else {}
    cidade = (endereco.get("city") or endereco.get("town") or
              endereco.get("village") or endereco.get("municipality") or "")
    uf = endereco.get("state_code", "") or ""
    if not uf:
        # Nominatim às vezes só traz o nome completo do estado, não a sigla
        estado_completo = endereco.get("state", "")
        uf = _ESTADOS_UF.get(estado_completo.upper(), "")

    if not cidade:
        return ""
    return f"{cidade}/{uf}" if uf else cidade


_ESTADOS_UF = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPÁ": "AP", "AMAZONAS": "AM", "BAHIA": "BA",
    "CEARÁ": "CE", "DISTRITO FEDERAL": "DF", "ESPÍRITO SANTO": "ES", "GOIÁS": "GO",
    "MARANHÃO": "MA", "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS",
    "MINAS GERAIS": "MG", "PARÁ": "PA", "PARAÍBA": "PB", "PARANÁ": "PR",
    "PERNAMBUCO": "PE", "PIAUÍ": "PI", "RIO DE JANEIRO": "RJ",
    "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS", "RONDÔNIA": "RO",
    "RORAIMA": "RR", "SANTA CATARINA": "SC", "SÃO PAULO": "SP", "SERGIPE": "SE",
    "TOCANTINS": "TO",
}


def obter_cidade_por_coordenada(lat, lon) -> str:
    """Retorna 'Cidade/UF' pra essas coordenadas, usando cache quando
    possível. Nunca lança exceção — se falhar, retorna ''."""
    try:
        lat_f, lon_f = float(str(lat).replace(",", ".")), float(str(lon).replace(",", "."))
    except (TypeError, ValueError):
        return ""
    if not lat_f and not lon_f:
        return ""

    lat_r, lon_r = _arredondar(lat_f), _arredondar(lon_f)

    cache = get_cidade_cache(lat_r, lon_r)
    if cache is not None:
        return cache.upper()

    cidade = _consultar_nominatim(lat_r, lon_r)
    try:
        set_cidade_cache(lat_r, lon_r, cidade)
    except Exception:
        pass
    # Maiúsculo pra bater com o padrão da OpenTech (relatório já vem em
    # caixa alta) — normaliza aqui (não no cache) pra cobrir também entradas
    # já cacheadas antes dessa mudança, sem precisar invalidar nada.
    return cidade.upper()
