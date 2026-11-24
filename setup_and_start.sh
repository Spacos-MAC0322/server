#!/usr/bin/env bash
# One-command local workflow; start.sh owns MongoDB and API lifecycles.
set -Eeuo pipefail

SERVER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_MODE=--refresh-data
START_ARGS=()
for argument in "$@"; do
    case "$argument" in
        --reuse-snapshot) DATA_MODE=--sync-latest ;;
        *) START_ARGS+=("$argument") ;;
    esac
done
if [[ " ${START_ARGS[*]} " == *" --help "* || " ${START_ARGS[*]} " == *" -h "* ]]; then
    printf 'Uso: bash setup_and_start.sh [--reuse-snapshot] [opcoes do start.sh]\n'
    printf 'Por padrao coleta, importa e inicia a API. --reuse-snapshot evita nova coleta.\n\n'
fi
exec bash "$SERVER_DIR/start.sh" "$DATA_MODE" "${START_ARGS[@]}"
