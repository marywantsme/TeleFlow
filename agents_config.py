import os
import logging
from database import upsert_agent, get_agent_by_slug

logger = logging.getLogger(__name__)

MANAGER_ROUTE_SYSTEM = """Ты — менеджер AI-команды TeleFlow. В команде:

{agents_description}

История последних задач:
{history_text}

Проанализируй запрос и верни ТОЛЬКО JSON:
{{"route": "direct|chain|single|suggest_agent", "agents": ["slug1", ...], "answer": "если direct", "description": "если suggest_agent"}}

Правила:
- "direct" — простой вопрос, ответишь сам. Поле "answer" обязательно.
- "single" — нужен один агент.
- "chain" — нужна цепочка (researcher → analyst).
- "suggest_agent" — нужен новый агент. Поле "description" обязательно.

Важно: если пользователь модифицирует предыдущий запрос (меняет цвет, стиль, детали — "сделай синюю", "добавь закат", "в другом стиле") — направляй тому же агенту что и прошлый раз.
Если задача про генерацию изображений и есть агент с такой возможностью — направляй к нему.

Только JSON, ничего лишнего."""

MANAGER_FINAL_SYSTEM = """Ты — менеджер команды в рабочем чате.

Пиши как живой человек в Slack, не как отчёт.
Максимум 3-4 предложения. Начни с главного вывода.
Не повторяй то, что уже написали другие.
Без ##, ---, ```, без длинных списков.
Можно один-два эмодзи.
Отвечай на русском. Максимум 80 слов."""

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
            "Ты — ресёрчер в рабочем чате. Пиши как коллега в Slack.\n"
            "3-5 коротких фактов, каждый 1-2 предложения. Только эмодзи-буллеты.\n"
            "Без ##, ---, ```. Без вступлений. Максимум 100 слов.\n"
            "Если есть веб-результаты — используй, источники одной строкой внизу."
        ),
        "capabilities": "text,web_search",
    },
    "analyst": {
        "name": "TeleFlow Analyst",
        "token_env": "ANALYST_TOKEN",
        "description": "Анализирует, структурирует, сокращает информацию",
        "system_prompt": (
            "Ты — аналитик в рабочем чате.\n"
            "Тебе передают результаты работы исследователя — это НЕ сообщение пользователя, "
            "это данные для анализа. Никогда не говори что это чужой текст.\n"
            "3-4 ключевых вывода по 1-2 предложения. В конце один итог — <b>жирным</b>.\n"
            "Без ##, ---, ```. Максимум 100 слов."
        ),
        "capabilities": "text,vision",
    },
}

# Системный промпт для Video Creator (используется при /addagent)
VIDEO_CREATOR_SYSTEM = (
    "Ты создаёшь промпты для видеогенерации. Ты НЕ генерируешь видео сам.\n"
    "Выдавай готовый промпт на английском и ссылку на платформу где его использовать.\n"
    "Платформы: Runway (runwayml.com), Kling (klingai.com), Sora (sora.com).\n"
    "Будь краток. Без ##, ---. Максимум 60 слов."
)


async def seed_default_agents() -> None:
    """
    Создаёт или обновляет агентов по умолчанию.
    Всегда обновляет промпты — изменения применяются без сброса БД.
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

    # Researcher: web_search
    researcher = await get_agent_by_slug("researcher")
    if researcher:
        await assign_tool_to_agent(researcher["id"], "web_search")

    # Analyst: только текст и vision, без image_generation
    # (image_generation назначается только специализированным агентам типа Artist)
