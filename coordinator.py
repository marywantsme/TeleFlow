import asyncio
import logging

from aiogram import Dispatcher, F
from aiogram.filters import StateFilter
from aiogram.types import BufferedInputFile, Message

import config
import database
import dynamic_loader
import agents as agents_module
import media
from tools import run_tool, is_available
from utils import typing_while

logger = logging.getLogger(__name__)

# Эмодзи для агентов по slug
AGENT_EMOJI = {
    "researcher": "🔍",
    "analyst": "📊",
    "manager": "📋",
}


def setup(dp: Dispatcher) -> None:
    """Регистрирует основной обработчик сообщений."""
    # Фильтр исключает команды — они обрабатываются в commands.py
    # ~F.text.startswith("/") пропускает None (фото, голосовые) как True
    dp.message.register(
        on_message,
        F.chat.id == config.GROUP_CHAT_ID,
        # Команды исключаем только если text не None, иначе пропускаем фото/голос/документы
        F.text.is_(None) | ~F.text.startswith("/"),
        StateFilter(None),  # Не запускать пайплайн если у пользователя активен FSM-диалог
    )


async def run_pipeline(message: Message, task: str, image_b64: str = None) -> None:
    """
    Основной pipeline обработки задачи:
    маршрутизация → выполнение агентами → финальный брифинг.
    """
    user_id = message.from_user.id
    manager_bot = dynamic_loader.get_bot("manager")
    chat_id = config.GROUP_CHAT_ID

    if not manager_bot:
        logger.error("Manager bot not found")
        return

    # Создаём задачу в БД
    task_id = await database.create_task(user_id, task)

    try:
        # Получаем историю задач для контекста
        history = await database.get_recent_tasks(user_id, 5)
        if history:
            history_lines = []
            for t in history:
                preview = t["task_text"][:60] + ("..." if len(t["task_text"]) > 60 else "")
                history_lines.append(f"- [{t['status']}] {preview}")
            history_text = "\n".join(history_lines)
        else:
            history_text = "История пуста."

        # Получаем список активных агентов
        active_agents = await database.get_all_active_agents()
        if active_agents:
            agents_description = "\n".join(
                f"- {a['name']} (slug: {a['slug']}): {a.get('description', '')}"
                for a in active_agents
                if a["slug"] != "manager"
            )
        else:
            agents_description = "Нет доступных агентов."

        # Маршрутизируем задачу
        route_result = await agents_module.route_task(task, agents_description, history_text)
        route = route_result.get("route", "chain")
        agent_slugs = route_result.get("agents", ["researcher", "analyst"])

        logger.info("Task #%d routed: %s → %s", task_id, route, agent_slugs)

        # Обрабатываем маршрут
        if route == "direct":
            answer = route_result.get("answer", "")
            if answer:
                await manager_bot.send_message(chat_id, answer, parse_mode="HTML")
                await database.save_agent_message(task_id, "manager", "assistant", answer)
            await database.update_task_status(task_id, "done")
            return

        if route == "suggest_agent":
            suggestion = route_result.get("description", "")
            # Сохраняем предложение в БД — /addagent подхватит его автоматически
            await database.save_pending_agent(
                name=route_result.get("name", "Новый агент"),
                description=suggestion,
                system_prompt=route_result.get("system_prompt", ""),
                capabilities=route_result.get("capabilities", "text"),
                original_task=task,  # исходный запрос пользователя
            )
            text = (
                f"💡 Для этой задачи нужен новый агент!\n\n"
                f"<i>{suggestion}</i>\n\n"
                f"Используй /addagent чтобы создать его."
            )
            await manager_bot.send_message(chat_id, text, parse_mode="HTML")
            await database.update_task_status(task_id, "done")
            return

        # route == "chain" или "single"
        # Сообщаем о начале обработки
        first_slug = agent_slugs[0] if agent_slugs else "агенту"
        first_agent_data = await database.get_agent_by_slug(first_slug)
        first_agent_name = first_agent_data["name"] if first_agent_data else first_slug
        await manager_bot.send_message(
            chat_id,
            f"📋 Принял задачу! Передаю {first_agent_name}...",
        )

        task_for_agent = task

        for slug in agent_slugs:
            agent_bot = dynamic_loader.get_bot(slug)
            if not agent_bot:
                logger.warning("Bot for agent '%s' not found, skipping", slug)
                continue

            db_agent = await database.get_agent_by_slug(slug)

            # Получаем инструменты агента из БД
            agent_id = db_agent["id"] if db_agent else None
            agent_tool_list = await database.get_agent_tools(agent_id) if agent_id else []
            agent_tool_names = [t["name"] for t in agent_tool_list]

            web_results = None
            if "web_search" in agent_tool_names and is_available("web_search"):
                result_dict = await run_tool("web_search", query=task_for_agent)
                web_results = result_dict.get("data")

            # Сохраняем пользовательский запрос как входящий контекст агента
            await database.save_agent_message(task_id, slug, "user", task_for_agent)

            has_image_tool = "image_generation" in agent_tool_names and is_available("image_generation")
            gen_keywords = ["нарисуй", "сгенерируй", "создай изображение", "generate image", "draw", "картинку", "нарисовать"]

            if has_image_tool and any(kw in task.lower() for kw in gen_keywords):
                # Агент с image_generation — Claude генерирует промпт, мы отправляем только фото
                logger.info("Task #%d: step %s START (image_generation)", task_id, slug)
                try:
                    # Claude возвращает только промпт для DALL-E — без вопросов и пояснений
                    image_prompt = await typing_while(
                        agent_bot,
                        chat_id,
                        agents_module.run_agent_by_slug(
                            slug=slug,
                            task=task_for_agent,
                            db_agent=db_agent,
                        ),
                    )
                    logger.info("Task #%d: step %s END (got prompt)", task_id, slug)
                    await agent_bot.send_message(chat_id, "🎨 Генерирую изображение...")
                    img_result = await run_tool("image_generation", prompt=image_prompt)
                    if img_result.get("type") == "photo" and img_result.get("data"):
                        photo = BufferedInputFile(file=img_result["data"], filename="generated.png")
                        await agent_bot.send_photo(chat_id, photo=photo)
                        await database.save_agent_message(task_id, slug, "assistant", image_prompt)
                        task_for_agent = image_prompt
                    else:
                        # Инструмент вернул ошибку — отправляем текст
                        err = img_result.get("data", "Ошибка генерации")
                        await agent_bot.send_message(chat_id, str(err))
                except Exception as exc:
                    logger.error("Task #%d: agent '%s' image error: %s", task_id, slug, exc)
                    await manager_bot.send_message(chat_id, f"❌ Ошибка генерации: {exc}")
                    await database.update_task_status(task_id, "error")
                    return
            else:
                # Обычный текстовый агент
                logger.info("Task #%d: step %s START", task_id, slug)
                try:
                    result = await typing_while(
                        agent_bot,
                        chat_id,
                        agents_module.run_agent_by_slug(
                            slug=slug,
                            task=task_for_agent,
                            db_agent=db_agent,
                            image_b64=image_b64,
                            web_results=web_results,
                        ),
                    )
                except Exception as exc:
                    logger.error("Task #%d: agent '%s' error: %s", task_id, slug, exc)
                    await manager_bot.send_message(chat_id, f"❌ Ошибка агента {slug}: {exc}")
                    await database.update_task_status(task_id, "error")
                    return

                logger.info("Task #%d: step %s END", task_id, slug)
                await database.save_agent_message(task_id, slug, "assistant", result)
                emoji = AGENT_EMOJI.get(slug, "🤖")
                try:
                    await agent_bot.send_message(chat_id, f"{emoji} {result}", parse_mode="HTML")
                except Exception:
                    await agent_bot.send_message(chat_id, f"{emoji} {result}")
                task_for_agent = result

            await asyncio.sleep(1.5)

        # Финальный брифинг менеджера (только для цепочки)
        if route == "chain" and len(agent_slugs) > 1:
            try:
                summary = await typing_while(
                    manager_bot,
                    chat_id,
                    agents_module.run_manager_final(task_for_agent),
                )
                await database.save_agent_message(task_id, "manager", "assistant", summary)
                try:
                    await manager_bot.send_message(
                        chat_id,
                        f"✅ Итоговый брифинг:\n\n{summary}",
                        parse_mode="HTML",
                    )
                except Exception:
                    await manager_bot.send_message(
                        chat_id,
                        f"✅ Итоговый брифинг:\n\n{summary}",
                    )
            except Exception as exc:
                logger.error("Manager final summary error: %s", exc)
                await manager_bot.send_message(chat_id, f"❌ Ошибка финального брифинга: {exc}")

        await database.update_task_status(task_id, "done")

    except Exception as exc:
        logger.error("Pipeline error for task #%d: %s", task_id, exc)
        await database.update_task_status(task_id, "error")
        try:
            await manager_bot.send_message(chat_id, f"❌ Ошибка обработки задачи: {exc}")
        except Exception:
            pass


async def handle_direct_mention(slug: str, message: Message) -> None:
    """
    Обрабатывает прямое обращение к конкретному агенту.
    Нет финального брифинга менеджера.
    """
    agent_bot = dynamic_loader.get_bot(slug)
    if not agent_bot:
        logger.warning("Direct mention: bot for '%s' not found", slug)
        return

    db_agent = await database.get_agent_by_slug(slug)
    task = message.text or message.caption or "Что сделать?"
    user_id = message.from_user.id if message.from_user else 0

    # Создаём полноценную задачу в БД — не task_id=0
    task_id = await database.create_task(user_id, task)
    await database.save_agent_message(task_id, slug, "user", task)

    try:
        result = await typing_while(
            agent_bot,
            config.GROUP_CHAT_ID,
            agents_module.run_agent_by_slug(
                slug=slug,
                task=task,
                db_agent=db_agent,
            ),
        )
        await database.save_agent_message(task_id, slug, "assistant", result)
        await database.update_task_status(task_id, "done")
        emoji = AGENT_EMOJI.get(slug, "🤖")
        try:
            await agent_bot.send_message(
                config.GROUP_CHAT_ID,
                f"{emoji} {result}",
                parse_mode="HTML",
            )
        except Exception:
            await agent_bot.send_message(config.GROUP_CHAT_ID, f"{emoji} {result}")
    except Exception as exc:
        logger.error("Direct mention error for agent '%s': %s", slug, exc)
        await database.update_task_status(task_id, "error")
        manager_bot = dynamic_loader.get_bot("manager")
        if manager_bot:
            await manager_bot.send_message(
                config.GROUP_CHAT_ID,
                f"❌ Ошибка агента {slug}: {exc}",
            )


async def on_message(message: Message) -> None:
    """
    Главный обработчик входящих сообщений в группе.
    Маршрутизирует между прямым обращением к агенту и общим pipeline.
    """
    # Пропускаем service messages (join, leave, pin и тд) — у них нет from_user или нет контента
    if not message.from_user:
        return

    # Пропускаем сообщения от самих ботов
    if message.from_user.id in dynamic_loader.get_all_bot_ids():
        return

    # Пропускаем если нет полезного контента (service messages, stickers, etc.)
    has_content = (
        message.text
        or message.photo
        or message.voice
        or message.document
    )
    if not has_content:
        return

    # Пропускаем команды (обрабатываются в commands.py)
    if message.text and message.text.startswith("/"):
        return

    # Проверяем, упомянут ли какой-то не-менеджер бот через @username
    mentioned_slug = None
    if message.text or message.caption:
        text_to_check = (message.text or message.caption or "").lower()
        for slug in dynamic_loader.get_all_bots():
            if slug == "manager":
                continue
            username = dynamic_loader.get_username(slug)
            if username and f"@{username.lower()}" in text_to_check:
                mentioned_slug = slug
                break

    if mentioned_slug:
        asyncio.create_task(handle_direct_mention(mentioned_slug, message))
        return

    manager_bot = dynamic_loader.get_bot("manager")
    if not manager_bot:
        logger.error("Manager bot not found")
        return

    image_b64 = None
    task = ""

    # Обрабатываем голосовые сообщения
    if message.voice:
        text = await media.voice_to_text(manager_bot, message.voice)
        if text is None:
            await manager_bot.send_message(
                config.GROUP_CHAT_ID,
                "❌ Не удалось распознать голосовое сообщение. Проверьте OPENAI_API_KEY.",
            )
            return
        task = text
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"🎤 Распознал: {text}\nОбрабатываю...",
        )

    # Обрабатываем фото
    elif message.photo:
        image_b64 = await media.photo_to_base64(manager_bot, message.photo[-1])
        task = message.caption or "Что сделать с этим изображением?"

    # Обрабатываем документы
    elif message.document:
        text = await media.document_to_text(manager_bot, message.document)
        if text is None:
            await manager_bot.send_message(
                config.GROUP_CHAT_ID,
                "❌ Не могу прочитать этот файл. Поддерживаются только текстовые файлы.",
            )
            return
        task = text

    # Обычный текст
    elif message.text:
        task = message.text

    if not task:
        return

    asyncio.create_task(run_pipeline(message, task, image_b64))


async def run_pipeline_from_task(user_id: int, task: str) -> None:
    """Запускает пайплайн напрямую по тексту задачи (без объекта Message)."""

    class _FakeUser:
        id = user_id

    class _FakeMessage:
        from_user = _FakeUser()

    await run_pipeline(_FakeMessage(), task)
