import sys

from ydcore import launcher, licensing


def test_unauthorized_business_never_imports_core_or_vendor(monkeypatch):
    for name in ("ydcore.trading", "pyyd"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(launcher.getpass, "getpass", lambda _prompt: "password")

    def missing(_feature, _password):
        raise licensing.LicenseMissingError("not activated")

    monkeypatch.setattr(launcher.licensing, "verify_business_access", missing)
    assert launcher.main(["order"]) == 20
    assert "ydcore.trading" not in sys.modules
    assert "pyyd" not in sys.modules


def test_invalid_password_is_limited_to_three_attempts(monkeypatch):
    attempts = []

    def prompt(_prompt):
        attempts.append(1)
        return "wrong"

    monkeypatch.setattr(launcher.getpass, "getpass", prompt)
    monkeypatch.setattr(
        launcher.licensing,
        "verify_business_access",
        lambda *_args: (_ for _ in ()).throw(licensing.LicenseInvalidError("bad")),
    )
    assert launcher.main(["monitor"]) == 21
    assert len(attempts) == 3
    assert "ydcore.monitoring" not in sys.modules
    assert "pyyd" not in sys.modules


def test_top_level_help_and_machine_code_need_no_password(monkeypatch, capsys):
    monkeypatch.setattr(launcher.licensing, "machine_code", lambda: "f" * 64)
    monkeypatch.setattr(
        launcher.getpass,
        "getpass",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("unexpected password prompt")),
    )
    assert launcher.main(["--help"]) == 0
    assert launcher.main(["machine-code"]) == 0
    assert "f" * 64 in capsys.readouterr().out
