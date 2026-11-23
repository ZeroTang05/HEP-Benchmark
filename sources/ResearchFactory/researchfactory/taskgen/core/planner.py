"""Deterministic artifact-to-capability planning for task generation.

The planner is intentionally independent from task-package construction.  It
joins an Artifact Pool to a capability index, expands requested quotas into
slots, and performs deterministic one-to-one matching.  Unmatched slots remain
in the output with an explicit reason; a requested primary capability is never
silently substituted.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any

from researchfactory.taskgen.core.matching import (
    constraints_match as _constraints_match,
    value_matches as _value_matches,
)
from researchfactory.taskgen.core.request import load_capability_request
from researchfactory.taskgen.core.types import (
    CapabilityRequest,
    CapabilityRequestSlot,
    TaskgenPlan,
)
from researchfactory.utils.storage import read_jsonl, write_jsonl


@dataclass(frozen=True)
class _ProfileRecord:
    artifact_id: str
    artifact: dict[str, Any] | None
    profile: dict[str, Any]


@dataclass(frozen=True)
class _ExpandedSlot:
    request_index: int
    ordinal: int
    slot_id: str
    request: CapabilityRequestSlot


def _canonical_hash(value: Any, *, length: int = 16) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _strings(value: Any, *, limit: int | None = None) -> list[str]:
    if value is None:
        result: list[str] = []
    elif isinstance(value, str):
        result = [value.strip()] if value.strip() else []
    elif isinstance(value, Sequence):
        result = [str(item).strip() for item in value if str(item).strip()]
    else:
        result = [str(value).strip()] if str(value).strip() else []
    return result[:limit] if limit is not None else result


def artifact_id_from_row(row: Mapping[str, Any]) -> str:
    """Return the stable join key used by Artifact Pool and capability index rows."""
    for key in ("artifact_id", "paper_id", "identity", "environment_id", "target_id", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return f"artifact:{_canonical_hash(dict(row))}"


def _normalize_profile(data: Mapping[str, Any], artifact_id: str) -> dict[str, Any]:
    nested = data.get("spec") or data.get("capability_spec") or {}
    if not isinstance(nested, Mapping):
        nested = {}

    primary = str(data.get("primary") or nested.get("primary") or "").strip()
    secondary = _strings(data.get("secondary") or nested.get("secondary"), limit=3)
    prerequisites = _strings(data.get("prerequisites") or nested.get("prerequisites"))
    forbidden = _strings(data.get("forbidden") or nested.get("forbidden"))
    target_level = str(
        data.get("target_level") or nested.get("target_level") or "applied"
    ).strip()
    context = data.get("context") or nested.get("context") or {}
    resources = data.get("resources") or data.get("resource_constraints") or {}
    return {
        "artifact_id": artifact_id,
        "primary": primary,
        "secondary": secondary,
        "prerequisites": prerequisites,
        "forbidden": forbidden,
        "target_level": target_level,
        "context": dict(context) if isinstance(context, Mapping) else {},
        "resources": dict(resources) if isinstance(resources, Mapping) else {},
        "catalog_version": str(data.get("catalog_version") or "").strip(),
    }


def _profile_from_artifact(row: Mapping[str, Any], artifact_id: str) -> dict[str, Any] | None:
    inline = row.get("capability_profile")
    if isinstance(inline, Mapping):
        profile = _normalize_profile(inline, artifact_id)
        if profile["primary"]:
            return profile

    if row.get("primary"):
        profile = _normalize_profile(row, artifact_id)
        if profile["primary"]:
            return profile

    # Compatibility is confined to the planner boundary.  Plans and consumers
    # use explicit primary/secondary fields and never need these legacy names.
    dimensions = _strings(row.get("capability_dimensions") or row.get("dimensions"))
    assigned = str(row.get("assigned_dimension") or "").strip()
    primary = assigned or (dimensions[0] if dimensions else "")
    if not primary:
        return None
    secondary = [value for value in dimensions if value != primary][:3]
    context = dict(row.get("context") or {}) if isinstance(row.get("context"), Mapping) else {}
    return {
        "artifact_id": artifact_id,
        "primary": primary,
        "secondary": secondary,
        "prerequisites": [],
        "forbidden": [],
        "target_level": str(row.get("target_level") or row.get("level") or "applied"),
        "context": context,
        "resources": dict(row.get("resources") or row.get("resource_constraints") or {}),
        "catalog_version": str(row.get("catalog_version") or ""),
    }


def _context_for(record: _ProfileRecord) -> dict[str, Any]:
    artifact = record.artifact or {}
    context: dict[str, Any] = {}
    raw = artifact.get("context")
    if isinstance(raw, Mapping):
        context.update(raw)
    for key in (
        "domain",
        "format",
        "file_format",
        "input_modalities",
        "modality",
        "delivery",
        "deliverable",
        "difficulty",
        "source",
        "kind",
        "category",
        "tags",
    ):
        if key in artifact and key not in context:
            context[key] = artifact[key]
    if "domain" not in context and artifact.get("assigned_domain"):
        context["domain"] = artifact["assigned_domain"]
    context.update(record.profile.get("context") or {})
    return context


def _resources_for(record: _ProfileRecord, context: Mapping[str, Any]) -> dict[str, Any]:
    # Including flat context keys permits adapters to represent requirements as
    # either context.resources or flat flags such as ``gpu_required``.
    resources = dict(context)
    artifact = record.artifact or {}
    for container in (artifact, context, record.profile):
        for key in ("resources", "resource_constraints", "resource_requirements"):
            raw = container.get(key) if isinstance(container, Mapping) else None
            if isinstance(raw, Mapping):
                resources.update(raw)
    return resources


def _build_records(
    artifact_rows: Iterable[Mapping[str, Any]],
    capability_rows: Iterable[Mapping[str, Any]],
) -> list[_ProfileRecord]:
    artifacts: dict[str, dict[str, Any]] = {}
    for source in artifact_rows:
        row = dict(source)
        artifact_id = artifact_id_from_row(row)
        if artifact_id in artifacts:
            raise ValueError(f"duplicate artifact id in pool: {artifact_id}")
        artifacts[artifact_id] = row

    profiles: dict[str, dict[str, Any]] = {}
    for source in capability_rows:
        raw = dict(source)
        artifact_id = str(raw.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("capability index row is missing artifact_id")
        if artifact_id in profiles:
            raise ValueError(f"duplicate artifact id in capability index: {artifact_id}")
        profiles[artifact_id] = _normalize_profile(raw, artifact_id)

    records: list[_ProfileRecord] = []
    for artifact_id, profile in profiles.items():
        records.append(_ProfileRecord(artifact_id, artifacts.get(artifact_id), profile))
    for artifact_id, artifact in artifacts.items():
        if artifact_id in profiles:
            continue
        profile = _profile_from_artifact(artifact, artifact_id)
        if profile is not None:
            records.append(_ProfileRecord(artifact_id, artifact, profile))
    return sorted(records, key=lambda record: record.artifact_id)


def _expand_slots(request: CapabilityRequest) -> list[_ExpandedSlot]:
    expanded: list[_ExpandedSlot] = []
    for request_index, slot in enumerate(request.slots):
        for ordinal in range(slot.count):
            label = slot.name or "slot"
            slot_id = f"{label}:r{request_index + 1:03d}:s{ordinal + 1:03d}"
            expanded.append(_ExpandedSlot(request_index, ordinal, slot_id, slot))
    return expanded


def _eligible_records(
    records: list[_ProfileRecord],
    slot: CapabilityRequestSlot,
    catalog_version: str,
) -> tuple[list[int], dict[str, int]]:
    stats = {
        "profiles": len(records),
        "primary_matches": 0,
        "catalog_matches": 0,
        "level_matches": 0,
        "context_matches": 0,
        "resource_matches": 0,
        "artifacts_in_pool": 0,
    }
    eligible: list[int] = []
    for index, record in enumerate(records):
        profile = record.profile
        if profile.get("primary") != slot.primary:
            continue
        stats["primary_matches"] += 1
        profile_version = str(profile.get("catalog_version") or "")
        if catalog_version and profile_version and profile_version != catalog_version:
            continue
        stats["catalog_matches"] += 1
        if profile.get("target_level") != slot.target_level:
            continue
        stats["level_matches"] += 1
        context = _context_for(record)
        if not _constraints_match(context, slot.context):
            continue
        stats["context_matches"] += 1
        resources = _resources_for(record, context)
        if not _constraints_match(resources, slot.resource_constraints):
            continue
        stats["resource_matches"] += 1
        if record.artifact is None:
            continue
        stats["artifacts_in_pool"] += 1
        eligible.append(index)
    return eligible, stats


def _unsatisfied_reason(stats: Mapping[str, int]) -> str:
    stages = (
        ("primary_matches", "no_primary_match"),
        ("catalog_matches", "catalog_version_mismatch"),
        ("level_matches", "target_level_mismatch"),
        ("context_matches", "context_mismatch"),
        ("resource_matches", "resource_constraints_mismatch"),
        ("artifacts_in_pool", "artifact_not_in_pool"),
    )
    for key, reason in stages:
        if not stats.get(key, 0):
            return reason
    return "quota_exhausted"


def _plan_id(
    request: CapabilityRequest,
    expanded: _ExpandedSlot,
    *,
    artifact_id: str,
    status: str,
) -> str:
    identity = {
        "request_id": request.request_id,
        "task_type": request.task_type,
        "catalog_version": request.catalog_version,
        "slot_id": expanded.slot_id,
        "primary": expanded.request.primary,
        "target_level": expanded.request.target_level,
        "construction_mode": expanded.request.construction_mode,
        "artifact_id": artifact_id,
        "status": status,
    }
    return f"plan_{_canonical_hash(identity)}"


def plan_taskgen(
    artifact_rows: Iterable[Mapping[str, Any]],
    capability_rows: Iterable[Mapping[str, Any]],
    request: CapabilityRequest | str | Path | Mapping[str, Any],
    *,
    task_type: str | None = None,
) -> list[TaskgenPlan]:
    """Create one deterministic plan row per requested quota slot.

    Artifacts are used at most once.  A deterministic augmenting-path matcher
    avoids the avoidable quota failures that a simple greedy first-fit would
    cause when broad and highly constrained slots overlap.
    """
    normalized_request = load_capability_request(request, task_type=task_type)
    records = _build_records(artifact_rows, capability_rows)

    versions = sorted(
        {str(record.profile.get("catalog_version")) for record in records
         if record.profile.get("catalog_version")}
    )
    catalog_version = normalized_request.catalog_version
    if not catalog_version and len(versions) > 1:
        raise ValueError(
            "capability index mixes catalog versions; set one catalog_version "
            "in the request or rebuild the index"
        )
    if not catalog_version:
        if versions:
            catalog_version = versions[0]
        else:
            # Legacy pool conversion still produces a fully traceable new plan.
            from researchfactory.capabilities import CapabilityCatalog

            catalog_version = CapabilityCatalog.load().version
        normalized_request = replace(normalized_request, catalog_version=catalog_version)

    expanded = _expand_slots(normalized_request)
    eligibility: list[list[int]] = []
    diagnostics: list[dict[str, int]] = []
    for item in expanded:
        eligible, stats = _eligible_records(records, item.request, catalog_version)
        eligibility.append(eligible)
        diagnostics.append(stats)

    artifact_to_slot: dict[int, int] = {}
    slot_to_artifact: list[int | None] = [None] * len(expanded)

    def assign(slot_index: int, seen: set[int]) -> bool:
        for record_index in eligibility[slot_index]:
            if record_index in seen:
                continue
            seen.add(record_index)
            previous = artifact_to_slot.get(record_index)
            if previous is None or assign(previous, seen):
                artifact_to_slot[record_index] = slot_index
                slot_to_artifact[slot_index] = record_index
                return True
        return False

    # Process in reverse so the final maximum matching also gives earlier plan
    # slots the lexicographically earliest feasible artifact when alternatives
    # are otherwise equivalent.
    for slot_index in reversed(range(len(expanded))):
        assign(slot_index, set())

    plans: list[TaskgenPlan] = []
    for slot_index, item in enumerate(expanded):
        slot = item.request
        record_index = slot_to_artifact[slot_index]
        stats = diagnostics[slot_index]
        detail: dict[str, Any] = dict(stats)
        if slot.name:
            detail["request_slot_name"] = slot.name

        if record_index is None:
            reason = _unsatisfied_reason(stats)
            plans.append(TaskgenPlan(
                plan_id=_plan_id(
                    normalized_request, item, artifact_id="", status="unsatisfied"
                ),
                request_id=normalized_request.request_id,
                slot_id=item.slot_id,
                status="unsatisfied",
                task_type=normalized_request.task_type,
                catalog_version=catalog_version,
                primary=slot.primary,
                target_level=slot.target_level,
                construction_mode=slot.construction_mode,
                requested_context=dict(slot.context),
                resource_constraints=dict(slot.resource_constraints),
                unsatisfied_reason=reason,
                diagnostics=detail,
            ))
            continue

        record = records[record_index]
        profile = record.profile
        context = _context_for(record)
        row_catalog_version = (
            catalog_version or str(profile.get("catalog_version") or "")
        )
        plans.append(TaskgenPlan(
            plan_id=_plan_id(
                normalized_request, item, artifact_id=record.artifact_id, status="matched"
            ),
            request_id=normalized_request.request_id,
            slot_id=item.slot_id,
            status="matched",
            task_type=normalized_request.task_type,
            catalog_version=row_catalog_version,
            artifact_id=record.artifact_id,
            primary=slot.primary,
            secondary=tuple(profile.get("secondary") or ()),
            prerequisites=tuple(profile.get("prerequisites") or ()),
            forbidden=tuple(profile.get("forbidden") or ()),
            target_level=slot.target_level,
            construction_mode=slot.construction_mode,
            requested_context=dict(slot.context),
            resource_constraints=dict(slot.resource_constraints),
            capability_context=context,
            target=dict(record.artifact or {}),
            diagnostics=detail,
        ))
    return plans


def write_taskgen_plan(path: str | Path, plans: Iterable[TaskgenPlan]) -> int:
    """Serialize plan contracts as JSONL."""
    return write_jsonl(path, (plan.to_dict() for plan in plans))


def read_taskgen_plan(path: str | Path) -> list[TaskgenPlan]:
    """Load plan contracts from JSONL."""
    return [TaskgenPlan.from_dict(row) for row in read_jsonl(path)]


def plan_taskgen_files(
    artifact_pool_path: str | Path,
    capability_index_path: str | Path,
    request_path: str | Path,
    *,
    output_path: str | Path | None = None,
    task_type: str | None = None,
) -> list[TaskgenPlan]:
    """File-oriented convenience wrapper used by the CLI integration."""
    plans = plan_taskgen(
        read_jsonl(artifact_pool_path),
        read_jsonl(capability_index_path),
        request_path,
        task_type=task_type,
    )
    if output_path is not None:
        write_taskgen_plan(output_path, plans)
    return plans
