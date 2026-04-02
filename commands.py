import asyncio
import logging
from typing import Dict

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message

import config
import database
import dynamic_loader
import agents as agents_module
from utils import random_suffix, extract_json

logger = logging.getLogger(__name__)

# Роутер для команд
router = Router()

# Состояние стиля пользователей
_user_styles: Dict[int, str] = {}

# Состояние многошаговых диалогов (chat_id → {"step": str, ...})
_add_agent_state: Dict[int, dict] = {}

# Допустимые стили
VALID_STYLES = ["кратко", "подробно", "для ребёнка", "для эксперта"]


def setup(dp: Dispatcher) -> None:
    """Подключает роутер команд к Dispatcher."""
    dp.include_router(router)


# ──────────────────────────────────────────────
# Хелперы
# ──────────────────────────────────────────────

def _get_manager_bot():
    """Возвращает бота-менеджера."""
    return dynamic_loader.get_bot("manager")


# ──────────────────────────────────────────────
# Команды
# ──────────────────────────────────────────────

@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("start", "help"))
async def cmd_start(message: Message) -> None:
    """Показывает приветствие и список команд."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    text = (
        "👋 <b>Добро пожаловать в TeleFlow v2.0!</b>\n\n"
        "Я — менеджер AI-команды. Просто напиши задачу, и я распределю её между агентами.\n\n"
        "<b>Команды:</b>\n"
        "• /status — статус системы и агентов\n"
        "• /agents — список активных агентов\n"
        "• /history — последние задачи\n"
        "• /style [стиль] — стиль ответов (кратко/подробно/для ребёнка/для эксперта)\n"
        "• /clear — очистить контекст\n"
        "• /addagent — добавить нового агента\n"
        "• /token [slug] [username] [token] — зарегистрировать токен агента\n"
        "• /removeagent [slug] — удалить агента\n"
        "• /editagent [slug] — изменить промпт агента"
    )
    await manager_bot.send_message(config.GROUP_CHAT_ID, text, parse_mode="HTML")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("status"))
async def cmd_status(message: Message) -> None:
    """Показывает статус системы."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    try:
        task_count = await database.count_tasks()
        active_agents = await database.get_all_active_agents()

        agents_lines = "\n".join(
            f"• {a['name']} (<code>{a['slug']}</code>)" for a in active_agents
        )

        text = (
            f"📊 <b>Статус TeleFlow</b>\n\n"
            f"Активных агентов: {len(active_agents)}\n"
            f"Задач выполнено: {task_count}\n\n"
            f"<b>Агенты:</b>\n{agents_lines}"
        )
        await manager_bot.send_message(config.GROUP_CHAT_ID, text, parse_mode="HTML")
    except Exception as exc:
        logger.error("Status command error: %s", exc)
        await manager_bot.send_message(config.GROUP_CHAT_ID, f"❌ Ошибка: {exc}")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("agents"))
async def cmd_agents(message: Message) -> None:
    """Показывает список активных агентов."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    try:
        active_agents = await database.get_all_active_agents()
        if not active_agents:
            await manager_bot.send_message(config.GROUP_CHAT_ID, "Нет активных агентов.")
            return

        lines = []
        for a in active_agents:
            caps = a.get("capabilities", "text")
            lines.append(
                f"<b>{a['name']}</b> (<code>{a['slug']}</code>)\n"
                f"<i>{a.get('description', '')}</i>\n"
                f"Возможности: {caps}"
            )

        text = "🤖 <b>Активные агенты:</b>\n\n" + "\n\n".join(lines)
        await manager_bot.send_message(config.GROUP_CHAT_ID, text, parse_mode="HTML")
    except Exception as exc:
        logger.error("Agents command error: %s", exc)
        await manager_bot.send_message(config.GROUP_CHAT_ID, f"❌ Ошибка: {exc}")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("history"))
async def cmd_history(message: Message) -> None:
    """Показывает историю последних задач пользователя."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    try:
        user_id = message.from_user.id
        tasks = await database.get_recent_tasks(user_id, limit=5)

        if not tasks:
            await manager_bot.send_message(
                config.GROUP_CHAT_ID,
                "📋 У вас пока нет истории задач.",
            )
            return

        lines = []
        for t in tasks:
            status_emoji = {"pending": "⏳", "done": "✅", "error": "❌"}.get(t["status"], "•")
            task_preview = t["task_text"][:60] + ("..." if len(t["task_text"]) > 60 else "")
            summary_preview = ""
            if t.get("summary"):
                summary_preview = "\n<i>" + t["summary"][:80] + ("..." if len(t["summary"]) > 80 else "") + "</i>"
            lines.append(f"{status_emoji} <b>#{t['id']}</b>: {task_preview}{summary_preview}")

        text = "📋 <b>История задач:</b>\n\n" + "\n\n".join(lines)
        await manager_bot.send_message(config.GROUP_CHAT_ID, text, parse_mode="HTML")
    except Exception as exc:
        logger.error("History command error: %s", exc)
        await manager_bot.send_message(config.GROUP_CHAT_ID, f"❌ Ошибка: {exc}")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("clear"))
async def cmd_clear(message: Message) -> None:
    """Очищает стиль и контекст пользователя."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    user_id = message.from_user.id
    _user_styles.pop(user_id, None)
    await manager_bot.send_message(
        config.GROUP_CHAT_ID,
        "🗑 Контекст очищен. Начинаем с чистого листа.",
    )


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("style"))
async def cmd_style(message: Message) -> None:
    """Устанавливает или показывает текущий стиль ответов."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        current = _user_styles.get(user_id, "не задан")
        valid = ", ".join(VALID_STYLES)
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"🎨 Текущий стиль: <b>{current}</b>\n\nДоступные стили: {valid}\n\nПример: /style кратко",
            parse_mode="HTML",
        )
        return

    style = parts[1].strip().lower()
    if style not in VALID_STYLES:
        valid = ", ".join(VALID_STYLES)
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Неверный стиль. Доступные: {valid}",
        )
        return

    _user_styles[user_id] = style
    await manager_bot.send_message(
        config.GROUP_CHAT_ID,
        f"✅ Стиль установлен: <b>{style}</b>",
        parse_mode="HTML",
    )


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("addagent"))
async def cmd_addagent(message: Message) -> None:
    """Начинает процесс добавления нового агента."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    chat_id = message.chat.id
    _add_agent_state[chat_id] = {"step": "waiting_description", "spec": {}}

    await manager_bot.send_message(
        config.GROUP_CHAT_ID,
        "🆕 <b>Добавление нового агента</b>\n\n"
        "Опиши задачу нового агента. Что он должен уметь делать?",
        parse_mode="HTML",
    )


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("token"))
async def cmd_token(message: Message) -> None:
    """
    Регистрирует токен агента.
    Синтаксис: /token [slug] [username] [token_value]
    """
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Синтаксис: /token [slug] [username] [token_value]",
        )
        return

    _, slug, username, token_value = parts
    slug = slug.strip().lower()
    username = username.strip().lstrip("@")
    token_value = token_value.strip()

    try:
        # Проверяем токен через get_me()
        from aiogram import Bot as AiogramBot
        test_bot = AiogramBot(token=token_value)
        me = await test_bot.get_me()
        await test_bot.session.close()

        logger.info("Token validated for @%s (slug: %s)", me.username, slug)

        # Получаем текущего агента из БД если есть
        existing = await database.get_agent_by_slug(slug)
        system_prompt = existing["system_prompt"] if existing else f"Ты — агент {slug}. Отвечай на русском языке."
        description = existing["description"] if existing else f"Агент {slug}"
        capabilities = existing["capabilities"] if existing else "text"
        name = existing["name"] if existing else f"TeleFlow {slug.capitalize()}"

        # Сохраняем в БД
        await database.upsert_agent(
            slug=slug,
            name=name,
            token=token_value,
            system_prompt=system_prompt,
            description=description,
            capabilities=capabilities,
            username=username,
        )

        # Добавляем бота в динамический загрузчик
        bot = dynamic_loader.get_bot(slug)
        if bot:
            await dynamic_loader.remove_bot(slug)
        await dynamic_loader.add_bot(slug, token_value, start_polling=False)

        # Приветственное сообщение от нового бота
        new_bot = dynamic_loader.get_bot(slug)
        if new_bot:
            await new_bot.send_message(
                config.GROUP_CHAT_ID,
                f"👋 Привет! Я — <b>{name}</b>. Готов к работе!",
                parse_mode="HTML",
            )

        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ Агент <code>{slug}</code> (@{me.username}) успешно зарегистрирован!",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("Token registration failed for slug '%s': %s", slug, exc)
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Ошибка регистрации токена: {exc}",
        )


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("removeagent"))
async def cmd_removeagent(message: Message) -> None:
    """Деактивирует и удаляет агента."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Синтаксис: /removeagent [slug]",
        )
        return

    slug = parts[1].strip().lower()

    if slug == "manager":
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Нельзя удалить менеджера.",
        )
        return

    try:
        await database.deactivate_agent(slug)
        await dynamic_loader.remove_bot(slug)
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ Агент <code>{slug}</code> деактивирован.",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("Remove agent error for slug '%s': %s", slug, exc)
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Ошибка удаления агента: {exc}",
        )


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("editagent"))
async def cmd_editagent(message: Message) -> None:
    """Начинает процесс редактирования системного промпта агента."""
    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Синтаксис: /editagent [slug]",
        )
        return

    slug = parts[1].strip().lower()
    existing = await database.get_agent_by_slug(slug)
    if not existing:
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Агент <code>{slug}</code> не найден.",
            parse_mode="HTML",
        )
        return

    chat_id = message.chat.id
    _add_agent_state[chat_id] = {"step": "waiting_new_prompt", "slug": slug}

    current_prompt = existing.get("system_prompt", "")[:200]
    await manager_bot.send_message(
        config.GROUP_CHAT_ID,
        f"✏️ Редактирование агента <code>{slug}</code>\n\n"
        f"Текущий промпт (первые 200 символов):\n<i>{current_prompt}...</i>\n\n"
        f"Введи новый системный промпт:",
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
# Обработчик текстовых сообщений для многошаговых диалогов
# ──────────────────────────────────────────────

@router.message(F.chat.id == config.GROUP_CHAT_ID)
async def handle_state_messages(message: Message) -> None:
    """
    Обрабатывает текстовые сообщения в рамках активных диалогов.
    Пропускает если нет активного состояния для этого чата.
    """
    chat_id = message.chat.id

    # Если нет активного состояния — пропускаем (обработает coordinator)
    if chat_id not in _add_agent_state:
        return

    # Пропускаем команды
    if message.text and message.text.startswith("/"):
        return

    state = _add_agent_state[chat_id]
    step = state.get("step")

    manager_bot = _get_manager_bot()
    if not manager_bot:
        return

    if step == "waiting_description":
        await _handle_addagent_description(message, state, manager_bot)

    elif step == "waiting_token":
        # Ожидаем токен — это обрабатывается командой /token
        pass

    elif step == "waiting_new_prompt":
        await _handle_editagent_prompt(message, state, manager_bot)


async def _handle_addagent_description(message: Message, state: dict, manager_bot) -> None:
    """Обрабатывает описание нового агента и генерирует его спецификацию."""
    chat_id = message.chat.id
    description = message.text or ""

    if not description.strip():
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Описание не может быть пустым. Попробуй ещё раз.",
        )
        return

    await manager_bot.send_message(
        config.GROUP_CHAT_ID,
        "⚙️ Генерирую спецификацию агента...",
    )

    try:
        suffix = random_suffix(4)
        generation_prompt = (
            f"Сгенерируй спецификацию Telegram-бота агента на основе описания:\n\n"
            f"{description}\n\n"
            f"Верни ТОЛЬКО JSON без пояснений:\n"
            f'{{"name": "Имя агента", "recommended_username": "username_{suffix}bot", '
            f'"system_prompt": "Подробный системный промпт на русском языке", '
            f'"description": "Краткое описание возможностей", '
            f'"capabilities": "text"}}\n\n'
            f"username должен быть на латинице, заканчиваться на 'bot', содержать суффикс '{suffix}'."
        )

        raw = await agents_module.call_agent(
            system_prompt="Ты — архитектор AI-агентов. Генерируй чёткие спецификации.",
            user_content=generation_prompt,
        )

        spec = extract_json(raw)
        if not spec:
            raise ValueError("Failed to parse agent specification JSON")

        state["spec"] = spec
        state["step"] = "waiting_token"
        _add_agent_state[chat_id] = state

        username = spec.get("recommended_username", f"agent_{suffix}bot")
        name = spec.get("name", "Новый агент")

        instructions = (
            f"✅ <b>Спецификация агента готова!</b>\n\n"
            f"<b>Имя:</b> {name}\n"
            f"<b>Описание:</b> {spec.get('description', '')}\n\n"
            f"<b>Создай бота в @BotFather:</b>\n"
            f"1. Напиши @BotFather → /newbot\n"
            f"2. Имя: <code>{name}</code>\n"
            f"3. Username: <code>{username}</code>\n"
            f"4. Скопируй токен\n\n"
            f"Затем введи команду:\n"
            f"<code>/token {username.replace('bot', '')} {username} ТВОЙ_ТОКЕН</code>"
        )
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            instructions,
            parse_mode="HTML",
        )

        # Очищаем состояние после показа инструкций
        _add_agent_state.pop(chat_id, None)

        # Сохраняем спецификацию в БД как черновик (без токена)
        slug = username.replace("bot", "").lower().strip("_")
        await database.upsert_agent(
            slug=slug,
            name=name,
            token="",
            system_prompt=spec.get("system_prompt", ""),
            description=spec.get("description", ""),
            capabilities=spec.get("capabilities", "text"),
            username=username,
        )

    except Exception as exc:
        logger.error("Agent spec generation failed: %s", exc)
        _add_agent_state.pop(chat_id, None)
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Ошибка генерации спецификации: {exc}",
        )


async def _handle_editagent_prompt(message: Message, state: dict, manager_bot) -> None:
    """Обрабатывает новый системный промпт для редактирования агента."""
    chat_id = message.chat.id
    slug = state.get("slug", "")
    new_prompt = message.text or ""

    if not new_prompt.strip():
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Промпт не может быть пустым. Попробуй ещё раз.",
        )
        return

    try:
        await database.update_agent_prompt(slug, new_prompt)
        _add_agent_state.pop(chat_id, None)

        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ Системный промпт агента <code>{slug}</code> обновлён.",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("Edit agent prompt failed for slug '%s': %s", slug, exc)
        _add_agent_state.pop(chat_id, None)
        await manager_bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Ошибка обновления промпта: {exc}",
        )
