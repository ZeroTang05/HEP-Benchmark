"""Shared, benchmark-neutral rendering of capability targets and evidence."""
from __future__ import annotations

import json
from typing import Any, Mapping

from researchfactory.capabilities.core.catalog import CapabilityCatalog
from researchfactory.capabilities.core.contracts import (
    ArtifactCapabilityProfile,
    CapabilitySpec,
)

_SAFE_CONTEXT_KEYS = (
    "discipline",
    "domain",
    "difficulty",
    "data_formats",
    "file_formats",
    "input_modalities",
    "resources",
    "deliverables",
)
_SAFE_FILE_KEYS = (
    "path",
    "kind",
    "type",
    "description",
    "directives",
    "signals",
    "test_names",
    "imports",
    "commands",
    "base_images",
    "services",
)


def _spec_from(value: CapabilitySpec | ArtifactCapabilityProfile | Mapping[str, Any]) -> CapabilitySpec:
    if isinstance(value, ArtifactCapabilityProfile):
        return value.spec
    if isinstance(value, CapabilitySpec):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("capability brief requires a CapabilitySpec, profile, or mapping")
    nested = value.get("spec")
    return CapabilitySpec.from_dict(nested if isinstance(nested, Mapping) else value)


def _describe(node_id: str, catalog: CapabilityCatalog | None) -> str:
    if catalog is None or node_id not in catalog:
        return node_id
    node = catalog.get(node_id)
    suffix = f" — {node.description}" if node.description else ""
    return f"{node.id} ({node.name}){suffix}"


def render_capability_brief(
    value: CapabilitySpec | ArtifactCapabilityProfile | Mapping[str, Any],
    construction_mode: str = "",
    *,
    catalog: CapabilityCatalog | None = None,
) -> str:
    """Render the dynamic capability contract shared by all task types.

    Benchmark layout, evaluator, and hidden/public protocol deliberately do not
    appear here; task types append those format-specific instructions themselves.
    """
    spec = _spec_from(value)
    if catalog is None:
        # TaskType call sites should get useful names/descriptions without
        # needing to know where the versioned catalog lives.  Unknown legacy
        # labels still render as-is through ``_describe``.
        catalog = CapabilityCatalog.load()
    lines = [
        "Capability target:",
        f"- Primary: {_describe(spec.primary, catalog)}",
        f"- Target level: {spec.target_level}",
    ]
    if spec.secondary:
        lines.append(
            "- Secondary: " + "; ".join(_describe(item, catalog) for item in spec.secondary)
        )
    if spec.prerequisites:
        lines.append(
            "- Prerequisites: "
            + "; ".join(_describe(item, catalog) for item in spec.prerequisites)
        )
    if spec.forbidden:
        lines.append(
            "- Forbidden: " + "; ".join(_describe(item, catalog) for item in spec.forbidden)
        )
    mode = str(construction_mode or "").strip()
    if mode:
        lines.append(f"- Construction mode: {mode}")
    return "\n".join(lines)


def render_evidence_for_classification(
    evidence: Mapping[str, Any], *, max_chars: int = 12000
) -> str:
    """Render only allowlisted public/normalized evidence for classification.

    In particular, arbitrary adapter keys and RCB evaluation/checklist metadata
    cannot flow into an LLM prompt through this function.
    """
    public_text = evidence.get("public_text") or []
    if isinstance(public_text, str):
        public_text = [public_text]
    public_text = [str(item).strip() for item in public_text if str(item).strip()]

    files: list[dict[str, Any]] = []
    for raw in evidence.get("files") or []:
        if not isinstance(raw, Mapping):
            continue
        item = {key: raw[key] for key in _SAFE_FILE_KEYS if key in raw and raw[key] not in (None, "", [], {})}
        if item:
            files.append(item)

    raw_context = evidence.get("context") or {}
    context = {}
    if isinstance(raw_context, Mapping):
        context = {
            key: raw_context[key]
            for key in _SAFE_CONTEXT_KEYS
            if key in raw_context and raw_context[key] not in (None, "", [], {})
        }
        graph = raw_context.get("compose_graph")
        services = graph.get("services") if isinstance(graph, Mapping) else None
        if isinstance(services, Mapping):
            dependencies: list[list[str]] = []
            images: list[str] = []
            for service_name, raw_service in services.items():
                service = raw_service if isinstance(raw_service, Mapping) else {}
                dependencies.extend(
                    [str(service_name), str(dependency)]
                    for dependency in service.get("depends_on") or []
                )
                image = str(service.get("image") or "").strip()
                if image and image not in images:
                    images.append(image)
            topology = {
                "service_names": sorted(str(name) for name in services),
                "dependency_edges": dependencies,
                "images": sorted(images),
            }
            if len(services) > 1:
                topology["multi_service"] = True
            context["compose_topology"] = topology
    weak_priors = evidence.get("weak_priors") or []
    if isinstance(weak_priors, str):
        weak_priors = [weak_priors]

    payload = {
        "artifact_id": str(evidence.get("artifact_id") or ""),
        "source": str(evidence.get("source") or ""),
        "public_text": public_text,
        "files": files,
        "context": context,
        "weak_priors": [str(item) for item in weak_priors if str(item).strip()],
    }
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    return rendered[:max_chars]
