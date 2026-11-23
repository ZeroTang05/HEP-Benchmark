"""HEP 数据合同的确定性验收。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT
from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import CONTRACT as HEP_EVALUATOR
from researchfactory.__main__ import main


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _base_package(root: Path, *, rows: str) -> tuple[Path, Path]:
    data = root / "data"
    related = root / "related_work"
    images = root / "target_study" / "images"
    data.mkdir(parents=True)
    related.mkdir(parents=True)
    images.mkdir(parents=True)
    observable = data / "observable.csv"
    observable.write_text(rows, encoding="utf-8")
    (root / "target_study" / "paper.pdf").write_bytes(b"%PDF-1.4 target")
    (related / "paper_000.pdf").write_bytes(b"%PDF-1.5 related")
    (images / "figure.png").write_bytes(b"\x89PNG\r\n")
    _write_json(
        root / "target_study" / "checklist.json",
        [
            {"type": "text", "content": "fit parameters", "weight": 0.4, "path": None},
            {"type": "text", "content": "uncertainties", "weight": 0.3, "path": None},
            {"type": "image", "content": "fit figure", "weight": 0.3, "path": "images/figure.png"},
        ],
    )
    return data, observable


def _valid_metadata() -> dict[str, object]:
    return {
        "schema_version": "researchfactory.hep_data_metadata.v1",
        "observable": "proton_flux",
        "independent_variable": {
            "name": "rigidity",
            "unit": "GV",
            "binning": "bin_edges",
            "columns": {"low": "bin_low", "high": "bin_high"},
        },
        "value": {
            "name": "flux",
            "unit": "m^-2 s^-1 sr^-1 GV^-1",
            "column": "flux",
            "quantity_type": "differential",
        },
        "uncertainties": [
            {"name": "statistical", "column": "stat_error", "unit": "same_as_value"},
            {"name": "systematic_total", "column": "syst_error", "unit": "same_as_value"},
        ],
        "correlations": {"available": False, "assumption": "diagonal_only"},
        "missing_value_policy": "blank_is_missing",
        "positive_value_required_for_log_fit": True,
    }


def _attach_hep_contract(data: Path, observable: Path) -> None:
    _write_json(data / "observable.metadata.json", _valid_metadata())
    _write_json(
        data / "source_provenance.json",
        {
            "schema_version": "researchfactory.hep_source_provenance.v1",
            "files": [
                {
                    "path": "observable.csv",
                    "sha256": hashlib.sha256(observable.read_bytes()).hexdigest(),
                    "source_url": "https://example.invalid/ams/observable.csv",
                    "source_type": "measurement_input",
                }
            ],
        },
    )
    _write_json(
        data.parent / "task_info.json",
        {
            "task": "Fit the supplied proton flux spectrum and report uncertainties.",
            "hep_data_contract": "v1",
            "hep_task": {
                "primary_task_type": "spectrum_fit",
                "secondary_task_types": ["systematics_propagation"],
                "observable": "proton_flux",
                "independent_variable": "rigidity_GV",
                "required_inputs": ["bin_low", "bin_high", "flux", "stat_error", "syst_error"],
                "required_outputs": ["fit_parameters", "uncertainty_interval", "report"],
                "difficulty": "applied",
            },
            "data": [
                {
                    "name": "observable.csv",
                    "path": "./data/observable.csv",
                    "type": "measurement_data",
                    "metadata": "./data/observable.metadata.json",
                    "provenance": "./data/source_provenance.json",
                }
            ],
        },
    )
    evaluator = data.parent / "target_study" / "evaluator"
    evaluator.mkdir()
    _write_json(
        evaluator / "schema.json",
        {
            "schema_version": "researchfactory.hep_evaluator.v1",
            "required_outputs": [
                {
                    "path": "fit_parameters.json",
                    "format": "json",
                    "description": "Fitted spectrum parameters.",
                }
            ],
            "numeric_checks": [
                {
                    "name": "slope",
                    "output": "fit_parameters.json",
                    "json_pointer": "/slope",
                    "reference": "slope",
                    "abs_tolerance": 0.1,
                }
            ],
        },
    )
    _write_json(
        evaluator / "reference.json",
        {
            "schema_version": "researchfactory.hep_evaluator_reference.v1",
            "values": {"slope": -2.7},
        },
    )


def test_valid_hep_measurement_table_passes(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(
        root,
        rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n2,4,8,0.3,0.4\n",
    )
    _attach_hep_contract(data, observable)

    result = CONTRACT.validate(root)

    assert result.ok, [issue.message for issue in result.errors]


def test_hep_sidecars_are_referenced_as_attached_data(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)

    codes = {issue.code for issue in CONTRACT.validate(root).errors}

    assert "data_unreferenced" not in codes


def test_hep_metadata_requires_units_differential_quantity_and_two_errors(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error\n1,2,10,0.2\n")
    _attach_hep_contract(data, observable)
    metadata = _valid_metadata()
    metadata["independent_variable"]["unit"] = ""
    metadata["value"]["quantity_type"] = "integrated"
    metadata["uncertainties"] = [{"name": "statistical", "column": "stat_error", "unit": "same_as_value"}]
    _write_json(data / "observable.metadata.json", metadata)

    codes = {issue.code for issue in CONTRACT.validate(root).errors}

    assert {"hep_independent_variable", "hep_quantity_type", "hep_systematic_error_missing"} <= codes


def test_hep_table_rejects_overlapping_bins_and_nonpositive_log_values(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(
        root,
        rows="bin_low,bin_high,flux,stat_error,syst_error\n1,3,-1,0.2,0.5\n2,4,8,0.3,0.4\n",
    )
    _attach_hep_contract(data, observable)

    codes = {issue.code for issue in CONTRACT.validate(root).errors}

    assert {"hep_bin_overlap", "hep_log_value"} <= codes


def test_hep_provenance_hash_must_match_measurement_input(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    provenance = json.loads((data / "source_provenance.json").read_text(encoding="utf-8"))
    provenance["files"][0]["sha256"] = "0" * 64
    _write_json(data / "source_provenance.json", provenance)

    codes = {issue.code for issue in CONTRACT.validate(root).errors}

    assert "hep_provenance_hash_mismatch" in codes


def test_hep_evaluator_schema_is_required_and_hidden(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    (root / "target_study" / "evaluator" / "schema.json").unlink()

    result = CONTRACT.validate(root)
    codes = {issue.code for issue in result.errors}
    public = tmp_path / "public"
    CONTRACT.export_public(root, public)

    assert "hep_evaluator_schema_missing" in codes
    assert not (public / "target_study").exists()


def test_hep_task_plan_rejects_unsupported_primary_type(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    task_info_path = root / "task_info.json"
    task_info = json.loads(task_info_path.read_text(encoding="utf-8"))
    task_info["hep_task"]["primary_task_type"] = "invented_analysis"
    _write_json(task_info_path, task_info)

    codes = {issue.code for issue in CONTRACT.validate(root).errors}

    assert "hep_task_type" in codes


def test_hep_evaluator_rejects_unsafe_output_path(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    schema_path = root / "target_study" / "evaluator" / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["required_outputs"][0]["path"] = "../target_study/paper.pdf"
    _write_json(schema_path, schema)

    codes = {issue.code for issue in CONTRACT.validate(root).errors}

    assert "hep_evaluator_output_path" in codes


def test_hep_evaluator_scores_correct_solver_output(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    solver = tmp_path / "solver"
    solver.mkdir()
    _write_json(solver / "fit_parameters.json", {"slope": -2.65})

    result = HEP_EVALUATOR.evaluate(root, solver)

    assert result.passed
    assert result.score == 1.0
    assert all(check.passed for check in result.checks)


def test_hep_evaluator_rejects_missing_solver_output(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    solver = tmp_path / "solver"
    solver.mkdir()

    result = HEP_EVALUATOR.evaluate(root, solver)

    assert not result.passed
    assert result.score == 0.0
    assert any(not check.passed for check in result.checks)


def test_hep_evaluator_rejects_numeric_scientific_error(tmp_path: Path) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    solver = tmp_path / "solver"
    solver.mkdir()
    _write_json(solver / "fit_parameters.json", {"slope": -1.2})

    result = HEP_EVALUATOR.evaluate(root, solver)

    assert not result.passed
    assert result.score < 1.0
    assert any(check.name == "numeric:slope" and not check.passed for check in result.checks)


def test_hep_score_command_writes_machine_readable_report(tmp_path: Path, capsys) -> None:
    root = tmp_path / "task"
    data, observable = _base_package(root, rows="bin_low,bin_high,flux,stat_error,syst_error\n1,2,10,0.2,0.5\n")
    _attach_hep_contract(data, observable)
    solver = tmp_path / "solver"
    solver.mkdir()
    _write_json(solver / "fit_parameters.json", {"slope": -2.65})
    report = tmp_path / "evaluation.json"

    exit_code = main([
        "hep-score",
        "--task-root", str(root),
        "--solver-root", str(solver),
        "--output", str(report),
    ])

    assert exit_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["score"] == 1.0
    assert json.loads(capsys.readouterr().out)["passed"] is True
