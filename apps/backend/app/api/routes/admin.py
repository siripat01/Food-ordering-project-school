from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import AdminDependency, get_product_service, get_user_service
from app.domain.products import ProductListResponse
from app.domain.users import (
    UserActiveUpdate,
    UserListResponse,
    UserResponse,
    UserRoleUpdate,
)
from app.services.products import ProductService
from app.services.users import UserService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=UserListResponse)
async def list_users(
    _admin: AdminDependency,
    users: Annotated[UserService, Depends(get_user_service)],
) -> UserListResponse:
    result = await users.list_users()
    return UserListResponse(users=result, total=len(result))


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: str,
    payload: UserRoleUpdate,
    admin: AdminDependency,
    users: Annotated[UserService, Depends(get_user_service)],
) -> UserResponse:
    return await users.update_role(user_id=user_id, role=payload.role, actor_id=admin.id)


@router.patch("/users/{user_id}/active", response_model=UserResponse)
async def update_user_active(
    user_id: str,
    payload: UserActiveUpdate,
    admin: AdminDependency,
    users: Annotated[UserService, Depends(get_user_service)],
) -> UserResponse:
    return await users.update_active(user_id=user_id, active=payload.active, actor_id=admin.id)


@router.get("/products", response_model=ProductListResponse)
async def list_all_products(
    _admin: AdminDependency,
    products: Annotated[ProductService, Depends(get_product_service)],
) -> ProductListResponse:
    return await products.list_all()
