"""Перевірка підпису initData із Telegram Mini App."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def verify(init_data: str, token: str, max_age: int = 86400 * 2) -> dict | None:
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        return None
    try:
        if max_age and time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs.get("user", "{}"))
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and user.get("id") else None


def sign(user: dict, token: str, auth_date: int | None = None) -> str:
    """Формує валідний initData — для тестів."""
    from urllib.parse import urlencode
    pairs = {"auth_date": str(auth_date or int(time.time())), "user": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)
