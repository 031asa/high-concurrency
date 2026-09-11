#!/usr/bin/env bash
# Read once at startup, reused unchanged on every run.meta status rewrite.
ydtrader_version_metadata() {
    local root=$1 commit=unknown tag=unknown dirty=unknown tags
    if [[ -e "$root/.git" ]] && command -v git >/dev/null 2>&1; then
        commit=$(git -C "$root" rev-parse HEAD 2>/dev/null) || commit=unknown
        tags=$(git -C "$root" tag --points-at HEAD 2>/dev/null) || tags=
        if [[ -n "$tags" && "$tags" != *$'\n'* ]]; then tag=$tags; fi
        if git -C "$root" status --porcelain --untracked-files=normal >/dev/null 2>&1; then
            if [[ -n "$(git -C "$root" status --porcelain --untracked-files=normal)" ]]; then dirty=true; else dirty=false; fi
        fi
    elif [[ -f "$root/build-version.meta" ]]; then
        # Never source metadata as shell code.
        grep -E '^git_(tag|commit|dirty)=' "$root/build-version.meta"
        return
    fi
    printf 'git_tag=%s\ngit_commit=%s\ngit_dirty=%s\n' "$tag" "$commit" "$dirty"
}
