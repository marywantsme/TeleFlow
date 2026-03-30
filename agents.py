from anthropic import AsyncAnthropic
from config import CLAUDE_API_KEY, CLAUDE_MODEL, MAX_TOKENS

_client = AsyncAnthropic(api_key=CLAUDE_API_KEY)

RESEARCHER_SYSTEM = (
    "Ты — исследователь. Твоя задача — найти максимум релевантной информации "
    "по заданной теме. Пиши факты, даты, имена, события. Не анализируй, "
    "не сокращай — просто собери всё что знаешь. Отвечай на русском языке. "
    "Объём: 3-5 абзацев."
)

ANALYST_SYSTEM = (
    "Ты — аналитик. Тебе передали сырую информацию от исследователя. "
    "Твоя задача: выдели 3-5 ключевых фактов, убери воду, структурируй. "
    "Формат: краткие пункты с пояснениями. Отвечай на русском языке."
)

MANAGER_SYSTEM = (
    "Ты — менеджер команды. Тебе передали анализ от аналитика. "
    "Твоя задача: сформулируй итоговый брифинг для пользователя. "
    "Коротко, ёмко, 2-3 абзаца. Начни с главного вывода. "
    "Отвечай на русском языке."
)


async def ask_researcher(task: str) -> str:
    response = await _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=RESEARCHER_SYSTEM,
        messages=[{"role": "user", "content": task}],
    )
    return response.content[0].text


async def ask_analyst(research: str) -> str:
    response = await _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=ANALYST_SYSTEM,
        messages=[{"role": "user", "content": research}],
    )
    return response.content[0].text


async def ask_manager(analysis: str) -> str:
    response = await _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=MANAGER_SYSTEM,
        messages=[{"role": "user", "content": analysis}],
    )
    return response.content[0].text
