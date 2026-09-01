from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_skill_layout_is_present():
    for name in ("data", "result", "scripts", "tests", "utils"):
        assert (PROJECT_ROOT / name).is_dir(), name
    assert (PROJECT_ROOT / "environment.yml").is_file()
    assert (PROJECT_ROOT / "data" / "error_code.csv").is_file()


def test_current_launchers_do_not_use_virtualenv_or_uv():
    launchers = [
        PROJECT_ROOT / "scripts" / "bootstrap_ctp_live.sh",
        PROJECT_ROOT / "scripts" / "bootstrap_ctp_tts.sh",
        PROJECT_ROOT / "scripts" / "run_aeron_mvp.sh",
        PROJECT_ROOT / "scripts" / "run_dashboard.sh",
    ]
    for launcher in launchers:
        content = launcher.read_text(encoding="utf-8")
        assert "/venv/" not in content
        assert "uv venv" not in content
        assert "requirements-ctp.txt" not in content


def test_environment_is_canonical_runtime_manifest():
    content = (PROJECT_ROOT / "environment.yml").read_text(encoding="utf-8")
    assert "name: ydtrader-high-concurrency" in content
    assert "python=3.9" in content
    assert "openjdk=17" in content
    assert "openctp-ctp==6.7.11.0" in content
    assert "./vendor/wheels/pyyd-" in content
    assert "\n  - uv\n" not in content
    assert not (PROJECT_ROOT / "requirements-ctp.txt").exists()


def test_manylinux_build_pins_are_mirrored_in_environment():
    environment = (PROJECT_ROOT / "environment.yml").read_text(encoding="utf-8")
    requirements = (
        PROJECT_ROOT / "requirements-build.txt"
    ).read_text(encoding="utf-8").splitlines()
    for requirement in requirements:
        assert f"- {requirement}" in environment


def test_runtime_has_no_project_license_or_encryption_layer():
    removed = [
        PROJECT_ROOT / "ydcore" / "licensing.py",
        PROJECT_ROOT / "build_tools" / "generate_keypair.py",
        PROJECT_ROOT / "build_tools" / "issue_license.py",
        PROJECT_ROOT / "install" / "ydtrader-destroy",
    ]
    assert not any(path.exists() for path in removed)
    environment = (PROJECT_ROOT / "environment.yml").read_text(encoding="utf-8")
    requirements = (PROJECT_ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    for dependency in ("argon2-cffi", "cryptography"):
        assert dependency not in environment
        assert dependency not in requirements


def test_release_build_and_installer_need_no_keys_or_activation():
    build = (PROJECT_ROOT / "build_tools" / "build_release.py").read_text(
        encoding="utf-8"
    )
    installer = (PROJECT_ROOT / "install" / "install_linux.sh").read_text(
        encoding="utf-8"
    )
    forbidden = ("public-key", "private-key", "install_manifest.sig", "activate")
    for value in forbidden:
        assert value not in build
        assert value not in installer
