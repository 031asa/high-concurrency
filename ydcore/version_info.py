"""Capture deployment identity without importing market/trading modules."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    return result.stdout.strip() if result.returncode == 0 else None

def current(root=ROOT):
    root = Path(root)
    # A packaged identity is authoritative only when no checkout is present.
    if (root / ".git").exists():
        try:
            commit = git(root, "rev-parse", "HEAD")
            tags = (git(root, "tag", "--points-at", "HEAD") or "").splitlines()
            dirty = git(root, "status", "--porcelain", "--untracked-files=normal")
            return dict(tag=tags[0] if len(tags) == 1 else None, tags=tags, commit=commit,
                        dirty=bool(dirty) if dirty is not None else None, provenance="git")
        except (OSError, subprocess.SubprocessError):
            pass
    path = root / "build-version.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict) and "commit" in data:
                return {**data, "provenance": "build-version.json"}
        except (ValueError, OSError):
            pass
    return dict(tag=None, tags=[], commit=None, dirty=None, provenance="unknown")

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--format", choices=["json", "meta"], default="json")
    p.add_argument("--write", type=Path)
    a = p.parse_args(argv)
    v = current()
    text = json.dumps(v, ensure_ascii=False)
    if a.format == "meta":
        text = "\n".join("git_" + k + "=" + (json.dumps(v[k], ensure_ascii=False) if k == "dirty" else str(v[k] or "unknown"))
                         for k in ("tag", "commit", "dirty"))
    if a.write:
        a.write.parent.mkdir(parents=True, exist_ok=True)
        tmp = a.write.with_suffix(a.write.suffix + ".tmp")
        tmp.write_text(text + "\n")
        os.replace(tmp, a.write)
    else:
        print(text)
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
