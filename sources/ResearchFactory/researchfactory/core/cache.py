"""HEP 论文与解析结果的内容缓存、路径校验与原子写入。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from researchfactory.utils.text import filesystem_slug

_LAYOUT = {
    "papers.pdf": ("papers", "pdf"),
    "papers.assets": ("papers", "assets"),
    "papers.sources": ("papers", "sources"),
}


class Cache:
    """维持论文文件名与缓存版本稳定，完整结果原子发布。"""

    def __init__(self, cache_dir: str | Path, schema: str = "v2"):
        self.cache_dir = Path(cache_dir)
        self.schema = self._component(schema, label="cache schema")


    @classmethod
    def from_paths(cls, paths: Any) -> "Cache":
        return cls(paths.cache_dir, paths.cache_schema)


    @property
    def root(self) -> Path:
        return self.cache_dir / self.schema


    @staticmethod
    def key(*parts: Any) -> str:
        """Return a canonical SHA-256 key without delimiter ambiguity."""
        encoded = json.dumps(
            parts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


    @staticmethod
    def _component(value: str, *, label: str) -> str:
        normalized = str(value or "").strip()
        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
            or Path(normalized).is_absolute()
        ):
            raise ValueError(f"unsafe {label}: {value!r}")
        return normalized


    def directory(self, area: str) -> Path:
        try:
            parts = _LAYOUT[area]
        except KeyError as exc:
            raise ValueError(f"unknown cache area: {area!r}") from exc
        return self.root.joinpath(*parts)


    def _file(
        self,
        area: str,
        key: str,
        *,
        suffix: str,
        partition: str | None = None,
    ) -> Path:
        directory = self.directory(area)
        if partition is not None:
            directory /= self._component(partition, label="cache partition")
        filename = self._component(f"{key}{suffix}", label="cache filename")
        return directory / filename


    def paper_pdf_path(self, paper_id: str) -> Path:
        return self._file("papers.pdf", self._paper_name(paper_id), suffix=".pdf")


    @staticmethod
    def _paper_name(paper_id: str) -> str:
        slug = filesystem_slug(paper_id, fallback="paper")
        identity = hashlib.sha256((paper_id or "").encode("utf-8")).hexdigest()[:16]
        return f"{slug}--{identity}"


    def _managed_path(self, path: str | Path) -> Path:
        candidate = Path(path).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError(f"path is outside cache root: {path}") from exc
        return candidate


    @contextmanager
    def atomic_target(self, path: str | Path) -> Iterator[Path]:
        """Yield a temporary sibling and publish it atomically on success."""
        target = self._managed_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=".tmp-",
            suffix=target.suffix or ".tmp",
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            yield temporary
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


    def resolve(self, reference: str | None, *, fallback: Path | None = None) -> Path | None:
        """Resolve only current-schema references; v1 and absolute paths are ignored."""
        candidates: list[Path] = []
        if reference:
            stored = Path(reference)
            if not stored.is_absolute():
                candidates.append(self.cache_dir / stored)
        if fallback is not None:
            candidates.append(fallback)
        for candidate in candidates:
            try:
                managed = self._managed_path(candidate)
            except ValueError:
                continue
            if managed.exists():
                return managed
        return None
