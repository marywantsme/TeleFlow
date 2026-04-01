import logging
from typing import Optional

from anthropic import AsyncAnthropic

from config import CLAUDE_API_KEY, CLAUDE_MODEL, MAX_TOKENS, RESEARCHER_MAX_TOKENS
from agents_config import MANAGER_ROUTE_SYSTEM, MANAGER_FINAL_SYSTEM
from utils import extract_json

logger = logging.getLogger(__name__)

# Singleton клиент Anthropic
_client = AsyncAnthropic(api_key=CLAUDE_API_KEY)


async def call_agent(
    system_prompt: str,
    user_content,
    context: Optional[list] = None,
    image_b64: Optional[str] = None,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """
    Вызывает Claude с заданным системным промптом и содержимым.
    Поддерживает контекст (история сообщений) и изображения.
    """
    messages = []

    # Добавляем контекст если есть
    if context:
        for msg in context:
            messages.append({"role": msg["role"], "content": msg["content"]})

    # Формируем содержимое последнего сообщения
    if image_b64:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_b64,
                },
            },
            {"type": "text", "text": user_content},
        ]
    else:
        content = user_content

    messages.append({"role": "user", "content": content})

    response = await _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


async def route_task(task: str, agents_description: str, history_text: str) -> dict:
    """
    Маршрутизирует задачу пользователя к нужным агентам.
    Возвращает словарь с полями route, agents и опциональными answer/description.
    """
    # Подставляем динамические данные в системный промпт
    system = MANAGER_ROUTE_SYSTEM.format(
        agents_description=agents_description,
        history_text=history_text,
    )

    try:
        raw = await call_agent(system_prompt=system, user_content=task)
        result = extract_json(raw)
        if result is None:
            raise ValueError("Failed to extract JSON from routing response")

        # Проверяем обязательные поля
        if "route" not in result:
            raise ValueError("Missing 'route' field in routing response")

        return result
    except Exception as exc:
        logger.error("Routing failed, using fallback chain: %s", exc)
        # Fallback: цепочка researcher → analyst
        return {"route": "chain", "agents": ["researcher", "analyst"]}


async def run_agent_by_slug(
    slug: str,
    task: str,
    db_agent: Optional[dict] = None,
    image_b64: Optional[str] = None,
    web_results: Optional[str] = None,
) -> str:
    """
    Запускает агента по slug.
    Использует данные из БД если предоставлены, иначе дефолтные настройки.
    """
    # Получаем системный промпт
    if db_agent:
        system_prompt = db_agent.get("system_prompt", "")
    else:
        # Дефолтные промпты как запасной вариант
        _defaults = {
            "researcher": (
                "Ты — исследователь. Найди максимум информации по теме. "
                "Отвечай на русском языке, 3-5 абзацев."
            ),
            "analyst": (
                "Ты — аналитик. Выдели 3-5 ключевых фактов, структурируй информацию. "
                "Отвечай на русском языке."
            ),
            "manager": (
                "Ты — менеджер команды. Сформулируй итоговый брифинг. "
                "Отвечай на русском языке, 2-3 абзаца."
            ),
        }
        system_prompt = _defaults.get(slug, "Отвечай на русском языке.")

    # Формируем содержимое запроса
    if web_results:
        user_content = f"{web_results}\n\n---\n\n{task}"
    else:
        user_content = task

    # Получаем контекст из БД
    from database import get_agent_context
    context = await get_agent_context(slug, limit=10)

    # Определяем лимит токенов
    max_tokens = RESEARCHER_MAX_TOKENS if slug == "researcher" else MAX_TOKENS

    return await call_agent(
        system_prompt=system_prompt,
        user_content=user_content,
        context=context,
        image_b64=image_b64,
        max_tokens=max_tokens,
    )


async def run_manager_final(analysis: str) -> str:
    """
    Запускает менеджера для формирования финального брифинга.
    """
    return await call_agent(
        system_prompt=MANAGER_FINAL_SYSTEM,
        user_content=analysis,
    )
