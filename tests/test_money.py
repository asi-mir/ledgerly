from decimal import Decimal

from app.money import format_money, parse_amount, round_to, to_cents


def test_turkish_receipt_numbers():
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("12,50") == Decimal("12.50")
    assert parse_amount("₺1.234,56") == Decimal("1234.56")
    assert parse_amount("1.250.000,00") == Decimal("1250000.00")


def test_chat_numbers():
    assert parse_amount("30000") == Decimal("30000.00")
    assert parse_amount("30.000") == Decimal("30000.00")
    assert parse_amount("30,000") == Decimal("30000.00")
    assert parse_amount("1,234.56") == Decimal("1234.56")
    assert parse_amount("30k") == Decimal("30000.00")
    assert parse_amount("₺5,000 gift") == Decimal("5000.00")
    assert parse_amount("no number") is None


def test_cents_and_format():
    assert to_cents(Decimal("1234.56")) == 123456
    assert format_money(30000) == "₺30,000"
    assert format_money(Decimal("1234.50")) == "₺1,234.50"
    assert round_to(38412) == 38400


def test_discount_signs():
    assert parse_amount("12,50-") == Decimal("-12.50")
    assert parse_amount("−12,50") == Decimal("-12.50")
    assert parse_amount("-12,50") == Decimal("-12.50")
