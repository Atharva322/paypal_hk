"""Spoken text, built by code from PayPal's record so a model can never misstate it."""

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


def confirmation_for(level: str, pause_seconds: int) -> dict:
    if level == "High":
        return {"method": "type_total", "pause_seconds": pause_seconds}
    if level == "Medium":
        return {"method": "press_twice", "pause_seconds": 0}
    return {"method": "press", "pause_seconds": 0}


def spoken_lines(summary: dict, risk: RiskResult, confirmation: dict) -> list[str]:
    lines = []
    if risk.level != "Low":
        lines.append(f"Warning: {risk.level.lower()} risk. {risk.hits[0].reason}")

    hit_codes = {hit.code for hit in risk.hits}
    payee = str(summary["payee"])
    if hit_codes & {"unknown_payee", "lookalike_name"}:
        payee = spell(payee)
    lines.append(f"Paying {payee}.")

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
