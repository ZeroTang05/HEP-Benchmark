"""用真实 AMS 任务及真实科学审核报告验证 Reviewer 的合并与公开边界。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from researchfactory.taskgen.core.registry import get_review_type
from researchfactory.taskgen.tasks.researchclawbench.reviewer import (
    REPORT_NAME, REPORT_SCHEMA, SCIENTIFIC_REPORT_NAME, ResearchClawBenchReviewType,
)

def prepare_real_review(hep_run: Path, tmp_path: Path, *, include_science: bool = True):
    """复制已经完成真实试运行的包，不替换 Agent 或网络环境。"""
    reports = sorted((hep_run / "reviewed").glob(f"*/{SCIENTIFIC_REPORT_NAME}"))
    if not reports:
        pytest.skip("先完成真实 AMS 自动流程试运行，才能验收科学审核记录")
    source = reports[0].parent
    source_meta = json.loads((source / "_meta.json").read_text())
    reviewer = ResearchClawBenchReviewType()
    prepared = reviewer.prepare(
        source_workspace=source, workspace=tmp_path / "review",
        source_meta=source_meta,
    )
    if include_science:
        shutil.copy2(source / SCIENTIFIC_REPORT_NAME, prepared.workspace / SCIENTIFIC_REPORT_NAME)
    return reviewer, prepared


@pytest.mark.integration
def test_reviewer_preserves_real_scientific_findings_and_public_boundary(hep_run: Path, tmp_path: Path) -> None:
    reviewer, prepared = prepare_real_review(hep_run, tmp_path)
    scientific = json.loads((prepared.workspace / SCIENTIFIC_REPORT_NAME).read_text())
    reviewer.finalize(prepared.task_root)
    report = json.loads((prepared.workspace / REPORT_NAME).read_text())
    assert report["schema_version"] == REPORT_SCHEMA
    assert report["scientific_review"] == scientific
    assert len([check for check in report["checks"] if check["code"].startswith("science:")]) == 7
    assert reviewer.validate(prepared.task_root).ok
    public = tmp_path / "public"
    reviewer.export_public(prepared.task_root, public)
    assert (public / "task_info.json").is_file()
    assert not (public / "target_study").exists()
    assert not (public / "_paper_cache").exists()


@pytest.mark.integration
def test_missing_scientific_review_cannot_be_accepted(hep_run: Path, tmp_path: Path) -> None:
    reviewer, prepared = prepare_real_review(hep_run, tmp_path, include_science=False)
    with pytest.raises(FileNotFoundError):
        reviewer.finalize(prepared.task_root)
    assert not (prepared.workspace / REPORT_NAME).exists()


@pytest.mark.integration
def test_reviewer_retains_contract_failure_reason(hep_run: Path, tmp_path: Path) -> None:
    reviewer, prepared = prepare_real_review(hep_run, tmp_path)
    next((prepared.task_root / "data").glob("*.metadata.json")).unlink()
    reviewer.finalize(prepared.task_root)
    report = json.loads((prepared.workspace / REPORT_NAME).read_text())
    assert report["status"] == "failed"
    assert any(check["code"] == "hep_metadata_missing" for check in report["checks"])
    assert not reviewer.validate(prepared.task_root).ok


@pytest.mark.integration
def test_reviewer_rejects_hidden_key_leak_in_real_package(hep_run: Path, tmp_path: Path) -> None:
    reviewer, prepared = prepare_real_review(hep_run, tmp_path)
    reference = json.loads((prepared.task_root / "target_study/evaluator/reference.json").read_text())
    hidden_key = next(key for key in reference["values"] if len(key) >= 6)
    path = prepared.task_root / "task_info.json"
    info = json.loads(path.read_text())
    info["task"] += "\n" + hidden_key
    path.write_text(json.dumps(info), encoding="utf-8")
    reviewer.finalize(prepared.task_root)
    report = json.loads((prepared.workspace / REPORT_NAME).read_text())
    assert report["status"] == "failed"
    assert any(check["code"] == "hep_answer_key_leak" for check in report["checks"])


def test_researchclawbench_review_type_is_registered() -> None:
    assert isinstance(get_review_type("researchclawbench"), ResearchClawBenchReviewType)


@pytest.mark.integration
@pytest.mark.parametrize("workspace_name", [
    "doi_10.1103_PhysRevLett.113.121102--1321fa577a46--power-law-threshold",
    "doi_10.1103_PhysRevLett.121.051103--389fd6c70c59--nitrogen-ratio-trend",
    "doi_10.1103_PhysRevLett.132.261001--cbb39eca9658--d4he-powerlaw",
])
def test_evidence_backed_capability_relabeling_passes_real_review(
    tmp_path: Path, workspace_name: str,
) -> None:
    """复现三道真实误拒题，改标后的科学证据须通过审核。"""
    from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import task_capabilities

    root = Path(__file__).resolve().parents[3] / "runs"
    original = root / "20261122-105806-ams02-taskgen-full" / workspace_name / "task"
    reviewed = root / "20261124-184841-ams02-full-review" / workspace_name
    if not original.is_dir() or not reviewed.is_dir():
        pytest.skip("需要本批真实 AMS 原题和审核修复后的题包")
    removed = set(task_capabilities(original)) - set(task_capabilities(reviewed / "task"))
    assert removed == {"tool_use"}
    package = tmp_path / "task"
    shutil.copytree(reviewed / "task", package)
    science_path = tmp_path / SCIENTIFIC_REPORT_NAME
    shutil.copy2(reviewed / SCIENTIFIC_REPORT_NAME, science_path)
    reviewer = ResearchClawBenchReviewType()
    reviewer.finalize(package, source_task_root=original)
    report = json.loads((tmp_path / REPORT_NAME).read_text())
    assert report["status"] == "repaired"
    assert reviewer.validate(package).ok

    scientific = json.loads(science_path.read_text())
    scientific["checks"][-1]["assessment"] = "concern"
    science_path.write_text(json.dumps(scientific), encoding="utf-8")
    with pytest.raises(ValueError, match="通过审核的能力评分项必须有可观察的证据"):
        reviewer.finalize(package, source_task_root=original)


@pytest.mark.integration
def test_declared_solver_output_fields_are_public_contract(hep_run: Path) -> None:
    """真实任务中公开声明的提交字段名可以与参考结果使用相同名称。"""
    from researchfactory.taskgen.tasks.researchclawbench.reviewer import _hidden_key_leaks

    tasks = sorted((hep_run / "reviewed").glob("*/public/task_info.json"))
    if not tasks:
        pytest.skip("本轮尚无审核通过的公开任务")
    for task_info in tasks:
        assert _hidden_key_leaks(task_info.parents[1] / "task") == []
