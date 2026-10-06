#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .wheelhouse
.venv/bin/python -m pip download --only-binary=:all: --require-hashes \
  -r requirements.lock -r requirements-build.lock -d .wheelhouse
# Build with: docker build --build-arg PIP_NO_INDEX=1 -t gmgn-revival-radar .
# Cache is platform-specific; generate it on the target Linux architecture.
