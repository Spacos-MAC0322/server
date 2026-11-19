"""Read admission prices that open-data sources only publish as free text.

Mapas Culturais has no structured price: "Entrada gratuita", "R$ 20; meia R$ 10".
Only explicit statements count. Text with neither a free-entry phrase nor an
amount in reais ("Consulte a programação") leaves every field unknown (None).
"""

import re
import unicodedata
from typing import TypedDict

# Bare "livre" is left out: it usually means the age rating ("classificação livre").
FREE_ENTRY = re.compile(
    r"\b(?:gratis|gratuit[oa]s?|gratuidade|sem custo|"
    r"(?:entrada|acesso|ingresso)s? (?:franc[oa]|livre))\b"
)
# "R$ 20", "R$20,00", "R$ 1.500" or "20 reais"; amounts must name the currency,
# so times ("10h") and ages ("12 anos") are never read as prices.
AMOUNT = re.compile(
    r"r\$\s*(?P<prefixed>\d[\d.]*(?:,\d{1,2})?)|"
    r"(?P<suffixed>\d[\d.]*(?:,\d{1,2})?)\s*reais\b"
)


class AdmissionPrice(TypedDict):
    is_free: bool | None
    price_min: float | None
    price_max: float | None


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(c for c in decomposed if not unicodedata.combining(c)).split()
    )


def _amount(raw: str) -> float | None:
    """pt-BR amounts: "1.500,50" or "1.500" use dots for thousands."""
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_price_text(value: object) -> AdmissionPrice:
    """Free entry, or the lowest and highest amounts in reais, when stated.

    Free-entry phrases alongside amounts ("grátis às quartas, demais dias R$ 25")
    mean the place is not always free, so is_free is False and the amounts set
    the range. A sole amount of zero counts as free entry.
    """
    unknown: AdmissionPrice = {"is_free": None, "price_min": None, "price_max": None}
    if not isinstance(value, str) or not value.strip():
        return unknown
    folded = _fold(value)
    amounts = [
        amount
        for match in AMOUNT.finditer(folded)
        if (amount := _amount(match["prefixed"] or match["suffixed"])) is not None
    ]
    if amounts:
        low, high = min(amounts), max(amounts)
        return {"is_free": high == 0, "price_min": low, "price_max": high}
    if FREE_ENTRY.search(folded):
        return {"is_free": True, "price_min": 0.0, "price_max": 0.0}
    return unknown
