"""HEP 任务提示和技能合同的验收。"""
from __future__ import annotations

from pathlib import Path

from researchfactory.taskgen.tasks.researchclawbench.initializer import (
    build_entry_prompt,
    is_hep_target,
)


def test_hep_target_prompt_requires_data_contract_sidecars() -> None:
    target = {
        "paper_id": "doi:hep-1",
        "title": "A rigidity spectrum measurement",
        "domain": "high_energy_physics",
        "experiment": "AMS-02",
    }

    prompt = build_entry_prompt(target)

    assert is_hep_target(target)
    assert 'hep_data_contract: "v1"' in prompt
    assert "task_series.json" in prompt and "build_task_series" in prompt
    assert "observable.metadata.json" in prompt
    assert "source_provenance.json" in prompt
    assert "statistical uncertainty" in prompt
    assert "Do not run `pip`" in prompt


def test_generic_target_prompt_does_not_enable_hep_contract() -> None:
    target = {
        "paper_id": "doi:generic-1",
        "title": "A generic scientific measurement",
        "assigned_domain": "Biology",
    }

    prompt = build_entry_prompt(target)

    assert not is_hep_target(target)
    assert "hep_data_contract" not in prompt


def test_ams_publication_target_enables_hep_mode_from_source_identity() -> None:
    target = {
        "paper_id": "doi:10.1103/d2vf-fw3v",
        "title": "Properties of Heavy Cosmic Nuclei from the Alpha Magnetic Spectrometer",
        "assigned_domain": "Physics",
        "source_url": "https://ams02.space/publications/202601",
    }

    assert is_hep_target(target)
    assert 'hep_data_contract: "v1"' in build_entry_prompt(target)


def test_staged_skill_contains_hep_contract_protocol() -> None:
    skill = Path(__file__).parents[1] / "researchfactory" / "taskgen" / "tasks" / "researchclawbench" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    assert "## HEP Data Contract" in text
    assert "researchfactory.hep_data_metadata.v1" in text
    assert "researchfactory.hep_source_provenance.v1" in text
    assert '"hep_data_contract": "v1"' in text
