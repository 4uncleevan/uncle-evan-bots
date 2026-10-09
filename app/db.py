"""База: одна на обидва боти. Ключ людини — Telegram ID. Час — секунди Unix (UTC)."""
import time

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

md = sa.MetaData()
BigInt = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

users = sa.Table(
    "users", md,
    sa.Column("tg_id", sa.BigInteger, primary_key=True, autoincrement=False),
    sa.Column("username", sa.String(64)),
    sa.Column("name", sa.String(200)),
    sa.Column("source", sa.String(64)),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("order_bot", sa.Boolean, default=False, nullable=False, server_default=sa.false()),
    sa.Column("info_bot", sa.Boolean, default=False, nullable=False, server_default=sa.false()),
    sa.Column("consent_at", sa.BigInteger),
    sa.Column("no_remind", sa.Boolean, default=False, nullable=False, server_default=sa.false()),
    sa.Column("blocked", sa.Boolean, default=False, nullable=False, server_default=sa.false()),
)
calcs = sa.Table(
    "calcs", md,
    sa.Column("id", BigInt, primary_key=True, autoincrement=True),
    sa.Column("tg_id", sa.BigInteger, index=True, nullable=False),
    sa.Column("state", sa.JSON, nullable=False),
    sa.Column("grant", sa.BigInteger, nullable=False),
    sa.Column("pct", sa.Integer, nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("updated_at", sa.BigInteger, nullable=False),
    sa.Column("ordered", sa.Boolean, default=False, nullable=False, server_default=sa.false()),
    sa.Column("remind_stage", sa.Integer, default=0, nullable=False, server_default="0"),
)
progress = sa.Table(
    "progress", md,
    sa.Column("tg_id", sa.BigInteger, primary_key=True, autoincrement=False),
    sa.Column("state", sa.JSON, nullable=False),
    sa.Column("started_at", sa.BigInteger, nullable=False),
    sa.Column("updated_at", sa.BigInteger, nullable=False),
    sa.Column("remind_stage", sa.Integer, default=0, nullable=False, server_default="0"),
)
apps = sa.Table(
    "applications", md,
    sa.Column("id", BigInt, primary_key=True, autoincrement=True),
    sa.Column("tg_id", sa.BigInteger, index=True, nullable=False),
    sa.Column("state", sa.JSON, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("grant", sa.BigInteger, nullable=False),
    sa.Column("status", sa.String(20), nullable=False, default="new"),
    sa.Column("source", sa.String(64)),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("status_at", sa.BigInteger, nullable=False),
    sa.Column("group_chat", sa.BigInteger),
    sa.Column("group_msg", sa.BigInteger),
    sa.Column("review_asked", sa.Boolean, default=False, nullable=False, server_default=sa.false()),
    sa.Column("unpaid_alerted", sa.Boolean, default=False, nullable=False, server_default=sa.false()),
)
events = sa.Table(
    "events", md,
    sa.Column("id", BigInt, primary_key=True, autoincrement=True),
    sa.Column("tg_id", sa.BigInteger, index=True),
    sa.Column("name", sa.String(40), index=True, nullable=False),
    sa.Column("source", sa.String(64)),
    sa.Column("at", sa.BigInteger, index=True, nullable=False),
)
settings = sa.Table(
    "settings", md,
    sa.Column("key", sa.String(40), primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
)

engine: AsyncEngine | None = None


def now() -> int:
    return int(time.time())


async def init(url: str) -> None:
    global engine
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as c:
        await c.run_sync(md.create_all)


async def close() -> None:
    if engine:
        await engine.dispose()


async def one(q):
    async with engine.connect() as c:
        r = (await c.execute(q)).mappings().first()
        return dict(r) if r else None


async def all_(q) -> list[dict]:
    async with engine.connect() as c:
        return [dict(r) for r in (await c.execute(q)).mappings().all()]


async def scalar(q):
    async with engine.connect() as c:
        return (await c.execute(q)).scalar()


async def run(q):
    async with engine.begin() as c:
        return await c.execute(q)


# ---------- settings ----------
async def get_setting(key: str, default: str = "") -> str:
    r = await one(sa.select(settings).where(settings.c.key == key))
    return r["value"] if r else default


async def set_setting(key: str, value: str) -> None:
    async with engine.begin() as c:
        res = await c.execute(sa.update(settings).where(settings.c.key == key).values(value=str(value)))
        if not res.rowcount:
            await c.execute(sa.insert(settings).values(key=key, value=str(value)))


# ---------- users ----------
async def touch_user(tg_id: int, username: str | None, name: str | None, *, source: str | None = None,
                     bot: str | None = None) -> dict:
    """Створює людину або оновлює ім'я. Джерело фіксується один раз — перше."""
    async with engine.begin() as c:
        row = (await c.execute(sa.select(users).where(users.c.tg_id == tg_id))).mappings().first()
        vals = {"username": (username or None), "name": (name or "")[:200]}
        if bot == "order":
            vals["order_bot"] = True
        if bot == "info":
            vals["info_bot"] = True
        if bot:
            vals["blocked"] = False
        if row is None:
            await c.execute(sa.insert(users).values(tg_id=tg_id, created_at=now(), source=source or None,
                                                    order_bot=False, info_bot=False, no_remind=False, blocked=False) )
            await c.execute(sa.update(users).where(users.c.tg_id == tg_id).values(**vals))
        else:
            if source and not row["source"]:
                vals["source"] = source
            await c.execute(sa.update(users).where(users.c.tg_id == tg_id).values(**vals))
        row = (await c.execute(sa.select(users).where(users.c.tg_id == tg_id))).mappings().first()
        return dict(row)


async def get_user(tg_id: int) -> dict | None:
    return await one(sa.select(users).where(users.c.tg_id == tg_id))


async def set_user(tg_id: int, **vals) -> None:
    await run(sa.update(users).where(users.c.tg_id == tg_id).values(**vals))


async def log(name: str, tg_id: int | None = None, source: str | None = None) -> None:
    await run(sa.insert(events).values(name=name, tg_id=tg_id, source=source, at=now()))


async def delete_user_data(tg_id: int) -> None:
    async with engine.begin() as c:
        for t in (progress, calcs, apps, events):
            await c.execute(sa.delete(t).where(t.c.tg_id == tg_id))
        await c.execute(sa.delete(users).where(users.c.tg_id == tg_id))


# ---------- calcs ----------
async def save_calc(tg_id: int, state: dict, grant: int, pct: int) -> int:
    """Один активний (незамовлений) розрахунок на людину; оновлюється на місці."""
    async with engine.begin() as c:
        row = (await c.execute(
            sa.select(calcs).where(calcs.c.tg_id == tg_id, calcs.c.ordered.is_(False)).order_by(calcs.c.id.desc())
        )).mappings().first()
        if row:
            await c.execute(sa.update(calcs).where(calcs.c.id == row["id"]).values(
                state=state, grant=grant, pct=pct, updated_at=now(), remind_stage=0))
            return row["id"]
        res = await c.execute(sa.insert(calcs).values(
            tg_id=tg_id, state=state, grant=grant, pct=pct, created_at=now(), updated_at=now(),
            ordered=False, remind_stage=0))
        return res.inserted_primary_key[0]


async def last_calc(tg_id: int) -> dict | None:
    return await one(sa.select(calcs).where(calcs.c.tg_id == tg_id).order_by(calcs.c.updated_at.desc(), calcs.c.id.desc()))


# ---------- progress ----------
async def save_progress(tg_id: int, state: dict) -> None:
    async with engine.begin() as c:
        res = await c.execute(sa.update(progress).where(progress.c.tg_id == tg_id).values(
            state=state, updated_at=now(), remind_stage=0))
        if not res.rowcount:
            await c.execute(sa.insert(progress).values(
                tg_id=tg_id, state=state, started_at=now(), updated_at=now(), remind_stage=0))


async def get_progress(tg_id: int) -> dict | None:
    return await one(sa.select(progress).where(progress.c.tg_id == tg_id))


async def drop_progress(tg_id: int) -> None:
    await run(sa.delete(progress).where(progress.c.tg_id == tg_id))


# ---------- applications ----------
async def add_app(tg_id: int, state: dict, text: str, grant: int, source: str | None) -> int:
    async with engine.begin() as c:
        res = await c.execute(sa.insert(apps).values(
            tg_id=tg_id, state=state, text=text, grant=grant, status="new", source=source,
            created_at=now(), status_at=now(), review_asked=False, unpaid_alerted=False))
        await c.execute(sa.delete(progress).where(progress.c.tg_id == tg_id))
        await c.execute(sa.update(calcs).where(calcs.c.tg_id == tg_id).values(ordered=True))
        return res.inserted_primary_key[0]


async def get_app(app_id: int) -> dict | None:
    return await one(sa.select(apps).where(apps.c.id == app_id))


async def set_app(app_id: int, **vals) -> None:
    await run(sa.update(apps).where(apps.c.id == app_id).values(**vals))
