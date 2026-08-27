#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)

if [[ -n "${PYTHON_BIN:-}" ]]; then
    python_bin=$PYTHON_BIN
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
    python_bin="$CONDA_PREFIX/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    python_bin=$(command -v python3)
else
    printf 'Python 3 is required. Create the Conda environment with: conda env create -f environment.yml\n' >&2
    exit 2
fi

exec "$python_bin" "$PROJECT_ROOT/dashboard/server.py" "$@"
