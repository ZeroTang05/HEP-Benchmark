"""使用本机真实模型配置和论文缓存验证 AMS 启动入口，不调用模型。"""
from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.model_profiles import CREDENTIAL_ENV, TOOL_CREDENTIALS, load_profile, load_tool_env

PROJECT_ROOT = Path(__file__).parents[3]
SCRIPT = PROJECT_ROOT / "scripts/run_ams_hep_batch.sh"


def launch(*arguments: str) -> subprocess.CompletedProcess[str]:
    """执行真实启动脚本，保留输出供断言使用。"""
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_ams_hep_batch_script_has_valid_shell_syntax() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_real_profile_stages_one_cached_paper(tmp_path: Path) -> None:
    """真实配置必须完整传入模型请求，同时保持凭证只在进程环境中。"""
    profile = load_profile()
    output_root = tmp_path / "local"
    result = launch(
        "--limit", "1", "--dry-run",
        "--output-root", str(output_root),
    )
    if result.returncode:
        pytest.fail((result.stdout + result.stderr).replace(profile.api_key, "<redacted>"))
    requests = list(output_root.glob("*/deepseek_harness_request.json"))
    assert len(requests) == 1
    request_text = requests[0].read_text(encoding="utf-8")
    request = json.loads(request_text)
    assert request["provider"] == profile.provider
    assert request["model"] == profile.model
    assert request["base_url"] == profile.base_url
    assert request["api"] == profile.api
    assert request["api_key_env"] == CREDENTIAL_ENV
    assert request["research_services"] is True
    assert (requests[0].parent / "task/target_study/paper.pdf").is_file()
    assert (output_root / "hep_batch_plan.jsonl").is_file()
    for path in output_root.rglob("*.json"):
        if profile.api_key in path.read_text(encoding="utf-8"):
            pytest.fail(f"模型凭证进入运行产物：{path.name}")
    if profile.api_key in result.stdout + result.stderr + repr(profile):
        pytest.fail("模型凭证进入启动器输出或对象展示")


def test_tool_credentials_are_passed_to_the_runtime() -> None:
    """从真实配置读取资料服务凭证，并检查公开配置只包含非敏感信息。"""
    credentials = load_tool_env()
    assert all(bool(credentials.get(key)) for key in TOOL_CREDENTIALS)
    profile = load_profile()
    env = profile.runtime_env()
    assert all(env.get(key) == credentials[key] for key in TOOL_CREDENTIALS)
    assert all(secret not in json.dumps(profile.public_config()) for secret in credentials.values())


def test_default_profile_is_local_config() -> None:
    result = launch("--show-config")
    assert result.returncode == 0, result.stderr
    displayed = json.loads(result.stdout)
    assert displayed["profile"] == "local"
    assert displayed["credentials_file"] == str(PROJECT_ROOT / ".env")
    assert displayed["model"] == load_profile().model
    assert stat.S_IMODE((PROJECT_ROOT / ".env").stat().st_mode) == 0o600


def test_real_runtime_uses_node_24_or_newer() -> None:
    """直接运行所配置的 Node，避免服务器的旧系统版本进入真实模型流程。"""
    env = load_profile().runtime_env()
    node = env.get("DEEPSEEK_HARNESS_NODE", "node")
    result = subprocess.run([node, "--version"], capture_output=True, text=True, env=env, check=True)
    assert int(result.stdout.strip().removeprefix("v").split(".")[0]) >= 24


def test_explicit_model_overrides_profile_model() -> None:
    model = "qwen3.8-27b"
    result = launch("--profile", "local", "--model", model, "--show-config")
    assert result.returncode == 0, result.stderr
    displayed = json.loads(result.stdout)
    assert displayed["profile"] == "local"
    assert displayed["model"] == model
    assert displayed["base_url"] == load_profile("local").base_url


def test_unknown_profile_stops_before_staging(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    result = launch("--profile", "unconfigured", "--output-root", str(output))
    assert result.returncode == 1
    assert "未知模型配置" in result.stderr
    assert not output.exists()
