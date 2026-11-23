"""交付每题使用的先前文献，验证文件、来源和公开边界。"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from researchfactory.core.cache import Cache
from researchfactory.recall.paper_assets import download_source, sha256_file
from researchfactory.taskgen.tasks.researchclawbench.hep_paper import (
    _first_pages_text, ensure_target_paper_identity,
)

REFERENCES = "references.json"
_PDF_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.pdf")


def _https(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and parts.username is None and parts.password is None


def fetch_pdf(root: Path, url: str, filename: str, cache: Cache) -> dict[str, Any]:
    """用现有下载器缓存公开 PDF，按内容校验后复制到自己的题包。"""
    if not _https(url) or not _PDF_NAME.fullmatch(filename):
        raise ValueError("use a public HTTPS URL and a PDF filename without directories")
    destination = root / "related_work" / filename
    if destination.parent.resolve() != destination.parent or not destination.parent.is_dir():
        raise ValueError("related_work must be a directory inside your task package")
    cached = cache.root / "related-pdf" / (cache.key(url) + ".pdf")
    record = download_source(url, cached, cache=cache, expected=".pdf")
    _first_pages_text(cached, pages=1)
    # 在题包所在目录写入临时文件，完整复制后再发布。
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        temporary = Path(directory) / filename
        shutil.copyfile(cached, temporary)
        temporary.replace(destination)
    return {"file": filename, "url": url, "sha256": record["sha256"]}


def _identity_text(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def validate_references(root: Path, target: dict[str, Any]) -> None:
    """校验文献目录中的 PDF 与逐篇著录相符，并排除目标论文及其改版。"""
    directory = root / "related_work"
    catalog = json.loads((directory / REFERENCES).read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("related_work/references.json must be an object")
    queries = catalog.get("search_queries")
    if not isinstance(queries, list) or not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise ValueError("record the literature search queries in related_work/references.json")
    papers = catalog.get("papers")
    if not isinstance(papers, list) or not papers:
        raise ValueError("include at least one relevant, publicly available prior PDF")
    files, identifiers, fingerprints = set(), set(), set()
    target_ids = {str(target.get(key) or "").strip().lower().removeprefix("doi:").removeprefix("arxiv:")
                  for key in ("paper_id", "doi", "arxiv_id")}
    target_title = _identity_text(str(target.get("title") or ""))
    target_pdf = root / "target_study/paper.pdf"
    target_sha = sha256_file(target_pdf)
    for paper in papers:
        if not isinstance(paper, dict):
            raise ValueError("each related paper must be an object")
        file = paper.get("file")
        if not isinstance(file, str) or not _PDF_NAME.fullmatch(file) or file in files:
            raise ValueError(f"invalid or repeated related PDF filename: {file!r}")
        for key in ("title", "identifier", "use"):
            if not isinstance(paper.get(key), str) or not paper[key].strip():
                raise ValueError(f"{file}: {key} must be non-empty")
        year = paper.get("year")
        if type(year) is not int or year <= 0 or (target.get("year") and year > int(target["year"])):
            raise ValueError(f"{file}: expected a publication year no later than the target study")
        if not _https(paper.get("url")):
            raise ValueError(f"{file}: include a public HTTPS source URL")
        identifier = paper["identifier"].strip().lower().removeprefix("doi:").removeprefix("arxiv:")
        path = directory / file
        fingerprint = sha256_file(path)
        if identifier in target_ids or fingerprint == target_sha:
            raise ValueError(f"{file}: the target study must stay private")
        if identifier in identifiers or fingerprint in fingerprints or paper.get("sha256") != fingerprint:
            raise ValueError(f"{file}: repeated paper or incorrect SHA-256")
        first_page = _identity_text(_first_pages_text(path, pages=1))
        if target_title and target_title in first_page:
            raise ValueError(f"{file}: this PDF contains the target paper title on its first page")
        ensure_target_paper_identity(path, paper)
        files.add(file)
        identifiers.add(identifier)
        fingerprints.add(fingerprint)
    if {entry.name for entry in directory.iterdir()} != files | {REFERENCES}:
        raise ValueError("related_work/ must contain only references.json and its listed PDFs")


def main() -> None:
    """供出题代理下载搜索结果中选中的公开 PDF。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("filename")
    args = parser.parse_args()
    cache = Cache(os.environ["RF_CACHE_DIR"])
    print(json.dumps(fetch_pdf(Path.cwd() / "task", args.url, args.filename, cache)))


if __name__ == "__main__":
    main()
