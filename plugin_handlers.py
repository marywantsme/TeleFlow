"""
Обработчики плагинов TeleFlow v3.0.
Каждый handler — async функция с единым интерфейсом.
Все вызовы обёрнуты в retry с обработкой типичных ошибок API.
"""
import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

# Голос по умолчанию для ElevenLabs (Rachel)
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


@dataclass
class PluginResult:
    type: str   # "text", "photo", "voice", "video", "document"
    data: str | bytes
    filename: str = ""
    caption: str = ""


async def _retry(coro_factory, plugin_name: str, max_attempts: int = 3) -> PluginResult:
    """Запускает coro_factory до max_attempts раз с паузами 2/5/10 сек."""
    delays = [2, 5, 10]
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()

            # Не повторять — результат не изменится
            if any(k in err for k in ("content_policy", "safety", "policy_violation", "content filter")):
                return PluginResult(
                    type="text",
                    data="Описание не прошло модерацию, переформулируй"
                )
            if any(k in err for k in ("invalid_api_key", "incorrect api key", "unauthorized", "authentication")) or \
               "401" in err:
                return PluginResult(
                    type="text",
                    data=f"API-ключ невалиден, обнови через /connect {plugin_name}"
                )

            if attempt < max_attempts - 1:
                logger.warning(
                    "Plugin '%s' attempt %d/%d failed: %s",
                    plugin_name, attempt + 1, max_attempts, exc
                )
                await asyncio.sleep(delays[attempt])

    err = str(last_exc).lower() if last_exc else ""
    if any(k in err for k in ("rate", "429", "quota", "too many")):
        return PluginResult(type="text", data="Лимит API исчерпан, попробуй позже")

    logger.error("Plugin '%s' failed after %d attempts: %s", plugin_name, max_attempts, last_exc)
    return PluginResult(type="text", data="Не удалось выполнить запрос, попробуй позже")


# ──────────────────────────────────────────────
# OpenRouter
# ──────────────────────────────────────────────

async def handle_openrouter_image(
    prompt: str,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Генерация картинок через OpenRouter (Gemini Flash Image)."""

    async def _do() -> PluginResult:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "google/gemini-2.5-flash-preview-image",
                    "messages": [{"role": "user", "content": prompt}],
                    "modalities": ["image", "text"],
                },
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"OpenRouter {resp.status}: {text[:300]}")
                data = await resp.json()

        choices = data.get("choices", [])
        if not choices:
            raise Exception("No choices in OpenRouter response")

        content = choices[0].get("message", {}).get("content", "")

        # Content может быть списком блоков или строкой
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        b64_data = url.split(",", 1)[1]
                        return PluginResult(
                            type="photo",
                            data=base64.b64decode(b64_data),
                            filename="generated.png",
                        )
                    # Скачиваем по URL
                    async with aiohttp.ClientSession() as s:
                        async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as img_resp:
                            img_bytes = await img_resp.read()
                    return PluginResult(type="photo", data=img_bytes, filename="generated.png")

        raise Exception(f"No image in OpenRouter response: {str(content)[:200]}")

    return await _retry(_do, "openrouter")


# ──────────────────────────────────────────────
# DALL-E
# ──────────────────────────────────────────────

async def handle_dalle(
    prompt: str,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Генерация картинок через DALL-E 3."""

    async def _do() -> PluginResult:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                image_bytes = await resp.read()
        return PluginResult(type="photo", data=image_bytes, filename="generated.png")

    return await _retry(_do, "dalle")


# ──────────────────────────────────────────────
# ElevenLabs
# ──────────────────────────────────────────────

async def handle_elevenlabs_tts(
    input_data: str,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Текст → речь через ElevenLabs."""
    voice_id = (options or {}).get("voice_id", _DEFAULT_VOICE_ID)

    async def _do() -> PluginResult:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": input_data,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"ElevenLabs TTS {resp.status}: {text[:300]}")
                audio_bytes = await resp.read()
        return PluginResult(type="voice", data=audio_bytes, filename="speech.mp3")

    return await _retry(_do, "elevenlabs_tts")


async def handle_elevenlabs_voice(
    input_data: bytes,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Изменение голоса через ElevenLabs Speech-to-Speech."""
    voice_id = (options or {}).get("voice_id", _DEFAULT_VOICE_ID)

    async def _do() -> PluginResult:
        form = aiohttp.FormData()
        form.add_field(
            "audio",
            input_data,
            filename="voice.ogg",
            content_type="audio/ogg",
        )
        form.add_field("model_id", "eleven_multilingual_sts_v2")
        form.add_field("voice_settings", '{"stability":0.5,"similarity_boost":0.75}')

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}",
                headers={
                    "xi-api-key": api_key,
                    "Accept": "audio/mpeg",
                },
                data=form,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"ElevenLabs voice {resp.status}: {text[:300]}")
                audio_bytes = await resp.read()
        return PluginResult(type="voice", data=audio_bytes, filename="voice.mp3")

    return await _retry(_do, "elevenlabs_voice")


# ──────────────────────────────────────────────
# Tavily
# ──────────────────────────────────────────────

async def handle_tavily_search(
    query: str,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Веб-поиск через Tavily."""

    async def _do() -> PluginResult:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.search(query, max_results=5)
        )
        parts = []
        for r in results.get("results", []):
            title = r.get("title", "")
            content = r.get("content", "")[:300]
            url = r.get("url", "")
            parts.append(f"<b>{title}</b>\n{content}\n<a href='{url}'>{url}</a>")
        text = "🌐 <b>Веб-поиск:</b>\n\n" + "\n\n".join(parts) if parts else "Ничего не найдено."
        return PluginResult(type="text", data=text)

    return await _retry(_do, "tavily")


# ──────────────────────────────────────────────
# Whisper
# ──────────────────────────────────────────────

async def handle_whisper(
    file_bytes: bytes,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Транскрипция голоса через Whisper."""
    file_id = (options or {}).get("file_id", "voice")
    tmp_path = f"/tmp/whisper_plugin_{file_id}.ogg"

    async def _do() -> PluginResult:
        from openai import AsyncOpenAI
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)
        try:
            client = AsyncOpenAI(api_key=api_key)
            with open(tmp_path, "rb") as f:
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1", file=f, language="ru"
                )
            return PluginResult(type="text", data=transcript.text)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    return await _retry(_do, "whisper")


# ──────────────────────────────────────────────
# Runway ML
# ──────────────────────────────────────────────

async def handle_runway(
    prompt: str,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Генерация видео через Runway Gen-3."""

    async def _do() -> PluginResult:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        }

        async with aiohttp.ClientSession() as session:
            # Создаём задачу генерации
            async with session.post(
                "https://api.dev.runwayml.com/v1/text_to_image",
                headers=headers,
                json={
                    "promptText": prompt,
                    "model": "gen3a_turbo",
                    "ratio": "1280:768",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    raise Exception(f"Runway {resp.status}: {text[:300]}")
                task_data = await resp.json()

            task_id = task_data.get("id")
            if not task_id:
                raise Exception("No task_id in Runway response")

            # Поллинг до завершения (максимум 5 минут)
            for _ in range(60):
                await asyncio.sleep(5)
                async with session.get(
                    f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as poll_resp:
                    poll_data = await poll_resp.json()

                status = poll_data.get("status", "")
                if status == "SUCCEEDED":
                    output = poll_data.get("output", [])
                    if not output:
                        raise Exception("No output in Runway response")
                    video_url = output[0]
                    async with session.get(
                        video_url, timeout=aiohttp.ClientTimeout(total=120)
                    ) as vid_resp:
                        video_bytes = await vid_resp.read()
                    return PluginResult(type="video", data=video_bytes, filename="video.mp4")
                if status in ("FAILED", "CANCELLED"):
                    raise Exception(f"Runway task {status}: {poll_data.get('failure', '')}")

            raise Exception("Runway task timed out after 5 minutes")

    return await _retry(_do, "runway")


# ──────────────────────────────────────────────
# Stability AI
# ──────────────────────────────────────────────

async def handle_stability(
    prompt: str,
    api_key: str,
    options: dict = None,
) -> PluginResult:
    """Генерация картинок через Stable Diffusion (Stability AI)."""

    async def _do() -> PluginResult:
        form = aiohttp.FormData()
        form.add_field("prompt", prompt)
        form.add_field("output_format", "png")
        form.add_field("aspect_ratio", "1:1")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.stability.ai/v2beta/stable-image/generate/core",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "image/*",
                },
                data=form,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Stability AI {resp.status}: {text[:300]}")
                image_bytes = await resp.read()
        return PluginResult(type="photo", data=image_bytes, filename="generated.png")

    return await _retry(_do, "stability")


# ──────────────────────────────────────────────
# Маппинг имя → handler
# ──────────────────────────────────────────────

HANDLERS = {
    "openrouter": handle_openrouter_image,
    "dalle": handle_dalle,
    "elevenlabs_voice": handle_elevenlabs_voice,
    "elevenlabs_tts": handle_elevenlabs_tts,
    "tavily": handle_tavily_search,
    "whisper": handle_whisper,
    "runway": handle_runway,
    "stability": handle_stability,
}


def get_handler(plugin_name: str):
    """Возвращает handler функцию для плагина или None."""
    return HANDLERS.get(plugin_name)
