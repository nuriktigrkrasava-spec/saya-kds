"""Уведомление управляющим о наплыве заказов: вечером на завтра, утром на сегодня.

Смысл — успеть усилить смену. Поэтому вечернее сообщение важнее: ночь есть,
чтобы кого-то вызвать или поменять график. Утреннее — напоминание.

В сообщение идут только всплески: управляющему нужно действие, а не сетка.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import select

from app import telegram
from app.config import settings
from app.db import SessionLocal
from app.models import TelegramLink

log = logging.getLogger("saya.staffing")

MANAGER_ROLES = {"MN0", "Администратор", "УПРЭКСПО", "ADM", "ADM2"}

WEEKDAYS = ["понедельник", "вторник", "среду", "четверг", "пятницу",
            "субботу", "воскресенье"]

# Отправленные сообщения: ключ f"{дата}:{kind}", чтобы не слать дважды за день.
_sent: set[str] = set()


def _hour_from_env(name: str, default: int | None) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        hour = int(raw)
    except ValueError:
        return default
    return hour if 0 <= hour <= 23 else default


def due_kind(now_local: str, evening: int | None, morning: int | None) -> str | None:
    """Какое уведомление пора слать в этот момент, если вообще пора.

    Час не задан -> этот вид уведомлений выключен.
    """
    hour = datetime.fromisoformat(now_local).hour
    if evening is not None and hour == evening:
        return "evening"
    if morning is not None and hour == morning:
        return "morning"
    return None


def target_date(now_local: str, kind: str) -> str:
    """Вечером говорим про завтрашнюю смену, утром — про сегодняшнюю."""
    day = datetime.fromisoformat(now_local).date()
    if kind == "evening":
        day = day + timedelta(days=1)
    return day.isoformat()


def format_message(date: str, plan: list[dict], zones: dict,
                   kind: str = "evening") -> str | None:
    """Текст для управляющего или None, если тревожить не о чем."""
    spikes = [p for p in plan if p["is_peak"] and p["shortage"] > 0]
    if not spikes:
        return None

    day = datetime.fromisoformat(date).date()
    when = "Завтра" if kind == "evening" else "Сегодня"
    head = (f"📈 {when} в {WEEKDAYS[day.weekday()]} {day.strftime('%d.%m')} "
            f"ожидается наплыв заказов")

    lines = [head, ""]
    for p in sorted(spikes, key=lambda x: (x["hour"], x["zone"])):
        lines.append(f"{p['hour']:02d}:00 · {p['zone_label']} — {plural_items(p['demand'])}, "
                     f"было {p['people']}, нужно {p['recommended_people']}")

    norms = ", ".join(
        f"{z['label']} {z['norm_per_person']}" for z in zones.values()
        if z.get("norm_per_person"))
    if norms:
        lines += ["", f"Норма выработки (позиций на человека в час): {norms}."]
    return "\n".join(lines)


def plural_items(n: int) -> str:
    """«1 позиция», «3 позиции», «20 позиций» — сообщение читает человек."""
    tail = abs(n) % 100
    if 11 <= tail <= 14:
        word = "позиций"
    else:
        last = tail % 10
        word = "позиция" if last == 1 else "позиции" if 2 <= last <= 4 else "позиций"
    return f"{n} {word}"


async def _managers(session, org_id: str) -> list[TelegramLink]:
    rows = (await session.execute(
        select(TelegramLink).where(TelegramLink.org_id == org_id,
                                   TelegramLink.role.in_(MANAGER_ROLES)))).scalars().all()
    return list(rows)


async def run_once(org_id: str, now_local: str | None = None) -> dict:
    """Одна проверка: пора ли слать и, если да, отправить. Отдаёт что сделал."""
    evening = _hour_from_env("STAFFING_ALERT_EVENING_HOUR", None)
    morning = _hour_from_env("STAFFING_ALERT_MORNING_HOUR", None)
    if evening is None and morning is None:
        return {"sent": 0, "reason": "выключено: часы не заданы"}

    now = now_local or (datetime.utcnow()
                        + timedelta(hours=settings.iiko_tz_offset_hours)).isoformat()
    kind = due_kind(now, evening, morning)
    if kind is None:
        return {"sent": 0, "reason": "не время"}

    date = target_date(now, kind)
    key = f"{date}:{kind}"
    if key in _sent:
        return {"sent": 0, "reason": "уже отправлено"}

    from app.api import _staffing_grid          # поздний импорт: api тянет пол-мира
    from app import staffing

    grid = await _staffing_grid(org_id, weeks=4)
    weekday = datetime.fromisoformat(date).weekday()
    text = format_message(date, staffing.shift_plan(grid, weekday), grid["zones"], kind)
    _sent.add(key)
    if text is None:
        return {"sent": 0, "reason": "спокойный день"}

    sent = 0
    async with SessionLocal() as session:
        for link in await _managers(session, org_id):
            if not link.telegram_id:
                continue
            try:
                await telegram.send_message(link.telegram_id, text)
                sent += 1
            except Exception as exc:                      # noqa: BLE001
                log.warning("не отправилось управляющему %s: %s", link.telegram_id, exc)
    return {"sent": sent, "reason": "отправлено", "kind": kind, "date": date}


def reset_sent() -> None:
    _sent.clear()


# Раз в 10 минут: попасть в нужный час достаточно, а OLAP с RMS дёргать чаще
# незачем — RMS занимает слот лицензии.
CHECK_INTERVAL_SECONDS = 600


async def run_staffing_alerts() -> None:
    """Фоновый цикл: проверяет, не пора ли предупредить о наплыве."""
    import asyncio
    org_id = (os.environ.get("STAFFING_ALERT_ORG_ID") or "").strip()
    if not org_id:
        log.info("уведомления о сменах выключены: нет STAFFING_ALERT_ORG_ID")
        return
    while True:
        try:
            result = await run_once(org_id)
            if result.get("sent"):
                log.info("уведомление о смене: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                          # noqa: BLE001
            log.warning("проверка смен не удалась: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def start_staffing_alerts():
    import asyncio
    return asyncio.create_task(run_staffing_alerts())
