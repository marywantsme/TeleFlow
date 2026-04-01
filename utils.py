import asyncio
import json
import random
import re
import string
import logging
from aiogram import Bot
from aiogram.enums import ChatAction

logger = logging.getLogger(__name__)


async def typing_while(bot: Bot, chat_id: int, coro):
    """Отправляет действие 'печатает' каждые 4 секунды пока выполняется coro."""
    async def keep_typing():
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        result = await coro
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
    return result


def random_suffix(length: int = 4) -> str:
    """Возвращает случайную строку из строчных букв и цифр."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))


def extract_json(text: str) -> dict | None:
    """
    Извлекает первый JSON-объект из текста.
    Сначала пробует markdown-блоки, затем ищет сбалансированные фигурные скобки.
    """
    if not text:
        return None

    # Попытка 1: markdown-блок ```json ... ``` или ``` ... ```
    md_pattern = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
    match = md_pattern.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Попытка 2: найти сбалансированные фигурные скобки
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Попробуем следующую позицию
                    next_start = text.find("{", start + 1)
                    if next_start == -1:
                        return None
                    start = next_start
                    depth = 0
                    i = start - 1
                    break

    return None
