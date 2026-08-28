#!/usr/bin/env python3
"""tools/run.py - In-Room Dining AI's main loop: fetch -> extract -> resolve
-> draft -> queue.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --limit 5
    python3 tools/run.py --once --provider mock
    python3 tools/run.py --set-rule upsell=off

One pass: read new guest messages, skip anything already seen, extract each
new one into an order, price and gate it deterministically, draft the
confirmation, and queue it in the review FSM (core.store). In-Room Dining AI
never sends, notifies the kitchen or posts to the PMS on its own -
workflows/80-review.md and docs/safety.md cover the review queue and the
shadow/live switch. Kitchen status advances after a ticket fires live in
tools/kitchen.py, not here.

Exit codes: 0 ok, 2 a bad --set-rule argument, 3 waiting on an `interactive`
answer (see the message), 1 a real error.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging, get_pms  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

from tools.engine import process_order, resolve_preview, set_upsell_enabled  # noqa: E402
from tools.menu_engine import money  # noqa: E402

log = get_logger("run")


def one_pass(settings, store: Store, *, limit: int, provider: str | None,
            today: str) -> tuple[int, dict]:
    """``settings.dry_run`` takes a separate, write-free path
    (``tools.engine.resolve_preview``): no item row, no cache, no order
    reference, no ``runs``/``events`` row, and the reaper never runs -
    ``--dry-run`` writes nothing, not to the database, not to sequences
    (factory/workflows/build-repo.md, "--dry-run writes nothing")."""
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "skipped": 0}
    with Run("take_orders", settings, None if settings.dry_run else store) as run:
        messaging = get_messaging(settings)
        pms = get_pms(settings)
        messages = messaging.fetch_new(limit=limit)
        seen = store.already_processed("messaging", [m.id for m in messages])
        for msg in messages:
            if msg.id in seen:
                stats["skipped"] += 1
                continue
            if settings.dry_run:
                try:
                    resolved = resolve_preview(settings, store, pms, msg, provider=provider,
                                               today=today)
                except LLMPendingInteractive as exc:
                    run.stats = dict(stats)
                    print(str(exc))
                    return 3, stats
                stats["processed"] += 1
                stats["drafted"] += 1
                if resolved.needs_human:
                    stats["needs_human"] += 1
                where = resolved.location or f"Room {resolved.room_number}"
                print(f"  --dry-run {msg.id}: {where} -> "
                     f"{money(resolved.total, resolved.currency)}, ETA {resolved.eta_minutes}min"
                     f"{' | gates: ' + ', '.join(resolved.gates) if resolved.gates else ''}")
                continue
            try:
                item, did_work = process_order(settings, store, pms, msg,
                                               provider=provider, today=today)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            if not did_work:
                stats["skipped"] += 1
                continue
            stats["processed"] += 1
            stats["drafted"] += 1
            if item.review_status == "needs_human":
                stats["needs_human"] += 1
            log.info("queued", item_id=item.id, ref=item.payload.get("_order_ref"),
                     room=item.payload.get("room_number"), status=item.review_status)
        if not settings.dry_run:
            reaped = store.reap_stuck_sending()
            if reaped:
                log.warn("reaped stuck sends", count=len(reaped))
        run.stats = dict(stats)
    if settings.dry_run:
        print("\n--dry-run: nothing written - no item row, no LLM usage event, no queue "
             "entry.\n")
    return 0, stats


def _parse_set_rule(spec: str) -> tuple[str, bool]:
    if "=" not in spec:
        raise ValueError('expected "upsell=on" or "upsell=off"')
    key, _, value = spec.partition("=")
    key, value = key.strip(), value.strip().lower()
    if key != "upsell":
        raise ValueError(f'only "upsell" is a live rule, got {key!r}')
    if value not in ("on", "off"):
        raise ValueError(f'value must be "on" or "off", got {value!r}')
    return key, value == "on"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--limit", type=int, default=50, help="max messages per pass")
    parser.add_argument("--provider", default=None,
                        help="override llm.provider for this run")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 300)")
    parser.add_argument("--set-rule", default=None, metavar="upsell=on|off",
                        help="toggle the live upsell rule, then exit without taking orders")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if not settings.db_path().exists():
        print("fresh database created: no previous orders, queue or history yet "
             f"({settings.db_path()}).")
    store = Store(settings)
    try:
        if args.set_rule:
            try:
                key, enabled = _parse_set_rule(args.set_rule)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            if settings.dry_run:
                print(f"--dry-run: would set {key}={'on' if enabled else 'off'}. "
                     "Nothing written.")
                return 0
            set_upsell_enabled(store, enabled)
            print(f"{key}: {'on' if enabled else 'off'}. Re-run `make run` to see the effect "
                 "on the next order.")
            return 0

        today = date.today().isoformat()
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 300))
            while True:
                code, stats = one_pass(settings, store, limit=args.limit,
                                       provider=args.provider, today=today)
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider,
                               today=today)
        print(summary_line(stats, settings.mode))
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
