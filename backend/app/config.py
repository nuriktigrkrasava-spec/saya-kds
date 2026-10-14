from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cloud_api_login: str = ""
    webhook_secret: str = ""
    rms_base: str = "https://saya-co.iiko.it:443/resto"
    rms_login: str = ""
    rms_password: str = ""
    jwt_secret: str = ""
    environment: str = "development"
    admin_api_key: str = ""
    telegram_webhook_secret: str = ""
    employee_org_access: str = "{}"
    cors_origins: str = "http://localhost:5173"
    database_url: str = "sqlite+aiosqlite:///./kitchen.db"
    telegram_bot_token: str = ""
    iiko_tz_offset_hours: int = 5
    # Код единственного филиала (RMS branch_code). Привязывает данные без org_id —
    # сейчас это курьеры на дашборде. Пусто -> авто: если в базе ровно один филиал.
    branch_code: str = ""
    # Коды филиалов на КДС через запятую (RMS branch_code) — мультифилиальная
    # замена branch_code. Пусто -> берём единственный branch_code, если задан.
    branch_codes: str = ""
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


def validate_production_settings():
    import json
    access = json.loads(settings.employee_org_access)
    if not isinstance(access, dict) or any(not isinstance(v, list) for v in access.values()):
        raise RuntimeError("EMPLOYEE_ORG_ACCESS must map employee IDs to organization arrays")
    if settings.environment != "production": return
    required = {"JWT_SECRET": settings.jwt_secret, "WEBHOOK_SECRET": settings.webhook_secret,
                "ADMIN_API_KEY": settings.admin_api_key}
    if settings.telegram_bot_token:
        required["TELEGRAM_WEBHOOK_SECRET"] = settings.telegram_webhook_secret
    invalid = [name for name,value in required.items() if len(value) < 32
               or value in {"dev-only-change-me", "saya-kds-webhook-secret"}]
    if invalid: raise RuntimeError("Configure independent strong secrets: " + ", ".join(invalid))
    if len(set(required.values())) != len(required):
        raise RuntimeError("Administrative, JWT and webhook secrets must be independent")
    if "*" in settings.cors_origins.split(","):
        raise RuntimeError("Wildcard CORS is forbidden in production")
