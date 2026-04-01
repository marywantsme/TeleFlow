import asyncio
import logging
from typing import Dict, Optional, Set

from aiogram import Bot, Dispatcher

logger = logging.getLogger(__name__)

# Состояние модуля
_active_bots: Dict[str, Bot] = {}           # slug → Bot
_bot_user_ids: Dict[str, int] = {}          # slug → telegram user id
_bot_usernames: Dict[str, str] = {}         # slug → @username (без @)
_polling_tasks: Dict[str, asyncio.Task] = {}
_dp: Optional[Dispatcher] = None


def set_dispatcher(dp: Dispatcher) -> None:
    """Устанавливает общий Dispatcher для всех ботов."""
    global _dp
    _dp = dp


def get_bot(slug: str) -> Optional[Bot]:
    """Возвращает бота по slug или None."""
    return _active_bots.get(slug)


def get_all_bots() -> Dict[str, Bot]:
    """Возвращает словарь всех активных ботов."""
    return dict(_active_bots)


def get_slug_by_bot_id(bot_id: int) -> Optional[str]:
    """Возвращает slug бота по его Telegram user id."""
    for slug, uid in _bot_user_ids.items():
        if uid == bot_id:
            return slug
    return None


def get_username(slug: str) -> Optional[str]:
    """Возвращает username бота (без @) по slug."""
    return _bot_usernames.get(slug)


def get_all_bot_ids() -> Set[int]:
    """Возвращает множество всех кешированных Telegram user ID ботов."""
    return set(_bot_user_ids.values())


async def add_bot(slug: str, token: str, start_polling: bool = True) -> Bot:
    """
    Добавляет бота в систему.
    Если start_polling=True и Dispatcher задан — запускает кастомный polling-цикл.
    """
    bot = Bot(token=token)
    _active_bots[slug] = bot

    # Получаем информацию о боте и кешируем его user id и username
    me = await bot.get_me()
    _bot_user_ids[slug] = me.id
    _bot_usernames[slug] = me.username or ""
    logger.info("Bot loaded: @%s (%s)", me.username, slug)

    # Запускаем кастомный polling если нужно
    if start_polling and _dp is not None:
        task = asyncio.create_task(_run_polling(bot, slug))
        _polling_tasks[slug] = task

    return bot


async def remove_bot(slug: str) -> None:
    """Останавливает и удаляет бота из системы."""
    # Отменяем polling-задачу
    task = _polling_tasks.pop(slug, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Закрываем сессию бота
    bot = _active_bots.pop(slug, None)
    if bot:
        try:
            await bot.session.close()
        except Exception as exc:
            logger.error("Error closing bot session for '%s': %s", slug, exc)

    # Удаляем из кеша user ids
    _bot_user_ids.pop(slug, None)
    _bot_usernames.pop(slug, None)
    logger.info("Bot removed: %s", slug)


async def _run_polling(bot: Bot, slug: str) -> None:
    """
    Кастомный polling-цикл для дополнительных ботов.
    Использует общий Dispatcher для обработки обновлений.
    """
    offset = 0
    logger.info("Custom polling started for bot: %s", slug)

    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                # Не-менеджеры получают только текстовые сообщения и фото
                allowed_updates=["message"],
            )
            for update in updates:
                offset = update.update_id + 1
                # Пропускаем голосовые — их обрабатывает только менеджер
                if update.message and update.message.voice:
                    continue
                asyncio.create_task(_dp.feed_update(bot, update))

        except asyncio.CancelledError:
            logger.info("Custom polling cancelled for bot: %s", slug)
            break
        except Exception as exc:
            logger.error("Polling error for bot '%s': %s", slug, exc)
            await asyncio.sleep(5)

    logger.info("Custom polling stopped for bot: %s", slug)


async def close_all() -> None:
    """Останавливает все polling-задачи и закрывает все сессии ботов."""
    # Отменяем все задачи
    for slug, task in list(_polling_tasks.items()):
        if not task.done():
            task.cancel()

    # Ждём завершения всех задач
    if _polling_tasks:
        await asyncio.gather(*_polling_tasks.values(), return_exceptions=True)
    _polling_tasks.clear()

    # Закрываем все сессии
    for slug, bot in list(_active_bots.items()):
        try:
            await bot.session.close()
        except Exception as exc:
            logger.error("Error closing session for bot '%s': %s", slug, exc)
    _active_bots.clear()
    _bot_user_ids.clear()

    logger.info("All bots closed")
