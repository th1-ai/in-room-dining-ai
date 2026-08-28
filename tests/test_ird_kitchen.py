"""Tests for tools/kitchen.py: the live board and status advances."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging, get_pms  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.review import approve  # noqa: E402
from core.store import Store  # noqa: E402

from tools.engine import process_order  # noqa: E402
from tools.kitchen import cmd_advance, cmd_board  # noqa: E402
from tools.review import cmd_send  # noqa: E402

DEMO_TODAY = "2026-09-01"


@pytest.fixture
def settings_and_store(tmp_path):
    """`tests/conftest.py`'s autouse `_isolated_repo` fixture sandboxes
    AGENT_CONFIG_DIR / AGENT_REPO_ROOT for every test here; this file-local
    fixture only builds the (settings, store) pair this module's tests
    share - `demo=True` forces mock provider, shadow mode and mock
    adapters regardless of the sandboxed config content."""
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "test.db")
    yield settings, store
    store.close()


@pytest.fixture
def live_settings_and_store(tmp_path, monkeypatch):
    """Same sandbox, but real (non-demo) settings in `mode: live` with the
    `mock` adapters still in force - the only way to test a real send
    (`tools/review.py send`) or a kitchen status push, both guarded writes
    that `mode: shadow` would block outright."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    settings = load_settings(provider="mock", mode="live")
    store = Store(settings, path=tmp_path / "test-live.db")
    yield settings, store
    store.close()


def _fired_order(settings, store):
    """Approve and really send order-01 (needs live mode + mock adapters -
    see conftest.py:live_settings_and_store)."""
    pms = get_pms(settings)
    msg = next(m for m in get_messaging(settings).fetch_new(limit=50) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    approve(store, item.id)
    assert cmd_send(store, settings, SimpleNamespace(limit=20)) == 0
    return store.get_item(item.id)


def test_advance_cannot_move_an_order_that_has_not_fired(settings_and_store, capsys):
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in get_messaging(settings).fetch_new(limit=50) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    code = cmd_advance(store, settings, SimpleNamespace(id=item.id))
    assert code == 1
    captured = capsys.readouterr()
    assert "has not been approved and sent" in captured.out + captured.err


def test_advance_walks_the_full_status_flow_and_pushes_the_guest(live_settings_and_store,
                                                                  capsys):
    settings, store = live_settings_and_store
    item = _fired_order(settings, store)
    ref = item.payload["_order_ref"]

    for expected in ("preparing", "on the way", "delivered"):
        code = cmd_advance(store, settings, SimpleNamespace(id=ref))
        assert code == 0
        out = capsys.readouterr().out
        assert expected in out
        assert "Guest notified" in out
        refreshed = store.get_item(item.id)
        assert refreshed.payload["_status"] == expected

    # a delivered order is a no-op, not an error
    code = cmd_advance(store, settings, SimpleNamespace(id=ref))
    assert code == 0
    assert "already delivered" in capsys.readouterr().out


def test_advance_accepts_the_order_ref_or_the_item_id(live_settings_and_store):
    settings, store = live_settings_and_store
    item = _fired_order(settings, store)
    assert cmd_advance(store, settings, SimpleNamespace(id=item.id)) == 0
    refreshed = store.get_item(item.id)
    assert refreshed.payload["_status"] == "preparing"


def test_board_lists_fired_tickets_and_hides_delivered_by_default(live_settings_and_store,
                                                                   capsys):
    settings, store = live_settings_and_store
    item = _fired_order(settings, store)
    ref = item.payload["_order_ref"]
    for _ in range(3):
        cmd_advance(store, settings, SimpleNamespace(id=ref))
    capsys.readouterr()

    cmd_board(store, SimpleNamespace(limit=50, all=False))
    assert "Nothing on the board" in capsys.readouterr().out

    cmd_board(store, SimpleNamespace(limit=50, all=True))
    assert ref in capsys.readouterr().out
