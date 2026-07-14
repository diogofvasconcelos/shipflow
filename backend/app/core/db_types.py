"""Shared SQLAlchemy column types (see docs/ARCHITECTURE.md §4 conventions)."""

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# JSON on SQLite (tests), JSONB on PostgreSQL (prod). Use for raw payload / audit columns.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")
