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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    assembled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    on_way_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
