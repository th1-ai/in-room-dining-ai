# knowledge/

`knowledge/property.md`, `knowledge/faq.md` and `knowledge/signature.md` ship
here as the generic scaffold every repo in this family carries, but nothing
in In-Room Dining AI reads them: there is no guest inbox and no outbound
email to sign. Skip them.

## The files worth filling in

| File | What it holds | Who reads it |
|---|---|---|
| `dietary-policy.md` | Your house rule for handling a flagged allergy/dietary note, and what the kitchen ticket line should say | `prompts/draft_confirmation.md` |
| `disclosure.md` | The EU AI Act Article 50 line appended to every guest confirmation (optional - a generic English default keeps working if you skip this; fill this in to put it in your own guest language(s)) | `core/adapters/base.py:Messaging.disclosure()`, applied in `tools/engine.py:draft()` and again (no-op if already present) by every messaging adapter's own `send()` |

```bash
cp knowledge/dietary-policy.example.md knowledge/dietary-policy.md
cp knowledge/disclosure.example.md knowledge/disclosure.md
```

`knowledge/*.md` (not the `.example.md` files) is gitignored - your kitchen's
own notes are yours.

## Where the menu actually lives

The menu, prices, categories and descriptions are **not** in `knowledge/` -
they are data, not prose, so they live in `config/agent.yaml: menu:` where a
hotel can edit them without touching a prompt (`docs/how-it-works.md`
"Design decisions" #1). Same for the kitchen ETA numbers and the sold-out
list (`config/agent.yaml: kitchen:`).

## Keeping it current

`dietary-policy.md` shapes how the kitchen ticket line reads once an order
has already been flagged for a person - it does not decide whether an order
needs a person in the first place (that gate is hard-coded, see
`docs/safety.md`). When your kitchen's cross-contamination notes or sign-off
process changes, change it here first.
