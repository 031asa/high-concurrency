#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
source "$PROJECT_ROOT/utils/conda_runtime.sh"
TTS_VERSION=6.7.11
TTS_ARCHIVE_SHA256=f9d4aec4aa3b3d1ec9f0587d37a447f6bfe4158745226279cc078257faa3a6ee
TTS_MD_SHA256=c81a6fdb0c70110190fa6a64d138c0fcb13c33cca076a3dde9f479af6c45a887
TTS_URL="http://www.openctp.cn/download/CTPAPI/TTS/tts_${TTS_VERSION}.zip"
RUNTIME_DIR=${CTP_TTS_RUNTIME_DIR:-"$PROJECT_ROOT/result/ctp-tts-runtime"}
CONDA_ENV_DIR="$RUNTIME_DIR/conda"
DOWNLOAD_DIR="$RUNTIME_DIR/downloads"
SDK_DIR="$RUNTIME_DIR/sdk"
ARCHIVE_FILE="$DOWNLOAD_DIR/tts_${TTS_VERSION}.zip"
SDK_MD_LIBRARY="$SDK_DIR/tts_${TTS_VERSION}/lin64/thostmduserapi_se.so"
LOCALE_ROOT="$RUNTIME_DIR/locale"

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf 'OpenCTP TTS bootstrap requires Linux x86_64\n' >&2
    exit 2
fi

for required in curl unzip sha256sum localedef; do
    command -v "$required" >/dev/null || {
        printf 'missing CTP bootstrap dependency: %s\n' "$required" >&2
        exit 2
    }
done

mkdir -p "$DOWNLOAD_DIR" "$SDK_DIR"
if [[ -n "${TTS_SDK_ZIP:-}" ]]; then
    [[ -f "$TTS_SDK_ZIP" ]] || {
        printf 'TTS_SDK_ZIP does not exist: %s\n' "$TTS_SDK_ZIP" >&2
        exit 2
    }
    if [[ "$(readlink -f -- "$TTS_SDK_ZIP")" != "$(readlink -f -- "$ARCHIVE_FILE")" ]]; then
        cp "$TTS_SDK_ZIP" "$ARCHIVE_FILE"
    fi
elif ! printf '%s  %s\n' "$TTS_ARCHIVE_SHA256" "$ARCHIVE_FILE" | sha256sum --check --status; then
    download_part="$ARCHIVE_FILE.part.$$"
    trap 'rm -f -- "$download_part"' EXIT
    curl --fail --location --silent --show-error --output "$download_part" "$TTS_URL"
    printf '%s  %s\n' "$TTS_ARCHIVE_SHA256" "$download_part" | sha256sum --check --status || {
        printf 'OpenCTP TTS SDK checksum mismatch\n' >&2
        exit 2
    }
    mv "$download_part" "$ARCHIVE_FILE"
    trap - EXIT
fi
printf '%s  %s\n' "$TTS_ARCHIVE_SHA256" "$ARCHIVE_FILE" | sha256sum --check --status || {
    printf 'OpenCTP TTS SDK checksum mismatch: %s\n' "$ARCHIVE_FILE" >&2
    exit 2
}

unzip -oq "$ARCHIVE_FILE" "tts_${TTS_VERSION}/lin64/thostmduserapi_se.so" -d "$SDK_DIR"
printf '%s  %s\n' "$TTS_MD_SHA256" "$SDK_MD_LIBRARY" | sha256sum --check --status || {
    printf 'OpenCTP TTS market library checksum mismatch\n' >&2
    exit 2
}

ydtrader_prepare_conda_prefix "$PROJECT_ROOT" "$RUNTIME_DIR"

mapfile -t native_libraries < <(
    find "$CONDA_ENV_DIR"/lib/python*/site-packages/openctp_ctp.libs \
        -maxdepth 1 -type f -name 'libthostmduserapi_se-*.so' -print
)
if [[ "${#native_libraries[@]}" -ne 1 ]]; then
    printf 'expected one openctp-ctp market library, found %s\n' \
        "${#native_libraries[@]}" >&2
    exit 2
fi
native_library=${native_libraries[0]}
if ! printf '%s  %s\n' "$TTS_MD_SHA256" "$native_library" | sha256sum --check --status; then
    if [[ ! -f "$native_library.ctp-original" ]]; then
        cp "$native_library" "$native_library.ctp-original"
    fi
    install -m 0755 "$SDK_MD_LIBRARY" "$native_library"
fi
printf '%s  %s\n' "$TTS_MD_SHA256" "$native_library" | sha256sum --check --status || {
    printf 'failed to activate OpenCTP TTS market library\n' >&2
    exit 2
}

if [[ ! -f "$LOCALE_ROOT/zh_CN.GB18030/LC_CTYPE" ]]; then
    mkdir -p "$LOCALE_ROOT"
    localedef --no-archive -i zh_CN -f GB18030 "$LOCALE_ROOT/zh_CN.GB18030"
fi

LOCPATH="$LOCALE_ROOT" "$CONDA_ENV_DIR/bin/python" -c \
    'from openctp_ctp import thostmduserapi as mdapi; print(mdapi.CThostFtdcMdApi.GetApiVersion())' \
    | grep -q 'openctp-tts v6\.7\.11' || {
        printf 'OpenCTP TTS Python import/version verification failed\n' >&2
        exit 2
    }

printf 'CTP_TTS_BOOTSTRAP result=SUCCESS version=%s python=%s locale=%s\n' \
    "$TTS_VERSION" "$CONDA_ENV_DIR/bin/python" "$LOCALE_ROOT"
