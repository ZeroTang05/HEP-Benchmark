"""高能物理输入数据合同。

这个模块只负责检查 HEP 任务的输入数据和旁车元数据。它使用 Python 标准库
解析 JSON、CSV/TSV 和 SHA-256，避免在任务生成阶段复制一套数据解析工具。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


HEP_DATA_CONTRACT_VERSION = "v1"
HEP_METADATA_SCHEMA = "researchfactory.hep_data_metadata.v1"
HEP_PROVENANCE_SCHEMA = "researchfactory.hep_source_provenance.v1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ALLOWED_BINNING = {"bin_edges", "bin_centers"}
_ALLOWED_MISSING_POLICIES = {"blank_is_missing", "null_is_missing"}
_ALLOWED_SOURCE_TYPES = {
    "measurement_input",
    "supplementary_measurement",
    "public_data",
}
HEP_TASK_TYPES = frozenset({
    "research_framing",
    "hypothesis_generation",
    "analysis_design",
    "spectrum_fit",
    "ratio_analysis",
    "template_decomposition",
    "time_series_modulation",
    "event_selection",
    "detector_calibration",
    "unfolding",
    "likelihood_inference",
    "systematics_propagation",
    "figure_digitization",
    "cross_experiment_comparison",
    "reproducibility_audit",
})
HEP_DIFFICULTIES = frozenset({"basic", "applied", "integrated", "advanced"})


@dataclass
class HepValidationResult:
    """HEP 合同产生的错误和警告。"""

    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def error(self, code: str, message: str) -> None:
        self.errors.append((code, message))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append((code, message))


def _normalize_reference(raw: Any) -> str:
    """把 task_info 中的 data 引用转成 data/ 下的相对路径。"""
    value = str(raw or "").strip().replace("\\", "/")
    if value.startswith("./"):
        value = value[2:]
    if value.startswith("data/"):
        value = value[5:]
    return value


def _safe_data_path(raw: Any) -> Path | None:
    normalized = _normalize_reference(raw)
    if not normalized:
        return None
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _read_json(path: Path, result: HepValidationResult, code: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result.error(code, f"cannot parse {path.name}: {exc}")
        return None
    if not isinstance(value, dict):
        result.error(code, f"{path.name} must contain a JSON object")
        return None
    return value


def _finite_number(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _required_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _column_name(spec: Any, default: str = "") -> str:
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, dict):
        return str(spec.get("column") or spec.get("name") or default).strip()
    return default


def _metadata_path(item: dict[str, Any], data_path: Path) -> Path:
    explicit = item.get("metadata") or item.get("metadata_path")
    if explicit:
        path = _safe_data_path(explicit)
        if path is None:
            raise ValueError(f"invalid metadata path: {explicit!r}")
        return path
    return data_path.with_name(f"{data_path.stem}.metadata.json")


def _provenance_path(item: dict[str, Any]) -> Path:
    explicit = item.get("provenance") or item.get("provenance_path")
    if explicit:
        path = _safe_data_path(explicit)
        if path is None:
            raise ValueError(f"invalid provenance path: {explicit!r}")
        return path
    return Path("source_provenance.json")


def _iter_data_items(task_info: dict[str, Any]) -> Iterable[dict[str, Any]]:
    raw = task_info.get("data") or task_info.get("inputs") or []
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("files") or raw.get("inputs") or raw
    if not isinstance(raw, list):
        return ()
    return (dict(item) for item in raw if isinstance(item, dict))


def _read_table(path: Path, result: HepValidationResult) -> tuple[list[str], list[dict[str, str]]]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            headers = [str(header or "").strip() for header in (reader.fieldnames or [])]
            # 发布表的列名可能带空格；表头与每行的字典键必须使用同一名称。
            reader.fieldnames = headers
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        result.error("hep_table_parse", f"cannot parse {path.name}: {exc}")
        return [], []
    if not headers or any(not header for header in headers):
        result.error("hep_table_header", f"{path.name} must have non-empty column names")
    if len(headers) != len(set(headers)):
        result.error("hep_table_duplicate_column", f"{path.name} has duplicate column names")
    return headers, rows


class HepDataContract:
    """Validate HEP measurement tables and their metadata sidecars."""

    name = "hep_data_v1"

    def validate(
        self, root: Path, task_info: dict[str, Any], *, allow_context_only: bool = False,
    ) -> HepValidationResult:
        result = HepValidationResult()
        contract_version = task_info.get("hep_data_contract")
        if contract_version != HEP_DATA_CONTRACT_VERSION:
            result.error(
                "hep_contract_version",
                f"hep_data_contract must be {HEP_DATA_CONTRACT_VERSION!r}, got {contract_version!r}",
            )
            return result

        items = list(_iter_data_items(task_info))
        if not items and not (allow_context_only and task_info.get("data") == []):
            result.error("hep_data_empty", "HEP data contract requires task_info.data[]")
            return result
        self._validate_task_plan(task_info, result)

        data_dir = root / "data"
        provenance_cache: dict[Path, dict[str, Any] | None] = {}
        for index, item in enumerate(items):
            raw_path = item.get("path") or item.get("file") or item.get("filename") or item.get("name")
            data_path = _safe_data_path(raw_path)
            if data_path is None:
                result.error("hep_data_path", f"data item {index} has invalid path: {raw_path!r}")
                continue
            absolute = data_dir / data_path
            if not absolute.is_file():
                result.error("hep_data_missing", f"HEP data file is missing: data/{data_path.as_posix()}")
                continue
            if absolute.suffix.lower() not in {".csv", ".tsv"}:
                result.error(
                    "hep_table_format",
                    f"HEP measurement input must be CSV or TSV: data/{data_path.as_posix()}",
                )
                continue

            try:
                metadata_rel = _metadata_path(item, data_path)
            except ValueError as exc:
                result.error("hep_metadata_path", str(exc))
                continue
            metadata_path = data_dir / metadata_rel
            if not metadata_path.is_file():
                result.error(
                    "hep_metadata_missing",
                    f"metadata sidecar is missing: data/{metadata_rel.as_posix()}",
                )
                continue
            metadata = _read_json(metadata_path, result, "hep_metadata_parse")
            if metadata is None:
                continue

            self._validate_metadata(metadata, metadata_path, result)
            headers, rows = _read_table(absolute, result)
            self._validate_table(metadata, absolute, headers, rows, result)

            try:
                provenance_rel = _provenance_path(item)
            except ValueError as exc:
                result.error("hep_provenance_path", str(exc))
                continue
            provenance_path = data_dir / provenance_rel
            provenance = provenance_cache.get(provenance_rel)
            if provenance_rel not in provenance_cache:
                if not provenance_path.is_file():
                    result.error(
                        "hep_provenance_missing",
                        f"source provenance sidecar is missing: data/{provenance_rel.as_posix()}",
                    )
                    provenance = None
                else:
                    provenance = _read_json(provenance_path, result, "hep_provenance_parse")
                provenance_cache[provenance_rel] = provenance
            if provenance is not None:
                self._validate_provenance(provenance, data_path, absolute, result)
        return result

    def _validate_task_plan(self, task_info: dict[str, Any], result: HepValidationResult) -> None:
        """检查 HEP 任务的物理分析目标是否可执行。"""
        plan = task_info.get("hep_task")
        if not isinstance(plan, dict):
            result.error("hep_task_plan_missing", "HEP task_info requires a hep_task object")
            return
        primary = str(plan.get("primary_task_type") or "").strip()
        if primary not in HEP_TASK_TYPES:
            result.error("hep_task_type", f"unsupported HEP primary_task_type: {primary!r}")
        secondary = plan.get("secondary_task_types", [])
        if not isinstance(secondary, list) or len(secondary) > 2:
            result.error("hep_task_secondary", "secondary_task_types must contain at most two items")
            secondary = []
        for item in secondary:
            task_type = str(item or "").strip()
            if task_type not in HEP_TASK_TYPES:
                result.error("hep_task_type", f"unsupported HEP secondary_task_type: {task_type!r}")
            if task_type == primary:
                result.error("hep_task_secondary", "secondary_task_types cannot repeat primary_task_type")
        for key in ("observable", "independent_variable"):
            if not str(plan.get(key) or "").strip():
                result.error("hep_task_scope", f"hep_task.{key} is required")
        for key in ("required_inputs", "required_outputs"):
            values = plan.get(key)
            if not isinstance(values, list) or not values or any(not str(value or "").strip() for value in values):
                result.error("hep_task_io", f"hep_task.{key} must be a non-empty list of names")
            elif len(set(str(value).strip() for value in values)) != len(values):
                result.error("hep_task_io", f"hep_task.{key} contains duplicate names")
        if plan.get("difficulty") not in HEP_DIFFICULTIES:
            result.error("hep_task_difficulty", "hep_task.difficulty must be basic, applied, integrated, or advanced")

    def _validate_metadata(
        self,
        metadata: dict[str, Any],
        path: Path,
        result: HepValidationResult,
    ) -> None:
        if metadata.get("schema_version") != HEP_METADATA_SCHEMA:
            result.error(
                "hep_metadata_schema",
                f"{path.name} schema_version must be {HEP_METADATA_SCHEMA!r}",
            )
        if not _required_text(metadata.get("observable")):
            result.error("hep_observable_missing", f"{path.name} observable is required")

        independent = metadata.get("independent_variable")
        if not isinstance(independent, dict):
            result.error("hep_independent_variable", f"{path.name} independent_variable must be an object")
        else:
            for key in ("name", "unit"):
                if not _required_text(independent.get(key)):
                    result.error("hep_independent_variable", f"{path.name} independent_variable.{key} is required")
            if independent.get("binning") not in _ALLOWED_BINNING:
                result.error(
                    "hep_binning",
                    f"{path.name} independent_variable.binning must be bin_edges or bin_centers",
                )

        value = metadata.get("value")
        if not isinstance(value, dict):
            result.error("hep_value", f"{path.name} value must be an object")
        else:
            for key in ("name", "unit", "column"):
                if not _required_text(value.get(key)):
                    result.error("hep_value", f"{path.name} value.{key} is required")
            if value.get("quantity_type") != "differential":
                result.error(
                    "hep_quantity_type",
                    f"{path.name} value.quantity_type must be 'differential'",
                )

        uncertainties = metadata.get("uncertainties")
        if not isinstance(uncertainties, list):
            result.error("hep_uncertainties", f"{path.name} uncertainties must be a list")
            uncertainties = []
        names: set[str] = set()
        for index, uncertainty in enumerate(uncertainties):
            if not isinstance(uncertainty, dict):
                result.error("hep_uncertainty_item", f"{path.name} uncertainties[{index}] must be an object")
                continue
            name = str(uncertainty.get("name") or "").strip()
            names.add(name)
            for key in ("name", "column", "unit"):
                if not _required_text(uncertainty.get(key)):
                    result.error("hep_uncertainty_item", f"{path.name} uncertainties[{index}].{key} is required")
        if "statistical" not in names:
            result.error("hep_statistical_error_missing", f"{path.name} must declare statistical uncertainty")
        if "systematic_total" not in names:
            result.error("hep_systematic_error_missing", f"{path.name} must declare systematic_total uncertainty")

        correlations = metadata.get("correlations")
        if not isinstance(correlations, dict) or not isinstance(correlations.get("available"), bool):
            result.error("hep_correlations", f"{path.name} correlations.available must be boolean")
        elif correlations["available"]:
            if not _required_text(correlations.get("source")) and not isinstance(correlations.get("matrix"), list):
                result.error("hep_correlations", f"{path.name} declares correlations but gives no source or matrix")
        elif not _required_text(correlations.get("assumption")):
            result.error("hep_correlations", f"{path.name} must state an assumption when correlations are unavailable")

        policy = metadata.get("missing_value_policy")
        if policy not in _ALLOWED_MISSING_POLICIES:
            result.error(
                "hep_missing_value_policy",
                f"{path.name} missing_value_policy must be blank_is_missing or null_is_missing",
            )
        if not isinstance(metadata.get("positive_value_required_for_log_fit"), bool):
            result.error(
                "hep_log_policy",
                f"{path.name} positive_value_required_for_log_fit must be boolean",
            )

    def _validate_table(
        self,
        metadata: dict[str, Any],
        path: Path,
        headers: list[str],
        rows: list[dict[str, str]],
        result: HepValidationResult,
    ) -> None:
        if not headers:
            return
        independent = metadata.get("independent_variable")
        value = metadata.get("value")
        if not isinstance(independent, dict) or not isinstance(value, dict):
            return

        def require_column(column: str, code: str) -> bool:
            if column not in headers:
                result.error(code, f"{path.name} is missing required column {column!r}")
                return False
            return True

        value_column = str(value.get("column") or "").strip()
        value_present = require_column(value_column, "hep_value_column") if value_column else False

        binning = independent.get("binning")
        columns = independent.get("columns") if isinstance(independent.get("columns"), dict) else {}
        group_by = independent.get("group_by", [])
        if not isinstance(group_by, list) or any(
            not isinstance(column, str) or not column.strip() for column in group_by
        ) or len(group_by) != len(set(group_by)):
            result.error("hep_bin_group", f"{path.name} independent_variable.group_by must list distinct columns")
            return
        axis_columns = (
            (_column_name(columns.get("low"), "bin_low"), _column_name(columns.get("high"), "bin_high"))
            if binning == "bin_edges" else (_column_name(columns.get("center"), "bin_center"),)
        )
        if set(group_by).intersection(axis_columns):
            result.error("hep_bin_group", f"{path.name} group_by must use columns outside the binned axis")
            return
        if not all(require_column(column, "hep_bin_group") for column in group_by):
            return

        # 同一套分箱可以出现在多个观测窗口，只比较同组的相邻数据行。
        groups: dict[tuple[str, ...], list[tuple[int, dict[str, str]]]] = {}
        for row_index, row in enumerate(rows, 1):
            group = tuple((row.get(column) or "").strip() for column in group_by)
            if "" in group:
                result.error("hep_bin_group", f"{path.name} row {row_index} has an empty bin group key")
                continue
            groups.setdefault(group, []).append((row_index, row))

        if binning == "bin_edges":
            low, high = axis_columns
            low_present = require_column(low, "hep_bin_column")
            high_present = require_column(high, "hep_bin_column")
            if low_present and high_present:
                for group_rows in groups.values():
                    previous: tuple[int, float] | None = None
                    for row_index, row in group_rows:
                        low_value = _finite_number(row.get(low))
                        high_value = _finite_number(row.get(high))
                        if low_value is None or high_value is None:
                            result.error("hep_bin_value", f"{path.name} row {row_index} has non-numeric bin edge")
                            continue
                        if not low_value < high_value:
                            result.error("hep_bin_order", f"{path.name} row {row_index} has bin_low >= bin_high")
                        if previous is not None and low_value < previous[1]:
                            result.error("hep_bin_overlap", f"{path.name} bins overlap or are unordered at rows {previous[0]} and {row_index}")
                        previous = (row_index, high_value)
        elif binning == "bin_centers":
            (center,) = axis_columns
            if require_column(center, "hep_bin_column"):
                for group_rows in groups.values():
                    previous_center: float | None = None
                    for row_index, row in group_rows:
                        center_value = _finite_number(row.get(center))
                        if center_value is None:
                            result.error("hep_bin_value", f"{path.name} row {row_index} has non-numeric bin center")
                            continue
                        if previous_center is not None and center_value <= previous_center:
                            result.error("hep_bin_order", f"{path.name} row {row_index}: bin centers must be strictly increasing within each group")
                        previous_center = center_value

        uncertainty_columns: list[str] = []
        for uncertainty in metadata.get("uncertainties") or []:
            if isinstance(uncertainty, dict):
                column = str(uncertainty.get("column") or "").strip()
                if column:
                    uncertainty_columns.append(column)
                    present = require_column(column, "hep_uncertainty_column")
                    if present:
                        for row_index, row in enumerate(rows, 1):
                            raw = str(row.get(column) or "").strip()
                            if not raw:
                                continue
                            number = _finite_number(raw)
                            if number is None or number < 0:
                                result.error(
                                    "hep_uncertainty_value",
                                    f"{path.name} row {row_index} has invalid uncertainty in {column!r}",
                                )

        if value_present:
            positive_required = metadata.get("positive_value_required_for_log_fit") is True
            for row_index, row in enumerate(rows, 1):
                raw = str(row.get(value_column) or "").strip()
                if not raw:
                    continue
                number = _finite_number(raw)
                if number is None:
                    result.error("hep_value_number", f"{path.name} row {row_index} has a non-finite value")
                elif positive_required and number <= 0:
                    result.error("hep_log_value", f"{path.name} row {row_index} is not positive for log fitting")

    def _validate_provenance(
        self,
        provenance: dict[str, Any],
        data_path: Path,
        absolute: Path,
        result: HepValidationResult,
    ) -> None:
        if provenance.get("schema_version") != HEP_PROVENANCE_SCHEMA:
            result.error("hep_provenance_schema", f"source provenance schema_version must be {HEP_PROVENANCE_SCHEMA!r}")
        records = provenance.get("files")
        if not isinstance(records, list):
            result.error("hep_provenance_files", "source provenance files must be a list")
            return
        match = None
        for record in records:
            if isinstance(record, dict) and _normalize_reference(record.get("path")) == data_path.as_posix():
                match = record
                break
        if match is None:
            result.error("hep_provenance_entry", f"source provenance has no entry for data/{data_path.as_posix()}")
            return
        digest = str(match.get("sha256") or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            result.error("hep_provenance_hash", f"source provenance hash is invalid for data/{data_path.as_posix()}")
        else:
            actual = hashlib.sha256(absolute.read_bytes()).hexdigest()
            if digest != actual:
                result.error("hep_provenance_hash_mismatch", f"source provenance hash does not match data/{data_path.as_posix()}")
        if not _required_text(match.get("source_url")):
            result.error("hep_provenance_source", f"source provenance source_url is missing for data/{data_path.as_posix()}")
        source_type = str(match.get("source_type") or "").strip()
        if source_type not in _ALLOWED_SOURCE_TYPES:
            result.error(
                "hep_provenance_source_type",
                f"source provenance source_type is invalid for data/{data_path.as_posix()}",
            )


CONTRACT = HepDataContract()
