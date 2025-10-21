from datetime import datetime, timezone
import os
from typing import TypedDict, Optional
from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient
from dotenv import load_dotenv
from pymongo.database import Database


class UserType(TypedDict, total=False):
    line_user_id: str
    username: str
    password: str
    email: str
    studentID: str
    role: str
    createdAt: datetime


class Users:
    def __init__(self):
        load_dotenv()
        self.uri = os.getenv("MONGODB_URI")
        self.client = MongoClient(self.uri)
        self.database: Database = self.client["Users"]
        self.collection = self.database["users"]

    def get_user_by_line_id(self, line_id: str) -> Optional[UserType]:
        """Find user by LINE ID"""
        return self.collection.find_one({"line_user_id": line_id})

    def get_user_by_user_id(self, user_id: str) -> Optional[UserType]:
        """Find user by LINE ID"""
        try:
            # Attempt to convert to ObjectId, assuming it might be one
            object_id = ObjectId(user_id)
            return self.collection.find_one({"_id": object_id})
        except:
            # Fall back to a simple string search if conversion fails
            return self.collection.find_one({"_id": user_id})

    def create_user(self, user_data: UserType) -> str:
        """Insert new user"""
        # if "line_user_id" in user_data:
        #     if self.collection.find_one({"line_user_id": line_user_id}):
        #         self.upsert_user(user_data)
        if "createdAt" not in user_data:
            user_data["createdAt"] = datetime.now(timezone.utc)
        result = self.collection.insert_one(user_data)
        return str(result.inserted_id)

    def upsert_user(self, user_data: UserType) -> str:
        """Update or insert user (based on line_user_id)"""
        if "line_user_id" not in user_data:
            raise ValueError("line_user_id is required for upsert")
        user_data["createdAt"] = user_data.get("createdAt", datetime.now(timezone.utc))
        result = self.collection.update_one(
            {"line_user_id": user_data["line_user_id"]},
            {"$set": user_data},
            upsert=True
        )
        return str(result.upserted_id) if result.upserted_id else "updated"
