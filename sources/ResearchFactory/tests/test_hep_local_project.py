"""验证本机 HEP 的实际入口、注册表和完整试运行记录。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchfactory.__main__ import _build_parser
from researchfactory.taskgen.core.registry import available_agents, available_task_types, available_review_types
from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import CONTRACT

def test_local_hep_runtime_and_contract_are_registered() -> None:
    """入口实际加载唯一运行框架和 HEP 数据合同。"""
    assert available_agents() == ["deepseek_harness"]
    assert available_task_types() == ["researchclawbench"]
    assert available_review_types() == ["researchclawbench"]
    parser = _build_parser()
    args = parser.parse_args(["taskgen", "--phase", "review"])
    assert args.phase == "review"


@pytest.mark.integration
def test_real_local_pilot_completed_every_stage(hep_run: Path) -> None:
    """完整流水线的证据来自真实本机试验。"""
    state = json.loads((hep_run / "workflow_state.json").read_text())
    plan = json.loads((hep_run / "workflow_plan.json").read_text())
    if plan["through"] in {"build", "review"}:
        pytest.skip("本轮试跑未要求解题和评分")
    assert state["status"] == "completed"
    assert set(state["completed_stages"]) == {"cache", "plan", "build", "review", "solver", "score", "judge", "report"}
    report = json.loads((hep_run / "batch_report.json").read_text())
    assert report["paper_count"] == 1
    assert report["count"] == len(report["entries"]) >= 1


@pytest.mark.integration
def test_real_local_solver_result_can_be_scored_again(hep_run: Path) -> None:
    """精简后用真实隐藏合同和真实解题产物重新评分，数值结果应一致。"""
    evaluations = sorted((hep_run / "scores").glob("*/evaluation.json"))
    if not evaluations:
        pytest.skip("本轮试跑没有数值评分产物")
    for evaluation in evaluations:
        name = evaluation.parent.name
        task = hep_run / "reviewed" / name / "task"
        solver_meta = json.loads((hep_run / "solver" / name / "_meta.json").read_text())
        recorded = json.loads(evaluation.read_text())
        actual = CONTRACT.evaluate(task, Path(solver_meta["artifacts"])).to_dict()
        assert actual["score"] == recorded["score"]
        assert actual["passed"] == recorded["passed"]
