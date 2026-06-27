from __future__ import annotations
from datetime import datetime
from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    server_department_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    server_employee_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="")
    org_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    iiko_order_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    org_id: Mapped[str] = mapped_column(String, index=True)
    number: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="")
    customer_name: Mapped[str] = mapped_column(String, default="")
    phone: Mapped[str] = mapped_column(String, default="")
    address: Mapped[str] = mapped_column(String, default="")
    address_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    address_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    service_type: Mapped[str | None] = mapped_column(String, nullable=True)
    comment: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    assembled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    on_way_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_courier_code: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    assigned_courier_name: Mapped[str | None] = mapped_column(String, nullable=True)
    iiko_sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    payment_type_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payment_type_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    payment_type_name: Mapped[str | None] = mapped_column(String, nullable=True)
    complete_before: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    preorder_held: Mapped[bool] = mapped_column(Boolean, default=False)
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    iiko_item_id: Mapped[str] = mapped_column(String, default="")
    product_id: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String, default="")
    amount: Mapped[float] = mapped_column(Float, default=1)
    station: Mapped[str] = mapped_column(String, default="other")
    kitchen_zone: Mapped[str | None] = mapped_column(String, nullable=True)
    comment: Mapped[str] = mapped_column(String, default="")
    combo_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    station_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    station_ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    order: Mapped["Order"] = relationship(back_populates="items")


class ProductStationMap(Base):
    __tablename__ = "product_station_map"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    category_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    station: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("product_id", "category_id", name="uq_psm"),)


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    org_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_code: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String, nullable=True)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TelegramLink(Base):
    __tablename__ = "telegram_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    employee_code: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, default="")
    branch_code: Mapped[str | None] = mapped_column(String, nullable=True)
    org_id: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)


class CourierLocation(Base):
    __tablename__ = "courier_locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    courier_code: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    branch_code: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StopListItem(Base):
    """Позиция стоп-листа (нет в наличии) для филиала. На каждый StopListUpdate
    строки филиала полностью заменяются (см. webhook-обработчик)."""
    __tablename__ = "stop_list"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String, index=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    polygon: Mapped[list] = mapped_column(JSON, default=list)  # [[lat, lon], ...]
    color: Mapped[str] = mapped_column(String, default="#2563eb")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
