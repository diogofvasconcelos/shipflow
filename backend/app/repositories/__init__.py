"""Data access: one repository module per aggregate, all SQL lives here.

Rule (CLAUDE.md): on tenant-owned tables, every method takes tenant_id as its
FIRST argument and filters by it — there is no unscoped get(id). Repositories
never commit; the calling service owns the transaction.
"""
