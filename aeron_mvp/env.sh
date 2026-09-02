#!/usr/bin/env bash

# Shared, relocatable dependency discovery for source trees and release packages.
AERON_MVP_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
AERON_MVP_PROJECT_ROOT=$(cd -- "$AERON_MVP_DIR/.." && pwd)
AERON_MVP_DEPS_DIR=${AERON_MVP_DEPS_DIR:-"$AERON_MVP_PROJECT_ROOT/result/aeron-mvp-deps"}
if [[ "$AERON_MVP_DEPS_DIR" != /* ]]; then
    AERON_MVP_DEPS_DIR="$AERON_MVP_PROJECT_ROOT/$AERON_MVP_DEPS_DIR"
fi

AERON_MVP_JDK_DIR="$AERON_MVP_DEPS_DIR/jdk-17.0.19+10"
AERON_MVP_AERON_JAR="$AERON_MVP_DEPS_DIR/aeron-all-1.51.0.jar"
AERON_MVP_SBE_JAR="$AERON_MVP_DEPS_DIR/sbe-all-1.38.1.jar"
AERON_MVP_JEROMQ_JAR="$AERON_MVP_DEPS_DIR/jeromq-0.6.0.jar"

aeron_mvp_java_major() {
    local java_home=$1
    "$java_home/bin/java" -XshowSettings:properties -version 2>&1 \
        | awk -F= '/java.specification.version =/{gsub(/[[:space:]]/, "", $2); print $2; exit}' \
        | sed 's/^1\.//; s/\..*$//'
}

aeron_mvp_java_home_is_valid() {
    local java_home=$1
    local require_compiler=${2:-0}
    local major
    [[ -x "$java_home/bin/java" ]] || return 1
    if [[ "$require_compiler" == 1 && ! -x "$java_home/bin/javac" ]]; then
        return 1
    fi
    major=$(aeron_mvp_java_major "$java_home")
    [[ "$major" =~ ^[0-9]+$ && "$major" -ge 17 ]]
}

aeron_mvp_detect_java_home() {
    local require_compiler=${1:-0}
    local executable
    local candidate
    local -a candidates=()

    [[ -n "${AERON_MVP_JAVA_HOME:-}" ]] && candidates+=("$AERON_MVP_JAVA_HOME")
    if [[ "$require_compiler" == 0 && -x "$AERON_MVP_DIR/runtime/bin/java" ]]; then
        candidates+=("$AERON_MVP_DIR/runtime")
    fi
    [[ -n "${JAVA_HOME:-}" ]] && candidates+=("$JAVA_HOME")

    if [[ "$require_compiler" == 1 ]]; then
        executable=$(command -v javac 2>/dev/null || true)
    else
        executable=$(command -v java 2>/dev/null || true)
    fi
    if [[ -n "$executable" ]]; then
        executable=$(readlink -f -- "$executable")
        candidates+=("$(cd -- "$(dirname -- "$executable")/.." && pwd)")
    fi
    candidates+=("$AERON_MVP_JDK_DIR")

    for candidate in "${candidates[@]}"; do
        if aeron_mvp_java_home_is_valid "$candidate" "$require_compiler"; then
            JAVA_HOME=$candidate
            export JAVA_HOME
            return 0
        fi
    done
    return 1
}

aeron_mvp_resolve_aeron_jar() {
    local candidate
    for candidate in \
        "${AERON_JAR:-}" \
        "$AERON_MVP_DIR/lib/aeron-all-1.51.0.jar" \
        "$AERON_MVP_AERON_JAR"; do
        if [[ -n "$candidate" && -f "$candidate" ]]; then
            AERON_JAR=$candidate
            export AERON_JAR
            return 0
        fi
    done
    return 1
}

aeron_mvp_resolve_sbe_jar() {
    local candidate
    for candidate in "${SBE_JAR:-}" "$AERON_MVP_SBE_JAR"; do
        if [[ -n "$candidate" && -f "$candidate" ]]; then
            SBE_JAR=$candidate
            export SBE_JAR
            return 0
        fi
    done
    return 1
}

aeron_mvp_resolve_jeromq_jar() {
    local candidate
    for candidate in \
        "${JEROMQ_JAR:-}" \
        "$AERON_MVP_DIR/lib/jeromq-0.6.0.jar" \
        "$AERON_MVP_JEROMQ_JAR"; do
        if [[ -n "$candidate" && -f "$candidate" ]]; then
            JEROMQ_JAR=$candidate
            export JEROMQ_JAR
            return 0
        fi
    done
    return 1
}

aeron_mvp_resolve_runtime_dependencies() {
    aeron_mvp_detect_java_home 0 \
        && aeron_mvp_resolve_aeron_jar \
        && aeron_mvp_resolve_jeromq_jar
}

aeron_mvp_resolve_build_dependencies() {
    aeron_mvp_detect_java_home 1 \
        && aeron_mvp_resolve_aeron_jar \
        && aeron_mvp_resolve_sbe_jar \
        && aeron_mvp_resolve_jeromq_jar
}

aeron_mvp_print_dependency_help() {
    printf 'Aeron MVP dependencies are not ready.\n' >&2
    printf 'Run: bash aeron_mvp/bootstrap.sh\n' >&2
    printf 'Or set JAVA_HOME, AERON_JAR and SBE_JAR explicitly.\n' >&2
    printf 'Project-local dependency directory: %s\n' "$AERON_MVP_DEPS_DIR" >&2
}
