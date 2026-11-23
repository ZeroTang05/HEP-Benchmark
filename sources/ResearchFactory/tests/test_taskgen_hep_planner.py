"""HEP 任务计划推断和入口提示验收。"""
from __future__ import annotations

import pytest

from researchfactory.taskgen.tasks.researchclawbench.hep_plan import infer_hep_task_plan
from researchfactory.taskgen.tasks.researchclawbench.initializer import build_entry_prompt


def test_ams_ratio_target_gets_authoritative_ratio_plan() -> None:
    plan = infer_hep_task_plan(
        {
            "title": "Properties of Heavy Cosmic Nuclei and their Flux Ratios",
            "experiment": "AMS-02",
            "assigned_domain": "Physics",
            "source_url": "https://ams02.space/publications/202601",
        }
    )

    assert plan.primary_task_type == "ratio_analysis"
    assert plan.secondary_task_types == ("spectrum_fit", "systematics_propagation")
    assert plan.difficulty == "integrated"


def test_explicit_hep_plan_is_validated_and_preserved() -> None:
    plan = infer_hep_task_plan(
        {
            "title": "A detector response study",
            "hep_task_plan": {
                "primary_task_type": "detector_calibration",
                "secondary_task_types": ["systematics_propagation"],
                "observable": "gain",
                "independent_variable": "charge",
                "required_inputs": ["reference peaks"],
                "required_outputs": ["calibration constants", "report"],
                "difficulty": "applied",
            },
        }
    )

    assert plan.primary_task_type == "detector_calibration"
    assert plan.source == "explicit"


def test_invalid_explicit_plan_fails_at_prompt_construction() -> None:
    with pytest.raises(ValueError, match="unsupported HEP primary_task_type"):
        build_entry_prompt(
            {
                "title": "A HEP study",
                "domain": "high_energy_physics",
                "hep_task_plan": {
                    "primary_task_type": "invented",
                    "secondary_task_types": [],
                    "observable": "x",
                    "independent_variable": "y",
                    "required_inputs": ["x"],
                    "required_outputs": ["y"],
                    "difficulty": "basic",
                },
            }
        )


def test_hep_series_prompt_leaves_question_selection_to_the_builder() -> None:
    prompt = build_entry_prompt(
        {
            "title": "Rigidity flux ratio measurement",
            "domain": "high_energy_physics",
            "experiment": "AMS-02",
        }
    )

    assert "Supported primary task types:" in prompt
    assert "Initial topic hint:" not in prompt
    assert "task_series.json" in prompt
    assert "authoritative for this task" not in prompt
