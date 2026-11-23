"""高能物理任务的隐藏 evaluator 合同。

这里先验证评分规格本身。评分执行器会在后续步骤读取同一份 schema 和
reference，当前模块不执行 Solver 代码，也不把隐藏评分资料导出到 public/。
"""
from __future__ import annotations

import json
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import (
    normalize_output_path, rubric_enabled, rubric_path, validate_rubric,
)


HEP_EVALUATOR_SCHEMA = "researchfactory.hep_evaluator.v1"
HEP_REFERENCE_SCHEMA = "researchfactory.hep_evaluator_reference.v1"
ALLOWED_EVALUATOR_FILES = frozenset({
    "schema.json",
    "reference.json",
    "numeric_checks.py",
    "table_checks.py",
    "figure_checks.py",
    "score.py",
})
ALLOWED_OUTPUT_FORMATS = frozenset({"json", "csv", "figure", "text"})
MIN_FIGURE_PIXELS = 128


def validate_figure(path: Path) -> tuple[bool, str]:
    """用成熟 PDF/图像解码器核实图表可读性，并拒绝空白或占位小图。"""
    with pymupdf.open(path) as document:
        if not document.page_count:
            return False, "figure has no readable page"
        pixmap = document[0].get_pixmap(dpi=96, alpha=False)
        if min(pixmap.width, pixmap.height) < MIN_FIGURE_PIXELS:
            return False, f"figure is smaller than {MIN_FIGURE_PIXELS} pixels on an axis"
        if pixmap.color_count() <= 1:
            return False, "figure is blank or a single-color placeholder"
    return True, "figure is readable and has nontrivial image content"


@dataclass
class HepEvaluatorValidationResult:
    """evaluator 合同的错误和警告。"""

    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def error(self, code: str, message: str) -> None:
        self.errors.append((code, message))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append((code, message))


@dataclass(frozen=True)
class HepCheckResult:
    """一个确定性评分检查的结果。"""

    name: str
    passed: bool
    message: str
    readable: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            **({"readable": self.readable} if self.readable is not None else {}),
        }


@dataclass(frozen=True)
class HepEvaluationResult:
    """一次 HEP evaluator 执行结果。"""

    passed: bool
    score: float
    checks: tuple[HepCheckResult, ...]
    rubric: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "researchfactory.hep_evaluation_result.v1",
            "passed": self.passed,
            "score": self.score,
            "checks": [check.to_dict() for check in self.checks],
            **({"rubric": self.rubric} if self.rubric is not None else {}),
        }


def _json(path: Path, result: HepEvaluatorValidationResult, code: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result.error(code, f"cannot parse {path.name}: {exc}")
        return None
    if not isinstance(value, dict):
        result.error(code, f"{path.name} must contain one JSON object")
        return None
    return value


class HepEvaluatorContract:
    """Validate hidden HEP evaluator schema and reference files."""

    name = "hep_evaluator_v1"

    def evaluator_dir(self, root: Path) -> Path:
        return root / "target_study" / "evaluator"

    def validate(self, root: Path) -> HepEvaluatorValidationResult:
        result = HepEvaluatorValidationResult()
        directory = self.evaluator_dir(root)
        if not directory.is_dir():
            result.error("hep_evaluator_missing", "missing target_study/evaluator/")
            return result
        for path in directory.iterdir():
            if path.name not in ALLOWED_EVALUATOR_FILES:
                result.error("hep_evaluator_extra", f"unexpected evaluator file: {path.name}")
        schema_path = directory / "schema.json"
        reference_path = directory / "reference.json"
        if not schema_path.is_file():
            result.error("hep_evaluator_schema_missing", "missing target_study/evaluator/schema.json")
        if not reference_path.is_file():
            result.error("hep_evaluator_reference_missing", "missing target_study/evaluator/reference.json")
        schema = _json(schema_path, result, "hep_evaluator_schema_parse") if schema_path.is_file() else None
        reference = _json(reference_path, result, "hep_evaluator_reference_parse") if reference_path.is_file() else None
        if schema is not None:
            self._validate_schema(schema, result)
        if reference is not None:
            self._validate_reference(reference, result, require_values=schema is None or bool(schema.get("numeric_checks")))
        return result

    def _validate_schema(self, schema: dict[str, Any], result: HepEvaluatorValidationResult) -> None:
        if schema.get("schema_version") != HEP_EVALUATOR_SCHEMA:
            result.error("hep_evaluator_schema_version", f"schema.json schema_version must be {HEP_EVALUATOR_SCHEMA!r}")
        outputs = schema.get("required_outputs")
        if not isinstance(outputs, list) or not outputs:
            result.error("hep_evaluator_outputs", "schema.json required_outputs must be a non-empty list")
            outputs = []
        seen: set[str] = set()
        for index, output in enumerate(outputs):
            if not isinstance(output, dict):
                result.error("hep_evaluator_output_item", f"required_outputs[{index}] must be an object")
                continue
            path = normalize_output_path(output.get("path"))
            if path is None:
                result.error("hep_evaluator_output_path", f"required_outputs[{index}].path is invalid")
            elif path in seen:
                result.error("hep_evaluator_output_duplicate", f"required output path is duplicated: {path}")
            else:
                seen.add(path)
            if not str(output.get("format") or "").strip():
                result.error("hep_evaluator_output_format", f"required_outputs[{index}].format is required")
            elif output.get("format") not in ALLOWED_OUTPUT_FORMATS:
                result.error("hep_evaluator_output_format", f"required_outputs[{index}].format is unsupported")
            if not str(output.get("description") or "").strip():
                result.error("hep_evaluator_output_description", f"required_outputs[{index}].description is required")

        checks = schema.get("numeric_checks", [])
        if not isinstance(checks, list):
            result.error("hep_evaluator_numeric_checks", "numeric_checks must be a list")
            return
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                result.error("hep_evaluator_numeric_item", f"numeric_checks[{index}] must be an object")
                continue
            for key in ("name", "output", "json_pointer", "reference"):
                if not str(check.get(key) or "").strip():
                    result.error("hep_evaluator_numeric_item", f"numeric_checks[{index}].{key} is required")
            if normalize_output_path(check.get("output")) is None:
                result.error("hep_evaluator_numeric_output", f"numeric_checks[{index}].output is invalid")
            tolerance = check.get("abs_tolerance")
            if not isinstance(tolerance, (int, float)) or tolerance < 0:
                result.error("hep_evaluator_tolerance", f"numeric_checks[{index}].abs_tolerance must be non-negative")

    def load_spec(self, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        """读取并验证隐藏评分规格，规格错误直接抛出。"""
        validation = self.validate(root)
        if validation.errors:
            detail = "; ".join(message for _code, message in validation.errors)
            raise ValueError(f"invalid HEP evaluator contract: {detail}")
        directory = self.evaluator_dir(root)
        schema = json.loads((directory / "schema.json").read_text(encoding="utf-8"))
        reference = json.loads((directory / "reference.json").read_text(encoding="utf-8"))
        return schema, reference

    def evaluate(self, task_root: Path, solver_root: Path) -> HepEvaluationResult:
        """对 Solver 输出执行确定性检查并返回分数。"""
        schema, reference = self.load_spec(task_root)
        checks: list[HepCheckResult] = []
        parsed_json: dict[Path, Any] = {}
        output_specs = schema["required_outputs"]
        output_paths = {
            normalize_output_path(spec["path"]): spec
            for spec in output_specs
        }
        for spec in output_specs:
            relative = normalize_output_path(spec["path"])
            assert relative is not None
            output_path = solver_root / relative
            name = f"required_output:{relative}"
            if not output_path.is_file():
                checks.append(HepCheckResult(name, False, f"missing Solver output: {relative}"))
                continue
            output_format = spec["format"]
            if output_format == "json":
                try:
                    parsed_json[output_path] = json.loads(output_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    checks.append(HepCheckResult(name, False, f"invalid JSON output: {exc}"))
                    continue
            elif output_format == "csv":
                try:
                    with output_path.open("r", encoding="utf-8", newline="") as handle:
                        rows = list(csv.reader(handle))
                except (OSError, UnicodeError, csv.Error) as exc:
                    checks.append(HepCheckResult(name, False, f"invalid CSV output: {exc}"))
                    continue
                if not rows or not rows[0]:
                    checks.append(HepCheckResult(name, False, "CSV output has no header"))
                    continue
            elif output_format == "figure":
                try:
                    valid, message = validate_figure(output_path)
                except (OSError, RuntimeError, ValueError) as exc:
                    valid, message = False, f"cannot decode figure: {exc}"
                if not valid:
                    checks.append(HepCheckResult(name, False, message))
                    continue
            elif output_format == "text" and output_path.stat().st_size == 0:
                checks.append(HepCheckResult(name, False, "Solver output is empty"))
                continue
            required_keys = spec.get("required_keys") or []
            if required_keys:
                value = parsed_json.get(output_path)
                if not isinstance(value, dict):
                    checks.append(HepCheckResult(name, False, "JSON output must be an object for required_keys"))
                    continue
                missing = [key for key in required_keys if key not in value]
                if missing:
                    checks.append(HepCheckResult(name, False, f"missing JSON keys: {', '.join(missing)}", readable=True))
                    continue
            checks.append(HepCheckResult(name, True, "required Solver output is present and readable"))

        reference_values = reference["values"]
        for spec in schema.get("numeric_checks", []):
            name = f"numeric:{spec['name']}"
            relative = normalize_output_path(spec["output"])
            assert relative is not None
            output_path = solver_root / relative
            if relative not in output_paths:
                checks.append(HepCheckResult(name, False, f"numeric check references undeclared output: {relative}"))
                continue
            value = parsed_json.get(output_path)
            if value is None and output_path.is_file() and output_paths[relative]["format"] == "json":
                try:
                    value = json.loads(output_path.read_text(encoding="utf-8"))
                    parsed_json[output_path] = value
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    checks.append(HepCheckResult(name, False, f"cannot read numeric JSON output: {exc}"))
                    continue
            actual = _json_pointer(value, str(spec["json_pointer"]))
            reference_key = str(spec["reference"])
            expected = reference_values.get(reference_key)
            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isfinite(float(actual)):
                checks.append(HepCheckResult(name, False, "Solver numeric value is not finite"))
                continue
            if not isinstance(expected, (int, float)) or isinstance(expected, bool) or not math.isfinite(float(expected)):
                raise ValueError(f"reference value {reference_key!r} is not finite")
            tolerance = float(spec["abs_tolerance"])
            difference = abs(float(actual) - float(expected))
            passed = difference <= tolerance
            checks.append(
                HepCheckResult(
                    name,
                    passed,
                    f"actual={actual}, expected={expected}, abs_error={difference:g}, tolerance={tolerance:g}",
                )
            )

        if rubric_enabled(task_root):
            groups = validate_rubric(json.loads(rubric_path(task_root).read_text(encoding="utf-8")), schema)
            results = {check.name: check.passed for check in checks}
            fixed = [item for group in groups for item in group["items"] if item["check"] != "judge"]
            # 确定性分按自动小项的权重归一化，使用完整清单算出的总分在科学评审后生成。
            # 纯定性任务只有文件检查，因此确定性分只反映交付完整度。
            earned = math.fsum(item["weight"] for item in fixed if results[item["check"]])
            score = earned / math.fsum(item["weight"] for item in fixed) if fixed else sum(results.values()) / len(results)
            return HepEvaluationResult(all(results.values()), score, tuple(checks), groups)

        total = len(checks)
        passed_count = sum(1 for check in checks if check.passed)
        score = passed_count / total if total else 0.0
        return HepEvaluationResult(score == 1.0, score, tuple(checks))

    def _validate_reference(
        self, reference: dict[str, Any], result: HepEvaluatorValidationResult, *, require_values: bool,
    ) -> None:
        if reference.get("schema_version") != HEP_REFERENCE_SCHEMA:
            result.error("hep_reference_schema_version", f"reference.json schema_version must be {HEP_REFERENCE_SCHEMA!r}")
        values = reference.get("values")
        if not isinstance(values, dict) or (require_values and not values):
            result.error("hep_reference_values", "reference.json values must contain the declared numeric references")


CONTRACT = HepEvaluatorContract()


def _json_pointer(value: Any, pointer: str) -> Any:
    """读取 RFC 6901 风格的 JSON Pointer；空路径表示根对象。"""
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        return None
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current
