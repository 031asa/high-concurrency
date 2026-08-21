from pathlib import Path


def test_destroy_helper_is_fixed_target_and_fail_closed():
    helper = Path(__file__).resolve().parents[1] / "install" / "ydtrader-destroy"
    source = helper.read_text(encoding="utf-8")
    assert "install_root=/opt/ydtrader" in source
    assert "state_root=/var/lib/ydtrader" in source
    assert '"$#" -eq 0' in source
    assert "realpath -e" in source
    assert ".ydtrader-install-marker" in source
    assert ".ydtrader-state-marker" in source
    assert "openssl pkeyutl -verify" in source
    assert "sha256sum --strict -c" in source
    assert "rm -rf --one-file-system -- \"$expired_root\"" in source
    assert "rm -rf --one-file-system -- \"$install_root\"" not in source


def test_release_builder_excludes_mutable_config_from_signed_hashes():
    builder = (
        Path(__file__).resolve().parents[1] / "build_tools" / "build_release.py"
    ).read_text(encoding="utf-8")
    assert 'relative.startswith(("config/", "logs/"))' in builder
