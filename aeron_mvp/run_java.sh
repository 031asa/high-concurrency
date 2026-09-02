#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

if [[ ! -d "$SCRIPT_DIR/build/classes" ]]; then
    bash "$SCRIPT_DIR/build.sh"
fi

if ! aeron_mvp_resolve_runtime_dependencies; then
    aeron_mvp_print_dependency_help
    exit 2
fi

main_class=com.ydtrader.mvp.AeronMvp
if [[ "${1:-}" == leader-zmq ]]; then
    main_class=com.ydtrader.mvp.LeaderZmqAdapter
    shift
fi

exec "$JAVA_HOME/bin/java" \
    --add-opens java.base/jdk.internal.misc=ALL-UNNAMED \
    --add-opens java.base/java.util.zip=ALL-UNNAMED \
    -cp "$SCRIPT_DIR/build/classes:$AERON_JAR:$JEROMQ_JAR" \
    "$main_class" "$@"
