"""Refuse official reports from dirty or unpublished generator code."""
import re
import subprocess


def require_published(root, identity):
    commit = identity.get("commit")
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(commit or "")) or identity.get("dirty") is not False:
        raise RuntimeError("日报生成器未提交或版本未知：请先提交并推送 Gitea，再生成报告")
    if not (root / ".git").exists():
        # Packages must carry evidence captured by the release build.
        if identity.get("published_commit") == commit:
            return
        raise RuntimeError("镜像缺少提交发布证明，请从已推送提交重新打包")
    def run(*args):
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, timeout=60)
        if p.returncode:
            raise RuntimeError("无法核验远程提交；报告未生成，请检查 Gitea 连接或推送状态")
        return p.stdout.strip()
    # Refresh, rather than accepting stale remote-tracking refs as evidence.
    run("fetch", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*")
    refs = run("for-each-ref", "--contains", commit, "--format=%(refname)", "refs/remotes/origin/")
    if not refs:
        raise RuntimeError("当前提交尚未推送到 Gitea 分支，请先推送再生成")
    identity["published_commit"] = commit
