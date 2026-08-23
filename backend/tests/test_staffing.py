"""Люди на кухне против объёма заказов: сколько стояло, где всплеск, сколько ставить.

Зоны — по планшетам на кухне: «Япония» (роллы) и «Пицца и горячее» (одна зона,
один планшет). Смены берём из явок iiko, а не из входов на планшет: планшета
всего два, а людей в зоне больше.
"""
from app import staffing


def _att(emp, frm, to=None):
    return {"employee_id": emp, "date_from": frm, "date_to": to or ""}


ROLES = {"суш1": "СУШ", "суш2": "СЭКСПО12", "пицц": "ПИЦЦ",
         "повар": "CO1", "курьер": "КР", "кассир": "CS1"}


class TestZones:
    def test_roles_map_to_two_kitchen_tablets(self):
        assert staffing.zone_of_role("СУШ") == "japan"
        assert staffing.zone_of_role("СЭКСПО13") == "japan"
        # пицца и горячее — одна зона: на кухне у них общий планшет
        assert staffing.zone_of_role("ПИЦЦ") == "pizza_hot"
        assert staffing.zone_of_role("CO1") == "pizza_hot"
        assert staffing.zone_of_role("СуШеф") == "pizza_hot"

    def test_non_kitchen_roles_are_ignored(self):
        assert staffing.zone_of_role("КР") is None
        assert staffing.zone_of_role("CS1") is None
        assert staffing.zone_of_role(None) is None


class TestShiftHours:
    def test_shift_covers_started_hours_only(self):
        """С 11:25 до 14:00 человек отработал часы 11, 12 и 13, но не 14."""
        rows = staffing.people_by_hour(
            [_att("суш1", "2026-08-05T11:25:00+05:00", "2026-08-05T14:00:00+05:00")],
            ROLES)
        assert [(r["date"], r["hour"], r["zone"], r["people"]) for r in rows] == [
            ("2026-08-05", 11, "japan", 1),
            ("2026-08-05", 12, "japan", 1),
            ("2026-08-05", 13, "japan", 1),
        ]

    def test_night_shift_splits_across_two_dates(self):
        """Смена до 00:05 — реальный случай с прода, час 0 уходит на новую дату."""
        rows = staffing.people_by_hour(
            [_att("суш1", "2026-08-05T22:00:00+05:00", "2026-08-06T01:00:00+05:00")],
            ROLES)
        assert [(r["date"], r["hour"]) for r in rows] == [
            ("2026-08-05", 22), ("2026-08-05", 23), ("2026-08-06", 0)]

    def test_two_people_in_one_hour_are_counted_together(self):
        rows = staffing.people_by_hour([
            _att("суш1", "2026-08-05T12:00:00+05:00", "2026-08-05T13:00:00+05:00"),
            _att("суш2", "2026-08-05T12:00:00+05:00", "2026-08-05T13:00:00+05:00"),
            _att("пицц", "2026-08-05T12:00:00+05:00", "2026-08-05T13:00:00+05:00"),
        ], ROLES)
        by_zone = {r["zone"]: r["people"] for r in rows}
        assert by_zone == {"japan": 2, "pizza_hot": 1}

    def test_couriers_and_cashiers_do_not_count_as_kitchen(self):
        rows = staffing.people_by_hour([
            _att("курьер", "2026-08-05T12:00:00+05:00", "2026-08-05T13:00:00+05:00"),
            _att("кассир", "2026-08-05T12:00:00+05:00", "2026-08-05T13:00:00+05:00"),
        ], ROLES)
        assert rows == []

    def test_open_shift_counts_up_to_now(self):
        """Явку без даты закрытия обрываем «сейчас», иначе она тянется в вечность."""
        rows = staffing.people_by_hour(
            [_att("суш1", "2026-08-05T12:00:00+05:00")], ROLES,
            now_local="2026-08-05T15:30:00")
        assert [r["hour"] for r in rows] == [12, 13, 14, 15]

    def test_forgotten_shift_is_not_counted_forever(self):
        """В iiko явки не закрывают годами — такие в статистику брать нельзя."""
        rows = staffing.people_by_hour(
            [_att("суш1", "2024-01-05T12:00:00+05:00")], ROLES,
            now_local="2026-08-05T15:30:00")
        assert rows == []


class TestDemand:
    def _sale(self, name, group, amount, hour="19", date="2026-08-05"):
        return {"DishName": name, "DishGroup": group, "DishGroup.TopParent": "Кухня",
                "DishAmountInt": amount, "HourOpen": hour, "OpenDate.Typed": date}

    def test_rolls_go_to_japan_pizza_and_hot_share_a_zone(self):
        rows = staffing.demand_by_hour([
            self._sale("Филадельфия", "Классические роллы", 3),
            self._sale("Пепперони", "Пицца", 2),
            self._sale("Лагман", "Горячие блюда", 1),
        ])
        assert {(r["zone"], r["items"]) for r in rows} == {("japan", 3), ("pizza_hot", 3)}

    def test_drinks_and_services_are_not_kitchen_work(self):
        rows = staffing.demand_by_hour([
            {"DishName": "-кола 1", "DishGroup": "Напиток 1л на выбор",
             "DishGroup.TopParent": "Модификаторы", "DishAmountInt": 2,
             "HourOpen": "00", "OpenDate.Typed": "2026-08-10"},
            {"DishName": "Вынос", "DishGroup": "Доп услуги",
             "DishGroup.TopParent": "Доп услуги", "DishAmountInt": 1,
             "HourOpen": "00", "OpenDate.Typed": "2026-08-10"},
        ])
        assert rows == []

    def test_hour_comes_as_string_with_leading_zero(self):
        rows = staffing.demand_by_hour([self._sale("Филадельфия", "Роллы", 1, hour="09")])
        assert rows[0]["hour"] == 9

    def test_same_hour_is_summed(self):
        rows = staffing.demand_by_hour([
            self._sale("Филадельфия", "Классические роллы", 3),
            self._sale("Калифорния", "Запеченые роллы", 4),
        ])
        assert rows == [{"date": "2026-08-05", "hour": 19, "zone": "japan", "items": 7}]


class TestGrid:
    def _demand(self, pairs):
        return [{"date": d, "hour": h, "zone": "japan", "items": n} for d, h, n in pairs]

    def _people(self, pairs):
        return [{"date": d, "hour": h, "zone": "japan", "people": n} for d, h, n in pairs]

    def test_median_over_same_weekday(self):
        """Три среды: 10, 12, 50 позиций. Медиана 12 — выброс не задирает прогноз."""
        grid = staffing.build_grid(
            self._demand([("2026-07-22", 19, 10), ("2026-07-29", 19, 12),
                          ("2026-08-05", 19, 50)]),
            self._people([("2026-07-22", 19, 2), ("2026-07-29", 19, 2),
                          ("2026-08-05", 19, 2)]))
        cell = next(c for c in grid["cells"] if c["hour"] == 19)
        assert cell["weekday"] == 2          # среда
        assert cell["demand"] == 12
        assert cell["people"] == 2
        assert cell["per_person"] == 6

    def test_peak_hour_is_marked(self):
        """Час, который заметно выше остальных часов этого дня, — всплеск."""
        rows = [("2026-08-05", h, 5) for h in range(12, 22)]
        rows += [("2026-08-05", 19, 40)]     # перебивает 19:00
        rows = [r for r in rows if not (r[1] == 19 and r[2] == 5)]
        grid = staffing.build_grid(self._demand(rows),
                                   self._people([("2026-08-05", h, 2)
                                                 for h in range(12, 22)]))
        peaks = [c["hour"] for c in grid["cells"] if c["is_peak"]]
        assert peaks == [19]

    def test_recommendation_grows_with_demand(self):
        """Норма выработки берётся из спокойных часов; в пик людей нужно больше."""
        calm = [("2026-08-05", h, 10) for h in range(12, 22) if h != 19]
        peak = [("2026-08-05", 19, 40)]
        grid = staffing.build_grid(self._demand(calm + peak),
                                   self._people([("2026-08-05", h, 2)
                                                 for h in range(12, 22)]))
        cell = next(c for c in grid["cells"] if c["hour"] == 19)
        # спокойный час: 10 позиций на 2 человек => норма 5/чел.
        # в пик 40 позиций => нужно 8 человек, стояло 2.
        assert grid["zones"]["japan"]["norm_per_person"] == 5
        assert cell["recommended_people"] == 8
        assert cell["people"] == 2

    def test_idle_night_hours_do_not_drag_the_norm_down(self):
        """Ночью повар стоит без работы — это не значит, что он делает 1 позицию в час.

        На проде из-за этого норма «Пиццы и горячего» вышла 2 позиции на человека,
        и отчёт требовал 10 человек на 20 позиций в час. Норму считаем только по
        часам с реальной нагрузкой.
        """
        night = [("2026-08-05", h, 2) for h in range(0, 11)]     # по 2 позиции
        work = [("2026-08-05", h, 30) for h in range(18, 22)]    # рабочие часы
        people = [("2026-08-05", h, 2) for h in list(range(0, 11)) + list(range(18, 22))]
        grid = staffing.build_grid(self._demand(night + work), self._people(people))
        assert grid["zones"]["japan"]["norm_per_person"] == 15
        cell = next(c for c in grid["cells"] if c["hour"] == 19)
        assert cell["recommended_people"] == 2

    def test_hour_without_people_does_not_break_norm(self):
        grid = staffing.build_grid(
            self._demand([("2026-08-05", 12, 10)]),
            self._people([("2026-08-05", 12, 0)]))
        cell = grid["cells"][0]
        assert cell["per_person"] is None
        assert cell["recommended_people"] >= 1

    def test_empty_input_is_empty_grid(self):
        grid = staffing.build_grid([], [])
        assert grid["cells"] == []
        assert grid["zones"] == {}


class TestShiftPlan:
    """Сводка на конкретный день недели — то, что уходит управляющему."""

    def _grid(self):
        calm = [{"date": "2026-08-07", "hour": h, "zone": "japan", "items": 10}
                for h in range(12, 22) if h != 19]
        peak = [{"date": "2026-08-07", "hour": 19, "zone": "japan", "items": 40}]
        people = [{"date": "2026-08-07", "hour": h, "zone": "japan", "people": 2}
                  for h in range(12, 22)]
        return staffing.build_grid(calm + peak, people)

    def test_plan_lists_only_hours_where_hands_are_short(self):
        plan = staffing.shift_plan(self._grid(), weekday=4)   # пятница
        assert [(p["hour"], p["people"], p["recommended_people"]) for p in plan] == [
            (19, 2, 8)]

    def test_other_weekday_has_nothing_to_say(self):
        assert staffing.shift_plan(self._grid(), weekday=0) == []

    def test_plan_carries_zone_label_for_the_message(self):
        assert staffing.shift_plan(self._grid(), weekday=4)[0]["zone_label"] == "Япония"


class TestOpenShiftClock:
    """Открытая смена и «сейчас» должны меряться одними часами."""

    def _open_shift_started_hours_ago(self, hours):
        from datetime import datetime, timedelta
        from app.config import settings
        off = settings.iiko_tz_offset_hours
        local_now = datetime.utcnow() + timedelta(hours=off)
        start = (local_now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        return _att("суш1", f"{start}+{off:02d}:00")

    def test_shift_open_right_now_is_counted(self):
        """Время явки локальное, а «сейчас» бралось в UTC.

        Из-за разницы в 5 часов конец смены оказывался РАНЬШЕ начала, и вся
        смена отбрасывалась: зона показывала ноль людей, а отчёт требовал
        поставить ещё троих.
        """
        rows = staffing.people_by_hour([self._open_shift_started_hours_ago(2)], ROLES)
        assert rows, "смена, открытая два часа назад, должна считаться"
        assert all(r["zone"] == "japan" for r in rows)

    def test_open_shift_covers_hours_since_it_started(self):
        rows = staffing.people_by_hour([self._open_shift_started_hours_ago(3)], ROLES)
        assert 3 <= len(rows) <= 4      # границы часа дают +-1


def test_one_person_with_two_records_is_still_one_person():
    """Перерыв даёт две явки на одного человека — это не двое на смене.

    Иначе нехватка рук прячется: в час, где реально один человек, отчёт видит
    двоих и молчит.
    """
    rows = staffing.people_by_hour([
        _att("суш1", "2026-08-05T12:00:00+05:00", "2026-08-05T13:00:00+05:00"),
        _att("суш1", "2026-08-05T12:30:00+05:00", "2026-08-05T13:00:00+05:00"),
    ], ROLES)
    assert [r["people"] for r in rows] == [1]
