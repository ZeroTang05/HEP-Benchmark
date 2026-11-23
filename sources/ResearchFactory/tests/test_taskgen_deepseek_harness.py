"""DeepSeek Harness 适配器的请求契约测试。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from researchfactory.taskgen.agents.deepseek_harness import (
    DeepSeekHarnessAgent,
    _node_binary,
    build_request,
)
from researchfactory.taskgen.core import registry
from researchfactory.core.config import load_config
from researchfactory.taskgen.core.types import TaskGenJob


def _job(tmp_path: Path) -> TaskGenJob:
    return TaskGenJob(
        target={"paper_id": "paper:hep-1", "experiment": "AMS-02"},
        workspace=tmp_path / "workspace",
        prompt="Build one HEP task.",
        system_prompt="Use only the task workspace.",
        task_type="researchclawbench",
        model="deepseek-chat",
        reasoning_effort="high",
        max_turns=12,
        timeout=900,
        extra={"runtime_profile": "hep-rcb"},
    )


def test_build_request_preserves_runtime_boundary(tmp_path: Path) -> None:
    request = build_request(_job(tmp_path))

    assert request["schema_version"] == "researchfactory.deepseek_harness_request.v1"
    assert request["target_id"] == "paper:hep-1"
    assert request["provider"] == "deepseek-official"
    assert request["model"] == "deepseek-chat"
    assert request["runtime_profile"] == "hep-rcb"
    assert request["workspace"].endswith("/workspace")
    assert request["trace_dir"].endswith("/workspace/agent_trace")
    assert request["metadata"]["target"]["experiment"] == "AMS-02"
    assert request["research_services"] is False
    assert request["harness_home"].endswith("/.cache/deepseek-harness")


def test_research_services_require_explicit_job_permission(tmp_path: Path) -> None:
    """资料访问由任务类型声明，其他阶段默认关闭。"""
    job = _job(tmp_path)
    job.extra["research_services"] = True
    assert build_request(job)["research_services"] is True
    job.extra.clear()
    for task_type in ("hep_solver", "researchclawbench_judge"):
        job.task_type = task_type
        assert build_request(job)["research_services"] is False


def test_dry_run_writes_only_deepseek_request(tmp_path: Path) -> None:
    job = _job(tmp_path)
    job.dry_run = True

    result = DeepSeekHarnessAgent().generate(job)

    assert result.ok
    request_path = job.workspace / "deepseek_harness_request.json"
    assert request_path.is_file()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["prompt"].startswith("Use only the task workspace.")
    assert request["prompt"].endswith("Build one HEP task.")
    assert not (job.workspace / "_prompt.txt").exists()


def test_registry_and_config_expose_deepseek_harness() -> None:
    assert "deepseek_harness" in registry.available_agents()
    settings = load_config([], apply_compat=False)
    assert settings.taskgen.agent_configs["deepseek_harness"].model == "deepseek-chat"


def test_deepseek_bridge_has_valid_node_syntax() -> None:
    bridge = Path(__file__).parents[3] / "runtime" / "deepseek-harness" / "run_task.mjs"
    completed = subprocess.run(
        ["node", "--check", str(bridge)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_deepseek_runtime_has_locked_cli_dependency() -> None:
    package = Path(__file__).parents[3] / "runtime" / "deepseek-harness" / "package.json"
    lockfile = package.with_name("package-lock.json")
    package_data = json.loads(package.read_text(encoding="utf-8"))
    lock_data = json.loads(lockfile.read_text(encoding="utf-8"))
    assert package_data["dependencies"]["@deepseek-ai/dsh"] == "0.2.2-rc.1"
    assert lock_data["packages"]["node_modules/@deepseek-ai/dsh"]["version"] == "0.2.2-rc.1"


def test_deepseek_node_binary_can_be_pinned(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_HARNESS_NODE", "/opt/node24/bin/node")

    assert _node_binary() == "/opt/node24/bin/node"


def test_deepseek_request_supports_anthropic_compatible_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEEPSEEK_PROVIDER", "minimax-anthropic")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.minimaxi.com/anthropic")
    monkeypatch.setenv("DEEPSEEK_API_KEY_ENV", "MINIMAX_API_KEY")
    monkeypatch.setenv("DEEPSEEK_API_PROTOCOL", "anthropic-messages")
    monkeypatch.setenv("DEEPSEEK_MODEL", "MiniMax-M3.1-Flash")
    job = _job(tmp_path)

    request = build_request(job)

    assert request["provider"] == "minimax-anthropic"
    assert request["base_url"] == "https://api.minimaxi.com/anthropic"
    assert request["api"] == "anthropic-messages"
    assert request["api_key_env"] == "MINIMAX_API_KEY"
    assert request["model"] == "MiniMax-M3.1-Flash"


def test_deepseek_adapter_resolves_relative_workspace_before_bridge(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    job = _job(tmp_path)
    job.workspace = Path("relative-workspace")
    job.dry_run = True

    result = DeepSeekHarnessAgent().generate(job)

    assert result.ok
    request_path = Path(result.meta["request_path"])
    assert request_path.is_absolute()
    assert request_path.is_file()
