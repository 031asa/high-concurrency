#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
CTP_TTS_RUNTIME_DIR=${CTP_TTS_RUNTIME_DIR:-"$PROJECT_ROOT/result/ctp-tts-runtime"}
export CTP_TTS_RUNTIME_DIR

ctp_python="$CTP_TTS_RUNTIME_DIR/venv/bin/python"
if [[ ! -x "$ctp_python" ]]; then
    bash "$SCRIPT_DIR/bootstrap_ctp_tts.sh"
fi

exec bash "$SCRIPT_DIR/run_aeron_mvp.sh" \
    --source ctp \
    --ctp-python "$ctp_python" \
    "$@"
