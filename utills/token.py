import os
import secrets
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient

class Token:
    def __init__(self, mongodb_uri):
        load_dotenv()
        self.uri = mongodb_uri
        self.client = MongoClient(self.uri)
        self.database = self.client["Users"]
        self.collection = self.database["line_oauth"]

    def generate_state_token(self) -> str:
        """สร้าง state token"""
        return secrets.token_urlsafe(32)

    def store_oauth_state(self, state_token: str, line_user_id: str = None):
        """เก็บ state token + user_id ลง MongoDB"""
        expires_at = datetime.utcnow() + timedelta(minutes=10)

        doc = {
            "state_token": state_token,
            "line_user_id": line_user_id,
            "expires_at": expires_at,
            "created_at": datetime.utcnow(),
        }

        self.collection.insert_one(doc)
        return True

    def get_oauth_state(self, state_token: str):
        """ดึง state token กลับมาเพื่อตรวจสอบ"""
        record = self.collection.find_one({"state_token": state_token})
        if not record:
            return None

        # ตรวจสอบหมดอายุ
        if record["expires_at"] < datetime.utcnow():
            self.collection.delete_one({"_id": record["_id"]})
            return None

        return record.get("line_user_id")  # ✅ คืนค่า user_id (string) อย่างเดียว

    def delete_oauth_state(self, state_token: str):
        """ลบ state token ทิ้ง (หลังใช้แล้ว)"""
        self.collection.delete_one({"state_token": state_token})
