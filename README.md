# ShipFlow

Order Fulfillment System for Mercado Libre sellers — real-time order ingestion,
batched shipping-label download/merge, SKU/size-grouped picking lists, and
barcode-verified packing. Multi-tenant from day 1.

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design, schema, business rules
- [`docs/API.md`](docs/API.md) — endpoints, payloads, outbound event contract
- [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md) — implementation task breakdown
- [`CLAUDE.md`](CLAUDE.md) — conventions and invariants for anyone (human or agent) writing code here

## Project map

```
backend/                    # Python application — run every command from here
├── app/
│   ├── main.py             #   FastAPI app factory — start reading here
│   ├── api/                #   HTTP routers, one module per feature (thin handlers only)
│   ├── services/           #   business logic + transactions, one module per feature
│   ├── repositories/       #   all SQL; tenant_id-scoped by convention
│   ├── models/             #   SQLAlchemy tables (schema spec: docs/ARCHITECTURE.md §4)
│   ├── schemas/            #   pydantic request/response shapes
│   ├── core/               #   settings, DB engine, shared types, errors, hashing, crypto
│   ├── integrations/meli/  #   the ONLY code that talks to the Mercado Libre API
│   └── workers/            #   Arq jobs/crons — thin wrappers over services
├── alembic/                # migrations (async), one per task
├── tests/                  # pytest + SQLite in-memory; fixtures in conftest.py
└── pyproject.toml
frontend/
└── templates/              # Jinja2 + HTMX operator screens (pt-BR, server-rendered)
infra/                      # Dockerfile + docker-compose (build context = repo root)
docs/                       # ARCHITECTURE.md · API.md · ORCHESTRATION.md
```

Dependency direction is strict: `api → services → repositories → models`;
workers call services; only `integrations/meli` does external I/O. Every
feature mirrors the Tenant reference slice (model → repository → service →
router → tests).

## Quickstart (local dev)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -e ".[dev]"
cp .env.example .env           # then fill in real secrets

docker compose -f ../infra/docker-compose.yml up -d postgres redis
alembic upgrade head
uvicorn app.main:app --reload
```

Run tests (from `backend/`):

```bash
pytest
```

Run the full stack (app + worker + postgres + redis) in Docker:

```bash
cd infra && docker compose up --build
```

## Status

Foundation scaffold only (T0 in `docs/ORCHESTRATION.md`): FastAPI app factory, async
SQLAlchemy + Alembic wiring, Redis/Arq worker skeleton, and the `Tenant` vertical
slice (model → repository → service → router → tests) that every subsequent feature
mirrors. See `docs/ORCHESTRATION.md` for what's next.
