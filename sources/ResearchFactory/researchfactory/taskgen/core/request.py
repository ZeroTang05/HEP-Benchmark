"""Capability-request loading and validation."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from researchfactory.taskgen.core.types import (
    CAPABILITY_LEVELS,
    CapabilityRequest,
    CapabilityRequestSlot,
)

_TASK_TYPE_ALIASES = {
    "rcb": "researchclawbench",
    "research_claw_bench": "researchclawbench",
}
_CONSTRUCTION_MODES = {"researchclawbench": ("reproduce", "analyze", "design")}
_DEFAULT_MODE = {"researchclawbench": "reproduce"}


@dataclass(frozen=True)
class _RequestDefaults:
    context: dict[str, Any]
    resources: dict[str, Any]
    target_level: str
    construction_mode: str

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "_RequestDefaults":
        defaults = dict(data.get("defaults") or {})
        return cls(
            context=dict(data.get("context") or defaults.get("context") or {}),
            resources=dict(
                data.get("resource_constraints")
                or data.get("resources")
                or defaults.get("resource_constraints")
                or defaults.get("resources")
                or {}
            ),
            target_level=str(
                data.get("target_level")
                or data.get("level")
                or defaults.get("target_level")
                or defaults.get("level")
                or "applied"
            ),
            construction_mode=str(
                data.get("construction_mode")
                or data.get("mode")
                or defaults.get("construction_mode")
                or defaults.get("mode")
                or ""
            ),
        )


def _normalize_task_type(value: str) -> str:
    raw = str(value or "").strip().lower()
    task_type = _TASK_TYPE_ALIASES.get(raw, raw)
    if task_type not in _CONSTRUCTION_MODES:
        supported = ", ".join(sorted(_CONSTRUCTION_MODES))
        raise ValueError(f"unsupported task_type {value!r}; expected one of: {supported}")
    return task_type


def _plain_data(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"capability request not found: {path}")
    data = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    if not isinstance(data, dict):
        raise ValueError("capability request document must contain a mapping")
    return data


def _raw_slots(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = next(
        (data[key] for key in ("slots", "requests", "capabilities") if data.get(key) is not None),
        None,
    )
    if raw is None:
        raw = [data] if data.get("primary") or data.get("capability") else []
    if isinstance(raw, Mapping):
        rows: list[dict[str, Any]] = []
        for primary, value in raw.items():
            row = dict(value) if isinstance(value, Mapping) else {"count": value}
            row.setdefault("primary", primary)
            rows.append(row)
        return rows
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("capability request slots must be a list or mapping")
    rows: list[dict[str, Any]] = []
    for value in raw:
        if isinstance(value, str):
            rows.append({"primary": value})
        elif isinstance(value, Mapping):
            rows.append(dict(value))
        else:
            raise ValueError("each capability request slot must be a mapping or capability id")
    return rows


def _slot_from_row(row: dict[str, Any], defaults: _RequestDefaults) -> CapabilityRequestSlot:
    merged = dict(row)
    merged["context"] = {**defaults.context, **dict(row.get("context") or {})}
    merged["resource_constraints"] = {
        **defaults.resources,
        **dict(row.get("resource_constraints") or row.get("resources") or {}),
    }
    if not (row.get("target_level") or row.get("level")):
        merged["target_level"] = defaults.target_level
    if not (row.get("construction_mode") or row.get("mode")):
        merged["construction_mode"] = defaults.construction_mode
    return CapabilityRequestSlot.from_dict(merged)


def _from_data(data: dict[str, Any], task_type: str | None) -> CapabilityRequest:
    document_task_type = str(data.get("task_type") or "").strip()
    if task_type and document_task_type:
        caller_type = _normalize_task_type(task_type)
        if caller_type != _normalize_task_type(document_task_type):
            raise ValueError(
                f"task_type mismatch: request has {document_task_type!r}, caller has {task_type!r}"
            )
    normalized_type = _normalize_task_type(task_type or document_task_type)
    defaults = _RequestDefaults.from_data(data)
    return CapabilityRequest(
        task_type=normalized_type,
        slots=tuple(_slot_from_row(row, defaults) for row in _raw_slots(data)),
        catalog_version=str(data.get("catalog_version") or "").strip(),
        request_id=str(data.get("request_id") or data.get("id") or "").strip(),
    )


def _from_request(request: CapabilityRequest, task_type: str | None) -> CapabilityRequest:
    normalized_type = _normalize_task_type(task_type or request.task_type)
    if task_type and request.task_type and _normalize_task_type(request.task_type) != normalized_type:
        raise ValueError(
            f"task_type mismatch: request has {request.task_type!r}, caller has {task_type!r}"
        )
    return replace(request, task_type=normalized_type)


def _validate(request: CapabilityRequest) -> CapabilityRequest:
    if not request.slots:
        raise ValueError("capability request contains no slots")
    allowed_modes = _CONSTRUCTION_MODES[request.task_type]
    validated: list[CapabilityRequestSlot] = []
    for index, slot in enumerate(request.slots, start=1):
        if not slot.primary:
            raise ValueError(f"capability request slot {index} has no primary capability")
        if slot.count < 1:
            raise ValueError(f"capability request slot {index} count must be positive")
        if slot.target_level not in CAPABILITY_LEVELS:
            allowed = ", ".join(CAPABILITY_LEVELS)
            raise ValueError(
                f"capability request slot {index} has invalid target_level "
                f"{slot.target_level!r}; expected one of: {allowed}"
            )
        mode = slot.construction_mode or _DEFAULT_MODE[request.task_type]
        if mode not in allowed_modes:
            allowed = ", ".join(allowed_modes)
            raise ValueError(
                f"capability request slot {index} has invalid construction_mode "
                f"{mode!r} for {request.task_type}; expected one of: {allowed}"
            )
        validated.append(replace(slot, construction_mode=mode))
    return replace(request, slots=tuple(validated))


def _with_request_id(request: CapabilityRequest) -> CapabilityRequest:
    if request.request_id:
        return request
    identity = request.to_dict()
    identity["request_id"] = ""
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    request_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return replace(request, request_id=f"request_{request_id}")


def load_capability_request(
    source: CapabilityRequest | str | Path | Mapping[str, Any],
    *,
    task_type: str | None = None,
) -> CapabilityRequest:
    """Load, normalize, and validate a capability request."""
    request = (
        _from_request(source, task_type)
        if isinstance(source, CapabilityRequest)
        else _from_data(_plain_data(source), task_type)
    )
    return _with_request_id(_validate(request))
