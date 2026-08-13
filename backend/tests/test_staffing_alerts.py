"""Уведомление управляющему о наплыве: что написать и когда отправить."""
from app import staffing_alerts as sa


PLAN = [
    {"zone": "japan", "zone_label": "Япония", "hour": 20, "demand": 74,
     "people": 3, "recommended_people": 6, "shortage": 3, "is_peak": True},
    {"zone": "pizza_hot", "zone_label": "Пицца и горячее", "hour": 20,
     "demand": 20, "people": 3, "recommended_people": 4, "shortage": 1,
     "is_peak": True},
    {"zone": "japan", "zone_label": "Япония", "hour": 10, "demand": 6,
     "people": 0, "recommended_people": 1, "shortage": 1, "is_peak": False},
]
ZONES = {"japan": {"label": "Япония", "norm_per_person": 14},
         "pizza_hot": {"label": "Пицца и горячее", "norm_per_person": 6}}


class TestMessage:
    def test_names_the_day_hour_zone_and_how_many_to_add(self):
        text = sa.format_message("2026-08-14", PLAN, ZONES, kind="evening")
        assert "завтра" in text.lower()
        assert "пятниц" in text.lower()
        assert "20:00" in text
        assert "Япония" in text
        assert "было 3" in text and "нужно 6" in text

    def test_evening_and_morning_word_differently(self):
        evening = sa.format_message("2026-08-14", PLAN, ZONES, kind="evening")
        morning = sa.format_message("2026-08-14", PLAN, ZONES, kind="morning")
        assert "завтра" in evening.lower()
        assert "сегодня" in morning.lower()

    def test_only_spikes_get_into_the_message(self):
        """Час без всплеска (10:00, шесть позиций) управляющему не нужен."""
        text = sa.format_message("2026-08-14", PLAN, ZONES, kind="evening")
        assert "10:00" not in text

    def test_quiet_day_gives_no_message(self):
        assert sa.format_message("2026-08-14", [], ZONES, kind="evening") is None

    def test_day_with_only_small_gaps_gives_no_message(self):
        calm = [p for p in PLAN if not p["is_peak"]]
        assert sa.format_message("2026-08-14", calm, ZONES, kind="evening") is None


class TestPlural:
    def test_positions_are_declined(self):
        assert sa.plural_items(1) == "1 позиция"
        assert sa.plural_items(3) == "3 позиции"
        assert sa.plural_items(74) == "74 позиции"
        assert sa.plural_items(20) == "20 позиций"
        assert sa.plural_items(11) == "11 позиций"

    def test_message_uses_correct_form(self):
        text = sa.format_message("2026-08-14", PLAN, ZONES, kind="evening")
        assert "74 позиции" in text
        assert "20 позиций" in text


class TestSchedule:
    def test_evening_alert_fires_at_its_hour(self):
        assert sa.due_kind("2026-08-13T21:05:00", evening=21, morning=10) == "evening"

    def test_morning_alert_fires_at_its_hour(self):
        assert sa.due_kind("2026-08-13T10:40:00", evening=21, morning=10) == "morning"

    def test_nothing_fires_in_between(self):
        assert sa.due_kind("2026-08-13T15:00:00", evening=21, morning=10) is None

    def test_target_day_is_tomorrow_in_the_evening_and_today_in_the_morning(self):
        assert sa.target_date("2026-08-13T21:05:00", "evening") == "2026-08-14"
        assert sa.target_date("2026-08-13T10:40:00", "morning") == "2026-08-13"

    def test_disabled_when_hours_not_set(self):
        assert sa.due_kind("2026-08-13T21:05:00", evening=None, morning=None) is None
