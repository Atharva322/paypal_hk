import pytest

from app.guard.rules import RiskResult, RuleHit
from app.guard.summary import confirmation_for, speak_amount, spell, spoken_lines

SUMMARY = {
    "payee": "gadgethub.shop",
    "total": "49.99",
    "currency": "USD",
    "items": [{"name": "Wireless earbuds", "quantity": 1, "unit_price": "49.99"}],
}
LOW = RiskResult("Low", 0, ())
HIGH = RiskResult("High", 60, (RuleHit("lookalike_name", 60, "The payee name imitates paypal."),))


@pytest.mark.parametrize(
    ("value", "spoken"),
    [
        ("49.99", "49 dollars and 99 cents, US dollars"),
        ("1.01", "1 dollar and 1 cent, US dollars"),
        ("50.00", "50 dollars, US dollars"),
        ("0.05", "0 dollars and 5 cents, US dollars"),
    ],
)
def test_amounts_read_with_currency_and_decimals(value, spoken):
    assert speak_amount(value, "USD") == spoken


def test_unnamed_currency_is_not_guessed():
    assert speak_amount("49.99", "INR") == "49.99 INR"


def test_spelling_keeps_lookalike_characters_apart():
    assert spell("gadgethub.shop") == "G-A-D-G-E-T-H-U-B dot S-H-O-P"
    assert spell("paypa1") == "P-A-Y-P-A-1"


def test_low_risk_reads_in_plan_order_without_a_warning():
    lines = spoken_lines(SUMMARY, LOW, confirmation_for("Low", 10))
    assert lines == [
        "Paying gadgethub.shop.",
        "49 dollars and 99 cents, US dollars.",
        "1 Wireless earbuds.",
        "To pay, Tab to Confirm payment and press Enter. Press Escape to cancel.",
    ]


def test_high_risk_warns_first_and_spells_the_payee():
    lines = spoken_lines(SUMMARY, HIGH, confirmation_for("High", 10))
    assert lines[0] == "Warning: high risk. The payee name imitates paypal."
    assert lines[1] == "Paying G-A-D-G-E-T-H-U-B dot S-H-O-P."
    assert lines[-1] == (
        "Cancel is selected: press Enter or Escape to cancel. To pay anyway, wait "
        "10 seconds, type the total in the total field and press Enter."
    )


def test_medium_risk_asks_for_two_presses():
    medium = RiskResult("Medium", 30, (RuleHit("hidden_charges", 30, "Extra charges."),))
    lines = spoken_lines(SUMMARY, medium, confirmation_for("Medium", 10))
    assert lines[0] == "Warning: medium risk. Extra charges."
    assert lines[-1] == (
        "To pay, Tab to Confirm payment and press Enter twice. Press Escape to cancel."
    )


def test_more_than_three_items_become_a_count():
    item = {"name": "Case", "quantity": 2, "unit_price": "15.99"}
    summary = {**SUMMARY, "items": [item] * 4}
    lines = spoken_lines(summary, LOW, confirmation_for("Low", 10))
    assert "8 items." in lines
    assert "2 Case." not in lines


def test_instructions_never_rely_on_letter_shortcuts():
    for level, risk in (("Low", LOW), ("Medium", LOW), ("High", HIGH)):
        instruction = spoken_lines(SUMMARY, risk, confirmation_for(level, 10))[-1]
        assert "Press C" not in instruction
        assert "Press X" not in instruction
        assert "Escape" in instruction
