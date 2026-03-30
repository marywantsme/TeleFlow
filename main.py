import asyncio
import logging

from coordinator import start, manager_bot, researcher_bot, analyst_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main() -> None:
    try:
        await start()
    finally:
        await manager_bot.session.close()
        await researcher_bot.session.close()
        await analyst_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
