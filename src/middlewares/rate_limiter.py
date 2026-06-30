import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.dialog_memory import dialog_memory


MAX_MESSAGE_CHARS = 1500
SHORT_NOISE_LIMIT = 5
SHORT_NOISE_WINDOW_SECONDS = 60
SHORT_NOISE_PROMPT = (
    "✍️ <b>Пожалуйста, напишите вопрос текстом.</b>\n\n"
    "Например: что случилось с автомобилем или что нужно уточнить по сервису.\n\n"
    "Чтобы увидеть основные команды бота, отправьте /start."
)
SHORT_NOISE_BLOCK_PROMPT = "⏳ <b>Слишком частые сообщения.</b>\n\nПопробуйте позже."
LONG_MESSAGE_PROMPT = (
    "⚠️ <b>Сообщение слишком длинное.</b>\n\n"
    "Опишите коротко, какой у вас вопрос. "
    "Или отправьте /start, чтобы увидеть возможности бота."
)
ALLOWED_SHORT_TEXTS = {
    "то",
    "т о",
    "help",
    "vin",
}


class RateLimiterMiddleware(BaseMiddleware):
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.events: dict[int, deque[float]] = defaultdict(deque)
        self.short_noise_events: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)

        text = event.text or ""
        if text and len(text) > MAX_MESSAGE_CHARS:
            await event.answer(LONG_MESSAGE_PROMPT, parse_mode="HTML")
            return None

        if _is_short_noise_text(text) and not _is_expected_numeric_reply(event):
            return await self._handle_short_noise(event)

        now = time.monotonic()
        user_events = self.events[event.from_user.id]

        while user_events and now - user_events[0] > self.window_seconds:
            user_events.popleft()

        if len(user_events) >= self.limit:
            await event.answer(
                "⏳ <b>Слишком много сообщений подряд.</b>\n\n"
                "Подождите немного и повторите вопрос.",
                parse_mode="HTML",
            )
            return None

        user_events.append(now)
        return await handler(event, data)

    async def _handle_short_noise(self, event: Message) -> None:
        if not event.from_user:
            return None

        now = time.monotonic()
        user_events = self.short_noise_events[event.from_user.id]
        _drop_expired_events(user_events, now, SHORT_NOISE_WINDOW_SECONDS)
        user_events.append(now)

        if len(user_events) == 1:
            await event.answer(SHORT_NOISE_PROMPT, parse_mode="HTML")
            return None

        if len(user_events) >= SHORT_NOISE_LIMIT:
            await event.answer(SHORT_NOISE_BLOCK_PROMPT, parse_mode="HTML")
            return None

        return None


def _drop_expired_events(
    events: deque[float],
    now: float,
    window_seconds: int,
) -> None:
    while events and now - events[0] > window_seconds:
        events.popleft()


def _is_short_noise_text(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False

    if normalized.startswith("/"):
        return False

    if normalized in ALLOWED_SHORT_TEXTS:
        return False

    compact = normalized.replace(" ", "")
    if compact.isdigit() and len(compact) <= 8:
        return True

    tokens = normalized.split()
    if len(tokens) >= 2 and all(len(token) == 1 and token.isalnum() for token in tokens):
        return True

    if len(compact) <= 3 and compact.isalpha():
        return True

    if len(set(compact)) == 1 and len(compact) <= 10 and compact.isalnum():
        return True

    return False


def _is_expected_numeric_reply(event: Message) -> bool:
    if not event.from_user:
        return False

    text = (event.text or "").strip()
    if not text.isdigit():
        return False

    messages = dialog_memory.get_messages(event.from_user.id)
    for item in reversed(messages):
        if item.get("role") != "assistant":
            continue

        last_answer = item.get("content", "").lower()
        return (
            "цифру заявки" in last_answer
            or "цифру из списка заявок" in last_answer
            or "напишите только цифру" in last_answer
        )

    return False
