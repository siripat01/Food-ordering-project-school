import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from model.orderSchema import OrderCreate, OrderUpdate
from service.order.order import OrderService
from dotenv import load_dotenv


from linebot.v3.messaging import (
    TextMessage,
    PushMessageRequest,
    AsyncMessagingApi,
    Configuration,
    AsyncApiClient
)

from service.users.user import Users

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api: AsyncMessagingApi | None = None

async def init_line_bot():
    """Init LINE bot client"""
    global line_bot_api
    config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    api_client = AsyncApiClient(config)
    line_bot_api = AsyncMessagingApi(api_client)

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")

router = APIRouter()
order_service = OrderService()

usermanagement = Users()

# ---------- Pydantic Schemas ----------

# ---------- Routes ----------

# Create order
@router.post("/order")
def create_order(order: OrderCreate):
    order_id = order_service.AddOrder(dict(order))
    return {"order_id": order_id}


# Get order by ID
@router.get("/order/{order_id}")
def get_order(order_id: str):
    ord_doc = order_service.GetOrder(order_id)
    if not ord_doc:
        raise HTTPException(status_code=404, detail="Order not found")
    ord_doc["_id"] = str(ord_doc["_id"])
    return ord_doc


# Get orders for a user filtered by status
@router.get("/order/user/{user_id}/status/{status}")
def get_user_orders_by_status(user_id: str, status: str):
    orders = order_service.GetUserOrdersByStatus(user_id, status)
    for o in orders:
        o["_id"] = str(o["_id"])
    return {"orders": orders}


@router.get("/order/user/{user_id}/status/{status}")
def get_user_orders_by_status(user_id: str, status: str):
    orders = order_service.GetUserOrdersByStatus(user_id, status)
    print(orders)
    for o in orders:
        o["_id"] = str(o["_id"])
    return {"orders": orders}


@router.get("/order/status/{status}")
def get_orders_by_status(status: str):
    orders = order_service.GetOrdersByStatus(status)
    
    for o in orders:
        o["_id"] = str(o["_id"])
    return {"orders": orders}


# Update order (status or addon)
@router.put("/order/{order_id}")
async def update_order(order_id: str, order: OrderUpdate):
    modified_count = 0
    if order.status is not None:
        modified_count += order_service.UpdateOrderStatus(order_id, order.status)
        
        # user_line_id = usermanagement.get_user_by_user_id(order.userId)
        # print(user_line_id)
        
        # if (user_line_id["line_user_id"]):
        #     if (order.status == "complete"):
        #         print("pass")
        #         print(user_line_id["line_user_id"])
        #         await line_bot_api.push_message(
        #             PushMessageRequest(
        #                 to=user_line_id["line_user_id"],
        #                 messages=[TextMessage(text="อาหารของคุณ ทำเสร็จแล้ว!!")],
        #             )
        #         )
    if order.addon is not None:
        modified_count += order_service.UpdateOrderAddon(order_id, order.addon)

    if modified_count == 0:
        raise HTTPException(status_code=404, detail="Order not found or no changes applied")
    return {"modified_count": modified_count}


# Cancel order
@router.put("/order/{order_id}/cancel")
def cancel_order(order_id: str):
    modified_count = order_service.CancelOrder(order_id)
    if modified_count == 0:
        raise HTTPException(status_code=404, detail="Order not found or already cancelled")
    return {"modified_count": modified_count}


# Delete order (admin)
@router.delete("/order/{order_id}")
def delete_order(order_id: str):
    deleted_count = order_service.DeleteOrder(order_id)
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"deleted_count": deleted_count}

init_line_bot()