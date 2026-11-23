"""Tests for the internalized RCB task-package contract (Task 11)."""
import json
from pathlib import Path

from researchfactory.taskgen.core.registry import get_task_type
from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT as C


def _valid_pkg(root: Path) -> None:
    C.data_dir(root).mkdir(parents=True, exist_ok=True)
    C.related_work_dir(root).mkdir(parents=True, exist_ok=True)
    C.images_dir(root).mkdir(parents=True, exist_ok=True)
    (C.data_dir(root) / "obs.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    task_text = "Describe a source-backed scientific analysis from the public inputs."
    C.task_info_path(root).write_text(json.dumps({
        "task": task_text,
        "data": [{"name": "obs.csv", "path": "./data/obs.csv", "type": "feature_data",
                  "description": "d"}],
    }), encoding="utf-8")
    C.paper_path(root).write_bytes(b"%PDF-1.4 target")
    (C.images_dir(root) / "fig.png").write_bytes(b"\x89PNG\r\n")
    C.checklist_path(root).write_text(json.dumps([
        {"type": "text", "content": "scientific outcome", "keywords": [], "weight": 0.4, "path": None},
        {"type": "text", "content": "another outcome", "keywords": [], "weight": 0.3, "path": None},
        {"type": "image", "content": "the figure depicts X", "keywords": [], "weight": 0.3,
         "path": "images/fig.png"},
    ]), encoding="utf-8")
    (C.related_work_dir(root) / "paper_000.pdf").write_bytes(b"%PDF-1.5 rw")


def test_valid_package_passes(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    res = C.validate(root)
    assert res.ok, [i.message for i in res.errors]


def test_nested_data_paths_are_checked_by_relative_path(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    nested = C.data_dir(root) / "Data"
    nested.mkdir()
    (nested / "input.root").write_bytes(b"root input")
    info = json.loads(C.task_info_path(root).read_text())
    info["data"].append({"name": "input.root", "path": "./data/Data/input.root"})
    C.task_info_path(root).write_text(json.dumps(info))
    assert C.validate(root).ok

    info["data"][-1]["path"] = "./data/input.root"
    C.task_info_path(root).write_text(json.dumps(info))
    codes = {issue.code for issue in C.validate(root).errors}
    assert "data_missing" in codes and "data_unreferenced" in codes


def test_weights_and_image_required(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    C.checklist_path(root).write_text(json.dumps([
        {"type": "text", "content": "x", "weight": 0.5, "path": None},
        {"type": "text", "content": "y", "weight": 0.2, "path": None},
    ]), encoding="utf-8")  # sums to 0.7, no image
    codes = {i.code for i in C.validate(root).errors}
    assert "weights_sum" in codes and "no_image_item" in codes


def test_related_work_must_be_pdf(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    (C.related_work_dir(root) / "bad.pdf").write_text("<html>not a pdf</html>", encoding="utf-8")
    assert "related_work_not_pdf" in {i.code for i in C.validate(root).errors}


def test_unreferenced_and_extra_files(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    (C.data_dir(root) / "orphan.csv").write_text("1\n", encoding="utf-8")
    (C.target_study_dir(root) / "scratch.txt").write_text("junk", encoding="utf-8")
    codes = {i.code for i in C.validate(root).errors}
    assert "data_unreferenced" in codes and "target_study_extra" in codes


def test_object_checklist_wrapper_and_object_data_mapping_are_supported(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    info = {
        "task": "Analyze the supplied observation and report the fitted result.",
        "data": {
            "./data/obs.csv": {
                "type": "csv",
                "description": "Observed measurements.",
            }
        },
    }
    C.task_info_path(root).write_text(json.dumps(info), encoding="utf-8")
    checklist = json.loads(C.checklist_path(root).read_text(encoding="utf-8"))
    C.checklist_path(root).write_text(json.dumps({
        "checklist": checklist,
        "total_weight": 1.0,
    }), encoding="utf-8")

    res = C.validate(root)
    assert res.ok, [i.message for i in res.errors]


def test_inputs_alias_and_empty_target_study_paper_dir_are_supported(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    C.task_info_path(root).write_text(json.dumps({
        "task": "Analyze the supplied observation and report the fitted result.",
        "inputs": [{"filename": "./data/obs.csv", "description": "Observed measurements."}],
    }), encoding="utf-8")
    (C.target_study_dir(root) / "paper").mkdir()

    res = C.validate(root)
    assert res.ok, [i.message for i in res.errors]

    (C.target_study_dir(root) / "paper" / "unexpected.txt").write_text("data", encoding="utf-8")
    assert "target_study_extra" in {i.code for i in C.validate(root).errors}


def test_unrecognized_checklist_object_has_shape_error(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    C.checklist_path(root).write_text(json.dumps({"notes": "not a checklist"}), encoding="utf-8")
    assert "checklist_shape" in {i.code for i in C.validate(root).errors}


def test_structural_label_is_warning_only(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    items = json.loads(C.checklist_path(root).read_text())
    items[0]["content"] = "Reproduce Figure 4 from the paper"
    C.checklist_path(root).write_text(json.dumps(items), encoding="utf-8")
    res = C.validate(root)
    assert res.ok  # warning, not error
    assert "structural_label" in {i.code for i in res.warnings}


def test_export_public_excludes_answer_key(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    public = tmp_path / "public"
    C.export_public(root, public)
    assert (public / "task_info.json").exists()
    assert (public / "data" / "obs.csv").exists()
    assert (public / "related_work" / "paper_000.pdf").exists()
    assert not (public / "target_study").exists()  # hidden answer key not exported


def test_prepare_workspace_stages_pdf_templates_and_no_seed_metadata(tmp_path):
    root = tmp_path / "task"
    pdf_src = tmp_path / "src.pdf"
    pdf_src.write_bytes(b"%PDF-1.4 src")
    (root / "_seed_info.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "_seed_info.json").write_text("stale", encoding="utf-8")
    target = {"title": "T", "arxiv_id": "1", "year": 2020, "assigned_domain": "Physics",
              "rationale": "LEAK", "overall_score": 9.9}
    C.prepare_workspace(root, target, pdf_src)
    assert C.paper_path(root).read_bytes().startswith(b"%PDF-")
    assert not (root / "_seed_info.json").exists()
    assert "PLACEHOLDER_" in C.task_info_path(root).read_text(encoding="utf-8")
    assert "PLACEHOLDER_" in C.checklist_path(root).read_text(encoding="utf-8")
    assert "80_TO_150" not in C.task_info_path(root).read_text(encoding="utf-8")


def test_placeholder_templates_must_be_replaced(tmp_path):
    root = tmp_path / "task"
    _valid_pkg(root)
    info = json.loads(C.task_info_path(root).read_text(encoding="utf-8"))
    info["task"] = f"{info['task']} PLACEHOLDER_TASK"
    C.task_info_path(root).write_text(json.dumps(info), encoding="utf-8")
    items = json.loads(C.checklist_path(root).read_text(encoding="utf-8"))
    items[0]["content"] = "PLACEHOLDER_CHECKLIST"
    C.checklist_path(root).write_text(json.dumps(items), encoding="utf-8")

    codes = {i.code for i in C.validate(root).errors}
    assert "task_info_placeholder" in codes
    assert "checklist_placeholder" in codes


def test_registry_resolves_task_type():
    task_type = get_task_type("researchclawbench")
    assert task_type.name == "researchclawbench"
