#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -n "${AERON_MVP_JAVA_HOME:-}" ]]; then
    JAVA_HOME=$AERON_MVP_JAVA_HOME
elif [[ -x "$SCRIPT_DIR/runtime/bin/java" ]]; then
    JAVA_HOME="$SCRIPT_DIR/runtime"
else
    JAVA_HOME=${JAVA_HOME:-/home/hello/.local/opt/jdk-17.0.19+10}
fi

if [[ -n "${AERON_JAR:-}" ]]; then
    AERON_RUNTIME_JAR=$AERON_JAR
elif [[ -f "$SCRIPT_DIR/lib/aeron-all-1.51.0.jar" ]]; then
    AERON_RUNTIME_JAR="$SCRIPT_DIR/lib/aeron-all-1.51.0.jar"
else
    AERON_RUNTIME_JAR=/home/hello/.cache/ydtrader-mvp/aeron-all-1.51.0.jar
fi

if [[ ! -d "$SCRIPT_DIR/build/classes" ]]; then
    bash "$SCRIPT_DIR/build.sh"
fi

exec "$JAVA_HOME/bin/java" \
    --add-opens java.base/jdk.internal.misc=ALL-UNNAMED \
    --add-opens java.base/java.util.zip=ALL-UNNAMED \
    -cp "$SCRIPT_DIR/build/classes:$AERON_RUNTIME_JAR" \
    com.ydtrader.mvp.AeronMvp "$@"
