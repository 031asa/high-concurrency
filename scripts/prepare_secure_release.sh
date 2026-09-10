#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python "$PROJECT_ROOT/main.py" version-info --write "$PROJECT_ROOT/build-version.json"
python "$PROJECT_ROOT/main.py" version-info --format meta --write "$PROJECT_ROOT/build-version.meta"
MVP_DIR="$PROJECT_ROOT/aeron_mvp"
if [[ -n "${SECURE_RELEASE_BUILD_CACHE:-}" ]]; then
    export AERON_MVP_DEPS_DIR="$SECURE_RELEASE_BUILD_CACHE/aeron-mvp-deps"
fi
source "$MVP_DIR/env.sh"

bash "$MVP_DIR/build.sh"
aeron_mvp_resolve_runtime_dependencies || {
    aeron_mvp_print_dependency_help
    exit 2
}

mkdir -p "$MVP_DIR/lib"
copy_runtime_jar() {
    local source=$1
    local destination=$2
    if [[ -e "$destination" \
        && "$(readlink -f -- "$source")" == "$(readlink -f -- "$destination")" ]]; then
        return 0
    fi
    cp -f -- "$source" "$destination"
}
copy_runtime_jar "$AERON_JAR" "$MVP_DIR/lib/aeron-all-1.51.0.jar"
copy_runtime_jar "$JEROMQ_JAR" "$MVP_DIR/lib/jeromq-0.6.0.jar"
test -f "$MVP_DIR/build/classes/com/ydtrader/mvp/AeronMvp.class"

printf 'SECURE_RELEASE_PREPARE result=SUCCESS classes=%s runtime_jars=%s\n' \
    "$MVP_DIR/build/classes" "$MVP_DIR/lib"
