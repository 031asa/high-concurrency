#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
ENV_PREFIX=${YDTRADER_CONDA_PREFIX:-"$HOME/miniconda3/envs/ydtrader-high-concurrency"}

[[ -x "$ENV_PREFIX/bin/python" ]] || {
    printf 'YDTrader Conda environment is missing: %s\n' "$ENV_PREFIX" >&2
    exit 2
}

export CONDA_PREFIX="$ENV_PREFIX"
export PYTHON_BIN="$ENV_PREFIX/bin/python"
export PATH="$ENV_PREFIX/bin:$PATH"

cd "$PROJECT_ROOT"
exec bash "$PROJECT_ROOT/scripts/run_dashboard.sh" \
    --host "${YDTRADER_DASHBOARD_HOST:-127.0.0.1}" \
    --port "${YDTRADER_DASHBOARD_PORT:-8080}"
