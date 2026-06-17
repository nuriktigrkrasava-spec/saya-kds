from __future__ import annotations
from pydantic import BaseModel, Field


class WebhookItem(BaseModel):
    product_id: str = ""
    category_id: str | None = None
    name: str = ""
    amount: float = 1
    combo_id: str | None = None


class NormalizedOrder(BaseModel):
    iiko_order_id: str
    org_id: str
    number: str = ""
    status: str = ""
    customer: str = ""
    phone: str = ""
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    total: float | None = None
    service_type: str | None = None
    items: list[WebhookItem] = Field(default_factory=list)
