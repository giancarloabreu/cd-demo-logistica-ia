import re
import unicodedata
from datetime import datetime, date


def normalize_text(value) -> str:
    if value is None:
        return ""

    text = str(value).strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_plate(value) -> str:
    if value is None:
        return ""

    text = normalize_text(value)
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text


def normalize_key(value) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return re.sub(r"\s+", "", text)


def first_filled(*values) -> str:
    for value in values:
        if value is not None and str(value).strip() != "" and str(value).strip().lower() != "nan":
            return str(value).strip()
    return ""


def format_date_br(value) -> str:
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")

    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")

    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return ""

    # tenta formatos comuns: 2026-05-29 00:00:00, 2026-05-29, 29/05/2026
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:19] if " " in fmt else text[:10], fmt).strftime("%d/%m/%Y")
        except Exception:
            pass

    return text
