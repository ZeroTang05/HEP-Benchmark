#!/usr/bin/env bash
# 激活本地环境，把模型配置选择和批量执行交给 Python 入口。
set -euo pipefail

BUNDLE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BUNDLE_ROOT"
source "$BUNDLE_ROOT/scripts/activate_local.sh" >/dev/null
exec python "$BUNDLE_ROOT/scripts/run_ams_hep_batch.py" "$@"
