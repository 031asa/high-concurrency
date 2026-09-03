#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
MVP_DIR="$PROJECT_ROOT/aeron_mvp"
RUN_JAVA=(bash "$MVP_DIR/run_java.sh")
source "$PROJECT_ROOT/utils/conda_runtime.sh"

count=${1:-1000}
case "$count" in
    ''|*[!0-9]*) printf 'count must be a positive integer\n' >&2; exit 64 ;;
esac
[[ "$count" -gt 0 ]] || { printf 'count must be positive\n' >&2; exit 64; }

python_candidate=${YDTRADER_PYTHON:-}
if [[ -z "$python_candidate" ]]; then
    for candidate in \
        "$HOME/miniconda3/envs/$YDTRADER_CONDA_ENV_NAME/bin/python" \
        "$PROJECT_ROOT/result/ctp-tts-runtime/conda/bin/python" \
        "$PROJECT_ROOT/result/ctp-live-runtime/conda/bin/python"; do
        if [[ -x "$candidate" ]] && "$candidate" -c 'import zmq' >/dev/null 2>&1; then
            python_candidate=$candidate
            break
        fi
    done
fi
python_bin=$(ydtrader_validate_conda_python "$PROJECT_ROOT" "$python_candidate")
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
run_dir="$PROJECT_ROOT/result/zmq-market-smoke/$run_id"
archive_dir="$run_dir/archive"
control_dir="$run_dir/control"
aeron_dir="/dev/shm/ydtrader-zmq-market-${UID}-$$"
ready_file="$control_dir/server.ready"
recording_file="$control_dir/recording.id"
live_checkpoint="$control_dir/live.checkpoint"
replay_checkpoint="$control_dir/replay.checkpoint"
base_port=$((31000 + ($$ % 10000)))
live_endpoint="tcp://127.0.0.1:$base_port"
replay_endpoint="tcp://127.0.0.1:$((base_port + 1))"
server_pid=
publisher_pid=
egress_pid=
probe_pid=

mkdir -p "$archive_dir" "$control_dir"

stop_if_running() {
    local process_id=$1
    if [[ -n "$process_id" ]] && kill -0 "$process_id" 2>/dev/null; then
        kill "$process_id" 2>/dev/null || true
        wait "$process_id" 2>/dev/null || true
    fi
}

cleanup() {
    stop_if_running "$probe_pid"
    stop_if_running "$egress_pid"
    stop_if_running "$publisher_pid"
    stop_if_running "$server_pid"
}
trap cleanup EXIT INT TERM

AERON_MVP_AUTO_BOOTSTRAP=0 "$python_bin" -c 'import zmq' >/dev/null
if [[ -f "$MVP_DIR/build.sh" ]]; then
    bash "$MVP_DIR/build.sh"
elif [[ ! -f "$MVP_DIR/build/classes/com/ydtrader/mvp/ZmqMarketDataEgress.class" ]]; then
    printf 'missing precompiled ZmqMarketDataEgress.class in release package\n' >&2
    exit 2
fi

"${RUN_JAVA[@]}" server \
    --aeron-dir "$aeron_dir" \
    --archive-dir "$archive_dir" \
    --ready-file "$ready_file" \
    --sync-level 0 \
    >"$run_dir/server.log" 2>&1 &
server_pid=$!
for _ in $(seq 1 200); do
    [[ -f "$ready_file" ]] && break
    kill -0 "$server_pid" 2>/dev/null || {
        sed -n '1,200p' "$run_dir/server.log" >&2
        exit 1
    }
    sleep 0.05
done
[[ -f "$ready_file" ]] || { printf 'Aeron server readiness timed out\n' >&2; exit 1; }

"$python_bin" "$PROJECT_ROOT/scripts/ydtrader.py" zmq-probe \
    --endpoint "$live_endpoint" \
    --expected-count "$count" \
    --summary-file "$run_dir/live.summary.json" \
    >"$run_dir/live-probe.log" 2>&1 &
probe_pid=$!
"${RUN_JAVA[@]}" zmq-egress \
    --mode live \
    --aeron-dir "$aeron_dir" \
    --endpoint "$live_endpoint" \
    --expected-count "$count" \
    --timeout-seconds 30 \
    --checkpoint-file "$live_checkpoint" \
    >"$run_dir/live-egress.log" 2>&1 &
egress_pid=$!
sleep 0.2

"${RUN_JAVA[@]}" publish \
    --aeron-dir "$aeron_dir" \
    --recording-file "$recording_file" \
    --count "$count" \
    --warmup-ms 500 \
    --instrument IC2609 \
    >"$run_dir/publisher.log" 2>&1 &
publisher_pid=$!

wait "$publisher_pid"
publisher_pid=
wait "$egress_pid"
egress_pid=
wait "$probe_pid"
probe_pid=
recording_id=$(tr -d '[:space:]' <"$recording_file")

"$python_bin" "$PROJECT_ROOT/scripts/ydtrader.py" zmq-probe \
    --endpoint "$replay_endpoint" \
    --expected-count "$count" \
    --summary-file "$run_dir/replay.summary.json" \
    >"$run_dir/replay-probe.log" 2>&1 &
probe_pid=$!
"${RUN_JAVA[@]}" zmq-egress \
    --mode replay \
    --aeron-dir "$aeron_dir" \
    --recording-id "$recording_id" \
    --endpoint "$replay_endpoint" \
    --expected-count "$count" \
    --timeout-seconds 30 \
    --checkpoint-file "$replay_checkpoint" \
    >"$run_dir/replay-egress.log" 2>&1 &
egress_pid=$!

wait "$egress_pid"
egress_pid=
wait "$probe_pid"
probe_pid=

cmp --silent "$run_dir/live.summary.json" "$run_dir/replay.summary.json" || {
    diff -u "$run_dir/live.summary.json" "$run_dir/replay.summary.json" >&2 || true
    exit 1
}
grep -q "^recording_id=$recording_id$" "$replay_checkpoint"
grep -q "^sequence=$count$" "$replay_checkpoint"

printf 'ZMQ_MARKET_SMOKE result=SUCCESS count=%s recording_id=%s\n' "$count" "$recording_id"
printf 'ZMQ_MARKET_SMOKE live_replay_match=YES result_dir=%s\n' "$run_dir"
