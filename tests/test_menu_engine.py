"""Tests for the pure decision engine (tools/menu_engine.py). No I/O, no
network, no config file - every input is a plain value.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.menu_engine import (STATUS_FLOW, detect_dietary, guest_status_copy,  # noqa: E402
                               kitchen_eta, load_menu, menu_by_slug, money, next_status,
                               resolve_menu_item, resolve_order)

MENU_RAW = [
    {"slug": "club-sandwich", "name": "Club sandwich", "category": "All-day kitchen",
     "price": 28, "description": "Chicken, bacon, tomato"},
    {"slug": "sparkling-water", "name": "Sparkling water", "category": "Drinks", "price": 6},
    {"slug": "caesar", "name": "Caesar salad", "category": "All-day kitchen", "price": 24},
    {"slug": "fondant", "name": "Chocolate fondant", "category": "Desserts", "price": 16},
    {"slug": "sea-bass", "name": "Grilled sea bass", "category": "All-day kitchen", "price": 42},
]
MENU = load_menu(MENU_RAW)
BY_SLUG = menu_by_slug(MENU)
KITCHEN_CFG = {"tray_charge": 8, "eta_base_minutes": 35, "capacity_before_delay": 4,
               "delay_step_minutes": 10, "max_eta_minutes": 75, "sold_out": ["sea-bass"]}
UPSELL_CFG = {"target_slug": "fondant", "category": "Desserts", "min_orders_for_stat": 5}


def _resolve(extracted: dict, **overrides) -> "ResolvedOrder":  # noqa: F821 - forward ref ok
    kwargs = dict(menu=MENU, currency="EUR", hotel_languages=["en", "pt", "es"],
                  default_language="en", kitchen_cfg=KITCHEN_CFG, upsell_enabled=True,
                  upsell_cfg=UPSELL_CFG, active_tickets=0, historic_attach_rate=None,
                  confidence_threshold=0.55, room_number="204", location="")
    kwargs.update(overrides)
    return resolve_order(extracted, **kwargs)


# --------------------------------------------------------------------------
# dietary detection - the safety-critical part. Six languages, three
# outcomes, and a non-empty note is never silently dropped.
# --------------------------------------------------------------------------
def test_dietary_named_allergen_in_five_languages():
    assert detect_dietary("sans gluten").kind == "named"
    assert "gluten" in detect_dietary("sans gluten").allergens
    assert "gluten" in detect_dietary("senza glutine, ho la celiachia").allergens
    assert "dairy" in detect_dietary("sem lactose, sou intolerante a lactose").allergens
    assert "nut" in detect_dietary("eine Nussallergie").allergens
    assert "nut" in detect_dietary("alergia a los frutos secos").allergens


def test_dietary_ambiguous_signal_still_escalates():
    finding = detect_dietary("tengo una alergia")
    assert finding.kind == "ambiguous"
    assert finding.allergens == []


def test_dietary_unclassified_note_still_escalates_never_none():
    finding = detect_dietary("vegetarian please, no meat")
    assert finding.kind == "unclassified"


def test_dietary_empty_note_is_none():
    assert detect_dietary("").kind == "none"
    assert detect_dietary("   ").kind == "none"


def test_dietary_accent_folding():
    # "Alérgico" (accented, capitalised) must match the same as "alergico".
    assert detect_dietary("Soy ALÉRGICO a los mariscos").allergens == ["shellfish"]


def test_dietary_named_allergen_in_greek():
    # SIMULATION.md finding 2's own reproduction: a Cyprus resort's Greek
    # nut-allergy note, which used to degrade to the generic
    # `dietary_signal:noted` instead of `named:nut`.
    finding = detect_dietary(
        "Γεια σας, θα ήθελα ένα halloumi burger, "
        "έχω αλλεργία στους ξηρούς καρπούς")
    assert finding.kind == "named"
    assert finding.allergens == ["nut"]


def test_dietary_named_allergen_in_greek_other_families():
    assert detect_dietary("Έχω αλλεργία στη γλουτένη").allergens == ["gluten"]
    assert detect_dietary("αλλεργία στη λακτόζη").allergens == ["dairy"]
    assert detect_dietary("αλλεργία στα θαλασσινά").allergens == ["shellfish"]
    assert detect_dietary("αλλεργία στο αυγό").allergens == ["egg"]
    assert detect_dietary("αλλεργία στη σόγια").allergens == ["soy"]


def test_dietary_ambiguous_signal_in_greek_still_escalates():
    finding = detect_dietary("έχω μια αλλεργία")
    assert finding.kind == "ambiguous"
    assert finding.allergens == []


def test_dietary_named_allergen_in_dutch():
    assert detect_dietary("ik ben allergisch voor noten").allergens == ["nut"]
    assert detect_dietary("glutenallergie").allergens == ["gluten"]
    assert detect_dietary("lactose intolerantie").allergens == ["dairy"]
    assert detect_dietary("schaaldierenallergie").allergens == ["shellfish"]
    assert detect_dietary("eiallergie").allergens == ["egg"]
    assert detect_dietary("pinda allergie").allergens == ["nut"]


def test_dietary_ambiguous_signal_in_dutch_still_escalates():
    # Dutch spells anaphylaxis "anafylaxie" - "ph" spellings do not match,
    # so this needed its own stem (see _ALLERGY_SIGNAL_STEMS).
    finding = detect_dietary("geschiedenis van anafylaxie")
    assert finding.kind == "ambiguous"


def test_dietary_unsupported_language_still_escalates_never_named():
    # Swedish has no named-allergen stems at all (only en es fr de it pt el
    # nl do - README "Named-allergen coverage by language"). The shared
    # Germanic/Romance "-allerg-" root still trips the generic ambiguous
    # signal here (never a bug: over-triggering only costs one extra human
    # glance), but it is never tagged with a specific allergen family.
    finding = detect_dietary("jag har en nötallergi")
    assert finding.kind == "ambiguous"
    assert finding.allergens == []


def test_dietary_unsupported_language_preference_note_is_unclassified_not_silent():
    # A Swedish dietary preference with no allergy-signal word at all still
    # escalates - detect_dietary() never returns "none" for a non-empty
    # note, whatever language it is in.
    finding = detect_dietary("jag är vegetarian, inget kött")
    assert finding.kind == "unclassified"
    assert finding.allergens == []


# --------------------------------------------------------------------------
# menu matching
# --------------------------------------------------------------------------
def test_resolve_menu_item_exact_slug():
    item = resolve_menu_item({"slug": "club-sandwich", "raw_text": "a club sandwich"}, BY_SLUG)
    assert item is not None and item.slug == "club-sandwich"


def test_resolve_menu_item_fuzzy_name_match_when_slug_is_empty():
    item = resolve_menu_item({"slug": "", "raw_text": "the chocolate fondant please"}, BY_SLUG)
    assert item is not None and item.slug == "fondant"


def test_resolve_menu_item_does_not_false_match_on_a_bare_slug_word():
    # "chicken caesar wrap" is not this hotel's Caesar salad - a bare slug
    # word ("caesar") must not be enough to claim a match.
    item = resolve_menu_item({"slug": "", "raw_text": "a chicken caesar wrap"}, BY_SLUG)
    assert item is None


def test_resolve_menu_item_invented_slug_is_ignored():
    item = resolve_menu_item({"slug": "lobster-thermidor", "raw_text": "lobster"}, BY_SLUG)
    assert item is None


# --------------------------------------------------------------------------
# kitchen capacity - the "cant: kitchen capacity rules cap what it promises"
# implementation.
# --------------------------------------------------------------------------
def test_kitchen_eta_grows_past_capacity_and_caps():
    assert kitchen_eta(0, KITCHEN_CFG) == 35
    assert kitchen_eta(4, KITCHEN_CFG) == 35            # at capacity, no growth yet
    assert kitchen_eta(6, KITCHEN_CFG) == 35 + 2 * 10    # 2 tickets over capacity
    assert kitchen_eta(100, KITCHEN_CFG) == 75           # capped at max_eta_minutes


# --------------------------------------------------------------------------
# money - never a hardcoded currency
# --------------------------------------------------------------------------
def test_money_formats_in_the_hotels_own_currency():
    assert money(42, "EUR") == "EUR 42"
    assert money(1234, "GBP") == "GBP 1,234"
    assert money(48, "NOK") == "NOK 48"


# --------------------------------------------------------------------------
# status flow
# --------------------------------------------------------------------------
def test_next_status_walks_the_flow_and_stops_at_delivered():
    assert next_status(None) == "placed"
    assert next_status("placed") == "preparing"
    assert next_status("preparing") == "on the way"
    assert next_status("on the way") == "delivered"
    assert next_status("delivered") is None


def test_guest_status_copy_falls_back_to_english():
    assert guest_status_copy("placed", "es") != guest_status_copy("placed", "en")
    assert guest_status_copy("placed", "zz") == guest_status_copy("placed", "en")


# --------------------------------------------------------------------------
# resolve_order - the full deterministic pipeline
# --------------------------------------------------------------------------
def test_resolve_order_clean_order_is_priced_and_offers_the_upsell():
    extracted = {"is_order": True, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "club-sandwich", "raw_text": "a club sandwich", "quantity": 1},
                         {"slug": "sparkling-water", "raw_text": "sparkling water",
                          "quantity": 1}]}
    resolved = _resolve(extracted)
    assert resolved.subtotal == 34
    assert resolved.tray_charge == 8
    assert resolved.total == 42
    assert resolved.needs_human is False
    assert resolved.upsell is not None and resolved.upsell.slug == "fondant"


def test_resolve_order_quantity_suffix_prices_correctly():
    extracted = {"is_order": True, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "club-sandwich", "raw_text": "club sandwich", "quantity": 1},
                         {"slug": "sparkling-water", "raw_text": "2 sparkling waters",
                          "quantity": 2}]}
    resolved = _resolve(extracted)
    assert resolved.subtotal == 40  # 28 + 6*2
    assert resolved.total == 48


def test_resolve_order_dietary_note_forces_needs_human_even_when_confident():
    extracted = {"is_order": True, "language": "fr", "confidence": 0.95,
                "dietary_note": "sans gluten",
                "items": [{"slug": "club-sandwich", "raw_text": "sandwich", "quantity": 1}]}
    resolved = _resolve(extracted)
    assert resolved.needs_human is True
    assert any(g.startswith("dietary_signal:named:gluten") for g in resolved.gates)


def test_resolve_order_sold_out_item_is_never_silently_substituted():
    extracted = {"is_order": True, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "sea-bass", "raw_text": "the sea bass", "quantity": 1}]}
    resolved = _resolve(extracted)
    assert resolved.lines == []
    assert resolved.total == 0
    assert "sold_out:sea-bass" in resolved.gates
    assert resolved.needs_human is True


def test_resolve_order_unmatched_item_escalates_instead_of_guessing():
    extracted = {"is_order": True, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "", "raw_text": "a chicken caesar wrap", "quantity": 1}]}
    resolved = _resolve(extracted)
    assert resolved.lines == []
    assert any(g.startswith("unmatched_item:") for g in resolved.gates)
    assert resolved.needs_human is True


def test_resolve_order_unsupported_language_falls_back_to_default_and_escalates():
    extracted = {"is_order": True, "language": "sv", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "sparkling-water", "raw_text": "vatten", "quantity": 1}]}
    resolved = _resolve(extracted)
    assert resolved.language_supported is False
    assert resolved.reply_language == "en"
    assert "language_unsupported:sv" in resolved.gates
    assert resolved.needs_human is True


def test_resolve_order_low_confidence_escalates_even_when_everything_else_is_clean():
    extracted = {"is_order": True, "language": "en", "confidence": 0.2, "dietary_note": "",
                "items": [{"slug": "sparkling-water", "raw_text": "water", "quantity": 1}]}
    resolved = _resolve(extracted)
    assert "low_confidence" in resolved.gates
    assert resolved.needs_human is True


def test_resolve_order_not_an_order_needs_a_human_and_prices_nothing():
    extracted = {"is_order": False, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": []}
    resolved = _resolve(extracted)
    assert resolved.gates == ["not_recognized_as_order"]
    assert resolved.lines == []
    assert resolved.total == 0


def test_resolve_order_dessert_already_in_cart_never_gets_a_second_upsell_offer():
    extracted = {"is_order": True, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "fondant", "raw_text": "the fondant", "quantity": 1}]}
    resolved = _resolve(extracted)
    assert resolved.upsell is None


def test_resolve_order_upsell_off_never_offers_regardless_of_cart():
    extracted = {"is_order": True, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "club-sandwich", "raw_text": "sandwich", "quantity": 1}]}
    resolved = _resolve(extracted, upsell_enabled=False)
    assert resolved.upsell is None


def test_resolve_order_never_quotes_a_made_up_attach_rate():
    extracted = {"is_order": True, "language": "en", "confidence": 0.9, "dietary_note": "",
                "items": [{"slug": "club-sandwich", "raw_text": "sandwich", "quantity": 1}]}
    without_data = _resolve(extracted, historic_attach_rate=None)
    assert "%" not in without_data.upsell.reason
    with_data = _resolve(extracted, historic_attach_rate=42.0)
    assert "42%" in with_data.upsell.reason
