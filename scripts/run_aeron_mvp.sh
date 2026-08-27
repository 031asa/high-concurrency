#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
MVP_DIR="$PROJECT_ROOT/aeron_mvp"
RUN_JAVA=(bash "$MVP_DIR/run_java.sh")

count=100000
sync_level=0
warmup_ms=3000
source=synthetic
ctp_front=tcp://trading.openctp.cn:30011
ctp_instruments=IF2609,IC2609,IH2609,IM2609,rb2610,au2610,ag2612,cu2610,m2609,i2609,SR609,TA609,si2609,lc2609
ctp_repeat=10000
ctp_runtime_root=${CTP_TTS_RUNTIME_DIR:-"$PROJECT_ROOT/result/ctp-tts-runtime"}
ctp_python="$ctp_runtime_root/venv/bin/python"
ctp_udp_port=$((20000 + ($$ % 20000)))
ctp_locale_root="$ctp_runtime_root/locale"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --count)
            count=$2
            shift 2
            ;;
        --sync-level)
            sync_level=$2
            shift 2
            ;;
        --warmup-ms)
            warmup_ms=$2
            shift 2
            ;;
        --source)
            source=$2
            shift 2
            ;;
        --ctp-front)
            ctp_front=$2
            shift 2
            ;;
        --ctp-instruments)
            ctp_instruments=$2
            shift 2
            ;;
        --ctp-repeat)
            ctp_repeat=$2
            shift 2
            ;;
        --ctp-python)
            ctp_python=$2
            shift 2
            ;;
        --ctp-udp-port)
            ctp_udp_port=$2
            shift 2
            ;;
        *)
            printf 'unknown option: %s\n' "$1" >&2
            exit 64
            ;;
    esac
done

case "$count" in
    ''|*[!0-9]*) printf -- '--count must be a positive integer\n' >&2; exit 64 ;;
esac
[[ "$count" -gt 0 ]] || { printf -- '--count must be greater than zero\n' >&2; exit 64; }
[[ "$sync_level" =~ ^[012]$ ]] || {
    printf -- '--sync-level must be 0, 1, or 2\n' >&2
    exit 64
}
case "$warmup_ms" in
    ''|*[!0-9]*) printf -- '--warmup-ms must be a non-negative integer\n' >&2; exit 64 ;;
esac
[[ "$source" == synthetic || "$source" == ctp ]] || {
    printf -- '--source must be synthetic or ctp\n' >&2
    exit 64
}
case "$ctp_repeat" in
    ''|*[!0-9]*) printf -- '--ctp-repeat must be a positive integer\n' >&2; exit 64 ;;
esac
[[ "$ctp_repeat" -ge 1 && "$ctp_repeat" -le 1000000 ]] || {
    printf -- '--ctp-repeat must be between 1 and 1000000\n' >&2
    exit 64
}
case "$ctp_udp_port" in
    ''|*[!0-9]*) printf -- '--ctp-udp-port must be an integer\n' >&2; exit 64 ;;
esac
[[ "$ctp_udp_port" -ge 1 && "$ctp_udp_port" -le 65535 ]] || {
    printf -- '--ctp-udp-port must be between 1 and 65535\n' >&2
    exit 64
}
if [[ "$source" == ctp ]]; then
    [[ -x "$ctp_python" ]] || {
        printf 'CTP Python is not executable: %s\n' "$ctp_python" >&2
        exit 2
    }
    if [[ ! -f "$ctp_locale_root/zh_CN.GB18030/LC_CTYPE" ]]; then
        command -v localedef >/dev/null || {
            printf 'localedef is required by the openctp-ctp Linux wheel\n' >&2
            exit 2
        }
        mkdir -p "$ctp_locale_root"
        localedef --no-archive \
            -i zh_CN \
            -f GB18030 \
            "$ctp_locale_root/zh_CN.GB18030"
    fi
    export LOCPATH="$ctp_locale_root"
    ctp_api_version=$("$ctp_python" -c \
        'from openctp_ctp import thostmduserapi as mdapi; print(mdapi.CThostFtdcMdApi.GetApiVersion())') || {
        printf 'OpenCTP TTS Python runtime cannot be imported: %s\n' "$ctp_python" >&2
        printf 'run: bash scripts/bootstrap_ctp_tts.sh\n' >&2
        exit 2
    }
    [[ "$ctp_api_version" == *"openctp-tts v6.7.11"* ]] || {
        printf 'wrong CTP native library: %s\n' "$ctp_api_version" >&2
        printf 'OpenCTP TTS requires its matching library; run: bash scripts/bootstrap_ctp_tts.sh\n' >&2
        exit 2
    }
fi

run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
run_started_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
run_dir="$PROJECT_ROOT/result/aeron-mvp/$run_id"
archive_dir="$run_dir/archive"
control_dir="$run_dir/control"
aeron_dir="/dev/shm/ydtrader-aeron-mvp-${UID}-$$"
ready_file="$control_dir/server.ready"
recording_file="$control_dir/recording.id"
server_pid=
publisher_pid=
compute_pid=
audit_pid=
bridge_pid=
run_status=RUNNING

mkdir -p "$archive_dir" "$control_dir"

write_run_metadata() {
    local metadata_tmp="$run_dir/run.meta.tmp"
    printf 'run_id=%s\nsource=%s\nexpected_count=%s\nsync_level=%s\nstarted_at_utc=%s\nstatus=%s\nrecording_id=%s\n' \
        "$run_id" "$source" "$count" "$sync_level" "$run_started_at_utc" "$run_status" \
        "${recording_id:-}" >"$metadata_tmp"
    mv "$metadata_tmp" "$run_dir/run.meta"
}

write_run_metadata

stop_if_running() {
    local process_id=$1
    if [[ -n "$process_id" ]] && kill -0 "$process_id" 2>/dev/null; then
        kill "$process_id" 2>/dev/null || true
        wait "$process_id" 2>/dev/null || true
    fi
}

cleanup() {
    stop_if_running "$bridge_pid"
    stop_if_running "$audit_pid"
    stop_if_running "$compute_pid"
    stop_if_running "$publisher_pid"
    stop_if_running "$server_pid"
    if [[ "$run_status" == RUNNING ]]; then
        run_status=FAILED
        write_run_metadata
    fi
}
trap cleanup EXIT INT TERM

if [[ ! -d "$MVP_DIR/build/classes" ]]; then
    bash "$MVP_DIR/build.sh"
fi
"${RUN_JAVA[@]}" selftest | tee "$run_dir/selftest.log"

"${RUN_JAVA[@]}" server \
    --aeron-dir "$aeron_dir" \
    --archive-dir "$archive_dir" \
    --ready-file "$ready_file" \
    --sync-level "$sync_level" \
    >"$run_dir/server.log" 2>&1 &
server_pid=$!

for _ in $(seq 1 200); do
    [[ -f "$ready_file" ]] && break
    kill -0 "$server_pid" 2>/dev/null || {
        printf 'Aeron server exited before readiness\n' >&2
        sed -n '1,200p' "$run_dir/server.log" >&2
        exit 1
    }
    sleep 0.05
done
[[ -f "$ready_file" ]] || { printf 'timed out waiting for Aeron server\n' >&2; exit 1; }

if [[ "$source" == ctp ]]; then
    "${RUN_JAVA[@]}" publish-ctp \
        --aeron-dir "$aeron_dir" \
        --recording-file "$recording_file" \
        --count "$count" \
        --bind-host 127.0.0.1 \
        --udp-port "$ctp_udp_port" \
        --source-timeout-seconds 60 \
        >"$run_dir/publisher.log" 2>&1 &
else
    "${RUN_JAVA[@]}" publish \
        --aeron-dir "$aeron_dir" \
        --recording-file "$recording_file" \
        --count "$count" \
        --warmup-ms "$warmup_ms" \
        --instrument IC2609 \
        >"$run_dir/publisher.log" 2>&1 &
fi
publisher_pid=$!

for _ in $(seq 1 200); do
    [[ -s "$recording_file" ]] && break
    kill -0 "$publisher_pid" 2>/dev/null || {
        printf 'publisher exited before creating recording\n' >&2
        sed -n '1,200p' "$run_dir/publisher.log" >&2
        exit 1
    }
    sleep 0.05
done
[[ -s "$recording_file" ]] || { printf 'timed out waiting for recording id\n' >&2; exit 1; }
recording_id=$(tr -d '[:space:]' <"$recording_file")
write_run_metadata

"${RUN_JAVA[@]}" compute \
    --aeron-dir "$aeron_dir" \
    --recording-id "$recording_id" \
    --expected-count "$count" \
    --timeout-seconds 60 \
    --summary-file "$run_dir/compute-live.summary" \
    --progress-file "$run_dir/compute-live.ndjson" \
    --progress-interval-ms 250 \
    >"$run_dir/compute-live.log" 2>&1 &
compute_pid=$!

"${RUN_JAVA[@]}" audit \
    --aeron-dir "$aeron_dir" \
    --recording-id "$recording_id" \
    --expected-count "$count" \
    --timeout-seconds 60 \
    --summary-file "$run_dir/audit-live.summary" \
    --progress-file "$run_dir/audit-live.ndjson" \
    --progress-interval-ms 250 \
    >"$run_dir/audit-live.log" 2>&1 &
audit_pid=$!

if [[ "$source" == ctp ]]; then
    "$ctp_python" "$MVP_DIR/ctp_bridge.py" \
        --front "$ctp_front" \
        --instruments "$ctp_instruments" \
        --udp-host 127.0.0.1 \
        --udp-port "$ctp_udp_port" \
        --repeat "$ctp_repeat" \
        --idle-timeout-seconds 60 \
        >"$run_dir/ctp-bridge.log" 2>&1 &
    bridge_pid=$!
    while kill -0 "$publisher_pid" 2>/dev/null; do
        if ! kill -0 "$bridge_pid" 2>/dev/null; then
            wait "$bridge_pid" || true
            bridge_pid=
            printf 'CTP bridge exited before publisher completed\n' >&2
            sed -n '1,240p' "$run_dir/ctp-bridge.log" >&2
            exit 1
        fi
        sleep 0.05
    done
fi

wait "$publisher_pid"
publisher_pid=
if [[ "$source" == ctp ]]; then
    stop_if_running "$bridge_pid"
    bridge_pid=
fi
wait "$compute_pid"
compute_pid=
wait "$audit_pid"
audit_pid=

"${RUN_JAVA[@]}" compute \
    --aeron-dir "$aeron_dir" \
    --recording-id "$recording_id" \
    --expected-count "$count" \
    --timeout-seconds 60 \
    --offline \
    --summary-file "$run_dir/compute-replay.summary" \
    >"$run_dir/compute-replay.log" 2>&1

cmp --silent "$run_dir/compute-live.summary" "$run_dir/compute-replay.summary" || {
    diff -u "$run_dir/compute-live.summary" "$run_dir/compute-replay.summary" >&2 || true
    printf 'live and offline statistics differ\n' >&2
    exit 1
}

grep -q '^status=SUCCESS$' "$run_dir/compute-live.summary"
grep -q '^status=SUCCESS$' "$run_dir/audit-live.summary"
find "$archive_dir" -type f -size +0c -print -quit | grep -q .

# Prove that the Archive catalog and recording survive a service restart.
stop_if_running "$server_pid"
server_pid=
restart_aeron_dir="${aeron_dir}-restart"
restart_ready_file="$control_dir/server-restart.ready"
"${RUN_JAVA[@]}" server \
    --aeron-dir "$restart_aeron_dir" \
    --archive-dir "$archive_dir" \
    --ready-file "$restart_ready_file" \
    --sync-level "$sync_level" \
    --reuse-archive \
    >"$run_dir/server-restart.log" 2>&1 &
server_pid=$!

for _ in $(seq 1 200); do
    [[ -f "$restart_ready_file" ]] && break
    kill -0 "$server_pid" 2>/dev/null || {
        printf 'restarted Aeron server exited before readiness\n' >&2
        sed -n '1,200p' "$run_dir/server-restart.log" >&2
        exit 1
    }
    sleep 0.05
done
[[ -f "$restart_ready_file" ]] || {
    printf 'timed out waiting for restarted Aeron server\n' >&2
    exit 1
}

# Interrupt a consumer process, then prove a fresh consumer can replay the same recording.
interrupted_expected=$((count + 1))
"${RUN_JAVA[@]}" compute \
    --aeron-dir "$restart_aeron_dir" \
    --recording-id "$recording_id" \
    --expected-count "$interrupted_expected" \
    --timeout-seconds 60 \
    --offline \
    --replay-stream-id 1104 \
    >"$run_dir/compute-interrupted.log" 2>&1 &
compute_pid=$!
for _ in $(seq 1 200); do
    grep -q 'state=REPLAY_CONNECTED' "$run_dir/compute-interrupted.log" && break
    kill -0 "$compute_pid" 2>/dev/null || {
        printf 'interruption probe exited before replay connection\n' >&2
        sed -n '1,200p' "$run_dir/compute-interrupted.log" >&2
        exit 1
    }
    sleep 0.05
done
grep -q 'state=REPLAY_CONNECTED' "$run_dir/compute-interrupted.log" || {
    printf 'timed out waiting for interruption probe replay connection\n' >&2
    exit 1
}
sleep 0.1
kill "$compute_pid"
wait "$compute_pid" 2>/dev/null || true
compute_pid=

"${RUN_JAVA[@]}" compute \
    --aeron-dir "$restart_aeron_dir" \
    --recording-id "$recording_id" \
    --expected-count "$count" \
    --timeout-seconds 60 \
    --offline \
    --replay-stream-id 1105 \
    --summary-file "$run_dir/compute-after-restart.summary" \
    >"$run_dir/compute-after-restart.log" 2>&1

cmp --silent "$run_dir/compute-live.summary" "$run_dir/compute-after-restart.summary" || {
    diff -u "$run_dir/compute-live.summary" "$run_dir/compute-after-restart.summary" >&2 || true
    printf 'statistics changed after Archive/consumer restart\n' >&2
    exit 1
}

run_status=SUCCESS
write_run_metadata

printf 'AERON_MVP_ACCEPTANCE result=SUCCESS recording_id=%s sent=%s sync_level=%s source=%s\n' \
    "$recording_id" "$count" "$sync_level" "$source"
printf 'AERON_MVP_ACCEPTANCE live_compute=SUCCESS live_audit=SUCCESS replay_match=YES\n'
printf 'AERON_MVP_ACCEPTANCE archive_restart=SUCCESS consumer_restart=SUCCESS\n'
printf 'AERON_MVP_RESULT_DIR %s\n' "$run_dir"

sed -n '$p' "$run_dir/publisher.log"
sed -n '$p' "$run_dir/compute-live.log"
sed -n '$p' "$run_dir/audit-live.log"
sed -n '$p' "$run_dir/compute-replay.log"
sed -n '$p' "$run_dir/compute-after-restart.log"
if [[ "$source" == ctp ]]; then
    sed -n '1p;$p' "$run_dir/ctp-bridge.log"
fi
