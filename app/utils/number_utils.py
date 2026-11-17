import re


def format_int_br(value) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return ""

    # remove unidade km, espaços e separadores antigos
    text = text.replace("km", "").replace("KM", "").strip()
    text = text.replace(".", "").replace(",", ".")

    try:
        number = float(text)
        return f"{int(round(number, 0)):,}".replace(",", ".")
    except Exception:
        return str(value)


def parse_int_br(value):
    if value is None:
        return None

    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None

    text = text.replace("km", "").replace("KM", "").strip()
    text = text.replace(".", "").replace(",", ".")

    match = re.search(r"-?\d+(\.\d+)?", text)
    if not match:
        return None

    try:
        return int(round(float(match.group(0)), 0))
    except Exception:
        return None


def calcular_tempo_60kmh(km_value) -> str:
    km = parse_int_br(km_value)
    if km is None:
        return ""

    horas_float = km / 60
    horas = int(horas_float)
    minutos = int(round((horas_float - horas) * 60, 0))

    if minutos == 60:
        horas += 1
        minutos = 0

    if horas <= 0:
        return f"{minutos} min"

    return f"{horas}h {minutos:02d}min"
