import hashlib
import respx
from httpx import Response
from app import rms
from app.config import settings

DEPARTMENTS_XML = """<?xml version="1.0"?><corporateItemDtoes>
<corporateItemDto><id>d-aaa</id><code>AAA</code><name>Альфа</name><type>DEPARTMENT</type></corporateItemDto>
<corporateItemDto><id>d-bbb</id><code>BBB</code><name>Бета</name><type>DEPARTMENT</type></corporateItemDto>
<corporateItemDto><id>d-jur</id><code>JUR</code><name>ТОО Сая</name><type>JURPERSON</type></corporateItemDto>
</corporateItemDtoes>"""

EMPLOYEES_XML = """<?xml version="1.0"?><employees>
<employee><id>e1</id><code>1</code><name>Иван</name><mainRoleCode>ПОВ</mainRoleCode><departmentCodes>AAA</departmentCodes><deleted>false</deleted><employee>true</employee></employee>
<employee><id>e2</id><code>2</code><name>Удалённый</name><mainRoleCode>ПОВ</mainRoleCode><departmentCodes>AAA</departmentCodes><deleted>true</deleted><employee>true</employee></employee>
<employee><id>e3</id><code>3</code><name>Сирота</name><mainRoleCode>ПОВ</mainRoleCode><departmentCodes>ZZZ</departmentCodes><deleted>false</deleted><employee>true</employee></employee>
</employees>"""


@respx.mock
async def test_branches_with_employees_groups_and_filters():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    sha1 = hashlib.sha1(b"pw").hexdigest()

    auth = respx.get(
        f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}"
    ).mock(return_value=Response(200, text="TOKEN-1"))
    logout = respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=Response(200, text="ok"))
    deps = respx.get(
        "https://rms.test/resto/api/corporation/departments?key=TOKEN-1"
    ).mock(return_value=Response(200, text=DEPARTMENTS_XML))
    emps = respx.get("https://rms.test/resto/api/employees?key=TOKEN-1").mock(
        return_value=Response(200, text=EMPLOYEES_XML))

    result = await rms.branches_with_employees()

    # Один логин/логаут на оба запроса.
    assert auth.call_count == 1
    assert logout.call_count == 1
    assert deps.called and emps.called

    by_code = {b["code"]: b for b in result}

    # AAA («Альфа»): только живой сотрудник-человек Иван.
    assert by_code["AAA"]["name"] == "Альфа"
    assert len(by_code["AAA"]["employees"]) == 1
    assert by_code["AAA"]["employees"][0]["name"] == "Иван"

    # BBB («Бета»): сотрудников нет.
    assert len(by_code["BBB"]["employees"]) == 0

    # «Без филиала» содержит Сироту (отдел ZZZ неизвестен).
    orphan = next(b for b in result if b["name"] == "Без филиала")
    assert any(e["name"] == "Сирота" for e in orphan["employees"])

    # Удалённый сотрудник исключён везде.
    all_names = [e["name"] for b in result for e in b["employees"]]
    assert "Удалённый" not in all_names
