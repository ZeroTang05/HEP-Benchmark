"""汇总 HEP 批量任务的运行、审查和评分结果。"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from researchfactory.taskgen.core.stages import built_tasks
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import CAPABILITIES, task_capabilities
from researchfactory.utils.storage import atomic_write_json


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _workspace_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"batch input root not found: {root}")
    return sorted(path for path in root.iterdir() if path.is_dir() and (path / "_meta.json").is_file())


def _stage_file(root: Path | None, workspace: Path, name: str) -> Path:
    """支持按 workspace 分目录或单批次根目录两种归档布局。"""
    if root is None:
        return workspace / name
    nested = root / workspace.name / name
    return nested if nested.is_file() else root / name


def collect_batch_report(
    input_root: Path,
    *,
    review_root: Path | None = None,
    score_root: Path | None = None,
    judge_root: Path | None = None,
    solver_root: Path | None = None,
    target_ids: set[str] | None = None,
    prior_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总一个批次，不对缺失阶段做猜测。"""
    entries: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    assessment_counts: Counter[str] = Counter()
    for workspace in _workspace_dirs(input_root):
        meta = _json(workspace / "_meta.json")
        if target_ids is not None and meta.get("target_id") not in target_ids:
            continue
        runtime = _json(workspace / "deepseek_harness_result.json")
        for child, child_meta in built_tasks(workspace, meta):
            variant = child_meta.get("task_variant")
            name = child_meta.get("task_workspace_name", workspace.name)

            def artifact(root: Path | None, filename: str) -> dict[str, Any]:
                if variant:
                    return _json(root / name / filename if root is not None else child / filename)
                return _json(_stage_file(root, workspace, filename))

            review = artifact(review_root, "hep_review_report.json")
            published = review.get("status") in {"accepted", "repaired"}
            reviewed = review_root / name if review_root else child
            evaluation = artifact(score_root, "evaluation.json") if not variant or published else {}
            judge = artifact(judge_root, "judge_result.json") if not variant or published else {}
            solver = _json(solver_root / name / "_meta.json") if solver_root and (not variant or published) else {}
            status = str(meta.get("status") or runtime.get("status") or "unknown")
            status_counts[status] += 1
            assessment_counts[str(judge.get("scientific_assessment") or "not_evaluated")] += 1
            entries.append({
                "workspace": name,
                **({"paper_workspace": workspace.name, "task_variant": variant} if variant else {}),
                "target_id": meta.get("target_id"),
                "status": status,
                "validation_ok": meta.get("validation_ok"),
                "duration_s": meta.get("duration_s"),
                "attempts": meta.get("attempts"),
                "agent": meta.get("agent"),
                "model": meta.get("model"),
                "event_count": runtime.get("event_count"),
                "review_status": review.get("status"),
                "generated_capabilities": task_capabilities(child / "task"),
                "reviewed_capabilities": task_capabilities(reviewed / "task") if published else [],
                "capability_scores": judge.get("capability_scores", {}),
                "solver_status": solver.get("status"),
                "solver_duration_s": solver.get("duration_s"),
                "deterministic_score": evaluation.get("score"),
                "deterministic_passed": evaluation.get("passed"),
                "rubric_score": judge.get("rubric_score"),
                "scientific_assessment": judge.get("scientific_assessment"),
                "fully_accepted": published and evaluation.get("passed") is True and judge.get("scientific_assessment") == "accepted",
            })
    prior = prior_coverage or {}
    coverage_entries = {
        (entry["target_id"], entry["workspace"]): entry
        for entry in prior.get("capability_coverage_entries", [])
    }
    for entry in entries:
        coverage_entries[entry["target_id"], entry["workspace"]] = {
            key: entry[key] for key in (
                "target_id", "workspace", "review_status", "generated_capabilities", "reviewed_capabilities", "capability_scores",
            )
        }
    coverage_rows = list(coverage_entries.values())
    has_review = review_root is not None or prior.get("capability_coverage_basis") == "reviewed"
    coverage_basis = "reviewed" if has_review or any(entry["review_status"] for entry in coverage_rows) else "generated"
    coverage = {}
    for capability in CAPABILITIES:
        generated = [entry for entry in coverage_rows if capability in entry["generated_capabilities"]]
        reviewed = [entry for entry in coverage_rows if capability in entry["reviewed_capabilities"]]
        scored = [entry["capability_scores"][capability] for entry in reviewed if capability in entry["capability_scores"]]
        coverage[capability] = {
            "generated_tasks": len(generated),
            "generated_papers": len({entry["target_id"] for entry in generated}),
            "reviewed_tasks": len(reviewed),
            "reviewed_papers": len({entry["target_id"] for entry in reviewed}),
            "scored_tasks": len(scored),
            "mean_score": math.fsum(scored) / len(scored) if scored else None,
        }
    return {
        "schema_version": "researchfactory.hep_batch_report.v1",
        "input_root": str(input_root),
        "count": len(entries),
        "paper_count": len({entry["target_id"] for entry in entries if entry["target_id"]}),
        "status_counts": dict(sorted(status_counts.items())),
        "assessment_counts": dict(sorted(assessment_counts.items())),
        "capability_coverage_basis": coverage_basis,
        "capability_coverage": coverage,
        "capability_coverage_entries": coverage_rows,
        "uncovered_capabilities": [
            name for name in CAPABILITIES if not coverage[name][f"{coverage_basis}_tasks"]
        ],
        "entries": entries,
    }


def write_batch_report(path: Path, report: dict[str, Any]) -> None:
    """写入 JSON 汇总。"""
    atomic_write_json(path, report)
