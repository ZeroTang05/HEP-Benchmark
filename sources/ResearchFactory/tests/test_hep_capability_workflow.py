"""用真实归档题包和 Harness 轨迹检查八维评分合同。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from researchfactory.taskgen.tasks.researchclawbench.hep_batch_plan import build_batch_plan
from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import CONTRACT as EVALUATOR
from researchfactory.taskgen.tasks.researchclawbench.hep_judge import observable_trace
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import CAPABILITY_SCHEMA, rubric_path, task_capabilities
from researchfactory.taskgen.tasks.researchclawbench.hep_series import read_series, validate_series_task


@pytest.mark.integration
def test_real_task_accepts_multiple_capabilities_and_named_trace(hep_run: Path, tmp_path: Path) -> None:
    """复制真实题后加入过程小项，检查定题清单与最终评分证据的对应。"""
    source = next(hep_run.glob("*/tasks/*/task/target_study/package_contract.json")).parents[1]
    task = tmp_path / "task"
    shutil.copytree(source, task)
    rubric_file = rubric_path(task)
    rubric = json.loads(rubric_file.read_text())
    rubric["schema_version"] = CAPABILITY_SCHEMA
    numeric = next(
        item for group in rubric["groups"] for item in group["items"] if item["check"].startswith("numeric:")
    )
    numeric["capability"] = "data_analysis"
    numeric["weight"] /= 2
    group = next(group for group in rubric["groups"] if numeric in group["items"])
    group["items"].append({
        "id": "first_analysis_plan",
        "criterion": "Before the fit, state the input and a check of the result.",
        "weight": numeric["weight"],
        "check": "judge",
        "capability": "task_planning",
        "evidence": "trace",
    })
    rubric_file.write_text(json.dumps(rubric), encoding="utf-8")
    paper = source / "target_study/paper.pdf"
    checks = [numeric["check"].removeprefix("numeric:"), "judge:first_analysis_plan"]
    assert task_capabilities(task) == ["task_planning", "data_analysis"]
    assert validate_series_task(task, paper, decision_checks=checks, capabilities=task_capabilities(task)) == []
    assert validate_series_task(task, paper, decision_checks=checks, capabilities=["hypothesis_generation"])


@pytest.mark.integration
@pytest.mark.parametrize("context_only", [False, True])
def test_qualitative_capability_can_use_real_material_without_a_numeric_reference(
    hep_run: Path, tmp_path: Path, context_only: bool,
) -> None:
    """沿用真实来源检查专门的文字答卷可以经过同一套验收和评分。"""
    source = next(hep_run.glob("*/tasks/*/task/target_study/package_contract.json")).parents[1]
    task = tmp_path / "task"
    shutil.copytree(source, task)
    evaluator = EVALUATOR.evaluator_dir(task)
    schema_path = evaluator / "schema.json"
    reference_path = evaluator / "reference.json"
    schema = json.loads(schema_path.read_text())
    reference = json.loads(reference_path.read_text())
    metadata = json.loads(next((task / "data").glob("*.metadata.json")).read_text())
    if context_only:
        info_path = task / "task_info.json"
        info = json.loads(info_path.read_text())
        info["task"] = (
            f"The experiment reports {metadata['observable']} as a function of "
            f"{metadata['independent_variable']['name']}. Explain how to design a comparison "
            "that distinguishes a changing trend from a constant trend."
        )
        info["data"] = []
        info["hep_task"].update(
            primary_task_type="analysis_design", secondary_task_types=[],
            required_inputs=["observation context and prior literature"], required_outputs=["response.md"],
        )
        info_path.write_text(json.dumps(info), encoding="utf-8")
        shutil.rmtree(task / "data")
        (task / "data").mkdir()
        numerical_errors = validate_series_task(task, source / "target_study/paper.pdf")
        assert any("hep_data_empty" in error for error in numerical_errors)
    schema["numeric_checks"] = []
    schema["required_outputs"] = [{"path": "response.md", "format": "text", "description": "Table interpretation"}]
    reference["values"] = {}
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    rubric_path(task).write_text(json.dumps({
        "schema_version": CAPABILITY_SCHEMA,
        "groups": [{"id": "reading", "title": "Read the data documentation", "weight": 1.0, "items": [{
            "id": "units_from_source", "criterion": "Explain the physical meaning of the documented measurement and units.",
            "weight": 1.0, "check": "judge", "capability": "knowledge_understanding", "output": "response.md",
        }]}],
    }), encoding="utf-8")
    assert validate_series_task(
        task, source / "target_study/paper.pdf",
        decision_checks=["judge:units_from_source"], capabilities=["knowledge_understanding"],
    ) == []
    submitted = tmp_path / "submitted"
    submitted.mkdir()
    (submitted / "response.md").write_text(
        f"The table records {metadata['observable']} in {metadata['value']['unit']}.\n", encoding="utf-8",
    )
    result = EVALUATOR.evaluate(task, submitted).to_dict()
    assert result["passed"] and result["score"] == 1.0
    assert result["rubric"][0]["items"][0]["check"] == "judge"


def test_series_preserves_the_context_for_an_open_decision(tmp_path: Path) -> None:
    """用锂同位素论文的实际问题验证上游决策和交接信息。"""
    plan = {
        "tasks": [{
            "id": "lithium-origin", "stage": "hypothesis",
            "question": "What competing origins for cosmic lithium-7 are testable?",
            "public_context": "Cosmic-ray collisions can produce lithium-6 and lithium-7.",
            "evidence": "AMS Phys. Rev. Lett. 134, 201001, Introduction and Supplemental Material S4–S5.",
            "method": "Leave the competing predictions and their discriminating observations to the Solver.",
            "decision_output": "A testable explanation and an observation that would weaken it.",
            "decision_checks": ["judge:prediction"],
            "capabilities": ["hypothesis_generation"],
        }],
        "single_task_reason": "This test covers an individual decision in the AMS investigation.",
    }
    manifest = tmp_path / "task_series.json"
    manifest.write_text(json.dumps(plan), encoding="utf-8")
    rows = read_series(tmp_path, require_decision_checks=True, require_packages=False)
    assert rows[0]["public_context"] == plan["tasks"][0]["public_context"]
    plan["tasks"][0]["stage"] = "hypothesis_generation"
    manifest.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="stage .*hypothesis_generation.*framing, hypothesis, design"):
        read_series(tmp_path, require_decision_checks=True, require_packages=False)
    plan["tasks"][0]["stage"] = "hypothesis"
    output = plan["tasks"][0]["decision_output"]
    plan["tasks"][0]["decision_output"] = "judge:prediction"
    manifest.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="decision_output"):
        read_series(tmp_path, require_decision_checks=True, require_packages=False)
    plan["tasks"][0]["decision_output"] = output
    del plan["tasks"][0]["public_context"]
    manifest.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="public_context"):
        read_series(tmp_path, require_decision_checks=True, require_packages=False)


@pytest.mark.integration
def test_real_trace_only_exposes_observable_events(hep_run: Path) -> None:
    """使用已有的真实运行轨迹，验证顺序、调用关联和可见事件过滤。"""
    path = next(hep_run.glob("*/agent_trace/deepseek_harness_events.jsonl"))
    trace = observable_trace(path)
    events = {entry["type"] for entry in trace}
    assert {"text", "tool_call", "tool_result"} <= events
    assert events <= {"text", "tool_call", "tool_result", "final"}
    assert [entry["event"] for entry in trace] == sorted({entry["event"] for entry in trace})
    calls = {entry["call_id"] for entry in trace if entry["type"] == "tool_call"}
    assert calls & {entry["call_id"] for entry in trace if entry["type"] == "tool_result"}


def test_existing_batch_coverage_prioritizes_only_gaps() -> None:
    """用真实论文池验证每篇收到的抽取重点。"""
    root = Path(__file__).parents[3]
    with (root / "inputs/ams02-publications/target_study_pool.jsonl").open() as pool:
        paper = json.loads(next(pool))
    report = {
        "schema_version": "researchfactory.hep_batch_report.v1",
        "uncovered_capabilities": ["hypothesis_generation"],
    }
    planned, _ = build_batch_plan([paper], coverage_report=report)
    assert planned[0]["capability_focus"] == "hypothesis_generation"
