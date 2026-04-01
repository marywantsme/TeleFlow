import base64
import io
import logging
import os
from typing import Optional

from aiogram import Bot
from aiogram.types import PhotoSize, Document, Voice

from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

# Проверяем доступность openai
try:
    import openai as _openai
    OPENAI_AVAILABLE = True
    logger.info("OpenAI available for voice transcription")
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI not installed. Voice transcription disabled.")


async def photo_to_base64(bot: Bot, photo: PhotoSize) -> Optional[str]:
    """
    Скачивает фото и возвращает base64-строку.
    """
    try:
        file_info = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        if hasattr(file_bytes, "read"):
            data = file_bytes.read()
        else:
            data = file_bytes
        return base64.b64encode(data).decode("utf-8")
    except Exception as exc:
        logger.error("Failed to convert photo to base64: %s", exc)
        return None


async def document_to_text(bot: Bot, document: Document) -> Optional[str]:
    """
    Скачивает документ и возвращает его текстовое содержимое.
    Поддерживает только text/* и application/pdf.
    """
    mime = document.mime_type or ""
    if not (mime.startswith("text/") or mime == "application/pdf"):
        logger.info("Unsupported document mime type: %s", mime)
        return None

    try:
        file_info = await bot.get_file(document.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        if hasattr(file_bytes, "read"):
            data = file_bytes.read()
        else:
            data = bytes(file_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error("Failed to read document: %s", exc)
        return None


async def voice_to_text(bot: Bot, voice: Voice) -> Optional[str]:
    """
    Транскрибирует голосовое сообщение через OpenAI Whisper.
    """
    if not OPENAI_AVAILABLE:
        logger.info("OpenAI not available for voice transcription")
        return None

    if not OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set, skipping voice transcription")
        return None

    tmp_path = f"/tmp/voice_{voice.file_id}.ogg"
    try:
        # Скачиваем голосовое сообщение
        file_info = await bot.get_file(voice.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        if hasattr(file_bytes, "read"):
            data = file_bytes.read()
        else:
            data = bytes(file_bytes)

        # Сохраняем во временный файл
        with open(tmp_path, "wb") as f:
            f.write(data)

        # Транскрибируем через Whisper
        client = _openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
        with open(tmp_path, "rb") as f:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ru",
            )
        return transcript.text

    except Exception as exc:
        logger.error("Voice transcription failed: %s", exc)
        return None
    finally:
        # Удаляем временный файл
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
