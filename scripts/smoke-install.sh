#!/usr/bin/env sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT HUP INT TERM

python3 -m venv "$TEMP_ROOT/venv"
"$TEMP_ROOT/venv/bin/pip" install "$PROJECT_DIR"
cd "$TEMP_ROOT"
"$TEMP_ROOT/venv/bin/autofly" init --path config.yaml
"$TEMP_ROOT/venv/bin/autofly" config validate --config config.yaml
"$TEMP_ROOT/venv/bin/autofly" check --all --dry-run --config config.yaml
