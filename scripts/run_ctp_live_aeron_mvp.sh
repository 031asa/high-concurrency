#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
CTP_LIVE_RUNTIME_DIR=${CTP_LIVE_RUNTIME_DIR:-"$PROJECT_ROOT/result/ctp-live-runtime"}
export CTP_LIVE_RUNTIME_DIR

ctp_python="$CTP_LIVE_RUNTIME_DIR/conda/bin/python"
bash "$SCRIPT_DIR/bootstrap_ctp_live.sh"

exec bash "$SCRIPT_DIR/run_aeron_mvp.sh" \
    --source ctp \
    --ctp-api-kind official \
    --ctp-latency-mode live \
    --ctp-front tcp://182.254.243.31:30011 \
    --ctp-python "$ctp_python" \
    "$@"
