import asyncio
import logging
import os
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import MenuButtonWebApp, WebAppInfo, MenuButtonDefault

from database import init_db
from agents_config import seed_default_agents
import dynamic_loader
import coordinator
import commands
import webapp_server
import config as cfg
from config import MANAGER_TOKEN, GROUP_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    dp = Dispatcher(storage=MemoryStorage())

    # Инициализируем БД и заполняем агентами по умолчанию
    await init_db()
    await seed_default_agents()

    # Команды регистрируем первыми — они имеют приоритет над общим хендлером
    commands.setup(dp)
    coordinator.setup(dp)

    # Устанавливаем Dispatcher для динамического загрузчика
    dynamic_loader.set_dispatcher(dp)

    # Загружаем всех активных агентов из БД и запускаем их polling
    from database import get_all_active_agents
    active_agents = await get_all_active_agents()

    for agent in active_agents:
        slug = agent["slug"]
        token = agent["token"]

        if not token:
            logging.getLogger(__name__).warning(
                "Agent '%s' has no token, skipping", slug
            )
            continue

        # Только manager получает апдейты через dp.start_polling.
        # Остальные боты — только Bot-инстансы для отправки сообщений.
        await dynamic_loader.add_bot(slug, token, start_polling=False)

    manager_bot = dynamic_loader.get_bot("manager")
    if not manager_bot:
        raise RuntimeError("Manager bot not found in registry")

    # Mini App: HTTP-сервер + MenuButton на менеджере
    webapp_runner = await webapp_server.start_in_background(cfg.WEBAPP_PORT)
    if cfg.WEBAPP_URL:
        try:
            await manager_bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="📊 Панель",
                    web_app=WebAppInfo(url=cfg.WEBAPP_URL),
                )
            )
            logging.getLogger(__name__).info("MenuButton set to %s", cfg.WEBAPP_URL)
        except Exception as exc:
            logging.getLogger(__name__).warning("set_chat_menu_button failed: %s", exc)

    try:
        await dp.start_polling(
            manager_bot,
            handle_signals=True,
            allowed_updates=["message", "edited_message", "callback_query", "managed_bot"],
        )
    finally:
        try:
            await webapp_runner.cleanup()
        except Exception:
            pass
        await dynamic_loader.close_all()


if __name__ == "__main__":
    asyncio.run(main())
