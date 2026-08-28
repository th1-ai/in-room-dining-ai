"""Shared pytest fixtures.

Two guarantees for every `test_menu_engine.py` / `test_ird_*.py` test:

1. **Tests never read this repo's own `config/hotel.yaml` /
   `config/agent.yaml`** - those are the hotel's own edits, and a real
   property filling them in must never be able to turn `make test` red
   (factory/workflows/build-repo.md section 5, "Tests never read the live
   config"). `AGENT_CONFIG_DIR` points at a throwaway copy of the shipped
   `.example.yaml` files instead.
2. **Tests never write into this repo's own `data/`.** A mock adapter's
   `data/exports/*.jsonl`/`*.csv` (guest confirmations, kitchen tickets, the
   order log) and the SQLite store both resolve through
   `core.config.repo_root()`, which respects `AGENT_REPO_ROOT` - the
   `_isolated_repo` fixture below points it at a throwaway sandbox holding
   copies of `prompts/`, `knowledge/`, `fixtures/` and `config/`, so a live
   send in a test can never leave a phantom "sent" record in the real repo.

`load_settings(demo=True)` still forces `mock` provider, `shadow` mode and
`mock` adapters regardless of file content - `settings_and_store` uses it;
`live_settings_and_store` deliberately does not, to exercise the real send
path once an item is approved.

`test_core_*.py` (synced byte-identical from `factory/core/` - never edited
here) manage their own isolation per test and do not use these fixtures.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

DEMO_TODAY = "2026-09-01"


@pytest.fixture(autouse=True)
def _isolated_repo(request, tmp_path, monkeypatch):
    """Factory-seeded (factory/scaffold/tests/conftest.py) - sandboxes
    `AGENT_CONFIG_DIR` and `AGENT_REPO_ROOT` for every test module except
    `test_core_*.py`, which manage their own isolation. `settings_and_store`
    and `live_settings_and_store` below build on top of the env vars this
    sets rather than duplicating the sandboxing themselves.
    """
    module_file = os.path.basename(str(getattr(request.node, "path", "")))
    if module_file.startswith("test_core_"):
        yield
        return
    cfg_dir = tmp_path / "isolated-config"
    cfg_dir.mkdir(exist_ok=True)
    for name in ("hotel", "agent"):
        example = REPO_ROOT / "config" / f"{name}.example.yaml"
        if example.exists():
            shutil.copy(example, cfg_dir / f"{name}.yaml")
    sandbox = tmp_path / "isolated-repo"
    if not sandbox.exists():
        sandbox.mkdir()
        for name in ("prompts", "knowledge", "fixtures", "config"):
            src = REPO_ROOT / name
            if src.exists():
                shutil.copytree(src, sandbox / name)
        (sandbox / "data" / "imports").mkdir(parents=True)
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENT_REPO_ROOT", str(sandbox))
    for var in ("AGENT_MODE", "LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def settings_and_store(tmp_path, _isolated_repo):
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "test.db")
    yield settings, store
    store.close()


@pytest.fixture
def live_settings_and_store(tmp_path, monkeypatch, _isolated_repo):
    """Same sandbox as ``settings_and_store`` but `mode: live` with the
    `mock` adapters still in force - used only to test the send path itself
    (WriteBlocked is what proves shadow works; this fixture proves the write
    goes through once a human has approved it and mode really is live). The
    `mode="live"` CLI-style override on `load_settings` beats the sandboxed
    `hotel.yaml`'s own `mode: shadow`, so there is no need to hand-edit the
    file - see core/config.py's resolution order.
    """
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    settings = load_settings(provider="mock", mode="live")
    store = Store(settings, path=tmp_path / "test-live.db")
    yield settings, store
    store.close()
