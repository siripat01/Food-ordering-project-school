import os
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any
from model.productSchema import ProductCreate, ProductUpdate
from service.product.product import ProductService

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")

router = APIRouter()
product_service = ProductService(MONGODB_URI)


# ---------- Pydantic Schemas ----------



# ---------- Routes ----------

# Create product
@router.post("/product")
def create_product(product: ProductCreate):
    product_id = product_service.CreateProduct(dict(product))
    return {"product_id": product_id}


# Get product by ID
# @router.get("/product/{product}")
# def get_product(product: str):
#     prod = product_service.GetProduct(product)
#     if not prod:
#         raise HTTPException(status_code=404, detail="Product not found")
#     prod["_id"] = str(prod["_id"])
#     return prod


@router.get("/product")
def get_all_products(
    product_id: str | None = Query(default=None),
    product_name: str | None = Query(default=None),
):
    if product_id:
        prod = product_service.GetProduct(product_id)
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")
        prod["_id"] = str(prod["_id"])
        return prod
    
    if product_name:
        prod = product_service.GetProductByName(product_name)
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")
        prod["_id"] = str(prod["_id"])
        return prod
    
    products = product_service.GetAllProducts()
    for p in products:
        p["_id"] = str(p["_id"])
    return {"products": products}


# Get products by status
@router.get("/product/status/{status}")
def get_products_by_status(status: str):
    products = product_service.GetProductsByStatus(status)
    for p in products:
        p["_id"] = str(p["_id"])
    return {"products": products}


# Update product
@router.put("/product/{product_id}")
def update_product(product_id: str, product: ProductUpdate):
    update_data = {k: v for k, v in dict(product).items() if v is not None}
    modified_count = 0

    if "product_name" in update_data:
        modified_count += product_service.UpdateProductName(product_id, update_data["product_name"])
    if "price" in update_data:
        modified_count += product_service.UpdateProductPrice(product_id, update_data["price"])
    if "status" in update_data:
        modified_count += product_service.UpdateProductStatus(product_id, update_data["status"])
    if "description" in update_data:
        modified_count += product_service.UpdateProductDescription(product_id, update_data["description"])
    if "image" in update_data:
        modified_count += product_service.UpdateProductImage(product_id, update_data["image"])

    if modified_count == 0:
        raise HTTPException(status_code=404, detail="Product not found or no changes applied")

    return {"modified_count": modified_count}


# Delete product
@router.delete("/product/{product_id}")
def delete_product(product_id: str):
    deleted_count = product_service.DeleteProduct(product_id)
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"deleted_count": deleted_count}
