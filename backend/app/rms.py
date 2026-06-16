from __future__ import annotations
import hashlib
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
