from __future__ import annotations
import socketio
from app.api import app as fastapi_app
from app.realtime import sio
from app.db import engine, Base
import app.models  # noqa: F401


@fastapi_app.on_event("startup")
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Socket.IO оборачивает FastAPI: один ASGI-апп на uvicorn.
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
