#!/usr/bin/env bash
# 使用 uv 锁定 Python 依赖，再安装官方 DeepSeek Harness。
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
uv sync --locked --python 3.10
exec .venv/bin/python scripts/setup_runtime.py
