"""Capture a reusable local snapshot without writing to MongoDB."""

import argparse
import asyncio
import hashlib
import json
import sys
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path

from bson import json_util

from app.core.config import Settings
from app.core.exceptions import ExternalIntegrationError
from app.integrations.mapas_culturais import (
    SPACE_FIELDS,
    MapasCulturaisClient,
    normalize_space,
)

DEFAULT_BASE_URL = "https://saas.mapas.cultura.gov.br"
SPACE_QUERY = {
    "@select": SPACE_FIELDS,
    "@order": "id ASC",
    "status": "EQ(1)",
    "En_Municipio": "ILIKE(São Paulo)",
}
COVERAGE_NOTE = (
    "Public records declaring São Paulo in En_Municipio; "
    "not a census of all city venues."
)


def write_json(path: Path, value) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cached_or_prepare(output: Path) -> dict | None:
    manifest = output / "manifest.json"
    if manifest.exists():
        report = json.loads(manifest.read_text(encoding="utf-8"))
        if checksum(output / "spaces_raw.json") != report["raw_sha256"]:
            raise ValueError("Cached snapshot checksum mismatch")
        return report
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Choose an empty output directory for a fresh capture")
    output.mkdir(parents=True, exist_ok=True)
    (output / "pages").mkdir(exist_ok=True)
    return None


async def write_snapshot(output: Path, rows: list, report: dict, base: str) -> dict:
    """Deduplicate, normalize and save the records with their manifest.

    Shared by network captures and manual downloads, so both produce the same
    files and statistics that import_mapas_snapshot validates.
    """
    manifest = output / "manifest.json"
    raw_file = output / "spaces_raw.json"
    unique = {str(row["id"]): row for row in rows}
    raw = list(unique.values())
    normalized, excluded = [], []
    for row in raw:
        place = normalize_space(row, base)
        if place is None:
            excluded.append(
                {
                    "id": row["id"],
                    "name": row.get("name"),
                    "reason": "not_eligible_for_published_sao_paulo_place",
                }
            )
        else:
            normalized.append(place)
    await asyncio.to_thread(write_json, raw_file, raw)
    await asyncio.to_thread(write_json, output / "excluded.json", excluded)
    # Extended JSON preserves BSON dates for optional MongoDB tooling.
    await asyncio.to_thread(
        (output / "places_normalized.json").write_text,
        json_util.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report.update(
        received=len(rows),
        unique_records=len(raw),
        duplicates=len(rows) - len(raw),
        normalized=len(normalized),
        excluded=len(excluded),
        with_location=sum(p["location"] is not None for p in normalized),
        with_opening_hours=sum(bool(p["opening_hours"]["text"]) for p in normalized),
        with_price=sum(bool(p["admission"]["price_text"]) for p in normalized),
        stale_source=sum(p["data_quality"]["stale_source"] for p in normalized),
        raw_sha256=await asyncio.to_thread(checksum, raw_file),
    )
    if isinstance(report["remote_count"], int) and report["complete"]:
        if report["unique_records"] != report["remote_count"]:
            report["complete"] = False
            report["stop_reason"] = "count_mismatch"
    await asyncio.to_thread(write_json, manifest, report)
    return report


async def export_spaces(
    output: Path,
    *,
    client: MapasCulturaisClient,
    page_size: int = 500,
    max_pages: int = 20,
) -> dict:
    """Cache-first export; use a new directory to request a fresh snapshot.

    All completed pages survive a network error. A capped or failed capture is
    explicitly incomplete, and its available raw records remain reusable offline.
    """
    if not 1 <= page_size <= 1000 or max_pages < 1:
        raise ValueError("page_size must be 1..1000 and max_pages must be positive")
    report = await asyncio.to_thread(cached_or_prepare, output)
    if report is not None:
        return report
    base = str(client.settings.mapas_culturais_base_url).rstrip("/")
    report = {
        "source_url": base,
        "captured_at": datetime.now(UTC).isoformat(),
        "query": {**SPACE_QUERY, "@limit": page_size},
        "max_pages": max_pages,
        "complete": False,
        "stop_reason": None,
        "remote_count": None,
        "pages": [],
        "errors": [],
        "coverage_note": COVERAGE_NOTE,
    }
    rows = []
    try:
        async with aclosing(client.iter_space_pages(page_size=page_size)) as pages:
            async for page in pages:
                path = output / "pages" / f"page_{page.number:04d}.json"
                await asyncio.to_thread(write_json, path, page.records)
                rows.extend(page.records)
                if report["remote_count"] is None:
                    report["remote_count"] = page.metadata.get("count")
                report["pages"].append(
                    {
                        "number": page.number,
                        "records": len(page.records),
                        "sha256": await asyncio.to_thread(checksum, path),
                        "metadata": page.metadata,
                    }
                )
                if len(report["pages"]) >= max_pages:
                    report["stop_reason"] = "page_limit"
                    break
                await asyncio.sleep(0.25)
            else:
                report["complete"] = True
                report["stop_reason"] = "end_of_results"
    except ExternalIntegrationError as error:
        report["errors"] = [type(error).__name__]
        report["stop_reason"] = "request_failed"
    return await write_snapshot(output, rows, report, base)


def read_manual_download(source: Path) -> list[dict]:
    """The JSON list that /api/space/find returns, as saved from the browser."""
    try:
        rows = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("O arquivo baixado não é um JSON válido.") from error
    if (
        not isinstance(rows, list)
        or not rows
        or not all(isinstance(row, dict) and "id" in row for row in rows)
    ):
        raise ValueError(
            "O arquivo deve ser a lista de espaços devolvida por /api/space/find."
        )
    return rows


async def snapshot_from_file(
    source: Path, output: Path, *, base_url: str = DEFAULT_BASE_URL
) -> dict:
    """Snapshot from a list downloaded in the browser, when scripts are blocked.

    The Mapas instance may answer scripts with a Cloudflare challenge while a
    browser gets the JSON. Whoever downloads asks for every record at once, so
    the capture counts as complete; without page metadata, remote_count stays
    unknown. The file is validated before the destination is created.
    """
    rows = await asyncio.to_thread(read_manual_download, source)
    if await asyncio.to_thread(cached_or_prepare, output) is not None:
        raise FileExistsError("Choose an empty output directory for a manual download")
    base = base_url.rstrip("/")
    stat = await asyncio.to_thread(source.stat)
    downloaded_at = datetime.fromtimestamp(stat.st_mtime, UTC)
    report = {
        "source_url": base,
        "captured_at": downloaded_at.isoformat(),
        "origin": "manual_download",
        "source_file": source.name,
        "source_sha256": await asyncio.to_thread(checksum, source),
        "query": dict(SPACE_QUERY),
        "max_pages": None,
        "complete": True,
        "stop_reason": "manual_download",
        "remote_count": None,
        "pages": [],
        "errors": [],
        "coverage_note": COVERAGE_NOTE,
    }
    return await write_snapshot(output, rows, report, base)


async def run(args) -> int:
    if args.from_file is not None:
        try:
            report = await snapshot_from_file(
                args.from_file, args.output, base_url=args.base_url
            )
        except (OSError, ValueError) as error:
            print(f"Arquivo não importado: {error}", file=sys.stderr)
            return 1
    else:
        settings = Settings(
            mapas_culturais_enabled=True,
            mapas_culturais_base_url=args.base_url,
            external_api_timeout_seconds=args.timeout,
        )
        report = await export_spaces(
            args.output,
            client=MapasCulturaisClient(settings),
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Salvar coleta local do Mapas Culturais"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--from-file",
        type=Path,
        help="JSON baixado pelo navegador; dispensa a coleta pela rede",
    )
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
