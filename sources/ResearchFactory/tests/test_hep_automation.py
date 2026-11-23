"""检查真实生成任务、完整试运行记录和评分产物，不替换模型或运行环境。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from researchfactory.taskgen.tasks.researchclawbench.hep_assessment import public_output_contract, _cached_stage
from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import validate_figure
from researchfactory.taskgen.core.runner import _review_sources
from researchfactory.taskgen.tasks.researchclawbench.hep_batch_report import collect_batch_report

NAME = "doi_10.1103_d2vf-fw3v--6e37f20233d0"


@pytest.mark.integration
def test_public_output_contract_omits_gold_values_and_tolerances(hep_run: Path) -> None:
    task = hep_run / NAME / "task"
    if not (task / "target_study/evaluator/reference.json").is_file():
        pytest.skip("先完成真实任务生成")
    contract = public_output_contract(task)
    assert contract["required_outputs"] and contract["numeric_fields"]
    serialized = json.dumps(contract)
    assert '"reference"' not in serialized
    assert '"abs_tolerance"' not in serialized
    assert '"values"' not in serialized
    assert "_paper_cache" not in serialized


def test_figure_validation_rejects_tiny_or_blank_images(tmp_path: Path) -> None:
    for name, size in (("tiny", (1, 1)), ("blank", (512, 512))):
        path = tmp_path / f"{name}.png"
        Image.new("RGB", size, "white").save(path)
        assert validate_figure(path)[0] is False


@pytest.mark.integration
def test_real_reference_plot_is_readable(hep_run: Path) -> None:
    figures = list((hep_run / NAME / "task/target_study/images").glob("*.png"))
    if not figures:
        pytest.skip("先完成真实任务生成")
    assert validate_figure(figures[0])[0] is True


@pytest.mark.integration
def test_completed_workflow_records_real_scoring_and_judgement(hep_run: Path) -> None:
    plan = json.loads((hep_run / "workflow_plan.json").read_text())
    if plan["through"] in {"build", "review"}:
        pytest.skip("本轮试跑未要求解题和评分")
    report_path = hep_run / "batch_report.json"
    if not report_path.is_file():
        pytest.skip("先完成真实审核、Solver 和 Judge 试运行")
    state = json.loads((hep_run / "workflow_state.json").read_text())
    assert state["status"] == "completed"
    assert {"cache", "plan", "build", "review", "solver", "score", "judge", "report"} <= set(state["completed_stages"])
    report = json.loads(report_path.read_text())
    assert report["count"] == len(report["entries"]) >= 1
    for item in report["entries"]:
        if item["review_status"] == "failed":
            assert item["solver_status"] is None
            continue
        name = item["workspace"]
        evaluation = json.loads((hep_run / "scores" / name / "evaluation.json").read_text())
        judge = json.loads((hep_run / "judge" / name / "judge_result.json").read_text())
        assert item["review_status"] in {"accepted", "repaired"}
        assert item["solver_status"] == "ok"
        assert item["deterministic_score"] == judge["deterministic_score"] == evaluation["score"]
        assert item["deterministic_passed"] is judge["deterministic_passed"] is evaluation["passed"]
        assert item["scientific_assessment"] == judge["scientific_assessment"]
        assert [value["name"] for value in judge["dimensions"]] == ["method", "uncertainty", "interpretation"]


@pytest.mark.integration
def test_completed_solver_can_be_reused_without_another_model_run(hep_run: Path) -> None:
    root = hep_run / "solver" / NAME
    if not (root / "_meta.json").is_file():
        pytest.skip("先完成真实 Solver 试运行")
    record = json.loads((root / "_meta.json").read_text())
    assert _cached_stage(root, record["input_fingerprint"]) == record
    assert _cached_stage(root, "different-input") is None


@pytest.mark.integration
def test_real_reviewer_resumes_the_same_native_session(hep_run: Path) -> None:
    """使用真实的恢复记录核对会话编号。"""
    workspace = hep_run / "reviewed" / NAME
    if not (workspace / "hep_review_report.json").is_file():
        pytest.skip("先完成真实审核试运行")
    request = json.loads((workspace / "deepseek_harness_request.json").read_text())
    if not request.get("resume_session_id"):
        pytest.skip("本次审核没有恢复会话")
    original = workspace / "agent_trace/deepseek_harness_events.jsonl"
    session = json.loads(original.read_text().splitlines()[0])["sessionId"]
    assert request["resume_session_id"] == session
    result = json.loads((workspace / "deepseek_harness_result.json").read_text())
    assert result["status"] == "completed"
    assert "resume-" in result["events_path"]


@pytest.mark.integration
def test_real_judge_reads_actual_solver_plot_with_native_image_tool(hep_run: Path) -> None:
    """图像能力通过真实图表读取记录验收，避免仅检查配置声明。"""
    marker = hep_run / "judge" / NAME / "_meta.json"
    if not marker.is_file():
        pytest.skip("先完成真实 Judge 试运行")
    meta = json.loads(marker.read_text())
    assert meta["status"] == "ok"
    trace = Path(meta["workspace"]) / "agent_trace/deepseek_harness_events.jsonl"
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    results = {e["callId"]: e for e in events if e["type"] == "tool_result"}
    images = [e for e in events if e["type"] == "tool_call" and e["tool"] == "read_image"]
    assert images
    assert any(
        "solver_output/" in e["input"]["file_path"]
        and results[e["callId"]]["status"] == "completed"
        and "<type>image</type>" in results[e["callId"]]["result"]
        for e in images
    )


@pytest.mark.integration
def test_review_and_report_only_select_the_requested_papers(hep_run: Path) -> None:
    """旧结果目录可保留其他任务，单篇试运行仍严格使用本轮论文清单。"""
    if not (hep_run / NAME / "_meta.json").is_file():
        pytest.skip("先完成真实任务生成")
    selected = {"doi:10.1103/d2vf-fw3v"}
    assert len(_review_sources(hep_run, "researchclawbench", None, selected)) == 1
    assert collect_batch_report(hep_run, target_ids=selected)["count"] == 1
    assert collect_batch_report(hep_run, target_ids=set())["count"] == 0
    with pytest.raises(ValueError, match="no successful"):
        _review_sources(hep_run, "researchclawbench", None, {"doi:10.1103/PhysRevLett.134.201001"})
