from datetime import datetime
from typing import Any, Dict, List, Optional
from langchain.tools import tool
from langchain.tools import BaseTool

# Assuming your original OrderService is imported or defined above
# from your_module import OrderService
from service.order.order import OrderService
import json

class LangChainOrderService(OrderService):
    """Extended OrderService with LangChain tool integration"""
    
    def __init__(self):
        super().__init__()
        self._tools = None
    
    def get_langchain_tools(self) -> List[BaseTool]:
        """Get all LangChain tools for this service"""
        if self._tools is None:
            self._tools = self._create_tools()
        return self._tools
    
    def _create_tools(self) -> List[BaseTool]:
        """Create LangChain tools from service methods"""
        
        @tool
        def create_order(
            product_name: str,
            user_id: str,
            price: float,
            addon: Optional[List[str]] = None,  # Change to List[str]
            status: str = "pending",
            description: str = ""
        ) -> str:
            """Create a new order. The 'addon' should be a list of strings if needed.
            Returns the order ID if successful, or None if the product doesn't exist.
            """
            if addon is None:
                addon = []
            
            # You no longer need to parse JSON.
            # The 'addon' parameter is now a list, which matches the Pydantic schema.
            
            order_data = {
                "product_name": product_name,
                "userId": user_id,
                "price": price,
                "addon": addon,  # Pass the list directly
                "status": status,
                "description": description
            }
            
            # The AddOrder function will now receive the correct data type.
            return self.AddOrder(order_data)

        @tool
        def get_order(order_id: str) -> Optional[Dict[str, Any]]:
            """Retrieve order details by ID 
            input should be a string of order id -> id of mongodb
            """
            result = self.GetOrder(order_id)
            if result:
                # Convert ObjectId and datetime for JSON serialization
                result['_id'] = str(result['_id'])
                for key, value in result.items():
                    if isinstance(value, datetime):
                        result[key] = value.isoformat()
            return result
        
        @tool
        def get_user_orders(user_id: str) -> List[Dict[str, Any]]:
            """Get all orders for a user
            input should be a string of user id -> id of mongodb
            """
            results = self.GetUserOrders(user_id)
            # Convert ObjectId and datetime for JSON serialization
            for result in results:
                result['_id'] = str(result['_id'])
                for key, value in result.items():
                    if isinstance(value, datetime):
                        result[key] = value.isoformat()
            return results
        
        @tool
        def get_user_orders_by_status(user_id: str, status: str) -> List[Dict[str, Any]]:
            """Get user orders filtered by status
                first input should be a string of user id -> id of mongodb
                second input is status of order, should have 2 value 
                    - avalible
                    - unavalible
            """
            results = self.GetUserOrdersByStatus(user_id, status)
            # Convert ObjectId and datetime for JSON serialization
            for result in results:
                result['_id'] = str(result['_id'])
                for key, value in result.items():
                    if isinstance(value, datetime):
                        result[key] = value.isoformat()
            return results
        
        @tool
        def update_order_status(order_id: str, status: str) -> str:
            """Update order status. Returns success message.
                first input should be a string of order id -> id of mongodb
                second input is status of order, should have 2 value 
                    - avalible
                    - unavalible
            """
            result = self.UpdateOrderStatus(order_id, status)
            if result > 0:
                return f"Successfully updated order {order_id} to status '{status}'"
            return f"Failed to update order {order_id}. Order may not exist."
        
        @tool
        def cancel_order(order_id: str) -> str:
            """Cancel an order. Returns success message.
                first input should be a string of order id -> id of mongodb
            """
            result = self.CancelOrder(order_id)
            if result > 0:
                return True
            return None
        
        @tool
        def get_latest_user_order(user_id: str) -> Optional[Dict[str, Any]]:
            """Get the most recent order for a user
                first input should be a string of user id -> id of mongodb
            """
            result = self.GetLatestUserOrder(user_id)
            if result:
                result['_id'] = str(result['_id'])
                for key, value in result.items():
                    if isinstance(value, datetime):
                        result[key] = value.isoformat()
            return result
        
        @tool
        def get_orders_by_date_range(start_date: str, end_date: str) -> List[Dict[str, Any]]:
            """Get orders within a date range. Dates in ISO format (YYYY-MM-DD)"""
            try:
                start = datetime.fromisoformat(start_date)
                end = datetime.fromisoformat(end_date)
                results = self.GetOrdersByDateRange(start, end)
                
                # Convert ObjectId and datetime for JSON serialization
                for result in results:
                    result['_id'] = str(result['_id'])
                    for key, value in result.items():
                        if isinstance(value, datetime):
                            result[key] = value.isoformat()
                return results
            except ValueError:
                return []
        
        return [
            create_order,
            get_order,
            get_user_orders, 
            get_user_orders_by_status,
            update_order_status,
            cancel_order,
            get_latest_user_order,
            get_orders_by_date_range
        ]