#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

if [[ ! -d "$SCRIPT_DIR/build/classes" ]]; then
    printf 'Aeron Java classes are missing; rebuild the image or release package.\n' >&2
    exit 2
fi

if ! aeron_mvp_resolve_runtime_dependencies; then
    printf 'Aeron runtime dependencies are missing from the package; rebuild it.\n' >&2
    exit 2
fi

main_class=com.ydtrader.mvp.AeronMvp
if [[ "${1:-}" == zmq-egress ]]; then
    main_class=com.ydtrader.mvp.ZmqMarketDataEgress
    shift
fi

exec "$JAVA_HOME/bin/java" \
    --add-opens java.base/jdk.internal.misc=ALL-UNNAMED \
    --add-opens java.base/java.util.zip=ALL-UNNAMED \
    -cp "$SCRIPT_DIR/build/classes:$AERON_JAR:$JEROMQ_JAR" \
    "$main_class" "$@"
