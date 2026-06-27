from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field


class WebhookItem(BaseModel):
    product_id: str = ""
    category_id: str | None = None
    category_name: str = ""  # категория блюда из iiko (Роллы/Пиццы/Горячее) — для зоны
    name: str = ""
    amount: float = 1
    combo_id: str | None = None
    comment: str = ""  # пожелание по блюду («без лука»)


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
    comment: str = ""  # комментарий клиента к заказу («без помидор»)
    payment_type_id: str | None = None
    payment_type_kind: str | None = None
    payment_type_name: str | None = None
    complete_before: datetime | None = None
    # Тайм-штампы жизненного цикла из iiko (UTC). Нужны, чтобы этапы аналитики
    # (Очередь/Сборка/Ожидание/Доставка) заполнялись, даже если курьер не тапает
    # кнопки в KDS. Заполняются в ingest по правилу «только если поле пусто».
    when_created: datetime | None = None        # -> created_at (только при вставке)
    when_cooking_completed: datetime | None = None  # -> assembled_at (fallback)
    when_sended: datetime | None = None         # -> on_way_at (fallback)
    when_delivered: datetime | None = None      # -> delivered_at (fallback)
    items: list[WebhookItem] = Field(default_factory=list)
