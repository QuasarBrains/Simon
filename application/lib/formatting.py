from decimal import Decimal


def dollars(value: str) -> str:
    amount = Decimal(value)
    if 0 < amount < Decimal("0.0001"):
        return "<$0.0001"
    return f"${amount:,.4f}"
