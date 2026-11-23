"""检查本机 Node 版本，并用锁文件安装官方 DeepSeek Harness。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from model_profiles import PROJECT_ROOT, load_runtime_env


def main() -> None:
    """安装不需要模型密钥；Node 路径由本机运行配置提供。"""
    env = {**os.environ, **load_runtime_env()}
    node = env.get("DEEPSEEK_HARNESS_NODE", "node")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    if tuple(int(part) for part in version.removeprefix("v").split(".")) < (24, 9, 0):
        raise RuntimeError("DeepSeek Harness 需要 Node 24.9.0 或更高版本")
    if Path(node).is_absolute():
        env["PATH"] = str(Path(node).parent) + os.pathsep + env["PATH"]
    subprocess.run(["npm", "ci"], cwd=PROJECT_ROOT / "runtime/deepseek-harness", env=env, check=True)
    print("HEP 本机环境安装完成。")


if __name__ == "__main__":
    main()
