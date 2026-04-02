import aiosqlite
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "teleflow.db"


async def init_db() -> None:
    """Создаёт таблицы базы данных если они не существуют."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                task_text TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                agent_name TEXT,
                role TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                slug TEXT UNIQUE,
                username TEXT,
                token TEXT,
                system_prompt TEXT,
                description TEXT,
                capabilities TEXT DEFAULT 'text',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                description TEXT,
                function_name TEXT,
                required_env_key TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_tools (
                agent_id INTEGER,
                tool_id INTEGER,
                PRIMARY KEY (agent_id, tool_id)
            )
        """)
        await db.commit()
    logger.info("Database initialized: %s", DB_PATH)


async def create_task(user_id: int, task_text: str) -> int:
    """Создаёт новую задачу и возвращает её id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO tasks (user_id, task_text, status) VALUES (?, ?, 'pending')",
            (user_id, task_text),
        )
        await db.commit()
        return cursor.lastrowid


async def update_task_status(task_id: int, status: str) -> None:
    """Обновляет статус задачи."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (status, task_id),
        )
        await db.commit()


async def save_agent_message(task_id: int, agent_name: str, role: str, content: str) -> None:
    """Сохраняет сообщение агента в базу данных."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO agent_messages (task_id, agent_name, role, content) VALUES (?, ?, ?, ?)",
            (task_id, agent_name, role, content),
        )
        await db.commit()


async def get_recent_tasks(user_id: int, limit: int = 5) -> list[dict]:
    """
    Возвращает последние задачи пользователя с кратким содержанием
    (последнее сообщение менеджера для каждой задачи).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT t.id, t.task_text, t.status, t.created_at,
                   (SELECT content FROM agent_messages
                    WHERE task_id = t.id AND agent_name = 'manager'
                    ORDER BY created_at DESC LIMIT 1) AS summary
            FROM tasks t
            WHERE t.user_id = ?
            ORDER BY t.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_agent_context(agent_name: str, limit: int = 10) -> list[dict]:
    """
    Возвращает последние сообщения агента для контекста (от старых к новым).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content FROM agent_messages
            WHERE agent_name = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (agent_name, limit),
        )
        rows = await cursor.fetchall()
        # Разворачиваем чтобы старые сообщения были первыми
        return [dict(row) for row in reversed(rows)]


async def get_all_active_agents() -> list[dict]:
    """Возвращает все активные агенты из реестра."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM agents_registry WHERE is_active = 1 ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_agent_by_slug(slug: str) -> Optional[dict]:
    """Возвращает агента по slug или None если не найден."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM agents_registry WHERE slug = ?",
            (slug,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def upsert_agent(
    slug: str,
    name: str,
    token: str,
    system_prompt: str,
    description: str,
    capabilities: str,
    username: str = "",
) -> int:
    """
    Создаёт или обновляет агента в реестре.
    Возвращает id записи.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO agents_registry (slug, name, token, system_prompt, description, capabilities, username, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(slug) DO UPDATE SET
                name = excluded.name,
                token = excluded.token,
                system_prompt = excluded.system_prompt,
                description = excluded.description,
                capabilities = excluded.capabilities,
                username = excluded.username,
                is_active = 1
            """,
            (slug, name, token, system_prompt, description, capabilities, username),
        )
        await db.commit()
        # Получаем id записи
        if cursor.lastrowid:
            return cursor.lastrowid
        row = await db.execute("SELECT id FROM agents_registry WHERE slug = ?", (slug,))
        result = await row.fetchone()
        return result[0] if result else 0


async def deactivate_agent(slug: str) -> None:
    """Деактивирует агента по slug."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE agents_registry SET is_active = 0 WHERE slug = ?",
            (slug,),
        )
        await db.commit()


async def update_agent_prompt(slug: str, system_prompt: str) -> None:
    """Обновляет системный промпт агента."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE agents_registry SET system_prompt = ? WHERE slug = ?",
            (system_prompt, slug),
        )
        await db.commit()


async def count_tasks() -> int:
    """Возвращает общее количество задач в базе данных."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM tasks")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def seed_tools(tools_list: list[dict]) -> None:
    """Добавляет инструменты в реестр если их нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        for tool in tools_list:
            await db.execute(
                """INSERT OR IGNORE INTO tools (name, description, function_name, required_env_key, is_active)
                   VALUES (?, ?, ?, ?, 1)""",
                (tool["name"], tool["description"], tool["function_name"], tool["required_env_key"]),
            )
        await db.commit()


async def get_all_tools() -> list[dict]:
    """Возвращает все активные инструменты из реестра."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tools WHERE is_active = 1")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_agent_tools(agent_id: int) -> list[dict]:
    """Возвращает инструменты привязанные к агенту."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT t.* FROM tools t
               JOIN agent_tools at ON t.id = at.tool_id
               WHERE at.agent_id = ? AND t.is_active = 1""",
            (agent_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def assign_tool_to_agent(agent_id: int, tool_name: str) -> None:
    """Привязывает инструмент к агенту по имени инструмента."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM tools WHERE name = ?", (tool_name,))
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "INSERT OR IGNORE INTO agent_tools (agent_id, tool_id) VALUES (?, ?)",
                (agent_id, row[0])
            )
            await db.commit()


async def clear_agent_tools(agent_id: int) -> None:
    """Удаляет все привязки инструментов агента."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM agent_tools WHERE agent_id = ?", (agent_id,))
        await db.commit()
