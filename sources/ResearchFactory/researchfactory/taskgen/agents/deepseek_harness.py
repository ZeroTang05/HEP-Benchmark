"""DeepSeek Harness 任务生成适配器。

ResearchFactory 只负责准备工作区和运行请求；Agent 循环、原生工具、会话
和模型调用全部交给 DeepSeek Harness 的 Node bridge。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from researchfactory.taskgen.core.registry import register_agent
from researchfactory.taskgen.core.types import AgentAdapter, AgentResult, TaskGenJob
from researchfactory.utils.storage import atomic_write_json


REQUEST_NAME = "deepseek_harness_request.json"
RESULT_NAME = "deepseek_harness_result.json"
TRACE_DIRNAME = "agent_trace"


def build_request(job: TaskGenJob) -> dict[str, Any]:
    """把统一任务对象转换成 DeepSeek Harness 的运行请求。"""
    trace_dir = job.workspace / TRACE_DIRNAME
    instructions = []
    if job.system_prompt:
        instructions.append(job.system_prompt)
    for directory in ((job.skill_dir,) if job.skill_dir else ()) + job.additional_skill_dirs:
        instructions.append((directory / "SKILL.md").read_text(encoding="utf-8"))
    prompt = "\n\n".join([*instructions, job.prompt])
    provider = str(
        job.extra.get("provider")
        or os.environ.get("DEEPSEEK_PROVIDER")
        or "deepseek-official"
    )
    base_url = str(
        job.extra.get("base_url")
        or os.environ.get("DEEPSEEK_BASE_URL")
        or ""
    )
    api_key_env = str(
        job.extra.get("api_key_env")
        or os.environ.get("DEEPSEEK_API_KEY_ENV")
        or "DEEPSEEK_API_KEY"
    )
    api = str(
        job.extra.get("api")
        or os.environ.get("DEEPSEEK_API_PROTOCOL")
        or ("anthropic-messages" if base_url and provider != "deepseek-official" else "")
    )
    model = str(
        job.extra.get("model")
        or os.environ.get("DEEPSEEK_MODEL")
        or job.model
    )
    return {
        "schema_version": "researchfactory.deepseek_harness_request.v1",
        "target_id": job.target_id,
        "task_type": job.task_type,
        "provider": provider,
        "model": model,
        "input_modalities": json.loads(os.environ.get("DEEPSEEK_MODEL_INPUT", '["text"]')),
        "api": api,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "reasoning_effort": job.reasoning_effort,
        "prompt": prompt,
        "system_prompt": job.system_prompt or "",
        "workspace": str(job.workspace.resolve()),
        "trace_dir": str(trace_dir.resolve()),
        "max_turns": int(job.max_turns),
        "timeout_seconds": int(job.timeout),
        "runtime_profile": str(job.extra.get("runtime_profile") or "hep-rcb"),
        "research_services": bool(job.extra.get("research_services", False)),
        "delegate_task_series": bool(job.extra.get("delegate_task_series", False)),
        **({"submission_marker": job.extra["submission_marker"]} if job.extra.get("submission_marker") else {}),
        "harness_home": str(_runner_path().parents[2] / ".cache/deepseek-harness"),
        "metadata": {
            "target": dict(job.target),
            "skill_name": job.skill_name,
        },
    }


def _runner_path() -> Path:
    configured = os.environ.get("DEEPSEEK_HARNESS_RUNNER", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    bundle_root = os.environ.get("RESEARCHFACTORY_BUNDLE_ROOT", "").strip()
    if bundle_root:
        return Path(bundle_root) / "runtime" / "deepseek-harness" / "run_task.mjs"
    return Path(__file__).resolve().parents[5] / "runtime" / "deepseek-harness" / "run_task.mjs"


def _node_binary() -> str:
    """返回 DeepSeek Harness 使用的 Node 可执行文件。"""
    return os.environ.get("DEEPSEEK_HARNESS_NODE", "node").strip() or "node"


def _write_request(path: Path, request: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _stop_process(process: subprocess.Popen) -> None:
    """终止本轮模型进程组，并回收子进程占用的管道。"""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # 模型进程可能已正常退出。
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()


@register_agent
class DeepSeekHarnessAgent(AgentAdapter):
    """通过独立 Node bridge 运行 DeepSeek Harness。"""

    name = "deepseek_harness"
    uses_skill = True
    supports_session_resume = True

    def generate(self, job: TaskGenJob) -> AgentResult:
        workspace = job.workspace.resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        job.workspace = workspace
        request = build_request(job)
        request_path = workspace / REQUEST_NAME
        _write_request(request_path, request)

        if job.dry_run:
            return AgentResult(
                status="ok",
                returncode=0,
                message="dry-run",
                workspace=job.workspace,
                meta={"dry_run": True, "request_path": str(request_path)},
            )

        return self._execute(request, request_path, timeout=job.timeout)

    def resume_interrupted(self, workspace: Path, *, timeout: int) -> AgentResult:
        """复用原生会话的检查记录，另存恢复轨迹，避免重复执行整轮审核。"""
        request = json.loads((workspace / REQUEST_NAME).read_text(encoding="utf-8"))
        if not request.get("resume_session_id"):
            events_path = Path(request["trace_dir"]) / "deepseek_harness_events.jsonl"
            with events_path.open(encoding="utf-8") as stream:
                events = (json.loads(line) for line in stream)
                session = next((event for event in events if event.get("type") == "session"), None)
            if session is None:
                raise ValueError(f"缺少可恢复的原生会话记录：{events_path}")
            request["resume_session_id"] = session["sessionId"]
        request["prompt"] = (
            "Continue the existing task from this session's completed checks and tool results. "
            "Preserve the previous work. Do not repeat completed inspections or calculations. "
            "Finish the required output files and report any remaining unresolved findings honestly. "
            "Then finish the task."
        )
        trace = workspace / TRACE_DIRNAME / f"resume-{uuid.uuid4().hex[:12]}"
        trace.mkdir(parents=True)
        request["trace_dir"] = str(trace.resolve())
        request["timeout_seconds"] = timeout
        request_path = trace / REQUEST_NAME
        _write_request(request_path, request)
        # 根请求指向最近一次轨迹，后续再次恢复可以继续同一原生会话。
        _write_request(workspace / REQUEST_NAME, request)
        return self._execute(request, request_path, timeout=timeout)

    def _execute(self, request: Mapping[str, Any], request_path: Path, *, timeout: int) -> AgentResult:
        """运行独立进程组；超时或中断时终止包括原生工具在内的子进程。"""
        workspace = Path(request["workspace"])

        runner = _runner_path()
        if not runner.is_file():
            raise FileNotFoundError(
                f"DeepSeek Harness bridge not found: {runner}. "
                "Set DEEPSEEK_HARNESS_RUNNER or install runtime/deepseek-harness/run_task.mjs."
            )

        result_path = workspace / RESULT_NAME
        started = time.monotonic()
        marker_name = request.get("submission_marker")
        if marker_name and (Path(marker_name).name != marker_name or marker_name in {".", ".."}):
            raise ValueError(f"无效的任务提交文件名：{marker_name!r}")
        submission = workspace / marker_name if marker_name else None
        if submission is not None and submission.exists():
            raise FileExistsError(f"任务提交标记已存在：{submission}")
        deadline = started + timeout if timeout else None
        command = [_node_binary(), str(runner), "--request", str(request_path)]
        with subprocess.Popen(
            command,
            cwd=str(workspace),
            env={**os.environ, "RESEARCHFACTORY_PYTHON": sys.executable},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as process:
            while True:
                remaining = max(0.0, deadline - time.monotonic()) if deadline is not None else None
                wait = remaining
                if submission is not None:
                    wait = min(1.0, remaining) if remaining is not None else 1.0
                try:
                    _stdout, stderr = process.communicate(timeout=wait)
                    break
                except KeyboardInterrupt:
                    _stop_process(process)
                    raise
                except subprocess.TimeoutExpired:
                    if submission is not None and submission.is_file():
                        # 模型已交付。停止它继续写文件，由批处理重新校验后再导出。
                        _stop_process(process)
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        _stop_process(process)
                        return AgentResult(
                            status="timeout", workspace=workspace, duration_s=time.monotonic() - started,
                            message=f"DeepSeek Harness timed out after {timeout}s",
                            meta={"request_path": str(request_path), "command": command},
                        )

        duration = time.monotonic() - started
        if submission is not None and submission.is_file():
            result = {
                "schema_version": "researchfactory.deepseek_harness_result.v1",
                "status": "submitted",
                "events_path": str(Path(request["trace_dir"]) / "deepseek_harness_events.jsonl"),
            }
            atomic_write_json(result_path, result)
            return AgentResult(
                status="ok", workspace=workspace, duration_s=duration, message="任务组已提交",
                meta={"request_path": str(request_path), "result": result},
            )
        if not result_path.is_file():
            raise RuntimeError(
                "DeepSeek Harness bridge exited without writing "
                f"{result_path}: {stderr[-2000:]}"
            )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        status = str(result.get("status") or "").strip()
        if process.returncode != 0 or status != "completed":
            return AgentResult(
                status="failed",
                returncode=process.returncode,
                workspace=workspace,
                duration_s=duration,
                message=str(result.get("message") or stderr)[-2000:],
                meta={"request_path": str(request_path), "result": result},
            )
        return AgentResult(
            status="ok",
            returncode=0,
            workspace=workspace,
            duration_s=duration,
            message=str(result.get("final_message") or "ok"),
            meta={"request_path": str(request_path), "result": result},
        )
