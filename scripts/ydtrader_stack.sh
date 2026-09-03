#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
UNIT_SOURCE="$PROJECT_ROOT/deploy/systemd"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TARGET=ydtrader-stack.target
SERVICES=(ydtrader-market.service ydtrader-dashboard.service)

usage() {
    printf 'Usage: %s {install-start|start|stop|restart|status|logs|hold}\n' "$0" >&2
    exit 64
}

require_user_systemd() {
    [[ "$(ps -p 1 -o comm=)" == systemd ]] || {
        printf 'systemd is not PID 1; enable systemd for this WSL distribution first\n' >&2
        exit 2
    }
    systemctl --user show-environment >/dev/null
}

install_units() {
    case "$PROJECT_ROOT" in
        *'|'*|*'&'*|*'%'*)
            printf 'unsupported project path for systemd template: %s\n' "$PROJECT_ROOT" >&2
            exit 2
            ;;
    esac
    mkdir -p "$UNIT_DIR"
    local unit temporary
    for unit in "$TARGET" "${SERVICES[@]}"; do
        temporary="$UNIT_DIR/$unit.tmp"
        sed "s|@PROJECT_ROOT@|$PROJECT_ROOT|g" "$UNIT_SOURCE/$unit" >"$temporary"
        chmod 0644 "$temporary"
        mv "$temporary" "$UNIT_DIR/$unit"
    done
    systemctl --user daemon-reload
    systemctl --user enable "$TARGET"
}

start_stack() {
    mkdir -p "$PROJECT_ROOT/logs" "$PROJECT_ROOT/result/aeron-mvp"
    systemctl --user reset-failed "${SERVICES[@]}" || true
    systemctl --user start "$TARGET" "${SERVICES[@]}"
}

stop_stack() {
    systemctl --user stop "$TARGET"
}

hold_stack() {
    # A foreground wsl.exe client keeps WSL alive; systemd services alone do not.
    # Lock per project prevents repeated desktop clicks from accumulating holders.
    mkdir -p "$PROJECT_ROOT/result"
    exec 9>"$PROJECT_ROOT/result/stack-keepalive.lock"
    flock -n 9 || return 0
    while systemctl --user is-active --quiet "$TARGET"; do
        sleep 5
    done
}

status_stack() {
    systemctl --user --no-pager --full status "$TARGET" "${SERVICES[@]}"
}

require_user_systemd
case "${1:-}" in
    install-start)
        install_units
        start_stack
        status_stack
        ;;
    start)
        start_stack
        status_stack
        ;;
    stop)
        stop_stack
        ;;
    restart)
        systemctl --user reset-failed "${SERVICES[@]}" || true
        systemctl --user restart "$TARGET"
        systemctl --user restart "${SERVICES[@]}"
        status_stack
        ;;
    status)
        status_stack
        ;;
    logs)
        exec journalctl --user -u ydtrader-market.service \
            -u ydtrader-dashboard.service -f
        ;;
    hold)
        hold_stack
        ;;
    *)
        usage
        ;;
esac
