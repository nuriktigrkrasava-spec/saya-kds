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


settings = Settings()
