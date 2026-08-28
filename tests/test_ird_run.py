"""Tests for tools/run.py's one_pass loop, --dry-run, and --set-rule."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

from tools.engine import get_upsell_enabled, set_upsell_enabled  # noqa: E402
from tools.run import one_pass  # noqa: E402

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


def test_one_pass_processes_all_twelve_and_reports_needs_human(settings_and_store):
    settings, store = settings_and_store
    code, stats = one_pass(settings, store, limit=50, provider="mock", today=DEMO_TODAY)
    assert code == 0
    assert stats["processed"] == 12
    assert stats["drafted"] == 12
    assert stats["sent"] == 0
    # order-01, order-02 and order-11 are the clean ones (see test_ird_engine.py)
    assert stats["needs_human"] == 9


def test_one_pass_is_idempotent_on_a_second_call(settings_and_store):
    settings, store = settings_and_store
    one_pass(settings, store, limit=50, provider="mock", today=DEMO_TODAY)
    code, stats = one_pass(settings, store, limit=50, provider="mock", today=DEMO_TODAY)
    assert code == 0
    assert stats["processed"] == 0
    assert stats["skipped"] == 12


def test_dry_run_writes_no_business_rows_twice_in_a_row(tmp_path, monkeypatch):
    """factory/workflows/build-repo.md, "--dry-run writes nothing": no item
    row, no sequence bump, no runs/events row - run it twice on the same
    fresh fixtures and nothing accumulates, nothing raises."""
    monkeypatch.chdir(REPO_ROOT)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "hotel.yaml").write_text(
        (REPO_ROOT / "config" / "hotel.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8")
    (cfg_dir / "agent.yaml").write_text(
        (REPO_ROOT / "config" / "agent.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8")
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(cfg_dir))
    settings = load_settings(provider="mock", mode="shadow", dry_run=True)
    store = Store(settings, path=tmp_path / "dry.db")
    try:
        for _ in range(2):
            code, stats = one_pass(settings, store, limit=50, provider="mock", today=DEMO_TODAY)
            assert code == 0
            assert stats["processed"] == 12  # nothing is ever marked "seen" on dry-run
            assert stats["sent"] == 0
            assert store.list_items(limit=100) == []
            assert store.counts() == {}
            row = store.db.execute("SELECT value FROM sequences WHERE name='order_ref'") \
                .fetchone()
            assert row is None
    finally:
        store.close()


def test_set_rule_toggles_the_live_upsell_flag(settings_and_store):
    settings, store = settings_and_store
    assert get_upsell_enabled(store, settings) is True
    set_upsell_enabled(store, False)
    assert get_upsell_enabled(store, settings) is False
    set_upsell_enabled(store, True)
    assert get_upsell_enabled(store, settings) is True
