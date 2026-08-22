from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import (
    AdminDependency,
    get_product_service,
)
from app.domain.products import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
)
from app.services.products import ProductService

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=ProductListResponse)
async def list_products(
    products: Annotated[ProductService, Depends(get_product_service)],
) -> ProductListResponse:
    return await products.list_available()


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: str,
    products: Annotated[ProductService, Depends(get_product_service)],
) -> ProductResponse:
    return await products.get(product_id)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    _admin: AdminDependency,
    products: Annotated[ProductService, Depends(get_product_service)],
) -> ProductResponse:
    return await products.create(payload)


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: str,
    payload: ProductUpdate,
    _admin: AdminDependency,
    products: Annotated[ProductService, Depends(get_product_service)],
) -> ProductResponse:
    return await products.update(product_id, payload)


@router.delete("/{product_id}", response_model=ProductResponse)
async def discontinue_product(
    product_id: str,
    _admin: AdminDependency,
    products: Annotated[ProductService, Depends(get_product_service)],
) -> ProductResponse:
    return await products.discontinue(product_id)
