from __future__ import annotations
import hashlib
import time
from contextlib import asynccontextmanager
import xml.etree.ElementTree as ET
import httpx
from app.config import settings


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


@asynccontextmanager
async def session():
    """Логинится в RMS и ГАРАНТИРОВАННО делает logout (освобождает слот лицензии)."""
    base = settings.rms_base.rstrip("/")
    async with httpx.AsyncClient(timeout=60, verify=True) as client:
        r = await client.get(f"{base}/api/auth",
                             params={"login": settings.rms_login,
                                     "pass": _sha1(settings.rms_password)})
        r.raise_for_status()
        token = r.text.strip()
        try:
            yield token
        finally:
            await client.get(f"{base}/api/logout", params={"key": token})


async def fetch_employees() -> list[dict]:
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{base}/api/employees", params={"key": token})
            r.raise_for_status()
            xml = r.text
    root = ET.fromstring(xml)
    out: list[dict] = []
    for emp in root.iter("employee"):
        out.append({
            "id": (emp.findtext("id") or "").strip(),
            "name": (emp.findtext("name") or "").strip(),
            "code": (emp.findtext("code") or "").strip(),
        })
    return out


def _as_bool(text: str | None) -> bool:
    return (text or "").strip().lower() == "true"


def _parse_employees_full(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    out: list[dict] = []
    for emp in root.iter("employee"):
        dep_raw = (emp.findtext("departmentCodes") or "").strip()
        dep_codes = [c.strip() for c in dep_raw.split(",") if c.strip()]
        out.append({
            "name": (emp.findtext("name") or "").strip(),
            "code": (emp.findtext("code") or "").strip(),
            "role": (emp.findtext("mainRoleCode") or "").strip(),
            "department_codes": dep_codes,
            "deleted": _as_bool(emp.findtext("deleted")),
            "is_employee": _as_bool(emp.findtext("employee")),
        })
    return out


def _parse_departments(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    out: list[dict] = []
    for dto in root.iter("corporateItemDto"):
        if (dto.findtext("type") or "").strip() != "DEPARTMENT":
            continue
        out.append({
            "id": (dto.findtext("id") or "").strip(),
            "name": (dto.findtext("name") or "").strip(),
            "code": (dto.findtext("code") or "").strip(),
        })
    return out


async def fetch_departments() -> list[dict]:
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{base}/api/corporation/departments",
                                 params={"key": token})
            r.raise_for_status()
            xml = r.text
    return _parse_departments(xml)


async def fetch_employees_full() -> list[dict]:
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{base}/api/employees", params={"key": token})
            r.raise_for_status()
            xml = r.text
    return _parse_employees_full(xml)


# Лёгкий кэш сотрудников+филиалов, чтобы не жечь слот лицензии RMS на каждый вход.
_EMP_CACHE: dict | None = None
_EMP_CACHE_TS: float = 0.0
_EMP_CACHE_TTL: float = 60.0


async def _employees_and_departments_cached() -> tuple[list[dict], list[dict]]:
    """Возвращает (employees_full, departments) с кэшем ~60с (один логин/логаут на оба запроса)."""
    global _EMP_CACHE, _EMP_CACHE_TS
    now = time.monotonic()
    if _EMP_CACHE is not None and (now - _EMP_CACHE_TS) < _EMP_CACHE_TTL:
        return _EMP_CACHE["employees"], _EMP_CACHE["departments"]

    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            rd = await client.get(f"{base}/api/corporation/departments",
                                  params={"key": token})
            rd.raise_for_status()
            departments = _parse_departments(rd.text)
            re_ = await client.get(f"{base}/api/employees", params={"key": token})
            re_.raise_for_status()
            employees = _parse_employees_full(re_.text)

    _EMP_CACHE = {"employees": employees, "departments": departments}
    _EMP_CACHE_TS = now
    return employees, departments


async def find_employee_by_pin(pin: str) -> dict | None:
    """Найти активного сотрудника по PIN (code) и определить его филиал. Кэш ~60с, чтобы не жечь лицензию RMS."""
    if not pin:
        return None
    employees, departments = await _employees_and_departments_cached()
    code_to_name = {d["code"]: d["name"] for d in departments if d["code"]}

    for emp in employees:
        if not emp["is_employee"] or emp["deleted"]:
            continue
        if emp["code"] and emp["code"] == pin:
            branch_code = emp["department_codes"][0] if emp["department_codes"] else ""
            branch_name = code_to_name.get(branch_code, "Без филиала")
            return {
                "name": emp["name"],
                "role": emp["role"],
                "branch_code": branch_code,
                "branch_name": branch_name,
                "org_id": branch_code,
            }
    return None


async def branches_with_employees() -> list[dict]:
    """Группирует сотрудников RMS по филиалам (DEPARTMENT). Один логин/логаут на оба запроса."""
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            rd = await client.get(f"{base}/api/corporation/departments",
                                  params={"key": token})
            rd.raise_for_status()
            departments = _parse_departments(rd.text)
            re_ = await client.get(f"{base}/api/employees", params={"key": token})
            re_.raise_for_status()
            employees = _parse_employees_full(re_.text)

    known_codes = {d["code"] for d in departments if d["code"]}

    branches = []
    for dep in sorted(departments, key=lambda d: d["name"]):
        members = []
        for emp in employees:
            if not emp["is_employee"] or emp["deleted"]:
                continue
            if dep["code"] and dep["code"] in emp["department_codes"]:
                members.append({
                    "name": emp["name"],
                    "role": emp["role"],
                    "code": emp["code"],
                })
        members.sort(key=lambda e: e["name"])
        branches.append({
            "code": dep["code"],
            "name": dep["name"],
            "id": dep["id"],
            "employees": members,
        })

    orphans = []
    for emp in employees:
        if not emp["is_employee"] or emp["deleted"]:
            continue
        if not any(c in known_codes for c in emp["department_codes"]):
            orphans.append({
                "name": emp["name"],
                "role": emp["role"],
                "code": emp["code"],
            })
    orphans.sort(key=lambda e: e["name"])
    if orphans:
        branches.append({
            "code": "",
            "name": "Без филиала",
            "id": "",
            "employees": orphans,
        })

    return branches
