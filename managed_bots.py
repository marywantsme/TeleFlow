"""
Managed Bots — Telegram Bot API 9.6 (3 апреля 2026).

Telegram позволяет «управляющему» боту создавать других ботов одним тапом
пользователя через KeyboardButtonRequestManagedBot или через специальную ссылку
вида https://t.me/newbot/{manager_username}/{suggested_username}?name=...

aiogram 3.26.0 ещё не содержит типов 9.6 — работаем через raw API поверх aiohttp.
"""
import json
import logging
from typing import Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


async def _raw_api_call(token: str, method: str, payload: dict) -> dict:
    """Сырой вызов Telegram Bot API."""
    url = f"{API_BASE}/bot{token}/{method}"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as resp:
            data = await resp.json()
    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error [{method}]: "
            f"{data.get('description') or data}"
        )
    return data.get("result")


async def can_manage_bots(token: str) -> bool:
    """Проверяет флаг can_manage_bots в getMe (включается в @BotFather)."""
    try:
        me = await _raw_api_call(token, "getMe", {})
        return bool(me.get("can_manage_bots"))
    except Exception as exc:
        logger.warning("can_manage_bots check failed: %s", exc)
        return False


async def send_managed_bot_button(
    token: str,
    chat_id: int,
    text: str,
    button_text: str,
    request_id: int,
    suggested_name: str,
    suggested_username: str,
) -> Optional[dict]:
    """
    Отправляет сообщение с ReplyKeyboard, содержащим request_managed_bot.

    Работает только в приватном чате с ботом — в группе Telegram возвращает
    ошибку «button is unavailable». Для группы используй fallback-ссылку.
    """
    reply_markup = {
        "keyboard": [[{
            "text": button_text,
            "request_managed_bot": {
                "request_id": request_id,
                "suggested_name": suggested_name,
                "suggested_username": suggested_username,
            },
        }]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
    return await _raw_api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(reply_markup),
    })


async def get_managed_bot_token(token: str, user_id: int) -> str:
    """Возвращает токен managed-бота по его user_id."""
    result = await _raw_api_call(token, "getManagedBotToken", {"user_id": user_id})
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("token", "") or result.get("result", "")
    return ""


def build_fallback_link(
    manager_username: str,
    suggested_username: str,
    suggested_name: str = "",
) -> str:
    """Ссылка-алиас для создания managed-бота (Bot API 9.6)."""
    link = f"https://t.me/newbot/{manager_username}/{suggested_username}"
    if suggested_name:
        link += f"?name={quote(suggested_name)}"
    return link
