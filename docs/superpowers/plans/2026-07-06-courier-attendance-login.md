# Вход курьера без PIN (по открытой явке iiko) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В Telegram-аппе курьера заменить вход по PIN на выбор своего имени из списка сотрудников с открытой сегодня явкой в iiko (филиал «Гейдар Алиева»).

**Architecture:** Бэкенд читает явки из RMS resto API (`/api/employees/attendance/byDepartment/{code}`), фильтрует «открыта И начата сегодня», джойнит с сотрудниками, исключает кухонные/кассирские роли. Два новых эндпоинта: `GET /tg/shift-employees` (список + метка «занято») и `POST /tg/claim` (вход по employee_id, выдаёт тот же ответ, что старый `/tg/link`). Привязка — существующая модель `TelegramLink` + 2 новые колонки.

**Tech Stack:** FastAPI, SQLAlchemy async, httpx (+respx в тестах), React (Vite), Telegram WebApp.

**Spec:** `docs/superpowers/specs/2026-07-06-courier-attendance-login-design.md`

## Global Constraints

- iiko — только чтение; слот лицензии RMS беречь: кэш ~60с, один login/logout на пачку запросов (паттерн `_EMP_CACHE`).
- Локальный день = UTC + `settings.iiko_tz_offset_hours` (=5), как `_local_today()` в `api.py`.
- Роли скрывать (mainRoleCode): `CS1`, `CO1`, `СуШеф`, `СУШ`, `СЭКСПО12`, `СЭКСПО13`, `ПИЦЦ`, `ПЭКСПО13`.
- Депо в attendance-API адресуется КОДОМ (`10ASTKBN`), не UUID; `from`/`to` обязательны.
- Ответ `/tg/claim` — тот же формат, что `/tg/link`: `{linked, name, branch_code, org_id, role, courier_code}`; ничего ниже по течению не менять.
- Кухонный KDS (вход по PIN, `/auth/login`) не трогаем.
- Тесты: pytest из `backend/`, запуск `.venv/bin/python -m pytest tests/<file> -q`.
- Коммиты в текущую ветку `phase1-backend-ingest`, сообщения по-русски в стиле репо.

---

### Task 1: RMS — явки и список смены (`shift_employees_today`)

**Files:**
- Modify: `backend/app/rms.py` (в `_parse_employees_full` добавить `id`; новые функции внизу файла)
- Test: `backend/tests/test_rms_attendance.py` (новый)

**Interfaces:**
- Produces: `rms.shift_employees_today() -> list[dict]` — элементы
  `{employee_id, name, role, code, branch_code, branch_name, org_id}`,
  отсортированы по `name`; бросает исключение при недоступном RMS;
  `ValueError("BRANCH_CODE не настроен")` при пустом `settings.branch_code`.
  `rms.EXCLUDED_LOGIN_ROLES: set[str]`, `rms.reset_attendance_cache()` (для тестов).

- [ ] **Step 1: Написать падающие тесты**

`backend/tests/test_rms_attendance.py`:

```python
"""Тесты списка смены: парсер явок RMS, фильтр «открыта+сегодня», роли, дедуп."""
import pytest
import respx
from httpx import Response

from app import rms
from app.config import settings

BASE = "https://rms.test/resto"

ATT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<attendances>
  <attendance><employeeId>e-courier</employeeId>
    <dateFrom>2026-07-06T11:53:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-courier</employeeId>
    <dateFrom>2026-07-06T12:00:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-cashier</employeeId>
    <dateFrom>2026-07-06T11:13:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-stale</employeeId>
    <dateFrom>2024-06-26T15:46:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-closed</employeeId>
    <dateFrom>2026-07-06T09:00:00+05:00</dateFrom>
    <dateTo>2026-07-06T10:00:00+05:00</dateTo></attendance>
  <attendance><employeeId>e-manager</employeeId>
    <dateFrom>2026-07-06T11:53:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-deleted</employeeId>
    <dateFrom>2026-07-06T11:00:00+05:00</dateFrom></attendance>
</attendances>"""

EMP_XML = """<?xml version="1.0"?>
<employees>
  <employee><id>e-courier</id><name>Адильхан курьер</name><code>77</code>
    <mainRoleCode>КЭКСПО20</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-cashier</id><name>Нурсая</name><code>11</code>
    <mainRoleCode>CS1</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-stale</id><name>Старый Хвост</name><code>22</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-closed</id><name>Закрытая Явка</name><code>33</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-manager</id><name>Kaнат Сарыбай</name><code>44</code>
    <mainRoleCode>УПРЭКСПО</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-deleted</id><name>Уволенный</name><code>55</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>true</deleted><employee>true</employee></employee>
</employees>"""

DEP_XML = """<?xml version="1.0"?>
<corporateItemDtoes>
  <corporateItemDto><id>dep-1</id><name>Астана, ул. Г.Алиева 10 (EXPO)</name>
    <code>10ASTKBN</code><type>DEPARTMENT</type></corporateItemDto>
</corporateItemDtoes>"""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "rms_base", BASE)
    monkeypatch.setattr(settings, "rms_login", "u")
    monkeypatch.setattr(settings, "rms_password", "p")
    monkeypatch.setattr(settings, "branch_code", "10ASTKBN")
    rms.reset_attendance_cache()
    rms._EMP_CACHE = None
    rms._ORG_MAP = None
    yield
    rms.reset_attendance_cache()
    rms._EMP_CACHE = None
    rms._ORG_MAP = None


def _mock_rms(att_xml=ATT_XML):
    respx.get(f"{BASE}/api/auth").mock(return_value=Response(200, text="tok"))
    respx.get(f"{BASE}/api/logout").mock(return_value=Response(200, text=""))
    respx.get(f"{BASE}/api/corporation/departments").mock(
        return_value=Response(200, text=DEP_XML))
    respx.get(f"{BASE}/api/employees").mock(return_value=Response(200, text=EMP_XML))
    respx.get(f"{BASE}/api/employees/attendance/byDepartment/10ASTKBN").mock(
        return_value=Response(200, text=att_xml))


def test_parse_attendances_extracts_fields():
    atts = rms._parse_attendances(ATT_XML)
    assert len(atts) == 7
    assert atts[0] == {"employee_id": "e-courier",
                       "date_from": "2026-07-06T11:53:00+05:00", "date_to": ""}
    assert atts[4]["date_to"] == "2026-07-06T10:00:00+05:00"


@respx.mock
@pytest.mark.asyncio
async def test_shift_today_filters_and_joins(monkeypatch):
    """Открытые+сегодняшние; хвост-2024, закрытая, кассир и deleted — вне списка; дедуп."""
    monkeypatch.setattr(rms, "_attendance_today_str", lambda: "2026-07-06")
    _mock_rms()
    out = await rms.shift_employees_today()
    names = [e["name"] for e in out]
    assert names == ["Kaнат Сарыбай", "Адильхан курьер"]  # sorted, no dups
    courier = out[1]
    assert courier == {"employee_id": "e-courier", "name": "Адильхан курьер",
                       "role": "КЭКСПО20", "code": "77",
                       "branch_code": "10ASTKBN",
                       "branch_name": "Астана, ул. Г.Алиева 10 (EXPO)",
                       "org_id": "10ASTKBN"}  # без Cloud-маппинга org_id=код


@respx.mock
@pytest.mark.asyncio
async def test_shift_today_cached(monkeypatch):
    monkeypatch.setattr(rms, "_attendance_today_str", lambda: "2026-07-06")
    _mock_rms()
    await rms.shift_employees_today()
    route = respx.get(f"{BASE}/api/employees/attendance/byDepartment/10ASTKBN")
    calls_before = route.call_count
    await rms.shift_employees_today()
    assert route.call_count == calls_before  # второй раз из кэша


@pytest.mark.asyncio
async def test_shift_today_requires_branch_code(monkeypatch):
    monkeypatch.setattr(settings, "branch_code", "")
    with pytest.raises(ValueError):
        await rms.shift_employees_today()
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd backend && .venv/bin/python -m pytest tests/test_rms_attendance.py -q`
Expected: FAIL — `AttributeError: module 'app.rms' has no attribute '_parse_attendances'` (и т.п.)

- [ ] **Step 3: Реализация в `rms.py`**

В `_parse_employees_full()` добавить поле id (первым в dict):

```python
        out.append({
            "id": (emp.findtext("id") or "").strip(),
            "name": (emp.findtext("name") or "").strip(),
            ...  # остальное без изменений
        })
```

В конец `rms.py`:

```python
# ── Вход по открытой явке (курьерский TG-апп) ────────────────────────────────

# Роли, которым НЕ показываем вход по явке: кассиры, повара/сушисты, пиццеристы.
# Источник кодов: docs/PHOTO-2026-06-17-13-42-13.jpg (скрин заказчика).
EXCLUDED_LOGIN_ROLES = {"CS1", "CO1", "СуШеф", "СУШ", "СЭКСПО12", "СЭКСПО13",
                        "ПИЦЦ", "ПЭКСПО13"}


def _parse_attendances(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    out: list[dict] = []
    for a in root.iter("attendance"):
        out.append({
            "employee_id": (a.findtext("employeeId") or "").strip(),
            "date_from": (a.findtext("dateFrom") or "").strip(),
            "date_to": (a.findtext("dateTo") or "").strip(),
        })
    return out


def _attendance_today_str() -> str:
    """Сегодня (локальный день филиала, iiko_tz_offset_hours) как YYYY-MM-DD."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc) + timedelta(hours=settings.iiko_tz_offset_hours)
    return now.strftime("%Y-%m-%d")


_SHIFT_CACHE: list[dict] | None = None
_SHIFT_CACHE_TS: float = 0.0
_SHIFT_CACHE_TTL: float = 60.0


def reset_attendance_cache() -> None:
    global _SHIFT_CACHE, _SHIFT_CACHE_TS
    _SHIFT_CACHE, _SHIFT_CACHE_TS = None, 0.0


async def shift_employees_today() -> list[dict]:
    """Сотрудники с ОТКРЫТОЙ явкой, НАЧАТОЙ СЕГОДНЯ, на филиале settings.branch_code,
    без кухонных/кассирских ролей. Кэш ~60с (бережём слот лицензии RMS).

    Явки в нашем iiko бывают задвоены и не закрываются годами — поэтому фильтр
    строгий: dateTo пуст И dateFrom = сегодняшний локальный день."""
    global _SHIFT_CACHE, _SHIFT_CACHE_TS
    branch = (settings.branch_code or "").strip()
    if not branch:
        raise ValueError("BRANCH_CODE не настроен")
    now = time.monotonic()
    if _SHIFT_CACHE is not None and (now - _SHIFT_CACHE_TS) < _SHIFT_CACHE_TTL:
        return _SHIFT_CACHE

    today = _attendance_today_str()
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(
                f"{base}/api/employees/attendance/byDepartment/{branch}",
                params={"key": token, "from": today, "to": today})
            r.raise_for_status()
            attendances = _parse_attendances(r.text)

    open_today = {a["employee_id"] for a in attendances
                  if a["employee_id"] and not a["date_to"]
                  and a["date_from"][:10] == today}

    employees, departments = await _employees_and_departments_cached()
    branch_name = next((d["name"] for d in departments if d["code"] == branch), branch)
    org_map = await branch_to_org_id()
    org_id = org_map.get(branch, branch)

    out: list[dict] = []
    for emp in employees:
        if emp["id"] not in open_today:
            continue
        if emp["deleted"] or not emp["is_employee"]:
            continue
        if emp["role"] in EXCLUDED_LOGIN_ROLES:
            continue
        out.append({
            "employee_id": emp["id"],
            "name": emp["name"],
            "role": emp["role"],
            "code": emp["code"],
            "branch_code": branch,
            "branch_name": branch_name,
            "org_id": org_id,
        })
    out.sort(key=lambda e: e["name"])
    _SHIFT_CACHE, _SHIFT_CACHE_TS = out, now
    return out
```

Примечание: в тесте `org_id == "10ASTKBN"` потому, что Cloud-запрос в
`branch_to_org_id()` в тестовом окружении падает и функция возвращает `{}` —
фоллбэк на код филиала (это существующее поведение).
Если тест упадёт из-за незамоканного Cloud-запроса — добавить в `_mock_rms()`:
`respx.post("https://api-ru.iiko.services/api/1/access_token").mock(return_value=Response(500))`.

- [ ] **Step 4: Тесты зелёные**

Run: `cd backend && .venv/bin/python -m pytest tests/test_rms_attendance.py tests/test_rms.py tests/test_login_pin.py -q`
Expected: PASS (все; login_pin и rms — регресс на `_parse_employees_full`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rms.py backend/tests/test_rms_attendance.py
git commit -m "feat(rms): список смены по открытым явкам iiko — фильтр «открыта+сегодня», роли, дедуп"
```

---

### Task 2: Модель — `TelegramLink.employee_id/claimed_on` + миграция

**Files:**
- Modify: `backend/app/models.py:100-108` (класс `TelegramLink`)
- Modify: `backend/app/main.py:22-37` (`_ADDED_COLUMNS`)
- Test: `backend/tests/test_models.py` (дописать один тест)

**Interfaces:**
- Produces: `TelegramLink.employee_id: str | None` (UUID сотрудника RMS),
  `TelegramLink.claimed_on: str | None` (локальная дата входа `YYYY-MM-DD`).

- [ ] **Step 1: Падающий тест** — в конец `backend/tests/test_models.py`:

```python
@pytest.mark.asyncio
async def test_telegram_link_claim_fields(session):
    from app.models import TelegramLink
    link = TelegramLink(telegram_id="42", employee_code="77",
                        employee_id="e-courier", claimed_on="2026-07-06")
    session.add(link)
    await session.commit()
    got = (await session.execute(
        select(TelegramLink).where(TelegramLink.telegram_id == "42"))).scalar_one()
    assert got.employee_id == "e-courier"
    assert got.claimed_on == "2026-07-06"
```

(если в файле нет `select`/`pytest` — импортировать по образцу соседних тестов).

- [ ] **Step 2: Убедиться, что падает**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models.py -q`
Expected: FAIL — `TypeError: 'employee_id' is an invalid keyword argument`

- [ ] **Step 3: Реализация**

`models.py`, в класс `TelegramLink` после `role`:

```python
    # Вход по явке (без PIN): UUID сотрудника RMS и локальная дата входа.
    # claimed_on != сегодня -> привязка протухла, курьер выбирает имя заново.
    employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    claimed_on: Mapped[str | None] = mapped_column(String, nullable=True)
```

`main.py`, в `_ADDED_COLUMNS` добавить:

```python
    ("telegram_links", "employee_id", "VARCHAR"),
    ("telegram_links", "claimed_on", "VARCHAR"),
```

- [ ] **Step 4: Тесты зелёные**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models.py tests/test_migrate_columns.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/main.py backend/tests/test_models.py
git commit -m "feat(models): TelegramLink.employee_id/claimed_on — привязка входа по явке на день"
```

---

### Task 3: API — `/tg/shift-employees`, `/tg/claim`, протухание `/tg/auth`, удалить `/tg/link`

**Files:**
- Modify: `backend/app/api.py:1317-1387` (блок tg-эндпоинтов)
- Test: `backend/tests/test_tg_shift_login.py` (новый)

**Interfaces:**
- Consumes: `rms.shift_employees_today()` (Task 1), поля `TelegramLink` (Task 2),
  существующие `_telegram.parse_and_validate/extract_user`, `_local_today()`.
- Produces:
  - `GET /tg/shift-employees` → `[{employee_id, name, role, role_label, busy}]`;
    503 при недоступном RMS/непроставленном BRANCH_CODE.
  - `POST /tg/claim {initData, employee_id}` → `{linked: true, name, branch_code,
    org_id, role, courier_code}`; 401 плохой initData; 409 если employee_id
    нет в текущей смене.
  - `/tg/auth`: `{"linked": False}` если `claimed_on != сегодня` или явка уже
    закрыта (RMS недоступен → fail-open, пускаем).
  - `/tg/link` УДАЛЁН.

- [ ] **Step 1: Падающие тесты**

`backend/tests/test_tg_shift_login.py`:

```python
"""Вход курьера по открытой явке: /tg/shift-employees, /tg/claim, протухание /tg/auth."""
import hmac
import hashlib
import json
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.db import Base
import app.models  # noqa: F401
from app.models import TelegramLink
from app.config import settings
from app import rms


def _build_init_data(token: str, user: dict) -> str:
    fields = {"auth_date": "1700000000", "query_id": "AAEtest",
              "user": json.dumps(user, separators=(",", ":"))}
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


SHIFT = [
    {"employee_id": "e-courier", "name": "Адильхан", "role": "КЭКСПО20",
     "code": "77", "branch_code": "10ASTKBN", "branch_name": "Алиева",
     "org_id": "org-aliev"},
    {"employee_id": "e-manager", "name": "Kaнат", "role": "УПРЭКСПО",
     "code": "44", "branch_code": "10ASTKBN", "branch_name": "Алиева",
     "org_id": "org-aliev"},
]


@pytest_asyncio.fixture
async def client(monkeypatch):
    settings.telegram_bot_token = "testtoken"

    async def fake_shift():
        return [dict(e) for e in SHIFT]
    monkeypatch.setattr(rms, "shift_employees_today", fake_shift)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from app import api
    from app.db import get_session

    async def override():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app),
                           base_url="http://test") as c:
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_shift_employees_list_and_busy(client):
    c, maker = client
    from app.api import _local_today
    async with maker() as s:
        s.add(TelegramLink(telegram_id="999", employee_code="44",
                           employee_id="e-manager",
                           claimed_on=_local_today().isoformat()))
        await s.commit()
    r = await c.get("/tg/shift-employees")
    assert r.status_code == 200
    rows = r.json()
    assert [x["employee_id"] for x in rows] == ["e-courier", "e-manager"]
    assert rows[0]["busy"] is False
    assert rows[1]["busy"] is True
    assert rows[0]["role_label"] == "Курьер"
    assert "code" not in rows[0]  # PIN наружу не отдаём


@pytest.mark.asyncio
async def test_claim_links_and_returns_identity(client):
    c, maker = client
    init = _build_init_data("testtoken", {"id": 42, "first_name": "K"})
    r = await c.post("/tg/claim", json={"initData": init, "employee_id": "e-courier"})
    assert r.status_code == 200
    body = r.json()
    assert body["linked"] is True
    assert body["courier_code"] == "77"
    assert body["name"] == "Адильхан"
    assert body["org_id"] == "org-aliev"
    async with maker() as s:
        link = (await s.execute(select(TelegramLink)
                .where(TelegramLink.telegram_id == "42"))).scalar_one()
        assert link.employee_id == "e-courier"
        from app.api import _local_today
        assert link.claimed_on == _local_today().isoformat()

    # auth после claim — пускает
    r2 = await c.post("/tg/auth", json={"initData": init})
    assert r2.json()["linked"] is True


@pytest.mark.asyncio
async def test_claim_rejects_not_on_shift(client):
    c, _ = client
    init = _build_init_data("testtoken", {"id": 42, "first_name": "K"})
    r = await c.post("/tg/claim", json={"initData": init, "employee_id": "e-ghost"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_claim_rejects_bad_initdata(client):
    c, _ = client
    r = await c.post("/tg/claim", json={"initData": "hash=deadbeef",
                                        "employee_id": "e-courier"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_auth_stale_claim_is_unlinked(client):
    """Вчерашняя (или старая PIN-) привязка -> linked=False, выбор заново."""
    c, maker = client
    init = _build_init_data("testtoken", {"id": 42, "first_name": "K"})
    async with maker() as s:
        s.add(TelegramLink(telegram_id="42", employee_code="77", name="Адильхан",
                           employee_id="e-courier", claimed_on="2020-01-01"))
        s.add(TelegramLink(telegram_id="43", employee_code="88", name="ПИН-старик"))
        await s.commit()
    r = await c.post("/tg/auth", json={"initData": init})
    assert r.json() == {"linked": False}


@pytest.mark.asyncio
async def test_auth_kicks_when_attendance_closed(client, monkeypatch):
    """Явка закрылась среди дня -> linked=False."""
    c, maker = client
    init = _build_init_data("testtoken", {"id": 42, "first_name": "K"})
    from app.api import _local_today
    async with maker() as s:
        s.add(TelegramLink(telegram_id="42", employee_code="77", name="Адильхан",
                           employee_id="e-courier",
                           claimed_on=_local_today().isoformat()))
        await s.commit()

    async def empty_shift():
        return []
    monkeypatch.setattr(rms, "shift_employees_today", empty_shift)
    r = await c.post("/tg/auth", json={"initData": init})
    assert r.json() == {"linked": False}


@pytest.mark.asyncio
async def test_auth_fail_open_when_rms_down(client, monkeypatch):
    """RMS лёг -> курьера НЕ выкидываем (fail-open)."""
    c, maker = client
    init = _build_init_data("testtoken", {"id": 42, "first_name": "K"})
    from app.api import _local_today
    async with maker() as s:
        s.add(TelegramLink(telegram_id="42", employee_code="77", name="Адильхан",
                           employee_id="e-courier",
                           claimed_on=_local_today().isoformat()))
        await s.commit()

    async def boom():
        raise RuntimeError("rms down")
    monkeypatch.setattr(rms, "shift_employees_today", boom)
    r = await c.post("/tg/auth", json={"initData": init})
    assert r.json()["linked"] is True


@pytest.mark.asyncio
async def test_shift_employees_503_when_rms_down(client, monkeypatch):
    c, _ = client

    async def boom():
        raise RuntimeError("rms down")
    monkeypatch.setattr(rms, "shift_employees_today", boom)
    r = await c.get("/tg/shift-employees")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_tg_link_removed(client):
    c, _ = client
    r = await c.post("/tg/link", json={"initData": "x", "pin": "1"})
    assert r.status_code in (404, 405)
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tg_shift_login.py -q`
Expected: FAIL (404 на новых эндпоинтах, `/tg/link` ещё жив и т.п.)

- [ ] **Step 3: Реализация в `api.py`**

Заменить блок `/tg/auth` + `/tg/link` (строки ~1317–1387) на:

```python
class TgAuthIn(BaseModel):
    initData: str


# Человеческие подписи ролей в списке смены (код RMS -> подпись).
_ROLE_LABELS = {
    "КР": "Курьер", "КЭКСПО20": "Курьер",
    "MN0": "Управляющий", "УПРЭКСПО": "Управляющий", "Администратор": "Управляющий",
    "ADM": "Сисадмин", "ADM2": "Сисадмин",
    "DW1": "Тех персонал", "WR1": "Официант", "opeCC": "Оператор КЦ",
    "Бух": "Бухгалтер", "GBUH": "Бухгалтер", "МАРКЕТ": "Маркетолог",
}


def _tg_user_id(init_data: str) -> str:
    """Проверенный telegram user id из initData; HTTPException(401) если подпись бита."""
    pairs = _telegram.parse_and_validate(init_data)
    if pairs is None:
        raise HTTPException(status_code=401, detail="Invalid initData")
    telegram_id = str(_telegram.extract_user(pairs).get("id") or "")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return telegram_id


@app.get("/tg/shift-employees")
async def tg_shift_employees(session: AsyncSession = Depends(get_session)):
    """Кто сегодня на смене (открытая явка iiko, начатая сегодня) — для входа
    без PIN. busy = имя уже выбрано другим Telegram-аккаунтом сегодня."""
    try:
        shift = await _rms.shift_employees_today()
    except Exception as e:  # noqa: BLE001 — RMS недоступен/не настроен
        raise HTTPException(status_code=503, detail=f"iiko недоступен: {e}")
    today = _local_today().isoformat()
    claimed = (await session.execute(
        _select(_TelegramLink.employee_id)
        .where(_TelegramLink.claimed_on == today,
               _TelegramLink.employee_id.is_not(None)))).scalars().all()
    busy_ids = set(claimed)
    return [{
        "employee_id": e["employee_id"],
        "name": e["name"],
        "role": e["role"],
        "role_label": _ROLE_LABELS.get(e["role"], e["role"]),
        "busy": e["employee_id"] in busy_ids,
    } for e in shift]


class TgClaimIn(BaseModel):
    initData: str
    employee_id: str


@app.post("/tg/claim")
async def tg_claim(body: TgClaimIn, session: AsyncSession = Depends(get_session)):
    """Вход по явке: тап по своему имени вместо PIN. Требует, чтобы явка
    сотрудника всё ещё была открыта (список кэширован ~60с)."""
    telegram_id = _tg_user_id(body.initData)
    try:
        shift = await _rms.shift_employees_today()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"iiko недоступен: {e}")
    emp = next((x for x in shift if x["employee_id"] == body.employee_id), None)
    if emp is None:
        raise HTTPException(status_code=409,
                            detail="Явка закрыта или смена не открыта — обнови список")

    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if link is None:
        link = _TelegramLink(telegram_id=telegram_id)
        session.add(link)
    link.employee_code = emp["code"]
    link.name = emp["name"]
    link.branch_code = emp["branch_code"]
    link.org_id = emp["org_id"]
    link.role = emp["role"]
    link.employee_id = emp["employee_id"]
    link.claimed_on = _local_today().isoformat()
    await session.commit()

    return {
        "linked": True,
        "name": emp["name"],
        "branch_code": emp["branch_code"],
        "org_id": emp["org_id"],
        "role": emp["role"],
        "courier_code": emp["code"],
    }


@app.post("/tg/auth")
async def tg_auth(body: TgAuthIn, session: AsyncSession = Depends(get_session)):
    """Проверка initData и привязки. Привязка живёт, пока явка открыта:
    вчерашняя/чужая эпоха (старый PIN-вход) или закрытая явка -> linked=False."""
    telegram_id = _tg_user_id(body.initData)
    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if not link:
        return {"linked": False}
    if link.claimed_on != _local_today().isoformat():
        return {"linked": False}
    try:
        shift = await _rms.shift_employees_today()
        if all(x["employee_id"] != link.employee_id for x in shift):
            return {"linked": False}
    except Exception:  # noqa: BLE001 — RMS лёг: не выкидываем курьера среди смены
        pass
    return {
        "linked": True,
        "name": link.name,
        "branch_code": link.branch_code,
        "org_id": link.org_id,
        "role": link.role,
        "courier_code": link.employee_code,
    }
```

`/tg/link` и класс `TgLinkIn` удалить. `/tg/unlink`, `/tg/my-orders`,
`/tg/location` не трогать (но если `/tg/unlink`/`/tg/location` дублируют
разбор initData — можно перевести на `_tg_user_id()`, поведение то же).
Проверить, что `_rms` уже импортирован в api.py (используется в старом
`/tg/link`); `_local_today` определён выше (строка ~625) — эндпоинты tg
идут после него, порядок ок.

- [ ] **Step 4: Тесты зелёные**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tg_shift_login.py tests/test_telegram.py -q`
Expected: PASS, КРОМЕ тестов `/tg/link` в `test_telegram.py` — их переписать/удалить:
тесты старого PIN-линка (`test_tg_link_*`, если есть) заменить проверкой,
что эндпоинт удалён, либо убрать — контракт теперь покрыт `test_tg_shift_login.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api.py backend/tests/test_tg_shift_login.py backend/tests/test_telegram.py
git commit -m "feat(api): вход курьера по открытой явке — /tg/shift-employees + /tg/claim, PIN-линк удалён"
```

---

### Task 4: Фронт — фаза выбора имени вместо PIN-пада

**Files:**
- Modify: `frontend/src/lib/api.js:142-149` (tg-методы)
- Modify: `frontend/src/screens/TgCourier.jsx` (фаза `pin` → `pick`)
- Modify: `frontend/src/tg.css` (стили списка)

**Interfaces:**
- Consumes: `GET /tg/shift-employees`, `POST /tg/claim` (Task 3).

- [ ] **Step 1: api.js** — заменить `tgLink` на:

```js
  tgShiftEmployees: () => tgGet("/tg/shift-employees"),
  tgClaim: (initData, employee_id) => tgPost("/tg/claim", { initData, employee_id }),
```

- [ ] **Step 2: TgCourier.jsx**

Удалить: `PAD_KEYS`, состояния `pin/pinErr/pinBusy`, функции `press/submitPin`,
весь рендер `phase === "pin"`. Комментарий фаз: `"loading" | "outside" | "pick" | "orders"`.

Новые состояния и загрузка списка (вместо PIN-блока):

```jsx
  // pick: список открытых сегодня явок iiko — тап по своему имени = вход
  const [shift, setShift] = useState([]);
  const [shiftErr, setShiftErr] = useState("");
  const [shiftBusy, setShiftBusy] = useState(false);
  const [claimBusyId, setClaimBusyId] = useState(null);
  const [confirmEmp, setConfirmEmp] = useState(null); // занято — подтверждение

  const loadShift = useCallback(async () => {
    setShiftBusy(true);
    setShiftErr("");
    try {
      const list = await api.tgShiftEmployees();
      setShift(Array.isArray(list) ? list : []);
    } catch (e) {
      setShiftErr(String(e.message || e).includes("503")
        ? "iiko недоступен, попробуй позже"
        : e.message || String(e));
    } finally {
      setShiftBusy(false);
    }
  }, []);

  useEffect(() => {
    if (phase === "pick") loadShift();
  }, [phase, loadShift]);

  async function claim(emp) {
    if (claimBusyId) return;
    setConfirmEmp(null);
    setClaimBusyId(emp.employee_id);
    setShiftErr("");
    try {
      const resp = await api.tgClaim(initData, emp.employee_id);
      setIdentity(resp);
      setPhase("orders");
      loadOrders(resp.courier_code);
    } catch (e) {
      const msg = String(e.message || e);
      setShiftErr(msg.includes("409")
        ? "Явка уже закрыта — обнови список"
        : msg.includes("503") ? "iiko недоступен, попробуй позже" : msg);
      loadShift();
    } finally {
      setClaimBusyId(null);
    }
  }
```

В auth-эффекте `setPhase("pin")` → `setPhase("pick")`; в `logout()` убрать
`setPin("")`, `setPhase(initData ? "pin" : "outside")` → `"pick"`.

Рендер фазы `pick`:

```jsx
  if (phase === "pick") {
    return (
      <div className="tg">
        <div className="tg__center tg__center--top">
          <h1 className="tg__title">Кто ты?</h1>
          <p className="tg__muted">Открытые сегодня явки · тапни своё имя</p>
          <p className={`tg__err ${shiftErr ? "tg__err--show" : ""}`}>{shiftErr || " "}</p>

          {shiftBusy && shift.length === 0 ? (
            <p className="tg__muted">Загрузка…</p>
          ) : shift.length === 0 ? (
            <p className="tg__muted">
              Никого нет в списке. Нет твоего имени? Попроси кассира открыть
              смену в iiko, затем обнови.
            </p>
          ) : (
            <div className="tg__emplist">
              {shift.map((e) => (
                <button
                  key={e.employee_id}
                  type="button"
                  className="tg__emp"
                  disabled={claimBusyId !== null}
                  onClick={() => (e.busy ? setConfirmEmp(e) : claim(e))}
                >
                  <span className="tg__emp-name">
                    {claimBusyId === e.employee_id ? "…" : e.name}
                  </span>
                  <span className="tg__emp-meta">
                    {e.role_label}
                    {e.busy && <span className="tg__emp-busy"> · уже вошли</span>}
                  </span>
                </button>
              ))}
            </div>
          )}

          <button
            type="button"
            className="tg-btn tg-btn--ghost tg__emplist-refresh"
            onClick={loadShift}
            disabled={shiftBusy}
          >
            {shiftBusy ? "…" : "↻ Обновить список"}
          </button>
          <p className="tg__hint tg__hint--pick">
            Нет твоего имени — попроси кассира открыть смену в iiko.
          </p>
        </div>

        {confirmEmp && (
          <div className="tg-payment-overlay" role="dialog" aria-modal="true">
            <div className="tg-payment-sheet">
              <p className="tg-payment-sheet__title">Под именем «{confirmEmp.name}» уже вошли</p>
              <p className="tg-payment-sheet__sub">
                Если это не ты — выбери своё имя, иначе статистика доставок перепутается.
              </p>
              <div className="tg-payment-sheet__btns">
                <button type="button" className="tg-btn tg-btn--primary"
                        onClick={() => claim(confirmEmp)}>
                  Это я, войти
                </button>
              </div>
              <button type="button"
                      className="tg-btn tg-btn--ghost tg-payment-sheet__cancel"
                      onClick={() => setConfirmEmp(null)}>
                Отмена
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }
```

- [ ] **Step 3: tg.css** — добавить (значения подогнать под существующие переменные файла):

```css
/* Вход по явке: список имён */
.tg__center--top { justify-content: flex-start; padding-top: 24px; }
.tg__emplist { display: flex; flex-direction: column; gap: 10px; width: 100%;
  max-width: 420px; margin: 8px auto 0; }
.tg__emp { display: flex; flex-direction: column; align-items: flex-start;
  gap: 2px; padding: 14px 16px; border: none; border-radius: 14px;
  background: var(--tg-card, #1e2530); color: var(--tg-text, #fff);
  font-size: 17px; text-align: left; cursor: pointer; }
.tg__emp:active { opacity: 0.7; }
.tg__emp-name { font-weight: 600; }
.tg__emp-meta { font-size: 13px; color: var(--tg-hint, #8a9099); }
.tg__emp-busy { color: #e5a50a; }
.tg__emplist-refresh { margin-top: 14px; }
.tg__hint--pick { margin-top: 10px; }
```

- [ ] **Step 4: Проверка сборки и грепом**

Run: `cd frontend && npm run build`
Expected: сборка без ошибок.
Run: `grep -rn "tgLink\|submitPin\|PAD_KEYS" frontend/src/`
Expected: пусто.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/screens/TgCourier.jsx frontend/src/tg.css
git commit -m "feat(tg): вход курьера тапом по имени из открытых явок — PIN-пад удалён"
```

---

### Task 5: Финальная проверка

**Files:** нет новых.

- [ ] **Step 1: Полный прогон бэкенда**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: все зелёные.

- [ ] **Step 2: Живая проверка против прода (read-only)**

Run: `cd backend && BRANCH_CODE=10ASTKBN .venv/bin/python -c "
import asyncio
from app import rms
out = asyncio.run(rms.shift_employees_today())
for e in out: print(e['name'], e['role'], e['code'])
"`
Expected: список без кассиров/сушистов/пиццеристов, совпадает с
`scripts/probe_attendance.py` минус исключённые роли.

- [ ] **Step 3: Коммит остатков (если есть) и итог**

Сверить `git status` — рабочее дерево чистое; доложить заказчику результат
живой проверки.
