import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ChatAction

from config import (
    MANAGER_TOKEN,
    RESEARCHER_TOKEN,
    ANALYST_TOKEN,
    GROUP_CHAT_ID,
)
from agents import ask_researcher, ask_analyst, ask_manager

logger = logging.getLogger(__name__)

manager_bot = Bot(token=MANAGER_TOKEN, default=None)
researcher_bot = Bot(token=RESEARCHER_TOKEN, default=None)
analyst_bot = Bot(token=ANALYST_TOKEN, default=None)

dp = Dispatcher()

# IDs ботов — заполняются при старте, чтобы фильтровать их собственные сообщения
_bot_ids: set[int] = set()


async def _typing_while(bot: Bot, chat_id: int, coro):
    """Непрерывно показывает 'печатает' пока выполняется coro."""
    async def keep_typing():
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        result = await coro
    finally:
        typing_task.cancel()
    return result


async def run_pipeline(task: str) -> None:
    chat_id = GROUP_CHAT_ID

    # Шаг 1: менеджер принимает задачу
    await manager_bot.send_message(chat_id, "📋 Принял задачу! Передаю исследователю...")

    # Шаг 2: исследователь работает
    try:
        research = await _typing_while(researcher_bot, chat_id, ask_researcher(task))
    except Exception as exc:
        logger.error("Researcher API error: %s", exc)
        await manager_bot.send_message(chat_id, f"❌ Ошибка на этапе исследования: {exc}")
        return
    await researcher_bot.send_message(chat_id, research)

    # Шаг 3: аналитик работает
    try:
        analysis = await _typing_while(analyst_bot, chat_id, ask_analyst(research))
    except Exception as exc:
        logger.error("Analyst API error: %s", exc)
        await manager_bot.send_message(chat_id, f"❌ Ошибка на этапе анализа: {exc}")
        return
    await analyst_bot.send_message(chat_id, analysis)

    # Шаг 4: менеджер формирует финальный итог
    try:
        summary = await _typing_while(manager_bot, chat_id, ask_manager(analysis))
    except Exception as exc:
        logger.error("Manager API error: %s", exc)
        await manager_bot.send_message(chat_id, f"❌ Ошибка на этапе подведения итогов: {exc}")
        return
    await manager_bot.send_message(chat_id, f"✅ Итоговый брифинг:\n\n{summary}")


@dp.message(F.chat.id == GROUP_CHAT_ID)
async def on_group_message(message: Message) -> None:
    # Игнорируем сообщения от самих ботов
    if message.from_user and message.from_user.id in _bot_ids:
        return
    # Игнорируем пустые сообщения
    if not message.text:
        return

    asyncio.create_task(run_pipeline(message.text))


async def start() -> None:
    # Получаем ID всех ботов, чтобы не реагировать на их сообщения
    for bot in (manager_bot, researcher_bot, analyst_bot):
        info = await bot.get_me()
        _bot_ids.add(info.id)
        logger.info("Bot ready: @%s (id=%d)", info.username, info.id)

    # Запускаем polling только через manager_bot — он слушает группу
    await dp.start_polling(manager_bot, handle_signals=False)
