"""Collect, normalize and persist cultural places using the existing components."""

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import HttpUrl, TypeAdapter

from app.core.database import connect_database
from app.integrations.mapas_culturais import MapasCulturaisClient, normalize_space
from app.services.sync_spaces import sync_spaces


async def sync_places(
    *,
    client: MapasCulturaisClient | None = None,
    input_path: Path | None = None,
    source_url: str | None = None,
) -> dict:
    """Run once and return the sync_runs summary; exceptions reach the caller.

    The adapter already normalizes API records to the project's places schema.
    Snapshots reuse that same normalizer. connect_database installs the shared
    MongoDB validators and indexes, and sync_spaces owns upserts and history.
    """
    spaces = None
    source = "api"
    if input_path is not None:
        if client is not None:
            raise ValueError("Use either an API client or a snapshot, not both")
        if not source_url:
            raise ValueError("--input requires --source-url identifying the instance")
        base_url = str(TypeAdapter(HttpUrl).validate_python(source_url)).rstrip("/")
        content = await asyncio.to_thread(input_path.read_text, encoding="utf-8")
        rows = json.loads(content)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Snapshot must contain a JSON array of objects")
        spaces = [p for row in rows if (p := normalize_space(row, base_url))]
        source = "snapshot"
    elif source_url is not None:
        raise ValueError("--source-url requires --input")
    async with connect_database() as database:
        return await sync_spaces(database, client=client, spaces=spaces, source=source)


async def run(args) -> None:
    """CLI bridge retained for compatibility with app.scripts.sync_spaces."""
    result = await sync_places(input_path=args.input, source_url=args.source_url)
    print(json.dumps(result, default=str, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sincronizar espaços culturais de São Paulo"
    )
    parser.add_argument(
        "--input", type=Path, help="Amostra JSON bruta previamente coletada"
    )
    parser.add_argument("--source-url", help="URL da instância original da amostra")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
