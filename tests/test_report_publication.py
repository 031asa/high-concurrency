from pathlib import Path
import subprocess
import pytest
from timer_pdf.code.publication import require_published
from ydcore.version_info import current

def test_remote_publication_required(tmp_path):
    remote=tmp_path/"remote.git"; repo=tmp_path/"repo"
    def git(*args):
        return subprocess.run(["git",*map(str,args)],check=True,capture_output=True,text=True).stdout.strip()
    git("init","--bare",remote);git("init",repo)
    git("-C",repo,"config","user.name","Test")
    git("-C",repo,"config","user.email","test@example.invalid")
    (repo/"file").write_text("one")
    git("-C",repo,"add","file");git("-C",repo,"commit","-m","one")
    git("-C",repo,"remote","add","origin",remote)
    with pytest.raises(RuntimeError):require_published(repo,current(repo))
    git("-C",repo,"push","origin","HEAD:main")
    identity=current(repo);require_published(repo,identity)
    assert identity["published_commit"]==identity["commit"]
    (repo/"file").write_text("two")
    with pytest.raises(RuntimeError):require_published(repo,current(repo))
    git("-C",repo,"commit","-am","two")
    with pytest.raises(RuntimeError):require_published(repo,current(repo))
    git("-C",repo,"push","origin","HEAD:main")
    require_published(repo,current(repo))

def test_package_needs_matching_evidence(tmp_path):
    identity=dict(commit="a"*40,dirty=False)
    with pytest.raises(RuntimeError):require_published(tmp_path,identity)
    identity["published_commit"]="b"*40
    with pytest.raises(RuntimeError):require_published(tmp_path,identity)
    identity["published_commit"]="a"*40
    require_published(tmp_path,identity)
