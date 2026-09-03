#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
MVP_DIR="$PROJECT_ROOT/aeron_mvp"
RUN_JAVA=(bash "$MVP_DIR/run_java.sh")
source "$PROJECT_ROOT/utils/conda_runtime.sh"

sources=synthetic:sim-a,synthetic:sim-b
declare -a source_config_files=()
legacy_source_option_used=0
count=100000
sync_level=0
base_udp_port=$((24000 + ($$ % 10000)))
source_timeout=60
synthetic_repeat=1000
ctp_front=tcp://trading.openctp.cn:30011
ctp_api_kind=tts
ctp_latency_mode=historical_replay
ctp_instruments=IF2609,IC2609,IH2609,IM2609
ctp_repeat=10000
ctp_runtime_root=${CTP_TTS_RUNTIME_DIR:-"$PROJECT_ROOT/result/ctp-tts-runtime"}
ctp_python=${CTP_PYTHON:-"$ctp_runtime_root/conda/bin/python"}
ydapi_instrument=IF2609
ydapi_repeat=10000
ydapi_python=${YDAPI_PYTHON:-}
ydapi_account_config="$PROJECT_ROOT/config/account.json"
ydapi_api_config="$PROJECT_ROOT/config/ydClient.ini"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-config) source_config_files+=("$2"); shift 2 ;;
        --sources) sources=$2; legacy_source_option_used=1; shift 2 ;;
        --count) count=$2; shift 2 ;;
        --sync-level) sync_level=$2; shift 2 ;;
        --base-udp-port) base_udp_port=$2; shift 2 ;;
        --source-timeout-seconds) source_timeout=$2; legacy_source_option_used=1; shift 2 ;;
        --synthetic-repeat) synthetic_repeat=$2; legacy_source_option_used=1; shift 2 ;;
        --ctp-front) ctp_front=$2; legacy_source_option_used=1; shift 2 ;;
        --ctp-api-kind) ctp_api_kind=$2; legacy_source_option_used=1; shift 2 ;;
        --ctp-latency-mode) ctp_latency_mode=$2; legacy_source_option_used=1; shift 2 ;;
        --ctp-instruments) ctp_instruments=$2; legacy_source_option_used=1; shift 2 ;;
        --ctp-repeat) ctp_repeat=$2; legacy_source_option_used=1; shift 2 ;;
        --ctp-python) ctp_python=$2; legacy_source_option_used=1; shift 2 ;;
        --ydapi-instrument|--instrument) ydapi_instrument=$2; legacy_source_option_used=1; shift 2 ;;
        --ydapi-repeat) ydapi_repeat=$2; legacy_source_option_used=1; shift 2 ;;
        --ydapi-python) ydapi_python=$2; legacy_source_option_used=1; shift 2 ;;
        --account-config|--ydapi-account-config) ydapi_account_config=$2; legacy_source_option_used=1; shift 2 ;;
        --api-config|--ydapi-api-config) ydapi_api_config=$2; legacy_source_option_used=1; shift 2 ;;
        *) printf 'unknown option: %s\n' "$1" >&2; exit 64 ;;
    esac
done

case "$count" in ''|*[!0-9]*) printf -- '--count must be a positive integer\n' >&2; exit 64 ;; esac
case "$base_udp_port" in ''|*[!0-9]*) printf -- '--base-udp-port must be an integer\n' >&2; exit 64 ;; esac
case "$source_timeout" in ''|*[!0-9]*) printf -- '--source-timeout-seconds must be a positive integer\n' >&2; exit 64 ;; esac
case "$synthetic_repeat" in ''|*[!0-9]*) printf -- '--synthetic-repeat must be a positive integer\n' >&2; exit 64 ;; esac
case "$ctp_repeat" in ''|*[!0-9]*) printf -- '--ctp-repeat must be a positive integer\n' >&2; exit 64 ;; esac
case "$ydapi_repeat" in ''|*[!0-9]*) printf -- '--ydapi-repeat must be a positive integer\n' >&2; exit 64 ;; esac
[[ "$count" -gt 0 ]] || { printf -- '--count must be positive\n' >&2; exit 64; }
[[ "$base_udp_port" -ge 1 && "$base_udp_port" -le 65535 ]] || { printf -- '--base-udp-port must be between 1 and 65535\n' >&2; exit 64; }
[[ "$source_timeout" -gt 0 ]] || { printf -- '--source-timeout-seconds must be positive\n' >&2; exit 64; }
[[ "$synthetic_repeat" -ge 1 && "$synthetic_repeat" -le 1000000 ]] || { printf -- '--synthetic-repeat must be between 1 and 1000000\n' >&2; exit 64; }
[[ "$ctp_repeat" -ge 1 && "$ctp_repeat" -le 1000000 ]] || { printf -- '--ctp-repeat must be between 1 and 1000000\n' >&2; exit 64; }
[[ "$ydapi_repeat" -ge 1 && "$ydapi_repeat" -le 1000000 ]] || { printf -- '--ydapi-repeat must be between 1 and 1000000\n' >&2; exit 64; }
[[ "$sync_level" =~ ^[012]$ ]] || { printf -- '--sync-level must be 0, 1, or 2\n' >&2; exit 64; }
[[ "$ctp_api_kind" == tts || "$ctp_api_kind" == official ]] || {
    printf -- '--ctp-api-kind must be tts or official\n' >&2
    exit 64
}
[[ "$ctp_latency_mode" == historical_replay || "$ctp_latency_mode" == live ]] || {
    printf -- '--ctp-latency-mode must be historical_replay or live\n' >&2
    exit 64
}

if [[ "${#source_config_files[@]}" -gt 0 && "$legacy_source_option_used" -eq 1 ]]; then
    printf -- '--source-config cannot be combined with legacy source-specific options\n' >&2
    exit 64
fi

trim() {
    local value=$1
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

declare -a source_kinds=()
declare -a source_names=()
declare -a source_instruments=()
declare -a source_instrument_lists=()
declare -a source_repeats=()
declare -a source_pythons=()
declare -a source_fronts=()
declare -a source_api_kinds=()
declare -a source_latency_modes=()
declare -a source_account_configs=()
declare -a source_api_configs=()
declare -a source_startup_timeouts=()
declare -a source_idle_timeouts=()
declare -a source_config_paths=()
declare -a source_ctp_locale_roots=()
source_config_mode=legacy
python_bin=$(ydtrader_validate_conda_python "$PROJECT_ROOT" "${YDTRADER_PYTHON:-}")

if [[ "${#source_config_files[@]}" -gt 0 ]]; then
    source_config_mode=independent
    normalized_configs=$(mktemp)
    config_command=(
        "$python_bin" "$MVP_DIR/source_config.py"
        --project-root "$PROJECT_ROOT"
    )
    for config_path in "${source_config_files[@]}"; do
        config_command+=(--config "$config_path")
    done
    if ! "${config_command[@]}" >"$normalized_configs"; then
        rm -f "$normalized_configs"
        exit 64
    fi
    mapfile -d '' -t config_values <"$normalized_configs"
    rm -f "$normalized_configs"
    field_count=14
    [[ $((${#config_values[@]} % field_count)) -eq 0 ]] || {
        printf 'invalid normalized source configuration output\n' >&2
        exit 70
    }
    for ((offset = 0; offset < ${#config_values[@]}; offset += field_count)); do
        source_names+=("${config_values[offset]}")
        source_kinds+=("${config_values[offset + 1]}")
        source_instruments+=("${config_values[offset + 2]}")
        source_instrument_lists+=("${config_values[offset + 3]}")
        source_repeats+=("${config_values[offset + 4]}")
        source_pythons+=("${config_values[offset + 5]}")
        source_fronts+=("${config_values[offset + 6]}")
        source_api_kinds+=("${config_values[offset + 7]}")
        source_latency_modes+=("${config_values[offset + 8]}")
        source_account_configs+=("${config_values[offset + 9]}")
        source_api_configs+=("${config_values[offset + 10]}")
        source_startup_timeouts+=("${config_values[offset + 11]}")
        source_idle_timeouts+=("${config_values[offset + 12]}")
        source_config_paths+=("${config_values[offset + 13]}")
    done
    source_timeout=0
    for configured_timeout in "${source_idle_timeouts[@]}"; do
        ((configured_timeout > source_timeout)) && source_timeout=$configured_timeout
    done
else
    declare -A seen_names=()
    IFS=',' read -r -a requested_sources <<<"$sources"
    for requested in "${requested_sources[@]}"; do
        requested=$(trim "$requested")
        [[ -n "$requested" ]] || continue
        kind=${requested%%:*}
        if [[ "$requested" == *:* ]]; then
            name=${requested#*:}
        else
            name=$kind
        fi
        [[ "$kind" == synthetic || "$kind" == ctp || "$kind" == ydapi ]] || {
            printf 'unsupported source kind: %s\n' "$kind" >&2
            exit 64
        }
        [[ "$name" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || {
            printf 'invalid source name: %s\n' "$name" >&2
            exit 64
        }
        [[ -z "${seen_names[$name]:-}" ]] || {
            printf 'duplicate source name: %s\n' "$name" >&2
            exit 64
        }
        seen_names[$name]=1
        source_kinds+=("$kind")
        source_names+=("$name")
        source_config_paths+=("")
        source_idle_timeouts+=("$source_timeout")
        if [[ "$kind" == synthetic ]]; then
            source_instruments+=("IC$((2609 + ${#source_names[@]} - 1))")
            source_instrument_lists+=("")
            source_repeats+=("$synthetic_repeat")
            source_pythons+=("$python_bin")
            source_fronts+=("")
            source_api_kinds+=("")
            source_latency_modes+=("live")
            source_account_configs+=("")
            source_api_configs+=("")
            source_startup_timeouts+=("$source_timeout")
        elif [[ "$kind" == ctp ]]; then
            source_instruments+=("")
            source_instrument_lists+=("$ctp_instruments")
            source_repeats+=("$ctp_repeat")
            source_pythons+=("$ctp_python")
            source_fronts+=("$ctp_front")
            source_api_kinds+=("$ctp_api_kind")
            source_latency_modes+=("$ctp_latency_mode")
            source_account_configs+=("")
            source_api_configs+=("")
            source_startup_timeouts+=("$source_timeout")
        else
            source_instruments+=("$ydapi_instrument")
            source_instrument_lists+=("")
            source_repeats+=("$ydapi_repeat")
            source_pythons+=("$ydapi_python")
            source_fronts+=("")
            source_api_kinds+=("")
            source_latency_modes+=("live")
            source_account_configs+=("$ydapi_account_config")
            source_api_configs+=("$ydapi_api_config")
            source_startup_timeouts+=("$source_timeout")
        fi
    done
fi

[[ "${#source_names[@]}" -ge 2 ]] || {
    printf 'at least two enabled market sources are required\n' >&2
    exit 64
}
[[ $((base_udp_port + ${#source_names[@]})) -le 65535 ]] || {
    printf 'UDP port range exceeds 65535\n' >&2
    exit 64
}

for index in "${!source_kinds[@]}"; do
    kind=${source_kinds[$index]}
    if [[ "$kind" == ctp ]]; then
        configured_python=${source_pythons[$index]}
        if [[ -z "$configured_python" ]]; then
            if [[ "${source_api_kinds[$index]}" == tts ]]; then
                configured_python="$PROJECT_ROOT/result/ctp-tts-runtime/conda/bin/python"
            else
                configured_python="$PROJECT_ROOT/result/ctp-live-runtime/conda/bin/python"
            fi
        fi
        configured_python=$(ydtrader_validate_conda_python "$PROJECT_ROOT" "$configured_python")
        source_pythons[$index]=$configured_python
        ctp_runtime_root=$(cd -- "$(dirname -- "$configured_python")/../.." && pwd)
        ctp_locale_root="$ctp_runtime_root/locale"
        source_ctp_locale_roots[$index]=$ctp_locale_root
        if [[ ! -f "$ctp_locale_root/zh_CN.GB18030/LC_CTYPE" ]]; then
            command -v localedef >/dev/null || {
                printf 'localedef is required by the openctp-ctp Linux wheel\n' >&2
                exit 2
            }
            mkdir -p "$ctp_locale_root"
            localedef --no-archive -i zh_CN -f GB18030 "$ctp_locale_root/zh_CN.GB18030"
        fi
        ctp_api_version=$(LOCPATH="$ctp_locale_root" "$configured_python" -c \
            'from openctp_ctp import thostmduserapi as mdapi; print(mdapi.CThostFtdcMdApi.GetApiVersion())') || {
            printf 'CTP runtime is unavailable for %s: %s\n' "${source_names[$index]}" "$configured_python" >&2
            exit 2
        }
        if [[ "${source_api_kinds[$index]}" == tts ]]; then
            [[ "$ctp_api_version" == *"openctp-tts v6.7.11"* ]] || {
                printf 'wrong CTP native library for TTS source %s: %s\n' "${source_names[$index]}" "$ctp_api_version" >&2
                exit 2
            }
        else
            [[ "$ctp_api_version" != *"openctp-tts"* ]] || {
                printf 'official CTP source %s cannot use the TTS native library: %s\n' "${source_names[$index]}" "$ctp_api_version" >&2
                exit 2
            }
        fi
    elif [[ "$kind" == ydapi ]]; then
        configured_python=$(ydtrader_validate_conda_python "$PROJECT_ROOT" "${source_pythons[$index]}")
        source_pythons[$index]=$configured_python
        "$configured_python" -c 'import pyyd' >/dev/null 2>&1 || {
            printf 'YDApi runtime is unavailable for %s: %s\n' "${source_names[$index]}" "$configured_python" >&2
            exit 2
        }
        [[ -f "${source_account_configs[$index]}" && -f "${source_api_configs[$index]}" ]] || {
            printf 'YDApi account/API configuration is missing for %s\n' "${source_names[$index]}" >&2
            exit 2
        }
    fi
done

run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
run_started_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
run_dir="$PROJECT_ROOT/result/aeron-mvp/$run_id"
archive_dir="$run_dir/archive"
control_dir="$run_dir/control"
aeron_dir="/dev/shm/ydtrader-aeron-multi-${UID}-$$"
ready_file="$control_dir/server.ready"
mux_ready_file="$control_dir/mux.ready"
recording_file="$control_dir/recording.id"
total_count=$((count * ${#source_names[@]}))
source_csv=$(IFS=,; printf '%s' "${source_names[*]}")
has_live_latency=0
has_historical_latency=0
for index in "${!source_kinds[@]}"; do
    if [[ "${source_kinds[$index]}" == ctp && "${source_latency_modes[$index]}" == historical_replay ]]; then
        has_historical_latency=1
    else
        has_live_latency=1
    fi
done
if [[ "$has_historical_latency" == 1 && "$has_live_latency" == 1 ]]; then
    latency_mode=mixed
elif [[ "$has_historical_latency" == 1 ]]; then
    latency_mode=historical_replay
else
    latency_mode=live
fi
run_status=RUNNING
server_pid=
publisher_pid=
mux_pid=
compute_pid=
audit_pid=
declare -a bridge_pids=()

mkdir -p "$archive_dir" "$control_dir"
write_run_metadata() {
    local temporary="$run_dir/run.meta.tmp"
    {
        printf 'run_id=%s\nsource=multi\nsources=%s\nsource_config_mode=%s\nlatency_mode=%s\nexpected_count=%s\ncount_per_source=%s\nsync_level=%s\nstarted_at_utc=%s\nstatus=%s\nrecording_id=%s\n' \
            "$run_id" "$source_csv" "$source_config_mode" "$latency_mode" "$total_count" "$count" "$sync_level" \
            "$run_started_at_utc" "$run_status" "${recording_id:-}"
        if [[ "$source_config_mode" == independent ]]; then
            for index in "${!source_names[@]}"; do
                printf 'source_config.%s=%s\n' "${source_names[$index]}" "${source_config_paths[$index]}"
            done
        fi
    } >"$temporary"
    mv "$temporary" "$run_dir/run.meta"
}
write_run_metadata

stop_if_running() {
    local process_id=${1:-}
    if [[ -n "$process_id" ]] && kill -0 "$process_id" 2>/dev/null; then
        kill "$process_id" 2>/dev/null || true
        wait "$process_id" 2>/dev/null || true
    fi
}
cleanup() {
    for process_id in "${bridge_pids[@]:-}"; do stop_if_running "$process_id"; done
    stop_if_running "$mux_pid"
    stop_if_running "$audit_pid"
    stop_if_running "$compute_pid"
    stop_if_running "$publisher_pid"
    stop_if_running "$server_pid"
    if [[ "$run_status" == RUNNING ]]; then run_status=FAILED; write_run_metadata; fi
}
trap cleanup EXIT INT TERM

if [[ -f "$MVP_DIR/build.sh" ]]; then
    bash "$MVP_DIR/build.sh"
elif [[ ! -f "$MVP_DIR/build/classes/com/ydtrader/mvp/AeronMvp.class" ]]; then
    printf 'missing precompiled AeronMvp.class in release package\n' >&2
    exit 2
fi
"${RUN_JAVA[@]}" selftest | tee "$run_dir/selftest.log"
"${RUN_JAVA[@]}" server \
    --aeron-dir "$aeron_dir" --archive-dir "$archive_dir" \
    --ready-file "$ready_file" --sync-level "$sync_level" \
    >"$run_dir/server.log" 2>&1 &
server_pid=$!
for _ in $(seq 1 200); do
    [[ -f "$ready_file" ]] && break
    kill -0 "$server_pid" 2>/dev/null || exit 1
    sleep 0.05
done
[[ -f "$ready_file" ]] || { printf 'Aeron server readiness timed out\n' >&2; exit 1; }

"${RUN_JAVA[@]}" publish-adapter \
    --aeron-dir "$aeron_dir" --recording-file "$recording_file" \
    --count "$total_count" --adapter-name multi-source \
    --bind-host 127.0.0.1 --udp-port "$base_udp_port" \
    --source-timeout-seconds "$source_timeout" \
    >"$run_dir/publisher.log" 2>&1 &
publisher_pid=$!
for _ in $(seq 1 200); do
    [[ -s "$recording_file" ]] && break
    kill -0 "$publisher_pid" 2>/dev/null || exit 1
    sleep 0.05
done
[[ -s "$recording_file" ]] || { printf 'recording readiness timed out\n' >&2; exit 1; }
recording_id=$(tr -d '[:space:]' <"$recording_file")
write_run_metadata

declare -a mux_args=()
for index in "${!source_names[@]}"; do
    mux_args+=(--input "${source_names[$index]}=$((base_udp_port + index + 1))")
done
"$python_bin" "$MVP_DIR/multi_source_mux.py" \
    "${mux_args[@]}" --output-port "$base_udp_port" \
    --count-per-source "$count" --source-timeout-seconds "$source_timeout" \
    --ready-file "$mux_ready_file" >"$run_dir/mux.log" 2>&1 &
mux_pid=$!
for _ in $(seq 1 200); do
    [[ -f "$mux_ready_file" ]] && break
    kill -0 "$mux_pid" 2>/dev/null || exit 1
    sleep 0.05
done
[[ -f "$mux_ready_file" ]] || { printf 'multi-source mux readiness timed out\n' >&2; exit 1; }

"${RUN_JAVA[@]}" compute \
    --aeron-dir "$aeron_dir" --recording-id "$recording_id" \
    --expected-count "$total_count" --timeout-seconds "$source_timeout" \
    --summary-file "$run_dir/compute-live.summary" \
    --progress-file "$run_dir/compute-live.ndjson" --progress-interval-ms 250 \
    >"$run_dir/compute-live.log" 2>&1 &
compute_pid=$!
"${RUN_JAVA[@]}" audit \
    --aeron-dir "$aeron_dir" --recording-id "$recording_id" \
    --expected-count "$total_count" --timeout-seconds "$source_timeout" \
    --summary-file "$run_dir/audit-live.summary" \
    --progress-file "$run_dir/audit-live.ndjson" --progress-interval-ms 250 \
    >"$run_dir/audit-live.log" 2>&1 &
audit_pid=$!

for index in "${!source_names[@]}"; do
    kind=${source_kinds[$index]}
    name=${source_names[$index]}
    port=$((base_udp_port + index + 1))
    if [[ "$kind" == synthetic ]]; then
        "$python_bin" "$MVP_DIR/synthetic_bridge.py" \
            --udp-port "$port" --count "$count" --repeat "${source_repeats[$index]}" \
            --instrument "${source_instruments[$index]}" >"$run_dir/$name-bridge.log" 2>&1 &
    elif [[ "$kind" == ctp ]]; then
        LOCPATH="${source_ctp_locale_roots[$index]}" \
        "${source_pythons[$index]}" "$MVP_DIR/ctp_bridge.py" \
            --front "${source_fronts[$index]}" --instruments "${source_instrument_lists[$index]}" \
            --udp-host 127.0.0.1 --udp-port "$port" --repeat "${source_repeats[$index]}" \
            --flow-path "$control_dir/$name-ctp-flow" \
            --idle-timeout-seconds "${source_idle_timeouts[$index]}" >"$run_dir/$name-bridge.log" 2>&1 &
    else
        "${source_pythons[$index]}" "$MVP_DIR/ydapi_bridge.py" \
            --instrument "${source_instruments[$index]}" \
            --account-config "${source_account_configs[$index]}" --api-config "${source_api_configs[$index]}" \
            --udp-host 127.0.0.1 --udp-port "$port" --repeat "${source_repeats[$index]}" \
            --startup-timeout-seconds "${source_startup_timeouts[$index]}" \
            --idle-timeout-seconds "${source_idle_timeouts[$index]}" >"$run_dir/$name-bridge.log" 2>&1 &
    fi
    bridge_pids+=("$!")
done

while kill -0 "$mux_pid" 2>/dev/null; do
    for index in "${!bridge_pids[@]}"; do
        if ! kill -0 "${bridge_pids[$index]}" 2>/dev/null; then
            wait "${bridge_pids[$index]}" || true
            if [[ "${source_kinds[$index]}" != synthetic ]]; then
                printf 'source bridge exited early: %s\n' "${source_names[$index]}" >&2
                exit 1
            fi
        fi
    done
    sleep 0.05
done
wait "$mux_pid"
mux_pid=
wait "$publisher_pid"
publisher_pid=
for process_id in "${bridge_pids[@]}"; do stop_if_running "$process_id"; done
bridge_pids=()
wait "$compute_pid"; compute_pid=
wait "$audit_pid"; audit_pid=

"${RUN_JAVA[@]}" compute \
    --aeron-dir "$aeron_dir" --recording-id "$recording_id" \
    --expected-count "$total_count" --timeout-seconds "$source_timeout" --offline \
    --summary-file "$run_dir/compute-replay.summary" >"$run_dir/compute-replay.log" 2>&1
cmp --silent "$run_dir/compute-live.summary" "$run_dir/compute-replay.summary"
grep -q '^status=SUCCESS$' "$run_dir/compute-live.summary"
grep -q '^status=SUCCESS$' "$run_dir/audit-live.summary"

run_status=SUCCESS
write_run_metadata
printf 'AERON_MULTI_SOURCE result=SUCCESS sources=%s count_per_source=%s total=%s recording_id=%s\n' \
    "$source_csv" "$count" "$total_count" "$recording_id"
printf 'AERON_MULTI_SOURCE live_compute=SUCCESS live_audit=SUCCESS replay_match=YES\n'
printf 'AERON_MVP_RESULT_DIR %s\n' "$run_dir"
tail -n 1 "$run_dir/mux.log"
