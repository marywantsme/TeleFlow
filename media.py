import base64
import io
import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import PhotoSize, Document, Voice

logger = logging.getLogger(__name__)


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
    """Транскрибирует голосовое через tools.py"""
    try:
        file_info = await bot.get_file(voice.file_id)
        file_bytes_io = await bot.download_file(file_info.file_path)
        if hasattr(file_bytes_io, "read"):
            data = file_bytes_io.read()
        else:
            data = bytes(file_bytes_io)

        from tools import run_tool, is_available
        if not is_available("voice_transcription"):
            logger.info("voice_transcription not available")
            return None
        result = await run_tool("voice_transcription", file_bytes=data, file_id=voice.file_id)
        return result.get("data")
    except Exception as exc:
        logger.error("Voice transcription failed: %s", exc)
        return None
