from __future__ import annotations
import hashlib
import re
import time
from contextlib import asynccontextmanager
import xml.etree.ElementTree as ET
import httpx
from app.config import settings


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def _norm_name(s: str) -> str:
    """Нормализация имени филиала для сопоставления RMS↔Cloud (убрать пунктуацию/регистр)."""
    return re.sub(r"[^\w]+", " ", (s or "").lower()).strip()


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
            "id": (emp.findtext("id") or "").strip(),
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


_ORG_MAP: dict | None = None
_ORG_MAP_TS: float = 0.0
_ORG_MAP_TTL: float = 3600.0


async def branch_to_org_id() -> dict:
    """Карта «код филиала RMS → Cloud organizationId» по совпадению имён. Кэш 1ч.

    Нужна, чтобы заказы из вебхука (приходят с Cloud-ID) попадали на доску того же
    филиала, под которым вошёл сотрудник.
    """
    global _ORG_MAP, _ORG_MAP_TS
    now = time.monotonic()
    if _ORG_MAP is not None and (now - _ORG_MAP_TS) < _ORG_MAP_TTL:
        return _ORG_MAP
    from app import iiko_cloud
    try:
        _, departments = await _employees_and_departments_cached()
        orgs = await iiko_cloud.fetch_organizations()
    except Exception:
        return _ORG_MAP or {}
    by_name = {_norm_name(o.get("name")): o.get("id") for o in orgs}
    mapping = {d["code"]: by_name[_norm_name(d.get("name"))]
               for d in departments
               if d.get("code") and _norm_name(d.get("name")) in by_name}
    _ORG_MAP, _ORG_MAP_TS = mapping, now
    return mapping


async def branch_mapping_report() -> list[dict]:
    """Сверка настроенных филиалов (BRANCH_CODES) с организациями iiko Cloud.

    Связка RMS↔Cloud идёт по совпадению НАЗВАНИЙ (branch_to_org_id). Разошлись
    имена — заказы филиала не находят доску, и в логах при этом тихо. Отчёт
    называет виновника: чьё имя не совпало и с чем именно можно было совпасть.
    """
    from app import iiko_cloud
    _, departments = await _employees_and_departments_cached()
    name_by_code = {d["code"]: (d.get("name") or "")
                    for d in departments if d.get("code")}
    orgs = await iiko_cloud.fetch_organizations()
    org_by_norm = {_norm_name(o.get("name")): o for o in orgs}
    org_names = ", ".join(sorted(f"«{o.get('name')}»" for o in orgs if o.get("name")))

    out: list[dict] = []
    for code in configured_branch_codes():
        rms_name = name_by_code.get(code)
        if rms_name is None:
            out.append({"branch_code": code, "rms_name": "", "org_id": "",
                        "org_name": "", "matched": False,
                        "problem": f"депо с кодом {code} нет в RMS"})
            continue
        org = org_by_norm.get(_norm_name(rms_name))
        out.append({
            "branch_code": code,
            "rms_name": rms_name,
            "org_id": (org or {}).get("id") or "",
            "org_name": (org or {}).get("name") or "",
            "matched": bool(org),
            "problem": "" if org else
                       (f"в iiko Cloud нет организации с названием «{rms_name}»; "
                        f"есть: {org_names or '—'}"),
        })
    return out


async def find_employee_by_pin(pin: str) -> dict | None:
    """Найти активного сотрудника по PIN (code) и определить его филиал. Кэш ~60с, чтобы не жечь лицензию RMS."""
    if not pin:
        return None
    employees, departments = await _employees_and_departments_cached()
    code_to_name = {d["code"]: d["name"] for d in departments if d["code"]}
    org_map = await branch_to_org_id()

    for emp in employees:
        if not emp["is_employee"] or emp["deleted"]:
            continue
        if emp["code"] and emp["code"] == pin:
            branch_code = emp["department_codes"][0] if emp["department_codes"] else ""
            branch_name = code_to_name.get(branch_code, "Без филиала")
            # org_id = Cloud-ID филиала (чтобы видеть реальные заказы из вебхука);
            # если нет совпадения — откатываемся на код филиала.
            org_id = org_map.get(branch_code, branch_code)
            return {
                "employee_id": emp["id"],
                "name": emp["name"],
                "role": emp["role"],
                "branch_code": branch_code,
                "branch_name": branch_name,
                "org_id": org_id,
            }
    return None


_PROD_CACHE: list[dict] | None = None
_PROD_CACHE_TS: float = 0.0
_PROD_CACHE_TTL: float = 600.0  # 10 мин — список большой, не дёргаем часто


def _build_group_index(groups: list[dict]) -> tuple[dict, dict]:
    """name-by-id и parent-by-id для групп меню."""
    gname = {g.get("id"): (g.get("name") or "").strip() for g in groups}
    gparent = {g.get("id"): g.get("parent") for g in groups}
    return gname, gparent


def _group_path_root(gid, gname: dict, gparent: dict) -> tuple[str, str]:
    """Полный путь группы «A / B / C» и имя корневой группы."""
    seen: set = set()
    parts: list[str] = []
    cur = gid
    while cur and cur not in seen:
        seen.add(cur)
        nm = gname.get(cur)
        if nm:
            parts.append(nm)
        cur = gparent.get(cur)
    parts.reverse()
    return " / ".join(parts), (parts[0] if parts else "")


async def fetch_products() -> list[dict]:
    """Полное меню из RMS: [{id, name, group_id, group_path, group_root}].
    Только DISH/GOODS, не удалённые. Кэш 10 мин."""
    global _PROD_CACHE, _PROD_CACHE_TS
    now = time.monotonic()
    if _PROD_CACHE is not None and (now - _PROD_CACHE_TS) < _PROD_CACHE_TTL:
        return _PROD_CACHE
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.get(f"{base}/api/v2/entities/products/list",
                                  params={"key": token, "includeDeleted": "false"})
            r.raise_for_status()
            data = r.json()
            rg = await client.get(f"{base}/api/v2/entities/products/group/list",
                                  params={"key": token, "includeDeleted": "false"})
            rg.raise_for_status()
            groups = rg.json()
    gname, gparent = _build_group_index(groups)
    out: list[dict] = []
    for p in data:
        if p.get("deleted"):
            continue
        if p.get("type") not in ("DISH", "GOODS"):
            continue
        pid = p.get("id")
        name = (p.get("name") or "").strip()
        if not pid or not name:
            continue
        gid = p.get("parent") or p.get("parentGroup")
        gpath, groot = _group_path_root(gid, gname, gparent)
        out.append({"id": pid, "name": name, "group_id": gid,
                    "group_path": gpath, "group_root": groot})
    _PROD_CACHE, _PROD_CACHE_TS = out, now
    return out


def products_cached() -> list[dict]:
    """Меню RMS из кэша БЕЗ сетевого запроса (пусто, если ещё не прогрет).
    Нужно резолверу зон на каждый ingest — без удара по RMS/лицензии."""
    return _PROD_CACHE or []


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


# ── Вход по открытой явке (курьерский TG-апп) ────────────────────────────────

# Роли, которым НЕ показываем вход по явке: кухня, касса и всё, что к доставке
# отношения не имеет (техперсонал, официанты, КЦ, бухгалтерия, маркетинг).
# В аппе остаются курьеры и управляющие — решение заказчика 09.08.2026.
# Источник кодов: docs/PHOTO-2026-06-17-13-42-13.jpg (скрин заказчика).
EXCLUDED_LOGIN_ROLES = {"CS1", "CO1", "СуШеф", "СУШ", "СЭКСПО12", "СЭКСПО13",
                        "ПИЦЦ", "ПЭКСПО13",
                        "DW1", "WR1", "opeCC", "Бух", "GBUH", "МАРКЕТ"}

# Явка, открытая раньше этого срока, — забытый хвост, а не смена: в нашем iiko
# такие висят открытыми с 2024 года (5 штук на филиале на 09.08.2026).
STALE_ATTENDANCE_DAYS = 2


def _parse_attendances(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    out: list[dict] = []
    for a in root.iter("attendance"):
        out.append({
            "employee_id": (a.findtext("employeeId") or "").strip(),
            "date_from": (a.findtext("dateFrom") or "").strip(),
            "date_to": (a.findtext("dateTo") or "").strip(),
        })
    return out


def _attendance_today_str() -> str:
    """Сегодня (локальный день филиала, iiko_tz_offset_hours) как YYYY-MM-DD."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc) + timedelta(hours=settings.iiko_tz_offset_hours)
    return now.strftime("%Y-%m-%d")


def _is_current_shift(attendance: dict, today: str) -> bool:
    """Открытая смена, начатая не раньше STALE_ATTENDANCE_DAYS назад."""
    if attendance["date_to"] or not attendance["employee_id"]:
        return False
    started = (attendance["date_from"] or "")[:10]
    if not started:
        return False
    from datetime import date, timedelta
    try:
        oldest = date.fromisoformat(today) - timedelta(days=STALE_ATTENDANCE_DAYS)
        return date.fromisoformat(started) >= oldest
    except ValueError:  # неожиданный формат даты — не выкидываем человека
        return True


# Кэш смен — ПО ФИЛИАЛАМ: {branch_code: (ts, employees)}. Общий кэш на все
# филиалы отдавал бы курьеров чужого депо, как только их стало больше одного.
_SHIFT_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SHIFT_CACHE_TTL: float = 60.0


def reset_attendance_cache() -> None:
    _SHIFT_CACHE.clear()


def configured_branch_codes() -> list[str]:
    """Филиалы на КДС: BRANCH_CODES (через запятую), иначе одиночный BRANCH_CODE."""
    raw = (settings.branch_codes or "").strip()
    if raw:
        return [c.strip() for c in raw.split(",") if c.strip()]
    single = (settings.branch_code or "").strip()
    return [single] if single else []


def cached_branch_to_org_id() -> dict:
    """Карта «код филиала → Cloud org_id» ТОЛЬКО из кэша, без похода в iiko.

    Нужна там, где лишний сетевой вызов недопустим (фильтры дашборда). Кэш
    греется на входе по PIN и на роутинге заказов.
    """
    return _ORG_MAP or {}


async def attendances_for_period(date_from: str, date_to: str,
                                 branch_code: str = "") -> list[dict]:
    """Явки филиала за период (YYYY-MM-DD) — для планирования смен.

    В отличие от shift_employees_today() отдаёт СЫРЫЕ явки, включая закрытые:
    нужно знать, кто сколько часов отстоял, а не кто на смене прямо сейчас.
    """
    branch = (branch_code or "").strip()
    if not branch:
        # Фоллбэк на глобальную настройку — только пока филиал один: иначе
        # вернули бы явки чужого депо и молча испортили планирование смен.
        configured = configured_branch_codes()
        branch = configured[0] if len(configured) == 1 else ""
    if not branch:
        raise ValueError("код филиала не задан (BRANCH_CODES)")
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.get(
                f"{base}/api/employees/attendance/byDepartment/{branch}",
                params={"key": token, "from": date_from, "to": date_to})
            r.raise_for_status()
            return _parse_attendances(r.text)


async def role_by_employee_id() -> dict[str, str]:
    """Карта «id сотрудника → код должности» из кэша сотрудников."""
    employees, _ = await _employees_and_departments_cached()
    return {e["id"]: e.get("role") or "" for e in employees if e.get("id")}


async def shift_employees_today(branch_code: str = "") -> list[dict]:
    """Сотрудники с ОТКРЫТОЙ сменой, без кухни, кассы и прочих не относящихся
    к делу ролей. Пустой branch_code -> все филиалы из configured_branch_codes()
    (курьерский апп не знает филиал до выбора имени; в строке он уже есть).

    Решение заказчика 09.08.2026: берём все текущие открытые смены, а не только
    начатые сегодня — курьеры с ночной смены (кассир открыл её вчера вечером)
    пропадали из аппа и не могли войти. Единственная защита от мусора: явки в
    нашем iiko иногда не закрывают годами, поэтому всё, что старше
    STALE_ATTENDANCE_DAYS, считаем забытым хвостом, а не сменой."""
    one = (branch_code or "").strip()
    branches = [one] if one else configured_branch_codes()
    if not branches:
        raise ValueError("BRANCH_CODE не настроен")
    out: list[dict] = []
    for branch in branches:
        out.extend(await _shift_for_branch(branch))
    return out


async def _shift_for_branch(branch: str) -> list[dict]:
    """Открытые смены одного филиала. Кэш ~60с — бережём слот лицензии RMS."""
    now = time.monotonic()
    cached = _SHIFT_CACHE.get(branch)
    if cached and (now - cached[0]) < _SHIFT_CACHE_TTL:
        return cached[1]

    today = _attendance_today_str()
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(
                f"{base}/api/employees/attendance/byDepartment/{branch}",
                params={"key": token, "from": today, "to": today})
            r.raise_for_status()
            attendances = _parse_attendances(r.text)

    open_today = {a["employee_id"] for a in attendances
                  if _is_current_shift(a, today)}

    employees, departments = await _employees_and_departments_cached()
    branch_name = next((d["name"] for d in departments if d["code"] == branch), branch)
    org_map = await branch_to_org_id()
    org_id = org_map.get(branch, branch)

    out: list[dict] = []
    for emp in employees:
        if emp["id"] not in open_today:
            continue
        if emp["deleted"] or not emp["is_employee"]:
            continue
        if emp["role"] in EXCLUDED_LOGIN_ROLES:
            continue
        out.append({
            "employee_id": emp["id"],
            "name": emp["name"],
            "role": emp["role"],
            "code": emp["code"],
            "branch_code": branch,
            "branch_name": branch_name,
            "org_id": org_id,
        })
    out.sort(key=lambda e: e["name"])
    _SHIFT_CACHE[branch] = (now, out)
    return out
