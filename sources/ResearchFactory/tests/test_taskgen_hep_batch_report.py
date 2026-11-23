"""HEP 批量结果汇总命令验收。"""
from __future__ import annotations

import json
from pathlib import Path

from researchfactory.__main__ import main
from researchfactory.taskgen.tasks.researchclawbench.hep_batch_report import collect_batch_report


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_collect_batch_report_combines_runtime_review_score_and_judge(tmp_path: Path) -> None:
    root = tmp_path / "batch"
    workspace = root / "doi_one"
    _write(workspace / "_meta.json", {
        "target_id": "doi:one",
        "status": "ok",
        "validation_ok": True,
        "duration_s": 12.5,
        "attempts": 1,
        "agent": "deepseek_harness",
        "model": "MiniMax-M3.1-Flash",
    })
    _write(workspace / "deepseek_harness_result.json", {"status": "completed", "event_count": 17})
    review = tmp_path / "review"
    _write(review / "doi_one" / "hep_review_report.json", {"status": "accepted"})
    score = tmp_path / "score"
    _write(score / "evaluation.json", {"score": 1.0, "passed": True})
    judge = tmp_path / "judge"
    _write(judge / "judge_result.json", {"scientific_assessment": "accepted"})

    report = collect_batch_report(root, review_root=review, score_root=score, judge_root=judge)

    assert report["count"] == 1
    assert report["status_counts"] == {"ok": 1}
    assert report["entries"][0]["event_count"] == 17
    assert report["entries"][0]["review_status"] == "accepted"
    assert report["entries"][0]["deterministic_score"] == 1.0
    assert report["entries"][0]["scientific_assessment"] == "accepted"


def test_hep_batch_report_cli_writes_summary(tmp_path: Path, capsys) -> None:
    root = tmp_path / "batch"
    _write(root / "doi_one" / "_meta.json", {"target_id": "doi:one", "status": "failed"})
    output = tmp_path / "summary.json"

    assert main([
        "hep-batch-report",
        "--input-root", str(root),
        "--output", str(output),
    ]) == 0
    summary = json.loads(capsys.readouterr().out)
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert summary["count"] == 1
    assert stored["status_counts"] == {"failed": 1}
