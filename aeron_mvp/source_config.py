#!/usr/bin/env python3
"""Validate independent market-source configuration files for the launcher."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
KINDS = {"synthetic", "ctp", "ydapi"}
COMMON_KEYS = {
    "schema_version",
    "name",
    "kind",
    "enabled",
    "repeat",
    "source_timeout_seconds",
}
KIND_KEYS = {
    "synthetic": {"instrument"},
    "ctp": {
        "front",
        "api_kind",
        "latency_mode",
        "python",
        "instruments",
    },
    "ydapi": {
        "python",
        "account_config",
        "api_config",
        "instrument",
        "startup_timeout_seconds",
    },
}
OUTPUT_FIELDS = (
    "name",
    "kind",
    "instrument",
    "instruments",
    "repeat",
    "python",
    "front",
    "api_kind",
    "latency_mode",
    "account_config",
    "api_config",
    "startup_timeout_seconds",
    "source_timeout_seconds",
    "config_path",
)


class SourceConfigError(ValueError):
    """Raised when an independent source configuration is invalid."""


def _nonempty_string(value: Any, field: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceConfigError(f"{source}: {field} must be a non-empty string")
    result = value.strip()
    if any(character in result for character in ("\x00", "\n", "\r", "\t")):
        raise SourceConfigError(f"{source}: {field} contains a control character")
    return result


def _integer(
    value: Any,
    field: str,
    source: Path,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceConfigError(f"{source}: {field} must be an integer")
    if not minimum <= value <= maximum:
        raise SourceConfigError(
            f"{source}: {field} must be between {minimum} and {maximum}"
        )
    return value


def _project_path(value: Any, field: str, source: Path, project_root: Path) -> str:
    configured = Path(_nonempty_string(value, field, source)).expanduser()
    if not configured.is_absolute():
        configured = project_root / configured
    return str(configured.resolve(strict=False))


def _optional_project_path(value: Any, field: str, source: Path, project_root: Path) -> str:
    if value is None:
        return ""
    return _project_path(value, field, source, project_root)


def _instrument(value: Any, field: str, source: Path) -> str:
    result = _nonempty_string(value, field, source)
    if "," in result:
        raise SourceConfigError(f"{source}: {field} must contain one instrument")
    return result


def _instruments(value: Any, source: Path) -> str:
    if not isinstance(value, list) or not value:
        raise SourceConfigError(f"{source}: instruments must be a non-empty array")
    instruments = [_instrument(item, "instruments[]", source) for item in value]
    if len(set(instruments)) != len(instruments):
        raise SourceConfigError(f"{source}: instruments contains duplicates")
    return ",".join(instruments)


def load_source_config(path: Path, project_root: Path) -> dict[str, str]:
    source = path.expanduser().resolve(strict=False)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise SourceConfigError(f"cannot read source config {source}: {error}") from error
    except json.JSONDecodeError as error:
        raise SourceConfigError(f"invalid JSON in {source}: {error}") from error
    if not isinstance(document, dict):
        raise SourceConfigError(f"{source}: top-level JSON value must be an object")
    if document.get("schema_version") != 1:
        raise SourceConfigError(f"{source}: schema_version must be 1")
    enabled = document.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SourceConfigError(f"{source}: enabled must be true or false")
    name = _nonempty_string(document.get("name"), "name", source)
    if SOURCE_NAME.fullmatch(name) is None:
        raise SourceConfigError(f"{source}: name must match {SOURCE_NAME.pattern}")
    kind = _nonempty_string(document.get("kind"), "kind", source)
    if kind not in KINDS:
        raise SourceConfigError(f"{source}: kind must be synthetic, ctp, or ydapi")
    unknown = set(document) - COMMON_KEYS - KIND_KEYS[kind]
    if unknown:
        raise SourceConfigError(
            f"{source}: unknown field(s): {', '.join(sorted(unknown))}"
        )

    normalized = {field: "" for field in OUTPUT_FIELDS}
    normalized.update(
        name=name,
        kind=kind,
        latency_mode="live",
        repeat=str(_integer(document.get("repeat", 1), "repeat", source, 1, 1_000_000)),
        source_timeout_seconds=str(
            _integer(
                document.get(
                    "source_timeout_seconds",
                    60 if kind == "synthetic" else 86_400,
                ),
                "source_timeout_seconds",
                source,
                1,
                86_400,
            )
        ),
        config_path=str(source),
    )
    if not enabled:
        normalized["kind"] = "disabled"
        return normalized

    if kind == "synthetic":
        normalized["instrument"] = _instrument(
            document.get("instrument"), "instrument", source
        )
    elif kind == "ctp":
        front = _nonempty_string(document.get("front"), "front", source)
        if not front.startswith("tcp://"):
            raise SourceConfigError(f"{source}: front must use tcp://")
        api_kind = _nonempty_string(document.get("api_kind"), "api_kind", source)
        if api_kind not in {"tts", "official"}:
            raise SourceConfigError(f"{source}: api_kind must be tts or official")
        latency_mode = _nonempty_string(
            document.get("latency_mode"), "latency_mode", source
        )
        if latency_mode not in {"historical_replay", "live"}:
            raise SourceConfigError(
                f"{source}: latency_mode must be historical_replay or live"
            )
        normalized.update(
            front=front,
            api_kind=api_kind,
            latency_mode=latency_mode,
            python=_optional_project_path(
                document.get("python"), "python", source, project_root
            ),
            instruments=_instruments(document.get("instruments"), source),
        )
    else:
        normalized.update(
            python=_optional_project_path(
                document.get("python"), "python", source, project_root
            ),
            account_config=_project_path(
                document.get("account_config"), "account_config", source, project_root
            ),
            api_config=_project_path(
                document.get("api_config"), "api_config", source, project_root
            ),
            instrument=_instrument(document.get("instrument"), "instrument", source),
            startup_timeout_seconds=str(
                _integer(
                    document.get("startup_timeout_seconds", 60),
                    "startup_timeout_seconds",
                    source,
                    1,
                    86_400,
                )
            ),
        )
    return normalized


def load_source_configs(paths: list[Path], project_root: Path) -> list[dict[str, str]]:
    if not paths:
        raise SourceConfigError("at least one --config is required")
    enabled: list[dict[str, str]] = []
    names: set[str] = set()
    for path in paths:
        config = load_source_config(path, project_root)
        if config["kind"] == "disabled":
            continue
        if config["name"] in names:
            raise SourceConfigError(f"duplicate source name: {config['name']}")
        names.add(config["name"])
        enabled.append(config)
    if not enabled:
        raise SourceConfigError("at least one enabled source config is required")
    return enabled


def emit_null(configs: list[dict[str, str]]) -> None:
    for config in configs:
        for field in OUTPUT_FIELDS:
            sys.stdout.buffer.write(config[field].encode("utf-8"))
            sys.stdout.buffer.write(b"\x00")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--config", action="append", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        configs = load_source_configs(args.config, args.project_root.resolve())
    except SourceConfigError as error:
        print(f"source configuration error: {error}", file=sys.stderr)
        return 64
    emit_null(configs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
