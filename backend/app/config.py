from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cloud_api_login: str = ""
    webhook_secret: str = ""
    rms_base: str = "https://saya-co.iiko.it:443/resto"
    rms_login: str = ""
    rms_password: str = ""
    jwt_secret: str = "dev-only-change-me"
    database_url: str = "sqlite+aiosqlite:///./kitchen.db"
    telegram_bot_token: str = ""
    iiko_tz_offset_hours: int = 5
    # Код единственного филиала (RMS branch_code). Привязывает данные без org_id —
    # сейчас это курьеры на дашборде. Пусто -> авто: если в базе ровно один филиал.
    branch_code: str = ""
    preorder_lead_minutes: int = 60
    preorder_check_seconds: int = 60
    # Ключ для межсервисного доступа к выгрузке аналитики (/admin/analytics/export).
    # Пусто -> эндпоинт выгрузки выключен (fail-closed).
    analytics_api_key: str = ""
    # Отдельный бот для отчётов управляющим. Пусто -> шлём тем же ботом, через
    # который входят курьеры: лучше прежним, чем молчать.
    report_bot_token: str = ""
    # Общий телефон колл-центра — показывается курьеру в деталке заказа.
    # Пусто -> строка КЦ в аппе скрыта.
    call_center_phone: str = ""


settings = Settings()
