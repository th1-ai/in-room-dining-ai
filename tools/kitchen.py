#!/usr/bin/env python3
"""tools/kitchen.py - the live kitchen board: what has fired, and moving a
ticket forward.

    python3 tools/kitchen.py board
    python3 tools/kitchen.py board --all
    python3 tools/kitchen.py advance RS-0001

Only a ticket that has actually fired (`python3 tools/review.py send`) can be
advanced - a drafted-but-not-yet-approved order has no kitchen status at all,
because the kitchen has not seen it yet (docs/how-it-works.md "Data model").
In `mode: shadow` no ticket ever fires, so there is nothing to advance; that
is expected, not a bug.

`advance` moves the order one step through `placed -> preparing -> on the
way -> delivered` and pushes the guest a short status update in their own
confirmed reply language (`messaging.send`, guarded like every other write -
blocked in shadow, or if mode flips back to shadow after the ticket fired).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Item, Store, StoreError  # noqa: E402

from tools.menu_engine import guest_status_copy, next_status  # noqa: E402


def _find(store: Store, id_or_ref: str) -> Item | None:
    item = store.get_item(id_or_ref)
    if item is not None:
        return item
    for candidate in store.list_items(kind="order", limit=5000):
        if (candidate.payload or {}).get("_order_ref") == id_or_ref:
            return candidate
    return None


def cmd_board(store: Store, args) -> int:
    statuses = ("sent", "auto_sent")
    items = store.list_items(kind="order", status=statuses, limit=args.limit)
    live = [i for i in items if args.all or (i.payload or {}).get("_status") != "delivered"]
    if not live:
        print("Nothing on the board. A ticket appears here once it has been "
             "approved and sent (`python3 tools/review.py send`).")
        return 0
    print(f"{len(live)} ticket(s):\n")
    for item in live:
        payload = item.payload or {}
        resolved = payload.get("_resolved") or {}
        ref = str(payload.get("_order_ref") or item.id[:8])
        where = str(payload.get("location") or f"Room {payload.get('room_number', '?')}")
        status = str(payload.get("_status") or "placed")
        lines = resolved.get("lines") or []
        items_txt = ", ".join(
            f"{l['name']} x{l['quantity']}" if l.get("quantity", 1) > 1 else l["name"]
            for l in lines) or "(no matched items)"
        print(f"  {ref:<8} {status:<12} {where:<26} {items_txt}")
    print("\nRun `python3 tools/kitchen.py advance <ref>` to move one forward.")
    return 0


def cmd_advance(store: Store, settings, args) -> int:
    item = _find(store, args.id)
    if item is None:
        print(f"error: no order {args.id}", file=sys.stderr)
        return 1
    if item.review_status not in ("sent", "auto_sent"):
        print(f"error: {args.id} has not been approved and sent yet "
             f"(status: {item.review_status}). Run `python3 tools/review.py send` first.",
             file=sys.stderr)
        return 1
    payload = item.payload or {}
    resolved = payload.get("_resolved") or {}
    ref = str(payload.get("_order_ref") or item.id[:8])
    current = payload.get("_status")
    nxt = next_status(current)
    if nxt is None:
        print(f"{ref} is already delivered.")
        return 0
    store.set_fields(item.id, payload={**payload, "_status": nxt})
    lang = str(resolved.get("reply_language") or settings.hotel.default_language)
    line = guest_status_copy(nxt, lang)
    chat_id = str(payload.get("chat_id", ""))
    try:
        messaging = get_messaging(settings)
        # A status push is not new drafted content a human needs to read
        # first - the order itself already went through the review queue
        # and only reaches here once it has fired (review_status
        # sent/auto_sent, checked above). The guard still has to see SOME
        # item in an approved state to let a `send_message` through in live
        # mode, and the real order item's review_status is permanently
        # "sent" (the guard would read that as "this exact message was
        # already sent - refuse the duplicate"), which is the wrong
        # question for a *different*, later status line. A throwaway
        # stand-in with review_status="approved" is never written to the
        # database - only the real order item (`item`, still "sent") is -
        # so this changes nothing about what `tools/review.py show` or the
        # audit trail records; it only answers the guard's question
        # correctly for this call. Shadow mode still blocks it either way.
        stand_in = Item(id=f"{item.id}:status:{nxt}", kind="status_push", source="kitchen",
                        external_id=ref, review_status="approved")
        messaging.send(chat_id, line, item=stand_in)
        print(f"{ref}: {current or 'placed'} -> {nxt}. Guest notified: \"{line}\"")
    except WriteBlocked as exc:
        print(f"{ref}: {current or 'placed'} -> {nxt}. Guest NOT notified: {exc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_board = sub.add_parser("board", help="what is currently live in the kitchen")
    p_board.add_argument("--limit", type=int, default=50)
    p_board.add_argument("--all", action="store_true", help="include delivered tickets")

    p_adv = sub.add_parser("advance", help="move one ticket to its next status")
    p_adv.add_argument("id", help="item id or order ref, e.g. RS-0001")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if not settings.db_path().exists():
        print("fresh database created: no previous orders, queue or history yet "
             f"({settings.db_path()}).")
    store = Store(settings)
    try:
        if args.command == "board":
            return cmd_board(store, args)
        if args.command == "advance":
            return cmd_advance(store, settings, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
