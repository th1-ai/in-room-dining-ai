#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Forces `llm.provider=mock` and `mode=shadow` regardless of config/hotel.yaml,
so this always works on a fresh clone with a blank .env (ARCHITECTURE.md
section 1, "works in 5 minutes with zero credentials"). It runs against its
own database (data/demo/demo.db) so running it twice always shows the same
twelve fixtures, and never touches data/agent.db (that is `make run`'s file).

The date is pinned to DEMO_TODAY so the bundled reservations (fixtures/hotel/
reservations.json) always look in-house, whatever day you actually run this -
tools/run.py uses the real date; only this script and the tests pin it.

Prints one line every check reads for the pass/fail signal:

    DEMO OK — 12 items processed, 12 drafted, 0 sent (shadow)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging, get_pms  # noqa: E402
from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store  # noqa: E402

from tools.engine import process_order  # noqa: E402
from tools.menu_engine import money  # noqa: E402

DEMO_TODAY = "2026-09-01"


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    store = Store(settings, path=demo_db)

    messaging = get_messaging(settings)
    pms = get_pms(settings)
    messages = messaging.fetch_new(limit=50)
    if not messages:
        print("no fixtures found in fixtures/inbound/messages.json - nothing to demo",
             file=sys.stderr)
        return 1

    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0}
    print(f"In-Room Dining AI demo - {len(messages)} sample guest message(s) from "
         f"fixtures/inbound/messages.json\n")
    for msg in messages:
        item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
        stats["processed"] += 1
        stats["drafted"] += 1
        if item.review_status == "needs_human":
            stats["needs_human"] += 1
        resolved = (item.payload or {}).get("_resolved") or {}
        room = item.payload.get("location") or f"Room {item.payload.get('room_number', '?')}"
        if resolved.get("is_order") and resolved.get("lines"):
            total = money(resolved.get("total", 0), resolved.get("currency", settings.hotel.currency))
            gate_note = f" [{', '.join(resolved['gates'])}]" if resolved.get("gates") else ""
            print(f"  {msg.id}: {room} -> {total}, ETA {resolved.get('eta_minutes')}min, "
                 f"status={item.review_status}{gate_note}")
        else:
            print(f"  {msg.id}: {room} -> not an order, status={item.review_status}")

    print(f"\n{stats['needs_human']} of {stats['processed']} need a person to look first "
         f"(a sold-out item, an unmatched item, any dietary note, an unsupported language, "
         f"or a message that was not an order at all - see docs/safety.md).")
    print("Nothing was sent: mode is shadow, and demo never calls send() at all.")
    print("Next: `make review` to see the queue, or read workflows/10-take-orders.md.\n")

    print(f"DEMO OK — {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
