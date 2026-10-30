"""Initialize the complete MongoDB model: python -m app.scripts.init_db."""

import asyncio

from app.core.database import connect_database


async def main() -> None:
    async with connect_database() as database:
        print(
            f"MongoDB '{database.name}': coleções, validadores e índices configurados."
        )


if __name__ == "__main__":
    asyncio.run(main())
