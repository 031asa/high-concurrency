#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
INTEGRATION_DIR="$PROJECT_ROOT/aeron_mvp/leader_integration"
IMAGE=${1:-hpquant-market:1.0.0}

command -v docker >/dev/null 2>&1 || {
    printf 'docker is required\n' >&2
    exit 2
}
[[ -f "$INTEGRATION_DIR/validate_compiled_hook.py" ]] || {
    printf 'missing Leader integration directory: %s\n' "$INTEGRATION_DIR" >&2
    exit 2
}
docker image inspect "$IMAGE" >/dev/null

docker run --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=0,mode=0700 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 64 \
    --memory 256m \
    --mount "type=bind,src=$INTEGRATION_DIR,dst=/opt/ydtrader-leader-integration,readonly" \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --env PYTHONPATH=/opt/ydtrader-leader-integration:/opt/hpquant-market \
    --env HPQUANT_MARKET_SOURCE=aeron-zmq \
    --env HPQUANT_AERON_ZMQ_ENDPOINT=tcp://127.0.0.1:7101 \
    --entrypoint /opt/hpquant-market-env/bin/python \
    "$IMAGE" \
    /opt/ydtrader-leader-integration/validate_compiled_hook.py
