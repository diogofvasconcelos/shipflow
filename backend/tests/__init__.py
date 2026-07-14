"""Test suite: pytest + pytest-asyncio over in-memory SQLite.

conftest.py provides the two fixtures everything builds on: db_session
(isolated AsyncSession with the full schema created) and client (httpx
AsyncClient over the real app with the DB dependency overridden).
"""
