import sys
from types import SimpleNamespace

from ydcore import launcher


def test_business_command_dispatches_directly_without_license_or_password(monkeypatch):
    calls = []

    def load(name):
        calls.append(name)
        return SimpleNamespace(run=lambda argv: 7 if argv == ["--probe"] else 1)

    monkeypatch.setattr(launcher.importlib, "import_module", load)
    assert launcher.main(["order", "--probe"]) == 7
    assert calls == ["ydcore.trading"]


def test_top_level_help_does_not_import_business_modules(monkeypatch, capsys):
    monkeypatch.setattr(
        launcher.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(AssertionError("unexpected import")),
    )
    assert launcher.main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "order" in output
    assert "activate" not in output
    assert "machine-code" not in output


def test_removed_license_commands_are_rejected(capsys):
    for command in ("activate", "machine-code"):
        assert launcher.main([command]) == 2
    assert "未知命令" in capsys.readouterr().err


def test_keyboard_interrupt_keeps_existing_exit_code(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "_dispatch",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert launcher.main(["monitor"]) == 130
