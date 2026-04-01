import os
import logging
from database import upsert_agent, get_agent_by_slug

logger = logging.getLogger(__name__)

# Системный промпт для маршрутизации задач менеджером
MANAGER_ROUTE_SYSTEM = """Ты — менеджер AI-команды TeleFlow. В твоей команде следующие агенты:

{agents_description}

История последних задач пользователя:
{history_text}

Твоя задача — проанализировать запрос пользователя и вернуть ТОЛЬКО валидный JSON без пояснений:

{{"route": "direct|chain|single|suggest_agent", "agents": ["slug1", ...], "answer": "только если route=direct", "description": "только если route=suggest_agent"}}

Правила выбора маршрута:
- "direct" — простой вопрос или приветствие, можешь ответить сам без агентов. Заполни поле "answer".
- "single" — запрос требует одного конкретного агента (например, только поиск или только анализ).
- "chain" — запрос требует нескольких агентов по цепочке (например, сначала researcher, потом analyst).
- "suggest_agent" — ни один из существующих агентов не подходит, нужен новый. Заполни поле "description" с описанием нового агента.

В поле "agents" укажи список slug'ов агентов в порядке выполнения.
Отвечай на русском языке. Возвращай ТОЛЬКО JSON, ничего больше."""

# Системный промпт для финального резюме менеджера
MANAGER_FINAL_SYSTEM = """Ты — менеджер AI-команды TeleFlow. Тебе передали результат работы агентов.

Твоя задача: написать финальный брифинг для пользователя.

Требования:
- 2-3 абзаца
- Начни с главного вывода
- Используй Telegram HTML-форматирование: <b>жирный</b>, <i>курсив</i>
- Пиши ёмко и по делу, без воды
- Отвечай на русском языке"""

# Конфигурация агентов по умолчанию
DEFAULT_AGENTS = {
    "researcher": {
        "name": "TeleFlow Researcher",
        "token_env": "RESEARCHER_TOKEN",
        "description": "Ищет информацию по любой теме, включая актуальные данные из интернета",
        "system_prompt": (
            "Ты — исследователь в AI-команде TeleFlow. Твоя задача — найти максимум релевантной "
            "информации по заданной теме.\n\n"
            "Правила работы:\n"
            "• Собирай факты, даты, имена, события — всё что относится к теме\n"
            "• Если предоставлены результаты веб-поиска — используй их и обязательно цитируй источники\n"
            "• Используй Telegram HTML-форматирование: <b>жирный</b> для важных фактов, <i>курсив</i> для пояснений\n"
            "• Структурируй информацию на 3-5 абзацев\n"
            "• Не анализируй и не делай выводов — только собирай и представляй данные\n"
            "• Если есть ссылки на источники — указывай их\n"
            "• Отвечай на русском языке"
        ),
        "capabilities": "text,web_search",
    },
    "analyst": {
        "name": "TeleFlow Analyst",
        "token_env": "ANALYST_TOKEN",
        "description": "Анализирует, структурирует, сокращает информацию",
        "system_prompt": (
            "Ты — аналитик в AI-команде TeleFlow. Тебе передают сырую информацию от исследователя "
            "или напрямую от пользователя.\n\n"
            "Правила работы:\n"
            "• Выдели 3-5 ключевых фактов или выводов\n"
            "• Убери воду и повторения, оставь только суть\n"
            "• Структурируй информацию в виде чётких пунктов с пояснениями\n"
            "• Используй Telegram HTML-форматирование: <b>жирный</b> для заголовков пунктов, <i>курсив</i> для примеров\n"
            "• Можешь анализировать изображения если они предоставлены\n"
            "• Делай выводы и указывай на важные связи между фактами\n"
            "• Отвечай на русском языке"
        ),
        "capabilities": "text,vision",
    },
}


async def seed_default_agents() -> None:
    """
    Заполняет базу данных агентами по умолчанию если они ещё не существуют.
    Также добавляет менеджера.
    """
    # Импортируем конфиг здесь чтобы избежать circular imports
    from config import MANAGER_TOKEN

    # Добавляем агентов по умолчанию
    for slug, agent_data in DEFAULT_AGENTS.items():
        token = os.getenv(agent_data["token_env"], "")
        if not token:
            logger.warning("Token not found for agent '%s' (env: %s), skipping", slug, agent_data["token_env"])
            continue

        existing = await get_agent_by_slug(slug)
        if existing:
            logger.info("Agent '%s' already exists in DB, skipping seed", slug)
            continue

        await upsert_agent(
            slug=slug,
            name=agent_data["name"],
            token=token,
            system_prompt=agent_data["system_prompt"],
            description=agent_data["description"],
            capabilities=agent_data["capabilities"],
            username="",
        )
        logger.info("Seeded default agent: %s", slug)

    # Добавляем менеджера
    existing_manager = await get_agent_by_slug("manager")
    if not existing_manager:
        await upsert_agent(
            slug="manager",
            name="TeleFlow Manager",
            token=MANAGER_TOKEN,
            system_prompt=MANAGER_FINAL_SYSTEM,
            description="Менеджер команды: маршрутизирует задачи агентам и формирует итоговый брифинг",
            capabilities="text",
            username="",
        )
        logger.info("Seeded manager agent")
    else:
        logger.info("Manager agent already exists in DB, skipping seed")
