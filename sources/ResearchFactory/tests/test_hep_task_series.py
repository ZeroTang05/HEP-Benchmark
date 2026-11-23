"""使用真实试跑结果验收任务组、公开材料与下游路由。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from researchfactory.taskgen.core.runner import _review_sources
from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT, SOLVER_VISIBLE
from researchfactory.taskgen.tasks.researchclawbench.hep_assessment import public_output_contract
from researchfactory.taskgen.tasks.researchclawbench.hep_batch_report import collect_batch_report
from researchfactory.taskgen.tasks.researchclawbench.hep_contract import CONTRACT as HEP_DATA
from researchfactory.taskgen.tasks.researchclawbench.hep_contract import HepValidationResult, _read_table
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import rubric_enabled, rubric_path
from researchfactory.taskgen.tasks.researchclawbench.hep_series import (
    DELEGATION, MANIFEST, SUBMISSION, read_series, require_delegated_submission, validate_series_task,
)


@pytest.mark.integration
@pytest.mark.parametrize(("suffix", "particle", "windows", "value_index", "total_error_offset", "log_positive"), [
    ("table-s1-s42.csv", "6Li", ["begin date YYYY-MM-DD", "end date YYYY-MM-DD"], 4, 3, True),
    ("pbar-table-s1-s139.csv", "antiproton", ["bartels_rotation_number"], 3, 3, False),
])
def test_real_table_supports_bins_repeated_across_windows(
    hep_run: Path, tmp_path: Path, suffix: str, particle: str, windows: list[str],
    value_index: int, total_error_offset: int, log_positive: bool,
) -> None:
    sources = sorted(hep_run.glob(f"*/_paper_cache/sources/*-{suffix}"))
    if not sources:
        pytest.skip(f"本轮没有 AMS {particle} 的官方时间序列表")
    source = sources[0]
    evidence = next(row for row in json.loads((source.parents[1] / "index.json").read_text())["sources"]
                    if row["path"] == source.relative_to(source.parents[1]).as_posix())
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy2(source, data / source.name)
    axis = {"name": "rigidity", "unit": "GV", "binning": "bin_edges",
            "columns": {"low": "rigidity_min GV", "high": "rigidity_max GV"}}
    unit = "m^-2 sr^-1 s^-1 GV^-1"
    metadata = {
        "schema_version": "researchfactory.hep_data_metadata.v1",
        "observable": f"{particle} flux",
        "independent_variable": axis,
        "value": {"name": "flux", "unit": unit,
                  "column": evidence["columns"][value_index], "quantity_type": "differential"},
        "uncertainties": [{"name": name, "column": evidence["columns"][value_index + offset].strip(), "unit": unit}
                          for name, offset in (("statistical", 1), ("systematic_total", total_error_offset),
                                               ("systematic_time_dependent", 2))],
        "correlations": {"available": False, "assumption": "No covariance supplied in this table."},
        "missing_value_policy": "blank_is_missing",
        "positive_value_required_for_log_fit": log_positive,
    }
    sidecar = data / f"{source.stem}.metadata.json"
    (data / "source_provenance.json").write_text(json.dumps({
        "schema_version": "researchfactory.hep_source_provenance.v1",
        "files": [{"path": source.name, "sha256": evidence["sha256"], "source_url": evidence["url"],
                   "source_type": "supplementary_measurement"}],
    }), encoding="utf-8")
    info = {
        "hep_data_contract": "v1",
        "hep_task": {"primary_task_type": "time_series_modulation", "observable": f"{particle} flux",
                     "independent_variable": "rigidity and time", "required_inputs": [source.name],
                     "required_outputs": ["modulation"], "difficulty": "applied"},
        "data": [{"path": f"./data/{source.name}"}],
    }

    def errors() -> set[str]:
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")
        return {code for code, _ in HEP_DATA.validate(tmp_path, info).errors}

    assert errors() == {"hep_bin_overlap"}
    axis["group_by"] = windows
    assert errors() == set()
    axis["group_by"] = ["rigidity_min GV"]
    assert errors() == {"hep_bin_group"}
    if particle == "antiproton":
        # 时间相关误差列原始表头带空格，校验实际读到了该列的数值。
        result = HepValidationResult()
        headers, rows = _read_table(source, result)
        column = evidence["columns"][value_index + 2].strip()
        assert not result.errors and column in headers
        assert len(rows) == 1529 and rows[0][windows[0]] == "2426"
        assert float(rows[0][column]) == pytest.approx(0.0006)
        assert all(row[column].strip() for row in rows)


@pytest.mark.integration
def test_real_series_has_independent_valid_packages(hep_run: Path) -> None:
    manifests = sorted(hep_run.glob(f"*/{MANIFEST}"))
    if not manifests:
        pytest.skip("先对真实论文运行多题生成")
    for manifest in manifests:
        workspace = manifest.parent
        series = read_series(workspace)
        assert len(series) >= 2
        for row in series:
            task = workspace / "tasks" / row["id"] / "task"
            assert validate_series_task(
                task, CONTRACT.paper_path(workspace / "task"), decision_checks=row.get("decision_checks")
            ) == []
            scoring = public_output_contract(task)
            assert scoring["required_outputs"] and scoring["numeric_fields"]
            assert '"abs_tolerance"' not in json.dumps(scoring)


@pytest.mark.integration
def test_submission_requires_all_real_packages_to_pass(hep_run: Path, tmp_path: Path) -> None:
    manifests = sorted(hep_run.glob(f"*/{MANIFEST}"))
    if not manifests:
        pytest.skip("先对真实论文运行多题生成")
    original = manifests[0].parent
    series = read_series(original)
    shutil.copytree(original / "task", tmp_path / "task")
    manifest = json.loads((original / MANIFEST).read_text(encoding="utf-8"))
    for row in manifest["tasks"]:
        child = tmp_path / "tasks" / row["id"]
        shutil.copytree(original / "tasks" / row["id"] / "task", child / "task")
        schema = json.loads((child / "task/target_study/evaluator/schema.json").read_text(encoding="utf-8"))
        # 旧试跑的数据也能用来验证新提交的结构检查。
        row.setdefault("method", "Recompute the packaged reference")
        row.setdefault("decision_output", "Numerical result from the packaged reference")
        row.setdefault("decision_checks", [schema["numeric_checks"][0]["name"]])
    (tmp_path / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    draft = tmp_path / "tasks/unselected-draft"
    shutil.copytree(tmp_path / "task", draft / "task")

    # 暂时移走一道真实题的评分文件，检验整组提交的边界。
    task = tmp_path / "tasks" / series[-1]["id"] / "task"
    schema = task / "target_study/evaluator/schema.json"
    saved = schema.rename(tmp_path / "schema.json")
    command = [sys.executable, "-m", "researchfactory.taskgen.tasks.researchclawbench.validate_package"]
    rejected = subprocess.run([*command, "--submit-series"], cwd=tmp_path, capture_output=True, text=True)
    assert rejected.returncode == 1
    assert "hep_evaluator_schema_missing" in rejected.stdout
    assert not (tmp_path / SUBMISSION).exists()
    saved.rename(schema)

    if rubric_enabled(task):
        # 同一份真实题包缺少文献，或小项权重与大项不符，都应阻止提交。
        references = task / "related_work/references.json"
        saved_references = references.rename(tmp_path / "references.json")
        rejected = subprocess.run([*command, "--submit-series"], cwd=tmp_path, capture_output=True, text=True)
        assert rejected.returncode == 1 and "hep_related_work" in rejected.stdout
        assert not (tmp_path / SUBMISSION).exists()
        saved_references.rename(references)

        checklist = rubric_path(task)
        original_checklist = checklist.read_text(encoding="utf-8")
        rubric = json.loads(original_checklist)
        rubric["groups"][0]["items"][0]["weight"] /= 2
        checklist.write_text(json.dumps(rubric), encoding="utf-8")
        rejected = subprocess.run([*command, "--submit-series"], cwd=tmp_path, capture_output=True, text=True)
        assert rejected.returncode == 1 and "child weights must sum" in rejected.stdout
        assert not (tmp_path / SUBMISSION).exists()
        checklist.write_text(original_checklist, encoding="utf-8")

    row = manifest["tasks"][-1]
    selection = row["decision_checks"]
    row["decision_checks"] = ["check-with-no-evaluator-entry"]
    (tmp_path / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    rejected_child = subprocess.run([*command, "--task-root", "task"], cwd=task.parent, capture_output=True, text=True)
    assert rejected_child.returncode == 1
    assert "check-with-no-evaluator-entry" in rejected_child.stdout
    rejected = subprocess.run([*command, "--submit-series"], cwd=tmp_path, capture_output=True, text=True)
    assert rejected.returncode == 1 and "check-with-no-evaluator-entry" in rejected.stdout
    assert not (tmp_path / SUBMISSION).exists()

    row["decision_checks"] = selection
    (tmp_path / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    completed = subprocess.run([*command, "--submit-series"], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads((tmp_path / SUBMISSION).read_text())["tasks"] == [row["id"] for row in series]
    assert draft.is_dir()


@pytest.mark.integration
def test_real_series_exports_and_routes_each_task(hep_run: Path) -> None:
    for manifest in sorted(hep_run.glob(f"*/{MANIFEST}")):
        workspace = manifest.parent
        marker = workspace / "_meta.json"
        if not marker.is_file():
            continue
        meta = json.loads(marker.read_text(encoding="utf-8"))
        if meta["status"] != "ok":
            continue
        selected = {meta["target_id"]}
        series = read_series(workspace)
        assert meta["tasks"] == series
        sources = _review_sources(hep_run, "researchclawbench", None, selected)
        assert [source.meta["task_variant"] for source in sources] == series
        for source in sources:
            public = source.workspace / "public"
            assert {path.name for path in public.iterdir()} == set(SOLVER_VISIBLE)
            assert (public / "task_info.json").read_bytes() == (source.workspace / "task/task_info.json").read_bytes()
            if rubric_enabled(source.workspace / "task"):
                catalog = json.loads((public / "related_work/references.json").read_text(encoding="utf-8"))
                assert catalog["search_queries"] and catalog["papers"]
                assert {p.name for p in (public / "related_work").glob("*.pdf")} == {
                    paper["file"] for paper in catalog["papers"]
                }
        report = collect_batch_report(hep_run, target_ids=selected)
        assert report["paper_count"] == 1
        assert report["count"] == len(series)
        assert [row["workspace"] for row in report["entries"]] == [source.meta["task_workspace_name"] for source in sources]
        return
    pytest.skip("等待真实任务组通过生成校验并导出")


@pytest.mark.integration
def test_real_dsh_series_requires_a_completed_child_for_each_task(hep_run: Path, tmp_path: Path) -> None:
    """用本轮 DSH 记录确认清单、子会话和提交一一对应。"""
    receipts = sorted(hep_run.glob(f"*/{DELEGATION}"))
    if not receipts:
        pytest.skip("用完成 DSH 多题委派的真实试跑验收")
    for receipt in receipts:
        rows = read_series(receipt.parent, require_decision_checks=True)
        require_delegated_submission(receipt.parent, rows)
        shutil.copy2(receipt.parent / SUBMISSION, tmp_path / SUBMISSION)
        with pytest.raises(ValueError, match="缺少 DSH 子会话"):
            require_delegated_submission(tmp_path, rows)
        recorded = json.loads(receipt.read_text(encoding="utf-8"))
        recorded["tasks"].pop()
        (tmp_path / DELEGATION).write_text(json.dumps(recorded), encoding="utf-8")
        with pytest.raises(ValueError, match="不一致"):
            require_delegated_submission(tmp_path, rows)
        (tmp_path / DELEGATION).unlink()
