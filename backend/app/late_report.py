"""Ежедневный отчёт об опозданиях доставок в Telegram.

Приходит в первый час суток и подводит итог дня, который только что кончился.
У каждого опоздания указана причина — иначе управляющему непонятно, к кому
идти: к кухне, к курьеру или к тому, кто держал собранный заказ.

Самовывоз в отчёт не идёт (решение заказчика): клиент забирает сам, курьер к
опозданию отношения не имеет.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta

from app import telegram
from app.config import settings

log = logging.getLogger("saya.late_report")

# Час местного времени, когда уходит отчёт. Полночь: кухня закрылась, заказы
# закрыты. Пара доставок, ещё едущих в этот момент, в отчёт не попадёт.
REPORT_HOUR = 0

# Сколько худших опозданий показываем: сообщение читают с телефона.
TOP_N = 5

# Опоздание больше этого — почти наверняка не доставка, а заказ, который
# провисел весь день и был закрыт разом в конце смены. На проде 22.08 такой
# заказ дал +373 минуты и в одиночку сделал «среднее опоздание» за день
# шестичасовым. Не прячем, но помечаем: иначе отчёту перестанут верить.
SUSPICIOUS_DELAY_MINUTES = 180

_sent: set[str] = set()


def chat_ids() -> list[str]:
    """Кому слать. Пусто -> отчёт выключен."""
    raw = os.environ.get("LATE_REPORT_CHAT_IDS", "") or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def is_due(now_local: str, hour: int = REPORT_HOUR) -> bool:
    return datetime.fromisoformat(now_local).hour == hour


def report_date(now_local: str) -> str:
    """День, за который отчитываемся, — вчерашний относительно момента отправки."""
    return (datetime.fromisoformat(now_local).date() - timedelta(days=1)).isoformat()


def _day(date: str) -> str:
    d = datetime.fromisoformat(date).date()
    return d.strftime("%d.%m")


def format_report(date: str, department: str, data: dict) -> str:
    """Текст отчёта. Спокойный день тоже отчитывается: молчание неотличимо от
    поломки, и через неделю никто не вспомнит, работает ли рассылка вообще."""
    total = data.get("total") or 0
    late = data.get("late_count") or 0
    head = f"📦 Опоздания за {_day(date)} · {department}"

    if not late:
        return f"{head}\n\nДоставок {total}, опозданий нет."

    rate = round((data.get("late_rate") or 0) * 100)
    lines = [head, "", f"Доставок {total}, опоздали {late} — {rate}%"]

    avg, worst = data.get("avg_delay_minutes"), data.get("max_delay_minutes")
    if avg is not None:
        lines.append(f"Среднее опоздание {round(avg)} мин, худшее {worst}")

    reasons = data.get("reasons") or {}
    if reasons:
        named = " · ".join(f"{REASON_LABEL.get(k, k)} {v}"
                           for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
        lines += ["", f"Причины: {named}"]

    orders = data.get("orders") or []
    lines.append("")
    for o in orders[:TOP_N]:
        reason = (o.get("reason") or {}).get("label") or "причина неизвестна"
        courier = o.get("courier") or "—"
        delay = o.get("delay_minutes") or 0
        doubt = ("  ⚠️ похоже, заказ закрыли задним числом"
                 if delay >= SUSPICIOUS_DELAY_MINUTES else "")
        lines.append(f"№{o.get('number')}  {courier}  +{delay} мин · {reason}{doubt}")
    if len(orders) > TOP_N:
        lines.append(f"…и ещё {len(orders) - TOP_N}")
    return "\n".join(lines)


REASON_LABEL = {"kitchen": "кухня", "delivery": "дорога",
                "pickup": "ждали курьера", "unknown": "без наших отметок"}


async def run_once(org_id: str, now_local: str | None = None) -> dict:
    """Одна проверка: пора ли слать отчёт и, если да, отправить."""
    recipients = chat_ids()
    if not recipients:
        return {"sent": 0, "reason": "выключено: нет LATE_REPORT_CHAT_IDS"}

    now = now_local or (datetime.utcnow()
                        + timedelta(hours=settings.iiko_tz_offset_hours)).isoformat()
    if not is_due(now):
        return {"sent": 0, "reason": "не время"}

    date = report_date(now)
    if date in _sent:
        return {"sent": 0, "reason": "уже отправлено"}

    from app.api import _late_deliveries          # поздний импорт: api тянет пол-мира
    from app.db import SessionLocal

    async with SessionLocal() as session:
        data = await _late_deliveries(org_id, date, date, session)
    text = format_report(date, data.get("department") or "", data)

    # У отчёта свой бот: получатели — управляющие, а не курьеры. Не задан —
    # шлём основным, лучше прежним ботом, чем молчать.
    token = settings.report_bot_token or None

    sent = 0
    for chat_id in recipients:
        # send_message не бросает, а возвращает False: считать отправку по
        # факту вызова нельзя, иначе неотправленный отчёт пометит день и
        # пропадёт насовсем.
        if await telegram.send_message(chat_id, text, token):
            sent += 1
        else:
            log.warning("отчёт не ушёл в %s", chat_id)
    if not sent:
        # Telegram лежал — день не помечаем, попробуем на следующей проверке.
        return {"sent": 0, "reason": "не доставлено, повторим"}
    _sent.add(date)
    return {"sent": sent, "reason": "отправлено", "date": date}


def reset_sent() -> None:
    _sent.clear()


CHECK_INTERVAL_SECONDS = 600


async def run_late_reports() -> None:
    """Фоновый цикл: раз в десять минут смотрит, не пора ли отчитаться."""
    import asyncio
    org_id = (os.environ.get("LATE_REPORT_ORG_ID") or "").strip()
    if not org_id or not chat_ids():
        log.info("ежедневный отчёт об опозданиях выключен")
        return
    while True:
        try:
            result = await run_once(org_id)
            if result.get("sent"):
                log.info("отчёт об опозданиях: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                      # noqa: BLE001
            log.warning("отчёт об опозданиях не собрался: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def start_late_reports():
    import asyncio
    return asyncio.create_task(run_late_reports())
