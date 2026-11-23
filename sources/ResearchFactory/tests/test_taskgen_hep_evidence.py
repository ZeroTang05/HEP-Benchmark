"""HEP 多论文研究证据包入口验收。"""
from __future__ import annotations

import pytest

from researchfactory.taskgen.tasks.researchclawbench.hep_evidence import normalize_evidence_sources
from researchfactory.taskgen.tasks.researchclawbench.initializer import build_entry_prompt


def _target() -> dict[str, object]:
    return {
        "title": "A cosmic-ray flux ratio study",
        "domain": "high_energy_physics",
        "experiment": "AMS-02",
        "evidence_sources": [
            {
                "source_id": "method-ams-2021",
                "role": "method_reference",
                "title": "Earlier primary and secondary cosmic-ray analysis",
                "doi": "10.1103/example.method",
                "url": "https://example.org/method.pdf",
            },
            {
                "source_id": "comparison-ams-2022",
                "role": "comparison_study",
                "title": "Independent cosmic-ray comparison",
                "arxiv_id": "arXiv:2201.00001",
                "url": "https://example.org/comparison.pdf",
            },
        ],
    }


def test_multi_paper_evidence_scope_is_rendered_separately() -> None:
    prompt = build_entry_prompt(_target())

    assert "method-ams-2021" in prompt
    assert "comparison-ams-2022" in prompt
    assert "Keep measurements, units, bins, uncertainties, and provenance separate by source." in prompt
    assert "do not silently replace the Target Study" in prompt


def test_evidence_sources_require_unique_roles_and_identifiers() -> None:
    sources = normalize_evidence_sources(_target())
    assert [source.role for source in sources] == ["method_reference", "comparison_study"]

    invalid = _target()
    invalid["evidence_sources"] = [dict(sources[0].__dict__, role="unknown")]
    with pytest.raises(ValueError, match="unsupported role"):
        normalize_evidence_sources(invalid)


def test_target_without_evidence_sources_keeps_single_paper_scope() -> None:
    prompt = build_entry_prompt({
        "title": "A rigidity spectrum measurement",
        "domain": "high_energy_physics",
        "experiment": "AMS-02",
    })

    assert "Use the preselected Target Study as the primary paper." in prompt
    assert "additional sources are allowed" not in prompt
