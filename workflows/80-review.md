# Workflow: working the review queue

Objective: turn a queued order into a decision - approve, edit, or reject -
and, once approved, actually fire it: post the folio note, confirm the
guest, and ticket the kitchen.

Nothing reaches a guest or the kitchen without going through this. `mode:
shadow` blocks `send_message` and `pms_write` for everything except an item
you have approved or edited; see `docs/safety.md` for the full guard.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   ```
   Each line shows the item id, its status (`pending_review` or
   `needs_human`), the order reference, the room or location, the total, and
   which gate fired if any (`unmatched_item:...`, `dietary_signal:...`,
   `sold_out:...`, `language_unsupported:...`, `low_confidence`,
   `not_recognized_as_order`).

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   This prints the original message, the resolved order (matched items,
   totals, ETA, dietary finding, upsell offer), the drafted guest
   confirmation and kitchen line, and the full event history. Summarise it
   for the hotel in plain language - who ordered what, from where, what the
   agent drafted, and why it stopped for a person - do not paste the raw
   JSON at them.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file guest-message.txt [--kitchen-line "..."]
   python3 tools/review.py reject <id> --reason "guest cancelled by phone"
   ```
   `edit` rewrites the guest-facing confirmation (and, optionally, the
   kitchen's dietary line) and records the before/after pair as a
   `learnings` row.

   **A dietary or allergy note always needs your own judgement here** -
   confirm with the guest or the kitchen before approving, never approve on
   trust because the total looks right.

4. **Fire what was approved.**
   ```bash
   python3 tools/review.py send
   ```
   This claims everything `approved`/`edited` and performs three writes, in
   order: a folio note (`pms.add_note`, skipped if the PMS could not confirm
   a reservation for the room), the guest confirmation
   (`messaging.send`), and the kitchen ticket (`messaging.notify_staff`),
   then logs a row to the order-log sheet. In `mode: shadow` nothing is sent
   at all, approved or not: the guard blocks it with a readable message, the
   item returns to `approved` ("approval kept"), and it will only actually go
   out after you flip `mode: live` (clear the old queue first with
   `python3 tools/review.py stale` - see `workflows/90-go-live.md`).

5. **Once fired, work the kitchen board.**
   ```bash
   python3 tools/kitchen.py board
   python3 tools/kitchen.py advance <ref>
   ```
   Each advance moves the ticket one step (`placed -> preparing -> on the
   way -> delivered`) and pushes the guest a short status line in their own
   confirmed language.

6. **A failed send.** `send` marks the item `failed` with the error
   attached. If an earlier write (say, the folio note) already succeeded
   before a later one failed, `data/exports/pms_writes.csv` (or your real
   PMS) shows it - check before retrying so a mistake does not get charged
   twice.
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it for another attempt once the cause is fixed (usually a
   messaging credential - `make doctor` will say which).

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- A dietary/allergy gate, a sold-out item, an unmatched item, an unsupported
  language, low confidence, or a message that was not an order at all are
  all `needs_human` on purpose (`docs/safety.md`) - never approve one
  without actually reading it.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
