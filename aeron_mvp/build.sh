#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
source "$SCRIPT_DIR/env.sh"
BUILD_DIR="$SCRIPT_DIR/build"
GENERATED_DIR="$BUILD_DIR/generated"
CLASSES_DIR="$BUILD_DIR/classes"

if ! aeron_mvp_resolve_build_dependencies; then
    if [[ "${AERON_MVP_AUTO_BOOTSTRAP:-1}" == 1 ]]; then
        printf 'AERON_MVP_DEPENDENCIES state=BOOTSTRAPPING deps=%s\n' "$AERON_MVP_DEPS_DIR"
        bash "$SCRIPT_DIR/bootstrap.sh"
    fi
    aeron_mvp_resolve_build_dependencies || {
        aeron_mvp_print_dependency_help
        exit 2
    }
fi

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
