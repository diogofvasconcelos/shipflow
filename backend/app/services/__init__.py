"""Business logic: one service module per feature.

Services own transactions and orchestration, call repositories for data and
integrations for external I/O, and raise app.core.errors exceptions that the
HTTP layer translates. This is the only layer workers and routers call into.
"""
