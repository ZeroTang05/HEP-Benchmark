"""下载论文和官方附件，按内容指纹复用正文、图像与表格预处理结果。"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import importlib.metadata
import json
import os
import shutil
import tempfile
from collections import Counter
from itertools import islice
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

import pymupdf
import requests

from researchfactory.core.cache import Cache
from researchfactory.taskgen.tasks.researchclawbench.hep_paper import ensure_target_paper_identity
from researchfactory.utils.storage import atomic_write_json

ASSET_SCHEMA = "researchfactory.paper_assets.v1"
BUNDLE_SCHEMA = "researchfactory.paper_bundle.v1"
DEFAULT_PDF_BACKEND = "mineru"
DPI = 144
DENSE_VECTOR_THRESHOLD = 300
MAX_VECTOR_SEGMENTS = 1000
MAX_SIMPLE_PAGE_BYTES = 250000
MAX_FULL_SUPPLEMENT_PAGES = 64


@lru_cache(maxsize=1)
def source_session() -> requests.Session:
    """每个下载进程复用 HTTP 连接，减少官方附件的重复 TLS 握手。"""
    session = requests.Session()
    session.headers["User-Agent"] = "HEP-ResearchFactory/0.1"
    return session


def sha256_file(path: Path) -> str:
    """分块计算文件指纹，避免将大型 ROOT 数据全部读入内存。"""
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


@contextmanager
def cache_lock(path: Path) -> Iterator[None]:
    """用系统文件锁防止不同进程同时下载或提取同一份资料。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def download_source(url: str, destination: Path, *, cache: Cache, expected: str) -> dict[str, Any]:
    """从声明的单个来源下载原始字节，失败时保留 HTTP 原始错误。"""
    provenance = destination.with_suffix(destination.suffix + ".source.json")
    with cache_lock(destination.with_suffix(destination.suffix + ".lock")):
        if destination.is_file() and provenance.is_file():
            recorded = json.loads(provenance.read_text(encoding="utf-8"))
            if sha256_file(destination) != recorded["sha256"]:
                raise ValueError(f"缓存文件指纹不符：{destination}")
            return recorded
        if destination.is_file():
            recorded = {
                "declared_source_url": url, "retrieval": "existing_cache",
                "sha256": sha256_file(destination), "bytes": destination.stat().st_size,
            }
        else:
            with source_session().get(url, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                with cache.atomic_target(destination) as temporary:
                    size = 0
                    with temporary.open("wb") as stream:
                        for block in response.iter_content(1024 * 1024):
                            size += len(block)
                            if size > 256 * 1024 * 1024:
                                raise ValueError(f"资料超过 256 MiB：{url}")
                            stream.write(block)
                    with temporary.open("rb") as stream:
                        magic = stream.read(512)
                    if not size or b"<html" in magic.lower() or b"<!doctype html" in magic.lower():
                        raise ValueError(f"下载结果为空或为 HTML 页面：{url}")
                    if expected == ".pdf" and not magic.startswith(b"%PDF-"):
                        raise ValueError(f"下载结果不是 PDF：{url}")
                    if expected == ".root" and not magic.startswith(b"root"):
                        raise ValueError(f"下载结果不是 ROOT 文件：{url}")
                recorded = {
                    "source_url": url, "resolved_url": response.url,
                    "retrieval": "downloaded", "content_type": response.headers.get("Content-Type"),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": sha256_file(destination), "bytes": size,
                }
        atomic_write_json(provenance, recorded)
        return recorded


def _processor() -> dict[str, Any]:
    """解析器版本和参数共同决定缓存版本。"""
    return {
        "schema": ASSET_SCHEMA, "pymupdf": pymupdf.VersionBind,
        "pymupdf4llm": importlib.metadata.version("pymupdf4llm"), "dpi": DPI,
        "table_strategy": "lines_strict", "ocr": False,
        "dense_vector_threshold": DENSE_VECTOR_THRESHOLD,
        "max_vector_segments": MAX_VECTOR_SEGMENTS, "max_simple_page_bytes": MAX_SIMPLE_PAGE_BYTES,
    }


def verify_assets(root: Path) -> dict[str, Any]:
    """复用之前先核对全部产物，防止缺图或修改后的文本被当作完整缓存。"""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = root / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ValueError(f"论文预处理缓存不完整或指纹不符：{path}")
    return manifest


def preprocess_pdf(pdf: Path, cache: Cache) -> Path:
    """一次提取全文、每页预览、矢量图和候选表格，后续运行直接复用。"""
    source_sha = sha256_file(pdf)
    processor = _processor()
    version = Cache.key(processor)[:16]
    parent = cache.directory("papers.assets") / source_sha
    destination = parent / version
    with cache_lock(parent / f"{version}.lock"):
        if (destination / "manifest.json").is_file():
            verify_assets(destination)
            return destination
        if destination.exists():
            raise FileExistsError(f"预处理目录缺少完成清单：{destination}")
        parent.mkdir(parents=True, exist_ok=True)
        import pymupdf4llm

        with tempfile.TemporaryDirectory(prefix=".extract-", dir=parent) as temporary_name:
            temporary = Path(temporary_name)
            for name in ("pages", "figures", "tables"):
                (temporary / name).mkdir()
            with pymupdf.open(pdf) as document:
                if document.is_encrypted or not document.page_count:
                    raise ValueError(f"论文 PDF 加密或无页面：{pdf}")
                vector_counts = [sum("path" in item[0] for item in page.get_bboxlog()) for page in document]
                normal_pages = []
                for index, page in enumerate(document):
                    if vector_counts[index] > DENSE_VECTOR_THRESHOLD or len(page.read_contents()) > MAX_SIMPLE_PAGE_BYTES:
                        continue
                    segments = sum(len(path["items"]) for path in page.get_drawings())
                    if segments <= MAX_VECTOR_SEGMENTS:
                        normal_pages.append(index)
                chunks = pymupdf4llm.to_markdown(
                    document, pages=normal_pages, page_chunks=True, write_images=True,
                    image_path=str(temporary / "figures"), image_format="png",
                    dpi=DPI, table_strategy="lines_strict", graphics_limit=DENSE_VECTOR_THRESHOLD,
                    show_progress=False,
                )
                chunks_by_page = {int(chunk["metadata"]["page"]): chunk for chunk in chunks}
                pages = []
                all_text = []
                links = []
                for index, page in enumerate(document, start=1):
                    stem = f"page-{index:04d}"
                    text = page.get_text("text", sort=True)
                    all_text.append(text)
                    (temporary / "pages" / f"{stem}.txt").write_text(text, encoding="utf-8")
                    page.get_pixmap(dpi=DPI, alpha=False).save(temporary / "pages" / f"{stem}.png")
                    dense = index - 1 not in normal_pages
                    if dense:
                        # 密集时间序列图按整页保存，保留全部坐标、图例和误差棒。
                        relative = f"figures/{stem}-dense.png"
                        shutil.copy2(temporary / "pages" / f"{stem}.png", temporary / relative)
                        chunks_by_page[index] = {"text": f"## Page {index}\n\n{text}\n\n![Full scientific page]({relative})\n"}
                    tables = []
                    detected = [] if dense else page.find_tables(strategy="lines_strict").tables
                    for number, table in enumerate(detected, start=1):
                        table_path = f"tables/{stem}-table-{number:02d}.csv"
                        with (temporary / table_path).open("w", encoding="utf-8", newline="") as stream:
                            csv.writer(stream).writerows(table.extract())
                        tables.append({
                            "path": table_path, "bbox": list(table.bbox),
                            "rows": table.row_count, "columns": table.col_count,
                            "status": "extracted_candidate_requires_scientific_review",
                        })
                    links.extend({"page": index, "url": item["uri"]} for item in page.get_links() if item.get("uri"))
                    pages.append({
                        "page": index, "text": f"pages/{stem}.txt", "preview": f"pages/{stem}.png",
                        "tables": tables, "text_status": "extracted" if text.strip() else "image_only",
                        "figure_strategy": "full_page_dense_vectors" if dense else "detected_regions",
                        "vector_paths": vector_counts[index - 1],
                    })
                if not any(text.strip() for text in all_text):
                    raise ValueError(f"整篇论文无可提取文字，需要单独 OCR：{pdf}")
                (temporary / "full_text.txt").write_text("\n\n".join(all_text), encoding="utf-8")
                markdown = "\n\n".join(chunks_by_page[index]["text"] for index in range(1, document.page_count + 1))
                # 写图时库返回绝对路径，发布缓存前统一转换为相对路径。
                markdown = markdown.replace(str(temporary) + "/", "")
                (temporary / "paper.md").write_text(markdown, encoding="utf-8")
                figures = []
                for path in sorted((temporary / "figures").iterdir()):
                    figures.append({
                        "path": path.relative_to(temporary).as_posix(),
                        "pages": [index for index, chunk in chunks_by_page.items() if path.name in chunk["text"]],
                    })
                manifest = {
                    "schema_version": ASSET_SCHEMA, "pdf_sha256": source_sha,
                    "processor": processor, "page_count": document.page_count,
                    "pages": pages, "figures": figures, "links": links,
                    "markdown": "paper.md", "full_text": "full_text.txt",
                    "table_count": sum(len(page["tables"]) for page in pages),
                    "files": [
                        {"path": path.relative_to(temporary).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size}
                        for path in sorted(temporary.rglob("*")) if path.is_file()
                    ],
                }
                atomic_write_json(temporary / "manifest.json", manifest)
            os.replace(temporary, destination)
    return destination


def preprocess_supplement(pdf: Path, cache: Cache) -> Path:
    """超长数值附录保存全文、页索引和八页预览，避免展开数千张重复表格图。"""
    with pymupdf.open(pdf) as document:
        page_count = document.page_count
    if page_count <= MAX_FULL_SUPPLEMENT_PAGES:
        return preprocess_pdf(pdf, cache)
    source_sha = sha256_file(pdf)
    processor = {
        **_processor(), "mode": "long_supplement.v1",
        "full_render_limit": MAX_FULL_SUPPLEMENT_PAGES, "preview_pages": 8,
    }
    version = Cache.key(processor)[:16]
    parent = cache.directory("papers.assets") / source_sha
    destination = parent / version
    with cache_lock(parent / f"{version}.lock"):
        if (destination / "manifest.json").is_file():
            verify_assets(destination)
            return destination
        if destination.exists():
            raise FileExistsError(f"附录缓存缺少完成清单：{destination}")
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=parent) as name:
            temporary = Path(name)
            (temporary / "pages").mkdir()
            selected = set(range(1, 5)) | set(range(page_count - 3, page_count + 1))
            pages, figures, links = [], [], []
            line_number = 1
            with pymupdf.open(pdf) as document, (temporary / "full_text.txt").open("w", encoding="utf-8") as text_file:
                for number, page in enumerate(document, 1):
                    text = f"\n## Page {number}\n" + page.get_text("text", sort=True) + "\n"
                    text_file.write(text)
                    line_count = text.count("\n")
                    preview = None
                    if number in selected:
                        preview = f"pages/page-{number:04d}.png"
                        page.get_pixmap(dpi=DPI, alpha=False).save(temporary / preview)
                        figures.append({"path": preview, "pages": [number], "kind": "supplement_preview"})
                    pages.append({
                        "page": number, "text": "full_text.txt", "first_line": line_number,
                        "last_line": line_number + line_count - 1, "preview": preview,
                        "tables": [], "figure_strategy": "indexed_long_appendix",
                    })
                    line_number += line_count
                    links.extend({"page": number, "url": item["uri"]} for item in page.get_links() if item.get("uri"))
            (temporary / "paper.md").write_text(
                "# Long supplementary appendix\n\n"
                "The complete text is in `full_text.txt`; `manifest.json` maps each original page "
                "to its line range. The first four and last four pages have PNG previews. "
                "Use the official CSV/ROOT files listed in the source index for numerical data.\n",
                encoding="utf-8",
            )
            atomic_write_json(temporary / "manifest.json", {
                "schema_version": ASSET_SCHEMA, "pdf_sha256": source_sha, "processor": processor,
                "page_count": page_count, "pages": pages, "figures": figures, "links": links,
                "markdown": "paper.md", "full_text": "full_text.txt", "table_count": 0,
                "table_extraction": "official_measurement_files_preferred",
                "files": [
                    {"path": path.relative_to(temporary).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size}
                    for path in sorted(temporary.rglob("*")) if path.is_file()
                ],
            })
            os.replace(temporary, destination)
    return destination


def prepare_paper(target: dict[str, Any], *, cache: Cache, pdf_backend: str = DEFAULT_PDF_BACKEND) -> dict[str, Any]:
    """准备一篇目标论文及官方补充资料，并返回供任务生成使用的引用。"""
    paper_id = target["paper_id"]
    url = target["pdf_source_url"]
    pdf = cache.paper_pdf_path(paper_id)
    provenance = download_source(url, pdf, cache=cache, expected=".pdf")
    ensure_target_paper_identity(pdf, target)
    if pdf_backend == "mineru":
        from researchfactory.recall.mineru_assets import preprocess_mineru
        assets = preprocess_mineru(pdf, cache)
    elif pdf_backend == "pymupdf":
        assets = preprocess_pdf(pdf, cache)
    else:
        raise ValueError(f"未知 PDF 解析服务：{pdf_backend}")
    supplements = []
    for item in target.get("supplemental_links", []):
        source_url = item["url"]
        suffix = Path(urlsplit(source_url).path).suffix.lower()
        if suffix not in {".pdf", ".csv", ".root"}:
            raise ValueError(f"未声明的补充资料格式：{source_url}")
        filename = hashlib.sha256(source_url.encode()).hexdigest()[:20] + suffix
        path = cache.directory("papers.sources") / filename
        source = download_source(source_url, path, cache=cache, expected=suffix)
        entry = {"title": item["title"], "url": source_url, "path": str(path.resolve()), "sha256": source["sha256"], "format": suffix[1:]}
        if suffix == ".pdf":
            entry["assets_dir"] = str(preprocess_supplement(path, cache).resolve())
        elif suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream)
                entry["columns"] = next(reader)
                entry["preview"] = list(islice(reader, 3))
                entry["data_rows"] = len(entry["preview"]) + sum(1 for _row in reader)
        supplements.append(entry)
    result = {
        "schema_version": BUNDLE_SCHEMA, "paper_id": paper_id,
        "pdf": str(pdf.resolve()), "pdf_sha256": provenance["sha256"],
        "assets_dir": str(assets.resolve()), "supplements": supplements,
    }
    bundle_key = Cache.key("bundle", paper_id) if pdf_backend == "pymupdf" else Cache.key("bundle", paper_id, pdf_backend)
    bundle_path = cache.directory("papers.sources") / (bundle_key + ".json")
    atomic_write_json(bundle_path, result)
    result["bundle_path"] = str(bundle_path.resolve())
    return result


def _prepare_worker(arguments: tuple[dict[str, Any], str, str, str]) -> dict[str, Any]:
    """每个 PDF 在独立进程处理，避免 PyMuPDF 的多线程限制。"""
    target, directory, schema, pdf_backend = arguments
    return prepare_paper(target, cache=Cache(directory, schema), pdf_backend=pdf_backend)


def prepare_pool(rows: list[dict[str, Any]], cache: Cache, *, workers: int = 2, pdf_backend: str = DEFAULT_PDF_BACKEND) -> list[dict[str, Any]]:
    """有界并行准备论文；任一论文失败时直接抛出原始错误。"""
    arguments = [(row, str(cache.cache_dir), cache.schema, pdf_backend) for row in rows]
    if workers == 1:
        return [_prepare_worker(item) for item in arguments]
    executor = ProcessPoolExecutor(max_workers=workers)
    futures = {executor.submit(_prepare_worker, item): index for index, item in enumerate(arguments)}
    results: dict[int, dict[str, Any]] = {}
    try:
        for future in as_completed(futures):
            bundle = future.result()
            results[futures[future]] = bundle
            print(f"[cache {len(results)}/{len(rows)}] {bundle['paper_id']} ready", flush=True)
    finally:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    return [results[index] for index in range(len(rows))]


def enrich_pool(
    rows: list[dict[str, Any]], bundles: list[dict[str, Any]], *,
    candidates: list[dict[str, Any]], cache: Cache,
) -> list[dict[str, Any]]:
    """给目标论文附上预处理资料和清单内已缓存、确实被引用的先前论文。"""
    # 参考文献只交付原始 PDF，直接查来源记录，无需对应的解析包。
    cached_sources = {}
    for candidate in candidates:
        paper_id = candidate["paper_id"]
        pdf = cache.paper_pdf_path(paper_id)
        provenance = pdf.with_suffix(".pdf.source.json")
        if pdf.is_file() and provenance.is_file():
            cached_sources[paper_id] = (pdf, json.loads(provenance.read_text(encoding="utf-8"))["sha256"])

    enriched = []
    verified = set()
    for row, bundle in zip(rows, bundles):
        manifest = verify_assets(Path(bundle["assets_dir"]))
        cited_urls = "\n".join(unquote(item["url"]).lower() for item in manifest["links"])
        related = []
        for candidate in candidates:
            doi = str(candidate.get("doi") or "").lower()
            if candidate["paper_id"] == row["paper_id"] or not doi or doi not in cited_urls:
                continue
            if int(candidate["year"]) > int(row["year"]):
                continue
            paper_id = candidate["paper_id"]
            if paper_id not in cached_sources:
                continue
            pdf, fingerprint = cached_sources[paper_id]
            related.append({
                "paper_id": paper_id, "title": candidate["title"],
                "year": candidate["year"], "pdf": str(pdf.resolve()), "sha256": fingerprint,
            })
        related.sort(key=lambda item: item["year"], reverse=True)
        related = related[:3]
        for item in related:
            paper_id = item["paper_id"]
            if paper_id in verified:
                continue
            pdf, fingerprint = cached_sources[paper_id]
            if sha256_file(pdf) != fingerprint:
                raise ValueError(f"缓存的参考文献 PDF 指纹不符：{pdf}")
            ensure_target_paper_identity(pdf, item)
            verified.add(paper_id)
        enriched.append(dict(
            row, paper_assets_dir=bundle["assets_dir"], paper_bundle_path=bundle["bundle_path"],
            cached_related_papers=related, hep_protocol_version="v2",
        ))
    return enriched


def stage_paper_bundle(target: dict[str, Any], workspace: Path, task_root: Path) -> None:
    """复制 Builder/Reviewer 的私有资料，图表和答案资料始终留在公开区之外。"""
    bundle = json.loads(Path(target["paper_bundle_path"]).read_text(encoding="utf-8"))
    if bundle["paper_id"] != target["paper_id"]:
        raise ValueError("预处理资料包和目标论文标识不符")
    main = Path(bundle["assets_dir"])
    manifest = verify_assets(main)
    if manifest["pdf_sha256"] != sha256_file(task_root / "target_study/paper.pdf"):
        raise ValueError("目标论文和预处理资料的 PDF 指纹不符")
    private = workspace / "_paper_cache"
    shutil.copytree(main, private / "main")
    index = {"schema_version": BUNDLE_SCHEMA, "paper_id": target["paper_id"], "main": "main/manifest.json", "sources": [], "reference_images": []}
    for number, figure in enumerate(manifest["figures"], 1):
        filename = f"cached-{number:03d}.png"
        shutil.copy2(main / figure["path"], task_root / "target_study/images" / filename)
        index["reference_images"].append({"path": f"task/target_study/images/{filename}", "pages": figure["pages"]})
    (private / "sources").mkdir()
    guide = ["# Official sources", "", "Use these official CSVs directly with pandas or csv. Extracted PDF tables are cross-check material.", ""]
    for number, supplement in enumerate(bundle["supplements"], 1):
        source = Path(supplement["path"])
        if sha256_file(source) != supplement["sha256"]:
            raise ValueError(f"官方补充资料的指纹不符：{source.name}")
        relative = f"sources/{number:03d}-{Path(urlsplit(supplement['url']).path).name}"
        shutil.copy2(source, private / relative)
        entry = {key: supplement[key] for key in ("title", "url", "sha256", "format")}
        entry["path"] = relative
        if supplement["format"] == "csv":
            entry.update({key: supplement[key] for key in ("columns", "preview", "data_rows")})
            guide.extend([
                f"## {supplement['title']}", f"File: `{relative}`", f"Source: {supplement['url']}",
                "Columns: " + json.dumps(supplement["columns"], ensure_ascii=False),
                "First rows: " + json.dumps(supplement["preview"], ensure_ascii=False), "",
            ])
        if "assets_dir" in supplement:
            extracted = Path(supplement["assets_dir"])
            verify_assets(extracted)
            entry["extracted"] = f"supplements/{source.stem}"
            shutil.copytree(extracted, private / entry["extracted"])
        index["sources"].append(entry)
    candidates = []
    for related in target.get("cached_related_papers", []):
        source = Path(related["pdf"])
        if sha256_file(source) != related["sha256"]:
            raise ValueError("缓存的参考文献 PDF 指纹不符")
        filename = f"related/{related['sha256'][:20]}.pdf"
        (private / "related").mkdir(exist_ok=True)
        shutil.copy2(source, private / filename)
        candidates.append({**{key: related[key] for key in ("paper_id", "title", "year", "sha256")},
                           "path": filename})
    atomic_write_json(private / "related_candidates.json", candidates)
    atomic_write_json(private / "index.json", index)
    (private / "README.md").write_text("\n".join(guide), encoding="utf-8")


def summarize_bundles(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    """按实际文件去重统计缓存规模，让预处理结果可以直接核对。"""
    assets = {}
    formats: Counter[str] = Counter()
    sources = set()
    main_pages = 0
    for bundle in bundles:
        main = verify_assets(Path(bundle["assets_dir"]))
        assets[bundle["assets_dir"]] = main
        main_pages += main["page_count"]
        for source in bundle["supplements"]:
            formats[source["format"]] += 1
            sources.add(source["sha256"])
            if "assets_dir" in source and source["assets_dir"] not in assets:
                assets[source["assets_dir"]] = verify_assets(Path(source["assets_dir"]))
    return {
        "main_pages": main_pages,
        "supplement_formats": dict(sorted(formats.items())),
        "unique_supplement_contents": len(sources),
        "preprocessed_pdfs": len(assets),
        "pdf_pages_with_supplements": sum(item["page_count"] for item in assets.values()),
        "figure_candidates": sum(len(item["figures"]) for item in assets.values()),
        "table_candidates": sum(item["table_count"] for item in assets.values()),
        "page_previews": sum(bool(page.get("preview")) for item in assets.values() for page in item["pages"]),
        "long_appendices": sum(item["processor"].get("mode") == "long_supplement.v1" for item in assets.values()),
        "assets_bytes": sum(
            path.stat().st_size for directory in assets for path in Path(directory).rglob("*") if path.is_file()
        ),
    }


def main() -> int:
    """提供可单独运行的论文缓存准备命令。"""
    import argparse
    from researchfactory.taskgen.tasks.researchclawbench.hep_batch_plan import read_pool
    from researchfactory.utils.storage import write_jsonl_atomic

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-pool", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pdf-backend", choices=("pymupdf", "mineru"), default=DEFAULT_PDF_BACKEND)
    args = parser.parse_args()
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers 和 limit 必须大于零")
    candidates = read_pool(args.pool)
    rows = candidates[:args.limit] if args.limit else candidates
    cache = Cache(args.cache_dir)
    bundles = prepare_pool(rows, cache, workers=args.workers, pdf_backend=args.pdf_backend)
    enriched = enrich_pool(rows, bundles, candidates=candidates, cache=cache)
    if args.output_pool.is_file():
        previous = {row["paper_id"]: row for row in read_pool(args.output_pool)}
        for row in enriched:
            prior = previous.get(row["paper_id"])
            if prior is not None and prior["paper_assets_dir"] == row["paper_assets_dir"]:
                # 同一轮任务固定已选参考文献，避免缓存增加触发重复付费生成。
                row["cached_related_papers"] = prior["cached_related_papers"]
    args.output_pool.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl_atomic(args.output_pool, enriched)
    summary = {
        "papers": len(rows), "supplements": sum(len(bundle["supplements"]) for bundle in bundles),
        **summarize_bundles(bundles),
        "output_pool": str(args.output_pool.resolve()),
        "assets": [
            {"paper_id": bundle["paper_id"], "pages": manifest["page_count"], "figures": len(manifest["figures"]), "tables": manifest["table_count"]}
            for bundle in bundles
            for manifest in [verify_assets(Path(bundle["assets_dir"]))]
        ],
    }
    atomic_write_json(args.output_pool.with_suffix(".cache-report.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
