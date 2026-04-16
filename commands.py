import asyncio
import logging
from typing import Dict

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ContentType
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import database
import dynamic_loader
import agents as agents_module
import managed_bots
from utils import random_suffix, extract_json

logger = logging.getLogger(__name__)

router = Router()

_user_styles: Dict[int, str] = {}
VALID_STYLES = ["кратко", "подробно", "для ребёнка", "для эксперта"]


class AddAgentStates(StatesGroup):
    awaiting_description = State()
    awaiting_token = State()


_dp_ref: Dispatcher | None = None


def setup(dp: Dispatcher) -> None:
    global _dp_ref
    _dp_ref = dp
    dp.include_router(router)
    dp.update.outer_middleware(_managed_bot_middleware)


async def _managed_bot_middleware(handler, event, data):
    """Перехватывает updates с полем managed_bot (Bot API 9.6)."""
    try:
        extra = getattr(event, "model_extra", None) or {}
        mb = extra.get("managed_bot")
        if mb:
            await _handle_managed_bot_update(mb)
            return
    except Exception as exc:
        logger.exception("managed_bot middleware error: %s", exc)
    return await handler(event, data)


async def _handle_managed_bot_update(mb_update: dict) -> None:
    """
    Обрабатывает managed_bot update: получает токен, активирует агента,
    запускает его polling и отправляет уведомление в группу.
    """
    bot = _manager()
    if not bot:
        return

    bot_info = mb_update.get("bot") or {}
    bot_user_id = bot_info.get("id")
    bot_username = (bot_info.get("username") or "").lstrip("@")
    bot_name = bot_info.get("first_name") or ""

    if not bot_user_id:
        logger.error("managed_bot update without bot.id: %s", mb_update)
        return

    try:
        token = await managed_bots.get_managed_bot_token(config.MANAGER_TOKEN, bot_user_id)
    except Exception as exc:
        logger.error("getManagedBotToken failed for %s: %s", bot_user_id, exc)
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Получил managed-бота @{bot_username}, но не смог запросить токен: {exc}",
        )
        return

    # Ищем черновик агента в реестре (был создан с пустым токеном на этапе spec)
    agent_row = await database.get_agent_by_username(bot_username)
    if not agent_row:
        # Черновика нет — создаём заново на лету
        slug = bot_username.replace("_bot", "").replace("teleflow_", "").strip("_").lower()
        if not slug:
            slug = f"agent_{bot_user_id}"
        await database.upsert_agent(
            slug=slug,
            name=bot_name or slug.capitalize(),
            token=token,
            system_prompt=f"Ты — {bot_name or slug}. Отвечай по теме.",
            description="Managed bot",
            capabilities="text",
            username=bot_username,
        )
    else:
        slug = agent_row["slug"]
        await database.update_agent_token(slug, token)

    try:
        await dynamic_loader.add_bot(slug, token, start_polling=False)
    except Exception as exc:
        logger.error("add_bot failed for %s: %s", slug, exc)

    # Чистим FSM-состояние создателя — он больше не в awaiting_token/awaiting_description,
    # иначе его следующее сообщение будет воспринято как токен.
    creator_user_id = (mb_update.get("user") or {}).get("id", 0)
    if creator_user_id and _dp_ref and bot:
        try:
            from aiogram.fsm.storage.base import StorageKey
            key = StorageKey(
                bot_id=bot.id,
                chat_id=config.GROUP_CHAT_ID,
                user_id=creator_user_id,
            )
            await _dp_ref.storage.set_state(key, state=None)
            await _dp_ref.storage.set_data(key, data={})
        except Exception as exc:
            logger.warning("FSM clear failed for user %s: %s", creator_user_id, exc)

    # Проверяем — состоит ли свежий managed-бот в рабочей группе.
    # Если нет — Telegram вернёт "chat not found" и все задачи к нему будут падать.
    new_bot = dynamic_loader.get_bot(slug)
    in_group = False
    if new_bot:
        try:
            await new_bot.send_message(
                config.GROUP_CHAT_ID,
                f"👋 Привет! Я — <b>{bot_name or slug}</b>. Готов к работе!",
                parse_mode="HTML",
            )
            in_group = True
        except Exception as exc:
            logger.info("New managed bot '%s' not in group yet: %s", slug, exc)

    if in_group:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ Managed-бот <b>{bot_name or slug}</b> (@{bot_username}) создан и подключён!",
            parse_mode="HTML",
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"➕ Добавить @{bot_username} в группу",
                url=f"https://t.me/{bot_username}?startgroup=true&admin=post_messages",
            )
        ]])
        await bot.send_message(
            config.GROUP_CHAT_ID,
            (
                f"✅ Managed-бот <b>{bot_name or slug}</b> (@{bot_username}) создан!\n\n"
                "⚠️ Осталось добавить его в эту группу — иначе он не сможет "
                "отвечать на задачи. Нажми кнопку ниже и выбери этот чат."
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )


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
        "👋 <b>TeleFlow v3.1</b>\n\n"
        "Пишите задачу — распределю по агентам.\n\n"
        "<b>Агенты:</b>\n"
        "• /status — статус и агенты\n"
        "• /agents — список агентов\n"
        "• /history — последние задачи\n"
        "• /addagent — создать агента (Managed Bot в один тап, Bot API 9.6)\n"
        "• /removeagent [slug] — удалить агента\n"
        "• /editagent [slug] — изменить промпт\n\n"
        "<b>Плагины:</b>\n"
        "• /plugins — плагины и интеграции\n"
        "• /connect [plugin] — подключить плагин\n"
        "• /disconnect [plugin] — отключить плагин\n\n"
        "<b>Панель (Mini App):</b> кнопка меню рядом с полем ввода.\n\n"
        "<b>Прочее:</b>\n"
        "• /style [кратко|подробно|для ребёнка|для эксперта]\n"
        "• /clear — сбросить контекст"
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
            original_task=pending.get("original_task", ""),
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
    # Игнорируем service messages (добавление в группу, pin и тд) — у них нет текста
    if message.content_type != "text" or not message.text:
        return

    bot = _manager()
    if not bot:
        return

    token_value = message.text.strip()
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

        # Генерируем invite link чтобы помочь добавить бота в группу
        try:
            invite = await bot.create_chat_invite_link(config.GROUP_CHAT_ID)
            invite_note = (
                f"\n\n🔗 Если бот ещё не в группе — <a href='{invite.invite_link}'>перейди по ссылке</a> "
                f"или добавь @{me.username} вручную."
            )
        except Exception:
            invite_note = f"\n\nЕсли бот ещё не в группе — добавь @{me.username} вручную и дай права администратора."

        new_bot = dynamic_loader.get_bot(slug)
        if new_bot:
            try:
                await new_bot.send_message(
                    config.GROUP_CHAT_ID,
                    f"👋 Привет! Я — <b>{spec.get('name', slug)}</b>. Готов к работе!",
                    parse_mode="HTML",
                )
                invite_note = ""  # Бот уже в группе, ссылка не нужна
            except Exception:
                pass  # Бот не в группе — invite_note останется

        # Удаляем использованное предложение из pending_agents
        pending_id = data.get("pending_id", 0)
        if pending_id:
            await database.delete_pending_agent(pending_id)

        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ Агент <code>{slug}</code> (@{me.username}) подключён!{invite_note}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await state.clear()

        # Если есть исходная задача — запускаем пайплайн автоматически
        original_task = data.get("original_task", "")
        if original_task:
            await bot.send_message(
                config.GROUP_CHAT_ID,
                "▶️ Выполняю исходную задачу...",
            )
            import coordinator as _coordinator
            asyncio.create_task(
                _coordinator.run_pipeline_from_task(
                    user_id=message.from_user.id if message.from_user else 0,
                    task=original_task,
                )
            )

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
    original_task: str = "",
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
            spec_system = "Ты — архитектор AI-агентов. Отвечай только JSON."
            raw = await agents_module.call_agent(system_prompt=spec_system, user_content=prompt)
            spec = extract_json(raw)
            if not spec:
                # Retry один раз с явным требованием чистого JSON
                raw = await agents_module.call_agent(
                    system_prompt=spec_system,
                    user_content=prompt + "\n\nВажно: ответь строго валидным JSON, без пояснений и markdown.",
                )
                spec = extract_json(raw)
            if not spec:
                await state.clear()
                await bot.send_message(
                    config.GROUP_CHAT_ID,
                    "❌ Не удалось создать спецификацию, попробуй ещё раз.",
                )
                return
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

        # Переходим в состояние ожидания токена (для fallback-пути через BotFather)
        await state.set_state(AddAgentStates.awaiting_token)
        await state.update_data(spec=spec, slug=slug, pending_id=pending_id, original_task=original_task)

        missing_note = ""
        if missing_tools:
            missing_note = f"\n\n⚠️ Нет API ключей для: <code>{', '.join(missing_tools)}</code>"

        # Managed Bots (Bot API 9.6): ссылка t.me/newbot/{manager}/{suggested}?name=…
        manager_username = dynamic_loader.get_username("manager") or "teleflow_manager_bot"
        fallback_link = managed_bots.build_fallback_link(
            manager_username=manager_username,
            suggested_username=username,
            suggested_name=spec["name"],
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🚀 Создать @{username} одним тапом",
                url=fallback_link,
            )],
        ])

        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"✅ <b>Спецификация готова!</b>\n\n"
            f"<b>Имя:</b> {spec['name']}\n"
            f"<b>Username:</b> @{username}\n"
            f"<b>Описание:</b> {spec.get('description', '')}\n\n"
            f"🆕 <b>Managed Bot (Telegram 9.6):</b>\n"
            f"Нажми кнопку ниже — Telegram сам создаст и привяжет бота, "
            f"токен подтянется автоматически.\n\n"
            f"<i>Альтернатива:</i> создай через @BotFather вручную "
            f"(/newbot → имя <code>{spec['name']}</code> → username <code>{username}</code>) "
            f"и пришли токен сюда следующим сообщением."
            f"{missing_note}",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=kb,
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


# ──────────────────────────────────────────────
# Плагины: /plugins, /connect, /apikey, /disconnect
# ──────────────────────────────────────────────

def _mask_key(key: str) -> str:
    """Маскирует API-ключ: sk-...a3Bf"""
    if len(key) <= 7:
        return "***"
    return f"{key[:3]}...{key[-4:]}"


def _parse_plugin_arg(text: str) -> str | None:
    """Извлекает имя плагина из текста команды, игнорируя @username.
    Например: '/connect@teleflow_manager_bot openrouter' → 'openrouter'
              '/connect openrouter' → 'openrouter'
    """
    parts = text.split()
    # Фильтруем части начинающиеся с '/' или '@' (сама команда)
    args = [p for p in parts[1:] if not p.startswith("@")]
    return args[0].lower() if args else None


async def _send_connect_info(bot, plugin_name: str) -> None:
    """Отправляет инструкцию по подключению плагина."""
    from plugins_registry import PLUGINS
    info = PLUGINS[plugin_name]
    text = (
        f"🔌 <b>{info['display_name']}</b>\n\n"
        f"{info['description']}\n\n"
        f"<b>Инструкция:</b>\n{info['setup_instructions']}\n\n"
        f"💰 {info['cost_info']}\n\n"
        f"Когда получишь ключ, отправь:\n"
        f"<code>/apikey {plugin_name} [твой_ключ]</code>\n\n"
        f"<i>Сообщение с ключом будет автоматически удалено.</i>"
    )
    await bot.send_message(config.GROUP_CHAT_ID, text, parse_mode="HTML")


async def _build_plugins_message() -> tuple[str, InlineKeyboardMarkup | None]:
    """Строит текст и клавиатуру для /plugins."""
    from plugins_registry import PLUGINS
    import config as _cfg

    active_names = await database.get_active_plugin_names()

    env_active = set()
    env_map = {
        "dalle": _cfg.OPENAI_API_KEY,
        "whisper": _cfg.OPENAI_API_KEY,
        "tavily": _cfg.TAVILY_API_KEY,
    }
    for pname, val in env_map.items():
        if val:
            env_active.add(pname)

    connected = set(active_names) | env_active

    connected_lines = []
    connect_buttons: list[InlineKeyboardButton] = []
    disconnect_buttons: list[InlineKeyboardButton] = []

    for name, info in PLUGINS.items():
        display = info["display_name"]
        if name in connected:
            connected_lines.append(f"• {display} ✅")
            disconnect_buttons.append(
                InlineKeyboardButton(
                    text=f"❌ {display}",
                    callback_data=f"disconnect_{name}",
                )
            )
        else:
            connect_buttons.append(
                InlineKeyboardButton(
                    text=f"🔌 {display}",
                    callback_data=f"connect_{name}",
                )
            )

    text = "🔌 <b>Плагины TeleFlow:</b>\n"
    if connected_lines:
        text += "\n" + "\n".join(connected_lines)
    else:
        text += "\nНет подключённых плагинов."

    if connect_buttons:
        text += "\n\n<b>Подключить:</b>"

    # Кнопки по одной в строке
    rows = [[btn] for btn in connect_buttons]
    if disconnect_buttons:
        rows += [[btn] for btn in disconnect_buttons]

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    return text, keyboard


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("plugins"))
async def cmd_plugins(message: Message) -> None:
    bot = _manager()
    if not bot:
        return
    text, keyboard = await _build_plugins_message()
    await bot.send_message(
        config.GROUP_CHAT_ID, text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("connect_"))
async def cb_connect(callback: CallbackQuery) -> None:
    await callback.answer()
    bot = _manager()
    if not bot:
        return
    from plugins_registry import PLUGINS
    plugin_name = callback.data.removeprefix("connect_")
    if plugin_name not in PLUGINS:
        return
    await _send_connect_info(bot, plugin_name)


@router.callback_query(F.data.startswith("disconnect_"))
async def cb_disconnect(callback: CallbackQuery) -> None:
    await callback.answer()
    bot = _manager()
    if not bot:
        return
    from plugins_registry import PLUGINS
    plugin_name = callback.data.removeprefix("disconnect_")
    if plugin_name not in PLUGINS:
        return

    active = await database.get_active_plugin_names()
    if plugin_name not in active:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"⚠️ Плагин уже не подключён.",
        )
        return

    await database.delete_plugin_key(plugin_name)
    info = PLUGINS[plugin_name]
    await bot.send_message(
        config.GROUP_CHAT_ID,
        f"✅ <b>{info['display_name']}</b> отключён.",
        parse_mode="HTML",
    )
    # Обновляем сообщение с кнопками
    text, keyboard = await _build_plugins_message()
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        pass


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("connect"))
async def cmd_connect(message: Message) -> None:
    bot = _manager()
    if not bot:
        return

    from plugins_registry import PLUGINS

    plugin_name = _parse_plugin_arg(message.text or "")
    if not plugin_name:
        # Показываем /plugins с кнопками
        text, keyboard = await _build_plugins_message()
        await bot.send_message(
            config.GROUP_CHAT_ID, text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if plugin_name not in PLUGINS:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Плагин <code>{plugin_name}</code> не найден. Используй /plugins",
            parse_mode="HTML",
        )
        return

    await _send_connect_info(bot, plugin_name)


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("apikey"))
async def cmd_apikey(message: Message) -> None:
    bot = _manager()
    if not bot:
        return

    # Удаляем сообщение немедленно — в нём API-ключ
    try:
        await message.delete()
    except Exception:
        pass  # Нет прав или сообщение уже удалено

    from plugins_registry import PLUGINS, SHARED_KEY_GROUPS

    # Разбираем вручную чтобы отфильтровать @username часть
    raw_parts = (message.text or "").split()
    args = [p for p in raw_parts[1:] if not p.startswith("@")]
    if len(args) < 2:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Синтаксис: /apikey [plugin_name] [key]\n"
            "Пример: /apikey openrouter sk-or-v1-...",
        )
        return

    plugin_name = args[0].lower()
    api_key = " ".join(args[1:])

    if plugin_name not in PLUGINS:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Плагин <code>{plugin_name}</code> не найден. Используй /plugins",
            parse_mode="HTML",
        )
        return

    user_id = message.from_user.id if message.from_user else 0
    await database.save_plugin_key(plugin_name, api_key, user_id)

    # Автоматически применяем ключ для плагинов с тем же api_key_name
    info = PLUGINS[plugin_name]
    api_key_name = info.get("api_key_name", "")
    auto_connected = []
    for group_key_name, group_plugins in SHARED_KEY_GROUPS.items():
        if api_key_name == group_key_name and plugin_name in group_plugins:
            for sibling in group_plugins:
                if sibling != plugin_name and sibling not in await database.get_active_plugin_names():
                    await database.save_plugin_key(sibling, api_key, user_id)
                    auto_connected.append(PLUGINS[sibling]["display_name"])

    masked = _mask_key(api_key)
    text = (
        f"✅ <b>{info['display_name']}</b> подключён!\n"
        f"Ключ: <code>{masked}</code>"
    )
    if auto_connected:
        text += f"\n\nАвтоматически подключено: {', '.join(auto_connected)}"

    await bot.send_message(config.GROUP_CHAT_ID, text, parse_mode="HTML")


@router.message(F.chat.id == config.GROUP_CHAT_ID, Command("disconnect"))
async def cmd_disconnect(message: Message) -> None:
    bot = _manager()
    if not bot:
        return

    from plugins_registry import PLUGINS

    plugin_name = _parse_plugin_arg(message.text or "")
    if not plugin_name:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            "❌ Синтаксис: /disconnect [plugin_name]",
        )
        return
    if plugin_name not in PLUGINS:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"❌ Плагин <code>{plugin_name}</code> не найден. Используй /plugins",
            parse_mode="HTML",
        )
        return

    active = await database.get_active_plugin_names()
    if plugin_name not in active:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            f"⚠️ Плагин <code>{plugin_name}</code> не был подключён.",
            parse_mode="HTML",
        )
        return

    await database.delete_plugin_key(plugin_name)
    info = PLUGINS[plugin_name]
    await bot.send_message(
        config.GROUP_CHAT_ID,
        f"✅ <b>{info['display_name']}</b> отключён.",
        parse_mode="HTML",
    )
