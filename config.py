import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


# Обязательные токены ботов
MANAGER_TOKEN: str = _require("MANAGER_TOKEN")
RESEARCHER_TOKEN: str = _require("RESEARCHER_TOKEN")
ANALYST_TOKEN: str = _require("ANALYST_TOKEN")

# API ключи
CLAUDE_API_KEY: str = _require("CLAUDE_API_KEY")
GROUP_CHAT_ID: int = int(_require("GROUP_CHAT_ID"))

# Опциональные ключи (пустая строка если не задан)
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# Константы модели
CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
RESEARCHER_MAX_TOKENS = 2048
