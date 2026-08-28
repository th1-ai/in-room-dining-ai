#!/usr/bin/env python3
"""tools/doctor.py - is In-Room Dining AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus
In-Room Dining AI's own: the menu, the kitchen numbers, the upsell target,
the prompts, the schedule block, and the AI-disclosure line
(`knowledge/disclosure.md`, warn-only - see docs/safety.md). Exits 0 when
everything passed, 1 when a FAIL line needs fixing. Never a traceback: a
config error is shown as a FAIL row like any other.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402

from tools.menu_engine import load_menu  # noqa: E402


def check_menu(settings: Settings) -> Check:
    raw = settings.agent_get("menu", [])
    if not raw:
        return Check("menu", FAIL, "config/agent.yaml has no menu: items",
                     "Copy config/agent.example.yaml to config/agent.yaml - it ships "
                     "with a starter menu.")
    menu = load_menu(raw)
    bad_price = [m.slug for m in menu if m.price <= 0]
    if bad_price:
        return Check("menu", FAIL, f"{len(bad_price)} item(s) with a price of 0 or less: "
                     f"{', '.join(bad_price)}", "Fix the price in config/agent.yaml: menu:.")
    dupes = {slug for slug in (m.slug for m in menu) if [m.slug for m in menu].count(slug) > 1}
    if dupes:
        return Check("menu", FAIL, f"duplicate slug(s): {', '.join(sorted(dupes))}",
                     "Every menu item needs a unique slug - extraction matches on it.")
    return Check("menu", PASS, f"{len(menu)} item(s) across "
                 f"{len({m.category for m in menu})} categories")


def check_kitchen(settings: Settings) -> Check:
    cfg = settings.agent_get("kitchen", {}) or {}
    required = ("tray_charge", "eta_base_minutes", "capacity_before_delay",
               "delay_step_minutes", "max_eta_minutes")
    missing = [k for k in required if k not in cfg]
    if missing:
        return Check("kitchen config", FAIL, f"missing {', '.join(missing)} in "
                     "config/agent.yaml: kitchen:",
                     "Copy config/agent.example.yaml - it ships with sane defaults.")
    sold_out = cfg.get("sold_out") or []
    return Check("kitchen config", PASS,
                 f"ETA {cfg['eta_base_minutes']}min base, grows after "
                 f"{cfg['capacity_before_delay']} tickets, capped at {cfg['max_eta_minutes']}min"
                 + (f" | {len(sold_out)} item(s) sold out today" if sold_out else ""))


def check_upsell(settings: Settings) -> Check:
    upsell = settings.agent_get("upsell", {}) or {}
    target = str(upsell.get("target_slug") or "")
    if not target:
        return Check("upsell", WARN, "no upsell.target_slug configured",
                     "Set upsell.target_slug in config/agent.yaml to a real menu slug, "
                     "or leave upsell.enabled: false if you do not want one.")
    menu = load_menu(settings.agent_get("menu", []))
    slugs = {m.slug for m in menu}
    if target not in slugs:
        return Check("upsell", FAIL, f"upsell.target_slug '{target}' is not a menu item",
                     "Fix upsell.target_slug in config/agent.yaml to match a slug under menu:.")
    return Check("upsell", PASS, f"target: {target} "
                 f"({'seed enabled' if upsell.get('enabled', True) else 'seed disabled'} - "
                 "the live value is `tools/run.py --set-rule upsell=on|off`)")


def check_prompts() -> Check:
    missing = [p for p in ("prompts/extract_order.md", "prompts/draft_confirmation.md",
                           "prompts/schemas/extract_order.json",
                           "prompts/schemas/draft_confirmation.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "extract_order.md + draft_confirmation.md + schemas present")


def check_disclosure() -> Check:
    """The EU AI Act Article 50 line every guest confirmation carries
    (`tools/engine.py:draft()`, applied before an order ever reaches the
    review queue - see docs/safety.md "Telling guests they are talking to
    AI"). Never a FAIL: a shipped generic English line is used whenever
    `knowledge/disclosure.md` is missing, so the disclosure itself is never
    absent - this check is only about whether it reads naturally in your
    own guests' languages."""
    path = REPO_ROOT / "knowledge" / "disclosure.md"
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        return Check("disclosure line", WARN,
                     "knowledge/disclosure.md not filled in - every confirmation still "
                     "carries the shipped generic English line",
                     "cp knowledge/disclosure.example.md knowledge/disclosure.md and put "
                     "it in your own guest language(s) - see workflows/90-go-live.md.")
    return Check("disclosure line", PASS, "knowledge/disclosure.md is set")


def check_schedule(settings: Settings) -> Check:
    schedule = settings.agent_get("schedule", {}) or {}
    if not schedule:
        return Check("schedule", WARN, "no schedule: block in config/agent.yaml",
                     "Copy config/agent.example.yaml - orders are time-sensitive and this "
                     "agent should run every few minutes.")
    return Check("schedule", PASS, ", ".join(schedule))


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="In-Room Dining AI - doctor")

    checks = run_checks(settings, extra=[check_menu, check_kitchen, check_upsell,
                                         check_schedule])
    checks.append(check_prompts())
    checks.append(check_disclosure())
    return print_table(checks, title="In-Room Dining AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
