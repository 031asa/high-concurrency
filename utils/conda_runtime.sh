#!/usr/bin/env bash

YDTRADER_CONDA_ENV_NAME=ydtrader-high-concurrency

ydtrader_runtime_error() {
    printf 'YDTrader environment error: %s\n' "$*" >&2
    return 2
}

ydtrader_require_wsl_project() {
    local project_root=$1
    [[ "$(uname -s)" == Linux ]] || {
        ydtrader_runtime_error "Linux under WSL 2 is required"
        return
    }
    grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null || {
        ydtrader_runtime_error "WSL 2 is required"
        return
    }
    case "$(readlink -f -- "$project_root")/" in
        /mnt/*)
            ydtrader_runtime_error "repository must be in the WSL Linux filesystem"
            return
            ;;
    esac
}

ydtrader_find_conda() {
    if [[ -n "${CONDA_EXE:-}" && -x "$CONDA_EXE" ]]; then
        printf '%s\n' "$CONDA_EXE"
    elif command -v conda >/dev/null 2>&1; then
        command -v conda
    elif [[ -x "${HOME:-}/miniconda3/bin/conda" ]]; then
        printf '%s\n' "$HOME/miniconda3/bin/conda"
    else
        ydtrader_runtime_error "user-local Miniconda is required"
    fi
}

ydtrader_validate_conda_python() {
    local project_root=$1
    local candidate=${2:-}
    ydtrader_require_wsl_project "$project_root"
    if [[ -z "$candidate" && -n "${CONDA_PREFIX:-}" ]]; then
        candidate="$CONDA_PREFIX/bin/python"
    fi
    [[ -x "$candidate" ]] || {
        ydtrader_runtime_error "activate $YDTRADER_CONDA_ENV_NAME"
        return
    }
    "$candidate" -c '
import pathlib, sys
assert sys.version_info[:2] == (3, 9), "CPython 3.9 is required"
assert (pathlib.Path(sys.prefix) / "conda-meta").is_dir(), "Conda Python required"
' || {
        ydtrader_runtime_error "invalid Conda Python: $candidate"
        return
    }
    readlink -f -- "$candidate"
}

ydtrader_prepare_conda_prefix() {
    local project_root=$1
    local runtime_dir=$2
    local conda_exe
    local fingerprint
    local marker="$runtime_dir/conda/.ydtrader-environment.sha256"
    ydtrader_require_wsl_project "$project_root"
    fingerprint=$(
        cd "$project_root"
        sha256sum environment.yml \
            vendor/wheels/pyyd-1.486.96.99-cp39-cp39-linux_x86_64.whl \
            | sha256sum | awk '{print $1}'
    )
    if [[ -x "$runtime_dir/conda/bin/python" && -f "$marker" ]] \
        && [[ "$(cat "$marker")" == "$fingerprint" ]]; then
        ydtrader_validate_conda_python \
            "$project_root" "$runtime_dir/conda/bin/python" >/dev/null
        return
    fi
    conda_exe=$(ydtrader_find_conda)
    mkdir -p "$runtime_dir"
    (
        cd "$project_root"
        "$conda_exe" env update --prefix "$runtime_dir/conda" \
            --file environment.yml --prune
    )
    ydtrader_validate_conda_python \
        "$project_root" "$runtime_dir/conda/bin/python" >/dev/null
    printf '%s\n' "$fingerprint" >"$marker.tmp"
    mv "$marker.tmp" "$marker"
}
