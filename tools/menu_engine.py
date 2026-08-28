"""tools/menu_engine.py - In-Room Dining AI's decision engine. Pure functions.

PURE: no I/O, no clock, no randomness. Feed :func:`resolve_order` the same
menu, extraction result and kitchen state and it returns the same answer every
time, and every number in the result traces back to a value you gave it -
prices come from the menu you loaded, never from the model.

Deterministic decisioning, LLM for language (ARCHITECTURE.md section 1): the
only two model calls in this agent are "extract the order" and "draft the
confirmation" (tools/engine.py). Whether an item is on the menu, whether it is
sold out, what the kitchen ETA is, and whether a dietary note needs a person -
none of that is left to the model. See docs/how-it-works.md "Design decisions".
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

STATUS_FLOW = ("placed", "preparing", "on the way", "delivered")


def _fold(text: str) -> str:
    """Casefold + strip accents, so "Alérgico", "ALERGIA" and "alergia" all
    compare equal. Every dietary/allergen keyword match in this module goes
    through this first - a hotel's own languages are not always ASCII."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold().strip()


# ---------------------------------------------------------------------------
# allergen keywords - en es fr de it pt el nl, accent-folded, substring match
# against the folded dietary text. Mirrors the pattern proven in
# Table / Floor Management AI's seating_engine.py (has_nut_allergy /
# has_allergy_signal), extended from "nut" to the common named allergens.
#
# Coverage is deliberately these eight languages, not "whatever hotel.
# languages lists" - README "Adding a language" / docs/safety.md say so
# explicitly. A note in any other language still escalates
# (detect_dietary() never returns "none" for a non-empty note) but only as
# the generic `dietary_signal:noted`, not a named allergen family - a
# reviewer has to open the item to see what it actually says.
#
# Greek stems are written already accent-folded and casefolded, matching
# what `_fold()` produces - note Python's casefold() maps the Greek final
# sigma "ς" to plain sigma "σ", so every Greek stem below ends "σ", never
# "ς" (verified against `_fold("ξηρούς καρπούς")`, the "nut" test case).
# ---------------------------------------------------------------------------
_ALLERGEN_STEMS: dict[str, set[str]] = {
    "nut": {
        "nut", "frutos secos", "fruto seco", "nuez", "nueces", "mani",
        "cacahuete", "cacahuates", "fruits a coque", "fruit a coque", "noix",
        "arachide", "cacahouete", "nuss", "noci", "nocciola", "nocciole",
        "arachidi", "noz", "nozes", "amendoim",
        # el - "ξηροί καρποί" ("dry fruits") is the everyday collective term
        # for nuts, in the three cases a note is likely to use, plus the
        # common specific nuts.
        "ξηρουσ καρπουσ", "ξηροι καρποι", "ξηρων καρπων", "φιστικ",
        "αμυγδαλ", "καρυδ", "φουντουκ",
        # nl - "noten" (plural) rather than the bare singular "noot", which
        # collides with the Dutch word for a musical note.
        "noten", "pinda", "amandel", "walnoot", "hazelnoot", "cashewnoot",
    },
    "gluten": {
        "gluten", "glutine", "celiac", "coeliac", "celiaco", "celiaca",
        "celiaque", "coeliaque", "celiachia", "zoliakie", "weizen", "trigo",
        "ble sans", "sans gluten", "wheat allergy",
        # el
        "γλουτεν", "κοιλιοκακ",
        # nl - "gluten"/"glutenvrij"/"glutenallergie" already match the
        # bare "gluten" stem above; "coeliakie" needs its own.
        "coeliak",
    },
    "dairy": {
        "lactose", "lactosa", "laktose", "lattosio", "dairy", "leite",
        "leche", "lait", "milch", "latte",
        # el
        "λακτοζ", "γαλα", "γαλακτοκομικ",
        # nl
        "melk", "zuivel",
    },
    "shellfish": {
        "shellfish", "crustacean", "shrimp", "prawn", "marisco", "mariscos",
        "crustaceo", "crustace", "fruits de mer", "schalentier",
        "krustentier", "crostacei", "frutti di mare",
        # el
        "θαλασσιν", "οστρακοειδ", "γαριδ", "καβουρ",
        # nl
        "schaaldier", "schelpdier", "garna", "kreeft", "mossel",
    },
    "egg": {
        "egg allergy", "egg-free", "huevo", "oeuf", "eiallergie",
        "allergie gegen ei", "uovo", "uova", "ovo", "ovos",
        # el - "αυγο"/"αυγα" (singular/plural of "egg") are short enough to
        # theoretically collide with "Αύγουστος" (August), which this
        # module accepts the same way "nut" accepts the bare English word:
        # a false positive here only ever costs one extra human glance,
        # never a silently dropped allergy.
        "αυγο", "αυγα", "ωοαλλεργ",
        # nl - "eiallergie" is already covered by the German entry above
        # (identical compound word); these add the phrasal forms.
        "eierallergie", "allergisch voor ei", "allergie voor ei",
    },
    # Deliberately no bare "soy": it collides with the Spanish word "soy"
    # ("I am"), which would false-flag ordinary Spanish sentences like "Soy
    # alergico a los mariscos" as a soy allergy. A note that says "no soy"
    # without a qualifying word still escalates (kind="unclassified", never
    # "none") - it just is not labelled as the soy family specifically.
    # "soja" already covers Dutch (identical spelling to Spanish/Portuguese).
    "soy": {"soy allergy", "soy-free", "soya", "soja", "soia", "σογια"},
}

#: a word from any of these families means "this note names SOME allergy"
#: even when it did not match a specific stem above - the common,
#: accent-folded substring across allergy/allergie/alergia/allergisch/
#: allergico/anafilassi/intolleranza/αλλεργία/αναφυλαξία/δυσανεξία etc.
#: Never dropped silently - see detect_dietary().
_ALLERGY_SIGNAL_STEMS = (
    "lerg", "anafil", "anaphyla", "anafyla", "intoleran",
    "αλλεργ", "αναφυλ", "δυσανεξ",
)


@dataclass
class DietaryFinding:
    """The result of reading one guest dietary/special-request note.

    ``kind`` is one of:

    - ``"none"``: no note at all.
    - ``"named"``: matched a specific allergen family (``allergens``).
    - ``"ambiguous"``: matched a generic allergy-signal word (allergy/
      allergie/alergia/...) but no specific family - this repo does not
      know what the allergen is and never guesses.
    - ``"unclassified"``: the guest flagged something dietary (a special
      request, a preference, an allergy word this module has no stem for)
      that matched neither a named allergen nor a generic signal word.

    Every kind except ``"none"`` escalates to ``needs_human`` - see
    docs/safety.md. The distinction only changes what the reviewer and the
    kitchen ticket are told, never whether a person sees it first.
    """

    kind: str = "none"
    allergens: list[str] = field(default_factory=list)
    note: str = ""


def detect_dietary(note: str) -> DietaryFinding:
    """Classify one dietary/special-request note. Never returns "none" for a
    non-empty note - see docs/how-it-works.md "Design decisions" #3."""
    note = (note or "").strip()
    if not note:
        return DietaryFinding(kind="none", note="")
    folded = _fold(note)
    matched = sorted(
        allergen for allergen, stems in _ALLERGEN_STEMS.items()
        if any(stem in folded for stem in stems)
    )
    if matched:
        return DietaryFinding(kind="named", allergens=matched, note=note)
    if any(stem in folded for stem in _ALLERGY_SIGNAL_STEMS):
        return DietaryFinding(kind="ambiguous", note=note)
    return DietaryFinding(kind="unclassified", note=note)


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------
@dataclass
class MenuItem:
    """One row of ``config/agent.yaml: menu:``."""

    slug: str
    name: str
    category: str
    price: float
    description: str = ""


def load_menu(raw: list[dict] | None) -> list[MenuItem]:
    return [
        MenuItem(slug=str(r.get("slug", "")).strip(), name=str(r.get("name", "")),
                 category=str(r.get("category", "")), price=float(r.get("price", 0) or 0),
                 description=str(r.get("description", "")))
        for r in (raw or []) if r.get("slug")
    ]


def menu_by_slug(menu: list[MenuItem]) -> dict[str, MenuItem]:
    return {item.slug: item for item in menu}


def money(amount: float, currency: str = "EUR") -> str:
    """Every human-facing amount in this agent goes through this - never a
    hardcoded EUR/USD (factory/workflows/build-repo.md, "Money strings")."""
    return f"{currency} {amount:,.0f}"


def format_menu_for_prompt(menu: list[MenuItem], currency: str) -> str:
    """The stable menu block every extraction prompt is grounded on."""
    by_category: dict[str, list[MenuItem]] = {}
    for item in menu:
        by_category.setdefault(item.category, []).append(item)
    lines = []
    for category, items in by_category.items():
        lines.append(f"### {category}")
        for item in items:
            desc = f" - {item.description}" if item.description else ""
            lines.append(f"- {item.slug}: {item.name}, {money(item.price, currency)}{desc}")
    return "\n".join(lines)


def resolve_menu_item(raw: dict, by_slug: dict[str, MenuItem]) -> MenuItem | None:
    """Match one extracted line to a real menu item. The model's ``slug`` is a
    proposal, not a fact: an exact hit against the real menu wins; otherwise a
    folded substring match against the item's own (multi-word) name; a slug
    the model invented is never priced on trust (docs/how-it-works.md
    "Design decisions" #1).

    Deliberately does NOT fall back to matching a bare slug word (e.g.
    "caesar") against the raw text: a single generic word is too likely to
    appear inside an unrelated request ("chicken caesar wrap" is not this
    hotel's Caesar salad) and a false match would price and ticket the wrong
    dish. An item that only a slug word would catch is correctly left
    unmatched and escalated - see docs/how-it-works.md "Design decisions" #1
    and the "unmatched item" fixture in fixtures/inbound/messages.json.
    """
    slug = str(raw.get("slug") or "").strip()
    if slug and slug in by_slug:
        return by_slug[slug]
    raw_text = _fold(str(raw.get("raw_text") or ""))
    if not raw_text:
        return None
    for item in by_slug.values():
        name_folded = _fold(item.name)
        if name_folded and (name_folded in raw_text or raw_text in name_folded):
            return item
    return None


# ---------------------------------------------------------------------------
# kitchen capacity - docs/how-it-works.md "Design decisions" #2
# ---------------------------------------------------------------------------
def kitchen_eta(active_tickets: int, kitchen_cfg: dict) -> int:
    """The ETA quoted to the guest, grown once the kitchen is busier than
    ``capacity_before_delay`` tickets currently in flight (``placed`` or
    ``preparing``), capped at ``max_eta_minutes``. ``active_tickets`` is
    counted by the caller (tools/engine.py) - this function has no I/O."""
    base = int(kitchen_cfg.get("eta_base_minutes", 35))
    capacity = int(kitchen_cfg.get("capacity_before_delay", 4))
    step = int(kitchen_cfg.get("delay_step_minutes", 10))
    max_eta = int(kitchen_cfg.get("max_eta_minutes", 75))
    over = max(0, int(active_tickets) - capacity)
    return min(max_eta, base + over * step)


# ---------------------------------------------------------------------------
# the resolved order
# ---------------------------------------------------------------------------
@dataclass
class OrderLine:
    """One priced, menu-matched line of the order."""

    slug: str
    name: str
    category: str
    quantity: int
    unit_price: float
    line_total: float
    raw_text: str = ""


@dataclass
class UpsellOffer:
    slug: str
    name: str
    price: float
    reason: str


@dataclass
class ResolvedOrder:
    """Everything downstream of the extraction step needs, in one object:
    the reviewer (``tools/review.py show``), the confirmation prompt
    (``tools/engine.py``), and the kitchen ticket (``tools/menu_engine.py
    kitchen_ticket_text``)."""

    room_number: str
    location: str
    lines: list[OrderLine]
    unmatched: list[str]
    unavailable: list[MenuItem]
    subtotal: float
    tray_charge: float
    total: float
    currency: str
    language: str
    language_supported: bool
    reply_language: str
    dietary: DietaryFinding
    eta_minutes: int
    upsell: UpsellOffer | None
    is_order: bool
    confidence: float
    gates: list[str]

    @property
    def needs_human(self) -> bool:
        return bool(self.gates)

    def as_dict(self) -> dict:
        return {
            "room_number": self.room_number, "location": self.location,
            "lines": [vars(l) for l in self.lines], "unmatched": self.unmatched,
            "unavailable": [i.slug for i in self.unavailable],
            "subtotal": self.subtotal, "tray_charge": self.tray_charge,
            "total": self.total, "currency": self.currency, "language": self.language,
            "language_supported": self.language_supported,
            "reply_language": self.reply_language,
            "dietary": {"kind": self.dietary.kind, "allergens": self.dietary.allergens,
                       "note": self.dietary.note},
            "eta_minutes": self.eta_minutes,
            "upsell": (vars(self.upsell) if self.upsell else None),
            "is_order": self.is_order, "confidence": self.confidence,
            "gates": self.gates, "needs_human": self.needs_human,
        }


def resolve_order(extracted: dict, *, menu: list[MenuItem], currency: str,
                  hotel_languages: list[str], default_language: str, kitchen_cfg: dict,
                  upsell_enabled: bool, upsell_cfg: dict, active_tickets: int,
                  historic_attach_rate: float | None, confidence_threshold: float,
                  room_number: str, location: str) -> ResolvedOrder:
    """Turn one ``extract_order`` answer into a priced, gated order. Pure -
    every input is a value, nothing is read from disk or the clock here."""
    by_slug = menu_by_slug(menu)
    language = str(extracted.get("language") or "en").strip().lower()[:2] or "en"
    language_supported = language in [str(l).lower() for l in hotel_languages]
    reply_language = language if language_supported else default_language
    is_order = bool(extracted.get("is_order"))
    confidence = float(extracted.get("confidence") or 0.0)
    dietary = detect_dietary(str(extracted.get("dietary_note") or ""))
    sold_out = {str(s).strip() for s in (kitchen_cfg.get("sold_out") or [])}

    gates: list[str] = []
    lines: list[OrderLine] = []
    unmatched: list[str] = []
    unavailable: list[MenuItem] = []

    if not is_order:
        gates.append("not_recognized_as_order")
    else:
        for raw in extracted.get("items") or []:
            item = resolve_menu_item(raw, by_slug)
            qty = max(1, int(raw.get("quantity") or 1))
            if item is None:
                label = str(raw.get("raw_text") or raw.get("slug") or "").strip()
                unmatched.append(label or "(unspecified item)")
                continue
            if item.slug in sold_out:
                unavailable.append(item)
                continue
            lines.append(OrderLine(slug=item.slug, name=item.name, category=item.category,
                                   quantity=qty, unit_price=item.price,
                                   line_total=round(item.price * qty, 2),
                                   raw_text=str(raw.get("raw_text") or "")))
        if not lines and not unmatched and not unavailable:
            gates.append("no_items_resolved")

    for label in unmatched:
        gates.append(f"unmatched_item:{label}")
    for item in unavailable:
        gates.append(f"sold_out:{item.slug}")
    if dietary.kind == "named":
        gates += [f"dietary_signal:named:{a}" for a in dietary.allergens]
    elif dietary.kind == "ambiguous":
        gates.append("dietary_signal:ambiguous")
    elif dietary.kind == "unclassified":
        gates.append("dietary_signal:noted")
    if is_order and not language_supported:
        gates.append(f"language_unsupported:{language}")
    if is_order and confidence < confidence_threshold:
        gates.append("low_confidence")

    subtotal = round(sum(l.line_total for l in lines), 2)
    tray_charge = float(kitchen_cfg.get("tray_charge", 8)) if lines else 0.0
    total = round(subtotal + tray_charge, 2)
    eta_minutes = kitchen_eta(active_tickets, kitchen_cfg) if lines else 0

    upsell = None
    if upsell_enabled and lines:
        target = by_slug.get(str(upsell_cfg.get("target_slug") or ""))
        category = str(upsell_cfg.get("category") or "")
        already_has = any(l.category == category for l in lines)
        if target is not None and not already_has and target.slug not in sold_out:
            if historic_attach_rate is not None:
                reason = (f"a popular pairing - about {historic_attach_rate:.0f}% of "
                         "recent orders added one")
            else:
                reason = "a popular pairing with your order"
            upsell = UpsellOffer(slug=target.slug, name=target.name, price=target.price,
                                 reason=reason)

    return ResolvedOrder(
        room_number=room_number, location=location, lines=lines, unmatched=unmatched,
        unavailable=unavailable, subtotal=subtotal, tray_charge=tray_charge, total=total,
        currency=currency, language=language, language_supported=language_supported,
        reply_language=reply_language, dietary=dietary, eta_minutes=eta_minutes,
        upsell=upsell, is_order=is_order, confidence=confidence, gates=gates)


# ---------------------------------------------------------------------------
# kitchen status flow
# ---------------------------------------------------------------------------
def next_status(status: str | None) -> str | None:
    """The next step in ``STATUS_FLOW``, or ``None`` at ``delivered``. An
    unrecognised status is treated as "not started yet" and returns the
    first step, never raises."""
    if status not in STATUS_FLOW:
        return STATUS_FLOW[0]
    idx = STATUS_FLOW.index(status)
    return STATUS_FLOW[idx + 1] if idx + 1 < len(STATUS_FLOW) else None


_STATUS_COPY: dict[str, dict[str, str]] = {
    "placed": {
        "en": "The kitchen has your ticket.", "es": "La cocina ya tiene su pedido.",
        "fr": "La cuisine a votre commande.", "de": "Die Küche hat Ihre Bestellung.",
        "it": "La cucina ha il tuo ordine.", "pt": "A cozinha já tem o seu pedido.",
    },
    "preparing": {
        "en": "Being cooked now.", "es": "Se está preparando.",
        "fr": "En cours de préparation.", "de": "Wird jetzt zubereitet.",
        "it": "In preparazione.", "pt": "A ser preparado agora.",
    },
    "on the way": {
        "en": "On the tray, heading up.", "es": "En la bandeja, ya sube.",
        "fr": "Sur le plateau, ça arrive.", "de": "Auf dem Tablett, ist unterwegs.",
        "it": "Sul vassoio, sta arrivando.", "pt": "Na bandeja, a caminho.",
    },
    "delivered": {
        "en": "Delivered. Enjoy.", "es": "Entregado. Que lo disfrute.",
        "fr": "Livré. Bon appétit.", "de": "Geliefert. Guten Appetit.",
        "it": "Consegnato. Buon appetito.", "pt": "Entregue. Bom apetite.",
    },
}


def guest_status_copy(status: str, lang: str) -> str:
    """One short status line for a guest push, in ``lang`` when this module
    has it, else English. Never raises on an unknown status or language."""
    row = _STATUS_COPY.get(status, {})
    return row.get(lang) or row.get("en") or ""


def kitchen_ticket_text(*, ref: str, room_number: str, location: str, guest_name: str,
                        lines: list[OrderLine], total: float, currency: str,
                        dietary_ticket_line: str) -> str:
    """The kitchen-facing ticket text - fully deterministic, no model
    involved. ``dietary_ticket_line`` is the one piece of model-written text
    in it (``prompts/draft_confirmation.md``), and is appended verbatim, in
    English, as its own headline line."""
    item_bits = ", ".join(
        f"{line.name} x{line.quantity}" if line.quantity > 1 else line.name for line in lines)
    parts = [f"Ticket {ref} - {location or ('Room ' + room_number)} - {guest_name}",
             item_bits or "(no matched items)", f"Total {money(total, currency)}"]
    if dietary_ticket_line:
        parts.append(f"DIETARY: {dietary_ticket_line}")
    return "\n".join(parts)
