# Workflow: taking orders

Objective: run one pass over new guest messages and see what In-Room Dining
AI made of them.

## Inputs

- A configured `systems.messaging.adapter` (`mock` by default - see
  `workflows/00-setup.md` step 6 to connect WhatsApp or a webhook).
- `config/agent.yaml`'s `menu:`, `kitchen:`, `upsell:` and
  `confidence_threshold` - the defaults work for the demo; fill in your own
  menu before running against a real channel.

## Steps

1. **Run one pass.**
   ```bash
   make run
   make run ARGS="--limit 5"       # just the first five messages
   make run ARGS="--dry-run"       # compute and print, write nothing
   ```
   Each new message is extracted (`prompts/extract_order.md`) into items, a
   quantity, a dietary note and a language, then priced and gated
   deterministically (`tools/menu_engine.py:resolve_order` - menu match,
   sold-out check, kitchen ETA, dietary gate, upsell decision), then drafted
   (`prompts/draft_confirmation.md`) into a guest-facing confirmation and a
   kitchen ticket line. See `tools/engine.py` and `docs/how-it-works.md`.

2. **If `llm.provider` is `interactive`,** the run stops with exit code 3 and
   parks a prompt in `data/pending/`. Read `*.prompt.md`, write your answer
   as JSON to the matching `*.answer.json` exactly matching the schema
   shown, and run the same command again. Do this for `extract_order` and
   then again for `draft_confirmation` if it also pends.

3. **See what happened.**
   ```bash
   make review
   ```
   A clean order (every item matched, no dietary note, a supported language,
   nothing sold out, confident extraction) is `pending_review`. Anything else
   - a dietary/allergy note of any kind, a sold-out item, an item that could
   not be matched to the menu, a guest language this hotel does not
   configure, low confidence, or a message that was not an order at all - is
   `needs_human`, on purpose (`docs/safety.md`).

4. **Work the queue.** `workflows/80-review.md` covers approve / edit /
   reject / send in full.

5. **Once a ticket has fired,** move it through the kitchen:
   ```bash
   python3 tools/kitchen.py board
   python3 tools/kitchen.py advance <ref>
   ```
   Each advance pushes the guest a short status update in their own
   confirmed language. See `docs/how-it-works.md` "Data model".

6. **Keep it running.**
   ```bash
   make watch                       # loop on the configured interval
   ```
   Or schedule it - `make schedule ARGS="--all"` prints ready-to-install
   snippets. `config/agent.yaml`'s `schedule.take_orders` documents the
   interval this repo was built around (every 5 minutes - orders are
   time-sensitive).

## Edge cases

- **No new messages.** `make run` prints `0 items processed, 0 drafted, 0
  sent` and exits 0. Nothing to do.
- **A message the model cannot extract cleanly.** `core.llm` raises
  `LLMSchemaError` rather than accept a bad answer; the item is queued as
  `needs_human` with the error recorded, instead of guessing.
- **A re-run sees the same message again.** `tools/engine.py` skips anything
  the store has already seen - see `core.store.Store.upsert_item` and
  `docs/how-it-works.md` "Idempotency".
- **The kitchen is busier than usual.** `tools/menu_engine.py:kitchen_eta`
  grows the quoted delivery time once more than `kitchen.
  capacity_before_delay` tickets are currently `placed`/`preparing` - this
  is automatic, nothing to run by hand.
