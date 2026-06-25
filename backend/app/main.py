from __future__ import annotations
import os
import logging
# Делаем логи приложения (saya.*) видимыми в Render — по умолчанию uvicorn
# настраивает только свои логгеры, и наши INFO/WARNING не выводятся.
logging.basicConfig(level=logging.INFO)
import socketio
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api import app as fastapi_app
from app.realtime import sio
from app.db import engine, Base
from app.poller import start_poller, stop_poller
from app.alerts import start_alerts, stop_alerts
import app.models  # noqa: F401


# Колонки, добавленные после первого деплоя. create_all не меняет существующие
# таблицы, поэтому добиваем их вручную (идемпотентно). (таблица, колонка, DDL-тип).
_ADDED_COLUMNS = [
    ("orders", "comment", "VARCHAR DEFAULT ''"),
    ("order_items", "comment", "VARCHAR DEFAULT ''"),
    ("orders", "iiko_sync_error", "VARCHAR"),
    ("order_events", "org_id", "VARCHAR"),
    ("order_events", "actor_code", "VARCHAR"),
    ("order_events", "actor_name", "VARCHAR"),
    ("order_events", "item_id", "INTEGER"),
    ("orders", "payment_type_id", "VARCHAR"),
    ("orders", "payment_type_kind", "VARCHAR"),
    ("orders", "payment_type_name", "VARCHAR"),
]


async def _migrate_columns(conn):
    """ADD COLUMN IF NOT EXISTS для новых полей. Каждый ALTER отдельно и под
    try/except, чтобы старт не падал, если БД не поддерживает IF NOT EXISTS."""
    for table, col, ddl in _ADDED_COLUMNS:
        try:
            await conn.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}")
        except Exception:  # noqa: BLE001 — колонка уже есть / старый диалект
            pass


@fastapi_app.on_event("startup")
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)
    # Фоллбэк-поллер: периодически опрашивает iiko by_revision, чтобы добрать
    # заказы, пропущенные вебхуками. Отключён по умолчанию (POLL_INTERVAL_SECONDS<=0).
    fastapi_app.state.poller_task = start_poller()
    # Алерты менеджерам в Telegram: фоновая проверка просроченных заказов и
    # аномалий времени готовки. Отключены по умолчанию (ALERTS_INTERVAL_SECONDS<=0).
    fastapi_app.state.alerts_task = start_alerts()
    # NB: подписку на вебхуки (update_settings) на старте НЕ дёргаем — она persists
    # в iiko и нужна разово, а iiko жёстко рейт-лимитит этот вызов (429). Применяем
    # вручную через POST /webhooks/register.
    # Прогреваем кэш меню RMS, чтобы резолвер зон сразу классифицировал по группам
    # (а не по грубым keyword'ам). Фоном, под guard — RMS-сбой не валит старт.
    import asyncio as _asyncio
    fastapi_app.state.menu_warm_task = _asyncio.create_task(_warm_menu_cache())


async def _warm_menu_cache():
    import logging
    try:
        from app import rms
        await rms.fetch_products()
    except Exception:  # noqa: BLE001
        logging.getLogger("saya.menu").warning("menu cache warm failed", exc_info=True)


@fastapi_app.on_event("shutdown")
async def _stop_poller():
    await stop_poller(getattr(fastapi_app.state, "poller_task", None))
    await stop_alerts(getattr(fastapi_app.state, "alerts_task", None))


# ──────────────────────────────────────────────────────────────────────────
# Отдаём собранный SPA тем же процессом, чтобы один ngrok-туннель обслуживал
# и API, и mini-app. Catch-all регистрируется ПОСЛЕ всех API-роутов
# (они определены при импорте app.api выше).
# ──────────────────────────────────────────────────────────────────────────
DIST = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")

# Префиксы, которые обслуживает API и которые SPA-роут НЕ должен перехватывать.
# Только чисто-API namespace'ы. Имена, которые ОДНОВРЕМЕННО являются маршрутами
# фронта (/admin, /branches, /tg, /stations), сюда НЕ входят: их под-роуты
# (/admin/analytics, /tg/auth, ...) уже зарегистрированы и матчатся раньше catch-all,
# а сама страница (/admin, /tg) должна отдавать index.html для SPA.
_API_PREFIXES = (
    "health", "orders", "items", "webhooks", "debug", "auth",
    "rms", "assets", "docs", "openapi.json", "redoc",
)

if os.path.isdir(DIST):
    fastapi_app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(DIST, "assets")),
        name="assets",
    )

    @fastapi_app.get("/{full_path:path}")
    async def spa(full_path: str):
        # Не затеняем уже зарегистрированные API-роуты.
        first = full_path.split("/", 1)[0]
        if first in _API_PREFIXES:
            raise HTTPException(status_code=404, detail="not found")
        index = os.path.join(DIST, "index.html")
        return FileResponse(index)


# Socket.IO оборачивает FastAPI: один ASGI-апп на uvicorn.
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
