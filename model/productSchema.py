from typing import Optional

from pydantic import BaseModel


class ProductCreate(BaseModel):
    product_name: str
    price: float
    status: Optional[str] = "available"
    description: Optional[str] = None
    image: Optional[str] = None

class ProductUpdate(BaseModel):
    product_name: Optional[str] = None
    price: Optional[float] = None
    status: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None