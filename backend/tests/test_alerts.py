from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app import alerts, telegram
from app.db import Base
import app.models  # noqa: F401  (регистрирует таблицы)
from app.models import Order, OrderItem, TelegramLink
from app.config import settings


@pytest_asyncio.fixture
async def memory_maker(monkeypatch):
    """In-memory SQLite, подменяющий alerts.SessionLocal, чтобы check_once
    читал из реальной (но эфемерной) БД без сети."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(alerts, "SessionLocal", maker)
    # Чистый дедуп-набор на каждый тест.
    alerts._alerted.clear()
    yield maker
    await engine.dispose()


async def test_run_alerts_disabled_by_default(monkeypatch):
    """Без ALERTS_INTERVAL_SECONDS (или =0) run_alerts сразу возвращается без работы."""
    monkeypatch.delenv("ALERTS_INTERVAL_SECONDS", raising=False)
    await alerts.run_alerts()  # не должно зависнуть и ничего не отправить

    monkeypatch.setenv("ALERTS_INTERVAL_SECONDS", "0")
    await alerts.run_alerts()


async def test_check_once_overdue_alerts_manager_and_dedups(memory_maker, monkeypatch):
    """Просроченный заказ -> сообщение менеджеру; повторный check_once не дублирует."""
    settings.telegram_bot_token = "test-token"  # send_message замокан, в сеть не ходим

    sent: list[tuple[str, str]] = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(telegram, "send_message", fake_send)

    # Сидируем: менеджер org "o1" + просроченный заказ (принято 15 мин назад, не готово).
    async with memory_maker() as s:
        s.add(TelegramLink(
            telegram_id="555", employee_code="E1", name="Mgr",
            branch_code=None, org_id="o1", role="MN0",
        ))
        order = Order(
            iiko_order_id="oo1", org_id="o1", number="42", status="OnCooking",
            created_at=datetime.utcnow(),
        )
        order.items = [OrderItem(
            iiko_item_id="i1", product_id="p1", name="Ролл",
            station_accepted_at=datetime.utcnow() - timedelta(minutes=15),
            station_ready_at=None,
        )]
        s.add(order)
        await s.commit()

    await alerts.check_once()

    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "555"
    assert "42" in text  # упомянут номер заказа

    # Повторный проход не должен слать снова (дедуп).
    await alerts.check_once()
    assert len(sent) == 1
