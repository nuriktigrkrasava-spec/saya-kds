from __future__ import annotations
import re
from typing import Callable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ProductStationMap
from app.schemas import WebhookItem

VALID_STATIONS = {"rolls", "pizza", "hot", "other"}

# Ручная разметка по НАЗВАНИЮ хранится в product_station_map с этим префиксом
# в поле product_id (без изменения схемы БД). Имя стабильно и в меню, и в заказе,
# поэтому такая привязка надёжнее, чем по product_id.
NAME_PREFIX = "name::"


def norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def classify_group(group_name: str) -> str:
    """Группа меню iiko -> зона (rolls|pizza|hot|other) по ключевым словам."""
    n = (group_name or "").lower()
    # порядок важен: ролл/сет/суши -> rolls раньше, чем 'горяч'
    if any(k in n for k in ["ролл", "сет", "суши", "маки", "темпура"]):
        return "rolls"
    if "пицц" in n:
        return "pizza"
    if any(k in n for k in ["суп", "вок", "горяч", "лапш", "паста", "вторые", "wok"]):
        return "hot"
    return "other"


def classify_name(name: str) -> str:
    """Классификация блюда по его НАЗВАНИЮ (надёжнее, чем по группе)."""
    n = (name or "").lower()
    if "пицц" in n:
        return "pizza"
    if any(k in n for k in ["ролл", "сет", "суши", "маки", "темпура", "урамаки", "филадельфия", "калифорни"]):
        return "rolls"
    if any(k in n for k in ["суп", "том ям", "том-ям", "том кха", "вок", "лапш", "горяч",
                            "паста", "удон", "рамен", "wok", "наггетс", "нагетс", "чикен",
                            "стрипс", "крыл", "боул", "тяхан", "терияки", "мисо", "лагман", "плов"]):
        return "hot"
    return "other"


# Корни меню iiko, которые НЕ относятся к кухонным зонам (товары, хозтовары,
# бар/напитки, архив, полуфабрикаты, инвентарь, канцелярия) — всё это «прочее».
_NONFOOD_ROOT_KEYS = ["товар", "хоз", "инвентар", "канцел", "архив",
                      "п/ф", "доп услуг", "цех", "бар"]
_GROUP_HOT_KEYS = ["горяч", "суп", "вок", "лапш", "паст", "рамен", "удон", "wok"]
# Кухонные подгруппы, которые точно «прочее» (не готовятся на роллах/пицце/горячем).
_OTHER_GROUP_KEYS = ["напит", "десерт", "добавк", "соус", "закуск", "салат"]
# Комбо/промо/подарки: смесь блюд. Если имя зону не определило — НЕ прячем в
# «прочее» (иначе заказ невидим кухне), а отдаём на роллы (основной цех).
_PROMO_GROUP_KEYS = ["подарок", "выгодн", "спецпредложен", "комбо", "агрегатор"]


def classify_by_group(group_path: str | None, group_root: str | None) -> str | None:
    """Зона по ПУТИ группы меню iiko (надёжнее имени для блюд без ключевых слов,
    напр. «Плов»/«Лагман» в группе «Горячие блюда»).

    Возвращает None для неоднозначных кухонных групп (Подарок, Выгодное меню,
    Блюда агрегаторов и т.п.) — там зону решает имя блюда.
    """
    p = (group_path or "").lower()
    r = (group_root or "").lower()
    # Нерелевантные корни → явно «прочее».
    if any(k in r for k in _NONFOOD_ROOT_KEYS):
        return "other"
    if "пицц" in p:
        return "pizza"
    if any(k in p for k in ["ролл", "маки", "суши", "сет"]):
        return "rolls"
    if any(k in p for k in _GROUP_HOT_KEYS):
        return "hot"
    # Напитки/десерты/соусы/закуски/салаты — точно «прочее».
    if any(k in p for k in _OTHER_GROUP_KEYS):
        return "other"
    return None


def is_promo_group(group_path: str | None) -> bool:
    """Группа-комбо/промо/подарок (смесь блюд, должна быть видна кухне)."""
    return any(k in (group_path or "").lower() for k in _PROMO_GROUP_KEYS)


def classify_product(name: str, group_path: str | None = None,
                     group_root: str | None = None) -> str:
    """Зона блюда: группа меню → имя → для комбо/промо откат на роллы (видно кухне)."""
    by_group = classify_by_group(group_path, group_root)
    if by_group:
        return by_group
    by_name = classify_name(name)
    if by_name != "other":
        return by_name
    if is_promo_group(group_path):
        return "rolls"
    return "other"


async def build_resolver(session: AsyncSession) -> Callable[[WebhookItem], str]:
    rows = (await session.execute(select(ProductStationMap))).scalars().all()
    by_product = {r.product_id: r.station for r in rows
                  if r.product_id and not r.product_id.startswith(NAME_PREFIX)}
    by_name = {r.product_id[len(NAME_PREFIX):]: r.station for r in rows
               if r.product_id and r.product_id.startswith(NAME_PREFIX)}
    by_category = {r.category_id: r.station for r in rows if r.category_id and not r.product_id}

    # Авто-классификация по ИМЕНИ через дерево групп меню RMS (надёжнее keyword'ов:
    # «Пепперони» в группе «Пиццы» -> pizza). В заказе product_id часто не совпадает
    # с меню, а имя стабильно — поэтому матчим по имени. Берём кэш меню без сетевого
    # запроса (прогревается фоном на старте); если пуст — откат на classify_name.
    from app import rms
    auto_by_name: dict[str, str] = {}
    for p in rms.products_cached():
        nm = norm_name(p.get("name"))
        if nm:
            auto_by_name[nm] = classify_product(
                p.get("name", ""), p.get("group_path"), p.get("group_root"))

    def resolve(item: WebhookItem) -> str:
        nm = norm_name(item.name)
        # Ручная разметка по названию — наивысший приоритет: её создаёт только
        # человек (авторазметка пишет лишь по product_id), поэтому она должна
        # побеждать автоматический маппинг даже после перезаписи авторазметкой.
        if nm in by_name:
            return by_name[nm]
        if item.product_id in by_product:
            return by_product[item.product_id]
        if item.category_id and item.category_id in by_category:
            return by_category[item.category_id]
        # Группа меню по имени — точнее, чем грубый classify_name.
        if nm in auto_by_name:
            return auto_by_name[nm]
        return classify_name(item.name)

    return resolve
