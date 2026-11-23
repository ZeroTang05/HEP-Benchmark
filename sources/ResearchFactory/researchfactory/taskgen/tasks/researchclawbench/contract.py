"""Internalized ResearchClawBench task-package contract.

Derived from the ResearchClawBench task format (``tasks/<Domain>_<NNN>/``):

    task_info.json                      # {"task": str, "data": [{name,path,type,description}]}
    data/<referenced files>             # real, computable Solver inputs
    related_work/paper_*.pdf            # real PDFs (start with %PDF-)
    target_study/paper.pdf              # the (hidden) target paper
    target_study/checklist.json         # [{type,content,path,keywords,weight}], weights sum 1.0
    target_study/images/<figs>          # reference figures for image checklist items

Solver-visible inputs are ``task_info.json`` + ``data/`` + ``related_work/``;
``target_study/`` is the hidden answer key. This module owns layout, validation,
the Solver-visible ``public/`` export, and workspace staging, with no dependency
on any external package.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from researchfactory.taskgen.tasks.researchclawbench.hep_contract import CONTRACT as HEP_CONTRACT
from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import CONTRACT as HEP_EVALUATOR_CONTRACT
from researchfactory.taskgen.tasks.researchclawbench.hep_related_work import validate_references
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import PACKAGE_METADATA, rubric_enabled, validate_rubric

WEIGHT_TOLERANCE = 0.01
_STRUCTURAL_LABEL_RE = re.compile(r"\b(figure|fig\.|table|section|equation|eq\.)\s*\(?\s*\d", re.IGNORECASE)
_TARGET_STUDY_ALLOWED = {"paper.pdf", "checklist.json", "images", "evaluator", PACKAGE_METADATA}
SOLVER_VISIBLE = ("task_info.json", "data", "related_work")

TASK_INFO_TEMPLATE = {
    "task": (
        "PLACEHOLDER_TASK_DESCRIPTION_REPLACE_WITH_CLEAR_PUBLIC_RESEARCH_GOAL_INPUTS_"
        "EXPECTED_OUTPUT_AND_SCOPE"
    ),
    "data": [
        {
            "name": "PLACEHOLDER_INPUT_NAME",
            "path": "./data/PLACEHOLDER_INPUT_FILE.csv",
            "type": "PLACEHOLDER_INPUT_TYPE",
            "description": "PLACEHOLDER_INPUT_DESCRIPTION_REPLACE_WITH_SOURCE_AND_CONTENT_SUMMARY",
        }
    ],
}

CHECKLIST_TEMPLATE = [
    {
        "type": "image",
        "content": "PLACEHOLDER_IMAGE_CRITERION_REPLACE_WITH_SCIENTIFIC_VISUAL_REQUIREMENT",
        "path": "images/PLACEHOLDER_REFERENCE_IMAGE.png",
        "keywords": ["PLACEHOLDER_KEYWORD_IMAGE"],
        "weight": 0.34,
    },
    {
        "type": "text",
        "content": "PLACEHOLDER_TEXT_CRITERION_REPLACE_WITH_METHOD_OR_RESULT_REQUIREMENT",
        "path": None,
        "keywords": ["PLACEHOLDER_KEYWORD_TEXT_A"],
        "weight": 0.33,
    },
    {
        "type": "text",
        "content": "PLACEHOLDER_TEXT_CRITERION_REPLACE_WITH_QUANTITATIVE_OR_UNCERTAINTY_REQUIREMENT",
        "path": None,
        "keywords": ["PLACEHOLDER_KEYWORD_TEXT_B"],
        "weight": 0.33,
    },
]


@dataclass
class ValidationIssue:
    level: str   # "error" | "warning"
    code: str
    message: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    def error(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue("error", code, message))

    def warn(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue("warning", code, message))

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(5).startswith(b"%PDF-")
    except OSError:
        return False


class RcbenchContract:
    """Layout + validation + export for ResearchClawBench-format packages."""

    name = "researchclawbench"

    # ---- layout helpers ----
    def task_info_path(self, root: Path) -> Path:
        return root / "task_info.json"

    def data_dir(self, root: Path) -> Path:
        return root / "data"

    def related_work_dir(self, root: Path) -> Path:
        return root / "related_work"

    def target_study_dir(self, root: Path) -> Path:
        return root / "target_study"

    def paper_path(self, root: Path) -> Path:
        return root / "target_study" / "paper.pdf"

    def checklist_path(self, root: Path) -> Path:
        return root / "target_study" / "checklist.json"

    def images_dir(self, root: Path) -> Path:
        return root / "target_study" / "images"

    # ---- workspace staging (before the agent runs) ----
    def prepare_workspace(self, root: Path, target: dict, pdf_src: Path) -> None:
        """Create the layout, stage the target PDF, and write JSON templates."""
        for d in (self.data_dir(root), self.related_work_dir(root), self.images_dir(root)):
            d.mkdir(parents=True, exist_ok=True)
        dst = self.paper_path(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(pdf_src), str(dst))
        stale_seed = root / "_seed_info.json"
        if stale_seed.exists():
            stale_seed.unlink()
        self.task_info_path(root).write_text(
            json.dumps(TASK_INFO_TEMPLATE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.checklist_path(root).write_text(
            json.dumps(CHECKLIST_TEMPLATE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- validation ----
    def validate(self, root: Path, *, require_hep_package: bool = False) -> ValidationResult:
        res = ValidationResult()
        task_info = self._validate_task_info(root, res)
        self._validate_paper(root, res)
        rich_hep = rubric_enabled(root)
        if require_hep_package and not rich_hep:
            res.error("hep_package_contract_missing", f"missing target_study/{PACKAGE_METADATA}")
        if not rich_hep:
            self._validate_checklist(root, res, require_image=not isinstance(task_info, dict) or task_info.get("hep_data_contract") != "v1")
            self._validate_related_work(root, res)
        else:
            try:
                target = json.loads((self.target_study_dir(root) / PACKAGE_METADATA).read_text(encoding="utf-8"))
                validate_references(root, target)
            except (OSError, ValueError) as exc:
                res.error("hep_related_work", str(exc))
            if not isinstance(task_info, dict) or task_info.get("hep_data_contract") != "v1":
                res.error("hep_data_contract_missing", 'HEP package requires hep_data_contract: "v1"')
        self._validate_target_study_contents(root, res)
        if isinstance(task_info, dict) and "hep_data_contract" in task_info:
            evaluator_result = HEP_EVALUATOR_CONTRACT.validate(root)
            for code, message in evaluator_result.errors:
                res.error(code, message)
            for code, message in evaluator_result.warnings:
                res.warn(code, message)
            context_only_allowed = False
            if rich_hep and not evaluator_result.errors:
                try:
                    evaluator = json.loads((HEP_EVALUATOR_CONTRACT.evaluator_dir(root) / "schema.json").read_text(encoding="utf-8"))
                    validate_rubric(json.loads(self.checklist_path(root).read_text(encoding="utf-8")), evaluator)
                    # 无数值检查的有效评分清单用题面和文献支撑定性判断，无需添加占位测量表。
                    context_only_allowed = not evaluator.get("numeric_checks")
                except (OSError, ValueError) as exc:
                    res.error("hep_checklist", str(exc))
            hep_result = HEP_CONTRACT.validate(root, task_info, allow_context_only=context_only_allowed)
            for code, message in hep_result.errors:
                res.error(code, message)
            for code, message in hep_result.warnings:
                res.warn(code, message)
        return res

    @staticmethod
    def _data_items(value: Any) -> list[dict[str, Any]]:
        """Normalize list, descriptor-object, wrapper, and path-map inputs."""
        if isinstance(value, list):
            items = []
            for raw_item in value:
                if isinstance(raw_item, dict):
                    items.append(dict(raw_item))
                elif isinstance(raw_item, str) and raw_item.strip():
                    items.append({"path": raw_item.strip(),
                                  "name": Path(raw_item.strip()).name})
            for item in items:
                if not item.get("path"):
                    item["path"] = (item.get("file") or item.get("filename")
                                    or item.get("filepath") or item.get("name"))
            return items
        if not isinstance(value, dict):
            return []

        descriptor_keys = {"path", "name", "file", "filename", "filepath"}
        if descriptor_keys.intersection(value):
            item = dict(value)
            if not item.get("path"):
                item["path"] = (item.get("file") or item.get("filename")
                                or item.get("filepath") or item.get("name"))
            return [item]

        for key in ("items", "files", "inputs", "data", "datasets"):
            if key in value:
                nested = RcbenchContract._data_items(value[key])
                if nested:
                    return nested

        # A mapping from file path to metadata/description.
        items: list[dict[str, Any]] = []
        for path, details in value.items():
            if isinstance(details, dict):
                item = dict(details)
                item.setdefault("path", str(path))
                item.setdefault("name", Path(str(path)).name)
                items.append(item)
            elif isinstance(details, str):
                items.append({"path": str(path), "name": Path(str(path)).name,
                              "description": details})
        return items

    @staticmethod
    def _checklist_items(value: Any) -> list[dict[str, Any]] | None:
        """Normalize list and common object-wrapped checklist forms."""
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return None

        # A single criterion represented as an object.
        if any(key in value for key in ("type", "content", "criterion", "weight")):
            return [value]

        for key in ("checklist", "items", "criteria", "rubric", "evaluation"):
            if key in value:
                nested = RcbenchContract._checklist_items(value[key])
                if nested is not None:
                    return nested

        # Criterion-id -> criterion-object form.
        mapped = [item for item in value.values() if isinstance(item, dict)]
        if mapped and len(mapped) == len(value):
            return mapped
        return None

    def _validate_task_info(self, root: Path, res: ValidationResult) -> dict[str, Any] | None:
        p = self.task_info_path(root)
        if not p.exists():
            res.error("task_info_missing", f"missing {p.name}")
            return None
        try:
            raw_text = p.read_text(encoding="utf-8")
            info = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as exc:
            res.error("task_info_parse", f"cannot parse task_info.json: {exc}")
            return None
        if not isinstance(info, dict):
            res.error("task_info_shape", "task_info.json must contain a JSON object")
            return None
        if "PLACEHOLDER_" in raw_text:
            res.error("task_info_placeholder", "task_info.json still contains PLACEHOLDER_ text")
        task_value = info.get("task") or info.get("task_objective")
        if isinstance(task_value, dict):
            task = " ".join(str(value) for value in task_value.values()
                            if value is not None).strip()
        else:
            task = str(task_value or "").strip()
        if not task:
            res.error("task_empty", "task_info.task is empty")

        raw_data = info.get("data") or info.get("inputs") or []
        data_items = self._data_items(raw_data)
        referenced: set[str] = set()
        ddir = self.data_dir(root)
        for item in data_items:
            references = [
                item.get("path") or item.get("name") or item.get("file") or item.get("filename"),
                item.get("metadata") or item.get("metadata_path"),
                item.get("provenance") or item.get("provenance_path"),
            ]
            for raw_value in references:
                if not isinstance(raw_value, str) or not raw_value.strip():
                    continue
                raw = raw_value.strip()
                normalized = raw.replace("\\", "/")
                if normalized.startswith("./"):
                    normalized = normalized[2:]
                if normalized.startswith("data/"):
                    normalized = normalized[5:]
                relative = Path(normalized)
                if relative.is_absolute() or not normalized or ".." in relative.parts:
                    res.error("data_path_invalid", f"task_info data[] has invalid path: {raw}")
                    continue
                referenced.add(relative.as_posix())
                if not (ddir / relative).is_file():
                    res.error("data_missing", f"task_info data[] references missing file: {raw}")
        # every file present under data/ must be referenced
        if ddir.is_dir():
            for f in ddir.rglob("*"):
                if f.is_file() and f.relative_to(ddir).as_posix() not in referenced:
                    res.error("data_unreferenced",
                              f"data/{f.relative_to(ddir).as_posix()} is not referenced in task_info.data[]")
        return info

    def _validate_paper(self, root: Path, res: ValidationResult) -> None:
        p = self.paper_path(root)
        if not p.exists():
            res.error("paper_missing", "missing target_study/paper.pdf")
        elif not _is_pdf(p):
            res.error("paper_not_pdf", "target_study/paper.pdf is not a valid PDF")

    def _validate_checklist(self, root: Path, res: ValidationResult, *, require_image: bool) -> None:
        p = self.checklist_path(root)
        if not p.exists():
            res.error("checklist_missing", "missing target_study/checklist.json")
            return
        try:
            raw_text = p.read_text(encoding="utf-8")
            items = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as exc:
            res.error("checklist_parse", f"cannot parse checklist.json: {exc}")
            return
        if "PLACEHOLDER_" in raw_text:
            res.error("checklist_placeholder", "checklist.json still contains PLACEHOLDER_ text")
        normalized = self._checklist_items(items)
        if normalized is None:
            res.error("checklist_shape", "checklist.json must be a list or an object containing checklist items")
            return
        if not normalized:
            res.error("checklist_empty", "checklist.json must contain at least one checklist item")
            return
        if not (3 <= len(normalized) <= 6):
            res.warn("checklist_count", f"{len(normalized)} checklist items (expected 3-6)")

        total = 0.0
        has_image = False
        for i, item in enumerate(normalized):
            if not isinstance(item, dict):
                res.error("checklist_item", f"item {i} is not an object")
                continue
            try:
                total += float(item.get("weight") or 0)
            except (TypeError, ValueError):
                res.error("checklist_weight", f"item {i} has a non-numeric weight")
            content = str(item.get("content") or item.get("description")
                          or item.get("criterion") or "")
            if _STRUCTURAL_LABEL_RE.search(content):
                res.warn("structural_label",
                         f"item {i} content names a target-paper structural label (Figure/Table/Section)")
            if item.get("type") == "image":
                has_image = True
                rel = str(item.get("path") or item.get("image_path")
                          or item.get("reference") or item.get("image") or "").strip()
                rel = rel.replace("\\", "/")
                for prefix in ("task/target_study/", "target_study/"):
                    if rel.startswith(prefix):
                        rel = rel[len(prefix):]
                        break
                if not rel or not (self.target_study_dir(root) / rel).exists():
                    res.error("image_path_missing",
                              f"image item {i} path does not exist: {rel!r}")
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            res.error("weights_sum", f"checklist weights sum to {total:.3f}, expected 1.0")
        if require_image and not has_image:
            res.error("no_image_item", "checklist has no type=image item")

    def _validate_related_work(self, root: Path, res: ValidationResult) -> None:
        d = self.related_work_dir(root)
        if not d.is_dir():
            res.warn("no_related_work", "no related_work/ directory")
            return
        pdfs = [f for f in d.iterdir() if f.is_file()]
        if not pdfs:
            res.warn("no_related_work", "related_work/ is empty")
        for f in pdfs:
            if not _is_pdf(f):
                res.error("related_work_not_pdf", f"related_work/{f.name} is not a valid PDF")

    def _validate_target_study_contents(self, root: Path, res: ValidationResult) -> None:
        d = self.target_study_dir(root)
        if not d.is_dir():
            res.error("target_study_missing", "missing target_study/ directory")
            return
        for entry in d.iterdir():
            if entry.name not in _TARGET_STUDY_ALLOWED:
                # Some agents create this empty intermediate directory while
                # staging target_study/paper.pdf. It has no package impact.
                if entry.name == "paper" and entry.is_dir() and not any(entry.iterdir()):
                    continue
                res.error("target_study_extra",
                          f"unexpected entry in target_study/: {entry.name}")

    # ---- Solver-visible export ----
    def export_public(self, root: Path, public_root: Path) -> list[Path]:
        """Copy the Solver-visible inputs (task_info.json + data/ + related_work/)."""
        public_root.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for name in SOLVER_VISIBLE:
            src = root / name
            dst = public_root / name
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                copied.append(dst)
            elif src.is_file():
                shutil.copy2(str(src), str(dst))
                copied.append(dst)
        return copied

CONTRACT = RcbenchContract()
