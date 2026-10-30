#!/usr/bin/env bash
# Local backend startup: MongoDB -> complete schema -> tests -> FastAPI.
set -Eeuo pipefail

SERVER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECK_ONLY=0
API_PID=""
MONGO_PID=""

die() { printf '\nErro: %s\n' "$*" >&2; exit 1; }
log() { printf '\n%s\n' "$*"; }

case "${1:-}" in
    "") ;;
    --check) CHECK_ONLY=1 ;;
    --help|-h)
        printf 'Uso: bash start.sh [--check]\n'
        printf 'MongoDB -> esquemas e indices -> testes completos -> API FastAPI.\n'
        printf -- '--check executa a preparacao e os testes sem iniciar a API.\n'
        exit 0 ;;
    *) die "Opcao desconhecida: $1. Use --help." ;;
esac
[[ $# -le 1 ]] || die "Informe no maximo uma opcao. Use --help."

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    for pid in "$API_PID" "$MONGO_PID"; do
        if [[ -n "$pid" ]]; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    for pid in "$API_PID" "$MONGO_PID"; do
        if [[ -n "$pid" ]]; then
            wait "$pid" 2>/dev/null || true
        fi
    done
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$SERVER_DIR"
export MONGODB_URI="${MONGODB_URI:-mongodb://localhost:27017}"
export MONGODB_DB_NAME="${MONGODB_DB_NAME:-spacos}"
export MONGODB_TEST_URI="${MONGODB_TEST_URI:-$MONGODB_URI}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"

log '[1/5] Preparando o ambiente Python'
if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
elif [[ -x .venv/Scripts/python.exe ]]; then
    PYTHON_BIN="$SERVER_DIR/.venv/Scripts/python.exe"
elif [[ -x .venv/bin/python ]]; then
    PYTHON_BIN="$SERVER_DIR/.venv/bin/python"
else
    PYTHON_BIN=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
    [[ -n "$PYTHON_BIN" ]] || die 'Instale Python 3.12 ou superior.'
    "$PYTHON_BIN" -m venv .venv
    if [[ -x .venv/Scripts/python.exe ]]; then
        PYTHON_BIN="$SERVER_DIR/.venv/Scripts/python.exe"
    else
        PYTHON_BIN="$SERVER_DIR/.venv/bin/python"
    fi
fi
"$PYTHON_BIN" -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
    die 'O ambiente selecionado precisa de Python 3.12 ou superior.'

if ! "$PYTHON_BIN" -c \
    'import fastapi, uvicorn, pytest, pytest_asyncio, pytest_cov, httpx, mongomock_motor; from pymongo import AsyncMongoClient' \
    >/dev/null 2>&1; then
    "$PYTHON_BIN" -m pip install -e '.[test]'
fi

if [[ "$CHECK_ONLY" == 0 ]]; then
    "$PYTHON_BIN" - "$API_HOST" "$API_PORT" <<'PY'
import socket
import sys

try:
    port = int(sys.argv[2])
    if not 1 <= port <= 65535:
        raise ValueError("porta fora do intervalo 1..65535")
    with socket.socket() as probe:
        probe.bind((sys.argv[1], port))
except (OSError, ValueError) as error:
    raise SystemExit(f"Nao foi possivel usar a porta da API: {error}. Configure API_PORT.")
PY
fi

mongo_ping() {
    "$PYTHON_BIN" - "$1" <<'PY'
import asyncio
import os
import sys

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

async def main():
    attempts = int(sys.argv[1])
    async with AsyncMongoClient(
        os.environ["MONGODB_URI"], serverSelectionTimeoutMS=1000, connectTimeoutMS=1000
    ) as client:
        for attempt in range(attempts):
            try:
                await client.admin.command("ping")
                return 0
            except PyMongoError as error:
                if attempt == attempts - 1:
                    print(f"MongoDB indisponivel ({type(error).__name__}).", file=sys.stderr)
                    return 1
                await asyncio.sleep(0.5)

raise SystemExit(asyncio.run(main()))
PY
}

log '[2/5] Verificando e iniciando o MongoDB'
if ! mongo_ping 1 >/dev/null 2>&1; then
    case "${MONGODB_URI%/}" in
        mongodb://localhost:27017|mongodb://127.0.0.1:27017) ;;
        *) die 'A instancia configurada em MONGODB_URI nao esta acessivel. Verifique a conexao.' ;;
    esac

    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            command -v powershell.exe >/dev/null 2>&1 ||
                die 'PowerShell nao encontrado para iniciar o servico MongoDB.'
            powershell.exe -NoProfile -NonInteractive -Command \
                '$ErrorActionPreference = "Stop"; $service = Get-Service -Name MongoDB -ErrorAction SilentlyContinue; if (-not $service) { throw "Instale MongoDB Community Server como servico do Windows." }; if ($service.Status -ne "Running") { Start-Service -Name MongoDB }' ||
                die 'Nao foi possivel iniciar MongoDB. Inicie o servico ou abra Git Bash como administrador.'
            ;;
        *)
            command -v mongod >/dev/null 2>&1 ||
                die 'Instale MongoDB Community Server e disponibilize mongod no PATH.'
            mkdir -p "$SERVER_DIR/.dev/mongodb"
            mongod --dbpath "$SERVER_DIR/.dev/mongodb" --bind_ip 127.0.0.1 \
                --port 27017 --logpath "$SERVER_DIR/.dev/mongodb.log" &
            MONGO_PID=$!
            ;;
    esac
    mongo_ping 30 || die 'O MongoDB nao ficou disponivel para inicializacao.'
fi

log '[3/5] Aplicando colecoes, validadores e indices'
"$PYTHON_BIN" -m app.scripts.init_db

log '[4/5] Executando testes unitarios e de integracao'
"$PYTHON_BIN" -m pytest

if [[ "$CHECK_ONLY" == 1 ]]; then
    log 'Banco inicializado e testes aprovados. A API nao foi iniciada (--check).'
    exit 0
fi

log "[5/5] Iniciando a API em http://$API_HOST:$API_PORT"
printf 'Documentacao: http://%s:%s/docs\n' "$API_HOST" "$API_PORT"
printf 'Use Ctrl+C para encerrar a API.\n'
"$PYTHON_BIN" -m uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" &
API_PID=$!
wait "$API_PID"
