import os
import sys
import pathlib

import pytest_asyncio

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("BOT_ORDER_TOKEN", "111:AAA-order-test")
os.environ.setdefault("BOT_INFO_TOKEN", "222:BBB-info-test")
os.environ.setdefault("PUBLIC_URL", "https://example.test")

from app import db, notify, ui  # noqa: E402
from fakes import FakeBot  # noqa: E402


@pytest_asyncio.fixture
async def env(tmp_path):
    url = os.getenv("TEST_DB_URL") or f"sqlite+aiosqlite:///{tmp_path}/t.db"
    await db.init(url)
    if os.getenv("TEST_DB_URL"):  # спільна база (PostgreSQL): чистимо перед кожним тестом
        async with db.engine.begin() as c:
            await c.run_sync(db.md.drop_all)
            await c.run_sync(db.md.create_all)
    order, info = FakeBot(1), FakeBot(2)
    notify.BOTS.clear()
    notify.BOTS.update(order=order, info=info)
    ui.BOT_NAMES.update(order="UncleEvanBot", info="VlasnaSpravaBot")
    ui._file_ids.clear()
    yield {"order": order, "info": info}
    await db.close()
