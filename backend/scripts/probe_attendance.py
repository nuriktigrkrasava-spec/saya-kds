"""Разведка v2: открытые сегодня явки по «Гейдар Алиева» + проверка фильтра дат.

Запуск:  python -m scripts.probe_attendance
"""
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from app import rms

ALIEV_HINT = "алиев"


def _local_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%d")


def _parse(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for a in root.iter("attendance"):
        out.append({
            "employee_id": (a.findtext("employeeId") or "").strip(),
            "date_from": (a.findtext("dateFrom") or "").strip(),
            "date_to": (a.findtext("dateTo") or "").strip(),
            "department_id": (a.findtext("departmentId") or "").strip(),
        })
    return out


async def main() -> None:
    today = _local_today()
    base = rms.settings.rms_base.rstrip("/")
    async with rms.session() as token:
        async with httpx.AsyncClient(timeout=120) as client:
            rd = await client.get(f"{base}/api/corporation/departments",
                                  params={"key": token})
            departments = rms._parse_departments(rd.text)
            dep = next(d for d in departments if ALIEV_HINT in d["name"].lower())

            url = f"{base}/api/employees/attendance/byDepartment/{dep['code']}"
            for label, params in [
                ("сегодня from/to", {"key": token, "from": today, "to": today}),
                ("вчера-вчера", {"key": token, "from": "2026-07-05", "to": "2026-07-05"}),
                ("без дат", {"key": token}),
            ]:
                r = await client.get(url, params=params)
                try:
                    atts = _parse(r.text)
                except ET.ParseError:
                    print(f"{label}: {r.status_code}, НЕ XML: {r.text[:200]!r}")
                    continue
                years = sorted({a["date_from"][:4] for a in atts})
                closed = sum(1 for a in atts if a["date_to"])
                print(f"{label}: {r.status_code}, записей={len(atts)}, "
                      f"закрытых={closed}, годы={years}")

            r = await client.get(url, params={"key": token, "from": today, "to": today})
            atts = _parse(r.text)
            opened_today = [a for a in atts
                            if a["date_from"][:10] == today and not a["date_to"]]
            open_any = [a for a in atts if not a["date_to"]]
            print(f"\nявок в выборке: {len(atts)}; открытых всего: {len(open_any)}; "
                  f"открытых С НАЧАЛОМ сегодня: {len(opened_today)}")

            re_ = await client.get(f"{base}/api/employees", params={"key": token})
            emps = {}
            root = ET.fromstring(re_.text)
            for e in root.iter("employee"):
                emps[(e.findtext("id") or "").strip()] = {
                    "name": (e.findtext("name") or "").strip(),
                    "role": (e.findtext("mainRoleCode") or "").strip(),
                }
            print("\nОткрытые сегодня явки (имя / роль / начало):")
            for a in sorted(opened_today, key=lambda x: x["date_from"]):
                info = emps.get(a["employee_id"], {"name": "???", "role": "?"})
                print(f"  {info['name']:<35} {info['role']:<10} {a['date_from']}")


if __name__ == "__main__":
    asyncio.run(main())
