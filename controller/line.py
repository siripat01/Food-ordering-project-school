import datetime
import json
import os
import sys
from typing import Optional, Dict, Any
from urllib.parse import urlencode, parse_qs

import httpx
import jwt
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Query, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from dotenv import load_dotenv

from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ReplyMessageRequest,
    TextMessage,
    PushMessageRequest,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from pydantic import BaseModel

from service.agent.llm import agent_executor
from service.order.order import OrderService
from utills.token import Token
from service.users.user import Users

# โหลด env
load_dotenv()
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_LOGIN_CHANNEL_ID = os.getenv("LINE_CHANNEL_LOGIN_ID")
LINE_LOGIN_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_LOGIN_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://d19b55b1af62.ngrok-free.app/api/ai/auth/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")  # frontend web app
SECRET_KEY = os.getenv("SECRET_KEY", "mysecret")
MONGODB_URI = os.getenv("MONGODB_URI")
ALGORITHM = "HS256"

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    print("❌ Please set LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN in your .env file")
    sys.exit(1)

if not LINE_LOGIN_CHANNEL_ID or not LINE_LOGIN_CHANNEL_SECRET:
    print("❌ Please set LINE_LOGIN_CHANNEL_ID and LINE_LOGIN_CHANNEL_SECRET for OAuth")
    sys.exit(1)

parser = WebhookParser(CHANNEL_SECRET)

line_bot_api: AsyncMessagingApi | None = None
router = APIRouter()

token_state = Token(MONGODB_URI)
usermangement = Users()
order_service = OrderService()

class pushMessageType(BaseModel):
    order_id: str
    status: str

# ---------------------- JWT Utils -------------------------

def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.now() + expires_delta
    else:
        expire = datetime.datetime.now() + datetime.timedelta(hours=24)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        line_user_id: str = payload.get("sub")
        if line_user_id is None:
            return None
        return line_user_id
    except jwt.PyJWTError:
        return None


# ---------------------- LINE Bot Init -------------------------

async def init_line_bot():
    """Init LINE bot client"""
    global line_bot_api
    config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    api_client = AsyncApiClient(config)
    line_bot_api = AsyncMessagingApi(api_client)
    
async def get_line_bot_api():
    """Get LINE bot API instance, initialize if needed"""
    global line_bot_api
    if line_bot_api is None:
        await init_line_bot()
    return line_bot_api


@router.get("/")
async def index():
    return {"status": "ok"}


# ---------------------- LINE Webhook -------------------------

@router.post("/callback")
async def callback(request: Request, background_tasks: BackgroundTasks):
    if not line_bot_api:
        raise HTTPException(status_code=500, detail="LINE Bot API not initialized")

    signature = request.headers.get("X-Line-Signature")
    body = (await request.body()).decode()

    try:
        events = parser.parse(body, signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent):
            if isinstance(event.message, TextMessageContent):
                user_id = event.source.user_id
                user = usermangement.get_user_by_line_id(user_id)

                if not user or not user.get("studentId"):
                    # ยังไม่ login หรือยังไม่ลงทะเบียน
                    auth_link = f"{request.base_url}api/ai/auth/line?origin=chat&user_id={user_id}"
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=f"🔐 กรุณา Login ก่อนใช้งาน\n{auth_link}")],
                        )
                    )
                else:
                    # ตอบ placeholder ไว้ก่อน
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="⏳ กำลังคิดคำตอบ...")],
                        )
                    )
                    background_tasks.add_task(process_message, event)
            else:
                await line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="ผมตอบได้เฉพาะข้อความตัวอักษรนะครับ 😊")],
                    )
                )
    return "OK"


async def process_message(event: MessageEvent):
    """ประมวลผล LLM และส่งคำตอบจริงกลับไปหาผู้ใช้"""
    try:
        user_id = event.source.user_id
        user_info = usermangement.get_user_by_line_id(user_id) or {}

        response = agent_executor.chat(event.message.text, user_id, user_info)

        if isinstance(response, dict):
            response_text = response.get("output") or response.get("content") or str(response)
        else:
            response_text = str(response)

        await line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=response_text)],
            )
        )
    except Exception as e:
        await line_bot_api.push_message(
            PushMessageRequest(
                to=event.source.user_id,
                messages=[TextMessage(text=f"⚠️ เกิดข้อผิดพลาด: {str(e)}")],
            )
        )


@router.post("/message/push/order-update")
async def send_order_update_message(
    data: pushMessageType,
    custom_message: Optional[str] = None
):
    """
    Send order update notification to user
    This can be called from your order service
    """
    bot_api = await get_line_bot_api()
    
    # Get order details (you'll need to implement this in your order service)
    try:
        order = order_service.GetOrder(data.order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        user = usermangement.get_user_by_user_id(order.get("userId"))
        if not user or not user.get("line_user_id"):
            raise HTTPException(status_code=404, detail="User not found or no LINE ID")
        
        # Create status-specific messages
        status_messages = {
            "pending": "✅ คำสั่งซื้อของคุณได้รับการยืนยันแล้ว",
            "making": "👨‍🍳 กำลังเตรียมอาหารของคุณ",
            "complete": "🎉 อาหารของคุณ ทำเสร็จแล้ว!!",
            "cancelled": "❌ คำสั่งซื้อของคุณถูกยกเลิก"
        }
        
        message = custom_message or status_messages.get(data.status, f"สถานะคำสั่งซื้อ: {data.status}")
        message += f"\n\nหมายเลขคำสั่งซื้อ: {data.order_id}"
        
        await bot_api.push_message(
            PushMessageRequest(
                to=user["line_user_id"],
                messages=[TextMessage(text=message)],
            )
        )
        
        return {"status": "success", "message": "Order update notification sent"}
        
    except Exception as e:
        # raise HTTPException(status_code=500, detail=f"Failed to send order update: {str(e)}")
        return HTMLResponse("User not found", status_code=200)


# ----------------------------- Authentication -----------------------------------

@router.get("/auth/line")
async def line_oauth_login(request: Request, origin: str = "chat", user_id: Optional[str] = None):
    """
    เริ่มต้นการ Login ด้วย LINE OAuth2
    origin = chat | web
    """
    state = token_state.generate_state_token()
    token_state.store_oauth_state(state, {"origin": origin, "user_id": user_id})
    
    print(REDIRECT_URI)

    params = {
        "response_type": "code",
        "client_id": LINE_LOGIN_CHANNEL_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "scope": "profile openid email",
    }

    auth_url = f"https://access.line.me/oauth2/v2.1/authorize?{urlencode(params)}"
    return RedirectResponse(url=auth_url)

@router.get("/auth/callback")
async def line_oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """รับ callback จาก LINE OAuth2"""

    if error:
        return HTMLResponse(f"<h2>❌ Authentication Error</h2><p>{error}</p>")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    # ตรวจสอบ state
    state_data = token_state.get_oauth_state(state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid state token")

    try:
        # แลก code เป็น access token
        token_data = await exchange_code_for_token(code)
        user_profile = await get_line_user_profile(token_data["access_token"])
        line_user_id = user_profile["userId"]

        # ตรวจสอบว่ามี user อยู่ในระบบหรือยัง
        user = usermangement.get_user_by_line_id(line_user_id)
        print(user)
        if not user:
            # สร้าง user ใหม่
            user_data = {
                "line_user_id": line_user_id,
                "display_name": user_profile["displayName"],
                "picture_url": user_profile.get("pictureUrl"),
                "email": user_profile.get("email"),
            }
            usermangement.create_user(user_data)

            # ยังไม่ได้ลงทะเบียน → ต้องไปกรอกข้อมูลเพิ่ม
            temp_token = create_access_token(
                data={"sub": line_user_id, "temp": True},
                expires_delta=datetime.timedelta(minutes=30)
            )
            return RedirectResponse(url=f"/api/ai/register?token={temp_token}")

        else:
            # อัปเดตข้อมูล user ถ้ามีการเปลี่ยนชื่อหรือรูป
            updated_data = {
                "line_user_id": line_user_id
            }
            if user_profile["displayName"] != user.get("display_name"):
                updated_data["display_name"] = user_profile["displayName"]
            if user_profile.get("pictureUrl") != user.get("picture_url"):
                updated_data["picture_url"] = user_profile.get("pictureUrl")

            if updated_data:
                usermangement.upsert_user(updated_data)

        # ออก JWT token
        jwt_token = create_access_token(data={"sub": line_user_id})

        # callback จาก chat
        if state_data["origin"] == "chat":
            try:
                await line_bot_api.push_message(
                    PushMessageRequest(
                        to=state_data["user_id"],
                        messages=[
                            TextMessage(
                                text=f"🎉 เข้าสู่ระบบสำเร็จ!\nยินดีต้อนรับ {user_profile['displayName']}"
                            )
                        ],
                    )
                )
            except:
                pass

            return HTMLResponse(
                f"<h2>✅ Login สำเร็จ</h2><p>กลับไปที่ LINE Chat เพื่อใช้งาน</p>"
            )

        # callback จาก web
        elif state_data["origin"] == "web":
            return RedirectResponse(url=f"{FRONTEND_URL}/callback?token={jwt_token}")

    except Exception as e:
        return HTMLResponse(f"<h2>❌ Authentication Error</h2><p>{str(e)}</p>")



@router.get("/register", response_class=HTMLResponse)
async def register_form(token: str):
    """แสดงฟอร์มลงทะเบียน"""
    line_user_id = verify_token(token)
    if not line_user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = usermangement.get_user_by_line_id(line_user_id)
    if not user:
        user = {
            "line_user_id": line_user_id,
            "display_name": "Guest",
            "picture_url": "",
            "email": "",
        }
        usermangement.create_user(user)

    return HTMLResponse(f"""
    <html>
      <body>
        <h2>📝 ลงทะเบียนผู้ใช้</h2>
        <form action="/api/ai/register" method="post">
          <input type="hidden" name="token" value="{token}"/>
          <label>Full Name:</label><br>
          <input type="text" name="full_name" required/><br><br>
          <label>Student ID:</label><br>
          <input type="text" name="studentId" required/><br><br>
          <label>Email:</label><br>
          <input type="text" name="email" required/><br><br>
          <button type="submit">Submit</button>
        </form>
        <hr>
        <p>LINE Display Name: {user.get("display_name","-")}</p>
        {"<img src='"+user.get("picture_url","")+"' width='100'/>" if user.get("picture_url") else ""}
      </body>
    </html>
    """)


@router.post("/register")
async def register_user(
    token: str = Form(...),
    full_name: str = Form(...),
    studentId: str = Form(...),
    email: str = Form(...),
):
    """บันทึกข้อมูลการลงทะเบียน"""
    line_user_id = verify_token(token)
    if not line_user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = usermangement.get_user_by_line_id(line_user_id)
    if not user:
        user = {
            "line_user_id": line_user_id,
            "display_name": full_name,
            "picture_url": "",
            "email": email,
        }
        usermangement.create_user(user)

    user_data = {
        "line_user_id": line_user_id,
        "studentId": studentId,
        "display_name": user.get("display_name", full_name),
        "picture_url": user.get("picture_url", ""),
        "email": email,
        "username": full_name,
    }
    usermangement.upsert_user(user_data)

    jwt_token = create_access_token(data={"sub": line_user_id})

    # แจ้งผ่าน LINE
    try:
        await line_bot_api.push_message(
            PushMessageRequest(
                to=line_user_id,
                messages=[TextMessage(text=f"✅ Login สำเร็จ! สวัสดี {full_name}")],
            )
        )
    except:
        pass

    return HTMLResponse(
        f"<h2>✅ Registration Complete</h2><p>สวัสดี {full_name}!</p>"
    )

@router.get("/users/me")
async def get_current_user(request: Request, token: str = Query(...)):
    """
    Return the user profile based on JWT token
    Frontend should call: /users/me?token=<jwt>
    """
    line_user_id = verify_token(token)
    if not line_user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = usermangement.get_user_by_line_id(line_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Don't return sensitive info like tokens
    
    print(user)
    
    return json.dumps({
        "user_id": str(user.get("_id")),
        "username": user.get("username") or user.get("display_name"),
        "email": user.get("email"),
        "display_name": user.get("display_name"),
        "picture_url": user.get("picture_url"),
        "studentId": user.get("studentId"),
    })


# ---------------------- Helpers -------------------------

async def exchange_code_for_token(code: str) -> Dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.line.me/oauth2/v2.1/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": LINE_LOGIN_CHANNEL_ID,
                "client_secret": LINE_LOGIN_CHANNEL_SECRET,
            },
        )
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {response.text}")
        return response.json()


async def get_line_user_profile(access_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.line.me/v2/profile",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Profile fetch failed: {response.text}")
        return response.json()
