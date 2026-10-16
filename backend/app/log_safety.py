"""Redact credentials even when an exception includes a request URL."""
import hashlib, logging, re
from app.config import settings


def redact(text):
    text = re.sub(r"(/bot)[^/\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED]", text)
    text = re.sub(r"([?&](?:token|key|pass|login|access_token|initData)=)[^&\s\"']+", r"\1[REDACTED]", text)
    values = [settings.telegram_bot_token, settings.report_bot_token, settings.rms_login,
        settings.rms_password, settings.cloud_api_login, settings.jwt_secret,
        settings.webhook_secret, settings.telegram_webhook_secret, settings.analytics_api_key, settings.admin_api_key]
    if settings.rms_password: values.append(hashlib.sha1(settings.rms_password.encode()).hexdigest())
    for value in sorted((v for v in values if len(v)>=4), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text


class SafeFormatter(logging.Formatter):
    def format(self, record): return redact(super().format(record))


def configure_safe_logging():
    for name in ["httpx", "httpcore"]: logging.getLogger(name).setLevel(logging.WARNING)
    for name in ["", "uvicorn", "uvicorn.error", "uvicorn.access"]:
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
