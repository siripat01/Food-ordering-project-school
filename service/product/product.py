from datetime import datetime
import os
from typing import Any, Dict, List, Optional

from bson import ObjectId
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.database import Database


class ProductSchema(BaseModel):
    product_name: str
    price: float = Field(..., ge=0)   # must be >= 0
    status: str = "available"
    description: Optional[str] = None
    image: Optional[str] = None
    createAt: datetime = Field(default_factory=datetime.utcnow)
    updateAt: datetime = Field(default_factory=datetime.utcnow)


class ProductService:
    def __init__(self, uri: str = None):
        load_dotenv()
        self.uri = uri
        self.client: MongoClient = MongoClient(self.uri)
        self.database: Database = self.client["Products"]
        self.collection = self.database["products"]

    # ---------- Create ----------
    def CreateProduct(self, product_data: Dict[str, Any]) -> str:
        product = dict(ProductSchema(**product_data))
        result = self.collection.insert_one(product)
        return str(result.inserted_id)

    # ---------- Read ----------
    def GetProduct(self, product_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.collection.find_one({"_id": ObjectId(product_id)})
        except Exception:
            return None

    def GetProductByName(self, product_name: str) -> Optional[Dict[str, Any]]:
        return self.collection.find_one({"product_name": product_name})

    def GetAllProducts(self) -> List[Dict[str, Any]]:
        return list(self.collection.find({}))

    def GetProductsByStatus(self, status: str) -> List[Dict[str, Any]]:
        return list(self.collection.find({"status": status}))

    # ---------- Update ----------
    def UpdateProductName(self, product_id: str, new_name: str) -> int:
        result = self.collection.update_one(
            {"_id": ObjectId(product_id)},
            {"$set": {"product_name": new_name, "updateAt": datetime.utcnow()}}
        )
        return int(result.modified_count)

    def UpdateProductPrice(self, product_id: str, price: float) -> int:
        result = self.collection.update_one(
            {"_id": ObjectId(product_id)},
            {"$set": {"price": price, "updateAt": datetime.utcnow()}}
        )
        return int(result.modified_count)

    def UpdateProductStatus(self, product_id: str, status: str) -> int:
        result = self.collection.update_one(
            {"_id": ObjectId(product_id)},
            {"$set": {"status": status, "updateAt": datetime.utcnow()}}
        )
        return int(result.modified_count)

    def UpdateProductDescription(self, product_id: str, description: str) -> int:
        result = self.collection.update_one(
            {"_id": ObjectId(product_id)},
            {"$set": {"description": description, "updateAt": datetime.utcnow()}}
        )
        return int(result.modified_count)

    def UpdateProductImage(self, product_id: str, image_url: str) -> int:
        result = self.collection.update_one(
            {"_id": ObjectId(product_id)},
            {"$set": {"image": image_url, "updateAt": datetime.utcnow()}}
        )
        return int(result.modified_count)

    # ---------- Delete ----------
    def DeleteProduct(self, product_id: str) -> int:
        result = self.collection.delete_one({"_id": ObjectId(product_id)})
        return int(result.deleted_count)
