"""HTTP layer: one router module per feature (health, tenants, orders, ...).

Handlers are thin — parse input, call a service, shape the response with a
schema. Business logic never lives here. Shared FastAPI dependencies (DB
session, current user) live in deps.py.
"""
