"""Тесты для _migrate_columns: SQLite-aware миграция колонок.

ADD COLUMN IF NOT EXISTS невалиден в SQLite — он бросает синтаксическую ошибку,
которую глушит окружающий except. Из-за этого на существующих SQLite-БД ни одна
из _ADDED_COLUMNS не добавляется. Эти тесты фиксируют корректное поведение:
на SQLite колонка должна реально добавиться (через PRAGMA-проверку + plain ALTER).
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

import app.main as main


@pytest_asyncio.fixture
async def conn():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        yield c
    await engine.dispose()


async def _column_names(conn, table):
    result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
    return [r[1] for r in result.fetchall()]


@pytest.mark.asyncio
async def test_migrate_adds_missing_column_on_sqlite(conn, monkeypatch):
    """RED до фикса: ALTER ... ADD COLUMN IF NOT EXISTS бросает на SQLite,
    исключение глотается, колонка не добавляется."""
    await conn.exec_driver_sql("CREATE TABLE t_demo (id INTEGER)")
    monkeypatch.setattr(main, "_ADDED_COLUMNS", [("t_demo", "added_col", "VARCHAR")])

    assert "added_col" not in await _column_names(conn, "t_demo")

    await main._migrate_columns(conn)

    cols = await _column_names(conn, "t_demo")
    assert "added_col" in cols, "ожидали что миграция добавит колонку на SQLite"


@pytest.mark.asyncio
async def test_migrate_is_idempotent(conn, monkeypatch):
    """Повторный вызов не падает и не дублирует колонку."""
    await conn.exec_driver_sql("CREATE TABLE t_demo (id INTEGER)")
    monkeypatch.setattr(main, "_ADDED_COLUMNS", [("t_demo", "added_col", "VARCHAR")])

    await main._migrate_columns(conn)
    await main._migrate_columns(conn)  # не должно бросить

    cols = await _column_names(conn, "t_demo")
    assert cols.count("added_col") == 1


@pytest.mark.asyncio
async def test_migrate_skips_missing_table(conn, monkeypatch):
    """Если таблицы нет — тихо пропускаем (create_all создаёт новые таблицы)."""
    monkeypatch.setattr(main, "_ADDED_COLUMNS", [("t_absent", "c", "VARCHAR")])
    # не должно бросить
    await main._migrate_columns(conn)
