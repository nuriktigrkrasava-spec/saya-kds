"""Ежедневный отчёт об опозданиях в Telegram: что написать и когда отправить."""
import pytest
from app import late_report as lr


DATA = {
    "total": 48, "late_count": 3, "late_rate": 0.0625,
    "avg_delay_minutes": 16.3, "max_delay_minutes": 37,
    "reasons": {"kitchen": 2, "delivery": 1},
    "orders": [
        {"number": 117, "courier": "Мейрам курьер", "delay_minutes": 37,
         "reason": {"key": "kitchen", "label": "Долгая готовка", "over_seconds": 1080}},
        {"number": 23, "courier": "Адильхан", "delay_minutes": 7,
         "reason": {"key": "delivery", "label": "Долго везли", "over_seconds": 300}},
        {"number": 91, "courier": "Мейіржан", "delay_minutes": 5, "reason": None},
    ],
}


class TestMessage:
    def _text(self, data=None):
        return lr.format_report("2026-08-23", "Г.Алиева 10 (EXPO)", data or DATA)

    def test_header_names_the_day_and_the_branch(self):
        text = self._text()
        assert "23.08" in text
        assert "Г.Алиева 10 (EXPO)" in text

    def test_totals_are_there(self):
        text = self._text()
        assert "48" in text and "3" in text and "6%" in text

    def test_each_order_shows_number_courier_delay_and_reason(self):
        """Причина у каждого заказа — иначе управляющему непонятно, к кому идти."""
        text = self._text()
        assert "№117" in text
        assert "Мейрам курьер" in text
        assert "+37 мин" in text
        assert "Долгая готовка" in text

    def test_order_without_a_reason_says_so(self):
        """Молча оставлять пустое место нельзя: решат, что причина потерялась."""
        assert "причина неизвестна" in self._text().lower()

    def test_only_five_worst_orders_get_listed(self):
        many = dict(DATA, late_count=9, orders=[
            {"number": n, "courier": "К", "delay_minutes": 50 - n, "reason": None}
            for n in range(1, 10)])
        text = lr.format_report("2026-08-23", "Филиал", many)
        assert text.count("№") == 5
        assert "ещё 4" in text

    def test_quiet_day_still_reports(self):
        """Молчание неотличимо от поломки — пишем, что опозданий не было."""
        quiet = {"total": 40, "late_count": 0, "late_rate": 0.0,
                 "avg_delay_minutes": None, "max_delay_minutes": None,
                 "reasons": {}, "orders": []}
        text = lr.format_report("2026-08-23", "Филиал", quiet)
        assert "опозданий нет" in text.lower()
        assert "40" in text


class TestSchedule:
    def test_fires_in_the_first_hour_of_the_day(self):
        assert lr.is_due("2026-08-24T00:04:00", hour=0) is True
        assert lr.is_due("2026-08-24T00:55:00", hour=0) is True

    def test_silent_the_rest_of_the_day(self):
        assert lr.is_due("2026-08-24T13:00:00", hour=0) is False

    def test_reports_for_the_day_that_just_ended(self):
        assert lr.report_date("2026-08-24T00:04:00") == "2026-08-23"


class TestRecipients:
    def test_ids_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LATE_REPORT_CHAT_IDS", "355897224, 8727082281")
        assert lr.chat_ids() == ["355897224", "8727082281"]

    def test_no_ids_means_reports_are_off(self, monkeypatch):
        monkeypatch.delenv("LATE_REPORT_CHAT_IDS", raising=False)
        assert lr.chat_ids() == []


class TestSuspiciousDelays:
    """Заказ, провисевший весь день и закрытый разом в конце смены, даёт
    «опоздание» в шесть часов. На проде такое было 22.08: +373 минуты у одного
    заказа, и среднее по дню стало 373 минуты. Молча выдавать это за опоздание
    доставки нельзя — отчёту перестанут верить."""

    def _one(self, delay):
        return {"total": 24, "late_count": 1, "late_rate": 0.04,
                "avg_delay_minutes": delay, "max_delay_minutes": delay,
                "reasons": {"kitchen": 1},
                "orders": [{"number": 47, "courier": "Адильхан",
                            "delay_minutes": delay,
                            "reason": {"label": "Долгая готовка"}}]}

    def test_huge_delay_is_marked_as_doubtful(self):
        text = lr.format_report("2026-08-22", "Филиал", self._one(373))
        assert "задним числом" in text

    def test_normal_delay_is_not_marked(self):
        text = lr.format_report("2026-08-22", "Филиал", self._one(37))
        assert "задним числом" not in text
