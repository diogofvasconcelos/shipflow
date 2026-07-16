# ShipFlow — Architecture

> Order Fulfillment System for Mercado Libre (ML) sellers. Ingests orders in real time,
> batch-downloads Mercado Envios shipping labels into a single carrier-sorted PDF,
> generates picking lists grouped by model/size, and enforces barcode-verified packing
> (scan label + scan product = confirmed pair).
>
> **Read this document, `docs/API.md`, and `CLAUDE.md` before writing any code.**
> `docs/ORCHESTRATION.md` defines the implementation task order.

## 1. Overview

- **Users**: 2 real ML sellers (the first 2 tenants), footwear-focused, shipping via
  **Mercado Envios ME2 (agência / coleta)** only. No Flex, no Full in v1.
- **Success metric**: daily dispatch time, measured before/after via timestamps the
  system records (batch created → labels ready → first check → batch completed).
- **Operators**: warehouse staff using HTMX screens on a desktop with a USB barcode
  scanner (keyboard-wedge: scanner types the code + Enter). UI text in **pt-BR**;
  code, docs, commits in **English**.
- **Multi-tenant from day 1**: shared database, `tenant_id` column on every
  tenant-owned table, isolation enforced in the repository layer (§5).

### Ecosystem boundary (EventHub / SellerOS)

ShipFlow is project 1 of 3. It must run standalone in Phase 1, and later emit events
(`new_order`, `order_shipped`) to **EventHub** (`POST /webhooks/inbound`, envelope
`{event_id, event_type, version, tenant_id, occurred_at, payload}`).

**Decoupling rule**: the ShipFlow core never calls EventHub inline. Domain services
write rows to a local `event_outbox` table **in the same DB transaction** as the state
change (transactional outbox pattern, §9). A background publisher delivers them when
`EVENTHUB_ENABLED=true`. With the flag off (Phase 1), outbox rows are still written —
they cost nothing and become backfill history when the hub goes live. The core is
never aware of the hub's availability.

## 2. Stack (fixed — do not substitute)

Python 3.12 · FastAPI · SQLAlchemy 2 async + Alembic · PostgreSQL · Redis + **Arq**
(background jobs) · HTMX + Jinja2 + **Pico.css** (operator UI — Pico and htmx are
vendored in `frontend/static/` and served via StaticFiles, no CDN at runtime; custom
CSS only where Pico can't cover, e.g. the check-station screens) · httpx (ML API) ·
pypdf (label merge)
· python-barcode (internal product labels) · bcrypt (password hashing) ·
cryptography/Fernet (token encryption at rest) · Docker Compose · GitHub Actions.
Tests: pytest + pytest-asyncio, SQLite in-memory (aiosqlite).

## 3. Data flow (end to end)

```
                     ┌─────────────────────────────────────────────────────┐
                     │                    Mercado Libre                    │
                     └───────┬──────────────────────────────▲──────────────┘
        webhook (orders_v2,  │                              │ REST (orders, shipments,
        shipments topics)    │                              │ shipment_labels, oauth)
                             ▼                              │
 ┌──────────────── FastAPI ──────────────────┐   ┌──── integrations/meli/client.py ───┐
 │ POST /webhooks/meli                       │   │ single gateway: auth injection,    │
 │  1. insert webhook_events row             │   │ token refresh, 401/429 handling    │
 │  2. enqueue Arq job                       │   └───────────────▲────────────────────┘
 │  3. return 200  (< 500 ms, no ML calls)   │                   │
 └───────────────┬───────────────────────────┘                   │
                 ▼                                               │
 ┌────────────── Arq workers (Redis) ──────────────────────────────────────┐
 │ process_meli_notification ── fetch order/shipment → upsert (idempotent) │
 │ poll_orders (cron 5 min) ──── fallback for missed webhooks              │
 │ sync_open_shipments (cron) ── detect shipped/cancelled missed by webhook│
 │ download_batch_labels ─────── per-shipment PDF + merge, partial-failure │
 │ publish_outbox (cron) ─────── deliver events to EventHub (Phase 2)      │
 └───────────────┬──────────────────────────────────────────────────────────┘
                 ▼
 ┌────────────── PostgreSQL ────────────────┐        ┌───── EventHub (Phase 2) ─────┐
 │ orders, shipments, print_batches,        │──────▶ │ POST /webhooks/inbound       │
 │ checks, event_outbox, …                  │ outbox └──────────────────────────────┘
 └───────────────┬──────────────────────────┘
                 ▼
 ┌────────────── Operator flow (HTMX screens) ──────────────────────────────────────┐
 │ 1. Orders arrive automatically (webhook/poll)                                    │
 │ 2. Operator creates the day's print batch from eligible shipments                │
 │ 3. Worker downloads labels → single PDF sorted by carrier → operator prints      │
 │ 4. Picking list grouped by (model, size) → operator picks stock                  │
 │ 5. Check station: scan label barcode → scan each product barcode → pair confirmed│
 │ 6. Handoff to ML carrier; shipment status sync marks orders shipped              │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

## 4. Database schema

Conventions: BIGINT identity PKs; `tenant_id BIGINT NOT NULL REFERENCES tenants(id)`
on every tenant-owned table; timestamps are `timestamptz` in **UTC** (display in
`America/Sao_Paulo`); money is `NUMERIC(12,2)`; JSON columns use SQLAlchemy `JSON`
with a `JSONB` PostgreSQL variant (keeps SQLite tests working); enums are `TEXT` +
`CHECK` constraints (not native PG enums — cheaper migrations). One Alembic migration
per implementation task.

### 4.1 Identity & access

```
tenants                      (exists — reference vertical slice)
  id, name, slug UNIQUE, created_at

users
  id, tenant_id, email, password_hash, role CHECK(admin|operator),
  is_active BOOL DEFAULT true, created_at
  UNIQUE (tenant_id, email)

meli_accounts
  id, tenant_id, meli_user_id BIGINT, nickname, site_id DEFAULT 'MLB',
  access_token_enc TEXT, refresh_token_enc TEXT,        -- Fernet-encrypted
  access_token_expires_at, status CHECK(active|reauth_required|disabled),
  last_refresh_at, created_at, updated_at
  UNIQUE (meli_user_id)          -- one ML account can never belong to two tenants
```

### 4.2 Orders & shipments

The **shipment is the printable/checkable unit**, not the order: ML labels are issued
per `shipment_id`, and a cart/pack (`pack_id`) groups several orders under one
shipment. All batch/picking/check logic hangs off `shipments`; `orders` exist for
listing, metrics, and event payloads.

```
shipments
  id, tenant_id, meli_account_id, meli_shipment_id BIGINT,
  meli_status,                 -- pending|handling|ready_to_ship|shipped|delivered|not_delivered|cancelled
  meli_substatus,              -- ready_to_print|printed|picked_up|… (raw from ML)
  logistic_type,               -- drop_off|xd_drop_off|cross_docking (ME2)
  carrier_name,                -- grouping key for the merged PDF (from tracking_method/carrier fields)
  tracking_number, handling_limit_at NULL,   -- ML dispatch deadline if provided
  raw JSON, created_at, updated_at
  UNIQUE (tenant_id, meli_shipment_id)
  INDEX (tenant_id, meli_status, meli_substatus)

orders
  id, tenant_id, meli_account_id, meli_order_id BIGINT, pack_id BIGINT NULL,
  shipment_id NULL REFERENCES shipments,
  meli_status,                 -- confirmed|paid|cancelled|… (raw from ML)
  buyer_nickname, total_amount NUMERIC(12,2), currency CHAR(3) DEFAULT 'BRL',
  meli_created_at, meli_last_updated_at, raw JSON, created_at, updated_at
  UNIQUE (tenant_id, meli_order_id)
  INDEX (tenant_id, meli_status)

order_items
  id, tenant_id, order_id, meli_item_id TEXT, variation_id BIGINT NULL,
  variant_id NULL REFERENCES variants,
  title, seller_sku NULL, size NULL,      -- size extracted from variation attributes
  quantity INT, unit_price NUMERIC(12,2), thumbnail_url NULL
  INDEX (order_id)
```

**Single source of truth for state**: we deliberately do **not** maintain a parallel
internal status machine mirroring ML's. `orders.meli_status` / `shipments.meli_status`
are ML's words, updated on every fetch. ShipFlow-owned state lives only where ShipFlow
owns the process: `batch_shipments.label_status`, `shipment_checks.status`,
`print_batches.status`. Dashboard states ("aguardando lote", "impresso", "conferido",
"despachado") are **derived** by joining these — never stored twice.

### 4.3 Catalog & barcodes

Products are footwear; the size ("numeração", e.g. 34–44) comes from the ML variation
attribute (`SIZE`/`TAMANHO` in `variation_attributes` / `attribute_combinations`), not
necessarily from the SKU. A `variant` is one (item, variation) pair — the physical
thing on a shelf.

```
variants
  id, tenant_id, meli_item_id TEXT, variation_id BIGINT NOT NULL DEFAULT 0,  -- 0 = no variation
  model_name,                  -- derived from item title, editable by admin
  size NULL, seller_sku NULL,
  internal_code TEXT UNIQUE,   -- "SFV" + zero-padded id, set right after insert; Code128 value
  created_at, updated_at
  UNIQUE (tenant_id, meli_item_id, variation_id)

variant_barcodes               -- N barcodes per variant (factory EAN + internal label may coexist)
  id, tenant_id, variant_id, barcode TEXT, source CHECK(ean|internal|manual), created_at
  UNIQUE (tenant_id, barcode)
```

Variants are **upserted during order ingestion** (from order item data) — no separate
listing-sync job in v1. Physical products may arrive from factories **without any
barcode**, so ShipFlow supports both paths: (a) binding an existing factory EAN to a
variant ("teach mode", §7.4), and (b) generating printable internal Code128 label
sheets (value = `internal_code`).

### 4.4 Print batches & labels

```
print_batches
  id, tenant_id, code,                        -- e.g. "2026-07-13-1", unique per tenant
  status CHECK(created|downloading|ready|ready_with_failures|completed|cancelled),
  created_by_user_id, merged_pdf_path NULL,
  created_at, labels_ready_at NULL, checking_started_at NULL,
  completed_at NULL, cancelled_at NULL        -- these timestamps ARE the success metric
  UNIQUE (tenant_id, code)

batch_shipments
  id, tenant_id, batch_id, shipment_id,
  label_status CHECK(pending|ok|failed|excluded) DEFAULT 'pending',
  label_error NULL, label_attempts INT DEFAULT 0,
  label_path NULL,                            -- individual PDF on disk
  sort_position INT NULL                      -- page order in the merged PDF
  UNIQUE (batch_id, shipment_id)
```

A shipment may appear in at most one **non-terminal** batch. PostgreSQL can't express
that across tables, so it is enforced in the service layer: batch creation runs in one
transaction that re-checks membership (`SELECT … FOR UPDATE` on the shipments rows)
before inserting `batch_shipments`.

### 4.5 Barcode check (conferência)

```
shipment_checks                -- one per non-excluded shipment, created when labels finish
  id, tenant_id, batch_id, shipment_id,
  status CHECK(pending|in_progress|completed|mismatch_hold) DEFAULT 'pending',
  claimed_by_user_id NULL, claimed_at NULL,   -- operator lock (§7.4)
  version INT DEFAULT 0,                      -- optimistic concurrency
  completed_at NULL
  UNIQUE (batch_id, shipment_id)

check_items                    -- expected contents of the shipment
  id, tenant_id, shipment_check_id, order_item_id, variant_id NULL,
  qty_expected INT, qty_checked INT DEFAULT 0,
  manual BOOL DEFAULT false, completed_at NULL

check_events                   -- append-only audit trail, never updated
  id, tenant_id, batch_id, shipment_check_id NULL,
  event_type CHECK(label_scan|item_scan|mismatch|unknown_barcode|barcode_bound|
                   manual_override|takeover|completed|blocked_cancelled),
  barcode_raw NULL, variant_id NULL, user_id, created_at
  INDEX (tenant_id, created_at)
```

### 4.6 Integration plumbing

```
webhook_events
  id, provider DEFAULT 'meli', topic, resource, meli_user_id BIGINT,
  payload JSON, status CHECK(received|processed|skipped|failed) DEFAULT 'received',
  error NULL, received_at DEFAULT now(), processed_at NULL
  INDEX (topic, resource); partial INDEX (status) WHERE status = 'received'

poll_cursors
  meli_account_id UNIQUE, orders_last_polled_at

event_outbox                   -- transactional outbox for EventHub (§9)
  id, tenant_id, event_id UUID DEFAULT gen_random_uuid(), event_type, version INT DEFAULT 1,
  occurred_at, payload JSON,
  status CHECK(pending|delivered|failed|dead) DEFAULT 'pending',
  attempts INT DEFAULT 0, next_attempt_at DEFAULT now(), last_error NULL, delivered_at NULL
  INDEX (status, next_attempt_at)
```

### 4.7 Relationships (summary)

```
tenants 1─N users
tenants 1─N meli_accounts 1─N orders N─1 shipments (via orders.shipment_id; pack orders share one shipment)
orders 1─N order_items N─1 variants 1─N variant_barcodes
print_batches 1─N batch_shipments N─1 shipments
print_batches 1─N shipment_checks 1─N check_items (N─1 order_items)
shipment_checks 1─N check_events
```

## 5. Multi-tenancy (explainer)

**Model chosen**: shared schema, `tenant_id` column, application-level isolation.
Alternatives (schema-per-tenant, database-per-tenant) add operational cost that 2–10
tenants never justify; PG row-level security adds policy complexity we don't need when
all access goes through one codepath.

How isolation is enforced — three rules, two narrow exceptions:

1. **Resolution**: `tenant_id` comes from the authenticated session (the `users` row),
   never from a query param or form field. For webhooks (no session), tenant is
   resolved server-side: notification `user_id` (the ML seller id) →
   `meli_accounts.meli_user_id` → `tenant_id`.
2. **Repositories require tenancy**: every repository method for tenant-owned tables
   takes `tenant_id` as its first argument and includes it in the WHERE clause.
   There is no `get(id)` without tenant — a cross-tenant id guess returns 404.

   *Exception* (`app/repositories/user.py`): two module-level functions,
   `find_user_for_login(session, email)` and `get_user_by_id(session, user_id)`,
   look up users without a `tenant_id` — because at that point in the request
   there isn't one yet. `POST /login` only has an email/password, and the
   session cookie (`app/api/deps.py::require_user`) carries only `user_id`
   (never `tenant_id` — set once at login, never trusted from elsewhere). Both
   are the same "resolve tenant from a trusted, server-derived value" pattern
   as rule 1's webhook routing, just applied to the login/session boundary
   instead of the webhook boundary. Every other `UserRepository` method keeps
   the tenant_id-first rule.
3. **Uniqueness is per tenant** (`UNIQUE(tenant_id, meli_order_id)` etc.), except
   `meli_accounts.meli_user_id` which is globally unique (an ML account belongs to
   exactly one tenant — this is also what makes webhook routing unambiguous).
   `users.email` is unique per tenant only (`UNIQUE(tenant_id, email)`), so in the
   rare case the same email is registered under two tenants, login resolves to
   whichever row is found first — acceptable for the current 2-tenant scale.

Background jobs receive explicit ids (`webhook_event_id`, `batch_id`) and re-derive
`tenant_id` from the row — jobs never trust a tenant id passed in from outside.

## 6. Mercado Libre integration

All ML traffic goes through **`app/integrations/meli/client.py`** — the single
gateway. No other module imports httpx or knows ML URLs. The client exposes typed
methods (`get_order`, `get_shipment`, `get_label_pdf`, `search_orders`,
`exchange_code`, `refresh_token`) and owns auth injection, refresh, retry, and rate
limiting.

### 6.1 OAuth 2.0 (explainer — first marketplace OAuth, read carefully)

ShipFlow is a registered ML application (one `MELI_CLIENT_ID`/`MELI_CLIENT_SECRET`
for the whole system — **app credentials are global, tokens are per seller account**).
Each tenant connects their seller account via authorization code flow:

1. Admin clicks "Conectar conta ML" → redirect to
   `https://auth.mercadolivre.com.br/authorization?response_type=code&client_id=…&redirect_uri=…&state=…`.
   `state` = signed value containing `tenant_id` + nonce (signed with `SECRET_KEY`);
   verified on callback to prevent CSRF and cross-tenant linking.
2. Callback receives `code` → client exchanges it at `POST /oauth/token`
   (`grant_type=authorization_code`) → `{access_token, refresh_token, expires_in, user_id}`.
3. Fetch `/users/me` for the nickname, upsert `meli_accounts` (keyed by `meli_user_id`),
   store both tokens **Fernet-encrypted** (`TOKEN_ENCRYPTION_KEY` env; never log tokens).

**Token lifecycle — the part that bites**: ML access tokens last ~6h; refresh tokens
are **single-use and rotate** — every refresh returns a *new* refresh token and
invalidates the old one. Two workers refreshing concurrently would race: the loser
holds a dead refresh token and the account is bricked until manual re-auth. Therefore:

- All refreshes go through one function guarded by a **per-account Redis lock**
  (`meli:refresh:{account_id}`, ~30s TTL). Inside the lock: re-read the account row —
  if another worker already refreshed (fresh `access_token_expires_at`), use the new
  token and skip. Otherwise call `/oauth/token` (`grant_type=refresh_token`) and
  **persist both new tokens before returning** the access token.
- Refresh is lazy (on use, when `expires_at < now + 5 min`) plus a safety-net cron
  (`refresh_stale_tokens`, every 30 min, refreshes accounts expiring within 1h) so
  tokens stay warm even on quiet days.

**HTTP error policy** (implemented once, in the client):

- **401** → run the locked refresh once, retry the original request once. If refresh
  fails with `invalid_grant` (seller revoked the app / password change), set
  `meli_accounts.status = 'reauth_required'`, stop calling ML for that account, and
  surface a red banner on the dashboard + accounts page ("Reconectar conta").
- **429** → honor `Retry-After` if present, else exponential backoff with jitter
  (1s, 2s, 4s… max 30s, max 5 attempts) — then raise; Arq's own job retry handles the
  rest. A per-account `asyncio.Semaphore(5)` caps concurrency preventively.
- **5xx / network** → same backoff, max 3 attempts, then raise.

### 6.2 Webhooks (`POST /webhooks/meli`)

ML requires a fast 200 response or it re-delivers; slow handlers cause storms. The
handler does exactly three things — **under 500 ms, zero ML API calls, zero heavy DB
work**:

1. Validate shape (`topic`, `resource`, `user_id`, `application_id` == ours; otherwise
   200 + `skipped` — never 4xx, ML would retry forever).
2. Insert a `webhook_events` row (raw payload) — this is the durable receipt.
3. Enqueue `process_meli_notification(webhook_event_id)` on Arq. Return `200 {}`.

**Dedup**: ML delivers at-least-once and bursts duplicates (one order can trigger
several `orders_v2` notifications in seconds). Two layers:

- *Collapse layer* (optimization): Redis `SET NX EX 30` on key
  `meli:notif:{topic}:{resource}` — duplicates inside a 30s window are recorded in
  `webhook_events` as `skipped` and not enqueued.
- *Correctness layer* (the real guarantee): processing is **idempotent**. The worker
  treats the notification purely as a pointer — it always re-fetches the resource from
  the ML API (never trusts payload contents) and **upserts** by
  `(tenant_id, meli_order_id)` / `(tenant_id, meli_shipment_id)`. Processing the same
  notification 5 times converges to the same row. Stale-overwrite protection: skip the
  update if fetched `last_updated <= orders.meli_last_updated_at`.

Subscribed topics: `orders_v2` (order created/paid/cancelled) and `shipments`
(substatus transitions — `ready_to_print` is what makes a shipment batch-eligible,
`shipped` is what triggers the `order_shipped` event).

### 6.3 Polling fallback (why it must exist)

Webhooks fail silently: server restarts/deploys, tunnel drops in dev, ML-side delivery
gives up after limited retries, subscription misconfiguration. For a system whose job
is "nothing ships late", a missed `ready_to_print` is a silent business failure.

- `poll_orders` cron, **every 5 min per active account**: `GET /orders/search?seller={meli_user_id}
  &order.date_last_updated.from={cursor - 10 min}&sort=date_asc`, paginate, run each
  order through the **same upsert pipeline** as webhooks. The 10-minute overlap is
  deliberate: upserts are idempotent, so overlap is free insurance against clock skew.
  Cursor (`poll_cursors.orders_last_polled_at`) advances only after a fully successful
  pass.
- `sync_open_shipments` cron, every 10 min: re-fetch every shipment in a non-terminal
  status from the DB (bounded set) to catch missed `shipped`/`cancelled` transitions.

One pipeline, two feeders: webhook (fast path) and poller (safety net) call the same
service function. There is no "webhook code" vs "polling code" divergence.

## 7. Core business rules

### 7.1 Batch eligibility & lifecycle

A shipment is **eligible** for a new batch when ALL hold:

1. `shipments.meli_status = 'ready_to_ship'` AND `meli_substatus = 'ready_to_print'`
   (label exists and is printable — ML's own signal);
2. `logistic_type` is an ME2 type (`drop_off`, `xd_drop_off`, `cross_docking`);
3. no related order is `cancelled`;
4. not already in a non-terminal batch.

Shipments with `substatus = 'printed'` (already printed once, e.g. reprint after a
paper jam) are **not** auto-eligible but can be force-added via a checkbox
("incluir já impressos") — reprints are legitimate daily reality.

**Batch creation is manual**: the operator opens "Novo lote", sees the eligible list
(pre-checked, with per-shipment `handling_limit_at` deadline highlighted when < 4h),
unchecks anything, clicks create. Automatic cutoffs are not the trigger — the deadline
is displayed as pressure, not used as a scheduler. Membership **freezes at creation**:
orders arriving later go to the next batch (operators reason about "the batch" as a
fixed pile of paper; a mutating batch breaks that mental model).

Lifecycle: `created → downloading → ready | ready_with_failures → completed | cancelled`.
`completed` is set manually ("Fechar lote") or automatically when all non-excluded
shipment checks are `completed`. Picking/checking are allowed from `ready*` onward —
3 failed labels must not block the other 37 (the failures screen handles stragglers).

### 7.2 Label download, partial failures, merge

Job `download_batch_labels(batch_id)`:

1. Set batch `downloading`. For each `batch_shipments` row `pending|failed`, download
   **individually**: `GET /shipment_labels?shipment_ids={id}&response_type=pdf`, with
   `asyncio.Semaphore(5)` concurrency. **Decision — one shipment per request**, even
   though ML accepts comma-separated ids returning one merged PDF: a bulk request is
   all-or-nothing opaque (one bad id can poison the call, and per-shipment failure
   attribution + selective retry — the 3-of-40 requirement — becomes guesswork). Label
   PDFs are small; 40 requests under a semaphore is seconds.
2. Per shipment: success → save `data/labels/{tenant_id}/{batch_id}/{meli_shipment_id}.pdf`,
   `label_status='ok'`; failure after client retries → `label_status='failed'`,
   `label_error` = human-readable reason (typical: shipment moved out of
   `ready_to_print`, i.e. cancelled or already collected — re-fetch the shipment to
   refresh local state when this happens), increment `label_attempts`.
3. Merge all `ok` PDFs with pypdf, sorted by `(carrier_name, handling_limit_at,
   meli_shipment_id)` — carrier grouping is what lets the operator hand each carrier
   pile over without re-sorting paper. Write `sort_position` per shipment, save
   `merged_pdf_path`, create the `shipment_checks` + `check_items` rows, set status
   `ready` (or `ready_with_failures`), stamp `labels_ready_at`.
4. Failures UI: batch page lists failed shipments + reasons + "Tentar novamente"
   (enqueues `retry_batch_labels(batch_id)`, which re-runs steps 1–3 for failed rows
   only and **re-merges the full PDF** — merge must be deterministic and repeatable).
   A failed shipment can also be excluded (`label_status='excluded'`) to stop it from
   blocking batch completion.

### 7.3 Picking list

Generated on demand from batch data (no stored copy — it's a projection):

- Take all `check_items` of the batch (i.e., items of non-excluded shipments), join
  variants, group by `(model_name, size)`, sum quantities.
- Sort: `model_name` asc, then `size` **numerically** (34 < 36 < 40; sizes are text —
  cast digits for ordering, non-numeric sizes sort last alphabetically).
- Render: print-friendly HTML (`@media print` CSS) with columns
  `Modelo | Numeração | Qtd | ✓`, plus batch code, date, total pairs. A second section
  lists multi-item shipments ("pedidos com mais de um volume") since those need
  bench attention during packing.

### 7.4 Barcode check (conferência) — the anti-wrong-shipment gate

Station model: full-screen HTMX page with one always-focused input; the USB scanner
types the code + Enter. The server decides what a scan means — the client is dumb.

**State machine** (per station):

- **IDLE** — expect a *label* scan. The ML label barcode encodes the shipment id:
  normalize the scan (strip non-digits; if a QR JSON payload, extract the id field)
  and look up `(tenant, meli_shipment_id)`. Outcomes:
  - shipment in an active batch & check `pending` → **claim it** (`claimed_by`,
    `claimed_at`, `in_progress`), show expected items (model, size, qty, thumbnail) →
    CHECKING.
  - check `completed` → info toast "já conferido" (idempotent, no error).
  - related order **cancelled** → full-screen red block "CANCELADO — NÃO DESPACHAR",
    `check_events.blocked_cancelled`. This is the payoff of the cancel-alert rule (§10).
  - claimed by another operator with `claimed_at` fresher than **5 min** → blocked
    with "em conferência por {name}" + takeover button (logs `takeover`); staler claim
    → auto-takeover silently (operator walked away).
  - unknown/foreign code → error toast, `check_events.unknown_barcode`.
- **CHECKING** — expect *product* scans. Look up `variant_barcodes` by
  `(tenant, barcode)`:
  - matches an expected item with `qty_checked < qty_expected` → increment (optimistic
    `UPDATE … WHERE version = :v`; on conflict re-read and retry once), green flash +
    beep. All items complete → check `completed`, `completed` event, auto-return IDLE.
  - matches a variant **not in this shipment** → **MISMATCH**: red alert with what was
    scanned vs. expected, `mismatch` event. This is the exact wrong-pair moment the
    system exists to catch. Operator dismisses and scans the right product; repeated
    mismatch → "reportar divergência" sets `mismatch_hold` for supervisor review.
  - unknown barcode → **teach mode**: modal "Código não cadastrado — vincular a
    {expected item}?" listing the shipment's unfulfilled items. Confirm → insert
    `variant_barcodes(source='manual')` + `barcode_bound` event + counts as the scan.
    This is how factory EANs get learned organically instead of via a data-entry
    project. Decline → rejected scan.
  - scanning another *label* while CHECKING → treated as "park current, open next"
    (current stays `in_progress`, claim retained until timeout).
- **Manual override** — per item, "confirmar sem bipar" requires a reason text →
  `manual=true`, `manual_override` event. Audited, visible in the batch report;
  legitimate for damaged barcodes, not a routine path.

Products with no barcode at all: the catalog screen prints internal Code128 label
sheets (value = `variants.internal_code`, source=`internal`) so the operator can label
stock once and scan forever.

### 7.5 Dispatch & completion

ShipFlow does not tell ML anything in v1 (read-only integration). "Shipped" truth
comes from ML: the `shipments` webhook/cron flips `meli_status='shipped'` → emit
`order_shipped` (once per order — guarded by an existing-outbox-row check within the
same transaction). Batch completion timestamps feed the dashboard metric:
`created_at → labels_ready_at → checking_started_at (first label_scan) → completed_at`.

## 8. Background jobs (Redis + Arq)

Why a queue at all (explainer): the webhook must answer in <500 ms, but real work
(fetch from ML, upserts, PDF downloads) takes seconds and can fail — so it runs in a
worker with retries, and the HTTP handler only records + enqueues. Arq over Celery:
async-native (matches SQLAlchemy async), tiny API, Redis only — no extra broker.

| Job | Trigger | Retry policy |
|---|---|---|
| `process_meli_notification(webhook_event_id)` | enqueued by webhook | 5 tries, expo backoff; final failure → `webhook_events.status='failed'` (poller is the backstop) |
| `poll_orders` | cron, every 5 min | next tick is the retry |
| `sync_open_shipments` | cron, every 10 min | next tick |
| `refresh_stale_tokens` | cron, every 30 min | next tick |
| `download_batch_labels(batch_id)` | batch creation | job-level 1 try; per-shipment failures land in `batch_shipments` (visible), not in dead jobs (invisible) |
| `retry_batch_labels(batch_id)` | operator button | same |
| `publish_outbox` | cron, every 30 s (only if `EVENTHUB_ENABLED`) | per-row backoff in `event_outbox` |

All jobs are idempotent — safe to run twice (Arq's at-least-once execution demands it).

## 9. EventHub emission (Phase 2-ready, Phase 1-dormant)

**Emission points** (rows written in the same transaction as the state change):

| Event | Written when | Where in code |
|---|---|---|
| `new_order` | order upsert **creates** a row (not on update) | order ingestion service |
| `order_shipped` | shipment transitions to `shipped` (first time per order) | shipment status update service |

Envelope (matches the EventHub inbound contract; full payload schemas in `API.md` §7):

```json
{"event_id": "uuid4", "event_type": "new_order", "version": 1,
 "tenant_id": "…", "occurred_at": "ISO-8601 UTC", "payload": { … }}
```

**Delivery is post-v1** (ORCHESTRATION T16 is deferred): in v1 the outbox rows are
only written, never delivered — they accumulate as history/backfill. Everything below
describes the publisher for when the hub phase starts.

**Delivery**: `publish_outbox` picks `pending` rows where `next_attempt_at <= now`
(oldest first, batch of 50), POSTs to `EVENTHUB_URL` with
`Authorization: Bearer {EVENTHUB_TOKEN}`. 2xx → `delivered`. Failure/timeout →
`attempts += 1`, backoff `min(2^attempts, 3600)s` + jitter; after 10 attempts →
`dead` + dashboard warning (hub outage must never page the warehouse). EventHub
dedupes by `event_id`, so at-least-once delivery is fine. The core commits its
transaction identically whether the hub is up, down, or not yet built.

## 10. Failure modes & edge cases

| Case | Handling |
|---|---|
| Duplicate webhook | Redis collapse (30s) + idempotent upsert + stale-timestamp skip (§6.2) — duplicates are a no-op by construction |
| Missed webhook | 5-min poller + shipment sync cron re-converge state (§6.3) |
| **Order cancelled after label printed** | Cancellation lands via webhook/poll → order `cancelled`. If its shipment sits in a non-terminal batch: red banner on batch page, struck-through row on picking list (regenerated live), and a hard full-screen block if the label is scanned at check (§7.4). Label paper in the physical pile is unavoidable — the check station is the last gate, and it's the one that counts |
| 3 of 40 labels fail | Batch → `ready_with_failures`; the 37 proceed; failure report + selective retry + exclude (§7.2) |
| Two operators, same shipment | Claim + 5-min staleness + explicit takeover + optimistic version on writes (§7.4); all attempts audited in `check_events` |
| Refresh-token race | Per-account Redis lock, single-flight (§6.1) |
| Seller revokes app | `invalid_grant` → `reauth_required`, red banner, ML calls stop for that account |
| ML API down mid-batch | Client backoff → failures recorded per shipment → operator retries later; nothing is lost |
| Pack (multi-order) shipments | One shipment, N orders: batch/check operate on the shipment; `check_items` span all orders; picking counts everything |
| Wrong pair scanned | MISMATCH alert — the core feature (§7.4) |
| Scanner reads garbage / foreign label | Unknown-barcode event + toast; state unchanged |
| Redis down | Webhook still persists `webhook_events` before enqueue attempt; enqueue failure → 500 to ML → ML retries later; poller also re-converges. Dedup collapse degrades gracefully (idempotency is the real guarantee) |
| Postgres down | System down — acceptable; ML retries webhooks and the poller catches up on recovery |
| Clock skew / DST | UTC everywhere in storage; 10-min poll overlap absorbs skew; `America/Sao_Paulo` only at render time |

## 11. Security

- Session auth: signed cookies (Starlette `SessionMiddleware`, `SECRET_KEY`), bcrypt
  password hashes, `SameSite=Lax`, `Secure` in prod. All pages/API require a session
  except `/login`, `/healthz`, `/webhooks/meli`, OAuth callback.
- ML tokens Fernet-encrypted at rest; decrypted only inside the meli client; never
  logged (client logs method+URL+status only).
- Webhook endpoint validates `application_id`; payload treated as untrusted pointer
  (always re-fetch, §6.2).
- OAuth `state` signed + nonce (CSRF / cross-tenant protection).
- Label PDFs served through authenticated, tenant-checked endpoints — never from a
  static-file mount (the `/static` mount serves only vendored CSS/JS assets).
- Logs are structured JSON, one object per line (`app/core/logging.py`): `ts`, `level`,
  `logger`, `msg`, plus `tenant_id` / `order_id` from contextvars whenever a request or
  job has them bound — every business-flow log line must carry them.

## 12. Configuration (env vars)

```
ENV=dev|prod
LOG_LEVEL=INFO               # stdlib logging level for the JSON formatter (app/core/logging.py)
DATABASE_URL, REDIS_URL
SECRET_KEY                  # sessions + OAuth state signing
TOKEN_ENCRYPTION_KEY        # Fernet key for ML tokens
MELI_CLIENT_ID, MELI_CLIENT_SECRET, MELI_REDIRECT_URI
BASE_URL                    # public HTTPS base (webhook + OAuth callback)
EVENTHUB_ENABLED=false, EVENTHUB_URL=, EVENTHUB_TOKEN=
DISPLAY_TZ=America/Sao_Paulo
LABEL_STORAGE_DIR=./data/labels
TEMPLATES_DIR=../frontend/templates  # relative to backend/, see repo layout in CLAUDE.md
STATIC_DIR=../frontend/static        # vendored pico.min.css + htmx.min.js, mounted at /static
```

Dev: ML needs a public HTTPS callback/webhook URL → use a tunnel (cloudflared/ngrok)
and set `BASE_URL` + the ML app config accordingly. Prod: **Railway or Render** —
web + worker services built from `infra/Dockerfile`, managed Postgres/Redis add-ons
supply `DATABASE_URL`/`REDIS_URL`, platform TLS (no reverse proxy to run), and a
persistent volume mounted on both services for `LABEL_STORAGE_DIR`.

## 13. Key design decisions (ADR-lite)

1. **Shipment, not order, is the fulfillment unit** — labels and checks are per
   shipment; packs make order-centric models wrong.
2. **No mirrored internal status machine** — ML status is stored raw; ShipFlow state
   exists only for ShipFlow-owned processes. Kills state-drift bugs.
3. **One ingestion pipeline, two feeders** — webhook and poller share the upsert code.
4. **Per-shipment label download** — failure attribution and selective retry beat the
   marginal efficiency of bulk requests.
5. **Transactional outbox for EventHub** — emission is a local DB write; delivery is
   someone else's (async) problem; hub outage cannot touch warehouse operations.
6. **Manual batch trigger, frozen membership** — matches how operators reason about
   a physical pile of labels.
7. **Teach-mode barcode binding** — the barcode catalog builds itself during normal
   work instead of demanding an upfront data-entry project.
8. **Claim + optimistic version for checks** — cheap concurrency control, fully
   audited, no pessimistic DB locks held across user think-time.
9. **Text + CHECK instead of PG enums; JSON with JSONB variant** — cheap migrations,
   SQLite-compatible tests.
