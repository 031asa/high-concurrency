#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
source "$PROJECT_ROOT/utils/conda_runtime.sh"

python_bin=$(ydtrader_validate_conda_python \
    "$PROJECT_ROOT" "${PYTHON_BIN:-}")

exec "$python_bin" "$PROJECT_ROOT/scripts/ydtrader.py" dashboard "$@"
