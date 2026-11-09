"""Spoken text, built by code from PayPal's record so a model can never misstate it."""

from .. import catalog
from .rules import RiskResult

MAX_ITEMS_READ_ALOUD = 3

SPELLED_SYMBOLS = {".": "dot", "@": "at", "-": "dash", "_": "underscore"}

CURRENCY_NAMES = {"USD": ("dollar", "cent", "US dollars")}


def spell(text: str) -> str:
    """Letter by letter, so a lookalike cannot hide behind how a word sounds."""
    spoken: list[str] = []
    letters: list[str] = []
    for char in text:
        if char.isalnum():
            letters.append(char.upper())
            continue
        if letters:
            spoken.append("-".join(letters))
            letters = []
        if char in SPELLED_SYMBOLS:
            spoken.append(SPELLED_SYMBOLS[char])
    if letters:
        spoken.append("-".join(letters))
    return " ".join(spoken)


def _unit(count: int, name: str) -> str:
    return f"{count} {name}" if count == 1 else f"{count} {name}s"


def speak_amount(value: str, currency: str) -> str:
    if currency not in CURRENCY_NAMES:
        return f"{value} {currency}"
    major_name, minor_name, currency_name = CURRENCY_NAMES[currency]
    major, _, minor = value.partition(".")
    amount = _unit(int(major), major_name)
    if int(minor or 0):
        amount += f" and {_unit(int(minor), minor_name)}"
    return f"{amount}, {currency_name}"


def mandate_lines(reviewed: dict) -> list[str]:
    """Reads the mandate back before any agent runs, so a misheard request is caught here."""
    fields = reviewed["fields"]
    described = " ".join(
        part for part in (fields["brand"], fields["item"]) if part
    )
    if fields["size"]:
        described += f", size {fields['size']}"
    lines = [f"Buy {fields['quantity']} {described}."]
    if fields["details"]:
        lines.append(f"Must be: {', '.join(fields['details'])}.")
    if reviewed["max_total_cents"] is not None:
        limit = catalog.to_value(reviewed["max_total_cents"])
        lines.append(f"Spend at most {speak_amount(limit, fields['currency'])}, in total.")
    if reviewed["store"]:
        lines.append(f"Only from {reviewed['store']}, a store you've paid before.")
    else:
        lines.append("From any store you've paid before. Any other store needs your approval.")
    lines += reviewed["problems"]
    return lines


def confirmation_for(level: str, pause_seconds: int) -> dict:
    if level == "High":
        return {"method": "type_total", "pause_seconds": pause_seconds}
    if level == "Medium":
        return {"method": "press_twice", "pause_seconds": 0}
    return {"method": "press", "pause_seconds": 0}


def spoken_lines(summary: dict, risk: RiskResult, confirmation: dict) -> list[str]:
    """Every way the order differs from the request comes first: that is the decision."""
    if risk.level == "Low":
        lines = ["This order matches what you asked for."]
    else:
        lines = [f"Warning: {risk.level.lower()} risk. This order is not what you asked for."]
        lines += [hit.reason for hit in risk.hits]
    return lines + _order_lines(summary, risk, confirmation)


SUSPECT_PAYEE_CODES = {"wrong_seller", "lookalike_seller"}


def _order_lines(summary: dict, risk: RiskResult, confirmation: dict) -> list[str]:
    lines = []
    hit_codes = {hit.code for hit in risk.hits}
    payee = str(summary["payee"])
    if hit_codes & SUSPECT_PAYEE_CODES:
        payee = spell(payee)
    elif summary.get("payee_name"):
        payee = summary["payee_name"]
    lines.append(f"Paying {payee.rstrip('.')}.")

    lines.append(f"{speak_amount(summary['total'], summary['currency'])}.")

    items = summary["items"]
    if len(items) > MAX_ITEMS_READ_ALOUD:
        lines.append(f"{sum(item['quantity'] for item in items)} items.")
    else:
        lines += [f"{item['quantity']} {item['name']}." for item in items]

    # Name buttons, not letter keys: a screen reader's reading mode takes single letters
    # for page navigation, while Enter on a button and Escape always get through.
    method = confirmation["method"]
    if method == "type_total":
        pause = confirmation["pause_seconds"]
        lines.append(
            "Cancel is selected: press Enter or Escape to cancel. To pay anyway, wait "
            f"{pause} seconds, type the total in the total field and press Enter."
        )
    elif method == "press_twice":
        lines.append(
            "To pay, Tab to Confirm payment and press Enter twice. Press Escape to cancel."
        )
    else:
        lines.append("To pay, Tab to Confirm payment and press Enter. Press Escape to cancel.")
    return lines
