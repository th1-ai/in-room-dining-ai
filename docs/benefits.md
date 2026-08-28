# Measuring the benefit

## The business case

From the roster this repo is built from:

**Why it matters.** In-room dining is the highest-margin F&B channel and the
most friction-heavy to order from. Removing the phone call lifts order
volume.

**What to expect.** Every order captured accurately with an upsell attached
and the guest kept informed to the door - no phone call, no front-desk
relay.

**ROI figure.** `+14%` In-room dining revenue (revenue).

That is a property-level claim built up from mechanisms this repo actually
implements: a lower-friction ordering channel (a WhatsApp/webchat message
instead of a phone call that may go unanswered during service), one
well-timed upsell offer attached to almost every order, and a guest who
knows what is happening without calling down to ask. Whether a specific
property sees 14% depends on how much phone-based ordering it is replacing
and how much of that traffic simply did not happen before - there is no
formula in this repo that outputs a percentage, and it should not claim one.

## What to measure

```bash
make report
python3 tools/report.py --json
```

- **Orders fired vs. waiting for a person** (`sent` vs. `waiting_on_human`) -
  the plainest volume signal. In shadow mode this is always zero fired;
  compare it against your own phone-order log for the same period as a
  baseline.
- **Revenue on fired tickets** and **average order value** - `make report`
  computes both straight from `data/agent.db`, in your own currency.
- **Upsell attach rate** (`upsell_attach_pct`) - the real percentage of
  fired orders that included the upsell target item, computed from your own
  history, never a placeholder figure
  (`docs/how-it-works.md` "Design decisions" #8). Compare it against
  `config/agent.yaml: upsell.min_orders_for_stat` orders' worth of data
  before drawing conclusions from a small sample.
- **Why orders needed a person** - the breakdown by gate
  (`dietary_escalations`, `unmatched_item_escalations`,
  `sold_out_escalations`, `language_escalations`, `not_an_order`). A rising
  `unmatched_item` count usually means the menu in `config/agent.yaml`
  needs a new item or a clearer description, not that guests are ordering
  strangely. A rising `sold_out` count means the kitchen's `sold_out` list
  needs updating more often, or an inventory feed is worth wiring in.
- **LLM calls and cost** - two calls per order (`extract_order`,
  `draft_confirmation`); `--dry-run` uses only the first. This number
  should track order volume roughly 2:1; if it does not, something is
  reprocessing the same message.

## What this repo cannot claim

Honesty over a bigger number, per `docs/how-it-works.md`'s design
decisions:

- **Kitchen capacity is a formula, not a live feed.** `kitchen.
  capacity_before_delay`/`delay_step_minutes`/`max_eta_minutes` grow the
  quoted ETA under load, but they are house constants you calibrate, not a
  reading from an actual kitchen queue or a KDS. Do not tell a hotel this
  repo forecasts real prep time.
- **The kitchen ticket is simulated.** `messaging.notify_staff()` delivers
  it however your messaging adapter can (a WhatsApp group, a webhook into
  your own printer bridge); there is no KDS callback, no "order accepted by
  the kitchen" acknowledgement, and no rejected/delayed state.
- **"Charge to room" posts a note, not a charge.** `pms.add_note()` is the
  only PMS write; nothing in this repo moves money or posts to a folio
  ledger. A person still has to turn that note into a real charge unless
  you have written your own folio-charge adapter method
  (`docs/integrations.md`).
- **No cancellation or edit path.** Once a ticket fires it can only move
  forward through `placed -> preparing -> on the way -> delivered`. A guest
  who wants to change or cancel needs a person; `make report`'s numbers do
  not account for orders handled that way outside this system.

## The counterfactual, honestly

The realistic comparison is not "AI vs. nothing" - most properties already
take in-room dining orders by phone, and a phone that rings during a busy
service is a phone that sometimes goes unanswered. The case for this agent
is: an order a guest can place in their own language without waiting for
someone to pick up, priced correctly against the real menu every time,
with a dietary note that is confirmed rather than assumed, and status
updates that mean the guest never has to call down and ask "where is my
food." It replaces the phone call and the manual relay to the kitchen, not
a person's judgement about a flagged allergy or a sold-out item - see
`docs/safety.md` "What the agent will not do".
