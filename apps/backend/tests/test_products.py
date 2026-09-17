from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.products import ProductUpdate


@pytest.mark.parametrize("field", ["price", "status"])
def test_product_update_rejects_explicit_null_for_catalog_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        ProductUpdate.model_validate({field: None})
