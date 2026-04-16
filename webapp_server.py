"""
HTTP-сервер для Telegram Mini App.

Подаёт webapp/index.html и JSON API:
    GET  /                 — index.html
    GET  /api/agents       — список активных агентов
    GET  /api/plugins      — каталог плагинов + какие подключены
    GET  /api/history      — последние задачи пользователя (?user_id=...)
    GET  /api/stats        — агрегированная статистика

HTTPS не обеспечивается на этом уровне — для продакшна используйте
reverse-proxy (caddy/nginx) перед этим сервером на порту 8080.
"""
import logging
import os
from aiohttp import web

import database
from plugins_registry import PLUGINS

logger = logging.getLogger(__name__)

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")
DEFAULT_PORT = int(os.getenv("WEBAPP_PORT", "8080"))


async def _index(request: web.Request) -> web.Response:
    path = os.path.join(WEBAPP_DIR, "index.html")
    if not os.path.exists(path):
        return web.Response(text="Mini App not built yet", status=404)
    with open(path, "rb") as f:
        body = f.read()
    return web.Response(body=body, content_type="text/html", charset="utf-8")


async def _api_agents(request: web.Request) -> web.Response:
    agents = await database.get_all_active_agents()
    out = [{
        "slug": a["slug"],
        "name": a["name"],
        "username": a.get("username", ""),
        "description": a.get("description", ""),
        "capabilities": a.get("capabilities", "text"),
    } for a in agents]
    return web.json_response(out)


async def _api_plugins(request: web.Request) -> web.Response:
    active = set(await database.get_active_plugin_names())
    out = []
    for name, info in PLUGINS.items():
        out.append({
            "name": name,
            "display_name": info["display_name"],
            "description": info["description"],
            "cost_info": info.get("cost_info", ""),
            "connected": name in active,
        })
    return web.json_response(out)


async def _api_history(request: web.Request) -> web.Response:
    try:
        user_id = int(request.query.get("user_id", "0"))
    except ValueError:
        user_id = 0
    limit = min(int(request.query.get("limit", "20")), 100)
    tasks = await database.get_recent_tasks(user_id, limit=limit)
    out = [{
        "id": t["id"],
        "text": t["task_text"],
        "status": t["status"],
        "created_at": t["created_at"],
        "summary": (t.get("summary") or "")[:200],
    } for t in tasks]
    return web.json_response(out)


async def _api_stats(request: web.Request) -> web.Response:
    count = await database.count_tasks()
    agents = await database.get_all_active_agents()
    active_plugins = await database.get_active_plugin_names()
    return web.json_response({
        "tasks_total": count,
        "agents_active": len(agents),
        "plugins_connected": len(active_plugins),
    })


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/api/agents", _api_agents)
    app.router.add_get("/api/plugins", _api_plugins)
    app.router.add_get("/api/history", _api_history)
    app.router.add_get("/api/stats", _api_stats)
    # Статика: webapp/*.js, *.css и прочее рядом с index.html
    if os.path.isdir(WEBAPP_DIR):
        app.router.add_static("/static/", WEBAPP_DIR)
    return app


async def start_in_background(port: int = DEFAULT_PORT) -> web.AppRunner:
    """Запускает сервер как фоновую задачу и возвращает AppRunner для остановки."""
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("Mini App HTTP server started on port %d", port)
    return runner
