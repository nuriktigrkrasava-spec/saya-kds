"""Подсказка сушисту за полчаса до наплыва: чего и сколько заготовить.

Считаем по месяцу продаж из iiko. Состав сетов туда попадает модификаторами
(«-Филадельфия жареная 4 шт»), поэтому роллы внутри сетов считаются наравне с
обычными позициями — техкарты не нужны.
"""
from app import prep_forecast as pf


def _row(name, hour, amount, date="2026-08-07", group="Классические роллы"):
    return {"DishName": name, "DishGroup": group, "DishGroup.TopParent": "Кухня",
            "HourOpen": f"{hour:02d}", "OpenDate.Typed": date,
            "DishAmountInt": amount}


class TestCounting:
    def test_set_contents_count_as_rolls(self):
        """Модификатор — это ролл внутри сета, он занимает руки так же."""
        rows = pf.dishes_by_hour([
            _row("Филадельфия", 19, 3),
            _row("-Филадельфия жареная 4 шт", 19, 2, group="Сеты-Комбо"),
        ])
        assert "Филадельфия жареная 4 шт" in {r["dish"] for r in rows}

    def test_leading_dash_is_stripped(self):
        rows = pf.dishes_by_hour([_row("-Калифорния", 19, 1)])
        assert rows[0]["dish"] == "Калифорния"

    def test_drinks_are_not_prep_work(self):
        """Колу не заготавливают — в подсказке она только мешает."""
        rows = pf.dishes_by_hour([_row("-кола 1", 19, 5, group="Напиток 1л на выбор")])
        assert rows == []

    def test_same_dish_in_one_hour_is_summed(self):
        rows = pf.dishes_by_hour([_row("Филадельфия", 19, 3), _row("Филадельфия", 19, 4)])
        assert rows[0]["amount"] == 7


class TestHint:
    def _month(self):
        rows = []
        for week in range(4):
            day = f"2026-08-{7 + week * 7:02d}"        # четыре пятницы
            for hour in range(12, 22):
                rows.append(_row("Филадельфия", hour, 2, date=day))
            rows.append(_row("Филадельфия", 19, 12, date=day))
            rows.append(_row("Калифорния", 19, 6, date=day))
        return pf.dishes_by_hour(rows)

    def test_hint_names_dishes_and_amounts_for_the_peak_hour(self):
        hint = pf.hint_for(self._month(), weekday=4, hour=19)
        assert hint is not None
        assert hint["hour"] == 19
        assert [d["dish"] for d in hint["dishes"]][:2] == ["Филадельфия", "Калифорния"]
        assert hint["dishes"][0]["amount"] == 14      # 2 обычных + 12 в пик

    def test_quiet_hour_gives_no_hint(self):
        """Обычный час не повод дёргать повара."""
        assert pf.hint_for(self._month(), weekday=4, hour=15) is None

    def test_other_weekday_has_its_own_picture(self):
        assert pf.hint_for(self._month(), weekday=0, hour=19) is None

    def test_no_history_no_hint(self):
        assert pf.hint_for([], weekday=4, hour=19) is None


class TestCondiments:
    """Васаби, имбирь и соевый соус идут к каждому роллу. На проде они заняли
    три строки из четырёх в каждой подсказке и заслонили то, что реально надо
    крутить. Классификатор относит их к роллам — в названии стоит «для роллов»."""

    def test_condiments_are_not_prep_work(self):
        rows = pf.dishes_by_hour([
            _row("Васаби для роллов 5 гр (весовой)", 19, 8),
            _row("Имбирь для роллов 15 гр (весовой)", 19, 8),
            _row("Соевый соус для роллов 30 мл (штучно)", 19, 8),
            _row("Филадельфия", 19, 3),
        ])
        assert [r["dish"] for r in rows] == ["Филадельфия"]

    def test_a_real_roll_with_sauce_in_the_name_survives(self):
        """«Ролл с соусом спайси» — это блюдо, а не порция соуса."""
        rows = pf.dishes_by_hour([_row("Ролл с соусом спайси", 19, 2)])
        assert rows and rows[0]["dish"] == "Ролл с соусом спайси"


class TestPreview:
    """Плашку нужно уметь показать по требованию — проверить её на планшете,
    не дожидаясь часа пик."""

    def _rows(self):
        rows = []
        for week in range(4):
            day = f"2026-08-04"  # вторник
            for hour in range(12, 22):
                rows.append(_row("Филадельфия", hour, 2, date=day))
            rows.append(_row("Филадельфия", 19, 12, date=day))
        return pf.dishes_by_hour(rows)

    def test_asked_hour_is_returned_even_if_it_is_not_now(self):
        hint = pf.hint_for(self._rows(), weekday=1, hour=19)
        assert hint and hint["hour"] == 19

    def test_asked_quiet_hour_still_gives_nothing(self):
        """Показ по требованию не должен выдумывать пик там, где его нет."""
        assert pf.hint_for(self._rows(), weekday=1, hour=14) is None
