"""读取两层评分清单，并用小项的权重计算各组得分。"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "researchfactory.hep_checklist.v2"
CAPABILITY_SCHEMA = "researchfactory.hep_checklist.v3"
CAPABILITIES = (
    "knowledge_understanding", "task_planning", "hypothesis_generation",
    "analysis_design", "tool_use", "model_invocation", "physical_consistency", "data_analysis",
)
PACKAGE_METADATA = "package_contract.json"
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "PLACEHOLDER_" in value:
        raise ValueError(f"{label} must be non-empty text")
    return value


def _id(value: Any, seen: set[str]) -> str:
    name = _text(value, "id")
    if not _IDENTIFIER.fullmatch(name) or name in seen:
        raise ValueError(f"invalid or duplicate rubric id: {name!r}")
    seen.add(name)
    return name


def _weight(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not 0 < value <= 1:
        raise ValueError(f"{label}.weight must be a number greater than 0 and at most 1")
    return float(value)


def _rows(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{label} must be a non-empty array of objects")
    return value


def normalize_output_path(value: Any) -> str | None:
    """为评分规格和清单生成一致的输出路径。"""
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] == "task":
        return None
    return path.as_posix()


def validate_rubric(value: Any, evaluator: dict[str, Any]) -> list[dict[str, Any]]:
    """小项占整题的比例，组权重等于组内小项之和。"""
    if not isinstance(value, dict) or value.get("schema_version") not in (SCHEMA, CAPABILITY_SCHEMA):
        raise ValueError(f"checklist schema_version must be {SCHEMA} or {CAPABILITY_SCHEMA}")
    capability_rubric = value["schema_version"] == CAPABILITY_SCHEMA
    groups = _rows(value.get("groups"), "groups")
    numeric = [f"numeric:{check['name']}" for check in evaluator.get("numeric_checks", [])]
    if (not numeric and not capability_rubric) or len(numeric) != len(set(numeric)):
        raise ValueError("the evaluator must have uniquely named numeric checks")
    outputs = {f"required_output:{normalize_output_path(output['path'])}"
               for output in evaluator["required_outputs"]}
    allowed = set(numeric) | set(outputs)
    seen: set[str] = set()
    automatic: Counter[str] = Counter()
    group_weights = []
    for group in groups:
        name = _id(group.get("id"), seen)
        _text(group.get("title"), f"{name}.title")
        group_weights.append(_weight(group.get("weight"), name))
        items = _rows(group.get("items"), f"{name}.items")
        item_weights = []
        for item in items:
            item_id = _id(item.get("id"), seen)
            _text(item.get("criterion"), f"{item_id}.criterion")
            item_weights.append(_weight(item.get("weight"), item_id))
            check = _text(item.get("check"), f"{item_id}.check")
            if "capability" in item and (not capability_rubric or item["capability"] not in CAPABILITIES):
                raise ValueError(f"{item_id}.capability must identify a benchmark capability")
            if "capability" in item and check.startswith("required_output:"):
                raise ValueError(f"{item_id}: file presence alone does not assess a capability")
            trace = item.get("evidence") == "trace"
            if "evidence" in item and (not trace or check != "judge" or "capability" not in item):
                raise ValueError(f'{item_id}: "evidence": "trace" requires "check": "judge" and a capability')
            if check == "judge":
                if (not trace or "output" in item) and f"required_output:{item.get('output')}" not in outputs:
                    raise ValueError(f"{item_id}.output must identify one declared Solver output")
            elif check in allowed:
                automatic[check] += 1
            else:
                raise ValueError(f"{item_id} points to an unknown check: {check}")
        if not math.isclose(math.fsum(item_weights), group_weights[-1], abs_tol=1e-8):
            raise ValueError(f"{name} child weights must sum to the group weight")
    if not math.isclose(math.fsum(group_weights), 1.0, abs_tol=1e-8):
        raise ValueError("group weights must sum to 1.0")
    invalid = sorted({name for name in numeric if automatic[name] != 1} | {
        name for name, count in automatic.items() if count > 1
    })
    if invalid:
        raise ValueError(f"each numeric check must be scored exactly once and other checks at most once: {', '.join(invalid)}")
    if capability_rubric and not rubric_capabilities(groups):
        raise ValueError("a capability rubric must contain scored capability items")
    if not numeric and not any(
        item["check"] == "judge" and "capability" in item for group in groups for item in group["items"]
    ):
        raise ValueError("a rubric without numeric checks needs capability evidence assessed by Judge")
    return groups


def rubric_capabilities(groups: list[dict[str, Any]]) -> list[str]:
    """从实际评分小项提取能力，单题同一能力只计一次。"""
    present = {item.get("capability") for group in groups for item in group["items"]}
    return [name for name in CAPABILITIES if name in present]


def task_capabilities(root: Path) -> list[str]:
    """读取已校验题包的能力标注。旧题包没有能力标注。"""
    if not rubric_enabled(root):
        return []
    rubric = json.loads(rubric_path(root).read_text(encoding="utf-8"))
    return rubric_capabilities(rubric["groups"]) if rubric["schema_version"] == CAPABILITY_SCHEMA else []


def capability_scores(groups: list[dict[str, Any]]) -> dict[str, float]:
    """按本题内同一能力的评分小项权重归一化。"""
    items_by_capability: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        for item in group["items"]:
            if "capability" in item:
                items_by_capability.setdefault(item["capability"], []).append(item)
    return {
        name: math.fsum(item["earned"] for item in items) / math.fsum(item["weight"] for item in items)
        for name, items in items_by_capability.items()
    }


def scored_groups(groups: list[dict[str, Any]], outcomes: dict[str, bool]) -> list[dict[str, Any]]:
    """小项通过时给足权重，再汇总每组和整题。"""
    result = []
    for group in groups:
        items = [{**item, "passed": outcomes[item["id"]], "earned": item["weight"] if outcomes[item["id"]] else 0.0}
                 for item in group["items"]]
        result.append({**group, "items": items, "earned": math.fsum(item["earned"] for item in items)})
    return result


def rubric_path(root: Path) -> Path:
    return root / "target_study/checklist.json"


def rubric_enabled(root: Path) -> bool:
    return (root / "target_study" / PACKAGE_METADATA).is_file()
