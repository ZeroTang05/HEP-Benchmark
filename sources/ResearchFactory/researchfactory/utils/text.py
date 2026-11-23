"""Small text-normalization helpers shared across tracks."""
from __future__ import annotations

import re


def normalize_title(title: str) -> str:
    """Normalize a title for stable fuzzy matching."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def filesystem_slug(value: str, *, fallback: str = "item", max_length: int = 80) -> str:
    """Return a bounded, filesystem-safe label for a stable identifier."""
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", value or "").strip("._-")
    return (slug or fallback)[:max_length]
