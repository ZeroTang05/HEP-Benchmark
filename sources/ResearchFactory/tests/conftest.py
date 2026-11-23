"""让依赖真实试跑产物的测试使用本次指定的结果目录。"""
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--hep-run-dir", help="真实 AMS 单篇试跑的结果目录")


@pytest.fixture(scope="session")
def hep_run(pytestconfig: pytest.Config) -> Path:
    """常规测试无需历史记录；指定目录后，路径错误立即报出。"""
    configured = pytestconfig.getoption("--hep-run-dir")
    if configured is None:
        pytest.skip("用 --hep-run-dir 指定当前真实试跑的结果目录")
    directory = Path(configured).expanduser().resolve()
    if not directory.is_dir():
        raise pytest.UsageError(f"找不到试跑结果目录：{directory}")
    return directory
