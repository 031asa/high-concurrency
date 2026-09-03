#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
ENV_PREFIX=${YDTRADER_CONDA_PREFIX:-"$HOME/miniconda3/envs/ydtrader-high-concurrency"}
COUNT_PER_SOURCE=${YDTRADER_CONTINUOUS_COUNT:-1000000000}
SYNC_LEVEL=${YDTRADER_SYNC_LEVEL:-0}

[[ -x "$ENV_PREFIX/bin/python" ]] || {
    printf 'YDTrader Conda environment is missing: %s\n' "$ENV_PREFIX" >&2
    exit 2
}

export CONDA_PREFIX="$ENV_PREFIX"
export PYTHON_BIN="$ENV_PREFIX/bin/python"
export PATH="$ENV_PREFIX/bin:$PATH"

cd "$PROJECT_ROOT"
exec bash "$PROJECT_ROOT/scripts/run_multi_source_aeron_mvp.sh" \
    --source-config \
        "$PROJECT_ROOT/config/market-sources/ctp-tts-7x24.json" \
        "$PROJECT_ROOT/config/market-sources/ctp-live-5level.json" \
    --count "$COUNT_PER_SOURCE" \
    --sync-level "$SYNC_LEVEL"
