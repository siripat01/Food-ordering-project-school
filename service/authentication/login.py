# from pymongo import MongoClient
# from typing import TypedDict
# from datetime import datetime
# from pymongo.database import Database
# import bcrypt

# class User(TypedDict):
#     LineID: str
#     username: str
#     password: str
#     email: str
#     studentID: str
#     role: str
#     createat: datetime


# class Login:
#     def __init__(self):
#         self.client = MongoClient(self.uri)
#         self.database: Database = self.client["Users"]
#         self.collection = self.database["users"]

#     def create_user(self, userData
#                     ) -> str:
#         # user: User = {
#         #     "line_user_id": line_user_id,
#         #     "username": username,
#         #     "password": self.hash_password(password),
#         #     "display_name": display_name,
#         #     "picture_url": picture_url,
#         #     "email": email or None,
#         #     "studentID": studentID or None,
#         #     "role": role or 'student',
#         #     "createat": datetime.now(),  # ใช้เวลา UTC ปลอดภัยกว่า
#         # }
#         result = self.collection.insert_one(userData)
#         return str(result.inserted_id)

#     def update_user(self, userData):
#         filter_query = {"line_user_id": userData["line_user_id"]}

#         # Define the update operation using update operators like $set
#         update_operation = {"$set": userData}

#         # Perform the update
#         return self.collection.update_one(filter_query, update_operation)

#     def get_user(self, username: str) -> dict | None:
#         return self.collection.find_one({"username": username})
    
#     def hash_password(password):
#         return  bcrypt.hashpw(password, bcrypt.gensalt())