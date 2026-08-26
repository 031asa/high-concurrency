#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
JAVA_HOME=${JAVA_HOME:-/home/hello/.local/opt/jdk-17.0.19+10}
AERON_JAR=${AERON_JAR:-/home/hello/.cache/ydtrader-mvp/aeron-all-1.51.0.jar}
SBE_JAR=${SBE_JAR:-/home/hello/.cache/ydtrader-mvp/sbe-all-1.38.1.jar}
BUILD_DIR="$SCRIPT_DIR/build"
GENERATED_DIR="$BUILD_DIR/generated"
CLASSES_DIR="$BUILD_DIR/classes"

for required in "$JAVA_HOME/bin/java" "$JAVA_HOME/bin/javac" "$AERON_JAR" "$SBE_JAR"; do
    if [[ ! -f "$required" ]]; then
        printf 'missing MVP dependency: %s\n' "$required" >&2
        exit 2
    fi
done

mkdir -p "$GENERATED_DIR" "$CLASSES_DIR"

"$JAVA_HOME/bin/java" \
    --add-opens java.base/jdk.internal.misc=ALL-UNNAMED \
    -Dsbe.output.dir="$GENERATED_DIR" \
    -Dsbe.target.language=Java \
    -Dsbe.generate.ir=true \
    -jar "$SBE_JAR" \
    "$SCRIPT_DIR/schema/market-data.xml"

find "$SCRIPT_DIR/src" "$GENERATED_DIR" -type f -name '*.java' -print0 \
    | sort -z \
    | xargs -0 "$JAVA_HOME/bin/javac" \
        -encoding UTF-8 \
        -cp "$AERON_JAR" \
        -d "$CLASSES_DIR"

printf 'AERON_MVP_BUILD result=SUCCESS project_root=%s classes=%s\n' \
    "$PROJECT_ROOT" "$CLASSES_DIR"
