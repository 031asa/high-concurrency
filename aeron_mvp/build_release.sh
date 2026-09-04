#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
VERSION=0.8.0
PLATFORM=linux-x86_64
DIST_NAME="ydtrader-aeron-mvp-java-${VERSION}-${PLATFORM}"
RESULT_DIR="$PROJECT_ROOT/result"
DIST_DIR="$RESULT_DIR/$DIST_NAME"
ARCHIVE_FILE="$RESULT_DIR/$DIST_NAME.tar.gz"
ARCHIVE_HASH_FILE="$ARCHIVE_FILE.sha256"
source "$SCRIPT_DIR/env.sh"

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf 'release build requires Linux x86_64\n' >&2
    exit 2
fi
if ! aeron_mvp_resolve_build_dependencies; then
    if [[ "${AERON_MVP_AUTO_BOOTSTRAP:-1}" == 1 ]]; then
        bash "$SCRIPT_DIR/bootstrap.sh"
    fi
    aeron_mvp_resolve_build_dependencies || {
        aeron_mvp_print_dependency_help
        exit 2
    }
fi
for required in "$JAVA_HOME/bin/java" "$JAVA_HOME/bin/jlink" "$JAVA_HOME/bin/jar" "$AERON_JAR"; do
    if [[ ! -f "$required" ]]; then
        printf 'missing release dependency: %s\n' "$required" >&2
        exit 2
    fi
done
if [[ -e "$DIST_DIR" || -e "$ARCHIVE_FILE" || -e "$ARCHIVE_HASH_FILE" ]]; then
    printf 'release output already exists: %s\n' "$DIST_NAME" >&2
    exit 2
fi

mkdir -p "$RESULT_DIR"
STAGE_ROOT=$(mktemp -d -p "$RESULT_DIR" .aeron-java-release-XXXXXX)
STAGE_DIST="$STAGE_ROOT/$DIST_NAME"
mkdir -p \
    "$STAGE_DIST/aeron_mvp/build" \
    "$STAGE_DIST/aeron_mvp/lib" \
    "$STAGE_DIST/aeron_mvp/schema" \
    "$STAGE_DIST/config/market-sources" \
    "$STAGE_DIST/dashboard" \
    "$STAGE_DIST/ydcore" \
    "$STAGE_DIST/licenses" \
    "$STAGE_DIST/scripts" \
    "$STAGE_DIST/utils" \
    "$STAGE_DIST/vendor/wheels"

bash "$SCRIPT_DIR/build.sh"
# Runtime Shell launchers invoke the only plaintext Python entrypoint.
cp -a "$PROJECT_ROOT/main.py" "$STAGE_DIST/main.py"
cp -a "$PROJECT_ROOT/ydcore/"*.py "$STAGE_DIST/ydcore/"
cp -a "$SCRIPT_DIR/__init__.py" "$STAGE_DIST/aeron_mvp/__init__.py"
cp -a "$SCRIPT_DIR/build/classes" "$STAGE_DIST/aeron_mvp/build/classes"
cp -a "$SCRIPT_DIR/run_java.sh" "$STAGE_DIST/aeron_mvp/run_java.sh"
cp -a "$SCRIPT_DIR/env.sh" "$STAGE_DIST/aeron_mvp/env.sh"
cp -a "$SCRIPT_DIR/ctp_bridge.py" "$STAGE_DIST/aeron_mvp/ctp_bridge.py"
cp -a "$SCRIPT_DIR/ctp_cli.py" "$STAGE_DIST/aeron_mvp/ctp_cli.py"
cp -a "$SCRIPT_DIR/ydapi_bridge.py" "$STAGE_DIST/aeron_mvp/ydapi_bridge.py"
cp -a "$SCRIPT_DIR/multi_source_mux.py" "$STAGE_DIST/aeron_mvp/multi_source_mux.py"
cp -a "$SCRIPT_DIR/source_config.py" "$STAGE_DIST/aeron_mvp/source_config.py"
cp -a "$SCRIPT_DIR/synthetic_bridge.py" "$STAGE_DIST/aeron_mvp/synthetic_bridge.py"
cp -a "$SCRIPT_DIR/market_wire.py" "$STAGE_DIST/aeron_mvp/market_wire.py"
cp -a "$SCRIPT_DIR/zmq_market_probe.py" "$STAGE_DIST/aeron_mvp/zmq_market_probe.py"
cp -a "$SCRIPT_DIR/schema/market-data.xml" "$STAGE_DIST/aeron_mvp/schema/market-data.xml"
cp -a "$AERON_JAR" "$STAGE_DIST/aeron_mvp/lib/aeron-all-1.51.0.jar"
cp -a "$JEROMQ_JAR" "$STAGE_DIST/aeron_mvp/lib/jeromq-0.6.0.jar"
cp -a "$PROJECT_ROOT/scripts/run_aeron_mvp.sh" "$STAGE_DIST/scripts/run_aeron_mvp.sh"
cp -a "$PROJECT_ROOT/scripts/run_multi_source_aeron_mvp.sh" \
    "$STAGE_DIST/scripts/run_multi_source_aeron_mvp.sh"
cp -a "$PROJECT_ROOT/scripts/run_ctp_aeron_mvp.sh" "$STAGE_DIST/scripts/run_ctp_aeron_mvp.sh"
cp -a "$PROJECT_ROOT/scripts/run_ctp_live_aeron_mvp.sh" "$STAGE_DIST/scripts/run_ctp_live_aeron_mvp.sh"
cp -a "$PROJECT_ROOT/scripts/run_ydapi_aeron_mvp.sh" "$STAGE_DIST/scripts/run_ydapi_aeron_mvp.sh"
cp -a "$PROJECT_ROOT/scripts/run_dashboard.sh" "$STAGE_DIST/scripts/run_dashboard.sh"
cp -a "$PROJECT_ROOT/scripts/run_zmq_market_smoke.sh" \
    "$STAGE_DIST/scripts/run_zmq_market_smoke.sh"
cp -a "$PROJECT_ROOT/scripts/bootstrap_ctp_tts.sh" "$STAGE_DIST/scripts/bootstrap_ctp_tts.sh"
cp -a "$PROJECT_ROOT/scripts/bootstrap_ctp_live.sh" "$STAGE_DIST/scripts/bootstrap_ctp_live.sh"
cp -a "$PROJECT_ROOT/utils/conda_runtime.sh" "$STAGE_DIST/utils/conda_runtime.sh"
cp -a "$PROJECT_ROOT/dashboard/." "$STAGE_DIST/dashboard/"
cp -a "$PROJECT_ROOT/environment.yml" "$STAGE_DIST/environment.yml"
cp -a "$PROJECT_ROOT/config/market-sources/." "$STAGE_DIST/config/market-sources/"
cp -a "$PROJECT_ROOT/vendor/wheels/pyyd-1.486.96.99-cp39-cp39-linux_x86_64.whl" \
    "$STAGE_DIST/vendor/wheels/"
cp -a "$SCRIPT_DIR/PACKAGE_README.md" "$STAGE_DIST/README.md"
cp -a "$SCRIPT_DIR/THIRD_PARTY_NOTICES.md" "$STAGE_DIST/THIRD_PARTY_NOTICES.md"
cp -a "$SCRIPT_DIR/licenses/JEROMQ-LICENSE.txt" \
    "$STAGE_DIST/licenses/JEROMQ-LICENSE.txt"
printf '%s\n' "$VERSION" >"$STAGE_DIST/VERSION"

"$JAVA_HOME/bin/jlink" \
    --add-modules java.base,java.compiler,java.management \
    --strip-debug \
    --no-header-files \
    --no-man-pages \
    --compress=2 \
    --output "$STAGE_DIST/aeron_mvp/runtime"

(cd "$STAGE_DIST/licenses" && \
    "$JAVA_HOME/bin/jar" xf "$AERON_JAR" META-INF/LICENSE.txt && \
    mv META-INF/LICENSE.txt AERON-LICENSE.txt && \
    rmdir META-INF)

chmod 0755 \
    "$STAGE_DIST/aeron_mvp/run_java.sh" \
    "$STAGE_DIST/aeron_mvp/env.sh" \
    "$STAGE_DIST/aeron_mvp/ctp_bridge.py" \
    "$STAGE_DIST/aeron_mvp/ydapi_bridge.py" \
    "$STAGE_DIST/aeron_mvp/multi_source_mux.py" \
    "$STAGE_DIST/aeron_mvp/source_config.py" \
    "$STAGE_DIST/aeron_mvp/synthetic_bridge.py" \
    "$STAGE_DIST/aeron_mvp/zmq_market_probe.py" \
    "$STAGE_DIST/scripts/run_aeron_mvp.sh" \
    "$STAGE_DIST/scripts/run_multi_source_aeron_mvp.sh" \
    "$STAGE_DIST/scripts/run_ctp_aeron_mvp.sh" \
    "$STAGE_DIST/scripts/run_ctp_live_aeron_mvp.sh" \
    "$STAGE_DIST/scripts/run_ydapi_aeron_mvp.sh" \
    "$STAGE_DIST/scripts/run_dashboard.sh" \
    "$STAGE_DIST/scripts/run_zmq_market_smoke.sh" \
    "$STAGE_DIST/scripts/bootstrap_ctp_tts.sh" \
    "$STAGE_DIST/scripts/bootstrap_ctp_live.sh" \
    "$STAGE_DIST/utils/conda_runtime.sh" \
    "$STAGE_DIST/dashboard/server.py" \
    "$STAGE_DIST/aeron_mvp/runtime/bin/java"

(cd "$STAGE_DIST" && \
    find . -type f ! -name manifest.sha256 -print0 | sort -z | xargs -0 sha256sum \
        >manifest.sha256)

tar -C "$STAGE_ROOT" -czf "$ARCHIVE_FILE" "$DIST_NAME"
(cd "$RESULT_DIR" && sha256sum "$(basename -- "$ARCHIVE_FILE")" \
    >"$(basename -- "$ARCHIVE_HASH_FILE")")
mv "$STAGE_DIST" "$DIST_DIR"
rmdir "$STAGE_ROOT"

printf 'AERON_MVP_RELEASE result=SUCCESS version=%s directory=%s archive=%s\n' \
    "$VERSION" "$DIST_DIR" "$ARCHIVE_FILE"
