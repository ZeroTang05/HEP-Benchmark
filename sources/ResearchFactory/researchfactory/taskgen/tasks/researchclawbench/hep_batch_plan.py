"""为 HEP 论文池生成模型运行前的批量任务计划。"""
from __future__ import annotations

import json
from collections import Counter
from itertools import cycle
from pathlib import Path
from typing import Any, Iterable

from researchfactory.taskgen.tasks.researchclawbench.hep_evidence import normalize_evidence_sources
from researchfactory.taskgen.tasks.researchclawbench.hep_plan import infer_hep_task_plan
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import CAPABILITIES
from researchfactory.taskgen.tasks.researchclawbench.initializer import is_hep_target


PLAN_SCHEMA = "researchfactory.hep_batch_plan.v1"


def read_pool(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """读取论文池；JSONL 格式错误立即失败。"""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"pool line {line_number} is invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"pool line {line_number} must contain a JSON object")
            rows.append(value)
            if limit is not None and len(rows) >= limit:
                break
    if not rows:
        raise ValueError("HEP pool is empty")
    return rows


def build_batch_plan(
    rows: Iterable[dict[str, Any]], *, coverage_report: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """为论文池生成可审查的计划行和主任务分组统计。"""
    plan_rows: list[dict[str, Any]] = []
    groups: Counter[str] = Counter()
    report = {} if coverage_report is None else coverage_report
    if coverage_report is not None and (
        not isinstance(report, dict) or report.get("schema_version") != "researchfactory.hep_batch_report.v1"
    ):
        raise ValueError("能力覆盖输入必须是 HEP 批次报告")
    missing = report.get("uncovered_capabilities", CAPABILITIES)
    if not isinstance(missing, (list, tuple)) or any(name not in CAPABILITIES for name in missing):
        raise ValueError("批次报告包含未知的能力缺口")
    missing = [name for name in CAPABILITIES if name in missing]
    focuses = cycle(missing)
    for index, target in enumerate(rows):
        if not is_hep_target(target):
            paper_id = str(target.get("paper_id") or f"row-{index}")
            raise ValueError(f"pool row {paper_id!r} is not recognized as a HEP target")
        plan = infer_hep_task_plan(target)
        evidence = normalize_evidence_sources(target)
        row = {
            "schema_version": PLAN_SCHEMA,
            "paper_id": str(target.get("paper_id") or ""),
            "title": str(target.get("title") or ""),
            "taskgen_plan": plan.as_dict(),
            "capability_focus": next(focuses, None),
            "evidence_source_count": len(evidence),
            "evidence_sources": [
                {
                    "source_id": source.source_id,
                    "role": source.role,
                    "title": source.title,
                    "identifier": source.identifier,
                    "url": source.url,
                }
                for source in evidence
            ],
        }
        plan_rows.append(row)
        groups[plan.primary_task_type] += 1
    return plan_rows, dict(sorted(groups.items()))


def write_batch_plan(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """写 JSONL 计划文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
