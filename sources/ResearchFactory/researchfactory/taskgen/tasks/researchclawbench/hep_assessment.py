"""审核通过后自动运行真实 Solver、数值评分和科学 Judge，并保存可恢复的阶段记录。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from researchfactory.core.cache import Cache
from researchfactory.recall.paper_assets import sha256_file
from researchfactory.taskgen.core.registry import get_agent
from researchfactory.taskgen.core.runner import _review_sources
from researchfactory.taskgen.core.types import TaskGenJob
from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT
from researchfactory.taskgen.tasks.researchclawbench.hep_batch_report import collect_batch_report, write_batch_report
from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import CONTRACT as EVALUATOR, MIN_FIGURE_PIXELS
from researchfactory.taskgen.tasks.researchclawbench.hep_judge import (
    observable_trace, run_hep_judge, uses_trace, validate_judgement,
)
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import rubric_enabled, rubric_path
from researchfactory.utils.storage import atomic_write_json, read_jsonl

SOLVER_POLICY = """Solve the scientific task using only the public inputs under `task/` and
the public output format in `output_contract.json`. Work inside this workspace.
Use the preinstalled Python environment and the native tools of your Agent Runtime.
Do not use the network, search for the target paper, inspect parent directories,
install packages, or access hidden evaluation materials. Do not modify the public
inputs or output contract. Do the scientific work requested by the task. For
numerical analysis, account for units, binning, uncertainties, and correlation.
Write the required outputs under `solution/` using the exact paths and JSON field
names in the output contract. When you do computations, save executable analysis
code in `solution/` for reproducibility. Generate any required plots from the data.
If you cannot solve a part, record that limitation in your report without inventing
data or results. Finish after checking the outputs you have produced.
"""
SOLVER_TRACE_POLICY = """
After inspecting the provided inputs and before starting the main analysis,
state your short plan in a visible progress message. If the task calls for
predictions or competing explanations, state them before running their tests.
Record material changes to the approach as they happen.
"""


def record_phase(root: Path, phase: str, task: str) -> None:
    """记录解题、评分、评审的实时位置，错误时保留准确阶段。"""
    path = root / "workflow_state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"status": "running", "completed_stages": []}
    state.update(current_stage=phase, current_task=task, updated_at=datetime.now(timezone.utc).isoformat())
    atomic_write_json(path, state)


def tree_digest(root: Path) -> str:
    """目录中文件名和内容共同决定阶段输入是否变化。"""
    return Cache.key([
        (path.relative_to(root).as_posix(), sha256_file(path))
        for path in sorted(root.rglob("*")) if path.is_file()
    ])


def public_output_contract(task_root: Path) -> dict[str, Any]:
    """公开提交格式，不暴露参考答案、评分容差和内部参考键名。"""
    schema, _reference = EVALUATOR.load_spec(task_root)
    return {
        "schema_version": "researchfactory.hep_public_output_contract.v1",
        "required_outputs": [
            {key: item[key] for key in ("path", "format", "required_keys") if key in item}
            for item in schema["required_outputs"]
        ],
        "numeric_fields": [
            {"output": item["output"], "json_pointer": item["json_pointer"]}
            for item in schema.get("numeric_checks", [])
        ],
        "figure_min_pixels_per_axis": MIN_FIGURE_PIXELS,
    }


def _cached_stage(root: Path, fingerprint: str) -> dict[str, Any] | None:
    """只复用输入和产物都未变化的已完成阶段。"""
    marker = root / "_meta.json"
    if not marker.is_file():
        return None
    record = json.loads(marker.read_text(encoding="utf-8"))
    if record["input_fingerprint"] != fingerprint or record["status"] != "ok":
        return None
    if tree_digest(Path(record["artifacts"])) != record["artifacts_sha256"]:
        raise ValueError(f"已完成阶段的产物被修改：{root}")
    return record


def run_solver(task_root: Path, root: Path, *, model: str, timeout: int) -> dict[str, Any]:
    """在独立目录中运行解题代理，只提供公开材料和提交格式。"""
    contract = public_output_contract(task_root)
    groups = json.loads(rubric_path(task_root).read_text(encoding="utf-8"))["groups"] if rubric_enabled(task_root) else []
    trace_required = uses_trace(groups)
    prompt = SOLVER_POLICY + (SOLVER_TRACE_POLICY if trace_required else "")
    public_hash = Cache.key(
        sha256_file(task_root / "task_info.json"),
        tree_digest(task_root / "data"), tree_digest(task_root / "related_work"),
    )
    fingerprint = Cache.key(
        "hep_solver.v2", public_hash, contract, prompt, model,
        os.environ.get("DEEPSEEK_PROVIDER"), os.environ.get("DEEPSEEK_BASE_URL"),
    )
    cached = _cached_stage(root, fingerprint)
    if cached is not None:
        return cached
    workspace = root / "attempts" / uuid.uuid4().hex[:12]
    workspace.mkdir(parents=True)
    public = workspace / "task"
    CONTRACT.export_public(task_root, public)
    atomic_write_json(workspace / "output_contract.json", contract)
    output = workspace / "solution"
    output.mkdir()
    immutable = Cache.key(tree_digest(public), sha256_file(workspace / "output_contract.json"))
    atomic_write_json(root / "_meta.json", {"status": "running", "input_fingerprint": fingerprint, "workspace": str(workspace)})
    result = get_agent("deepseek_harness").generate(TaskGenJob(
        target={"target_id": "public-hep-task"}, workspace=workspace,
        prompt=prompt, task_type="hep_solver", model=model,
        max_turns=120, timeout=timeout,
    ))
    if not result.ok:
        raise RuntimeError(f"Solver 运行失败：{result.status}: {result.message}")
    if immutable != Cache.key(tree_digest(public), sha256_file(workspace / "output_contract.json")):
        raise ValueError("Solver 修改了公开输入或提交格式")
    record = {
        "status": "ok", "input_fingerprint": fingerprint, "workspace": str(workspace),
        "artifacts": str(output), "artifacts_sha256": tree_digest(output),
        "model": model, "duration_s": result.duration_s,
        "event_count": result.meta["result"]["event_count"],
        "events_path": result.meta["result"]["events_path"],
    }
    atomic_write_json(root / "_meta.json", record)
    return record


def run_assessment(
    task_root: Path, output_root: Path, *, model: str, solver_timeout: int, judge_timeout: int,
) -> dict[str, Any]:
    """数值未通过仍交给 Judge 解释，运行错误则直接终止。"""
    name = task_root.parent.name
    record_phase(output_root, "solver", name)
    print(f"[solver] {name}", flush=True)
    solver = run_solver(task_root, output_root / "solver" / name, model=model, timeout=solver_timeout)
    record_phase(output_root, "score", name)
    evaluation = EVALUATOR.evaluate(task_root, Path(solver["artifacts"])).to_dict()
    solver_trace = observable_trace(Path(solver["events_path"])) if uses_trace(evaluation.get("rubric", [])) else None
    score_root = output_root / "scores" / name
    score_path = score_root / "evaluation.json"
    atomic_write_json(score_path, evaluation)
    print(f"[score] {name}: {evaluation['score']:.4f}, passed={evaluation['passed']}", flush=True)

    judge_root = output_root / "judge" / name
    record_phase(output_root, "judge", name)
    fingerprint = Cache.key(
        "hep_judge.v2", solver["artifacts_sha256"], public_output_contract(task_root), solver_trace,
        sha256_file(task_root / "task_info.json"), tree_digest(task_root / "data"),
        tree_digest(task_root / "related_work"), evaluation, model,
        Path(__file__).with_name("hep_judge.py").read_text(encoding="utf-8"),
        os.environ.get("DEEPSEEK_PROVIDER"), os.environ.get("DEEPSEEK_BASE_URL"),
    )
    cached = _cached_stage(judge_root, fingerprint)
    if cached is None:
        print(f"[judge] {name}", flush=True)
        workspace = judge_root / "attempts" / uuid.uuid4().hex[:12]
        atomic_write_json(judge_root / "_meta.json", {"status": "running", "input_fingerprint": fingerprint, "workspace": str(workspace)})
        result = run_hep_judge(
            task_root=task_root, solver_root=Path(solver["artifacts"]), evaluation_path=score_path,
            workspace=workspace, model=model, timeout=judge_timeout,
            solver_trace=solver_trace,
        )
        if not result.ok:
            raise RuntimeError(f"Judge 运行失败：{result.status}: {result.message}")
        # 复用检查只核对最终报告，运行轨迹仍独立保留。
        artifacts = judge_root / "result"
        artifacts.mkdir(exist_ok=True)
        shutil.copy2(workspace / "judge_result.json", artifacts / "judge_result.json")
        atomic_write_json(judge_root / "_meta.json", {
            "status": "ok", "input_fingerprint": fingerprint, "workspace": str(workspace),
            "artifacts": str(artifacts), "artifacts_sha256": tree_digest(artifacts),
            "model": model, "duration_s": result.duration_s,
        })
    judgement = validate_judgement(judge_root / "result/judge_result.json", evaluation, solver_trace=solver_trace)
    atomic_write_json(judge_root / "judge_result.json", judgement)
    return {"workspace": name, "evaluation": evaluation, "judgement": judgement}


def main() -> int:
    """对已审核通过的任务执行完整评估并汇总。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--pool", type=Path, help="仅评估本轮论文清单中的任务")
    parser.add_argument("--coverage-report", type=Path, help="合并已有题包的能力覆盖")
    parser.add_argument("--solver-timeout", type=int, default=1800)
    parser.add_argument("--judge-timeout", type=int, default=900)
    args = parser.parse_args()
    target_ids = {row["paper_id"] for row in read_jsonl(args.pool)} if args.pool else None
    prior_coverage = json.loads(args.coverage_report.read_text(encoding="utf-8")) if args.coverage_report else None
    tasks = []
    for source in _review_sources(args.build_root, "researchclawbench", None, target_ids):
        name = source.meta.get("task_workspace_name", source.workspace.name)
        directory = args.review_root / name
        meta = json.loads((directory / "_meta.json").read_text(encoding="utf-8"))
        if (meta["target_id"] != source.meta["target_id"]
                or Path(meta["source_workspace"]).resolve() != source.workspace.resolve()
                or not meta["validation_ok"] or meta["status"] not in {"ok", "rejected"}):
            raise ValueError(f"缺少本轮有效的任务审核结果：{directory}")
        if meta["status"] == "ok":
            tasks.append(directory / "task")
    def write_report() -> None:
        report = collect_batch_report(
            args.build_root, review_root=args.review_root,
            score_root=args.output_root / "scores", judge_root=args.output_root / "judge",
            solver_root=args.output_root / "solver", target_ids=target_ids,
            prior_coverage=prior_coverage,
        )
        write_batch_report(args.output_root / "batch_report.json", report)

    write_report()
    for task in tasks:
        run_assessment(task, args.output_root, model=args.model, solver_timeout=args.solver_timeout, judge_timeout=args.judge_timeout)
        record_phase(args.output_root, "report", task.parent.name)
        write_report()
    state_path = args.output_root / "workflow_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(status="completed", current_stage=None, current_task=None)
    atomic_write_json(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
