from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.orders import OrderCreate
from app.integrations.agent.service import _safe_validation_errors, _tool_validation_feedback


def _invalid_order_error() -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        OrderCreate.model_validate(
            {
                "items": [
                    {
                        "product_id": "pad-kaprao",
                        "quantity": 1,
                        "addon_ids": [],
                    }
                ]
            }
        )
    return exc_info.value


def test_validation_log_errors_exclude_rejected_input_values() -> None:
    errors = _safe_validation_errors(_invalid_order_error())

    assert errors
    assert errors[0]["loc"] == ["items", "0", "product_id"]
    assert "pad-kaprao" not in str(errors)


def test_create_order_validation_feedback_is_actionable() -> None:
    feedback = _tool_validation_feedback("create_own_order", _invalid_order_error())

    assert "create_own_order" in feedback
    assert "list_products" in feedback
    assert "product_id" in feedback
