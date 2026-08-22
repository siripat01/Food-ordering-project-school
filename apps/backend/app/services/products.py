from __future__ import annotations

from typing import Any

from bson.decimal128 import Decimal128
from pymongo import ReturnDocument

from app.db.mongodb import MongoDatabase
from app.domain.common import normalize_money, parse_object_id, serialize_mongo, utc_now
from app.domain.errors import NotFoundError
from app.domain.products import (
    AddonInput,
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductStatus,
    ProductUpdate,
)


class ProductService:
    def __init__(self, db: MongoDatabase) -> None:
        self.db = db

    @staticmethod
    def normalize_status(value: Any) -> ProductStatus:
        status = str(value or "available").lower()
        if status in {"avalible", "active", "available"}:
            return ProductStatus.AVAILABLE
        if status in {"unavalible", "inactive", "out_of_stock", "unavailable"}:
            return ProductStatus.UNAVAILABLE
        return ProductStatus.DISCONTINUED

    @classmethod
    def to_response(cls, doc: dict[str, Any]) -> ProductResponse:
        addons = []
        for addon in doc.get("addons", []):
            if not isinstance(addon, dict):
                continue
            addons.append(
                AddonInput(
                    id=str(addon.get("id", "legacy")),
                    name=str(addon.get("name", addon.get("id", "Addon"))),
                    price=normalize_money(addon.get("price", 0)),
                    available=bool(addon.get("available", True)),
                )
            )
        return ProductResponse(
            id=str(doc["_id"]),
            name=str(doc.get("productName") or doc.get("product_name") or "Unnamed"),
            price=normalize_money(doc.get("price", 0)),
            status=cls.normalize_status(doc.get("status")),
            description=doc.get("description"),
            image_url=doc.get("imageUrl") or doc.get("image"),
            addons=addons,
            created_at=doc.get("createdAt"),
            updated_at=doc.get("updatedAt") or doc.get("updateAt"),
        )

    async def list_available(self) -> ProductListResponse:
        docs = (
            await self.db.products.find(
                {"status": {"$in": ["available", "avalible", "active"]}}
            )
            .sort("createdAt", -1)
            .to_list()
        )
        return ProductListResponse(products=[self.to_response(doc) for doc in docs])

    async def list_all(self) -> ProductListResponse:
        docs = await self.db.products.find({}).sort("createdAt", -1).to_list()
        return ProductListResponse(products=[self.to_response(doc) for doc in docs])

    async def get(self, product_id: str) -> ProductResponse:
        doc = await self.db.products.find_one({"_id": parse_object_id(product_id)})
        if doc is None:
            raise NotFoundError("Product not found")
        return self.to_response(doc)

    async def create(self, payload: ProductCreate) -> ProductResponse:
        now = utc_now()
        doc = serialize_mongo(
            {
                "productName": payload.name,
                "price": payload.price,
                "status": payload.status.value,
                "description": payload.description,
                "imageUrl": str(payload.image_url) if payload.image_url else None,
                "addons": payload.addons,
                "createdAt": now,
                "updatedAt": now,
                "schemaVersion": 2,
            }
        )
        result = await self.db.products.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self.to_response(doc)

    async def update(self, product_id: str, payload: ProductUpdate) -> ProductResponse:
        fields: dict[str, Any] = {}
        if "name" in payload.model_fields_set:
            fields["productName"] = payload.name
        if "price" in payload.model_fields_set:
            fields["price"] = Decimal128(payload.price) if payload.price is not None else None
        if "status" in payload.model_fields_set:
            fields["status"] = payload.status.value if payload.status else None
        if "description" in payload.model_fields_set:
            fields["description"] = payload.description
        if "image_url" in payload.model_fields_set:
            fields["imageUrl"] = str(payload.image_url) if payload.image_url else None
        if "addons" in payload.model_fields_set:
            fields["addons"] = serialize_mongo(payload.addons or [])
        fields["updatedAt"] = utc_now()
        doc = await self.db.products.find_one_and_update(
            {"_id": parse_object_id(product_id)},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise NotFoundError("Product not found")
        return self.to_response(doc)

    async def discontinue(self, product_id: str) -> ProductResponse:
        doc = await self.db.products.find_one_and_update(
            {"_id": parse_object_id(product_id)},
            {
                "$set": {
                    "status": ProductStatus.DISCONTINUED.value,
                    "deletedAt": utc_now(),
                    "updatedAt": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise NotFoundError("Product not found")
        return self.to_response(doc)
