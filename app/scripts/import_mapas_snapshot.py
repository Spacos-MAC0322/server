"""Import the last complete capture, optionally publishing its located places."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.database import connect_database
from app.integrations.mapas_culturais import normalize_space
from app.jobs.sync_places import sync_places
from app.scripts.export_mapas_spaces import checksum

DEFAULT_POINTER = Path("data/mapas_culturais/latest.json")


class InvalidSnapshotError(ValueError):
    """A safe diagnostic for a capture that must not be imported."""


def load_snapshot(pointer: Path) -> tuple[Path, str]:
    """Reject partial, changed or mismatched files before any database writes."""
    latest = json.loads(pointer.read_text(encoding="utf-8"))
    raw = Path(latest["raw_file"])
    manifest_path = Path(latest["manifest_file"])
    if not raw.is_absolute():
        raw = pointer.parent / raw
    if not manifest_path.is_absolute():
        manifest_path = pointer.parent / manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise InvalidSnapshotError(
            "Snapshot incompleto; execute collect_mapas.sh novamente"
        )
    expected = manifest.get("raw_sha256")
    if (
        not expected
        or checksum(raw) != expected
        or latest.get("raw_sha256") != expected
    ):
        raise InvalidSnapshotError("Hash do snapshot divergente; importacao cancelada")
    source_url = manifest["source_url"]
    if latest.get("source_url") != source_url:
        raise InvalidSnapshotError(
            "Origem do snapshot divergente; importacao cancelada"
        )
    return raw, source_url


async def import_snapshot(
    pointer: Path = DEFAULT_POINTER, *, publish_imported: bool = False
) -> dict:
    raw, source_url = await asyncio.to_thread(load_snapshot, pointer)
    result = await sync_places(input_path=raw, source_url=source_url)
    published = 0
    if publish_imported:
        # Match source keys instead of generated ids: upserts can retain an older
        # document identity when that source has already been imported.
        rows = json.loads(await asyncio.to_thread(raw.read_text, encoding="utf-8"))
        source_keys = {
            key
            for row in rows
            if (place := normalize_space(row, source_url)) is not None
            for key in place["source_keys"]
        }
        async with connect_database() as database:
            update = await database.places.update_many(
                {
                    "source_keys": {"$in": sorted(source_keys)},
                    "publication_status": "review",
                    "location.type": "Point",
                    "data_quality.location_valid": {"$ne": False},
                },
                {"$set": {"publication_status": "published"}},
            )
            published = update.modified_count
    return {"sync": result, "published": published}


def main() -> None:
    parser = argparse.ArgumentParser(description="Importar o ultimo snapshot completo")
    parser.add_argument("--latest", type=Path, default=DEFAULT_POINTER)
    parser.add_argument(
        "--publish-imported",
        action="store_true",
        help="Publicar locais importados em review com localizacao valida (teste)",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(
            import_snapshot(args.latest, publish_imported=args.publish_imported)
        )
    except InvalidSnapshotError as error:
        parser.exit(1, f"{error}\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(
            1,
            f"Importacao interrompida ({type(error).__name__}). "
            "Verifique o snapshot e a configuracao.\n",
        )
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
