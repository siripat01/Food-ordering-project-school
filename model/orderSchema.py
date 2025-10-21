from typing import Any, Dict, Optional, List

from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    product_name: str
    userId: str
    price: float = Field(..., ge=0)
    addon: Optional[List[str]] = []
    status: Optional[str] = "pending"
    description: Optional[str] = ""


class OrderUpdate(BaseModel):
    status: Optional[str] = None
    userId: Optional[str] = None
    addon: Optional[Dict[str, Any]] = None