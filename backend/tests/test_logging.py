import json
import logging

from app.core.logging import JsonFormatter, log_context


def make_record(msg: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test", level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None
    )


def test_formatter_emits_valid_json_with_expected_keys():
    parsed = json.loads(JsonFormatter().format(make_record()))

    assert parsed["msg"] == "hello"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "app.test"
    assert "ts" in parsed
    assert "tenant_id" not in parsed
    assert "order_id" not in parsed


def test_formatter_includes_bound_context():
    formatter = JsonFormatter()
    with log_context(tenant_id=7, order_id=42):
        parsed = json.loads(formatter.format(make_record()))

    assert parsed["tenant_id"] == 7
    assert parsed["order_id"] == 42


def test_context_is_cleared_after_the_with_block():
    with log_context(tenant_id=7, order_id=42):
        pass

    parsed = json.loads(JsonFormatter().format(make_record()))
    assert "tenant_id" not in parsed
    assert "order_id" not in parsed


def test_nested_context_restores_outer_value_on_exit():
    with log_context(tenant_id=1):
        with log_context(tenant_id=2):
            inner = json.loads(JsonFormatter().format(make_record()))
        outer_after_inner = json.loads(JsonFormatter().format(make_record()))

    assert inner["tenant_id"] == 2
    assert outer_after_inner["tenant_id"] == 1
