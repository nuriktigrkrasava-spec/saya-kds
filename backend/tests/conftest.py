import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa: F401  (регистрирует таблицы)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s
    await engine.dispose()


import pytest
from fastapi import Request
from app.config import settings

@pytest.fixture(autouse=True)
def isolated_test_settings(request):
    snapshot = settings.model_dump()
    settings.jwt_secret = "test-jwt-secret-that-is-at-least-32-characters"
    from app.security import _login_buckets, authorize_request
    _login_buckets.clear()
    from app.api import app
    if request.node.path.name != "test_security_review.py" and "requires_real_auth" not in request.keywords:
        async def business_actor(req: Request):
            req.state.actor = {"telegram_id":("999" if request.node.name == "test_location_rejects_unlinked" else "555") if request.node.path.name == "test_gps.py" else "42", "name":"test", "branch_code":"AAA", "org_id":"org-1", "orgs":["org-1"], "role":"ADM"}
        app.dependency_overrides[authorize_request] = business_actor
    yield
    app.dependency_overrides.pop(authorize_request, None)
    for key,value in snapshot.items(): setattr(settings,key,value)
