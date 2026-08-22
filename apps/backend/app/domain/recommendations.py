from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.domain.common import APIModel
from app.domain.products import ProductResponse


class RecommendationEventType(StrEnum):
    IMPRESSION = "impression"
    CLICK = "click"
    ADD_TO_CART = "add_to_cart"
    PURCHASE = "purchase"


class ClientRecommendationEventType(StrEnum):
    IMPRESSION = "impression"
    CLICK = "click"
    ADD_TO_CART = "add_to_cart"


class RecommendationEventCreate(APIModel):
    event_type: ClientRecommendationEventType
    product_id: str = Field(min_length=24, max_length=24)
    recommendation_id: str = Field(min_length=16, max_length=64)


class RecommendationEventResponse(APIModel):
    accepted: bool
    duplicate: bool


class RecommendationStrategy(StrEnum):
    EXTERNAL = "external"
    POPULARITY = "popularity"
    TRENDING = "trending"
    ITEM_ITEM = "item_item"
    RECENT = "recent"


class RecommendationResponse(APIModel):
    recommendation_id: str
    strategy: RecommendationStrategy
    products: list[ProductResponse]
