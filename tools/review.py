#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit /
reject / retry / send.

    python3 tools/review.py list [--status pending_review]
    python3 tools/review.py show <id-or-ref>
    python3 tools/review.py approve <id-or-ref> [--note "..."]
    python3 tools/review.py edit <id-or-ref> --body-file guest-message.txt [--kitchen-line "..."] [--note "..."]
    python3 tools/review.py reject <id-or-ref> --reason "duplicate order"
    python3 tools/review.py retry <id-or-ref>   # re-queue a failed send
    python3 tools/review.py send                # fires every approved/edited ticket

`<id-or-ref>` is either the full item id `list` prints, or the order
reference (e.g. `RS-0001`) shown next to it - whichever is easier to copy.

Only this tool writes `approved` / `edited` / `rejected` (core/review.py).
Only `send` writes `sending` / `sent`, and only `send` performs the three
real writes for an order: the folio note (`pms.add_note`), the guest
confirmation (`messaging.send`) and the kitchen ticket
(`messaging.notify_staff`), plus an order-log row (`sheets.append`). Nothing
here bypasses `mode: shadow` - see docs/safety.md.

A note on retries: if one of the three writes for an order fails after an
earlier one already succeeded (for example the folio note posts but the
guest message fails), `retry` re-attempts all three. A folio note being
posted twice is the safe direction for a mistake to fail in; check
`data/exports/pms_writes.csv` (or your real PMS) before retrying an order a
second time in the same shift.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging, get_pms, get_sheets  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.review import (WriteBlocked, approve, edit, list_queue, reject, retry,  # noqa: E402
                         show, stale_backlog)
from core.store import Store, StoreError  # noqa: E402
from core.store import utcnow  # noqa: E402

from tools.menu_engine import OrderLine, kitchen_ticket_text, money  # noqa: E402


def _find(store, id_or_ref: str):
    """Resolve either the full item id or the order ref (``RS-0001``) shown
    next to it in `list`. Every command below accepts both - `list` prints
    the id at full length, but the ref is the one worth copying."""
    item = store.get_item(id_or_ref)
    if item is not None:
        return item
    for candidate in store.list_items(kind="order", limit=5000):
        if (candidate.payload or {}).get("_order_ref") == id_or_ref:
            return candidate
    return None


def _print_item_line(item) -> None:
    payload = item.payload or {}
    resolved = payload.get("_resolved") or {}
    ref = payload.get("_order_ref") or item.id
    room = payload.get("location") or (f"Room {payload.get('room_number', '?')}")
    total = resolved.get("total")
    total_str = f"{resolved.get('currency', '')} {total:,.0f}" if total is not None else "-"
    gates = ", ".join(resolved.get("gates") or []) or "-"
    # `item.is_sample` is set by core (core/store.py) for anything read
    # through a mock adapter outside `make demo` - see docs/integrations.md
    # "Sample data is labelled". A human working the real queue must never
    # mistake a shipped fixture for a real guest order.
    marker = "  [SAMPLE DATA]" if item.is_sample else ""
    print(f"  {ref:<8} {item.review_status:<14} {room:<26} {total_str:<10} {gates[:40]}{marker}\n"
         f"    id: {item.id}")


def cmd_list(store, args) -> int:
    items = list_queue(store, status=args.status, kind="order", limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    print("\nRun `python3 tools/review.py show <ref>` for the full order (the ref, "
         "e.g. RS-0001, or the id both work).")
    return 0


def cmd_show(store, args) -> int:
    item = _find(store, args.id)
    if item is None:
        print(f"error: no order {args.id}", file=sys.stderr)
        return 1
    detail = show(store, item.id)
    if item.is_sample:
        print("[SAMPLE DATA] this order was read through a mock adapter, not your "
             "property - see docs/integrations.md.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, args) -> int:
    found = _find(store, args.id)
    if found is None:
        print(f"error: no order {args.id}", file=sys.stderr)
        return 1
    item = approve(store, found.id, note=args.note or "")
    ref = (item.payload or {}).get("_order_ref") or item.id
    print(f"approved {ref} - now in the send queue")
    return 0


def cmd_edit(store, args) -> int:
    item = _find(store, args.id)
    if item is None:
        print(f"error: no order {args.id}", file=sys.stderr)
        return 1
    body = Path(args.body_file).read_text(encoding="utf-8")
    new_draft = dict(item.draft or {})
    new_draft["guest_message"] = body
    if args.kitchen_line is not None:
        new_draft["kitchen_ticket_line"] = args.kitchen_line
    edit(store, item.id, new_draft, note=args.note or "")
    ref = (item.payload or {}).get("_order_ref") or item.id
    print(f"edited {ref} - now in the send queue")
    return 0


def cmd_reject(store, args) -> int:
    found = _find(store, args.id)
    if found is None:
        print(f"error: no order {args.id}", file=sys.stderr)
        return 1
    item = reject(store, found.id, reason=args.reason or "")
    ref = (item.payload or {}).get("_order_ref") or item.id
    print(f"rejected {ref}")
    return 0


def cmd_retry(store, args) -> int:
    found = _find(store, args.id)
    if found is None:
        print(f"error: no order {args.id}", file=sys.stderr)
        return 1
    item = retry(store, found.id)
    ref = (item.payload or {}).get("_order_ref") or item.id
    print(f"queued {ref} for another send attempt")
    return 0


def cmd_send(store, settings, args) -> int:
    claimed = store.claim_for_send(limit=args.limit)
    if not claimed:
        print("Nothing approved or edited is waiting to send.")
        return 0
    pms = get_pms(settings)
    messaging = get_messaging(settings)
    sheets = get_sheets(settings)
    sent, failed = 0, 0
    for item in claimed:
        payload = item.payload or {}
        draft = item.draft or {}
        resolved = payload.get("_resolved") or {}
        ref = payload.get("_order_ref") or item.id[:8]
        room_number = str(payload.get("room_number", ""))
        location = str(payload.get("location", ""))
        chat_id = str(payload.get("chat_id", ""))
        guest_name = str(resolved.get("guest_name") or "Guest")
        reservation_id = resolved.get("reservation_id")
        currency = str(resolved.get("currency") or settings.hotel.currency)
        total = float(resolved.get("total") or 0)
        lines = [OrderLine(**l) for l in (resolved.get("lines") or [])]
        ticket_text = kitchen_ticket_text(
            ref=ref, room_number=room_number, location=location, guest_name=guest_name,
            lines=lines, total=total, currency=currency,
            dietary_ticket_line=str(draft.get("kitchen_ticket_line") or ""))
        try:
            if reservation_id:
                pms.add_note(reservation_id, ticket_text, item=item)
            messaging.send(chat_id, str(draft.get("guest_message") or ""), item=item)
            messaging.notify_staff(ticket_text, item=item)
            sheets.append("orders", [[
                ref, utcnow(), room_number, location, guest_name,
                "; ".join(f"{l.name} x{l.quantity}" for l in lines), f"{total:.2f}", currency,
                ", ".join(resolved.get("gates") or []),
            ]], item=item)
        except WriteBlocked as exc:
            # Not a failure: the mode blocked it. The approval stands for go-live.
            store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
            print(f"blocked {item.id} (approval kept): {exc}")
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.mark_send_failed(item.id, str(exc))
            print(f"failed {item.id}: {exc}")
            failed += 1
            continue
        store.set_fields(item.id, payload={**payload, "_status": "placed"})
        store.mark_sent(item.id, message_id=ref)
        print(f"sent {item.id} ({ref}) - {money(total, currency)} to {room_number or location}")
        sent += 1
    print(f"\n{sent} sent, {failed} failed.")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one order")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the confirmation and ticket unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="rewrite the guest message, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--body-file", required=True,
                        help="file containing the new guest_message text")
    p_edit.add_argument("--kitchen-line", default=None,
                        help="optional replacement kitchen_ticket_line")
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the order")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", "--note", dest="reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed send")
    p_retry.add_argument("id")

    p_send = sub.add_parser("send", help="fire every approved/edited ticket")
    p_send.add_argument("--limit", type=int, default=20)

    sub.add_parser("stale", help="go-live step: mark everything still un-sent as stale "
                                 "(the shadow-era queue was never sent and is out of date)")

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
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        if args.command == "stale":
            moved = stale_backlog(store)
            print(f"marked {len(moved)} item(s) stale. Nothing from before go-live will be sent.")
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
