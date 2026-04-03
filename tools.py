"""
Реестр инструментов и их реализации.
Каждый инструмент — async функция, возвращающая dict {"type": ..., "data": ...}
"""
import logging
import os
import aiohttp
from typing import Optional

from config import TAVILY_API_KEY, OPENAI_API_KEY

logger = logging.getLogger(__name__)


async def web_search(query: str) -> dict:
    """Ищет актуальную информацию через Tavily."""
    if not TAVILY_API_KEY:
        return {"type": "text", "data": None}
    try:
        from tavily import TavilyClient
        import asyncio
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.search(query, max_results=5)
        )
        parts = []
        for r in results.get("results", []):
            title = r.get("title", "")
            content = r.get("content", "")[:300]
            url = r.get("url", "")
            parts.append(f"<b>{title}</b>\n{content}\n<a href='{url}'>{url}</a>")
        text = "🌐 <b>Веб-поиск:</b>\n\n" + "\n\n".join(parts) if parts else None
        return {"type": "text", "data": text}
    except Exception as exc:
        logger.error("web_search error: %s", exc)
        return {"type": "text", "data": None}


async def generate_image(prompt: str) -> dict:
    """Генерирует изображение через DALL-E 3."""
    if not OPENAI_API_KEY:
        return {"type": "text", "data": "❌ OPENAI_API_KEY не задан."}
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                image_bytes = await resp.read()
        return {"type": "photo", "data": image_bytes}
    except Exception as exc:
        logger.error("generate_image error: %s", exc)
        err_str = str(exc).lower()
        if "content_policy" in err_str or "safety" in err_str or "policy_violation" in err_str:
            return {
                "type": "text",
                "data": "❌ Не получилось сгенерировать — описание не прошло проверку безопасности. Попробуй переформулировать запрос.",
            }
        return {"type": "text", "data": f"❌ Ошибка генерации: {exc}"}


async def transcribe_voice(file_bytes: bytes, file_id: str) -> dict:
    """Транскрибирует голосовое сообщение через Whisper."""
    if not OPENAI_API_KEY:
        return {"type": "text", "data": None}
    tmp_path = f"/tmp/voice_{file_id}.ogg"
    try:
        from openai import AsyncOpenAI
        import io
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        with open(tmp_path, "rb") as f:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1", file=f, language="ru"
            )
        return {"type": "text", "data": transcript.text}
    except Exception as exc:
        logger.error("transcribe_voice error: %s", exc)
        return {"type": "text", "data": None}
    finally:
        import os as _os
        if _os.path.exists(tmp_path):
            try:
                _os.remove(tmp_path)
            except Exception:
                pass


# Реестр всех инструментов
TOOLS = {
    "web_search": {
        "description": "Поиск актуальной информации в интернете",
        "function": web_search,
        "env_key": "TAVILY_API_KEY",
    },
    "image_generation": {
        "description": "Генерация изображений по текстовому описанию (DALL-E 3)",
        "function": generate_image,
        "env_key": "OPENAI_API_KEY",
    },
    "voice_transcription": {
        "description": "Распознавание голосовых сообщений",
        "function": transcribe_voice,
        "env_key": "OPENAI_API_KEY",
    },
}


def is_available(tool_name: str) -> bool:
    """Проверяет доступен ли инструмент (задан ли нужный env ключ)."""
    tool = TOOLS.get(tool_name)
    if not tool:
        return False
    env_key = tool.get("env_key", "")
    return bool(os.getenv(env_key, ""))


async def run_tool(tool_name: str, **kwargs) -> dict:
    """Запускает инструмент по имени."""
    tool = TOOLS.get(tool_name)
    if not tool:
        return {"type": "text", "data": None}
    return await tool["function"](**kwargs)
