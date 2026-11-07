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
    'import fastapi, uvicorn, pytest, pytest_asyncio, pytest_cov, httpx, mongomock_motor, pydantic_settings, tenacity, cachetools, bcrypt, jwt, email_validator; from pymongo import AsyncMongoClient' \
    >/dev/null 2>&1; then
    "$PYTHON_BIN" -m pip install -e '.[test]'
fi

# Read only non-secret values. Never source .env as executable shell code.
STARTUP_CONFIG="$("$PYTHON_BIN" -m app.scripts.startup_config)" ||
    die 'Configuracao invalida. Verifique as variaveis de ambiente e o arquivo .env.'
STARTUP_CONFIG="${STARTUP_CONFIG//$'\r'/}"
read -r API_HOST API_PORT DEFAULT_LOCAL_MONGO <<< "$STARTUP_CONFIG"

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
import sys

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from app.core.config import get_database_settings

async def main():
    attempts = int(sys.argv[1])
    settings = get_database_settings()
    async with AsyncMongoClient(
        settings.mongo_uri.get_secret_value(),
        serverSelectionTimeoutMS=1000, connectTimeoutMS=1000
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
    [[ "$DEFAULT_LOCAL_MONGO" == 1 ]] ||
        die 'A instancia configurada em MONGO_URI nao esta acessivel. Verifique a conexao.'

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
"$PYTHON_BIN" - <<'PY'
import os
import subprocess
import sys

from app.core.config import get_database_settings

settings = get_database_settings()
uri = settings.mongodb_test_uri or settings.mongo_uri
# A separate process measures coverage from the first application import.
# Pass credentials through its environment without printing them.
test_environment = os.environ.copy()
test_environment["MONGODB_TEST_URI"] = uri.get_secret_value()
raise SystemExit(subprocess.call([sys.executable, "-m", "pytest"], env=test_environment))
PY

if [[ "$CHECK_ONLY" == 1 ]]; then
    log 'Banco inicializado e testes aprovados. A API nao foi iniciada (--check).'
    exit 0
fi

# JWT_SECRET pode vir do terminal ou do .env; so o resultado (0/1) e exposto aqui.
JWT_SECRET_MISSING="$("$PYTHON_BIN" -c \
    'from app.core.config import get_settings; print(int(get_settings().jwt_secret is None))')" ||
    die 'Configuracao invalida. Verifique as variaveis de ambiente e o arquivo .env.'
if [[ "${JWT_SECRET_MISSING//$'\r'/}" == 1 ]]; then
    JWT_SECRET="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    export JWT_SECRET
    printf 'Aviso: JWT_SECRET nao definido no terminal nem no .env; usando um segredo temporario.\n'
    printf 'Os logins expiram quando a API reiniciar. Defina JWT_SECRET no .env para manter as sessoes.\n'
fi

log "[5/5] Iniciando a API em http://$API_HOST:$API_PORT"
printf 'Documentacao: http://%s:%s/docs\n' "$API_HOST" "$API_PORT"
printf 'Use Ctrl+C para encerrar a API.\n'
"$PYTHON_BIN" -m uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" &
API_PID=$!
wait "$API_PID"
