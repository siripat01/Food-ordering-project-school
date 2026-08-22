from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings
from app.core.security import TokenError, TokenType, decode_token
from app.db.mongodb import MongoDatabase
from app.domain.users import CurrentUser, Role
from app.services.order_updates import OrderEventBroker
from app.services.orders import OrderService
from app.services.products import ProductService
from app.services.recommendations import RecommendationService
from app.services.users import UserService


async def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def get_db(request: Request) -> MongoDatabase:
    return cast(MongoDatabase, request.app.state.db)


async def get_user_service(request: Request) -> UserService:
    return cast(UserService, request.app.state.users)


async def get_product_service(request: Request) -> ProductService:
    return cast(ProductService, request.app.state.products)


async def get_order_service(request: Request) -> OrderService:
    return cast(OrderService, request.app.state.orders)


async def get_order_event_broker(request: Request) -> OrderEventBroker:
    return cast(OrderEventBroker, request.app.state.order_events)


async def get_recommendation_service(request: Request) -> RecommendationService:
    return cast(RecommendationService, request.app.state.recommendations)


async def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    users: Annotated[UserService, Depends(get_user_service)],
) -> CurrentUser:
    authorization = request.headers.get("Authorization", "")
    bearer_token = (
        authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
    )
    token = bearer_token or request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(token, expected_type=TokenType.ACCESS, settings=settings)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = await users.get_by_id(str(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication identity is no longer active",
        )
    return user


def require_roles(
    *allowed: Role,
) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    async def dependency(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return dependency


CurrentUserDependency = Annotated[CurrentUser, Depends(get_current_user)]
CustomerDependency = Annotated[CurrentUser, Depends(require_roles(Role.CUSTOMER))]
StaffDependency = Annotated[CurrentUser, Depends(require_roles(Role.STAFF, Role.ADMIN))]
AdminDependency = Annotated[CurrentUser, Depends(require_roles(Role.ADMIN))]
