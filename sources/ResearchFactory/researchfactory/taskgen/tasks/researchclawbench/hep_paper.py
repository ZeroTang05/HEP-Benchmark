"""HEP 目标论文身份预检。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import pymupdf


_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")
_COMMON_TOKENS = frozenset({"with", "from", "results", "using", "measurement", "study"})


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(value.lower())
        if token not in _COMMON_TOKENS
    }


def _first_pages_text(path: Path, pages: int = 2) -> str:
    """读取 PDF 前几页文本；解析失败直接抛出。"""
    try:
        document = pymupdf.open(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"cannot open target paper PDF {path}: {exc}") from exc
    try:
        if document.page_count == 0:
            raise ValueError(f"target paper PDF has no pages: {path}")
        return "\n".join(document.load_page(index).get_text() for index in range(min(pages, document.page_count)))
    finally:
        document.close()


def ensure_target_paper_identity(pdf_path: Path, target: Mapping[str, Any]) -> None:
    """确认 HEP 目标 PDF 首页内容与 pool 标题相符。"""
    title = str(target.get("title") or "").strip()
    if not title:
        raise ValueError("HEP target paper identity check requires target.title")
    expected = _tokens(title)
    if len(expected) < 3:
        raise ValueError("HEP target paper title is too short for identity checking")
    actual_text = _first_pages_text(pdf_path)
    actual = _tokens(actual_text)
    overlap = expected.intersection(actual)
    threshold = max(2, (len(expected) + 1) // 2)
    if len(overlap) < threshold:
        missing = ", ".join(sorted(expected - actual)[:8])
        raise ValueError(
            "target paper identity mismatch: "
            f"{len(overlap)}/{len(expected)} title tokens found in PDF; missing={missing}"
        )
