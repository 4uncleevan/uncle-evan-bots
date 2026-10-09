"""Налаштування з змінних середовища. Секрети — лише тут, у код не потрапляють."""
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip().lstrip("@").lower() for x in os.getenv(name, default).split(",") if x.strip()]


def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data.db")
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


@dataclass
class Config:
    order_token: str = field(default_factory=lambda: os.getenv("BOT_ORDER_TOKEN", ""))
    info_token: str = field(default_factory=lambda: os.getenv("BOT_INFO_TOKEN", ""))
    public_url: str = field(default_factory=lambda: (
        os.getenv("PUBLIC_URL") or
        (("https://" + os.getenv("RAILWAY_PUBLIC_DOMAIN")) if os.getenv("RAILWAY_PUBLIC_DOMAIN") else "")
    ).rstrip("/"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    db_url: str = field(default_factory=_db_url)
    admins: list[str] = field(default_factory=lambda: _list("ADMIN_USERNAMES", "diadyaevan"))
    price: int = field(default_factory=lambda: int(os.getenv("PRICE", "7000")))
    channel_url: str = field(default_factory=lambda: os.getenv("CHANNEL_URL", "https://t.me/granrtydia"))
    reviews_url: str = field(default_factory=lambda: os.getenv("REVIEWS_URL", "https://t.me/uncleevan"))
    owner_url: str = field(default_factory=lambda: os.getenv("OWNER_URL", "https://t.me/diadyaevan"))
    site_url: str = field(default_factory=lambda: os.getenv("SITE_URL", "https://uncleevan.watt-coin.org/"))
    policy_url: str = field(default_factory=lambda: os.getenv("POLICY_URL", ""))
    video_url: str = field(default_factory=lambda: os.getenv("VIDEO_URL", ""))
    sheet_webhook: str = field(default_factory=lambda: os.getenv("SHEET_WEBHOOK_URL", ""))
    terms_date: str = field(default_factory=lambda: os.getenv("TERMS_DATE", "25.09.2026"))
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo(os.getenv("TZ_NAME", "Europe/Kyiv")))
    quiet_from: int = 22
    quiet_to: int = 8


cfg = Config()
