#!/bin/sh
set -eu

if [ "$#" -ne 0 ]; then
    echo "usage: $0" >&2
    exit 64
fi
command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the formal manylinux2014 build" >&2
    exit 69
}

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
host_uid=$(id -u)
host_gid=$(id -g)
docker run --rm -it \
    -e HOST_UID="$host_uid" \
    -e HOST_GID="$host_gid" \
    -v "$project_root:/work" \
    -w /work \
    quay.io/pypa/manylinux2014_x86_64 \
    /bin/bash -lc '
        set -eu
        PYTHON=/opt/python/cp39-cp39/bin/python
        "$PYTHON" -m pip install --disable-pip-version-check -r requirements-build.txt
        "$PYTHON" -m pip install vendor/wheels/pyyd-1.486.96.99-cp39-cp39-linux_x86_64.whl
        "$PYTHON" -m pytest -q
        "$PYTHON" build_tools/build_release.py
        max_glibc=$(find result/ydtrader-linux-x86_64/app -type f \
            \( -name "*.so" -o -name ydtrader \) -exec objdump -T {} \; 2>/dev/null \
            | grep -o "GLIBC_[0-9.]*" | sort -Vu | tail -n 1)
        [ -n "$max_glibc" ]
        [ "$(printf "%s\n%s\n" GLIBC_2.17 "$max_glibc" | sort -V | tail -n 1)" = GLIBC_2.17 ]
        if ldd result/ydtrader-linux-x86_64/app/ydtrader \
            result/ydtrader-linux-x86_64/app/*.so \
            result/ydtrader-linux-x86_64/app/ydcore/*.so | grep -q "not found"; then
            exit 1
        fi
        chown -R "$HOST_UID:$HOST_GID" build result ydcore
    '
