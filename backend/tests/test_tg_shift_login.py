from app.security import courier_ref
import time
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
    fields = {"auth_date": str(int(time.time())), "query_id": "AAEtest",
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
    {"employee_id": "e-kazh", "name": "Ерлан", "role": "КР",
     "code": "99", "branch_code": "KAZH", "branch_name": "Кажымукана",
     "org_id": "org-kazh"},
]


@pytest_asyncio.fixture
async def client(monkeypatch):
    async def verified_pin(pin):
        return next((e for e in SHIFT if e["code"] == pin), None)
    monkeypatch.setattr(rms, "find_employee_by_pin", verified_pin)
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
    r = await c.post("/tg/shift-employees", json={"initData":_build_init_data("testtoken", {"id":42})})
    assert r.status_code == 200
    rows = r.json()
    assert [x["employee_id"] for x in rows] == ["e-courier", "e-manager", "e-kazh"]
    assert rows[0]["busy"] is False
    assert rows[1]["busy"] is True
    assert rows[0]["role_label"] == "Курьер"
    assert "code" not in rows[0]  # PIN наружу не отдаём


@pytest.mark.asyncio
async def test_claim_links_and_returns_identity(client):
    c, maker = client
    init = _build_init_data("testtoken", {"id": 42, "first_name": "K"})
    r = await c.post("/tg/claim", json={"initData": init, "employee_id": "e-courier", "pin":"77"})
    assert r.status_code == 200
    body = r.json()
    assert body["linked"] is True
    assert body["courier_code"] == courier_ref("77")
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
    r = await c.post("/tg/claim", json={"initData": init, "employee_id": "e-ghost", "pin":"wrong"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_claim_rejects_bad_initdata(client):
    c, _ = client
    r = await c.post("/tg/claim", json={"initData": "hash=deadbeef",
                                        "employee_id": "e-courier", "pin":"77"})
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
                           employee_id="e-courier", role="КР", org_id="org-aliev", branch_code="10ASTKBN",
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
                           employee_id="e-courier", role="КР", org_id="org-aliev", branch_code="10ASTKBN",
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
    r = await c.post("/tg/shift-employees", json={"initData":_build_init_data("testtoken", {"id":42})})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_tg_link_removed(client):
    c, _ = client
    r = await c.post("/tg/link", json={"initData": "x", "pin": "1"})
    assert r.status_code in (404, 405)


@pytest.mark.asyncio
async def test_shift_rows_carry_branch(client):
    """Филиал виден в списке: на двух филиалах имена иначе смешаются в кучу."""
    c, _ = client
    rows = (await c.post("/tg/shift-employees", json={"initData":_build_init_data("testtoken", {"id":42})})).json()
    by_id = {r["employee_id"]: r for r in rows}
    assert by_id["e-kazh"]["branch_name"] == "Кажымукана"
    assert by_id["e-kazh"]["branch_code"] == "KAZH"
    assert by_id["e-courier"]["branch_name"] == "Алиева"
