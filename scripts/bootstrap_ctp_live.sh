#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
source "$PROJECT_ROOT/utils/conda_runtime.sh"
RUNTIME_DIR=${CTP_LIVE_RUNTIME_DIR:-"$PROJECT_ROOT/result/ctp-live-runtime"}
CONDA_ENV_DIR="$RUNTIME_DIR/conda"
LOCALE_ROOT="$RUNTIME_DIR/locale"
DOWNLOAD_DIR="$RUNTIME_DIR/downloads"
SDK_DIR="$RUNTIME_DIR/official-ctp-6.7.11"
SDK_ARCHIVE="$DOWNLOAD_DIR/homalos_ctp-6.7.11.2.tar.gz"
SDK_URL=https://files.pythonhosted.org/packages/47/6e/c577d1d452f911bbe0860b229c8497e4ad387287bb4e11b3f2f3fbf0d115/homalos_ctp-6.7.11.2.tar.gz
SDK_ARCHIVE_SHA256=e84fe262e2c2b3e1e469b86d05b11d6ac566233f73b34719b322a0b52ddf7208
SDK_LIBRARY_SHA256=9c6d321f3d880e1c91ec225cc46950f8aa1502f43fc9f41d1d92f10ee04d190a
SDK_LIBRARY_MEMBER=homalos_ctp-6.7.11.2/ctp/api/thostmduserapi_se.so

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf 'official CTP bootstrap requires Linux x86_64\n' >&2
    exit 2
fi
command -v localedef >/dev/null || {
    printf 'localedef is required by the CTP Linux wheel\n' >&2
    exit 2
}
command -v curl >/dev/null || {
    printf 'curl is required to download the pinned official CTP SDK\n' >&2
    exit 2
}
command -v sha256sum >/dev/null || {
    printf 'sha256sum is required to verify the official CTP SDK\n' >&2
    exit 2
}
command -v tar >/dev/null || {
    printf 'tar is required to extract the official CTP SDK\n' >&2
    exit 2
}

mkdir -p "$RUNTIME_DIR"
ydtrader_prepare_conda_prefix "$PROJECT_ROOT" "$RUNTIME_DIR"

mkdir -p "$DOWNLOAD_DIR" "$SDK_DIR"
if [[ ! -f "$SDK_ARCHIVE" ]] || \
   [[ "$(sha256sum "$SDK_ARCHIVE" | awk '{print $1}')" != "$SDK_ARCHIVE_SHA256" ]]; then
    rm -f -- "$SDK_ARCHIVE"
    curl -fL --retry 3 --retry-all-errors --connect-timeout 15 \
        -o "$SDK_ARCHIVE" "$SDK_URL"
fi
actual_archive_sha=$(sha256sum "$SDK_ARCHIVE" | awk '{print $1}')
[[ "$actual_archive_sha" == "$SDK_ARCHIVE_SHA256" ]] || {
    printf 'official CTP SDK archive checksum mismatch: %s\n' "$actual_archive_sha" >&2
    exit 2
}

official_library="$SDK_DIR/$SDK_LIBRARY_MEMBER"
if [[ ! -f "$official_library" ]] || \
   [[ "$(sha256sum "$official_library" | awk '{print $1}')" != "$SDK_LIBRARY_SHA256" ]]; then
    tar -xzf "$SDK_ARCHIVE" -C "$SDK_DIR" "$SDK_LIBRARY_MEMBER"
fi
actual_library_sha=$(sha256sum "$official_library" | awk '{print $1}')
[[ "$actual_library_sha" == "$SDK_LIBRARY_SHA256" ]] || {
    printf 'official CTP market library checksum mismatch: %s\n' "$actual_library_sha" >&2
    exit 2
}

native_library=$(find "$CONDA_ENV_DIR/lib" -path '*/site-packages/openctp_ctp.libs/libthostmduserapi_se-*.so' -type f -print -quit)
[[ -n "$native_library" ]] || {
    printf 'openctp-ctp market library was not found under %s\n' "$CONDA_ENV_DIR" >&2
    exit 2
}
install -m 0755 "$official_library" "$native_library"
installed_sha=$(sha256sum "$native_library" | awk '{print $1}')
[[ "$installed_sha" == "$SDK_LIBRARY_SHA256" ]] || {
    printf 'failed to install official CTP market library: %s\n' "$installed_sha" >&2
    exit 2
}

if [[ ! -f "$LOCALE_ROOT/zh_CN.GB18030/LC_CTYPE" ]]; then
    mkdir -p "$LOCALE_ROOT"
    localedef --no-archive -i zh_CN -f GB18030 "$LOCALE_ROOT/zh_CN.GB18030"
fi

api_version=$(LOCPATH="$LOCALE_ROOT" "$CONDA_ENV_DIR/bin/python" -c \
    'from openctp_ctp import thostmduserapi as mdapi; print(mdapi.CThostFtdcMdApi.GetApiVersion())')
[[ "$api_version" != *"openctp-tts"* ]] || {
    printf 'official CTP runtime unexpectedly contains the TTS library: %s\n' "$api_version" >&2
    exit 2
}
[[ "$api_version" == *"6.7.11"* ]] || {
    printf 'unexpected official CTP API version: %s\n' "$api_version" >&2
    exit 2
}

printf 'CTP_LIVE_BOOTSTRAP result=SUCCESS api_version=%s library_sha256=%s python=%s locale=%s\n' \
    "$api_version" "$installed_sha" "$CONDA_ENV_DIR/bin/python" "$LOCALE_ROOT"
