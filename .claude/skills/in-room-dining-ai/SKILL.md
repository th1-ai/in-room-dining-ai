---
name: in-room-dining-ai
description: Run In-Room Dining AI ("The Butler") — Takes room-service and poolside orders in any language — from the QR card on the room desk, web chat, or the Voice AI line.. Use when the user asks to run the agent, check what is waiting for review, approve or reject a draft, or asks how the agent is doing. Trigger phrases: "run The Butler", "/in-room-dining-ai", "check the queue", "what is waiting for me", "approve that draft".
---

# In-Room Dining AI

Takes room-service and poolside orders and works the review queue.
Everything happens from the repo root; every command below exists and
works.

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-take-orders.md`
for the main loop. If the user has never run this agent, start at
`workflows/00-setup.md` instead and walk them through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines are
worth mentioning but do not stop the run.

**2. Take new orders.**

```bash
make run                        # one pass over new guest messages
make run ARGS="--limit 5"       # just the first five
make run ARGS="--dry-run"       # compute and price, write nothing
```

If `llm.provider` is `interactive`, the run will stop with exit code 3 and park
a prompt in `data/pending/`. That is expected - it can happen once for
`extract_order` and again for `draft_confirmation`. Read the `*.prompt.md`,
write your answer as JSON to the matching `*.answer.json` following the
schema exactly, then run the same command again; it resumes at whichever
stage is missing, it never re-extracts an order it already parsed.

**3. Show what is waiting.**

```bash
make review
python3 tools/review.py show <id>
```

Summarise it for the user in plain language: who ordered what, from which
room or location, the total, and why it stopped for a person if it did (a
dietary note, a sold-out item, an item nothing on the menu matched, a
language this hotel does not configure, low confidence, or a message that
was not an order at all). Do not paste raw JSON at them. **A dietary or
allergy gate always needs the user's own judgement** - never approve one on
trust.

**4. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file <path> [--kitchen-line "..."]
python3 tools/review.py reject <id> --reason "<why>"
```

Read the confirmation back to them before approving. If they want changes,
write the new guest message to a file and use `edit` — the before/after is
stored and is what teaches the agent their voice.

**5. Fire what was approved, then work the kitchen board.**

```bash
python3 tools/review.py send
python3 tools/kitchen.py board
python3 tools/kitchen.py advance <ref>
```

`send` posts the folio note, confirms the guest, and tickets the kitchen -
blocked entirely in shadow mode. Once a ticket has fired, `advance` moves it
through `placed -> preparing -> on the way -> delivered` and pushes the
guest a status update each step.

**6. Report.**

```bash
make report
```

## Rules

- **Never fire a ticket in shadow mode**, and never work around a blocked write.
  The error message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through.
- **Confirm before anything irreversible** — a guest message, a PMS folio note —
  even when it is approved.
- **A dietary/allergy note, a sold-out item, an unmatched item, an unsupported
  language, or low confidence always need a person.** There is no config that
  turns any of these off - do not suggest one.
- **Never print or paste a credential.**
- If a run fails, read the whole error, fix the cause, re-run, and note what you
  learned in `workflows/99-troubleshooting.md`.
