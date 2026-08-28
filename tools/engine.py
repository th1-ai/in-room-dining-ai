"""tools/engine.py - In-Room Dining AI's order-taking loop: extract, resolve,
draft, queue.

Deterministic decisioning, LLM for language: the only two model calls are
"extract_order" and "draft_confirmation" (core.llm.complete, always with a
JSON schema). Whether an order needs a human is a plain rule over the
deterministic resolution (tools/menu_engine.py:resolve_order), not something
either model call decides - see docs/how-it-works.md and docs/safety.md.

Shared by tools/run.py (the real loop) and tools/demo.py (the zero-credential
walkthrough), so both exercise exactly the same code path.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.adapters import get_messaging
from core.adapters.base import AdapterError, ChatMessage
from core.config import Settings
from core.llm import LLMResult, LLMSchemaError, complete
from core.store import Item, Store
from core.templates import build_prompt

from tools import menu_engine
from tools.menu_engine import ResolvedOrder, load_menu, resolve_order

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "schemas"
RULE_UPSELL = "upsell"

#: EU AI Act Article 50: a guest must be told they are talking to an AI
#: system. Every messaging adapter's own `send()` already appends the real
#: disclosure line via `core.adapters.base.Messaging.with_disclosure()`
#: (reading `knowledge/disclosure.md`) - that is the path this agent's
#: messages actually take, see docs/safety.md "Telling guests they are
#: talking to AI". This constant is the fallback for a hotel that has not
#: filled that file in yet: identical wording to the shipped
#: `knowledge/disclosure.example.md`, so the two never disagree. It is
#: never left out - see `_disclosed()` below.
DEFAULT_DISCLOSURE = (
    "This message was drafted with AI assistance and checked by our team; "
    "reply and a person will help you."
)


def _disclosed(settings: Settings, guest_message: str) -> str:
    """``guest_message`` plus the AI-disclosure line, applied once, here -
    before the draft ever reaches the review queue, so the person approving
    it sees exactly what the guest will get, not something appended
    invisibly at send time.

    Uses the hotel's own `knowledge/disclosure.md` if they have filled it
    in (`Messaging.disclosure()`); otherwise falls back to
    `DEFAULT_DISCLOSURE` so a confirmation is never shipped without one -
    the gap `factory/workflows/simulate-onboarding.md` found (SIMULATION.md
    finding 1). Idempotent: a messaging adapter's own `send()` calls
    `with_disclosure()` again on the way out, and that is a no-op once the
    line (whichever one) is already present.
    """
    if not guest_message:
        return guest_message
    messaging = get_messaging(settings)
    line = messaging.disclosure() or DEFAULT_DISCLOSURE
    if line in guest_message:
        return guest_message
    return guest_message.rstrip() + "\n\n" + line


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{name}.json").read_text(encoding="utf-8"))


EXTRACT_SCHEMA = _schema("extract_order")
DRAFT_SCHEMA = _schema("draft_confirmation")


def message_to_dict(msg: ChatMessage) -> dict:
    """The fields the prompts and the store need from an inbound message."""
    extra = msg.extra or {}
    return {
        "id": msg.id, "chat_id": msg.chat_id, "from_number": msg.from_number,
        "from_name": msg.from_name, "text": msg.text, "sent_at": msg.sent_at,
        "room_number": str(extra.get("room_number") or "").strip(),
        "location": str(extra.get("location") or "").strip(),
    }


# ---------------------------------------------------------------------------
# the upsell rule - live state in data/agent.db, seeded from config once.
# Mirrors Table / Floor Management AI's dining_rules
# (docs/how-it-works.md "Design decisions" #9).
# ---------------------------------------------------------------------------
def get_upsell_enabled(store: Store, settings: Settings) -> bool:
    val = store.get(f"rule:{RULE_UPSELL}")
    if val is None:
        val = bool(settings.agent_get("upsell.enabled", True))
        store.set(f"rule:{RULE_UPSELL}", val)
    return bool(val)


def set_upsell_enabled(store: Store, enabled: bool) -> bool:
    store.set(f"rule:{RULE_UPSELL}", bool(enabled))
    return bool(enabled)


def _peek_upsell_enabled(store: Store, settings: Settings) -> bool:
    """Read-only version of :func:`get_upsell_enabled` - never seeds the kv
    row. Used by :func:`resolve_preview` so ``--dry-run`` truly writes
    nothing, not even the first-run seed value."""
    val = store.get(f"rule:{RULE_UPSELL}")
    if val is None:
        return bool(settings.agent_get("upsell.enabled", True))
    return bool(val)


# ---------------------------------------------------------------------------
# PMS identity/charge target - docs/how-it-works.md "Design decisions" #6
# ---------------------------------------------------------------------------
def resolve_room(pms, room_number: str, *, today: str) -> tuple[str, str | None]:
    """``(guest_name, reservation_id)`` for a room, or ``("Guest", None)`` if
    the PMS cannot confirm one. Two lookups, in order:

    1. ``pms.get_reservation(room_number)`` - works out of the box with the
       bundled fixtures, which key reservations by room number precisely so
       this never depends on the current date.
    2. ``pms.list_in_house(today)`` filtered to ``room_id == room_number`` -
       what a real PMS (csv/cloudbeds) needs, using the actual date at call
       time. Never raises: an adapter error here means "could not confirm",
       not "crash the run" - the order still queues, just without a
       reservation id to post the folio note against.
    """
    if not room_number:
        return "Guest", None
    try:
        res = pms.get_reservation(room_number)
        if res is not None and res.id:
            return (res.guest.full_name or "Guest"), res.id
    except AdapterError:
        pass
    try:
        for res in pms.list_in_house(today):
            if res.room_id == room_number:
                return (res.guest.full_name or "Guest"), res.id
    except AdapterError:
        pass
    return "Guest", None


# ---------------------------------------------------------------------------
# store-derived numbers the pure engine needs but cannot compute itself
# ---------------------------------------------------------------------------
def order_snapshot(store: Store, *, upsell_target_slug: str,
                   min_orders_for_stat: int) -> tuple[int, float | None]:
    """``(active_tickets, historic_attach_rate)`` from ``data/agent.db``.

    ``active_tickets`` counts orders currently ``placed``/``preparing``
    (tools/menu_engine.py:kitchen_eta's input). ``historic_attach_rate`` is
    the real percentage of completed orders that included the upsell target
    - ``None`` until at least ``min_orders_for_stat`` orders have gone out,
    so the confirmation never quotes a made-up figure
    (docs/how-it-works.md "Design decisions" #8).
    """
    active = 0
    completed = 0
    attached = 0
    for item in store.list_items(kind="order", limit=5000):
        payload = item.payload or {}
        if payload.get("_status") in ("placed", "preparing"):
            active += 1
        if item.review_status in ("sent", "auto_sent"):
            resolved = payload.get("_resolved") or {}
            lines = resolved.get("lines") or []
            if lines:
                completed += 1
                if any(line.get("slug") == upsell_target_slug for line in lines):
                    attached += 1
    if completed < max(1, min_orders_for_stat):
        return active, None
    return active, round(100 * attached / completed, 1)


# ---------------------------------------------------------------------------
# the two LLM stages
# ---------------------------------------------------------------------------
def extract(settings: Settings, store: Store, item: Item, msg: ChatMessage,
           *, provider: str | None = None) -> dict:
    """Run extract_order and record the result on the item. Returns the data."""
    menu = load_menu(settings.agent_get("menu", []))
    menu_text = menu_engine.format_menu_for_prompt(menu, settings.hotel.currency)
    prompt = build_prompt("extract_order", settings=settings, item=message_to_dict(msg),
                          fixture_id=msg.id, menu_text=menu_text)
    result: LLMResult = complete("extract_order", prompt, EXTRACT_SCHEMA, settings=settings,
                                 provider=provider, store=store, item_id=item.id,
                                 fixture_id=msg.id)
    data = result.data or {}
    intent = "order" if data.get("is_order") else "not_order"
    store.set_fields(item.id, intent=intent, confidence=float(data.get("confidence", 0.0)))
    return data


def draft(settings: Settings, store: Store, item: Item, resolved: ResolvedOrder,
         *, provider: str | None = None) -> dict:
    """Run draft_confirmation and record the result on the item. Returns the data."""
    prompt = build_prompt("draft_confirmation", settings=settings, item=resolved.as_dict(),
                          fixture_id=item.external_id)
    result: LLMResult = complete("draft_confirmation", prompt, DRAFT_SCHEMA, settings=settings,
                                 provider=provider, store=store, item_id=item.id,
                                 fixture_id=item.external_id)
    data = result.data or {}
    guest_message = str(data.get("guest_message") or "")
    if guest_message:
        data = {**data, "guest_message": _disclosed(settings, guest_message)}
    store.set_fields(item.id, draft=data)
    return data


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def process_order(settings: Settings, store: Store, pms, msg: ChatMessage, *,
                  provider: str | None = None, today: str) -> tuple[Item, bool]:
    """Extract + resolve + draft one inbound message and queue it for review.

    Idempotent: an item that already has an intent AND a draft was handled by
    an earlier pass and is left untouched (returns ``(item, False)``). An item
    with an intent but no draft is resumed at the draft stage - see
    docs/how-it-works.md "Idempotency" and the retry regression test in
    tests/test_ird_engine.py.
    """
    payload = message_to_dict(msg)
    existing = store.get_by_external("messaging", msg.id)
    if existing is not None and "_extract_cache" in (existing.payload or {}):
        payload["_extract_cache"] = existing.payload["_extract_cache"]
    item = store.upsert_item("messaging", msg.id, kind="order", payload=payload)
    if item.intent and item.draft is not None:
        return item, False

    extracted = (item.payload or {}).get("_extract_cache")
    if not extracted:
        try:
            extracted = extract(settings, store, item, msg, provider=provider)
        except LLMSchemaError as exc:
            store.set_fields(item.id, error=str(exc))
            updated = store.transition(item.id, "needs_human", actor="agent",
                                       detail={"error": "extract_schema_error"})
            return updated, True
        item = store.set_fields(
            item.id, payload={**(item.payload or {}), "_extract_cache": extracted}) or item

    menu = load_menu(settings.agent_get("menu", []))
    kitchen_cfg = settings.agent_get("kitchen", {}) or {}
    upsell_cfg = settings.agent_get("upsell", {}) or {}
    active_tickets, attach_rate = order_snapshot(
        store, upsell_target_slug=str(upsell_cfg.get("target_slug") or ""),
        min_orders_for_stat=int(upsell_cfg.get("min_orders_for_stat", 5)))

    guest_name, reservation_id = resolve_room(pms, payload["room_number"], today=today)
    resolved = resolve_order(
        extracted, menu=menu, currency=settings.hotel.currency,
        hotel_languages=settings.hotel.languages,
        default_language=settings.hotel.default_language, kitchen_cfg=kitchen_cfg,
        upsell_enabled=get_upsell_enabled(store, settings), upsell_cfg=upsell_cfg,
        active_tickets=active_tickets, historic_attach_rate=attach_rate,
        confidence_threshold=float(settings.agent_get("confidence_threshold", 0.55)),
        room_number=payload["room_number"], location=payload["location"])
    resolved_dict = resolved.as_dict()
    resolved_dict["guest_name"] = guest_name
    resolved_dict["reservation_id"] = reservation_id
    item = store.set_fields(
        item.id, payload={**(item.payload or {}), "_resolved": resolved_dict}) or item

    if not resolved.is_order:
        store.set_fields(item.id, draft={"guest_message": "", "kitchen_ticket_line": "",
                                         "note": "not recognised as an order; a person "
                                                 "should reply directly"})
        updated = store.transition(item.id, "needs_human", actor="agent",
                                   detail={"reason": "not_recognized_as_order"})
        return updated, True

    try:
        draft(settings, store, item, resolved, provider=provider)
    except LLMSchemaError as exc:
        store.set_fields(item.id, error=str(exc))
        updated = store.transition(item.id, "needs_human", actor="agent",
                                   detail={"error": "draft_schema_error"})
        return updated, True

    if item.payload.get("_order_ref") is None:
        # Underscore-prefixed: `upsert_item`'s payload refresh (message_to_dict
        # is rebuilt from the raw message on every call) only preserves keys
        # starting with "_" - a bare "order_ref" would be silently dropped the
        # moment this item is re-fetched, and a retry would look un-refed.
        ref_n = store.next_sequence("order_ref", dry_run=settings.dry_run)
        item = store.set_fields(
            item.id, payload={**item.payload, "_order_ref": f"RS-{ref_n:04d}"}) or item

    status = "needs_human" if resolved.needs_human else "pending_review"
    updated = store.transition(item.id, status, actor="agent",
                               detail={"gates": resolved.gates, "total": resolved.total})
    return updated, True


def resolve_preview(settings: Settings, store: Store, pms, msg: ChatMessage, *,
                    provider: str | None = None, today: str) -> ResolvedOrder:
    """Compute (never persist) one message's resolved order - what
    `tools/run.py --dry-run` shows. No item row, no cache, no order
    reference, no ``runs``/``events`` row: `--dry-run` writes nothing, not
    even to the database (factory/workflows/build-repo.md, "--dry-run writes
    nothing"). Only the cheaper `extract_order` call runs; the confirmation
    is not drafted for a preview that nothing will ever send.
    """
    payload = message_to_dict(msg)
    menu = load_menu(settings.agent_get("menu", []))
    menu_text = menu_engine.format_menu_for_prompt(menu, settings.hotel.currency)
    prompt = build_prompt("extract_order", settings=settings, item=payload,
                          fixture_id=msg.id, menu_text=menu_text)
    result: LLMResult = complete("extract_order", prompt, EXTRACT_SCHEMA, settings=settings,
                                 provider=provider, store=None, item_id=None,
                                 fixture_id=msg.id)
    extracted = result.data or {}

    kitchen_cfg = settings.agent_get("kitchen", {}) or {}
    upsell_cfg = settings.agent_get("upsell", {}) or {}
    active_tickets, attach_rate = order_snapshot(
        store, upsell_target_slug=str(upsell_cfg.get("target_slug") or ""),
        min_orders_for_stat=int(upsell_cfg.get("min_orders_for_stat", 5)))
    guest_name, reservation_id = resolve_room(pms, payload["room_number"], today=today)
    resolved = resolve_order(
        extracted, menu=menu, currency=settings.hotel.currency,
        hotel_languages=settings.hotel.languages,
        default_language=settings.hotel.default_language, kitchen_cfg=kitchen_cfg,
        upsell_enabled=_peek_upsell_enabled(store, settings), upsell_cfg=upsell_cfg,
        active_tickets=active_tickets, historic_attach_rate=attach_rate,
        confidence_threshold=float(settings.agent_get("confidence_threshold", 0.55)),
        room_number=payload["room_number"], location=payload["location"])
    # guest_name/reservation_id are informational only here - nothing is
    # stored for a preview, so there is no payload dict to attach them to.
    return resolved
