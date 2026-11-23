"""Versioned closed capability catalog and DAG validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from researchfactory.utils.storage import atomic_write_text

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "catalog.json"
_CAPABILITY_ID_RE = re.compile(
    r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$"
)


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
class CapabilityNode:
    """One versioned node in the capability prerequisite DAG."""

    id: str
    name: str
    description: str = ""
    prerequisites: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        node_id = str(self.id).strip()
        if not _CAPABILITY_ID_RE.fullmatch(node_id):
            raise ValueError(
                f"capability id {node_id!r} must be a stable lowercase dotted id"
            )
        name = str(self.name).strip()
        if not name:
            raise ValueError(f"capability {node_id!r} must have a non-empty name")
        object.__setattr__(self, "id", node_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(
            self,
            "prerequisites",
            _strings(self.prerequisites, field_name=f"{node_id}.prerequisites"),
        )
        object.__setattr__(
            self,
            "aliases",
            _strings(self.aliases, field_name=f"{node_id}.aliases"),
        )
        object.__setattr__(
            self,
            "keywords",
            _strings(self.keywords, field_name=f"{node_id}.keywords"),
        )
        if node_id in self.prerequisites:
            raise ValueError(f"capability {node_id!r} cannot reference itself")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapabilityNode":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            prerequisites=tuple(data.get("prerequisites") or ()),
            aliases=tuple(data.get("aliases") or ()),
            keywords=tuple(data.get("keywords") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prerequisites": list(self.prerequisites),
            "aliases": list(self.aliases),
            "keywords": list(self.keywords),
        }


@dataclass
class CapabilityCatalog:
    """A closed, versioned capability catalog with validated DAG edges."""

    version: str
    nodes: dict[str, CapabilityNode]
    schema_version: int = 1

    def __post_init__(self) -> None:
        self.version = str(self.version).strip()
        if not self.version:
            raise ValueError("capability catalog version must be non-empty")
        self.schema_version = int(self.schema_version)
        if self.schema_version < 1:
            raise ValueError("capability catalog schema_version must be >= 1")
        normalized: dict[str, CapabilityNode] = {}
        for key, raw in self.nodes.items():
            node = raw if isinstance(raw, CapabilityNode) else CapabilityNode.from_dict(raw)
            if str(key) != node.id:
                raise ValueError(
                    f"catalog node key {key!r} does not match node id {node.id!r}"
                )
            if node.id in normalized:
                raise ValueError(f"duplicate capability id {node.id!r}")
            normalized[node.id] = node
        if not normalized:
            raise ValueError("capability catalog must contain at least one node")
        self.nodes = normalized
        self.validate()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapabilityCatalog":
        raw_nodes = data.get("nodes")
        if isinstance(raw_nodes, Mapping):
            items: list[tuple[str, Any]] = []
            for node_id, raw in raw_nodes.items():
                if not isinstance(raw, Mapping):
                    raise TypeError(f"catalog node {node_id!r} must be an object")
                node_data = dict(raw)
                node_data.setdefault("id", node_id)
                items.append((str(node_id), node_data))
        elif isinstance(raw_nodes, list):
            items = []
            seen: set[str] = set()
            for index, raw in enumerate(raw_nodes):
                if not isinstance(raw, Mapping):
                    raise TypeError(f"catalog node at index {index} must be an object")
                node_id = str(raw.get("id") or "")
                if node_id in seen:
                    raise ValueError(f"duplicate capability id {node_id!r}")
                seen.add(node_id)
                items.append((node_id, raw))
        else:
            raise TypeError("capability catalog nodes must be a list or object")

        nodes = {node_id: CapabilityNode.from_dict(raw) for node_id, raw in items}
        return cls(
            version=str(data.get("version") or ""),
            schema_version=int(data.get("schema_version") or 1),
            nodes=nodes,
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "CapabilityCatalog":
        """Load and validate a JSON catalog; no path loads the packaged default."""
        catalog_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
        try:
            data = json.loads(catalog_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"capability catalog not found: {catalog_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid capability catalog JSON at {catalog_path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise TypeError(f"capability catalog at {catalog_path} must be an object")
        return cls.from_dict(data)

    def validate(self) -> None:
        """Validate references and reject self references and dependency cycles."""
        for node in self.nodes.values():
            for prerequisite in node.prerequisites:
                if prerequisite == node.id:
                    raise ValueError(f"capability {node.id!r} cannot reference itself")
                if prerequisite not in self.nodes:
                    raise ValueError(
                        f"capability {node.id!r} has unknown prerequisite "
                        f"{prerequisite!r}"
                    )

        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(node_id: str) -> None:
            mark = state.get(node_id, 0)
            if mark == 2:
                return
            if mark == 1:
                start = stack.index(node_id)
                cycle = stack[start:] + [node_id]
                raise ValueError(f"capability catalog cycle: {' -> '.join(cycle)}")
            state[node_id] = 1
            stack.append(node_id)
            for prerequisite in self.nodes[node_id].prerequisites:
                visit(prerequisite)
            stack.pop()
            state[node_id] = 2

        for node_id in sorted(self.nodes):
            visit(node_id)

    def get(self, node_id: str) -> CapabilityNode:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown capability {node_id!r}") from exc

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.nodes

    def __iter__(self):
        return iter(self.nodes.values())

    def topological_ids(self) -> list[str]:
        """Return deterministic prerequisite-first node IDs."""
        ordered: list[str] = []
        seen: set[str] = set()

        def add(node_id: str) -> None:
            if node_id in seen:
                return
            for prerequisite in self.nodes[node_id].prerequisites:
                add(prerequisite)
            seen.add(node_id)
            ordered.append(node_id)

        for node_id in sorted(self.nodes):
            add(node_id)
        return ordered

    def prerequisite_closure(self, node_ids: Iterable[str]) -> tuple[str, ...]:
        """Return all transitive prerequisites in deterministic topo order."""
        requested = tuple(node_ids)
        unknown = sorted(set(requested) - self.nodes.keys())
        if unknown:
            raise ValueError(f"unknown capabilities: {', '.join(unknown)}")
        closure: set[str] = set()

        def collect(node_id: str) -> None:
            for prerequisite in self.nodes[node_id].prerequisites:
                if prerequisite not in closure:
                    closure.add(prerequisite)
                    collect(prerequisite)

        for node_id in requested:
            collect(node_id)
        return tuple(node_id for node_id in self.topological_ids() if node_id in closure)

    def resolve_alias(self, value: str) -> str | None:
        """Resolve an ID, name, or alias; return ``None`` on no/ambiguous match."""
        needle = str(value or "").strip().casefold()
        if not needle:
            return None
        direct = {node_id.casefold(): node_id for node_id in self.nodes}
        if needle in direct:
            return direct[needle]
        matches = [
            node.id
            for node in self.nodes.values()
            if needle == node.name.casefold()
            or needle in {alias.casefold() for alias in node.aliases}
        ]
        return matches[0] if len(matches) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "nodes": [self.nodes[node_id].to_dict() for node_id in sorted(self.nodes)],
        }

    def snapshot(self, path: str | Path | None = None) -> dict[str, Any]:
        """Return a serializable snapshot and optionally write it atomically."""
        data = self.to_dict()
        if path is not None:
            atomic_write_text(
                path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            )
        return data
