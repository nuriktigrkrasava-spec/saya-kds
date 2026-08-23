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


class TestSending:
    @pytest.fixture
    def outbox(self, monkeypatch):
        monkeypatch.setenv("LATE_REPORT_CHAT_IDS", "1,2")
        monkeypatch.setenv("LATE_REPORT_ORG_IDS", "org-1")
        sent = []

        async def fake_late(org_id, date_from, date_to, session=None):
            return {"department": "Филиал", "total": 10, "late_count": 0,
                    "late_rate": 0, "avg_delay_minutes": None,
                    "max_delay_minutes": None, "reasons": {}, "orders": []}

        import app.api
        monkeypatch.setattr(app.api, "_late_deliveries", fake_late)

        class _S:
            async def __aenter__(self): return None
            async def __aexit__(self, *a): return False
        import app.db
        monkeypatch.setattr(app.db, "SessionLocal", lambda: _S())
        lr.reset_sent()
        return sent

    @pytest.mark.asyncio
    async def test_refused_send_is_not_counted(self, outbox, monkeypatch):
        """send_message не бросает исключение, а возвращает False.

        Раньше мы увеличивали счётчик независимо от ответа: неотправленный
        отчёт считался отправленным, день помечался — и сводка пропадала
        насовсем.
        """
        async def refuses(chat_id, text, token=None):
            return False
        monkeypatch.setattr(lr.telegram, "send_message", refuses)

        result = await lr.run_once("org-1", now_local="2026-08-24T00:05:00")
        assert result["sent"] == 0
        assert "повтор" in result["reason"]

    @pytest.mark.asyncio
    async def test_report_goes_from_its_own_bot(self, outbox, monkeypatch):
        """Отчёт шлёт отдельный бот, а не тот, через который входят курьеры."""
        used = []

        async def spy(chat_id, text, token=None):
            used.append(token)
            return True
        monkeypatch.setattr(lr.telegram, "send_message", spy)
        monkeypatch.setattr(lr.settings, "report_bot_token", "REPORT-TOKEN")

        await lr.run_once("org-1", now_local="2026-08-24T00:05:00")
        assert used == ["REPORT-TOKEN", "REPORT-TOKEN"]

    @pytest.mark.asyncio
    async def test_falls_back_to_the_main_bot(self, outbox, monkeypatch):
        """Свой токен не задан — шлём прежним ботом, а не молчим."""
        used = []

        async def spy(chat_id, text, token=None):
            used.append(token)
            return True
        monkeypatch.setattr(lr.telegram, "send_message", spy)
        monkeypatch.setattr(lr.settings, "report_bot_token", "")

        await lr.run_once("org-1", now_local="2026-08-24T00:05:00")
        assert used == [None, None]


NETWORK = [
    {"department": "Астана, ул. Кажымукана 16", "total": 42, "late_count": 12,
     "late_rate": 12 / 42, "avg_delay_minutes": 20, "max_delay_minutes": 60,
     "reasons": {"kitchen": 8, "delivery": 4}, "orders": []},
    {"department": "Астана, ул. Г.Алиева 10 (EXPO)", "total": 25, "late_count": 2,
     "late_rate": 0.08, "avg_delay_minutes": 6, "max_delay_minutes": 11,
     "reasons": {"delivery": 1, "pickup": 1}, "orders": []},
    {"department": "Астана, ул. Бухар Жырау 36", "total": 9, "late_count": 0,
     "late_rate": 0.0, "avg_delay_minutes": None, "max_delay_minutes": None,
     "reasons": {}, "orders": []},
]


class TestNetworkReport:
    def _text(self, branches=None):
        return lr.format_network_report("2026-08-23", branches or NETWORK)

    def test_totals_are_summed_across_branches(self):
        text = self._text()
        assert "76" in text        # 42 + 25 + 9 доставок
        assert "14" in text        # 12 + 2 опозданий

    def test_worst_branch_comes_first(self):
        """Управляющий должен видеть, где горит, не читая весь список."""
        text = self._text()
        assert text.index("Кажымукана") < text.index("Г.Алиева")
        assert text.index("Г.Алиева") < text.index("Бухар Жырау")

    def test_branch_name_is_short(self):
        """«Астана, ул.» в каждой строке только мешает."""
        text = self._text()
        assert "Кажымукана 16" in text
        assert "Астана, ул. Кажымукана" not in text

    def test_reasons_are_summed_across_the_network(self):
        text = self._text()
        assert "кухня 8" in text
        assert "дорога 5" in text     # 4 + 1

    def test_branch_without_data_is_shown_not_dropped(self):
        """RMS моргнул по одной точке — остальные всё равно уходят, а эта
        честно помечается: тихо пропасть из отчёта она не должна."""
        text = self._text(NETWORK + [{"department": "Астана, ул. Сыганак 11",
                                      "error": "rms down"}])
        assert "Сыганак 11" in text
        assert "нет данных" in text

    def test_quiet_network_still_reports(self):
        quiet = [{"department": "Астана, ул. Абая", "total": 12, "late_count": 0,
                  "late_rate": 0, "avg_delay_minutes": None,
                  "max_delay_minutes": None, "reasons": {}, "orders": []}]
        text = lr.format_network_report("2026-08-23", quiet)
        assert "опозданий нет" in text.lower()


class TestBranchList:
    def test_ids_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LATE_REPORT_ORG_IDS", "a, b ,c")
        assert lr.org_ids() == ["a", "b", "c"]

    def test_single_org_still_works(self, monkeypatch):
        """Старая переменная остаётся: не ломаем то, что уже настроено."""
        monkeypatch.delenv("LATE_REPORT_ORG_IDS", raising=False)
        monkeypatch.setenv("LATE_REPORT_ORG_ID", "one")
        assert lr.org_ids() == ["one"]


class TestCollect:
    @pytest.mark.asyncio
    async def test_one_broken_branch_does_not_kill_the_report(self, monkeypatch):
        async def flaky(org_id, date_from, date_to, session=None):
            if org_id == "bad":
                raise RuntimeError("rms down")
            return {"department": f"Астана, ул. {org_id}", "total": 10,
                    "late_count": 1, "late_rate": 0.1, "reasons": {},
                    "orders": [], "avg_delay_minutes": 5, "max_delay_minutes": 5}

        import app.api, app.db, app.olap
        monkeypatch.setattr(app.api, "_late_deliveries", flaky)

        class _S:
            async def __aenter__(self): return None
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(app.db, "SessionLocal", lambda: _S())

        async def name(org): return f"Астана, ул. {org}"
        monkeypatch.setattr(app.olap, "department_name", name)

        rows = await lr.collect_branches(["good", "bad"], "2026-08-23")
        assert len(rows) == 2
        assert rows[1]["error"]
        text = lr.format_network_report("2026-08-23", rows)
        assert "good" in text and "нет данных" in text


class TestUnknownReasons:
    """Причины видны только по филиалам, заведённым в КДС. Сейчас это Алиева,
    остальные восемь точек дают «без наших отметок» — на проде так вышло 25 из
    27, и строка причин превращалась в шум."""

    def _rows(self):
        return [
            {"department": "Астана, ул. Калдаякова 23а", "total": 13,
             "late_count": 9, "late_rate": 0.69, "reasons": {"unknown": 9},
             "orders": [], "avg_delay_minutes": 10, "max_delay_minutes": 20},
            {"department": "Астана, ул. Г.Алиева 10 (EXPO)", "total": 26,
             "late_count": 2, "late_rate": 0.08,
             "reasons": {"delivery": 1, "pickup": 1}, "orders": [],
             "avg_delay_minutes": 6, "max_delay_minutes": 11},
        ]

    def test_unknown_is_not_listed_among_causes(self):
        text = lr.format_network_report("2026-08-23", self._rows())
        assert "без наших отметок" not in text
        assert "дорога 1" in text

    def test_footnote_explains_where_causes_come_from(self):
        text = lr.format_network_report("2026-08-23", self._rows())
        assert "на КДС" in text

    def test_no_footnote_when_every_cause_is_known(self):
        rows = [r for r in self._rows() if "unknown" not in r["reasons"]]
        assert "на КДС" not in lr.format_network_report("2026-08-23", rows)


class TestReadability:
    """Сводку читают с телефона: цифры должны стоять в столбик, а взгляд —
    сразу попадать на проблемные точки."""

    ROWS = [
        {"department": "Астана, ул. Калдаякова 23а", "total": 13, "late_count": 9,
         "late_rate": 9 / 13, "reasons": {"unknown": 9}, "orders": [],
         "avg_delay_minutes": 10, "max_delay_minutes": 20},
        {"department": "Астана, ул. Кумисбекова 9а", "total": 16, "late_count": 3,
         "late_rate": 3 / 16, "reasons": {"kitchen": 3}, "orders": [],
         "avg_delay_minutes": 8, "max_delay_minutes": 12},
        {"department": "Астана, ул. Бухар Жырау 36", "total": 10, "late_count": 0,
         "late_rate": 0.0, "reasons": {}, "orders": [],
         "avg_delay_minutes": None, "max_delay_minutes": None},
    ]

    def test_rows_are_in_a_monospace_block(self):
        """Без моноширинного блока Telegram съедает пробелы и столбик плывёт."""
        text = lr.format_network_report("2026-08-23", self.ROWS)
        assert "<pre>" in text and "</pre>" in text

    def test_severity_is_visible_without_reading_numbers(self):
        text = lr.format_network_report("2026-08-23", self.ROWS)
        assert "🔴" in text     # 69%
        assert "🟢" in text     # 0%

    def test_columns_line_up(self):
        text = lr.format_network_report("2026-08-23", self.ROWS)
        body = text.split("<pre>")[1].split("</pre>")[0].strip().splitlines()
        assert len({len(line.rstrip()) - len(line.rstrip().lstrip()) for line in body}) == 1
        assert all(line.rstrip().endswith("%") for line in body)

    def test_html_is_escaped(self):
        """Название филиала уедет в HTML — угловые скобки сломали бы сообщение."""
        rows = [dict(self.ROWS[0], department="Астана, ул. <Тест> & Ко")]
        text = lr.format_network_report("2026-08-23", rows)
        assert "&lt;Тест&gt;" in text and "&amp;" in text
