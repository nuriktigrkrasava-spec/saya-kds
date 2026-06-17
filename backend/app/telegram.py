from __future__ import annotations
import hmac
import hashlib
import json
from urllib.parse import parse_qsl
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
