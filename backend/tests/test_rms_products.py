import hashlib
import respx
from httpx import Response
from app import rms
from app.config import settings

PRODUCTS_JSON = [
    {"id": "p-dish", "name": "Филадельфия", "type": "DISH",
     "deleted": False, "parent": "g-rolls"},
    {"id": "p-goods", "name": "Кола 0.5", "type": "GOODS",
     "deleted": False, "parentGroup": "g-drinks"},
    {"id": "p-mod", "name": "Доп. соус", "type": "MODIFIER",
     "deleted": False, "parent": "g-mods"},
    {"id": "p-deleted", "name": "Старое блюдо", "type": "DISH",
     "deleted": True, "parent": "g-rolls"},
]

GROUPS_JSON = [
    {"id": "g-rolls", "name": "Роллы", "parent": "g-kitchen"},
    {"id": "g-kitchen", "name": "Кухня", "parent": None},
    {"id": "g-drinks", "name": "Напитки", "parent": "g-bar"},
    {"id": "g-bar", "name": "БАР", "parent": None},
]


@respx.mock
async def test_fetch_products_keeps_only_dish_goods_not_deleted():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    sha1 = hashlib.sha1(b"pw").hexdigest()

    # Сброс кэша между тестами/прогонами.
    rms._PROD_CACHE = None
    rms._PROD_CACHE_TS = 0.0

    auth = respx.get(
        f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}"
    ).mock(return_value=Response(200, text="TOKEN-1"))
    logout = respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=Response(200, text="ok"))
    products = respx.get(
        "https://rms.test/resto/api/v2/entities/products/list",
        params={"key": "TOKEN-1", "includeDeleted": "false"},
    ).mock(return_value=Response(200, json=PRODUCTS_JSON))
    groups = respx.get(
        "https://rms.test/resto/api/v2/entities/products/group/list",
        params={"key": "TOKEN-1", "includeDeleted": "false"},
    ).mock(return_value=Response(200, json=GROUPS_JSON))

    result = await rms.fetch_products()
    assert groups.called

    # Один логин/логаут на запрос.
    assert auth.call_count == 1
    assert logout.call_count == 1
    assert products.called

    # Только не удалённые DISH+GOODS.
    by_id = {p["id"]: p for p in result}
    assert set(by_id) == {"p-dish", "p-goods"}

    # MODIFIER и удалённый DISH отброшены.
    assert "p-mod" not in by_id
    assert "p-deleted" not in by_id

    # Поля id/name/group_id + путь и корень группы.
    assert by_id["p-dish"] == {
        "id": "p-dish", "name": "Филадельфия", "group_id": "g-rolls",
        "group_path": "Кухня / Роллы", "group_root": "Кухня"}
    assert by_id["p-goods"] == {
        "id": "p-goods", "name": "Кола 0.5", "group_id": "g-drinks",
        "group_path": "БАР / Напитки", "group_root": "БАР"}
