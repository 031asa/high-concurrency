#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

JDK_ARCHIVE="$AERON_MVP_DEPS_DIR/temurin-17.0.19+10.tar.gz"

JDK_URL='https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.19%2B10/OpenJDK17U-jdk_x64_linux_hotspot_17.0.19_10.tar.gz'
AERON_URL='https://repo.maven.apache.org/maven2/io/aeron/aeron-all/1.51.0/aeron-all-1.51.0.jar'
SBE_URL='https://repo.maven.apache.org/maven2/uk/co/real-logic/sbe-all/1.38.1/sbe-all-1.38.1.jar'
JDK_SHA256='d8afc263758141a66e0e3aafc321e783f7016696f4eaea067d340a269037d331'
AERON_SHA1='4d17308cba9d4ff3ff97833d6dac52e3289f81e1'
SBE_SHA1='ef7dd43a54a0269854ac2a296c2f6ba25edbaeff'

for required in curl tar sha256sum sha1sum; do
    command -v "$required" >/dev/null || {
        printf 'missing bootstrap command: %s\n' "$required" >&2
        exit 2
    }
done

mkdir -p "$AERON_MVP_DEPS_DIR"

download_and_verify() {
    local url=$1
    local destination=$2
    local checksum=$3
    local checksum_command=$4
    local part
    if printf '%s  %s\n' "$checksum" "$destination" | "$checksum_command" --check --status 2>/dev/null; then
        return 0
    fi
    part="${destination}.part.$$"
    trap 'rm -f -- "$part"' RETURN
    curl --fail --location --retry 3 --retry-all-errors \
        --connect-timeout 15 --silent --show-error --output "$part" "$url"
    printf '%s  %s\n' "$checksum" "$part" | "$checksum_command" --check --status || {
        printf 'dependency checksum mismatch: %s\n' "$destination" >&2
        return 1
    }
    mv -- "$part" "$destination"
    trap - RETURN
}

download_and_verify "$AERON_URL" "$AERON_MVP_AERON_JAR" "$AERON_SHA1" sha1sum
download_and_verify "$SBE_URL" "$AERON_MVP_SBE_JAR" "$SBE_SHA1" sha1sum

if ! aeron_mvp_detect_java_home 1; then
    download_and_verify "$JDK_URL" "$JDK_ARCHIVE" "$JDK_SHA256" sha256sum
    if [[ ! -x "$AERON_MVP_JDK_DIR/bin/java" ]]; then
        tar -xzf "$JDK_ARCHIVE" -C "$AERON_MVP_DEPS_DIR"
    fi
fi

aeron_mvp_resolve_build_dependencies || {
    aeron_mvp_print_dependency_help
    exit 2
}

"$JAVA_HOME/bin/java" -version
printf 'AERON_MVP_BOOTSTRAP result=SUCCESS deps=%s java_home=%s\n' \
    "$AERON_MVP_DEPS_DIR" "$JAVA_HOME"
