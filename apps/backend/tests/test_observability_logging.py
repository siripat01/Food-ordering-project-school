from __future__ import annotations

import json
import logging

from app.core.observability import JsonLogFormatter


def test_json_formatter_includes_tool_and_traceback_without_secret_leak() -> None:
    formatter = JsonLogFormatter()
    try:
        raise ValueError("token=super-secret")
    except ValueError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="tool failed",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )
    record.tool_name = "create_own_order"
    record.validation_errors = [
        {
            "loc": ["items", "0", "product_id"],
            "type": "string_too_short",
            "msg": "String should have at least 24 characters",
        }
    ]

    payload = json.loads(formatter.format(record))

    assert payload["toolName"] == "create_own_order"
    assert payload["validationErrors"][0]["loc"] == ["items", "0", "product_id"]
    assert payload["errorType"] == "ValueError"
    assert "traceback" in payload
    assert "super-secret" not in payload["traceback"]
    assert "token=[REDACTED]" in payload["traceback"]
