# Dietary and allergen policy

`make setup` copies this to `knowledge/dietary-policy.md`. Edit that copy.
`prompts/draft_confirmation.md` reads it when it writes the kitchen ticket
line - keep it short, it is prepended to every draft.

This file does not decide whether an order needs a person: that is a hard,
non-configurable rule in `tools/menu_engine.py` (see `docs/safety.md`), not
something a hotel can turn off here. This file only shapes how the kitchen
line reads once an order has already been flagged.

## House rule

Never plate a dish against a stated allergy or intolerance without the
kitchen confirming the exact preparation first - shared fryers, shared
grills, and sauces made in batch are the usual places a "safe" dish stops
being safe. Say so plainly to the guest; do not imply certainty the kitchen
has not confirmed yet.

## What we tell the guest

- We never promise a dish is allergen-free in the confirmation message. We
  say a person will confirm with the kitchen before it goes out.
- We never suggest a substitution ourselves. That is a kitchen decision.

## What goes on the kitchen ticket

- The guest's own words for the allergy or request, not a paraphrase that
  could drop a detail.
- One line, in English, regardless of the guest's language.

## Cross-contamination notes (fill in for your kitchen)

- Fryer shared between: _______
- Nut-free prep station: yes / no
- Who signs off a dietary ticket before it leaves the pass: _______
