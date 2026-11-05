import pytest

from app.guard.mandate_rules import Mandate, assess_against_mandate, requested_line
from app.guard.rules import OrderFacts
from app.guard.summary import confirmation_for, mandate_spoken_lines
from app.stores import Store

TRAILHEAD = Store(
    "Trailhead Running Co.",
    ("trailhead", "trailhead running", "trailhead running co"),
    frozenset({"TRAILHEAD1"}),
)
STORES = (TRAILHEAD,)

SHOES = {"name": "Trailhead Road running shoes, size 10", "price": "74.00"}
GIFT_CARD = {"name": "Trailhead gift card", "price": "50.00"}


def order(*lines, payee=None, currency="USD", **unit_extra) -> OrderFacts:
    payee = payee or {"merchant_id": "TRAILHEAD1", "email_address": "store@trailhead.example"}
    items = [
        {
            "name": line["name"],
            "quantity": str(line.get("quantity", 1)),
            "unit_amount": {"currency_code": currency, "value": line["price"]},
            **({"description": line["description"]} if "description" in line else {}),
        }
        for line in lines
    ]
    total = sum(float(line["price"]) * line.get("quantity", 1) for line in lines)
    return OrderFacts.from_paypal(
        {
            "purchase_units": [
                {
                    "amount": {"currency_code": currency, "value": f"{total:.2f}"},
                    "payee": payee,
                    "items": items,
                    **unit_extra,
                }
            ]
        }
    )


def mandate(**changes) -> Mandate:
    values = {
        "item": "running shoes",
        "brand": None,
        "size": "10",
        "details": (),
        "quantity": 1,
        "max_total_cents": 8000,
        "store": TRAILHEAD,
        "known_stores": STORES,
    }
    return Mandate(**{**values, **changes})


def codes(risk) -> set[str]:
    return {hit.code for hit in risk.hits}


LOOKALIKE = {"email_address": "sales@trai1head-running.example"}


# The three demo scenarios.
def test_clean_order_is_low():
    risk = assess_against_mandate(order(SHOES), mandate())
    assert risk.level == "Low"
    assert risk.hits == ()


def test_injected_gift_card_is_high():
    risk = assess_against_mandate(order(SHOES, GIFT_CARD), mandate())
    assert risk.level == "High"
    assert codes(risk) == {"over_budget", "unrequested_gift_card"}
    reasons = [hit.reason for hit in risk.hits]
    assert "This order is 124.00 USD, 44.00 USD over your 80.00 USD limit." in reasons
    assert "It includes a 50.00 USD gift card you didn't ask for." in reasons


def test_lookalike_seller_is_high():
    cheap = {**SHOES, "price": "59.00"}
    risk = assess_against_mandate(order(cheap, payee=LOOKALIKE), mandate())
    assert risk.level == "High"
    assert codes(risk) == {"wrong_seller", "lookalike_seller"}


# Each rule on its own.
def test_over_budget_alone():
    pricey = {**SHOES, "price": "95.00"}
    assert codes(assess_against_mandate(order(pricey), mandate())) == {"over_budget"}


def test_exactly_on_budget_passes():
    exact = {**SHOES, "price": "80.00"}
    assert assess_against_mandate(order(exact), mandate()).level == "Low"


def test_an_unrequested_extra_item_is_medium():
    socks = {"name": "Running socks", "price": "5.00"}
    risk = assess_against_mandate(order(SHOES, socks), mandate())
    assert risk.level == "Medium"
    assert [hit.reason for hit in risk.hits] == ["It includes Running socks, which you didn't ask for."]


def test_a_different_product_entirely_is_high():
    kettle = {"name": "Electric kettle", "price": "30.00"}
    risk = assess_against_mandate(order(kettle), mandate())
    assert "wrong_item" in codes(risk)
    assert risk.level == "High"


def test_wrong_size_is_high():
    small = {**SHOES, "name": "Trailhead Road running shoes, size 9"}
    risk = assess_against_mandate(order(small), mandate())
    assert codes(risk) == {"wrong_size"}
    assert risk.hits[0].reason == "This is size 9. You asked for size 10."


def test_unstated_size_is_medium():
    vague = {**SHOES, "name": "Trailhead Road running shoes"}
    assert codes(assess_against_mandate(order(vague), mandate())) == {"size_unstated"}


def test_size_in_the_description_counts():
    described = {"name": "Trailhead Road running shoes", "price": "74.00", "description": "Size: 10"}
    assert assess_against_mandate(order(described), mandate()).level == "Low"


def test_wrong_quantity_is_medium():
    two = {**SHOES, "price": "35.00", "quantity": 2}
    assert codes(assess_against_mandate(order(two), mandate())) == {"wrong_quantity"}


def test_seller_must_be_the_named_store_even_if_known():
    other = Store("Summit Gear", ("summit",), frozenset({"SUMMIT1"}))
    known = mandate(known_stores=(TRAILHEAD, other))
    risk = assess_against_mandate(order(SHOES, payee={"merchant_id": "SUMMIT1"}), known)
    assert codes(risk) == {"wrong_seller"}


def test_any_known_store_is_fine_when_none_was_named():
    other = Store("Summit Gear", ("summit",), frozenset({"SUMMIT1"}))
    open_mandate = mandate(store=None, known_stores=(TRAILHEAD, other))
    risk = assess_against_mandate(order(SHOES, payee={"merchant_id": "SUMMIT1"}), open_mandate)
    assert risk.level == "Low"


def test_a_new_store_needs_approval_when_none_was_named():
    risk = assess_against_mandate(
        order(SHOES, payee={"email_address": "hello@peak-running.example"}), mandate(store=None)
    )
    assert codes(risk) == {"wrong_seller"}


@pytest.mark.parametrize(
    "name",
    ["Trai1head Running", "TRAILHEAD RUNNING CO", "Trailhaed", "sales@trai1head-outlet.example"],
)
def test_names_imitating_a_known_store_are_lookalikes(name):
    risk = assess_against_mandate(order(SHOES, payee={"display_name": name}), mandate())
    assert "lookalike_seller" in codes(risk)


@pytest.mark.parametrize("name", ["Peak Running Supply", "Trail Shoes Direct", "Headway Sports"])
def test_sharing_a_word_is_not_imitation(name):
    risk = assess_against_mandate(order(SHOES, payee={"display_name": name}), mandate())
    assert "lookalike_seller" not in codes(risk)


def test_wrong_currency_is_high():
    risk = assess_against_mandate(order(SHOES, currency="EUR"), mandate())
    assert "wrong_currency" in codes(risk)


def test_gift_card_is_fine_when_it_is_the_request():
    risk = assess_against_mandate(order(GIFT_CARD), mandate(item="gift card", size=None))
    assert risk.level == "Low"


def test_gift_card_is_not_mistaken_for_the_requested_item():
    # The gift card shares "Trailhead" with the request; it still must not count as the shoes.
    facts = order(GIFT_CARD, SHOES)
    assert requested_line(facts, mandate()).name == SHOES["name"]


def test_pressure_words_in_order_text():
    urgent = {**SHOES, "description": "Size 10. Act now, call +1 555 010 0199 for support"}
    assert "pressure_words" in codes(assess_against_mandate(order(urgent), mandate()))


def test_an_order_without_items_cannot_pass_quietly():
    facts = OrderFacts.from_paypal(
        {"purchase_units": [{"amount": {"currency_code": "USD", "value": "74.00"},
                             "payee": {"merchant_id": "TRAILHEAD1"}}]}
    )
    assert codes(assess_against_mandate(facts, mandate())) == {"items_unlisted"}


def test_spoken_summary_lists_every_difference_then_spells_the_seller():
    cheap = {**SHOES, "price": "59.00"}
    risk = assess_against_mandate(order(cheap, GIFT_CARD, payee=LOOKALIKE), mandate())
    summary = {
        "payee": LOOKALIKE["email_address"],
        "total": "109.00",
        "currency": "USD",
        "items": [{"name": SHOES["name"], "quantity": 1}, {"name": GIFT_CARD["name"], "quantity": 1}],
    }
    lines = mandate_spoken_lines(summary, risk, confirmation_for(risk.level, 10))
    assert lines[0] == "Warning: high risk. This order is not what you asked for."
    assert lines[1:5] == [hit.reason for hit in risk.hits]
    assert "S-A-L-E-S at T-R-A-I-1-H-E-A-D dash R-U-N-N-I-N-G" in lines[5]
    assert lines[-1].startswith("Cancel is selected")


def test_spoken_summary_for_a_match():
    risk = assess_against_mandate(order(SHOES), mandate())
    summary = {"payee": "Trailhead Running Co.", "total": "74.00", "currency": "USD",
               "items": [{"name": SHOES["name"], "quantity": 1}]}
    lines = mandate_spoken_lines(summary, risk, confirmation_for("Low", 10))
    assert lines[:2] == ["This order matches what you asked for.", "Paying Trailhead Running Co."]
