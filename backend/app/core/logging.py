"""Structured JSON logging (see docs/ARCHITECTURE.md §11). stdlib only, no new deps.

Services/workers call `log_context(tenant_id=..., order_id=...)` at request/job entry
points; every log line emitted while bound picks up those fields automatically.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime

tenant_id_var: ContextVar[int | None] = ContextVar("tenant_id", default=None)
order_id_var: ContextVar[int | None] = ContextVar("order_id", default=None)


@contextmanager
def log_context(*, tenant_id: int | None = None, order_id: int | None = None) -> Iterator[None]:
    resets: list[tuple[ContextVar[int | None], Token]] = []
    if tenant_id is not None:
        resets.append((tenant_id_var, tenant_id_var.set(tenant_id)))
    if order_id is not None:
        resets.append((order_id_var, order_id_var.set(order_id)))
    try:
        yield
    finally:
        for var, token in resets:
            var.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        tenant_id = tenant_id_var.get()
        if tenant_id is not None:
            payload["tenant_id"] = tenant_id
        order_id = order_id_var.get()
        if order_id is not None:
            payload["order_id"] = order_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
