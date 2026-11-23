"""加载共用资料服务凭证，运行独立论文预处理入口。"""
from __future__ import annotations

import os

from model_profiles import load_tool_env
from researchfactory.recall.paper_assets import main


if __name__ == "__main__":
    os.environ.update(load_tool_env())
    raise SystemExit(main())
