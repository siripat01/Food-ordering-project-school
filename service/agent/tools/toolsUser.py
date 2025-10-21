
from datetime import datetime, timezone
import os
from typing import TypedDict, Optional, List, Dict, Any
import json
from bson import ObjectId

from langchain.tools import tool, BaseTool

from service.users.user import Users

class LangChainUsers(Users):
    """Extended Users service with LangChain tool integration"""
    
    def __init__(self):
        super().__init__()  # <--- เรียก constructor ของ Users
        self._tools = None
    
    # Additional helper methods for extended functionality
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Find user by MongoDB ObjectId"""
        try:
            return self.collection.find_one({"_id": ObjectId(user_id)})
        except Exception:
            return None
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Find user by email"""
        return self.collection.find_one({"email": email})
    
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Find user by username"""
        return self.collection.find_one({"username": username})
    
    def get_user_by_student_id(self, student_id: str) -> Optional[Dict[str, Any]]:
        """Find user by student ID"""
        return self.collection.find_one({"studentID": student_id})
    
    def get_users_by_role(self, role: str) -> List[Dict[str, Any]]:
        """Get all users with specific role"""
        return list(self.collection.find({"role": role}))
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users"""
        return list(self.collection.find({}))
    
    def update_user_role(self, user_id: str, new_role: str) -> int:
        """Update user role"""
        try:
            result = self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"role": new_role, "updatedAt": datetime.now(timezone.utc)}}
            )
            return int(result.modified_count)
        except Exception:
            return 0
    
    def update_user_info(self, user_id: str, update_data: Dict[str, Any]) -> int:
        """Update user information"""
        try:
            update_data["updatedAt"] = datetime.now(timezone.utc)
            result = self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": update_data}
            )
            return int(result.modified_count)
        except Exception:
            return 0
    
    def delete_user(self, user_id: str) -> int:
        """Delete user by ID"""
        try:
            result = self.collection.delete_one({"_id": ObjectId(user_id)})
            return int(result.deleted_count)
        except Exception:
            return 0
    
    def search_users(self, keyword: str) -> List[Dict[str, Any]]:
        """Search users by keyword in username, email, or studentID"""
        regex_pattern = {"$regex": keyword, "$options": "i"}
        query = {
            "$or": [
                {"username": regex_pattern},
                {"email": regex_pattern},
                {"studentID": regex_pattern}
            ]
        }
        return list(self.collection.find(query))

    # Helper method for JSON serialization
    def _serialize_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """Convert MongoDB document for JSON serialization"""
        if user:
            user['_id'] = str(user['_id'])
            for key, value in user.items():
                if isinstance(value, datetime):
                    user[key] = value.isoformat()
            # Remove sensitive data
            if 'password' in user:
                user['password'] = "[HIDDEN]"
        return user

    def _serialize_users(self, users: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert list of MongoDB documents for JSON serialization"""
        return [self._serialize_user(user.copy()) for user in users]

    # LangChain Tools Integration
    def get_langchain_tools(self) -> List[BaseTool]:
        """Get all LangChain tools for this service"""
        if self._tools is None:
            self._tools = self._create_tools()
        return self._tools

    def _create_tools(self) -> List[BaseTool]:
        """Create LangChain tools from service methods"""
        
        @tool
        def create_new_user(
            username: str,
            email: str,
            role: str = "student",
            student_id: Optional[str] = None,
            line_id: Optional[str] = None,
            password: Optional[str] = None
        ) -> str:
            """
            Create a new user account.
            
            Args:
                username: User's username (required)
                email: User's email address (required)
                role: User role (default: student)
                student_id: Student ID (optional)
                line_id: LINE user ID (optional)
                password: User password (optional)
                
            Returns:
                Success message with user ID
            """
            try:
                # Check if user already exists
                existing_user = self.get_user_by_email(email)
                if existing_user:
                    return f"❌ User with email '{email}' already exists"
                
                existing_username = self.get_user_by_username(username)
                if existing_username:
                    return f"❌ Username '{username}' is already taken"
                
                user_data = {
                    "username": username,
                    "email": email,
                    "role": role
                }
                
                if student_id:
                    # Check if student ID is already used
                    existing_student = self.get_user_by_student_id(student_id)
                    if existing_student:
                        return f"❌ Student ID '{student_id}' is already registered"
                    user_data["studentID"] = student_id
                
                if line_id:
                    user_data["line_user_id"] = line_id
                
                if password:
                    user_data["password"] = password  # In production, hash this!
                
                user_id = self.create_user(user_data)
                return f"✅ User '{username}' created successfully with ID: {user_id}"
                
            except Exception as e:
                return f"❌ Error creating user: {str(e)}"

        @tool
        def find_user_by_email(email: str) -> str:
            """
            Find user by email address.
            
            Args:
                email: Email address to search for
                
            Returns:
                User details or error message
            """
            try:
                user = self.get_user_by_email(email)
                if user:
                    user = self._serialize_user(user)
                    return f"👤 **User Found:**\n" + json.dumps(user, indent=2)
                else:
                    return f"❌ No user found with email '{email}'"
            except Exception as e:
                return f"❌ Error searching user: {str(e)}"

        @tool
        def find_user_by_line_id(line_id: str) -> str:
            """
            Find user by LINE ID.
            
            Args:
                line_id: LINE user ID to search for
                
            Returns:
                User details or error message
            """
            try:
                user = self.get_user_by_line_id(line_id)
                if user:
                    user = self._serialize_user(user)
                    return f"👤 **User Found:**\n" + json.dumps(user, indent=2)
                else:
                    return f"❌ No user found with LINE ID '{line_id}'"
            except Exception as e:
                return f"❌ Error searching user: {str(e)}"

        @tool
        def find_user_by_student_id(student_id: str) -> str:
            """
            Find user by student ID.
            
            Args:
                student_id: Student ID to search for
                
            Returns:
                User details or error message
            """
            try:
                user = self.get_user_by_student_id(student_id)
                if user:
                    user = self._serialize_user(user)
                    return f"👤 **User Found:**\n" + json.dumps(user, indent=2)
                else:
                    return f"❌ No user found with student ID '{student_id}'"
            except Exception as e:
                return f"❌ Error searching user: {str(e)}"

        @tool
        def list_users_by_role(role: str) -> str:
            """
            Get all users with a specific role.
            
            Args:
                role: Role to filter by (e.g., 'student', 'teacher', 'admin')
                
            Returns:
                List of users with the specified role
            """
            try:
                users = self.get_users_by_role(role)
                if not users:
                    return f"📝 No users found with role '{role}'"
                
                users = self._serialize_users(users)
                result = f"👥 **Users with role '{role}' ({len(users)} found):**\n\n"
                
                for user in users:
                    result += f"• **{user.get('username', 'N/A')}** ({user.get('email', 'N/A')})\n"
                    result += f"  ID: {user['_id']}"
                    if user.get('studentID'):
                        result += f" | Student ID: {user['studentID']}"
                    result += "\n\n"
                
                return result
                
            except Exception as e:
                return f"❌ Error listing users: {str(e)}"

        @tool
        def search_users_by_keyword(keyword: str) -> str:
            """
            Search users by keyword in username, email, or student ID.
            
            Args:
                keyword: Search keyword
                
            Returns:
                List of matching users
            """
            try:
                users = self.search_users(keyword)
                if not users:
                    return f"🔍 No users found matching '{keyword}'"
                
                users = self._serialize_users(users)
                result = f"🔍 **Search Results for '{keyword}' ({len(users)} found):**\n\n"
                
                for user in users[:10]:  # Limit to 10 results
                    result += f"• **{user.get('username', 'N/A')}** ({user.get('email', 'N/A')})\n"
                    result += f"  ID: {user['_id']} | Role: {user.get('role', 'N/A')}"
                    if user.get('studentID'):
                        result += f" | Student ID: {user['studentID']}"
                    result += "\n\n"
                
                if len(users) > 10:
                    result += f"... and {len(users) - 10} more results"
                
                return result
                
            except Exception as e:
                return f"❌ Error searching users: {str(e)}"

        @tool
        def update_user_role_by_id(user_id: str, new_role: str) -> str:
            """
            Update user's role by user ID.
            
            Args:
                user_id: User's MongoDB ObjectId
                new_role: New role to assign
                
            Returns:
                Success message or error
            """
            try:
                # First check if user exists
                user = self.get_user_by_id(user_id)
                if not user:
                    return f"❌ User with ID '{user_id}' not found"
                
                result = self.update_user_role(user_id, new_role)
                if result > 0:
                    return f"✅ Updated user '{user.get('username', 'Unknown')}' role to '{new_role}'"
                else:
                    return f"❌ Failed to update user role"
                    
            except Exception as e:
                return f"❌ Error updating user role: {str(e)}"

        @tool
        def update_user_information(
            user_id: str,
            username: Optional[str] = None,
            email: Optional[str] = None,
            student_id: Optional[str] = None
        ) -> str:
            """
            Update user information by user ID.
            
            Args:
                user_id: User's MongoDB ObjectId
                username: New username (optional)
                email: New email (optional)
                student_id: New student ID (optional)
                
            Returns:
                Success message or error
            """
            try:
                # Check if user exists
                user = self.get_user_by_id(user_id)
                if not user:
                    return f"❌ User with ID '{user_id}' not found"
                
                update_data = {}
                updates = []
                
                if username:
                    # Check if username is already taken by another user
                    existing = self.get_user_by_username(username)
                    if existing and str(existing['_id']) != user_id:
                        return f"❌ Username '{username}' is already taken"
                    update_data["username"] = username
                    updates.append(f"username to '{username}'")
                
                if email:
                    # Check if email is already used by another user
                    existing = self.get_user_by_email(email)
                    if existing and str(existing['_id']) != user_id:
                        return f"❌ Email '{email}' is already registered"
                    update_data["email"] = email
                    updates.append(f"email to '{email}'")
                
                if student_id:
                    # Check if student ID is already used by another user
                    existing = self.get_user_by_student_id(student_id)
                    if existing and str(existing['_id']) != user_id:
                        return f"❌ Student ID '{student_id}' is already registered"
                    update_data["studentID"] = student_id
                    updates.append(f"student ID to '{student_id}'")
                
                if not update_data:
                    return "❌ No valid updates provided"
                
                result = self.update_user_info(user_id, update_data)
                if result > 0:
                    return f"✅ Updated user {', '.join(updates)}"
                else:
                    return f"❌ Failed to update user information"
                    
            except Exception as e:
                return f"❌ Error updating user: {str(e)}"

        @tool
        def upsert_user_by_line_id(
            line_id: str,
            username: Optional[str] = None,
            email: Optional[str] = None,
            role: str = "student",
            student_id: Optional[str] = None
        ) -> str:
            """
            Create or update user based on LINE ID.
            
            Args:
                line_id: LINE user ID (required)
                username: Username (optional)
                email: Email (optional)
                role: User role (default: student)
                student_id: Student ID (optional)
                
            Returns:
                Success message indicating if user was created or updated
            """
            try:
                user_data = {"line_user_id": line_id, "role": role}
                
                if username:
                    user_data["username"] = username
                if email:
                    user_data["email"] = email
                if student_id:
                    user_data["studentID"] = student_id
                
                result = self.upsert_user(user_data)
                
                if result == "updated":
                    return f"✅ User with LINE ID '{line_id}' updated successfully"
                else:
                    return f"✅ New user created with LINE ID '{line_id}' and ID: {result}"
                    
            except Exception as e:
                return f"❌ Error upserting user: {str(e)}"

        @tool
        def delete_user_by_id(user_id: str) -> str:
            """
            Delete user by ID.
            
            Args:
                user_id: User's MongoDB ObjectId
                
            Returns:
                Success message or error
            """
            try:
                # First get user details for confirmation
                user = self.get_user_by_id(user_id)
                if not user:
                    return f"❌ User with ID '{user_id}' not found"
                
                username = user.get('username', 'Unknown')
                result = self.delete_user(user_id)
                
                if result > 0:
                    return f"🗑️ User '{username}' (ID: {user_id}) deleted successfully"
                else:
                    return f"❌ Failed to delete user with ID '{user_id}'"
                    
            except Exception as e:
                return f"❌ Error deleting user: {str(e)}"

        @tool
        def get_user_statistics() -> str:
            """
            Get user statistics and summary.
            
            Returns:
                Statistics about users in the system
            """
            try:
                all_users = self.get_all_users()
                total_users = len(all_users)
                
                if total_users == 0:
                    return "📊 **User Statistics:** No users in the system"
                
                # Count by role
                role_counts = {}
                line_users = 0
                students_with_id = 0
                
                for user in all_users:
                    role = user.get('role', 'unknown')
                    role_counts[role] = role_counts.get(role, 0) + 1
                    
                    if user.get('line_user_id'):
                        line_users += 1
                    if user.get('studentID'):
                        students_with_id += 1
                
                stats = f"📊 **User Statistics:**\n\n"
                stats += f"👥 Total Users: {total_users}\n"
                stats += f"📱 LINE Connected: {line_users}\n"
                stats += f"🎓 With Student ID: {students_with_id}\n\n"
                
                stats += "**By Role:**\n"
                for role, count in sorted(role_counts.items()):
                    stats += f"• {role.title()}: {count}\n"
                
                return stats
                
            except Exception as e:
                return f"❌ Error getting statistics: {str(e)}"

        return [
            create_new_user,
            find_user_by_email,
            find_user_by_line_id,
            find_user_by_student_id,
            list_users_by_role,
            search_users_by_keyword,
            update_user_role_by_id,
            update_user_information,
            upsert_user_by_line_id,
            delete_user_by_id,
            get_user_statistics
        ]
