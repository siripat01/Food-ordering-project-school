from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUserDependency, get_settings, get_user_service
from app.core.config import Settings
from app.core.security import TokenError, TokenType, decode_token
from app.domain.users import CurrentUser
from app.integrations.line import LineOAuthClient, LineUpstreamError
from app.services.auth_sessions import AuthSessionService
from app.services.oauth import OAuthStateService
from app.services.users import UserService

router = APIRouter(tags=["authentication"])


@router.get("/auth/line")
async def start_line_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    origin: Literal["web", "chat"] = "web",
    chat_ticket: str | None = Query(default=None),
) -> RedirectResponse:
    if not settings.line_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LINE authentication is disabled",
        )
    chat_user_id: str | None = None
    if origin == "chat":
        if not chat_ticket:
            raise HTTPException(status_code=400, detail="Chat login ticket is required")
        try:
            payload = decode_token(
                chat_ticket, expected_type=TokenType.LINE_CHAT, settings=settings
            )
        except TokenError as exc:
            raise HTTPException(status_code=400, detail="Invalid chat login ticket") from exc
        chat_user_id = str(payload["sub"])

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    oauth_states: OAuthStateService = request.app.state.oauth_states
    await oauth_states.store(
        state=state,
        nonce=nonce,
        origin=origin,
        chat_user_id=chat_user_id,
    )
    oauth_client: LineOAuthClient = request.app.state.line_oauth
    return RedirectResponse(oauth_client.authorization_url(state=state, nonce=nonce))


@router.get("/auth/line/callback")
async def line_login_callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    users: Annotated[UserService, Depends(get_user_service)],
    code: str | None = Query(default=None),
    state_token: str | None = Query(default=None, alias="state"),
    error: str | None = Query(default=None),
) -> Response:
    if error:
        return HTMLResponse("<h2>Authentication was cancelled or denied.</h2>", status_code=400)
    if not code or not state_token:
        raise HTTPException(status_code=400, detail="Missing OAuth callback parameters")
    oauth_states: OAuthStateService = request.app.state.oauth_states
    state_data = await oauth_states.consume(state_token)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    try:
        claims = await request.app.state.line_oauth.exchange_and_verify(
            code=code, nonce=state_data["nonce"]
        )
    except LineUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LINE authentication could not be completed",
        ) from exc

    if state_data.get("origin") == "chat" and state_data.get("chatUserId") != claims.get("sub"):
        raise HTTPException(status_code=400, detail="LINE identity does not match chat login")

    user = await users.upsert_line_user(
        line_user_id=str(claims["sub"]),
        display_name=str(claims.get("name") or "LINE user"),
        picture_url=claims.get("picture"),
        email=claims.get("email"),
    )
    if state_data.get("origin") == "chat":
        chat_user_id = state_data.get("chatUserId")
        if isinstance(chat_user_id, str):
            await request.app.state.line_bot.push_text(
                line_user_id=chat_user_id,
                text="เข้าสู่ระบบสำเร็จ กลับไปที่แชตเพื่อสั่งอาหารได้เลยครับ",
            )
        return HTMLResponse("<h2>Login successful. You can return to LINE.</h2>")

    sessions: AuthSessionService = request.app.state.auth_sessions
    access_token, refresh_token = await sessions.issue(user.id)
    response = RedirectResponse(
        url=f"{str(settings.frontend_url).rstrip('/')}/callback",
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        key="access_token",
        value=access_token,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )
    return response


@router.post("/auth/refresh", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def refresh(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required"
        )
    sessions: AuthSessionService = request.app.state.auth_sessions
    try:
        tokens = await sessions.rotate(refresh_token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from exc
    if tokens is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired or revoked",
        )
    access_token, rotated_refresh_token = tokens
    response.set_cookie(
        key="access_token",
        value=access_token,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=rotated_refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )
    return response


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def logout(
    request: Request, response: Response, settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            claims = decode_token(
                access_token, expected_type=TokenType.ACCESS, settings=settings
            )
            sessions: AuthSessionService = request.app.state.auth_sessions
            await sessions.revoke(claims)
        except TokenError:
            pass
    response.delete_cookie(
        "access_token",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        "refresh_token",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/users/me", response_model=CurrentUser)
async def current_user(user: CurrentUserDependency) -> CurrentUser:
    return user
