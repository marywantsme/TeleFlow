import asyncio
import logging
from config import TAVILY_API_KEY

logger = logging.getLogger(__name__)

# Проверяем доступность tavily
TAVILY_AVAILABLE = False
try:
    from tavily import AsyncTavilyClient as _AsyncTavilyClient
    TAVILY_AVAILABLE = True
    _USE_ASYNC = True
    logger.info("Tavily async client available")
except ImportError:
    try:
        from tavily import TavilyClient as _SyncTavilyClient  # type: ignore
        TAVILY_AVAILABLE = True
        _USE_ASYNC = False
        logger.info("Tavily sync client available (will use executor)")
    except ImportError:
        logger.warning("Tavily not installed. Web search disabled.")
        _USE_ASYNC = False


async def search_web(query: str, max_results: int = 5) -> str | None:
    """
    Выполняет веб-поиск через Tavily API.
    Возвращает отформатированный HTML-текст с результатами или None при ошибке.
    """
    if not TAVILY_AVAILABLE:
        logger.debug("Tavily not available, skipping web search")
        return None

    if not TAVILY_API_KEY:
        logger.debug("TAVILY_API_KEY not set, skipping web search")
        return None

    try:
        if _USE_ASYNC:
            # Используем асинхронный клиент напрямую
            client = _AsyncTavilyClient(api_key=TAVILY_API_KEY)
            response = await client.search(query=query, max_results=max_results)
        else:
            # Запускаем синхронный клиент в executor
            def _sync_search():
                client = _SyncTavilyClient(api_key=TAVILY_API_KEY)
                return client.search(query=query, max_results=max_results)

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, _sync_search)

        results = response.get("results", [])
        if not results:
            return None

        # Форматируем результаты в HTML
        lines = ["🌐 <b>Результаты веб-поиска:</b>\n"]
        for item in results:
            title = item.get("title", "Без заголовка")
            content = item.get("content", "")
            url = item.get("url", "")
            snippet = content[:300] + ("..." if len(content) > 300 else "")
            lines.append(f"<b>{title}</b>\n{snippet}\n<a href='{url}'>{url}</a>")

        return "\n\n".join(lines)

    except Exception as exc:
        logger.error("Web search error for query '%s': %s", query, exc)
        return None
