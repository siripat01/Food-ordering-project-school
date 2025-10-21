from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.database import Database

from service.product.product import ProductService
import os
from dotenv import load_dotenv

load_dotenv()
mongodb_uri = os.getenv("MONGODB_URI")
productService = ProductService(mongodb_uri)

class OrderSchema(BaseModel):
    product_name: str
    userId: str
    price: float = Field(..., ge=0)
    addon: List[str] = Field(default_factory=list)  # เปลี่ยนเป็น List[str]
    status: str = Field(default="pending")
    description: str
    createAt: datetime = Field(default_factory=datetime.now)
    Finish: Optional[datetime] = None


class OrderService:
    def __init__(self):
        # default uri included from your examples — replace if needed
        self.uri = os.getenv("MONGODB_URI", "mongodb+srv://siriboonruangsiripat:00137989Mek@ai-application.ivc9mtv.mongodb.net/")
        self.client: MongoClient = MongoClient(self.uri)
        self.database: Database = self.client["Orders"]
        self.collection = self.database["orders"]

    # ---------- Create ----------
    def AddOrder(self, order_data: Dict[str, Any]) -> str:
        """
        Validate with Pydantic and insert into MongoDB.

            Data format of db schema
            
            product_name: str
            userId: str
            price: float = Field(..., ge=0)  # must be >= 0
            addon: List[str, Any] = Field(default_factory=list)
            status: str = Field(default="pending")
            description: str
            
        Returns inserted document id as string.
        """
        print(order_data)
        # if not ProductService.GetProductByName(product_name=order_data["product_name"]):
        #     return None
        order = dict(OrderSchema(**order_data))
        result = self.collection.insert_one(order)
        return str(result.inserted_id)

    # ---------- Read ----------
    def GetOrder(self, orderId: str) -> Optional[Dict[str, Any]]:
        """Find a single order by _id (string form)."""
        try:
            doc = self.collection.find_one({"_id": ObjectId(orderId)})
            return doc
        except Exception:
            return None

    def GetUserOrders(self, userId: str) -> List[Dict[str, Any]]:
        """Return all orders for a user (list)."""
        try:
            return list(self.collection.find({"userId": userId}))
        except Exception:
            return []

    def GetUserOrdersByStatus(self, userId: str, status: str) -> List[Dict[str, Any]]:
        """Return all orders for a user filtered by status."""
        try:
            
            return list(self.collection.find({"userId": userId, "status": status}))
        except Exception:
            return []

    def GetOrdersByStatus(self, status: str) -> List[Dict[str, Any]]:
        """Return all orders for a user filtered by status."""
        try:
            
            return list(self.collection.find({"status": status}))
        except Exception:
            return []

    def GetLatestUserOrder(self, userId: str) -> Optional[Dict[str, Any]]:
        """Return the latest order for a user sorted by createAt descending."""
        try:
            return self.collection.find_one({"userId": userId}, sort=[("createAt", -1)])
        except Exception:
            return None

    def GetOrdersByDateRange(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Return orders whose createAt is between start and end (inclusive)."""
        try:
            return list(self.collection.find({"createAt": {"$gte": start, "$lte": end}}))
        except Exception:
            return []

    # ---------- Update ----------
    def UpdateOrderStatus(self, orderId: str, status: str) -> int:
        """
        Update status for an order.
        If status indicates completion or cancellation, update Finish timestamp.
        Returns modified_count (0 or 1).
        """
        try:
            update_payload = {"status": status}
            if status.lower() in ("completed", "finished", "cancelled", "canceled"):
                update_payload["Finish"] = datetime.utcnow()
            update_payload["updateAt"] = datetime.utcnow()

            result = self.collection.update_one(
                {"_id": ObjectId(orderId)},
                {"$set": update_payload}
            )
            return int(result.modified_count)
        except Exception:
            return 0

    def UpdateOrderAddon(self, orderId: str, addon: Dict[str, Any]) -> int:
        """Replace the addons dict for an order. Returns modified_count."""
        try:
            result = self.collection.update_one(
                {"_id": ObjectId(orderId)},
                {"$set": {"addon": addon, "updateAt": datetime.utcnow()}}
            )
            return int(result.modified_count)
        except Exception:
            return 0

    # ---------- Cancel / Delete ----------
    def CancelOrder(self, orderId: str) -> int:
        """
        Mark an order as cancelled (soft-delete style).
        Returns modified_count.
        """
        try:
            result = self.collection.update_one(
                {"_id": ObjectId(orderId)},
                {"$set": {"status": "cancelled", "Finish": datetime.utcnow(), "updateAt": datetime.utcnow()}}
            )
            return int(result.modified_count)
        except Exception:
            return 0

    def DeleteOrder(self, orderId: str) -> int:
        """
        Physically delete an order from the collection.
        Use with caution — intended for admin operations.
        Returns deleted_count.
        """
        try:
            result = self.collection.delete_one({"_id": ObjectId(orderId)})
            return int(result.deleted_count)
        except Exception:
            return 0

