# Workflow: shadow to live

Objective: decide, together with the hotel, whether In-Room Dining AI is
ready to fire approved tickets on its own instead of only drafting them -
and make the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly
what changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/agent.yaml: menu:` has your real items and prices, not the
      shipped starter menu. `config/hotel.yaml` has the real property name,
      currency and languages.
- [ ] `knowledge/dietary-policy.md` exists (not the shipped example) and the
      kitchen has actually agreed the cross-contamination notes in it.
- [ ] `knowledge/disclosure.md` reads naturally in your guests' own
      language(s) - the EU AI Act Article 50 line every confirmation
      carries (`make doctor`'s `disclosure line` check). A generic English
      default ships and keeps every confirmation compliant even if you
      skip this, but it will read oddly to a guest who does not read
      English - see `docs/safety.md`.
- [ ] At least a few days of real `make run` passes have gone through the
      review queue, not just the demo fixtures - across enough guest
      languages and enough dietary/allergy cases to trust the extraction
      and the confirmation copy.
- [ ] The hotel understands that a dietary/allergy note, a sold-out item, an
      unmatched item, an unsupported language, low confidence, and a
      message that was not an order at all **always** need a person - going
      live never changes that (`tools/menu_engine.py:resolve_order`,
      `docs/safety.md`). There is no config that turns any of these off.
- [ ] A real messaging channel is connected (`systems.messaging.adapter:
      unipile` or `webhook`) and `make doctor` shows it healthy - going live
      on the `mock` adapter would only ever touch the fixtures.
- [ ] The hotel has decided who is on the review queue during service hours
      - this agent never sends without a person approving first, live or
      not.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` (`config/agent.yaml`) still lists
   `send_message` and `pms_write` by default - it should. Going live means
   **approved orders get fired**, not that the agent starts firing
   unapproved ones. There is no config that changes that.
3. Clear the shadow-era backlog so nothing stale goes out by surprise:
   ```bash
   python3 tools/review.py stale
   ```
4. Run `make doctor` again to confirm.
5. Run one real pass and manually watch a ticket fire:
   ```bash
   make run ARGS="--limit 1"
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   python3 tools/kitchen.py board
   ```
6. Tell the hotel exactly what just changed: an approved order now actually
   posts a folio note, messages the guest, and tickets the kitchen the next
   time someone (or a scheduled job) runs `python3 tools/review.py send` -
   it is still never automatic before that approval, and every gate in
   `docs/safety.md` still forces a person regardless of mode.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action on the next pass, mid-schedule, with no other
change required.
