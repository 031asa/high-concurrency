#!/usr/bin/env bash
set -euo pipefail

CACHE_DIR=/home/hello/.cache/ydtrader-mvp
OPT_DIR=/home/hello/.local/opt
JDK_ARCHIVE="$CACHE_DIR/temurin17.tar.gz"
JDK_DIR="$OPT_DIR/jdk-17.0.19+10"
AERON_JAR="$CACHE_DIR/aeron-all-1.51.0.jar"
SBE_JAR="$CACHE_DIR/sbe-all-1.38.1.jar"

JDK_URL='https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.19%2B10/OpenJDK17U-jdk_x64_linux_hotspot_17.0.19_10.tar.gz'
AERON_URL='https://repo.maven.apache.org/maven2/io/aeron/aeron-all/1.51.0/aeron-all-1.51.0.jar'
SBE_URL='https://repo.maven.apache.org/maven2/uk/co/real-logic/sbe-all/1.38.1/sbe-all-1.38.1.jar'
JDK_SHA256='d8afc263758141a66e0e3aafc321e783f7016696f4eaea067d340a269037d331'
AERON_SHA1='4d17308cba9d4ff3ff97833d6dac52e3289f81e1'
SBE_SHA1='ef7dd43a54a0269854ac2a296c2f6ba25edbaeff'

mkdir -p "$CACHE_DIR" "$OPT_DIR"

download_if_missing() {
    local url=$1
    local destination=$2
    if [[ ! -f "$destination" ]]; then
        curl -fL --retry 3 --output "${destination}.part" "$url"
        mv -- "${destination}.part" "$destination"
    fi
}

download_if_missing "$JDK_URL" "$JDK_ARCHIVE"
download_if_missing "$AERON_URL" "$AERON_JAR"
download_if_missing "$SBE_URL" "$SBE_JAR"

printf '%s  %s\n' "$JDK_SHA256" "$JDK_ARCHIVE" | sha256sum -c -
printf '%s  %s\n' "$AERON_SHA1" "$AERON_JAR" | sha1sum -c -
printf '%s  %s\n' "$SBE_SHA1" "$SBE_JAR" | sha1sum -c -

if [[ ! -x "$JDK_DIR/bin/java" ]]; then
    tar -xzf "$JDK_ARCHIVE" -C "$OPT_DIR"
fi

"$JDK_DIR/bin/java" -version
printf 'AERON_MVP_BOOTSTRAP result=SUCCESS cache=%s jdk=%s\n' "$CACHE_DIR" "$JDK_DIR"
