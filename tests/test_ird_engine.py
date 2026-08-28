"""Tests for tools/engine.py's order-taking loop against the bundled
fixtures, with provider=mock. No network, no credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging, get_pms  # noqa: E402

from tools.engine import DEFAULT_DISCLOSURE, process_order  # noqa: E402

DEMO_TODAY = "2026-09-01"

EXPECTED_STATUS = {
    "order-01": "pending_review", "order-02": "pending_review",
    "order-03": "needs_human", "order-04": "needs_human", "order-05": "needs_human",
    "order-06": "needs_human", "order-07": "needs_human", "order-08": "needs_human",
    "order-09": "needs_human", "order-10": "needs_human", "order-11": "pending_review",
    "order-12": "needs_human",
}


def _messages(settings):
    return get_messaging(settings).fetch_new(limit=50)


def test_twelve_fixtures_are_present(settings_and_store):
    settings, _store = settings_and_store
    messages = _messages(settings)
    assert len(messages) == 12
    assert {m.id for m in messages} == set(EXPECTED_STATUS)


def test_every_fixture_resolves_to_its_expected_status(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    for msg in _messages(settings):
        item, did_work = process_order(settings, store, pms, msg, provider="mock",
                                       today=DEMO_TODAY)
        assert did_work is True
        assert item.review_status == EXPECTED_STATUS[msg.id], msg.id


def test_a_clean_order_is_priced_and_never_needs_a_human(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    resolved = item.payload["_resolved"]
    assert resolved["total"] == 42
    assert resolved["gates"] == []
    assert item.draft["guest_message"]


def test_not_an_order_skips_the_draft_call_but_still_gets_a_draft_record(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-08")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    assert item.review_status == "needs_human"
    assert item.draft is not None
    assert item.draft["guest_message"] == ""


def test_shadow_mode_never_sends_anything(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    for msg in _messages(settings):
        process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0


def test_rerun_is_idempotent_and_does_not_reprocess(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    messages = _messages(settings)
    for msg in messages:
        process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    for msg in messages:
        item, did_work = process_order(settings, store, pms, msg, provider="mock",
                                       today=DEMO_TODAY)
        assert did_work is False  # already handled by the first pass
    assert len(store.list_items(kind="order", limit=100)) == 12


# --------------------------------------------------------------------------
# EU AI Act Article 50 - SIMULATION.md finding 1: the disclosure line must
# actually ship, and a demo confirmation must carry it with zero setup.
# --------------------------------------------------------------------------
def test_demo_confirmation_always_carries_the_ai_disclosure_line(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    # `_isolated_repo` only carries knowledge/disclosure.example.md, exactly
    # what a fresh clone ships - so this also proves the shipped generic
    # default is what makes the line appear, not a per-test fixture.
    assert DEFAULT_DISCLOSURE in item.draft["guest_message"]
    assert item.draft["guest_message"].count(DEFAULT_DISCLOSURE) == 1


def test_gated_order_confirmation_also_carries_the_disclosure_line(settings_and_store):
    # order-05 has a named nut-allergy gate and still gets a real drafted
    # guest_message (only "not an order" skips the draft call entirely) -
    # the disclosure line must not depend on the order being clean.
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-05")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    assert item.review_status == "needs_human"
    assert DEFAULT_DISCLOSURE in item.draft["guest_message"]


def test_not_an_order_never_gets_a_disclosure_line_on_an_empty_message(settings_and_store):
    # No content, nothing to disclose against - matches the existing empty
    # guest_message contract for "not an order" items.
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-08")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    assert item.draft["guest_message"] == ""


def test_custom_disclosure_file_is_used_instead_of_the_generic_default(settings_and_store):
    settings, store = settings_and_store
    custom = "Δημιουργήθηκε με τη βοήθεια AI και ελέγχθηκε από την ομάδα μας."
    (settings.root / "knowledge" / "disclosure.md").write_text(custom, encoding="utf-8")
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    assert custom in item.draft["guest_message"]
    assert DEFAULT_DISCLOSURE not in item.draft["guest_message"]


def test_room_identity_resolves_from_the_fixture_pms(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    resolved = item.payload["_resolved"]
    assert resolved["guest_name"] == "Owen Bright"
    assert resolved["reservation_id"] == "204"


def test_order_ref_is_minted_once_per_item(settings_and_store):
    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-01")
    item, _ = process_order(settings, store, pms, msg, provider="mock", today=DEMO_TODAY)
    ref = item.payload["_order_ref"]
    assert ref.startswith("RS-")
    item2, did_work = process_order(settings, store, pms, msg, provider="mock",
                                    today=DEMO_TODAY)
    assert did_work is False
    assert item2.payload["_order_ref"] == ref


def test_retry_after_draft_pended_resumes_at_draft_not_extract(settings_and_store, monkeypatch):
    """`interactive` can park the draft call after extraction already
    succeeded. The next pass must draft (not skip the item) and must not
    extract a second time - the trap documented in
    factory/workflows/build-repo.md ("Resumable stages")."""
    import tools.engine as engine
    from core.llm import LLMPendingInteractive

    settings, store = settings_and_store
    pms = get_pms(settings)
    msg = next(m for m in _messages(settings) if m.id == "order-01")

    calls = {"extract": 0, "draft": 0}
    real_extract, real_draft = engine.extract, engine.draft

    def counting_extract(*a, **kw):
        calls["extract"] += 1
        return real_extract(*a, **kw)

    def pending_draft(*a, **kw):
        calls["draft"] += 1
        if calls["draft"] == 1:
            raise LLMPendingInteractive("pending-1", Path("/tmp/pending-1.prompt.md"), None,
                                        Path("/tmp/pending-1.answer.json"))
        return real_draft(*a, **kw)

    monkeypatch.setattr(engine, "extract", counting_extract)
    monkeypatch.setattr(engine, "draft", pending_draft)

    try:
        engine.process_order(settings, store, pms, msg, today=DEMO_TODAY)
    except LLMPendingInteractive:
        pass
    parked = store.get_by_external("messaging", msg.id)
    assert parked.intent and parked.draft is None and parked.review_status == "new"

    item, did_work = engine.process_order(settings, store, pms, msg, today=DEMO_TODAY)
    assert did_work is True
    assert item.draft is not None
    assert item.review_status in ("pending_review", "needs_human")
    assert calls == {"extract": 1, "draft": 2}
