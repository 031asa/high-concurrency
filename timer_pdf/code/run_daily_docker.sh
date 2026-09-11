#!/usr/bin/env bash
# Host scheduler entry. Container reads archives; never starts market/trading services.
set -euo pipefail

if [[ $# -lt 2 || ! "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ || ! "$2" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    printf 'Usage: bash run_daily_docker.sh CONTAINER FIRST_DATE [daily-report options]\n' >&2
    exit 64
fi
report_container=$1
report_first_date=$2
shift 2
command -v docker >/dev/null || { printf 'Docker CLI not found\n' >&2; exit 127; }
exec docker exec "$report_container" "${YDTRADER_REPORT_EXECUTABLE:-ydtrader}" daily-report \
    --date yesterday --catch-up-from "$report_first_date" "$@"
