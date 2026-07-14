"""SQLAlchemy models, one module per table. Schema is specified in
docs/ARCHITECTURE.md §4 — column names and constraints there are binding.

Every model MUST be imported here so Base.metadata sees it (Alembic
autogenerate and the test fixtures both rely on this).
"""

from app.models.tenant import Tenant

__all__ = ["Tenant"]
