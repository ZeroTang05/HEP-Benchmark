#!/usr/bin/env bash
# 激活本机 HEP 环境，源码与缓存统一从当前项目读取。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export RESEARCHFACTORY_BUNDLE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
export VIRTUAL_ENV="${RESEARCHFACTORY_VENV:-$RESEARCHFACTORY_BUNDLE_ROOT/.venv}"
export RF_CACHE_DIR="${RF_CACHE_DIR:-$RESEARCHFACTORY_BUNDLE_ROOT/.cache/researchfactory-mineru-rebuild-20261118}"
export PYTHONPATH="$RESEARCHFACTORY_BUNDLE_ROOT/sources/ResearchFactory${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$VIRTUAL_ENV/bin:$PATH"
if [[ ! -x "$VIRTUAL_ENV/bin/python" ]]; then
  echo "缺少 Python 环境，请先运行 bash scripts/setup.sh" >&2
  return 1 2>/dev/null || exit 1
fi
