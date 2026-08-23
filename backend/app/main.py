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
from app.poller import start_poller, stop_poller, start_hall_poller
from app.alerts import start_alerts, stop_alerts
from app.staffing_alerts import start_staffing_alerts
from app.late_report import start_late_reports
from app.preorder import start_releaser, stop_releaser
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
    ("orders", "complete_before", "TIMESTAMP"),
    ("orders", "preorder_held", "BOOLEAN DEFAULT FALSE"),
    ("order_items", "kitchen_zone", "VARCHAR"),
    ("order_items", "modifiers", "JSON"),
    ("delivery_zones", "auto_generated", "BOOLEAN DEFAULT FALSE"),
    ("telegram_links", "employee_id", "VARCHAR"),
    ("telegram_links", "claimed_on", "VARCHAR"),
]


async def _migrate_columns(conn):
    """Добиваем новые колонки в существующих таблицах (идемпотентно, по одной
    колонке, под try/except — чтобы старт не падал).

    Диалект-зависимо: SQLite НЕ понимает `ADD COLUMN IF NOT EXISTS` (это
    синтаксическая ошибка), поэтому для него проверяем наличие колонки через
    PRAGMA table_info и шлём plain `ADD COLUMN`. Если таблицы ещё нет — тихо
    пропускаем (новые таблицы создаёт create_all). Postgres поддерживает
    IF NOT EXISTS — для него оставляем прежнюю форму."""
    is_sqlite = conn.dialect.name == "sqlite"
    for table, col, ddl in _ADDED_COLUMNS:
        try:
            if is_sqlite:
                result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
                rows = result.fetchall()
                if not rows:
                    # Таблицы нет — пропускаем (create_all создаст её отдельно).
                    continue
                existing = {r[1] for r in rows}  # r[1] — имя колонки
                if col in existing:
                    continue
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            else:
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
    # Поллер зала: тянет заказы зала из Cloud (init_by_table -> by_table).
    # Отключён по умолчанию (HALL_POLL_INTERVAL_SECONDS<=0).
    fastapi_app.state.hall_poller_task = start_hall_poller()
    # Алерты менеджерам в Telegram: фоновая проверка просроченных заказов и
    # аномалий времени готовки. Отключены по умолчанию (ALERTS_INTERVAL_SECONDS<=0).
    fastapi_app.state.alerts_task = start_alerts()
    # Предупреждение управляющим о наплыве заказов: вечером на завтра, утром
    # на сегодня. Выключено, пока не заданы STAFFING_ALERT_ORG_ID и часы.
    fastapi_app.state.staffing_alerts_task = start_staffing_alerts()
    # Ежедневный отчёт об опозданиях в Telegram (в полночь за прошедший день).
    # Выключен, пока не заданы LATE_REPORT_ORG_ID и LATE_REPORT_CHAT_IDS.
    fastapi_app.state.late_report_task = start_late_reports()
    # Релизер предзаказов: снимает флаг preorder_held за lead-минут до срока.
    # Включён по умолчанию (preorder_check_seconds=60).
    fastapi_app.state.preorder_task = start_releaser()
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
    await stop_poller(getattr(fastapi_app.state, "hall_poller_task", None))
    await stop_alerts(getattr(fastapi_app.state, "alerts_task", None))
    await stop_alerts(getattr(fastapi_app.state, "staffing_alerts_task", None))
    await stop_alerts(getattr(fastapi_app.state, "late_report_task", None))
    await stop_releaser(getattr(fastapi_app.state, "preorder_task", None))


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
