#!/usr/bin/env bash
# Refresh an offline Mapas Culturais snapshot; MongoDB is not accessed.
set -Eeuo pipefail

SERVER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT=""
EXPORT_ARGS=()

die() { printf '\nErro: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'HELP'
Uso: bash collect_mapas.sh [opcoes]

Faz uma nova coleta em data/mapas_culturais/sp_<data-hora>_<id>/.
Atualiza data/mapas_culturais/latest.json somente se a coleta estiver completa.
Preserva coletas anteriores e dados parciais; nao importa nem publica no banco.

Opcoes:
  --output DIRETORIO  Destino novo ou vazio, relativo a server ou absoluto
  --base-url URL      Instancia (padrao: https://saas.mapas.cultura.gov.br)
  --page-size N       Registros por pagina, de 1 a 1000 (padrao: 500)
  --max-pages N       Limite de paginas (padrao: 20)
  --timeout SEGUNDOS  Tempo por requisicao (padrao: 30)
  --help, -h         Exibe esta ajuda

Requer Python 3.12+. Usa .venv ou cria esse ambiente; PYTHON permite escolher
outro interpretador. Instala as dependencias do projeto caso estejam faltando.
HELP
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --output)
            [[ $# -ge 2 && -n "$2" ]] || die '--output exige um diretorio.'
            OUTPUT="$2"
            shift 2
            ;;
        --base-url|--page-size|--max-pages|--timeout)
            [[ $# -ge 2 && -n "$2" ]] || die "$1 exige um valor."
            EXPORT_ARGS+=("$1" "$2")
            shift 2
            ;;
        *) die "Opcao desconhecida: $1. Use --help." ;;
    esac
done

cd "$SERVER_DIR"

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
if ! "$PYTHON_BIN" -c 'import app.scripts.export_mapas_spaces' >/dev/null 2>&1; then
    "$PYTHON_BIN" -m pip install -e .
fi

if [[ -z "$OUTPUT" ]]; then
    OUTPUT="$("$PYTHON_BIN" -c 'from datetime import UTC, datetime; from uuid import uuid4; print("data/mapas_culturais/sp_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8])')"
    OUTPUT="${OUTPUT//$'\r'/}"
fi

# The exporter reuses existing snapshots. This refresh command requires an empty
# destination so running it again always fetches new data, never cached results.
"$PYTHON_BIN" - "$OUTPUT" <<'PY'
import sys
from pathlib import Path

output = Path(sys.argv[1])
if output.exists() and (not output.is_dir() or any(output.iterdir())):
    raise SystemExit("Escolha um destino novo ou vazio; snapshots existentes sao preservados.")
PY

printf '\nColetando um novo snapshot em %s\n' "$OUTPUT"
if ! "$PYTHON_BIN" -m app.scripts.export_mapas_spaces --output "$OUTPUT" "${EXPORT_ARGS[@]}"; then
    die "Coleta nao concluida. Verifique os arquivos e o manifest em $OUTPUT. O ultimo snapshot completo nao foi substituido."
fi

"$PYTHON_BIN" - "$OUTPUT" <<'PY'
import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

output = Path(sys.argv[1]).resolve()
manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("complete") is not True:
    raise SystemExit("Snapshot incompleto: latest.json nao foi atualizado.")
root = Path("data/mapas_culturais")
root.mkdir(parents=True, exist_ok=True)
latest = {
    "snapshot_directory": str(output),
    "raw_file": str(output / "spaces_raw.json"),
    "manifest_file": str(output / "manifest.json"),
    **{key: manifest[key] for key in (
        "source_url", "captured_at", "unique_records", "raw_sha256"
    )},
}
# A unique temporary file avoids collisions between concurrent captures. The
# replacement happens only after the complete pointer has been written.
with NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                        prefix=".latest_", suffix=".tmp", delete=False) as temporary:
    json.dump(latest, temporary, ensure_ascii=False, indent=2)
Path(temporary.name).replace(root / "latest.json")
print(f"\nColeta completa: {manifest['unique_records']} registros unicos.")
print(f"Snapshot: {output}")
print(f"Referencia atualizada: {(root / 'latest.json').resolve()}")
print("O banco nao foi alterado. Use app.jobs.sync_places para importar o arquivo bruto.")
PY