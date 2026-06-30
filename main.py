import asyncio
import logging

from aiogram import Bot, Dispatcher

from src.config import settings
from src.handlers import router
from src.middlewares.rate_limiter import RateLimiterMiddleware


async def main() -> None:
    logging.basicConfig(
        filename=settings.bot_log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.message.middleware(
        RateLimiterMiddleware(
            limit=settings.rate_limit_messages,
            window_seconds=settings.rate_limit_window_seconds,
        )
    )
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
