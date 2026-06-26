# Pre-order Hold + Analytics Local Time — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Предзаказы (заказы на будущее время) появляются на доске KDS только за час до срока; аналитика показывает часы/дни в локальном времени Казахстана (UTC+5).

**Architecture:** Тащим iiko `completeBefore` (локальное время филиала) в `Order.complete_before` (приводя к UTC). Флаг `Order.preorder_held` ставится на ingest, если срок дальше `now+lead`. Придержанные не broadcast'ятся и не отдаются в `/orders`. Фоновая петля `preorder.py` (дефолт ВКЛ) снимает флаг и broadcast'ит заказ за час до срока. Аналитика сдвигает таймстампы на `IIKO_TZ_OFFSET_HOURS` при бакетировании.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest; конфиг через pydantic-settings.

## Global Constraints
- iiko-время — локальное время филиала (Казахстан UTC+5), naive, формат `YYYY-MM-DD HH:MM:SS[.fff]`. Хранить `complete_before` в naive UTC (минус `IIKO_TZ_OFFSET_HOURS`), как `created_at`.
- Дефолты (env): `IIKO_TZ_OFFSET_HOURS=5`, `PREORDER_LEAD_MINUTES=60`, `PREORDER_CHECK_SECONDS=60`.
- «Придержан» = `complete_before is not None AND complete_before > now_utc + lead`. Релизер выпускает при `complete_before <= now_utc + lead`.
- Терминальные статусы (везде одинаково): `Closed`, `Deleted`, `Cancelled`.
- Доска-онли: печать (`iiko-print-capture`) НЕ трогаем. Фронт не трогаем. YAGNI, TDD, частые коммиты.

---

### Task 1: Захват `completeBefore` (конфиг, схема, модель, нормализация, миграция)

**Files:** Modify `backend/app/config.py`, `backend/app/schemas.py`, `backend/app/iiko_cloud.py`, `backend/app/models.py`, `backend/app/main.py`; Test `backend/tests/test_iiko_cloud.py`

**Interfaces produced:**
- `settings.iiko_tz_offset_hours: int = 5`, `settings.preorder_lead_minutes: int = 60`, `settings.preorder_check_seconds: int = 60`.
- `iiko_cloud._parse_iiko_local_to_utc(s, offset_hours) -> datetime | None`.
- `NormalizedOrder.complete_before: datetime | None`.
- `Order.complete_before: datetime | None`, `Order.preorder_held: bool` (default False).

- [ ] **Step 1: Failing test** — в `backend/tests/test_iiko_cloud.py`:
```python
from datetime import datetime

def test_parse_iiko_local_to_utc():
    from app.iiko_cloud import _parse_iiko_local_to_utc
    assert _parse_iiko_local_to_utc("2026-06-25 18:01:04.638", 5) == datetime(2026, 6, 25, 13, 1, 4, 638000)
    assert _parse_iiko_local_to_utc("2026-06-25 18:01:04", 5) == datetime(2026, 6, 25, 13, 1, 4)
    assert _parse_iiko_local_to_utc("", 5) is None
    assert _parse_iiko_local_to_utc("garbage", 5) is None

def test_normalize_extracts_complete_before_as_utc():
    from app.iiko_cloud import normalize_full_order
    wrapper = {"id": "o1", "order": {"number": "5", "sum": 100,
               "completeBefore": "2026-06-25 18:00:00"}}
    n = normalize_full_order(wrapper, "org-1")
    assert n.complete_before == datetime(2026, 6, 25, 13, 0, 0)  # -5h
```
- [ ] **Step 2: Run, expect FAIL** — `cd backend && .venv/bin/pytest tests/test_iiko_cloud.py -k "complete_before or local_to_utc" -v`
- [ ] **Step 3: Implement:**
  - `config.py` — в класс `Settings` добавить:
    ```python
    iiko_tz_offset_hours: int = 5
    preorder_lead_minutes: int = 60
    preorder_check_seconds: int = 60
    ```
  - `schemas.py` — в `NormalizedOrder` добавить (нужен импорт `from datetime import datetime` сверху, если нет):
    ```python
    complete_before: datetime | None = None
    ```
  - `iiko_cloud.py` — добавить хелпер (рядом с другими модульными функциями) и импорт `from datetime import datetime, timedelta` (если нет):
    ```python
    def _parse_iiko_local_to_utc(s, offset_hours: int) -> datetime | None:
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(s).strip(), fmt) - timedelta(hours=offset_hours)
            except ValueError:
                continue
        return None
    ```
    В `normalize_full_order` И `normalize_table_order` перед `return NormalizedOrder(...)` добавить:
    ```python
        complete_before = _parse_iiko_local_to_utc(
            o.get("completeBefore"), settings.iiko_tz_offset_hours)
    ```
    и в конструктор `NormalizedOrder(... complete_before=complete_before)`.
  - `models.py` — в `Order` добавить (`Boolean` уже импортирован):
    ```python
    complete_before: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    preorder_held: Mapped[bool] = mapped_column(Boolean, default=False)
    ```
  - `main.py` — в `_ADDED_COLUMNS`:
    ```python
    ("orders", "complete_before", "TIMESTAMP"),
    ("orders", "preorder_held", "BOOLEAN DEFAULT FALSE"),
    ```
- [ ] **Step 4: Run, expect PASS**; затем `tests/test_iiko_cloud.py tests/test_models.py -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(preorder): захват completeBefore (локальное->UTC) + поля модели"`

---

### Task 2: `is_held` + ingest проставляет held

**Files:** Modify `backend/app/ingest.py`; Test `backend/tests/test_ingest.py`

**Interfaces produced:** `ingest.is_held(complete_before, now, lead_minutes) -> bool`. `ingest_order` проставляет `order.complete_before` и `order.preorder_held`.

- [ ] **Step 1: Failing test** — в `backend/tests/test_ingest.py`:
```python
from datetime import datetime, timedelta

def test_is_held():
    from app.ingest import is_held
    now = datetime(2026, 6, 26, 10, 0, 0)
    assert is_held(None, now, 60) is False
    assert is_held(now + timedelta(minutes=90), now, 60) is True   # дальше часа
    assert is_held(now + timedelta(minutes=30), now, 60) is False  # в пределах часа
    assert is_held(now - timedelta(minutes=10), now, 60) is False  # в прошлом

async def test_ingest_sets_preorder_held(session):
    from app.ingest import ingest_order
    from app.schemas import NormalizedOrder
    far = datetime.utcnow() + timedelta(hours=5)
    order = await ingest_order(session, NormalizedOrder(
        iiko_order_id="pre1", org_id="org-1", number="9", complete_before=far))
    assert order.preorder_held is True and order.complete_before == far
    near = await ingest_order(session, NormalizedOrder(
        iiko_order_id="now1", org_id="org-1", number="10"))
    assert near.preorder_held is False
```
- [ ] **Step 2: Run, expect FAIL** — `cd backend && .venv/bin/pytest tests/test_ingest.py -k "is_held or preorder_held" -v`
- [ ] **Step 3: Implement** — в `backend/app/ingest.py`:
  - импорты сверху: `from datetime import datetime, timedelta` и `from app.config import settings`.
  - добавить функцию:
    ```python
    def is_held(complete_before, now: datetime, lead_minutes: int) -> bool:
        return complete_before is not None and complete_before > now + timedelta(minutes=lead_minutes)
    ```
  - в `ingest_order`, рядом с присвоением полей (после `order.payment_type_name = data.payment_type_name`) добавить:
    ```python
    order.complete_before = data.complete_before
    order.preorder_held = is_held(data.complete_before, datetime.utcnow(),
                                  settings.preorder_lead_minutes)
    ```
- [ ] **Step 4: Run, expect PASS**; затем `tests/test_ingest.py -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(preorder): is_held + ingest проставляет complete_before/preorder_held"`

---

### Task 3: Не показывать придержанные (broadcast-гейт + фильтр /orders)

**Files:** Modify `backend/app/api.py` (вебхук: ветки TableOrderUpdate и DeliveryOrderUpdate; `GET /orders`), `backend/app/poller.py`; Test `backend/tests/test_webhook.py`, `backend/tests/test_order_actions.py` (или где тест /orders)

**Interfaces:** на ingest broadcast только если `not order.preorder_held`; `/orders` исключает `preorder_held == True`.

- [ ] **Step 1: Failing tests:**
  - В `backend/tests/test_webhook.py` (см. как там вызывается вебхук; мок `broadcast_order`) — тест: предзаказ (DeliveryOrderUpdate с `completeBefore` далеко в будущем) ingest'ится, но `realtime.broadcast_order` НЕ вызывается; обычный — вызывается. Используй monkeypatch/respx по образцу существующих тестов файла. Если структура теста вебхука отличается — следуй ей, суть: held → нет broadcast.
  - В тесте `/orders` (там, где он есть) — создать два заказа (один `preorder_held=True`), проверить, что `GET /orders?org_id=...` возвращает только не-held.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:**
  - `api.py` вебхук, ветка TableOrderUpdate — заменить `await realtime.broadcast_order(order)` на:
    ```python
            if not order.preorder_held:
                await realtime.broadcast_order(order)
    ```
  - `api.py` вебхук, ветка DeliveryOrderUpdate — то же самое для её `broadcast_order`.
  - `api.py` `GET /orders` — к существующему `.where(...)` фильтру статусов добавить `_Order.preorder_held == False` (в тот же `where`).
  - `poller.py` `poll_once` — обернуть `await realtime.broadcast_order(order)`:
    ```python
                if not order.preorder_held:
                    await realtime.broadcast_order(order)
    ```
- [ ] **Step 4: Run, expect PASS**; затем ВЕСЬ backend `.venv/bin/pytest -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(preorder): придержанные не broadcast'ятся и не отдаются в /orders"`

---

### Task 4: Релизер — выпускать предзаказ за час до срока

**Files:** Create `backend/app/preorder.py`; Modify `backend/app/main.py`; Test `backend/tests/test_preorder.py`

**Interfaces produced:** `preorder.release_due(session, now) -> int` (число выпущенных); `preorder.start_releaser()/stop_releaser()`.

- [ ] **Step 1: Failing test** — `backend/tests/test_preorder.py`:
```python
from datetime import datetime, timedelta
import respx
from sqlalchemy import select
from app.models import Order


@respx.mock
async def test_release_due_releases_in_window(session, monkeypatch):
    from app import preorder, realtime
    sent = []
    async def fake_broadcast(order): sent.append(order.id)
    monkeypatch.setattr(realtime, "broadcast_order", fake_broadcast)
    now = datetime(2026, 6, 26, 10, 0, 0)
    # в окне (<= now+60м) и held -> выпустить
    o1 = Order(iiko_order_id="a", org_id="org-1", number="1", status="WaitCooking",
               preorder_held=True, complete_before=now + timedelta(minutes=30))
    # ещё далеко -> не трогать
    o2 = Order(iiko_order_id="b", org_id="org-1", number="2", status="WaitCooking",
               preorder_held=True, complete_before=now + timedelta(hours=3))
    # терминальный -> пропустить
    o3 = Order(iiko_order_id="c", org_id="org-1", number="3", status="Closed",
               preorder_held=True, complete_before=now + timedelta(minutes=5))
    session.add_all([o1, o2, o3]); await session.commit()

    n = await preorder.release_due(session, now)
    assert n == 1 and sent == [o1.id]
    refreshed = {o.iiko_order_id: o for o in (await session.execute(select(Order))).scalars()}
    assert refreshed["a"].preorder_held is False
    assert refreshed["b"].preorder_held is True
```
(Учти `preorder_lead_minutes=60` по умолчанию.)
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — `backend/app/preorder.py` (по образцу `poller.py`):
```python
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from app import realtime
from app.config import settings
from app.db import SessionLocal
from app.models import Order

log = logging.getLogger("saya.preorder")
_TERMINAL = ("Closed", "Deleted", "Cancelled")


async def release_due(session, now: datetime) -> int:
    lead = settings.preorder_lead_minutes
    rows = (await session.execute(
        select(Order).where(Order.preorder_held == True,  # noqa: E712
                            Order.complete_before != None,  # noqa: E711
                            Order.complete_before <= now + timedelta(minutes=lead),
                            Order.status.notin_(_TERMINAL)))).scalars().all()
    for o in rows:
        o.preorder_held = False
    if rows:
        await session.commit()
    for o in rows:
        await realtime.broadcast_order(o)
    return len(rows)


async def run_releaser() -> None:
    interval = settings.preorder_check_seconds
    if interval <= 0:
        log.info("preorder releaser disabled (interval=%s)", interval)
        return
    log.info("preorder releaser started: interval=%ss", interval)
    while True:
        try:
            async with SessionLocal() as session:
                await release_due(session, datetime.utcnow())
        except Exception:  # noqa: BLE001 — устойчивость
            log.exception("preorder releaser failed")
        await asyncio.sleep(interval)


def start_releaser() -> asyncio.Task:
    return asyncio.create_task(run_releaser())


async def stop_releaser(task) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```
  - `main.py` — импорт `from app.preorder import start_releaser, stop_releaser`; в startup рядом с poller/alerts: `fastapi_app.state.preorder_task = start_releaser()`; в shutdown — `await stop_releaser(getattr(fastapi_app.state, "preorder_task", None))` (по образцу остановки poller/alerts).
- [ ] **Step 4: Run, expect PASS**; затем ВЕСЬ backend `.venv/bin/pytest -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(preorder): фоновый релизер — выпуск предзаказа за час до срока"`

---

### Task 5: Аналитика в локальном времени (UTC+5) — часы начинаются с 11, а не с 5

**Files:** Modify `backend/app/analytics_stages.py` (`sla_breaches`), `backend/app/api.py` (`/admin/stage-stats`, `/admin/forecast`); Test `backend/tests/test_analytics_stages.py`

**Interfaces:** `sla_breaches(orders, threshold_sec, tz_offset_hours=0)` — бакеты `by_day`/`by_hour` по локальному времени. `/admin/forecast` бакетит по локальному часу.

- [ ] **Step 1: Failing test** — в `backend/tests/test_analytics_stages.py`:
```python
def test_sla_breaches_local_hour_offset():
    from datetime import datetime
    from app import analytics_stages as A
    from app.models import Order
    o = Order(iiko_order_id="1", org_id="org-1", number="1")
    o.created_at = datetime(2026, 6, 26, 6, 0)   # 06:00 UTC == 11:00 local (+5)
    o.delivered_at = datetime(2026, 6, 26, 7, 0)  # 60м -> breach при пороге 45м
    o.items = []
    r = A.sla_breaches([o], threshold_sec=45 * 60, tz_offset_hours=5)
    hours = {h["hour"]: h for h in r["by_hour"]}
    assert 11 in hours and 6 not in hours          # локальный час 11, не UTC 6
    days = {d["date"]: d for d in r["by_day"]}
    assert "2026-06-26" in days
```
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:**
  - `analytics_stages.py` `sla_breaches` — добавить параметр `tz_offset_hours: int = 0`; внутри цикла локализовать время заказа перед бакетированием:
    ```python
        local = o.created_at + timedelta(hours=tz_offset_hours)
        day = local.strftime("%Y-%m-%d")
        hour = local.hour
    ```
    (заменить прежние `o.created_at.strftime(...)` / `o.created_at.hour` на `local`). `timedelta` уже импортирован? если нет — `from datetime import timedelta` сверху.
  - `api.py` `/admin/stage-stats` — в вызов `_stages.sla_breaches(orders, sla_minutes * 60)` добавить `tz_offset_hours=settings.iiko_tz_offset_hours`.
  - `api.py` `/admin/forecast` — найти бакетирование по `created_at.hour` (24 ячейки) и заменить на локальный час: `hour = (o.created_at + timedelta(hours=settings.iiko_tz_offset_hours)).hour` (импортировать `timedelta`/`settings`, если не импортированы; `settings` уже импортирован в api.py). Если у forecast есть тест в `tests/` — обнови его под локальный сдвиг, НЕ ослабляя.
- [ ] **Step 4: Run, expect PASS** (новый тест + существующий `test_sla_breaches` с дефолтным offset=0 остаётся зелёным); затем ВЕСЬ backend `.venv/bin/pytest -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(analytics): часы/дни в локальном времени (UTC+5) — по просьбе заказчика"`

---

### Task 6: Ручная проверка (на тебе)
**Не код.** 1) Создай в iiko предзаказ на время через 2-3 часа → убедись, что на доске KDS его НЕТ; за час до срока он появляется сам. 2) Обычный заказ — появляется сразу, как раньше. 3) В аналитике (`/analytics`) часы на графиках идут по локальному времени (открытие ~11:00, не 06:00).

## Self-Review
- Покрытие спеки: §4.1 захват → Task 1; §4.2 is_held → Task 2; §4.3 broadcast-гейт → Task 3; §4.4 фильтр /orders → Task 3; §4.5 релизер → Task 4; §4.6 конфиг → Task 1. Аналитика (прямая просьба заказчика, вне спеки предзаказов) → Task 5.
- Типы/имена: `_parse_iiko_local_to_utc(s, offset)`, `is_held(complete_before, now, lead_minutes)`, `release_due(session, now)`, `Order.complete_before/preorder_held`, settings `iiko_tz_offset_hours/preorder_lead_minutes/preorder_check_seconds`, терминальные статусы `Closed/Deleted/Cancelled` — едины во всех задачах.
- Плейсхолдеров нет; места «как устроен тест вебхука/forecast» — явные указания сверить с реальным кодом (структуры известны: вебхук ingest+broadcast, forecast бакетит created_at.hour).
