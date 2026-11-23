"""HEP 批量任务预检命令验收。"""
from __future__ import annotations

import json
from pathlib import Path

from researchfactory.__main__ import main
from researchfactory.taskgen.tasks.researchclawbench.hep_batch_plan import build_batch_plan


def _row(title: str, *, evidence: list[dict[str, str]] | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "paper_id": f"doi:{title}",
        "title": title,
        "assigned_domain": "Physics",
        "experiment": "AMS-02",
        "source_url": "https://ams02.space/publications/202601",
    }
    if evidence is not None:
        row["evidence_sources"] = evidence
    return row


def test_batch_plan_groups_hep_rows_by_primary_task() -> None:
    rows, groups = build_batch_plan([
        _row("Rigidity flux ratio measurement"),
        _row("Time series solar modulation measurement"),
    ])

    assert len(rows) == 2
    assert groups == {"ratio_analysis": 1, "time_series_modulation": 1}
    assert rows[0]["taskgen_plan"]["primary_task_type"] == "ratio_analysis"


def test_hep_plan_cli_writes_jsonl_and_reports_groups(tmp_path: Path, capsys) -> None:
    pool = tmp_path / "pool.jsonl"
    output = tmp_path / "plans.jsonl"
    pool.write_text(json.dumps(_row(
        "Rigidity flux ratio measurement",
        evidence=[{
            "source_id": "method-1",
            "role": "method_reference",
            "title": "Method paper",
            "doi": "10.1103/example",
            "url": "https://example.org/method.pdf",
        }],
    )) + "\n", encoding="utf-8")

    assert main(["hep-plan", "--pool", str(pool), "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    row = json.loads(output.read_text(encoding="utf-8").strip())
    assert summary["rows"] == 1
    assert summary["groups"] == {"ratio_analysis": 1}
    assert row["evidence_source_count"] == 1


def test_hep_plan_rejects_non_hep_pool_rows(tmp_path: Path, capsys) -> None:
    pool = tmp_path / "pool.jsonl"
    output = tmp_path / "plans.jsonl"
    pool.write_text(json.dumps({"paper_id": "generic:1", "title": "A biology paper"}) + "\n", encoding="utf-8")

    assert main(["hep-plan", "--pool", str(pool), "--output", str(output)]) == 2
    assert "not recognized as a HEP target" in capsys.readouterr().err
