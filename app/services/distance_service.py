import math
import webbrowser


def _to_float(value):
    if value is None:
        return None

    try:
        text = str(value).strip().replace(",", ".")
        if text == "" or text.lower() == "nan":
            return None
        return float(text)
    except Exception:
        return None


def coordenadas_validas(lat, lon):
    lat_f = _to_float(lat)
    lon_f = _to_float(lon)

    if lat_f is None or lon_f is None:
        return False

    return -90 <= lat_f <= 90 and -180 <= lon_f <= 180


def calcular_km_estimado(lat_origem, lon_origem, lat_destino, lon_destino, fator_rodoviario=1.25):
    """
    Calcula distância aproximada.

    Se faltar qualquer coordenada, retorna vazio.
    Não usa KM da OpenTech como fallback.
    """
    lat1 = _to_float(lat_origem)
    lon1 = _to_float(lon_origem)
    lat2 = _to_float(lat_destino)
    lon2 = _to_float(lon_destino)

    if None in [lat1, lon1, lat2, lon2]:
        return ""

    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90 and -180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        return ""

    raio_terra_km = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distancia_reta = raio_terra_km * c

    try:
        fator = float(str(fator_rodoviario).replace(",", "."))
    except Exception:
        fator = 1.25

    estimado = distancia_reta * fator
    return int(round(estimado, 0))


def abrir_rota_google_maps(lat_origem, lon_origem, lat_destino, lon_destino):
    lat1 = _to_float(lat_origem)
    lon1 = _to_float(lon_origem)
    lat2 = _to_float(lat_destino)
    lon2 = _to_float(lon_destino)

    if None in [lat1, lon1, lat2, lon2]:
        raise ValueError(
            "Latitude/longitude inválida. Verifique se o relatório possui LAT/LONG "
            "e se a localização do CD foi configurada."
        )

    if not coordenadas_validas(lat1, lon1) or not coordenadas_validas(lat2, lon2):
        raise ValueError("Coordenadas fora do intervalo válido.")

    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={lat1},{lon1}"
        f"&destination={lat2},{lon2}"
        "&travelmode=driving"
    )

    webbrowser.open(url)
