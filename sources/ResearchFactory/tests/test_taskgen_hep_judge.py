"""HEP LLM Judge 输入和输出合同验收。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchfactory.taskgen.tasks.researchclawbench.hep_judge import (
    HEP_JUDGEMENT_SCHEMA,
    build_judge_prompt,
    stage_judge_workspace,
    validate_judgement,
)
from researchfactory.__main__ import main


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _task(root: Path) -> Path:
    (root / "data").mkdir(parents=True)
    (root / "related_work").mkdir()
    (root / "target_study" / "images").mkdir(parents=True)
    (root / "data" / "input.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (root / "related_work" / "paper.pdf").write_bytes(b"%PDF-1.5 paper")
    (root / "target_study" / "paper.pdf").write_bytes(b"%PDF-1.4 target")
    (root / "target_study" / "images" / "figure.png").write_bytes(b"\x89PNG\r\n")
    _json(root / "task_info.json", {
        "task": "Analyze the public input.",
        "data": [{"name": "input.csv", "path": "./data/input.csv", "type": "csv"}],
    })
    _json(root / "target_study" / "checklist.json", [
        {"type": "text", "content": "analysis", "weight": 0.4, "path": None},
        {"type": "text", "content": "result", "weight": 0.3, "path": None},
        {"type": "image", "content": "figure", "weight": 0.3, "path": "images/figure.png"},
    ])
    return root


def test_judge_workspace_contains_public_inputs_only(tmp_path: Path) -> None:
    task = _task(tmp_path / "task")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "report.md").write_text("analysis", encoding="utf-8")
    evaluation = tmp_path / "evaluation.json"
    _json(evaluation, {"schema_version": "researchfactory.hep_evaluation_result.v1", "passed": True, "score": 1.0})

    staged = stage_judge_workspace(
        task_root=task,
        solver_root=solver,
        evaluation_path=evaluation,
        workspace=tmp_path / "judge",
    )

    assert staged.task_root.is_dir()
    assert staged.solver_root.joinpath("report.md").is_file()
    assert staged.evaluation_path.is_file()
    assert not (staged.task_root / "target_study").exists()
    assert staged.result_path.name == "judge_result.json"


def test_judge_prompt_requires_authoritative_deterministic_values(tmp_path: Path) -> None:
    task = _task(tmp_path / "task")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "report.md").write_text("analysis", encoding="utf-8")
    evaluation = tmp_path / "evaluation.json"
    _json(evaluation, {"passed": True, "score": 1.0})
    staged = stage_judge_workspace(
        task_root=task,
        solver_root=solver,
        evaluation_path=evaluation,
        workspace=tmp_path / "judge",
    )

    prompt = build_judge_prompt(task_id="hep-1", input_data=staged)

    assert HEP_JUDGEMENT_SCHEMA in prompt
    assert "deterministic score" in prompt
    assert "Do not read or search for hidden target-study files" in prompt
    assert "judge_result.json" in prompt


def test_judge_result_must_preserve_deterministic_values(tmp_path: Path) -> None:
    path = tmp_path / "judge_result.json"
    deterministic = {"passed": True, "score": 1.0}
    _json(path, {
        "schema_version": HEP_JUDGEMENT_SCHEMA,
        "deterministic_score": 1.0,
        "deterministic_passed": True,
        "scientific_assessment": "accepted",
        "dimensions": [
            {"name": "method", "assessment": "pass", "reason": "clear"},
            {"name": "uncertainty", "assessment": "pass", "reason": "reported"},
            {"name": "interpretation", "assessment": "pass", "reason": "bounded"},
        ],
        "reason": "The output is scientifically coherent.",
    })

    result = validate_judgement(path, deterministic)

    assert result["scientific_assessment"] == "accepted"

    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["deterministic_score"] = 0.5
    _json(path, changed)
    with pytest.raises(ValueError, match="changed the deterministic score"):
        validate_judgement(path, deterministic)


def test_judge_result_requires_all_scientific_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "judge_result.json"
    deterministic = {"passed": True, "score": 1.0}
    _json(path, {
        "schema_version": HEP_JUDGEMENT_SCHEMA,
        "deterministic_score": 1.0,
        "deterministic_passed": True,
        "scientific_assessment": "accepted",
        "dimensions": [
            {"name": "method", "assessment": "pass", "reason": "clear"},
            {"name": "uncertainty", "assessment": "pass", "reason": "reported"},
        ],
        "reason": "The output is scientifically coherent.",
    })

    with pytest.raises(ValueError, match="method, uncertainty, interpretation"):
        validate_judgement(path, deterministic)


def test_hep_judge_command_dry_run_uses_deepseek_adapter_request(tmp_path: Path, capsys) -> None:
    task = _task(tmp_path / "task")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "report.md").write_text("analysis", encoding="utf-8")
    evaluation = tmp_path / "evaluation.json"
    _json(evaluation, {"schema_version": "researchfactory.hep_evaluation_result.v1", "passed": True, "score": 1.0})
    workspace = tmp_path / "judge"

    exit_code = main([
        "hep-judge",
        "--task-root", str(task),
        "--solver-root", str(solver),
        "--evaluation", str(evaluation),
        "--workspace", str(workspace),
        "--dry-run",
    ])

    assert exit_code == 0
    request = json.loads((workspace / "deepseek_harness_request.json").read_text(encoding="utf-8"))
    assert request["task_type"] == "researchclawbench_judge"
    assert request["runtime_profile"] == "hep-rcb"
    assert "deterministic score" in request["prompt"]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
