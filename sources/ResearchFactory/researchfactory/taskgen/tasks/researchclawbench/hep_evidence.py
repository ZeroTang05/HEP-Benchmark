"""HEP 研究证据包的声明、校验和提示渲染。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


_ROLES = frozenset({
    "primary_paper",
    "supplementary_material",
    "method_reference",
    "detector_context",
    "comparison_study",
})


@dataclass(frozen=True)
class HepEvidenceSource:
    """证据包中的一个论文或补充材料来源。"""

    source_id: str
    role: str
    title: str
    identifier: str
    url: str


def normalize_evidence_sources(target: Mapping[str, Any]) -> tuple[HepEvidenceSource, ...]:
    """读取并严格校验证据来源声明。"""
    raw = target.get("evidence_sources")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("evidence_sources must be a list")
    sources: list[HepEvidenceSource] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"evidence_sources[{index}] must be an object")
        source_id = str(item.get("source_id") or item.get("id") or "").strip()
        role = str(item.get("role") or "").strip()
        title = str(item.get("title") or "").strip()
        identifier = str(item.get("doi") or item.get("arxiv_id") or item.get("identifier") or "").strip()
        url = str(item.get("url") or item.get("source_url") or "").strip()
        if not source_id or source_id in seen:
            raise ValueError(f"evidence_sources[{index}] has a missing or duplicate source_id")
        if role not in _ROLES:
            raise ValueError(f"evidence_sources[{index}] has unsupported role: {role!r}")
        if not title or not identifier or not url:
            raise ValueError(f"evidence_sources[{index}] requires title, identifier, and url")
        seen.add(source_id)
        sources.append(HepEvidenceSource(source_id, role, title, identifier, url))
    primary_count = sum(source.role == "primary_paper" for source in sources)
    if primary_count > 1:
        raise ValueError("evidence_sources may contain at most one primary_paper")
    return tuple(sources)


def render_evidence_sources(sources: tuple[HepEvidenceSource, ...]) -> str:
    """将证据包范围写入 Agent 入口提示。"""
    if not sources:
        return """Research evidence scope:
  - Use the preselected Target Study as the primary paper.
  - Related work may support the method, but every source must remain separately identifiable.
"""
    lines = [
        "Research evidence scope:",
        "  - The preselected Target Study remains the primary scientific subject.",
        "  - The following additional sources are allowed as separately identified evidence:",
    ]
    for source in sources:
        lines.append(
            f"    - {source.source_id}: role={source.role}; title={source.title}; "
            f"identifier={source.identifier}; url={source.url}"
        )
    lines.extend([
        "  - Keep measurements, units, bins, uncertainties, and provenance separate by source.",
        "  - Use an additional source only for its declared role; do not silently replace the Target Study.",
        "  - Record every used source in task/data provenance or task/related_work/.",
    ])
    return "\n".join(lines) + "\n"
