# CLAUDE.md — ShipFlow

Order fulfillment system for Mercado Libre sellers: real-time order ingestion
(webhook + polling fallback), batch label download merged into one carrier-sorted PDF,
picking lists grouped by model/size, and barcode-verified packing. Multi-tenant,
used in production daily by real sellers.

## Read first, always

Before writing ANY code, read `docs/ARCHITECTURE.md` and `docs/API.md`. Before picking
up a task, read its entry in `docs/ORCHESTRATION.md`. Every design decision is already
made and recorded there — implement it, don't re-derive or re-decide it. If you find a
genuine gap or contradiction, fix the doc in the same PR so it stays the source of
truth. Schema, payloads, and business rules in the docs are binding, down to field
names and response shapes.

## Simplicity rule

Use the simplest approach that satisfies the spec. No speculative abstractions, no
"manager"/"factory" layers, no generic frameworks for one call site, no config for
things that never vary. Three similar lines beat one clever indirection. If a base
class has one subclass, delete it. New dependencies require a reason the standard
library or an existing dep can't cover.

## Structure & layering

Monorepo layout — top level groups by concern:

```
backend/           # the Python application (run every command from here)
├── app/
│   ├── api/           # routers — thin: parse, call service, shape response. No business logic.
│   ├── services/      # business logic, transactions, orchestration
│   ├── repositories/  # all DB queries. tenant_id is the FIRST arg of every method on tenant-owned tables
│   ├── models/        # SQLAlchemy models only
│   ├── schemas/       # pydantic request/response models
│   ├── core/          # settings, db, security, crypto, shared types
│   ├── integrations/meli/client.py   # the ONLY module that talks to the ML API (owns httpx, tokens, retries)
│   └── workers/       # Arq jobs and crons — thin wrappers that call services
├── tests/             # pytest, async, SQLite in-memory
├── alembic/           # migrations
└── pyproject.toml
frontend/templates/    # Jinja2 + HTMX screens (server-rendered; located via TEMPLATES_DIR setting)
frontend/static/       # vendored assets (pico.min.css, htmx.min.js) served at /static — no CDNs at runtime
infra/                 # Dockerfile + docker-compose (build context is the repo root)
docs/                  # ARCHITECTURE.md · API.md · ORCHESTRATION.md
```

**Path convention in the docs**: `app/...` and `tests/...` paths in docs/ mean
`backend/app/...` and `backend/tests/...`; `templates/...` means
`frontend/templates/...`. All Python commands (pytest, alembic, uvicorn, ruff)
run from `backend/`.

Every feature mirrors the Tenant vertical slice: model → repository → service →
router → tests. Dependency direction is strict: api → services → repositories →
models. Workers call services, never repositories directly for business flows.
Nothing outside `integrations/meli/` may import httpx or reference ML URLs/tokens.

## Non-negotiable invariants

- **Tenancy**: `tenant_id` comes from the session user or is derived server-side
  (webhook: `user_id` → `meli_accounts` → tenant). Never from request params. Every
  tenant-owned query filters by it. Cross-tenant lookups return 404, not 403.
- **Idempotency**: every Arq job and every webhook-triggered path must be safe to run
  twice (at-least-once delivery everywhere). Upserts by natural key, transition guards,
  stale-timestamp skips.
- **Webhook budget**: `/webhooks/meli` = validate + insert + enqueue + return 200.
  Never call the ML API in the handler; never return 4xx to ML.
- **Tokens**: ML tokens are Fernet-encrypted at rest, decrypted only inside the meli
  client, never logged, never serialized into API responses.
- **Events**: EventHub emission = writing an `event_outbox` row in the same
  transaction as the state change. Never call EventHub inline from a service.
- **State**: ML statuses are stored raw (`meli_status`/`meli_substatus`), never
  mirrored into a parallel internal status. ShipFlow-owned state lives only in
  batch/check tables. Display states are derived in queries.

## Conventions

- Python 3.12, full type hints, `async def` end to end. Format/lint with ruff.
- Timestamps: `timestamptz` UTC in DB and JSON; convert to `America/Sao_Paulo` only in
  templates. Money: `NUMERIC(12,2)` + currency.
- Enums: TEXT + CHECK constraint (no native PG enums). JSON columns: `JSONVariant`
  from `app/core/db_types.py` (JSONB on PG, JSON on SQLite).
- One Alembic migration per task; migrations must run on PostgreSQL (CI checks this)
  while models stay SQLite-compatible for tests.
- UI text in **pt-BR** (warehouse operators); code, comments, commits, docs in
  English. Commit style: `t{NN}: imperative summary`.
- Tests: pytest + pytest-asyncio, SQLite in-memory, respx for httpx mocking, ML
  payloads as committed JSON fixtures. Test behavior (inputs → observable outcome),
  not implementation details. Every task's "done when" list maps to real tests.
- Logging: structured JSON via `app/core/logging.py` — one JSON object per line with
  `tenant_id`/`order_id` contextvars bound at request/job entry points. Never use
  bare `print`; every business-flow log carries the tenant.
- UI styling: Pico.css (semantic HTML, minimal classes); custom CSS only where Pico
  can't cover (check-station full-screen states).
- Secrets only via env (`.env` git-ignored, `.env.example` maintained). Never log
  tokens, passwords, or full ML payloads at INFO level.

## Definition of done (any task)

1. Spec in `docs/` satisfied exactly; docs updated if the task changed behavior.
2. `pytest` green; new behavior covered by tests.
3. `ruff check` and `ruff format --check` clean.
4. No layering or invariant violations above.
