# Workflow: first-run setup

Objective: get In-Room Dining AI from a fresh clone to a working demo, then to
real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never overwrites
   your own copies). `make doctor` will show a `FAIL` on "hotel identity"
   right after setup - that is expected, it means the property name is still
   the shipped placeholder. Everything else should be `ok` or `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see 12 sample guest messages resolved into orders, and the line
   `DEMO OK — 12 items processed, 12 drafted, 0 sent (shadow)`. If you do not
   see that, stop and read `workflows/99-troubleshooting.md` before going
   further.

3. **Fill in the menu.** Edit `config/agent.yaml: menu:` - slugs, names,
   categories, prices, descriptions. This is the one file that matters most:
   the extraction step matches guest text against these slugs, and every
   price the agent ever quotes comes from here. Also check `kitchen:` (the
   tray charge, the base ETA, today's sold-out items) and `upsell:` (which
   item gets offered).

4. **Fill in the property.** Edit `config/hotel.yaml` (name, address,
   contact, languages, currency). `hotel.languages` order matters: the
   first entry is the reply language for a guest writing in something not
   on the list (`settings.hotel.default_language`) - put whichever
   language most of your international guests would actually read, not
   necessarily your own working language. Then:
   ```bash
   cp knowledge/dietary-policy.example.md knowledge/dietary-policy.md
   ```
   and fill in your kitchen's own cross-contamination notes and sign-off
   process - see `knowledge/README.md`. Also, optionally but worth doing
   before you go live:
   ```bash
   cp knowledge/disclosure.example.md knowledge/disclosure.md
   ```
   and put the EU AI Act disclosure line in your own guest language(s) -
   every confirmation carries a generic English version of this line
   automatically even if you skip this step, but it will read oddly to a
   guest who does not read English (`docs/safety.md`).

5. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider` starts
   as `interactive` - it asks you, in this Claude Code session, instead of
   calling a model. That costs nothing extra and is the best way to see how
   the extraction and confirmation steps reason. `docs/how-it-works.md` and
   `docs/safety.md` explain the other three providers (`mock`, `claude-code`,
   `anthropic`) and when to move to one of them.

6. **Connect real systems (optional for now).** `systems.messaging.adapter`
   starts as `mock`, which only ever sees the 12 fixtures. `systems.pms.
   adapter` also starts as `mock` (fixture reservations by room number).
   `docs/integrations.md` covers `unipile`/`webhook` for messaging and
   `csv`/`cloudbeds` for the PMS. Run `make doctor` after changing either.

7. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real, the menu has your own items, and
   `knowledge/dietary-policy.md` exists, the "hotel identity", "menu" and
   "knowledge" lines turn green. Move on to `workflows/10-take-orders.md` to
   run the loop for real.
