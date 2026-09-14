#!/usr/bin/env bash
# Run from the published, clean source checkout; never fabricate build identity.
set -euo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"
python main.py version-info --write build-version.json
python main.py version-info --format meta --write build-version.meta
exec secure-release docker-build --config "$PROJECT_ROOT/product.toml" --project "$PROJECT_ROOT" "$@"
