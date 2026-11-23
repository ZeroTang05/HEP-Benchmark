#!/usr/bin/env bash
# 下载并预处理真实论文，不调用语言模型。
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/activate_local.sh" >/dev/null
exec python "$PROJECT_ROOT/scripts/prepare_ams_papers.py" \
  --pool "$PROJECT_ROOT/inputs/ams02-publications/target_study_pool.jsonl" \
  --cache-dir "$RF_CACHE_DIR" \
  --output-pool "$PROJECT_ROOT/.cache/ams02-prepared/target_study_pool.jsonl" \
  "$@"
