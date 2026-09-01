#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
source "$PROJECT_ROOT/utils/conda_runtime.sh"

YDAPI_PYTHON=$(ydtrader_validate_conda_python \
    "$PROJECT_ROOT" "${YDAPI_PYTHON:-}")
export YDAPI_PYTHON

exec bash "$SCRIPT_DIR/run_aeron_mvp.sh" \
    --source ydapi \
    "$@"
