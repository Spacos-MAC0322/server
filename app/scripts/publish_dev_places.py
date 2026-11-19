"""Publish located places in local dev so they appear on the map and discovery."""

import asyncio

from app.core.database import connect_database


async def main() -> None:
    async with connect_database() as db:
        await db.places.update_many(
            {"location": {"$ne": None}},
            {
                "$set": {
                    "publication_status": "published",
                    "is_independent": True,
                    "community_center": True,
                    "region_zone": "Oeste",
                    "curation_score": 5,
                }
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
