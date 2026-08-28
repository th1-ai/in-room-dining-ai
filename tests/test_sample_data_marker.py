"""A real (not `make demo`) pass on a fresh clone must never let a shipped
sample fixture pass for the property's own guest orders.

`core.store.Store.upsert_item` tags an item `_sample: True` when its source
is read through a `mock` adapter outside `make demo`
(`core.adapters.is_sample_source`); `item.is_sample` reads that back.
`config/agent.example.yaml: systems_used: [pms, messaging]` says both
adapters count here - this repo reads `messaging.fetch_new` (guest
messages) and `pms.get_reservation` / `pms.list_in_house` (the room's
reservation) - so `tools/review.py list` and `show` must print a
`[SAMPLE DATA]` marker a host cannot miss before approving an order.

`live_settings_and_store` (a file-local fixture below, on top of
`tests/conftest.py`'s autouse `_isolated_repo` sandbox) builds real
(non-demo) settings with `systems.pms.adapter` / `systems.messaging.adapter`
still the shipped `mock` default (config/hotel.example.yaml), which is
exactly the "connected nothing yet" state a fresh clone starts in.
"""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

from tools.review import cmd_list, cmd_show  # noqa: E402

RESOLVED = {"total": 42.0, "currency": "EUR", "gates": []}


@pytest.fixture
def live_settings_and_store(tmp_path, monkeypatch):
    """Real (non-demo) settings in `mode: live` with the `mock` adapters
    still in force - the "connected nothing yet" state a fresh clone
    starts in, which is exactly what makes a real pass sample data."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    settings = load_settings(provider="mock", mode="live")
    store = Store(settings, path=tmp_path / "test-live.db")
    yield settings, store
    store.close()


def _sample_order(store):
    item = store.upsert_item(
        "messaging", "sample-order-1", kind="order",
        payload={"room_number": "204", "chat_id": "c-204", "location": "",
                "_order_ref": "RS-0001", "_resolved": RESOLVED})
    return store.transition(item.id, "pending_review", "agent") or item


def test_a_real_pass_on_the_mock_default_tags_the_order_sample(live_settings_and_store):
    settings, store = live_settings_and_store
    assert settings.demo is False
    assert settings.systems.pms.adapter == "mock"        # the shipped default
    assert settings.systems.messaging.adapter == "mock"  # the shipped default
    item = _sample_order(store)
    assert item.payload.get("_sample") is True
    assert item.is_sample is True


def test_review_list_shows_the_sample_marker(live_settings_and_store, capsys):
    settings, store = live_settings_and_store
    _sample_order(store)
    capsys.readouterr()  # discard anything printed while setting up
    assert cmd_list(store, Namespace(status=None, limit=50)) == 0
    out = capsys.readouterr().out
    assert "[SAMPLE DATA]" in out


def test_review_show_says_it_is_sample_data(live_settings_and_store, capsys):
    settings, store = live_settings_and_store
    item = _sample_order(store)
    capsys.readouterr()
    assert cmd_show(store, Namespace(id=item.id)) == 0
    out = capsys.readouterr().out
    assert "[SAMPLE DATA]" in out
