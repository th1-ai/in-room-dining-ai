# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

In-Room Dining AI uses three system families: PMS (room identity and the
folio note), Messaging (guest orders in, confirmations and status pushes and
the kitchen ticket out) and Sheets (the order log). It does not use Email at
all - there is no guest inbox and nothing to sign.

## Status

### PMS - `systems.pms.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/hotel/reservations.json`, keyed by room number. What `make demo` uses. |
| `csv` | universal | a CSV export | Reads `data/imports/*.csv`. **Start here.** Works with every PMS. |
| `cloudbeds` | built | OAuth app + refresh token | Live reads and writes. |
| `cli` | universal | a JSON-speaking CLI | Advanced. Bridges to a vendor command line tool. |

Only two PMS calls happen at all, both in `tools/engine.py:resolve_room`:

1. `pms.get_reservation(room_number)` - the `mock` adapter's own trick: its
   fixture reservations are keyed by room number as the id, so this never
   depends on today's date.
2. If that returns nothing, `pms.list_in_house(today)` filtered to
   `room_id == room_number` - what a real PMS (`csv`/`cloudbeds`) needs,
   using the actual date at call time.

If neither confirms a reservation, the order still queues - it just has no
reservation id, so the folio note step is skipped on send (a person can post
it by hand). **The only write is `add_note()`** - an itemised note on the
guest's reservation standing in for "charge to room" (`docs/how-it-works.md`
"Design decisions" #4). No PMS in this family exposes a real folio-charge
API; if yours does, add a method and call it from `tools/review.py:cmd_send`
instead.

**`csv` - the one that always works.** Export from your PMS and drop the
files in `data/imports/`:

- `reservations.csv` - `id, status, check_in, check_out, room_type_id,
  room_type_name, room_id, adults, children, source, total, balance,
  currency, guest_email, guest_first_name, guest_last_name, guest_phone,
  guest_country`. For `get_reservation(room_number)` to work the way the
  fixtures do, set `id` to the room number too - otherwise the agent falls
  back to the `list_in_house` lookup, which needs `room_id` filled in and
  today's date to actually fall inside `check_in`/`check_out`.
- `guests.csv` - `id, first_name, last_name, email, phone, country,
  language, vip`.

Headers are matched loosely: `checkIn`, `check_in` and `Check In` all work,
and extra columns are kept. Dates must be `YYYY-MM-DD`.

In CSV mode the agent cannot write back to your PMS, so the folio note is
appended to `data/exports/pms_writes.csv` with everything a person needs to
apply it by hand. That is a feature: it is how you check the agent's
judgement before you give it write access.

**`cloudbeds`.** Create an app in the Cloudbeds developer portal, authorise
it once against your property, and put the result in `.env`:

```
CLOUDBEDS_CLIENT_ID=
CLOUDBEDS_CLIENT_SECRET=
CLOUDBEDS_REFRESH_TOKEN=
CLOUDBEDS_PROPERTY_ID=
```

Scopes: `read:reservation`, `write:reservation`, `read:guest`, `read:room`,
`read:hotel`.

**`cli`.** If your PMS already has a command line tool that prints JSON,
point at it. See the profiles at the top of `core/adapters/pms_cli.py`.

### Messaging - `systems.messaging.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/messages.json`. What `make demo` uses. |
| `unipile` | built | your own UniPile account | WhatsApp on your own number. |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, your own QR-ordering page's backend, or your own endpoint. |

This is the whole guest-facing surface: `fetch_new()` is where an order
comes in (from WhatsApp, a web-chat widget, or the transcript the Voice AI
line hands off - see `docs/how-it-works.md` "Design decisions" #7), `send()`
delivers the order confirmation and every later kitchen status push, and
`notify_staff()` is the kitchen ticket (`docs/how-it-works.md` "Design
decisions" #5). Every message carries a `room_number` (and an optional
`location` for poolside orders) in its raw payload - see
`fixtures/inbound/messages.json` for the shape a real integration needs to
produce.

**`unipile`.** You create the account, you connect your number by QR code,
you own the credentials: `UNIPILE_DSN`, `UNIPILE_API_KEY`,
`UNIPILE_ACCOUNT_ID`, `UNIPILE_STAFF_CHAT_ID` (where the kitchen ticket
lands). WhatsApp Business policy limits what you may send outside a
guest-initiated window; read your provider's rules before turning this on.

**`webhook`.** Send-only, so pair it with something that can also deliver
*inbound* messages to this agent (most QR-ordering pages and chat widgets
already have a webhook of their own) - `fetch_new()` is not implemented for
this adapter. Set `MESSAGING_WEBHOOK_URL` and the agent POSTs `{chat_id,
text, kind, hotel, sent_at}` for every guest message and kitchen ticket.

### Sheets - `systems.sheets.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Writes `data/exports/orders.csv` - one row per fired ticket. |
| `google` | built | service account JSON | A live shared spreadsheet. |

Every fired order appends one row: ref, sent-at, room, location, guest name,
items, total, currency, and the gates that fired before a human approved
it - `tools/report.py` computes its own numbers straight from `data/
agent.db`, so this sheet is for a human to skim or export, not something the
agent reads back.

For `google`: enable the Sheets API, create a service account and a JSON
key, save it as `service_account.json`, and share your spreadsheet with the
service account's email address as an Editor. Set
`systems.sheets.spreadsheet_id` to the long id from the sheet's URL.

### Everything else

`pos`, `accounting`, `reviews`, `calendar`, `payments`, `procurement` and
`locks` are **stubs**: the interface exists, nothing is implemented. Calling
one raises an error that tells you exactly this. This agent never calls any
of them - the closest thing to a POS write is the folio note above. If you
later want a real kitchen-display-system callback instead of a WhatsApp
staff message, that is the integration worth adding; see below.

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose, and your Claude Code session can do this
with you in an afternoon. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need a PMS adapter for **<your system>**. Its API docs are at **<url>** and
> I have credentials in `.env` as `<VAR names>`. Copy `core/adapters/pms_csv.py`
> as the shape, implement `ping`, `capabilities` and the read methods first,
> register it in `core/adapters/__init__.py`, and stop before the write methods
> so I can check the reads with `make doctor`.

### The five steps

**1. Copy the closest existing adapter.**
`core/adapters/pms_csv.py` for a PMS, `messaging_webhook.py` for a chat
channel. They are short and heavily commented.

**2. Implement `ping()` and `capabilities()` first.**

```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```

`make doctor` reads both. Getting them right first means the rest of the work
has a feedback loop.

**3. Implement the reads.** Map the vendor's fields onto the dataclasses in
`core/adapters/base.py` (`Reservation`, `Guest`, `ChatMessage`). Put anything
you do not map into `.extra` rather than dropping it. Dates are ISO
`YYYY-MM-DD`. Money is a float in the hotel's currency.

**4. Implement the writes, each with the guard.**

```python
from core.adapters.base import guarded_write

@guarded_write("pms_write")
def add_note(self, reservation_id: str, text: str) -> dict:
    ...
```

The decorator is not optional. Without it your adapter can write while the
agent is in shadow mode, which defeats the entire safety model. The action
name should be one of the values in `review.require_approval_for`
(`config/agent.yaml`, for this repo: `send_message`, `pms_write`,
`sheets_write`).

**5. Register it.** One line in `core/adapters/__init__.py`:

```python
REGISTRY["pms"]["yoursystem"] = "core.adapters.pms_yoursystem:YourSystemPMS"
```

Then set `systems.pms.adapter: yoursystem` in `config/hotel.yaml` and run
`make doctor`.

### Rules that matter

- **`ping()` never raises.** It returns `HealthCheck(ok=False, ...)` with a
  hint. A broken adapter must still produce a readable doctor table.
- **Every write is decorated.** No exceptions.
- **Rate limits belong in the adapter.** Use `core/adapters/_http.py:RateLimiter`.
  Retry 429 and 5xx with backoff; never retry a 4xx.
- **Never log a credential.** `core/log.py` masks anything whose key looks like a
  secret, but do not rely on it.
- **Redact on ingestion.** Any guest-written text goes through
  `core.redact.redact()` before it is stored or shown to a model.
- **Write a test.** Copy `tests/test_core_adapters_mock_csv.py`. It should run
  with no network: feed your parser a fixture, check the dataclass that comes out.

### `core/` is shared

`core/` is identical in all 28 agents in this family. If you change something in
`core/`, keep it generic - a hotel-specific tweak belongs in `tools/` or in your
own adapter file, not in the shared runtime.
