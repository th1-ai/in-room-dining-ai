# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`menu`: config/agent.yaml has no menu: items.** Copy
  `config/agent.example.yaml` to `config/agent.yaml` - it ships with a
  starter menu.
- **`menu`: duplicate slug(s).** Every item needs a unique slug; the
  extraction step matches guest text against it.
- **`upsell`: upsell.target_slug '...' is not a menu item.** Fix
  `upsell.target_slug` in `config/agent.yaml` to match a real slug under
  `menu:`.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic` in `config/hotel.yaml`.
- **`llm provider`: ANTHROPIC_API_KEY is not set.** Add it to `.env`, or
  switch `llm.provider` to `claude-code` or `interactive`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail
  loud when misconfigured (a `warn` is reserved for stubs). Read the
  `detail` column - it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock` and reads
  `fixtures/inbound/messages.json` - if you deleted or renamed it, restore
  it from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose, so a fixture problem shows up immediately.

## `make run` exits with code 3

Not an error. `llm.provider: interactive` parked a prompt. Read
`data/pending/*.prompt.md`, write your answer to the matching
`*.answer.json` (JSON only, matching the schema shown, no prose, no code
fence), and run the same command again. This can happen twice per order -
once for `extract_order`, once for `draft_confirmation` - the second run
resumes at whichever stage is still missing, it never re-extracts a message
it already parsed.

## An item is stuck at `sending`

A process died between claiming an item and finishing the send.
`tools/run.py` calls `core.store.Store.reap_stuck_sending()` on every real
pass (never on `--dry-run`), which moves anything stuck for more than 30
minutes to `failed` so you see it in the queue instead of it vanishing. Use
`python3 tools/review.py retry <id>` once the cause is fixed - see
`workflows/80-review.md` for the note on checking `data/exports/
pms_writes.csv` before retrying a send whose folio note might already have
posted.

## An order comes back `needs_human` and you are not sure why

```bash
python3 tools/review.py show <id>
```
The `gates` list in the resolved order names every reason, machine-readable:
`unmatched_item:<text>` (nothing on the menu matched), `sold_out:<slug>`,
`dietary_signal:named:<allergen>` / `dietary_signal:ambiguous` /
`dietary_signal:noted` (any dietary note at all, in any language - named-
allergen family detection covers eight, see `docs/safety.md`),
`language_unsupported:<lang>`, `low_confidence`, or
`not_recognized_as_order`. None of these are configurable off.

## `tools/kitchen.py advance` says the order "has not been approved and sent
yet"

That is correct, not a bug: only a ticket that has actually fired
(`python3 tools/review.py send`, and only in `mode: live`) has a kitchen
status to advance. In `mode: shadow` nothing ever fires, so there is nothing
on the board - that is expected, see `docs/safety.md`.

## The extraction step gets an order wrong

Fix it in the review queue first (`edit`, not `reject`, so the correction is
recorded as a learning), then look at whether `prompts/extract_order.md`
needs a clearer instruction, or whether `config/agent.yaml: menu:` is
missing an item or a clearer description the model can match against.
Prompts are plain markdown - edit them directly and re-run.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id. `python3 tools/review.py show <id>` has the full event trail for one
item. If neither explains it, that is a real bug - describe exactly what you
ran and what you expected, and ask.
