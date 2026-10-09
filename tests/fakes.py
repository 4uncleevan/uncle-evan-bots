"""Заглушка Telegram-бота: записує виклики замість відправки."""
from types import SimpleNamespace


class FakeBot:
    def __init__(self, bot_id: int = 1):
        self.id = bot_id
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail_for: set[int] = set()
        self._n = 100

    def _msg(self):
        self._n += 1
        return SimpleNamespace(message_id=self._n, photo=[SimpleNamespace(file_id=f"file{self._n}")])

    def _rec(self, name, a, kw):
        self.calls.append((name, a, kw))
        chat = a[0] if a else kw.get("chat_id")
        if chat in self.fail_for:
            from aiogram.exceptions import TelegramForbiddenError
            raise TelegramForbiddenError(method=None, message="blocked")
        return self._msg()

    async def send_message(self, *a, **kw):
        return self._rec("send_message", a, kw)

    async def send_photo(self, *a, **kw):
        return self._rec("send_photo", a, kw)

    async def send_document(self, *a, **kw):
        return self._rec("send_document", a, kw)

    async def send_video(self, *a, **kw):
        return self._rec("send_video", a, kw)

    def sent(self, name=None, chat=None):
        return [c for c in self.calls if (name is None or c[0] == name) and (chat is None or (c[1] and c[1][0] == chat))]
