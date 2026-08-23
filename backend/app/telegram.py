from __future__ import annotations
import hmac
import hashlib
import json
from urllib.parse import parse_qsl
import httpx
from app.config import settings


def parse_and_validate(init_data: str) -> dict | None:
    """Validate Telegram WebApp initData. Returns parsed dict (incl. 'user' json) if valid, else None."""
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=False))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        return None
    return pairs  # contains 'user' as a JSON string, 'auth_date', etc.


def extract_user(pairs: dict) -> dict:
    """Parse the 'user' JSON blob from validated initData pairs into {id, first_name, username}."""
    try:
        user = json.loads(pairs.get("user", "{}"))
    except Exception:
        user = {}
    return {
        "id": user.get("id"),
        "first_name": user.get("first_name", ""),
        "username": user.get("username", ""),
    }


async def set_webhook(url: str, secret_token: str = "") -> dict:
    """Регистрирует webhook бота на наш URL для приёма Live Location (и сообщений).
    allowed_updates: message + edited_message (живая геопозиция приходит как
    edited_message с обновлённым location)."""
    token = settings.telegram_bot_token
    if not token:
        return {"ok": False, "error": "no bot token"}
    body = {
        "url": url,
        "allowed_updates": ["message", "edited_message"],
        "drop_pending_updates": True,
    }
    if secret_token:
        body["secret_token"] = secret_token
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"https://api.telegram.org/bot{token}/setWebhook", json=body)
        try:
            return r.json()
        except Exception:
            return {"ok": False, "status": r.status_code}


async def send_message(chat_id: str, text: str, token: str | None = None) -> bool:
    """Отправляет сообщение в Telegram через Bot API sendMessage.

    token — если нужно отправить не основным ботом (у отчётов управляющим свой).

    Возвращает True при HTTP 200, иначе False. Любая сетевая ошибка
    проглатывается (возвращает False), чтобы рассылка алертов не падала.
    """
    token = token or settings.telegram_bot_token
    if not token or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
            return r.status_code == 200
    except Exception:
        return False
