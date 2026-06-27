"""Одноразовый бэкфилл OrderItem.kitchen_zone по имени блюда.
Запуск: cd backend && python -m scripts.backfill_kitchen_zone"""
import asyncio
from app.db import engine, Base
import app.models  # noqa: F401
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from app.scripts_backfill import backfill_kitchen_zone


async def main():
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        n = await backfill_kitchen_zone(s)
        print(f"kitchen_zone backfilled: {n}")


if __name__ == "__main__":
    asyncio.run(main())
