import asyncio
import logging
from typing import Dict

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

import config
import database
import dynamic_loader
import agents as agents_module
from utils import random_suffix, extract_json

logger = logging.getLogger(__name__)

router = Router()

_user_styles: Dict[int, str] = {}
VALID_STYLES = ["кратко", "подробно", "для ребёнка", "для эксперта"]


class AddAgentStates(StatesGroup):
    awaiting_description = State()
    awaiting_token = State()


def setup(dp: Dispatcher) -> None:
    dp.include_router(router)


def _manager():
    return dynamic_loader.get_bot("manager")


# ──────────────────────────────────────────────
# Информационные команды
# ──────────────────────────────────────────────

@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("start", "help"))
async def cmd_start(message: Message) -> None:
    bot = _manager()
    if not bot:
        return
    text = (
        "👋 <b>TeleFlow v2.0</b>\n\n"
        "Пишите задачу — распределю по агентам.\n\n"
        "<b>Команды:</b>\n"
        "• /status — статус и агенты\n"
        "• /agents — список агентов\n"
        "• /history — последние задачи\n"
        "• /style [кратко|подробно|для ребёнка|для эксперта]\n"
        "• /clear — сбросить контекст\n"
        "• /addagent — добавить агента\n"
        "• /removeagent [slug] — удалить агента\n"
        "• /editagent [slug] — изменить промпт"
    )
    await bot.send_message(config.GROUP_CHAT_ID, text, parse_mode="HTML")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("status"))
async def cmd_status(message: Message) -> None:
    bot = _manager()
    if not bot:
        return
    try:
        count = await database.count_tasks()
        agents = await database.get_all_active_agents()
        lines = "\n".join(f"• {a['name']} (<code>{a['slug']}</code>)" for a in agents)
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"📊 <b>TeleFlow</b>\n\nАгентов: {len(agents)}\nЗадач: {count}\n\n{lines}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ {exc}")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("agents"))
async def cmd_agents(message: Message) -> None:
    bot = _manager()
    if not bot:
        return
    try:
        agents = await database.get_all_active_agents()
        if not agents:
            await bot.send_message(config.GROUP_CHAT_ID, "Нет активных агентов.")
            return
        lines = [
            f"<b>{a['name']}</b> (<code>{a['slug']}</code>)\n<i>{a.get('description','')}</i>"
            for a in agents
        ]
        await bot.send_message(
            config.GROUP_CHAT_ID,
            "🤖 <b>Агенты:</b>\n\n" + "\n\n".join(lines),
            parse_mode="HTML",
        )
    except Exception as exc:
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ {exc}")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("history"))
async def cmd_history(message: Message) -> None:
    bot = _manager()
    if not bot:
        return
    try:
        tasks = await database.get_recent_tasks(message.from_user.id, limit=5)
        if not tasks:
            await bot.send_message(config.GROUP_CHAT_ID, "📋 Задач пока нет.")
            return
        emoji = {"pending": "⏳", "done": "✅", "error": "❌"}
        lines = []
        for t in tasks:
            preview = t["task_text"][:60] + ("..." if len(t["task_text"]) > 60 else "")
            lines.append(f"{emoji.get(t['status'],'•')} <b>#{t['id']}</b>: {preview}")
        await bot.send_message(
            config.GROUP_CHAT_ID,
            "📋 <b>История:</b>\n\n" + "\n".join(lines),
            parse_mode="HTML",
        )
    except Exception as exc:
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ {exc}")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("clear"))
async def cmd_clear(message: Message, state: FSMContext) -> None:
    bot = _manager()
    if not bot:
        return
    _user_styles.pop(message.from_user.id, None)
    await state.clear()
    await bot.send_message(config.GROUP_CHAT_ID, "🗑 Контекст сброшен.")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("style"))
async def cmd_style(message: Message) -> None:
    bot = _manager()
    if not bot:
        return
    parts = message.text.split(maxsplit=1)
    user_id = message.from_user.id
    if len(parts) < 2:
        current = _user_styles.get(user_id, "не задан")
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"🎨 Стиль: <b>{current}</b>\nДоступные: {', '.join(VALID_STYLES)}",
            parse_mode="HTML",
        )
        return
    style = parts[1].strip().lower()
    if style not in VALID_STYLES:
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ Доступные стили: {', '.join(VALID_STYLES)}")
        return
    _user_styles[user_id] = style
    await bot.send_message(config.GROUP_CHAT_ID, f"✅ Стиль: <b>{style}</b>", parse_mode="HTML")


# ──────────────────────────────────────────────
# /addagent — FSM
# ──────────────────────────────────────────────

@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("addagent"))
async def cmd_addagent(message: Message, state: FSMContext) -> None:
    bot = _manager()
    if not bot:
        return

    # Сценарий А: проверяем свежее предложение из pending_agents (не старше 30 мин)
    pending = await database.get_fresh_pending_agent()
    if pending:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"💡 Вижу, что уже предложил агента — <b>{pending['name']}</b>.\n"
            f"<i>{pending['description']}</i>\n\nГенерирую спецификацию...",
            parse_mode="HTML",
        )
        await _generate_and_show_spec(
            pending["description"],
            state,
            bot,
            prefill_name=pending["name"],
            prefill_system_prompt=pending.get("system_prompt", ""),
            prefill_capabilities=pending.get("capabilities", "text"),
            pending_id=pending["id"],
        )
    else:
        # Сценарий Б: спрашиваем описание
        await state.set_state(AddAgentStates.awaiting_description)
        await bot.send_message(
            config.GROUP_CHAT_ID,
            "🆕 <b>Новый агент</b>\n\nОпиши что он должен делать.",
            parse_mode="HTML",
        )


@router.message(
    F.chat.id == config.GROUP_CHAT_ID,
    StateFilter(AddAgentStates.awaiting_description),
)
async def handle_agent_description(message: Message, state: FSMContext) -> None:
    bot = _manager()
    if not bot:
        return
    if not message.text or not message.text.strip():
        await bot.send_message(config.GROUP_CHAT_ID, "❌ Текст не может быть пустым.")
        return

    data = await state.get_data()
    editing_slug = data.get("editing_slug")

    if editing_slug:
        # Режим редактирования — обновляем промпт существующего агента
        try:
            await database.update_agent_prompt(editing_slug, message.text.strip())
            await state.clear()
            await bot.send_message(
                config.GROUP_CHAT_ID,
                f"✅ Промпт агента <code>{editing_slug}</code> обновлён.",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error("Edit agent prompt failed for '%s': %s", editing_slug, exc)
            await state.clear()
            await bot.send_message(config.GROUP_CHAT_ID, f"❌ Ошибка: {exc}")
    else:
        # Режим добавления — генерируем спецификацию нового агента
        await bot.send_message(config.GROUP_CHAT_ID, "⚙️ Генерирую спецификацию...")
        await _generate_and_show_spec(message.text, state, bot)


@router.message(
    F.chat.id == config.GROUP_CHAT_ID,
    StateFilter(AddAgentStates.awaiting_token),
)
async def handle_agent_token(message: Message, state: FSMContext) -> None:
    bot = _manager()
    if not bot:
        return

    token_value = (message.text or "").strip()
    if not token_value:
        await bot.send_message(config.GROUP_CHAT_ID, "❌ Токен не может быть пустым.")
        return

    data = await state.get_data()
    spec = data.get("spec", {})
    slug = data.get("slug", "")

    try:
        from aiogram import Bot as AiogramBot
        test_bot = AiogramBot(token=token_value)
        me = await test_bot.get_me()
        await test_bot.session.close()

        await database.upsert_agent(
            slug=slug,
            name=spec.get("name", f"TeleFlow {slug.capitalize()}"),
            token=token_value,
            system_prompt=spec.get("system_prompt", ""),
            description=spec.get("description", ""),
            capabilities=spec.get("capabilities", "text"),
            username=me.username or "",
        )

        await dynamic_loader.add_bot(slug, token_value, start_polling=False)

        new_bot = dynamic_loader.get_bot(slug)
        if new_bot:
            await new_bot.send_message(
                config.GROUP_CHAT_ID,
                f"👋 Привет! Я — <b>{spec.get('name', slug)}</b>. Готов к работе!",
                parse_mode="HTML",
            )

        # Удаляем использованное предложение из pending_agents
        pending_id = data.get("pending_id", 0)
        if pending_id:
            await database.delete_pending_agent(pending_id)

        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ Агент <code>{slug}</code> (@{me.username}) подключён!",
            parse_mode="HTML",
        )
        await state.clear()

    except Exception as exc:
        logger.error("Token registration failed: %s", exc)
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ Неверный токен или ошибка: {exc}")


async def _generate_and_show_spec(
    description: str,
    state: FSMContext,
    bot,
    prefill_name: str = "",
    prefill_system_prompt: str = "",
    prefill_capabilities: str = "text",
    pending_id: int = 0,
) -> None:
    """Генерирует спецификацию агента через Claude и показывает инструкцию для BotFather.

    Если переданы prefill_* — используем их как базу, Claude только дополняет недостающее.
    pending_id — id записи pending_agents, которую нужно удалить после генерации.
    """
    try:
        suffix = random_suffix(3)

        if prefill_name and prefill_system_prompt:
            # Сценарий А: у нас уже есть данные от менеджера — генерируем только username
            name = prefill_name
            system_prompt = prefill_system_prompt
            capabilities = prefill_capabilities
            # Генерируем username из имени
            base = name.lower().replace(" ", "_").replace("teleflow_", "")[:20]
            username = f"teleflow_{base}_{suffix}_bot"
            spec = {
                "name": name,
                "recommended_username": username,
                "system_prompt": system_prompt,
                "description": description,
                "capabilities": capabilities,
            }
        else:
            # Сценарий Б: генерируем полную спецификацию через Claude
            prompt = (
                f"Сгенерируй спецификацию Telegram-бота на основе описания:\n\n{description}\n\n"
                f"Верни ТОЛЬКО JSON:\n"
                f'{{"name": "Имя агента", "recommended_username": "teleflow_name_{suffix}_bot", '
                f'"system_prompt": "Системный промпт на русском", '
                f'"description": "Краткое описание", "capabilities": "text"}}\n\n'
                f"Username: латиница, содержит суффикс '_{suffix}_bot'."
            )
            raw = await agents_module.call_agent(
                system_prompt="Ты — архитектор AI-агентов. Отвечай только JSON.",
                user_content=prompt,
            )
            spec = extract_json(raw)
            if not spec:
                raise ValueError("Не удалось разобрать JSON спецификации")
            name = spec.get("name", "Новый агент")
            username = spec.get("recommended_username", f"teleflow_agent_{suffix}_bot")

        # Нормализуем username — должен заканчиваться на _bot
        if not username.endswith("_bot") and not username.endswith("bot"):
            username = username + "_bot"
        slug = username.replace("_bot", "").replace("teleflow_", "").strip("_").lower()
        if not slug:
            slug = f"agent_{suffix}"

        # Сохраняем черновик в agents_registry
        await database.upsert_agent(
            slug=slug,
            name=spec["name"],
            token="",
            system_prompt=spec.get("system_prompt", ""),
            description=spec.get("description", description),
            capabilities=spec.get("capabilities", "text"),
            username=username,
        )

        # Автоматически привязываем инструменты по ключевым словам описания
        from tools import is_available
        saved_agent = await database.get_agent_by_slug(slug)
        missing_tools = []
        if saved_agent:
            tool_keywords = {
                "web_search": ["ищет", "поиск", "search", "интернет", "актуальн", "новост"],
                "image_generation": ["генерирует", "рисует", "картинк", "изображен", "image", "draw", "визуал"],
            }
            desc_lower = description.lower()
            for tool_name, keywords in tool_keywords.items():
                if any(kw in desc_lower for kw in keywords):
                    if is_available(tool_name):
                        await database.assign_tool_to_agent(saved_agent["id"], tool_name)
                    else:
                        missing_tools.append(tool_name)

        # Переходим в состояние ожидания токена
        await state.set_state(AddAgentStates.awaiting_token)
        await state.update_data(spec=spec, slug=slug, pending_id=pending_id)

        missing_note = ""
        if missing_tools:
            missing_note = f"\n\n⚠️ Нет API ключей для: <code>{', '.join(missing_tools)}</code>"

        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ <b>Спецификация готова!</b>\n\n"
            f"<b>Имя:</b> {spec['name']}\n"
            f"<b>Описание:</b> {spec.get('description', '')}\n\n"
            f"<b>Создай бота в @BotFather:</b>\n"
            f"1. Отправь /newbot\n"
            f"2. Имя: <code>{spec['name']}</code>\n"
            f"3. Username: <code>{username}</code>\n"
            f"4. Добавь @{username} в эту группу\n"
            f"5. Дай ему права администратора\n"
            f"6. Скопируй токен и отправь его сюда"
            f"{missing_note}",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("Agent spec generation failed: %s", exc)
        await state.clear()
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ Ошибка генерации: {exc}")


# ──────────────────────────────────────────────
# /removeagent и /editagent
# ──────────────────────────────────────────────

@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("removeagent"))
async def cmd_removeagent(message: Message) -> None:
    bot = _manager()
    if not bot:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(config.GROUP_CHAT_ID, "❌ Синтаксис: /removeagent [slug]")
        return
    slug = parts[1].strip().lower()
    if slug == "manager":
        await bot.send_message(config.GROUP_CHAT_ID, "❌ Нельзя удалить менеджера.")
        return
    try:
        await database.deactivate_agent(slug)
        await dynamic_loader.remove_bot(slug)
        await bot.send_message(config.GROUP_CHAT_ID, f"✅ Агент <code>{slug}</code> удалён.", parse_mode="HTML")
    except Exception as exc:
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ {exc}")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("editagent"))
async def cmd_editagent(message: Message, state: FSMContext) -> None:
    bot = _manager()
    if not bot:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(config.GROUP_CHAT_ID, "❌ Синтаксис: /editagent [slug]")
        return
    slug = parts[1].strip().lower()
    existing = await database.get_agent_by_slug(slug)
    if not existing:
        await bot.send_message(config.GROUP_CHAT_ID, f"❌ Агент <code>{slug}</code> не найден.", parse_mode="HTML")
        return
    await state.set_state(AddAgentStates.awaiting_description)
    await state.update_data(editing_slug=slug)
    current = (existing.get("system_prompt") or "")[:200]
    await bot.send_message(
        config.GROUP_CHAT_ID,
        f"✏️ Редактирование <code>{slug}</code>\n\n"
        f"Текущий промпт:\n<i>{current}…</i>\n\n"
        f"Введи новый системный промпт:",
        parse_mode="HTML",
    )
