import json
from pathlib import Path

import pytest

from aeron_mvp.source_config import SourceConfigError
from aeron_mvp.source_config import load_source_config
from aeron_mvp.source_config import load_source_configs


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_config(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def synthetic(name: str, instrument: str = "IC2609") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "kind": "synthetic",
        "instrument": instrument,
        "repeat": 1000,
    }


def test_loads_multiple_independent_source_configs(tmp_path):
    first = write_config(tmp_path / "first.json", synthetic("sim-a"))
    second = write_config(tmp_path / "second.json", synthetic("sim-b", "IC2610"))

    configs = load_source_configs([first, second], tmp_path)

    assert [config["name"] for config in configs] == ["sim-a", "sim-b"]
    assert [config["instrument"] for config in configs] == ["IC2609", "IC2610"]
    assert all(config["repeat"] == "1000" for config in configs)


def test_loads_one_independent_source_config(tmp_path):
    source = write_config(tmp_path / "only.json", synthetic("sim-a"))

    configs = load_source_configs([source], tmp_path)

    assert [config["name"] for config in configs] == ["sim-a"]


def test_ctp_paths_are_resolved_from_project_root(tmp_path):
    config_path = write_config(
        tmp_path / "ctp.json",
        {
            "schema_version": 1,
            "name": "ctp-live",
            "kind": "ctp",
            "front": "tcp://127.0.0.1:30011",
            "api_kind": "official",
            "latency_mode": "live",
            "python": "result/ctp/bin/python",
            "instruments": ["IF2609", "IC2609"],
        },
    )

    config = load_source_config(config_path, tmp_path)

    assert config["python"] == str((tmp_path / "result/ctp/bin/python").resolve())
    assert config["instruments"] == "IF2609,IC2609"


def test_ctp_python_override_is_optional(tmp_path):
    config_path = write_config(
        tmp_path / "ctp.json",
        {
            "schema_version": 1,
            "name": "ctp-live",
            "kind": "ctp",
            "front": "tcp://127.0.0.1:30011",
            "api_kind": "official",
            "latency_mode": "live",
            "instruments": ["IF2609"],
        },
    )

    assert load_source_config(config_path, tmp_path)["python"] == ""


def test_ydapi_python_override_is_optional(tmp_path):
    config_path = write_config(
        tmp_path / "ydapi.json",
        {
            "schema_version": 1,
            "name": "ydapi-main",
            "kind": "ydapi",
            "account_config": "config/account.json",
            "api_config": "config/ydClient.ini",
            "instrument": "IF2609",
        },
    )

    config = load_source_config(config_path, tmp_path)

    assert config["python"] == ""
    assert config["account_config"] == str(
        (tmp_path / "config/account.json").resolve()
    )


def test_rejects_duplicate_names(tmp_path):
    first = write_config(tmp_path / "first.json", synthetic("duplicate"))
    second = write_config(tmp_path / "second.json", synthetic("duplicate"))

    with pytest.raises(SourceConfigError, match="duplicate source name"):
        load_source_configs([first, second], tmp_path)


def test_disabled_config_is_ignored_when_another_source_is_enabled(tmp_path):
    first = write_config(tmp_path / "first.json", synthetic("sim-a"))
    disabled_document = synthetic("sim-b")
    disabled_document["enabled"] = False
    second = write_config(tmp_path / "second.json", disabled_document)

    configs = load_source_configs([first, second], tmp_path)

    assert [config["name"] for config in configs] == ["sim-a"]


def test_rejects_config_list_without_an_enabled_source(tmp_path):
    document = synthetic("sim-a")
    document["enabled"] = False
    source = write_config(tmp_path / "disabled.json", document)

    with pytest.raises(SourceConfigError, match="at least one enabled"):
        load_source_configs([source], tmp_path)


def test_rejects_unknown_fields(tmp_path):
    document = synthetic("sim-a")
    document["typo_field"] = "ignored-data-would-be-dangerous"
    config_path = write_config(tmp_path / "source.json", document)

    with pytest.raises(SourceConfigError, match="unknown field"):
        load_source_config(config_path, tmp_path)


def test_aeron_release_includes_source_config_runtime_and_examples():
    build = (PROJECT_ROOT / "aeron_mvp" / "build_release.sh").read_text(
        encoding="utf-8"
    )

    assert '"$SCRIPT_DIR/source_config.py"' in build
    assert '"$PROJECT_ROOT/config/market-sources/."' in build
