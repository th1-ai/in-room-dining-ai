# Guardrails and safety

This agent talks to your guests, posts to your PMS, and fires kitchen
tickets. Everything below is built in, not optional, and this page explains
what it does and what is left for you to decide.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads guest messages, extracts and prices the order, drafts the confirmation, and queues it. It **never** posts a folio note, **never** messages a guest and **never** tickets the kitchen. Approving, editing or rejecting an order records your decision (and teaches the agent) but sends nothing. At go-live, `python3 tools/review.py stale` clears that shadow-era queue so nothing old goes out by surprise. |
| `live` | An order you approved really fires: folio note, guest confirmation, kitchen ticket, order-log row. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it
back to `shadow` stops every outbound action immediately, mid-schedule, with
no other change. `config/agent.yaml` can be stricter than `hotel.yaml`,
never looser.

Two more brakes:

- `make run ARGS="--dry-run"` computes and prices the order and prints it,
  and writes nothing at all - not an item row, not a sequence number, not a
  `runs`/`events` row (`tools/engine.py:resolve_preview`). Use it when you
  change the menu or a prompt.
- `review.require_approval_for` in `config/agent.yaml` lists the actions
  that need a human even in live mode: `send_message`, `pms_write`,
  `sheets_write` by default. Shortening that list is how you hand the agent
  more rope, one action at a time - but see "What always needs a human"
  below for the part of this agent that no config can change.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path.

## The review queue

Nothing reaches a guest or the kitchen without passing through the queue.

```bash
make review                              # what is waiting
python3 tools/review.py show <id>         # the full order and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-message.txt
python3 tools/review.py reject <id> --reason "guest cancelled by phone"
```

An order moves `new -> pending_review` (clean) or `new -> needs_human`
(anything flagged) and then waits. Only `tools/review.py` can write
`approved`, `edited` or `rejected`; only `tools/review.py send` can write
`sending`/`sent`. A crash between "about to send" and "sent" is picked up on
the next real pass and shown to you as `failed` rather than silently
retried.

**Your edits teach it.** When you rewrite a confirmation, the before and
after are stored. Over time that is what makes the drafts sound like your
hotel instead of a machine.

## What the agent will not do

- Fire a ticket while `mode: shadow`.
- Fire an order a human has not approved, once the action needs approval.
- Guess at a menu item it cannot match, or quietly substitute one for
  another when the guest's exact request is sold out - both always
  escalate (`unmatched_item:...`, `sold_out:...`).
- Assume a dietary or allergy note is safe to ignore, whatever language it
  is written in, or invent a substitution itself - see "What always needs
  a human" below.
- Take a payment, issue a refund, or move money. There is no payments
  adapter call anywhere in this repo; "charge to room" is a note on the
  reservation for a human to turn into a real charge
  (`docs/how-it-works.md` "Design decisions" #4).
- Quote a made-up statistic. The upsell's "popular pairing" line only cites
  a real attach-rate percentage once enough completed orders exist to
  compute one (`config/agent.yaml: upsell.min_orders_for_stat`) - never a
  placeholder figure.
- Cancel or edit an order once it has fired. There is no
  `tools/review.py cancel` - a guest who wants to change or drop an order
  needs a person (`docs/how-it-works.md` "Design decisions" #10).

## What always needs a human

Enforced in code (`tools/menu_engine.py:resolve_order`), not just in the
prompt - there is no config flag that relaxes any of these:

- **A dietary or allergy note, in any form, in any language.**
  `detect_dietary()` never returns "nothing to worry about" for a
  non-empty note, whatever language it is written in. In eight languages
  (`en es fr de it pt el nl`), accent-folded, a named allergen (nut,
  gluten, dairy/lactose, shellfish, egg, soy) escalates as
  `dietary_signal:named:<allergen>`, and a note that clearly signals an
  allergy in one of those languages but does not match a specific allergen
  family escalates as `dietary_signal:ambiguous`. **Any other language, or
  anything else flagged as dietary at all** (a preference, a request this
  module has no keyword for) escalates as `dietary_signal:noted` — never
  silently dropped, never silently mislabelled, but without the
  allergen-family tag the review queue and `make report` otherwise show.
  `tools/menu_engine.py`'s `_ALLERGEN_STEMS` names exactly which eight
  languages have named-allergen coverage; README "Adding a language" shows
  how to add a ninth. This repo never guesses which allergen it is and
  never decides a request is safe to skip.
- **A requested item that is sold out** (`config/agent.yaml:
  kitchen.sold_out`) - never silently substituted, never dropped from the
  ticket without a person deciding what happens next
  (`sold_out:<slug>`).
- **An item the extraction step could not match to the real menu.** The
  model's proposed slug is checked against `config/agent.yaml: menu:`
  before anything is priced; an invented or unmatched item always
  escalates (`unmatched_item:<text>`) rather than being priced on trust.
- **A guest language `hotel.languages` does not include.** The
  confirmation is drafted in the hotel's default language instead, and the
  item still needs a person (`language_unsupported:<lang>`) - see
  `factory/workflows/build-repo.md`, "Reply only in the hotel's languages".
- **Low extraction confidence** (`config/agent.yaml:
  confidence_threshold`, default 0.55) - `low_confidence`.
- **A message that was not placing an order at all.** `extract_order` is
  explicit that a question or anything else is not an order; the item
  skips straight to `needs_human` with nothing priced
  (`not_recognized_as_order`).

Every other order is `pending_review` and still waits for a human to
approve before anything is sent - "always needs a human" above is about
which orders get flagged more urgently, not about which orders skip review.
Nothing here ever reaches `auto_sent`.

## Data handling

**What leaves your machine.** With `llm.provider: anthropic` or
`claude-code`, two prompts per order go to Anthropic: `extract_order`
(the guest's message plus the menu) and `draft_confirmation` (the resolved,
priced order plus any dietary note). With `llm.provider: mock` or
`interactive`, nothing leaves the machine at all.

**What is stored, and where.** Everything lives in `data/` inside this
folder: `agent.db` (SQLite - every order, every decision, every dietary
note), `logs/*.jsonl`, `exports/`. `data/` is gitignored. There is no cloud
service behind this repo and no telemetry.

**Dietary and allergy notes are the sensitive data here**, alongside guest
phone numbers. `core/redact.py`'s card/IBAN redaction still runs on every
string that flows through this agent (the same shared runtime as every repo
in the family), but it is not the meaningful protection for a kitchen order
- keeping `data/agent.db` off a shared machine, and setting
`privacy.retention_days` to something short, is.

**Retention.** `privacy.retention_days` (default 365) is how long processed
orders stay in the database. Deleting `data/agent.db` deletes everything the
agent knows, including the live kitchen board - it starts clean on the next
run.

## GDPR, in practice

If you are in the EU or handle EU guests' data, the short version:

- **You are the controller.** This software runs on your machine, under
  your control, on your data. TH1 does not receive it.
- **Your model provider is a processor.** If you use the `anthropic` or
  `claude-code` provider, Anthropic processes guest data (including any
  dietary/allergy note) on your behalf. Check their data processing terms
  and record them in your processing register.
- **Purpose and minimisation.** The agent sees a guest's message, their room
  or location, and whatever dietary/special-request text they wrote. Do not
  put staff phone numbers or full guest histories in `knowledge/`.
- **Special-category data.** A health-related allergy note is
  special-category personal data under GDPR Article 9. Processing it to
  fulfil the order (vital interest / explicit provision by the guest) is
  usually fine, but keep it out of anywhere beyond `data/agent.db` and the
  kitchen ticket that needs it, and make sure `privacy.retention_days`
  reflects that.
- **Right to erasure.** A guest asking to be forgotten means removing their
  rows from `data/agent.db` (`items` where the payload mentions them) and
  `data/exports/orders.csv`. Ask your Claude session: *"Delete every item in
  data/agent.db whose payload mentions this guest's name or room, and tell
  me how many rows you removed."*
- **Retention.** Set `privacy.retention_days` to what your own policy says,
  not to the default.

This is a practical summary, not legal advice.

## Telling guests they are talking to AI

The EU AI Act (Article 50) requires that a person is told when they are
interacting with an AI system, unless it is obvious. Whether it applies to
you depends on where you and your guests are, but it is good practice
everywhere and guests react well to it.

There is no `knowledge/signature.md` sign-off in this agent (there is no
email) - chat has its own, equivalent mechanism instead, and this repo
wires it in rather than leaving it for you to add. Every messaging
adapter's own `send()` call (`Messaging.with_disclosure()` in
`core/adapters/base.py`, used by `messaging_mock.py`,
`messaging_unipile.py` and `messaging_webhook.py` alike) appends a
disclosure line read from `systems.messaging.options.disclosure_file`
(default `knowledge/disclosure.md`) to every guest-facing send, once,
never duplicated - the messaging-channel equivalent of
`Email.with_signature()`.

That alone would only prove itself once you go live and something actually
sends. So `tools/engine.py:draft()` calls the same `with_disclosure()`
logic **before** an order ever reaches the review queue, with a fallback:
if `knowledge/disclosure.md` has not been filled in yet, it uses a shipped
generic English line instead of nothing at all
(`tools/engine.py: DEFAULT_DISCLOSURE`):

> This message was drafted with AI assistance and checked by our team;
> reply and a person will help you.

That means the person approving a draft sees, and approves, the exact text
the guest will receive - not something appended invisibly at send time -
and a fresh clone with zero setup still never sends a confirmation without
the line. `make doctor`'s `disclosure line` check tells you whether you are
still on the generic default.

**Put it in your own language(s).**

```bash
cp knowledge/disclosure.example.md knowledge/disclosure.md
```

then translate it - one sentence per language you serve, if more than one.
The shipped default is English only and will read strangely to a guest
writing in Greek, Dutch, or anything else. `workflows/90-go-live.md`'s
checklist asks about this before you flip `mode: live`.

Keep the escape hatch in the sentence. A guest who wants a human should
never have to work out how to get one.

## Subscription or API: an honest note

Two ways to pay for the reasoning:

**Your Claude Code subscription** (`llm.provider: claude-code` or
`interactive`). Flat monthly cost, no per-message billing. This is
genuinely the cheapest way to run a small hotel's ordering desk - two calls
per order (extract + confirm), and `--dry-run` uses only one.

The caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to
automated use of it. A handful of orders an hour is a normal way to work.
Pointing a busy dinner service at it is closer to the edge, and you will hit
rate limits at the worst moment. Read the terms and decide for yourself.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token, no
ambiguity about automated use, proper rate limits, and usage you can
attribute. This is the right answer for a property doing real volume.
`make report` shows what you are spending.

Start on the subscription while you are learning what the agent does. Move
to the API when it becomes part of how the kitchen actually runs.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`.
   Every outbound action stops on the next pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now in-room-dining-ai-take_orders.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.
