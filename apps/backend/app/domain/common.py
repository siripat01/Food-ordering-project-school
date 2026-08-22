from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from bson import ObjectId
from bson.decimal128 import Decimal128
from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer

MONEY_QUANTUM = Decimal("0.01")


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_money(value: Any) -> Decimal:
    if isinstance(value, Decimal128):
        value = value.to_decimal()
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


Money = Annotated[
    Decimal,
    AfterValidator(normalize_money),
    PlainSerializer(lambda value: float(value), return_type=float),
]


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def parse_object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise ValueError("Invalid resource ID")
    return ObjectId(value)


def serialize_mongo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return Decimal128(value)
    if isinstance(value, BaseModel):
        return serialize_mongo(value.model_dump(by_alias=True))
    if isinstance(value, dict):
        return {key: serialize_mongo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_mongo(item) for item in value]
    return value
