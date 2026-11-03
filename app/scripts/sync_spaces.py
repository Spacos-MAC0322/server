"""Run explicitly: python -m app.scripts.sync_spaces [--input snapshot.json]."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.database import connect_database
from app.integrations.mapas_culturais import normalize_space
from app.services.sync_spaces import sync_spaces


async def run(args):
    spaces = None
    source = "api"
    if args.input:
        if not args.source_url:
            raise ValueError(
                "--input requires --source-url identifying the original instance"
            )
        rows = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Snapshot must contain a JSON array of objects")
        spaces = [p for r in rows if (p := normalize_space(r, args.source_url))]
        source = "snapshot"
    async with connect_database() as database:
        result = await sync_spaces(database, spaces=spaces, source=source)
    print(json.dumps(result, default=str, ensure_ascii=False))


def main():
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
