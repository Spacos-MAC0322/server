"""Exercise the shell refresh against a local HTTP fake, without MongoDB."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import get_database_settings


@pytest.fixture
def snapshot_command(tmp_path):
    if sys.platform == "win32":
        bash = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Git/bin/bash.exe"
        )
        if not bash.is_file():
            pytest.skip("Git Bash is required for the shell script test")
    else:
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("Bash is required for the shell script test")
    source = Path(__file__).parents[1]
    server = tmp_path / "backend with spaces" / "server"
    server.mkdir(parents=True)
    shutil.copytree(
        source / "app", server / "app", ignore=shutil.ignore_patterns("__pycache__")
    )
    for name in ("collect_mapas.sh", "setup_and_start.sh", "start.sh"):
        shutil.copyfile(source / name, server / name)
    environment = os.environ.copy()
    environment["PYTHON"] = Path(sys.executable).as_posix()

    def run(*args, script_name="collect_mapas.sh", env_overrides=None):
        return subprocess.run(
            [str(bash), (server / script_name).as_posix(), *args],
            cwd=tmp_path,
            env=environment | (env_overrides or {}),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )

    return server, run


@pytest.fixture
def source_api():
    state = {"id": 1, "fail_next_page": False, "requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"] += 1
            page = parse_qs(urlparse(self.path).query)["@page"][0]
            if page != "1" and state["fail_next_page"]:
                self.send_response(503)
                self.end_headers()
                return
            rows = (
                []
                if page != "1"
                else [
                    {
                        "id": state["id"],
                        "name": "Museu",
                        "status": 1,
                        "En_Municipio": "São Paulo",
                        "location": {"longitude": -46.63, "latitude": -23.55},
                    }
                ]
            )
            body = json.dumps(rows).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("API-Metadata", '{"count": 1}')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("failure", ["page_limit", "request_failed"])
def test_refresh_preserves_history_and_latest_after_incomplete_capture(
    snapshot_command, source_api, failure
):
    server, run = snapshot_command
    url, source = source_api
    root = server / "data/mapas_culturais"
    latest_file = root / "latest.json"

    first = run("--base-url", url)
    assert first.returncode == 0, first.stdout + first.stderr
    previous = json.loads(latest_file.read_text(encoding="utf-8"))
    previous_raw = Path(previous["raw_file"]).read_bytes()

    source["id"] = 2
    second = run("--base-url", url)
    assert second.returncode == 0, second.stdout + second.stderr
    latest = json.loads(latest_file.read_text(encoding="utf-8"))
    assert latest["snapshot_directory"] != previous["snapshot_directory"]
    assert (
        json.loads(Path(latest["raw_file"]).read_text(encoding="utf-8"))[0]["id"] == 2
    )
    assert Path(previous["raw_file"]).read_bytes() == previous_raw
    assert source["requests"] == 4
    assert not list(root.glob(".latest_*.tmp"))
    pointer = latest_file.read_bytes()

    source["fail_next_page"] = failure == "request_failed"
    partial = run(
        "--base-url",
        url,
        "--output",
        "data/mapas_culturais/partial capture",
        "--max-pages",
        "1" if failure == "page_limit" else "20",
    )
    assert partial.returncode != 0
    assert latest_file.read_bytes() == pointer

    report = json.loads(
        (root / "partial capture/manifest.json").read_text(encoding="utf-8")
    )
    assert report["complete"] is False
    assert report["stop_reason"] == failure
    assert report["unique_records"] == 1

    requests_before = source["requests"]
    repeated = run(
        "--base-url", url, "--output", "data/mapas_culturais/partial capture"
    )
    assert repeated.returncode != 0
    assert source["requests"] == requests_before
    assert latest_file.read_bytes() == pointer


def test_start_options_fail_before_preparing_or_changing_data(snapshot_command):
    server, run = snapshot_command
    help_result = run("--help", script_name="setup_and_start.sh")
    assert help_result.returncode == 0
    assert "--reuse-snapshot" in help_result.stdout
    for arguments in [
        ["--publish-imported"],
        ["--base-url", "https://example.com"],
    ]:
        result = run(*arguments, script_name="start.sh")
        assert result.returncode != 0
    assert not (server / "data").exists()


@pytest.mark.integration
async def test_setup_imports_publishes_reuses_and_stops_on_failed_capture(
    snapshot_command, source_api, real_db
):
    server, run = snapshot_command
    url, source = source_api
    tests = server / "tests"
    tests.mkdir()
    # The child startup must run tests, but must not recursively execute this
    # parent suite. Check that the isolated application exposes the expected API.
    (tests / "test_smoke.py").write_text(
        "from app.main import app\n"
        "def test_routes():\n"
        '    assert "/api/places" in app.openapi()["paths"]\n',
        encoding="utf-8",
    )
    environment = {
        "MONGO_URI": get_database_settings().mongodb_test_uri.get_secret_value(),
        "MONGODB_DB_NAME": real_db.name,
        "GEOSAMPA_ENABLED": "false",
        "MAPAS_CULTURAIS_ENABLED": "false",
        "NOMINATIM_ENABLED": "false",
    }

    async def setup(*args):
        return await asyncio.to_thread(
            run, *args, script_name="setup_and_start.sh", env_overrides=environment
        )

    first = await setup("--check", "--publish-imported", "--base-url", url)
    assert first.returncode == 0, first.stdout + first.stderr
    assert source["requests"] == 2
    assert (
        await real_db.places.count_documents({"publication_status": "published"}) == 1
    )
    pointer = server / "data/mapas_culturais/latest.json"
    previous = pointer.read_bytes()

    repeated = await setup("--check", "--reuse-snapshot", "--publish-imported")
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert source["requests"] == 2
    assert await real_db.places.count_documents({}) == 1
    assert await real_db.sync_runs.count_documents({"status": "success"}) == 2

    source["fail_next_page"] = True
    failed = await setup("--check", "--publish-imported", "--base-url", url)
    assert failed.returncode != 0
    assert pointer.read_bytes() == previous
    assert await real_db.places.count_documents({}) == 1
    assert await real_db.sync_runs.count_documents({}) == 2
