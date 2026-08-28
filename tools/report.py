#!/usr/bin/env python3
"""tools/report.py - what the agent did, and what it cost.

    make report
    python3 tools/report.py --json

Everything here is computed from `data/agent.db` - nothing phoned home. See
docs/benefits.md for what each number means and its honest caveats.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.review import queue_summary  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

from tools.menu_engine import money  # noqa: E402


def build_report(store: Store, currency: str) -> dict:
    counts = store.counts()
    queue = queue_summary(store)
    usage = store.usage_totals()
    total_orders = sum(counts.values())
    sent = counts.get("sent", 0) + counts.get("auto_sent", 0)
    rejected = counts.get("rejected", 0)

    revenue = 0.0
    order_values: list[float] = []
    upsell_target_count = 0
    gate_counter: Counter[str] = Counter()
    for item in store.list_items(kind="order", limit=5000):
        payload = item.payload or {}
        resolved = payload.get("_resolved") or {}
        for gate in resolved.get("gates") or []:
            gate_counter[gate.split(":")[0]] += 1
        if item.review_status in ("sent", "auto_sent"):
            total = float(resolved.get("total") or 0)
            revenue += total
            order_values.append(total)
            upsell_slug = resolved.get("upsell", {})
            lines = resolved.get("lines") or []
            target_slug = (upsell_slug or {}).get("slug")
            if target_slug and any(l.get("slug") == target_slug for l in lines):
                # the offered item ended up on the ticket - counted loosely
                # here as an attach; tools/engine.py:order_snapshot computes
                # the same number for the confirmation prompt itself.
                upsell_target_count += 1

    avg_order = round(revenue / len(order_values), 2) if order_values else 0.0
    attach_pct = round(100 * upsell_target_count / len(order_values), 1) if order_values else 0.0

    return {
        "total_orders": total_orders, "by_status": counts,
        "waiting_on_human": queue["waiting_on_human"], "sent": sent, "rejected": rejected,
        "revenue_fired": round(revenue, 2), "avg_order_value": avg_order,
        "upsell_attach_pct": attach_pct,
        "dietary_escalations": gate_counter.get("dietary_signal", 0),
        "unmatched_item_escalations": gate_counter.get("unmatched_item", 0),
        "sold_out_escalations": gate_counter.get("sold_out", 0),
        "language_escalations": gate_counter.get("language_unsupported", 0),
        "not_an_order": gate_counter.get("not_recognized_as_order", 0),
        "llm_calls": usage["calls"], "llm_cost_usd": round(usage["cost_usd"], 4),
        "currency": currency,
    }


def print_human(report: dict, mode: str) -> None:
    print("In-Room Dining AI - report\n")
    print(f"Mode: {mode}")
    print(f"Orders seen: {report['total_orders']}")
    print(f"Waiting for a person: {report['waiting_on_human']}")
    print(f"Fired to the kitchen: {report['sent']}")
    print(f"Rejected: {report['rejected']}")
    print()
    print(f"Revenue on fired tickets: {money(report['revenue_fired'], report['currency'])}")
    print(f"Average order value: {money(report['avg_order_value'], report['currency'])}")
    print(f"Upsell attach rate: {report['upsell_attach_pct']}%")
    print()
    print("Why orders needed a person:")
    print(f"  Dietary/allergy note: {report['dietary_escalations']}")
    print(f"  Item not on the menu: {report['unmatched_item_escalations']}")
    print(f"  Item sold out: {report['sold_out_escalations']}")
    print(f"  Guest language not configured: {report['language_escalations']}")
    print(f"  Not an order at all: {report['not_an_order']}")
    print()
    print(f"LLM calls: {report['llm_calls']} (extract + draft, one pair per order) - "
         f"cost so far: ${report['llm_cost_usd']}")
    if mode == "shadow":
        print("\nNote: mode is shadow, so 'fired' and 'revenue' are zero until you go live "
             "- see docs/benefits.md.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if not settings.db_path().exists():
        print("fresh database created: no previous orders, queue or history yet "
             f"({settings.db_path()}).")
    try:
        store = Store(settings)
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    try:
        report = build_report(store, settings.hotel.currency)
    finally:
        store.close()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report, settings.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
