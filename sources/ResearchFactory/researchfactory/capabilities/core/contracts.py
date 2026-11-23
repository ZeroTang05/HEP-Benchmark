"""Stable capability specification and artifact-profile contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from researchfactory.capabilities.core.catalog import CapabilityCatalog

TARGET_LEVELS = ("basic", "applied", "integrated", "advanced")


def _strings(value: Iterable[Any] | None, *, field_name: str) -> tuple[str, ...]:
    """Normalize a sequence while rejecting an ambiguous bare string."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of strings, not a string")
    out: list[str] = []
    for raw in value:
        item = str(raw).strip()
        if not item:
            raise ValueError(f"{field_name} cannot contain an empty value")
        if item in out:
            raise ValueError(f"{field_name} contains duplicate value {item!r}")
        out.append(item)
    return tuple(out)


@dataclass(frozen=True)
class CapabilitySpec:
    """The capabilities and target depth requested for one task."""

    primary: str
    secondary: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    target_level: str = "applied"

    def __post_init__(self) -> None:
        primary = str(self.primary or "").strip()
        if not primary:
            raise ValueError("CapabilitySpec requires exactly one non-empty primary")
        object.__setattr__(self, "primary", primary)
        for field_name in ("secondary", "prerequisites", "forbidden"):
            object.__setattr__(
                self,
                field_name,
                _strings(getattr(self, field_name), field_name=field_name),
            )
        if len(self.secondary) > 3:
            raise ValueError("CapabilitySpec allows at most three secondary capabilities")
        level = str(self.target_level or "").strip().lower()
        if level not in TARGET_LEVELS:
            raise ValueError(
                f"invalid target_level {level!r}; expected one of {', '.join(TARGET_LEVELS)}"
            )
        object.__setattr__(self, "target_level", level)

        selected = {primary, *self.secondary, *self.prerequisites}
        if primary in self.secondary:
            raise ValueError("primary capability cannot also be secondary")
        conflict = selected.intersection(self.forbidden)
        if conflict:
            raise ValueError(
                "forbidden capabilities conflict with selected capabilities: "
                + ", ".join(sorted(conflict))
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapabilitySpec":
        return cls(
            primary=str(data.get("primary") or ""),
            secondary=tuple(data.get("secondary") or ()),
            prerequisites=tuple(data.get("prerequisites") or ()),
            forbidden=tuple(data.get("forbidden") or ()),
            target_level=str(data.get("target_level") or "applied"),
        )

    def validate_against(self, catalog: CapabilityCatalog) -> None:
        refs = {self.primary, *self.secondary, *self.prerequisites, *self.forbidden}
        unknown = sorted(refs - catalog.nodes.keys())
        if unknown:
            raise ValueError(f"CapabilitySpec contains unknown capabilities: {', '.join(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "secondary": list(self.secondary),
            "prerequisites": list(self.prerequisites),
            "forbidden": list(self.forbidden),
            "target_level": self.target_level,
        }


@dataclass(frozen=True)
class ArtifactCapabilityProfile:
    """Closed-catalog classification for a paper, environment, or seed task."""

    artifact_id: str
    artifact_type: str
    source: str
    spec: CapabilitySpec
    evidence: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    classification_method: str = "rule"
    catalog_version: str = ""
    unmapped_candidates: tuple[str, ...] = ()
    classification_error: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        artifact_id = str(self.artifact_id or "").strip()
        if not artifact_id:
            raise ValueError("ArtifactCapabilityProfile.artifact_id must be non-empty")
        if not isinstance(self.spec, CapabilitySpec):
            if not isinstance(self.spec, Mapping):
                raise TypeError("ArtifactCapabilityProfile.spec must be a CapabilitySpec")
            object.__setattr__(self, "spec", CapabilitySpec.from_dict(self.spec))
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("ArtifactCapabilityProfile.confidence must be between 0 and 1")
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "artifact_type", str(self.artifact_type or "artifact").strip())
        object.__setattr__(self, "source", str(self.source or "").strip())
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "evidence", dict(self.evidence or {}))
        object.__setattr__(self, "context", dict(self.context or {}))
        object.__setattr__(
            self,
            "classification_error",
            {
                str(key): str(value)
                for key, value in dict(self.classification_error or {}).items()
            },
        )
        object.__setattr__(
            self,
            "unmapped_candidates",
            _strings(self.unmapped_candidates, field_name="unmapped_candidates"),
        )

    @property
    def primary(self) -> str:
        return self.spec.primary

    @property
    def secondary(self) -> tuple[str, ...]:
        return self.spec.secondary

    @property
    def target_level(self) -> str:
        return self.spec.target_level

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactCapabilityProfile":
        raw_spec = data.get("spec")
        spec_data = raw_spec if isinstance(raw_spec, Mapping) else data
        return cls(
            artifact_id=str(data.get("artifact_id") or ""),
            artifact_type=str(data.get("artifact_type") or "artifact"),
            source=str(data.get("source") or ""),
            spec=CapabilitySpec.from_dict(spec_data),
            evidence=dict(data.get("evidence") or {}),
            context=dict(data.get("context") or {}),
            confidence=float(data.get("confidence") or 0.0),
            classification_method=str(data.get("classification_method") or "rule"),
            catalog_version=str(data.get("catalog_version") or ""),
            unmapped_candidates=tuple(data.get("unmapped_candidates") or ()),
            classification_error=dict(data.get("classification_error") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize as a flat JSONL-friendly row used by matching/planning."""
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "source": self.source,
            **self.spec.to_dict(),
            "evidence": self.evidence,
            "context": self.context,
            "confidence": self.confidence,
            "classification_method": self.classification_method,
            "catalog_version": self.catalog_version,
            "unmapped_candidates": list(self.unmapped_candidates),
            "classification_error": self.classification_error,
        }
