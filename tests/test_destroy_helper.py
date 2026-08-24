from pathlib import Path
import tempfile

from build_tools.build_release import copy_release_support_files


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


def test_release_time_support_contains_production_assets_only():
    with tempfile.TemporaryDirectory() as directory:
        release = Path(directory) / "release"
        release.mkdir()
        copy_release_support_files(release)
        assert (release / "docs" / "README_授时与行情延迟操作手册.md").is_file()
        assert (release / "tools" / "setup_linux_time_sync.sh").is_file()
        assert (release / "tools" / "linux_time_report.sh").is_file()
        template = release / "config" / "time_authority.cffex.example.conf"
        assert template.is_file()
        assert "REPLACE_WITH_CFFEX_NTP_HOST_OR_IP" in template.read_text(encoding="utf-8")
        fallback = release / "config" / "time_authority.tencent-south-china-fallback.conf"
        assert fallback.is_file()
        fallback_text = fallback.read_text(encoding="utf-8")
        assert "environment=test" in fallback_text
        assert "ntp_servers=106.55.184.199" in fallback_text
        assert not list(release.rglob("*cloudflare*"))
