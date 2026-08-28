"""Tests for tools/review.py: the review queue and the shadow/live send path."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging, get_pms  # noqa: E402
from core.review import approve, reject, stale_backlog  # noqa: E402

from tools.engine import DEFAULT_DISCLOSURE, process_order  # noqa: E402
from tools.review import cmd_send  # noqa: E402

DEMO_TODAY = "2026-09-01"


def _clean_order(settings, store):
    pms = get_pms(settings)
    msg = next(m for m in get_messaging(settings).fetch_new(limit=50) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    assert item.review_status == "pending_review"
    return item


def test_approve_then_send_is_blocked_in_shadow_and_the_approval_stands(
        settings_and_store, capsys):
    settings, store = settings_and_store
    item = _clean_order(settings, store)
    approve(store, item.id)
    code = cmd_send(store, settings, SimpleNamespace(limit=20))
    out = capsys.readouterr().out
    assert "blocked" in out and "approval kept" in out
    refreshed = store.get_item(item.id)
    assert refreshed.review_status == "approved"  # never advanced to sending/sent
    assert code == 1  # nothing sent counts as a failed batch, not a crash


def test_live_send_fires_the_three_writes_and_marks_the_order_placed(live_settings_and_store):
    settings, store = live_settings_and_store
    item = _clean_order(settings, store)
    approve(store, item.id)
    code = cmd_send(store, settings, SimpleNamespace(limit=20))
    assert code == 0
    refreshed = store.get_item(item.id)
    assert refreshed.review_status == "sent"
    assert refreshed.payload["_status"] == "placed"
    assert refreshed.sent_message_id == refreshed.payload["_order_ref"]


def test_reject_is_terminal_and_never_reaches_send(settings_and_store):
    settings, store = settings_and_store
    item = _clean_order(settings, store)
    reject(store, item.id, reason="guest cancelled by phone")
    code = cmd_send(store, settings, SimpleNamespace(limit=20))
    assert code == 0  # nothing to send is not a failure
    refreshed = store.get_item(item.id)
    assert refreshed.review_status == "rejected"


def test_live_send_never_duplicates_the_disclosure_line(live_settings_and_store):
    # tools/engine.py:draft() already appended the disclosure before this
    # item ever reached the queue; messaging.send()'s own with_disclosure()
    # call must be a no-op on the way out, not a second copy.
    settings, store = live_settings_and_store
    item = _clean_order(settings, store)
    assert item.draft["guest_message"].count(DEFAULT_DISCLOSURE) == 1
    approve(store, item.id)
    code = cmd_send(store, settings, SimpleNamespace(limit=20))
    assert code == 0
    outbox = settings.root / "data" / "exports" / "sent_messages.jsonl"
    sent = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
    guest_sends = [row for row in sent if row["kind"] == "guest"]
    assert guest_sends, "expected the guest confirmation to have been logged"
    assert guest_sends[0]["text"].count(DEFAULT_DISCLOSURE) == 1


def test_stale_clears_the_shadow_era_queue_at_go_live(settings_and_store):
    settings, store = settings_and_store
    item = _clean_order(settings, store)
    approve(store, item.id)
    moved = stale_backlog(store)
    assert item.id in moved
    refreshed = store.get_item(item.id)
    assert refreshed.review_status == "stale"
