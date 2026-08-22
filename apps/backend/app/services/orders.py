from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.observability import ApplicationMetrics
from app.db.mongodb import MongoDatabase
from app.domain.common import normalize_money, parse_object_id, serialize_mongo, utc_now
from app.domain.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.orders import (
    ALLOWED_TRANSITIONS,
    LEGACY_STATUS_MAP,
    OrderAddonSnapshot,
    OrderCreate,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    OrderStatus,
    StatusHistoryEntry,
)
from app.domain.products import ProductStatus
from app.domain.users import Role
from app.services.products import ProductService

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self, db: MongoDatabase, *, metrics: ApplicationMetrics | None = None) -> None:
        self.db = db
        self.metrics = metrics

    @staticmethod
    def _aware(value: datetime | None, fallback: datetime) -> datetime:
        if value is None:
            return fallback
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _status(value: Any) -> OrderStatus:
        return LEGACY_STATUS_MAP.get(str(value or "pending").lower(), OrderStatus.PENDING)

    @classmethod
    def to_response(cls, doc: dict[str, Any]) -> OrderResponse:
        created_fallback = (
            doc["_id"].generation_time if isinstance(doc.get("_id"), ObjectId) else utc_now()
        )
        created_at = cls._aware(doc.get("createdAt") or doc.get("createAt"), created_fallback)
        updated_at = cls._aware(doc.get("updatedAt") or doc.get("updateAt"), created_at)
        status = cls._status(doc.get("status"))
        schema_version = int(doc.get("schemaVersion", 1))

        items: list[OrderItemResponse] = []
        if schema_version >= 2 and isinstance(doc.get("items"), list):
            for item in doc["items"]:
                addons = [
                    OrderAddonSnapshot(
                        id=str(addon.get("id", "legacy")),
                        name=str(addon.get("name", "Addon")),
                        price=normalize_money(addon.get("price", 0)),
                    )
                    for addon in item.get("addons", [])
                    if isinstance(addon, dict)
                ]
                items.append(
                    OrderItemResponse(
                        product_id=str(item.get("productId"))
                        if item.get("productId")
                        else None,
                        product_name_snapshot=str(
                            item.get("productNameSnapshot", "Unnamed product")
                        ),
                        unit_price=normalize_money(item.get("unitPrice", 0)),
                        quantity=int(item.get("quantity", 1)),
                        addons=addons,
                        note=item.get("note"),
                        line_total=normalize_money(item.get("lineTotal", 0)),
                    )
                )
        else:
            legacy_price = normalize_money(doc.get("price", 0))
            legacy_addons = doc.get("addon", [])
            legacy_addon_names = legacy_addons if isinstance(legacy_addons, list) else []
            addon_snapshots = [
                OrderAddonSnapshot(id=f"legacy-{index}", name=str(name), price=Decimal("0"))
                for index, name in enumerate(legacy_addon_names)
            ]
            items.append(
                OrderItemResponse(
                    product_id=None,
                    product_name_snapshot=str(doc.get("product_name", "Legacy product")),
                    unit_price=legacy_price,
                    quantity=1,
                    addons=addon_snapshots,
                    note=doc.get("description"),
                    line_total=legacy_price,
                )
            )

        subtotal = normalize_money(doc.get("subtotal", doc.get("price", 0)))
        total = normalize_money(doc.get("total", subtotal))
        history = []
        for entry in doc.get("statusHistory", []):
            if not isinstance(entry, dict):
                continue
            actor_role = str(entry.get("actorRole") or "")
            history.append(
                StatusHistoryEntry(
                    status=cls._status(entry.get("status")),
                    changed_at=cls._aware(entry.get("changedAt"), created_at),
                    actor_id=entry.get("actorId"),
                    actor_role=(
                        Role(actor_role) if actor_role in Role._value2member_map_ else None
                    ),
                )
            )
        if not history:
            history = [StatusHistoryEntry(status=status, changed_at=created_at)]
        return OrderResponse(
            id=str(doc["_id"]),
            user_id=str(doc.get("userId", "")),
            items=items,
            subtotal=subtotal,
            total=total,
            status=status,
            status_history=history,
            created_at=created_at,
            updated_at=updated_at,
            completed_at=doc.get("completedAt"),
            cancelled_at=doc.get("cancelledAt"),
            schema_version=max(schema_version, 1),
            legacy_price_unverified=bool(doc.get("legacyPriceUnverified", schema_version < 2)),
        )

    async def create(
        self,
        *,
        user_id: str,
        payload: OrderCreate,
        idempotency_key: str,
    ) -> tuple[OrderResponse, bool]:
        existing = await self.db.orders.find_one(
            {"userId": user_id, "idempotencyKey": idempotency_key}
        )
        if existing:
            return self.to_response(existing), False

        product_ids: list[ObjectId] = []
        try:
            product_ids = [parse_object_id(item.product_id) for item in payload.items]
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
        product_docs = await self.db.products.find(
            {"_id": {"$in": list(set(product_ids))}}
        ).to_list()
        products_by_id = {str(doc["_id"]): doc for doc in product_docs}
        if len(products_by_id) != len(set(item.product_id for item in payload.items)):
            raise InvalidInputError("One or more products do not exist")

        order_items: list[dict[str, Any]] = []
        subtotal = Decimal("0")
        for requested in payload.items:
            product_doc = products_by_id[requested.product_id]
            product_status = ProductService.normalize_status(product_doc.get("status"))
            if product_status is not ProductStatus.AVAILABLE:
                raise ConflictError("One or more products are unavailable")
            unit_price = normalize_money(product_doc.get("price", 0))
            catalog_addons = {
                str(addon.get("id")): addon
                for addon in product_doc.get("addons", [])
                if isinstance(addon, dict) and addon.get("available", True)
            }
            if any(addon_id not in catalog_addons for addon_id in requested.addon_ids):
                raise InvalidInputError("One or more add-ons are invalid or unavailable")
            addon_snapshots = []
            addon_total = Decimal("0")
            for addon_id in requested.addon_ids:
                addon = catalog_addons[addon_id]
                addon_price = normalize_money(addon.get("price", 0))
                addon_total += addon_price
                addon_snapshots.append(
                    {
                        "id": addon_id,
                        "name": str(addon.get("name", addon_id)),
                        "price": addon_price,
                    }
                )
            line_total = normalize_money((unit_price + addon_total) * requested.quantity)
            subtotal += line_total
            order_items.append(
                {
                    "productId": requested.product_id,
                    "productNameSnapshot": str(
                        product_doc.get("productName")
                        or product_doc.get("product_name")
                        or "Unnamed product"
                    ),
                    "unitPrice": unit_price,
                    "quantity": requested.quantity,
                    "addons": addon_snapshots,
                    "note": requested.note,
                    "lineTotal": line_total,
                }
            )
        subtotal = normalize_money(subtotal)
        now = utc_now()
        doc = serialize_mongo(
            {
                "userId": user_id,
                "items": order_items,
                "subtotal": subtotal,
                "total": subtotal,
                "status": OrderStatus.PENDING.value,
                "statusHistory": [
                    {
                        "status": OrderStatus.PENDING.value,
                        "changedAt": now,
                        "actorId": user_id,
                        "actorRole": Role.CUSTOMER.value,
                    }
                ],
                "createdAt": now,
                "updatedAt": now,
                "idempotencyKey": idempotency_key,
                "schemaVersion": 2,
            }
        )
        try:
            result = await self.db.orders.insert_one(doc)
        except DuplicateKeyError:
            existing = await self.db.orders.find_one(
                {"userId": user_id, "idempotencyKey": idempotency_key}
            )
            if existing is None:
                raise ConflictError("Duplicate order request") from None
            return self.to_response(existing), False
        doc["_id"] = result.inserted_id
        order = self.to_response(doc)
        if self.metrics:
            self.metrics.record_order_created(order.total)
        logger.info(
            "order_created",
            extra={"order_id": order.id, "order_status": order.status.value},
        )
        return order, True

    async def get(self, order_id: str) -> OrderResponse:
        doc = await self.db.orders.find_one({"_id": parse_object_id(order_id)})
        if doc is None:
            raise NotFoundError("Order not found")
        return self.to_response(doc)

    async def list_own(
        self, *, user_id: str, status: OrderStatus | None = None
    ) -> OrderListResponse:
        query: dict[str, Any] = {"userId": user_id}
        if status:
            legacy_values = [key for key, value in LEGACY_STATUS_MAP.items() if value is status]
            query["status"] = {"$in": legacy_values}
        docs = await self.db.orders.find(query).sort("createdAt", -1).limit(100).to_list()
        return OrderListResponse(orders=[self.to_response(doc) for doc in docs])

    async def list_queue(self, *, status: OrderStatus | None = None) -> OrderListResponse:
        query: dict[str, Any] = {}
        if status:
            legacy_values = [key for key, value in LEGACY_STATUS_MAP.items() if value is status]
            query["status"] = {"$in": legacy_values}
        else:
            query["status"] = {
                "$in": [
                    OrderStatus.PENDING.value,
                    OrderStatus.CONFIRMED.value,
                    OrderStatus.PREPARING.value,
                    "making",
                    OrderStatus.READY.value,
                ]
            }
        docs = await self.db.orders.find(query).sort("createdAt", 1).limit(200).to_list()
        return OrderListResponse(orders=[self.to_response(doc) for doc in docs])

    async def cancel_own(self, *, order_id: str, user_id: str) -> OrderResponse:
        object_id = parse_object_id(order_id)
        now = utc_now()
        doc = await self.db.orders.find_one_and_update(
            {
                "_id": object_id,
                "userId": user_id,
                "status": {"$in": [OrderStatus.PENDING.value, OrderStatus.CONFIRMED.value]},
            },
            {
                "$set": {
                    "status": OrderStatus.CANCELLED.value,
                    "updatedAt": now,
                    "cancelledAt": now,
                },
                "$push": {
                    "statusHistory": {
                        "status": OrderStatus.CANCELLED.value,
                        "changedAt": now,
                        "actorId": user_id,
                        "actorRole": Role.CUSTOMER.value,
                    }
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if doc:
            order = self.to_response(doc)
            if self.metrics:
                self.metrics.record_order_status(order.status.value)
            logger.info(
                "order_status_changed",
                extra={"order_id": order.id, "order_status": order.status.value},
            )
            return order
        existing = await self.db.orders.find_one({"_id": object_id, "userId": user_id})
        if existing is None:
            raise NotFoundError("Order not found")
        raise ConflictError("Order can no longer be cancelled")

    async def transition(
        self,
        *,
        order_id: str,
        new_status: OrderStatus,
        actor_id: str,
        actor_role: Role,
    ) -> OrderResponse:
        object_id = parse_object_id(order_id)
        existing = await self.db.orders.find_one({"_id": object_id})
        if existing is None:
            raise NotFoundError("Order not found")
        current = self._status(existing.get("status"))
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise ConflictError(
                f"Invalid order status transition: {current.value} -> {new_status.value}"
            )
        now = utc_now()
        set_fields: dict[str, Any] = {
            "status": new_status.value,
            "updatedAt": now,
        }
        if new_status is OrderStatus.COMPLETED:
            set_fields["completedAt"] = now
        if new_status is OrderStatus.CANCELLED:
            set_fields["cancelledAt"] = now
        doc = await self.db.orders.find_one_and_update(
            {"_id": object_id, "status": existing.get("status")},
            {
                "$set": set_fields,
                "$push": {
                    "statusHistory": {
                        "status": new_status.value,
                        "changedAt": now,
                        "actorId": actor_id,
                        "actorRole": actor_role.value,
                    }
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise ConflictError("Order status changed concurrently; retry the request")
        order = self.to_response(doc)
        if self.metrics:
            self.metrics.record_order_status(order.status.value)
        logger.info(
            "order_status_changed",
            extra={"order_id": order.id, "order_status": order.status.value},
        )
        return order
