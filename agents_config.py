import os
import logging
from database import upsert_agent, get_agent_by_slug

logger = logging.getLogger(__name__)

MANAGER_ROUTE_SYSTEM = """Ты — менеджер AI-команды TeleFlow. В команде:

{agents_description}

История задач:
{history_text}

Проанализируй запрос и верни ТОЛЬКО JSON:
{{"route": "direct|chain|single|suggest_agent", "agents": ["slug1", ...], "answer": "если direct", "description": "если suggest_agent"}}

Правила:
- "direct" — простой вопрос, ответишь сам. Поле "answer" обязательно.
- "single" — нужен один агент.
- "chain" — нужна цепочка (researcher → analyst).
- "suggest_agent" — нужен новый агент. Поле "description" обязательно.

Только JSON, ничего лишнего."""

MANAGER_FINAL_SYSTEM = """Ты — менеджер команды в рабочем чате.

Пиши как живой человек в Slack, не как отчёт.
Максимум 3-4 предложения. Начни с главного вывода.
Не повторяй то, что уже написали другие.
Без заголовков, без маркированных списков.
Можно один-два эмодзи для живости.
Отвечай на русском."""

DEFAULT_TOOLS = [
    {"name": "web_search", "description": "Поиск актуальной информации", "function_name": "web_search", "required_env_key": "TAVILY_API_KEY"},
    {"name": "image_generation", "description": "Генерация изображений (DALL-E 3)", "function_name": "generate_image", "required_env_key": "OPENAI_API_KEY"},
    {"name": "voice_transcription", "description": "Распознавание голоса (Whisper)", "function_name": "transcribe_voice", "required_env_key": "OPENAI_API_KEY"},
]

DEFAULT_AGENTS = {
    "researcher": {
        "name": "TeleFlow Researcher",
        "token_env": "RESEARCHER_TOKEN",
        "description": "Ищет информацию по любой теме, включая актуальные данные из интернета",
        "system_prompt": (
            "Ты — ресёрчер в рабочем чате. Пиши как коллега в Slack, не как энциклопедия.\n\n"
            "Формат ответа:\n"
            "• 3-5 коротких фактов, каждый 1-2 предложения\n"
            "• Только эмодзи-буллеты, никаких ## и ---\n"
            "• Максимум 150 слов\n"
            "• Если есть веб-результаты — используй их, источники одной строкой внизу\n\n"
            "Без вступлений типа «Конечно! Вот информация:». Сразу факты."
        ),
        "capabilities": "text,web_search",
    },
    "analyst": {
        "name": "TeleFlow Analyst",
        "token_env": "ANALYST_TOKEN",
        "description": "Анализирует, структурирует, сокращает информацию",
        "system_prompt": (
            "Ты — аналитик в рабочем чате. Коротко и по делу, как в Slack.\n\n"
            "Формат:\n"
            "• 3-4 ключевых вывода, каждый 1-2 предложения\n"
            "• В конце один итоговый вывод — <b>жирным</b>\n"
            "• Максимум 100 слов\n\n"
            "Без повторов, без вступлений. Только суть."
        ),
        "capabilities": "text,vision",
    },
}


async def seed_default_agents() -> None:
    """
    Создаёт или обновляет агентов по умолчанию в БД.
    Всегда обновляет системные промпты — чтобы изменения применялись без сброса БД.
    """
    from config import MANAGER_TOKEN

    for slug, agent_data in DEFAULT_AGENTS.items():
        token = os.getenv(agent_data["token_env"], "")
        if not token:
            logger.warning("Token not found for '%s' (env: %s)", slug, agent_data["token_env"])
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
        logger.info("Agent seeded/updated: %s", slug)

    await upsert_agent(
        slug="manager",
        name="TeleFlow Manager",
        token=MANAGER_TOKEN,
        system_prompt=MANAGER_FINAL_SYSTEM,
        description="Менеджер: маршрутизирует задачи, формирует итоговый брифинг",
        capabilities="text",
        username="",
    )
    logger.info("Manager seeded/updated")

    from database import seed_tools, assign_tool_to_agent
    await seed_tools(DEFAULT_TOOLS)

    # Assign tools to researcher: web_search
    researcher = await get_agent_by_slug("researcher")
    if researcher:
        await assign_tool_to_agent(researcher["id"], "web_search")

    # Analyst gets text from researcher — no tools assigned by default
