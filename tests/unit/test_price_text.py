import pytest

from app.core.price_text import parse_price_text

UNKNOWN = {"is_free": None, "price_min": None, "price_max": None}
FREE = {"is_free": True, "price_min": 0.0, "price_max": 0.0}


@pytest.mark.parametrize(
    "text",
    [
        "Gratuito",
        "Entrada gratuita",
        "GRÁTIS",
        "Entrada franca",
        "Acesso livre ao parque",
        "Ingressos gratuitos, retirar 1 hora antes",
        "Gratuidade para todos os públicos",
        "R$ 0,00",
    ],
)
def test_free_entry(text):
    assert parse_price_text(text) == FREE


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("R$ 20", 20.0, 20.0),
        ("R$20,00", 20.0, 20.0),
        ("R$ 12,50–25", 12.5, 12.5),
        ("R$ 20; meia R$ 10", 10.0, 20.0),
        ("Inteira: R$ 30,00 / Meia: R$ 15,00", 15.0, 30.0),
        ("Ingressos de R$ 10 a R$ 40", 10.0, 40.0),
        ("R$ 1.500", 1500.0, 1500.0),
        ("R$ 1.234,56", 1234.56, 1234.56),
        ("R$ 20.50", 20.5, 20.5),
        ("25 reais", 25.0, 25.0),
        ("R$ 0 a R$ 20 (contribuição)", 0.0, 20.0),
    ],
)
def test_amounts_in_reais(text, low, high):
    assert parse_price_text(text) == {
        "is_free": False,
        "price_min": low,
        "price_max": high,
    }


def test_free_on_some_days_is_not_always_free():
    assert parse_price_text("Grátis às quartas; demais dias R$ 25") == {
        "is_free": False,
        "price_min": 25.0,
        "price_max": 25.0,
    }


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "Consulte a programação",
        "Classificação livre",
        "Das 10h às 18h, 12 anos",
        "Varia conforme o evento",
        42,
    ],
)
def test_unknown_without_explicit_statement(text):
    assert parse_price_text(text) == UNKNOWN
