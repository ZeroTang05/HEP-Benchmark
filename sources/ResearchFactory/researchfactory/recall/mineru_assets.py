"""调用 MinerU 官方云 API，按 PDF 内容缓存正文、公式、图片和结构化表格。

本模块只负责上传、轮询和缓存；版面识别与 PDF 解析由 MinerU 完成。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pymupdf
import requests

from researchfactory.core.cache import Cache
from researchfactory.recall.paper_assets import ASSET_SCHEMA, cache_lock, sha256_file, verify_assets
from researchfactory.utils.storage import atomic_write_json

API = "https://mineru.net/api/v4"
PROCESSOR = {"backend": "mineru", "api_version": "v4", "model_version": "vlm", "adapter_version": 1}


def _api(session: requests.Session, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
    """凭证只发往官方 API，错误仅保留服务错误码和请求编号。"""
    token = os.environ.get("MINERU_TOKEN", "").strip()
    if not token:
        raise ValueError("缺少 MINERU_TOKEN")
    response = session.request(method, API + endpoint, headers={"Authorization": f"Bearer {token}"}, timeout=(20, 60), **kwargs)
    if not response.ok:
        raise RuntimeError(f"MinerU HTTP {response.status_code}")
    result = response.json()
    if result["code"] != 0:
        raise RuntimeError(f"MinerU code={result['code']}, trace_id={result.get('trace_id', '')}")
    return result["data"]


def _transfer(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    """文件传输不携带 API 凭证，也不在错误中暴露有签名的下载地址。"""
    try:
        response = session.request(method, url, timeout=(20, 120), **kwargs)
    except requests.RequestException as error:
        raise RuntimeError(f"MinerU 文件传输失败：{type(error).__name__}") from None
    if not response.ok:
        response.close()
        raise RuntimeError(f"MinerU 文件传输 HTTP {response.status_code}")
    return response


def _extract(archive: Path, destination: Path) -> None:
    """使用标准 ZIP 库展开服务结果，拒绝越界文件、符号链接和超大归档。"""
    with zipfile.ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > 1024**3:
            raise ValueError("MinerU 解压结果超过 1 GiB")
        for item in package.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("MinerU ZIP 包含越界路径或符号链接")
        package.extractall(destination)


def preprocess_mineru(pdf: Path, cache: Cache, *, timeout: int = 540) -> Path:
    """同一内容只上传一次；中断后继续轮询已有作业，完整结果原子发布。"""
    source_sha = sha256_file(pdf)
    version = Cache.key(PROCESSOR)[:16]
    parent = cache.directory("papers.assets") / source_sha
    destination = parent / ("mineru-" + version)
    job_path = parent / ("mineru-" + version + ".job.json")
    with cache_lock(parent / ("mineru-" + version + ".lock")):
        if (destination / "manifest.json").is_file():
            verify_assets(destination)
            return destination
        with pymupdf.open(pdf) as document:
            if document.is_encrypted or not 0 < document.page_count <= 600:
                raise ValueError("MinerU 云解析要求未加密且不超过 600 页的 PDF")
            page_count = document.page_count
            links = [{"page": index, "url": item["uri"]}
                     for index, page in enumerate(document, 1) for item in page.get_links() if item.get("uri")]
        if pdf.stat().st_size > 200 * 1024**2:
            raise ValueError("MinerU 云解析文件超过 200 MiB")
        deadline = time.monotonic() + timeout
        with requests.Session() as session:
            if job_path.is_file():
                job = json.loads(job_path.read_text(encoding="utf-8"))
                if job["state"] == "failed":
                    raise RuntimeError(f"MinerU 已记录解析失败，请检查 {job_path}")
            else:
                data = _api(session, "POST", "/file-urls/batch", json={
                    "files": [{"name": source_sha + ".pdf", "data_id": source_sha}],
                    "model_version": PROCESSOR["model_version"],
                })
                job = {"batch_id": data["batch_id"], "state": "upload_pending", "upload_url": data["file_urls"][0]}
                atomic_write_json(job_path, job)
                job_path.chmod(0o600)
            if job["state"] == "upload_pending":
                with pdf.open("rb") as stream:
                    _transfer(session, "PUT", job["upload_url"], data=stream).close()
                job = {"batch_id": job["batch_id"], "state": "submitted"}
                atomic_write_json(job_path, job)
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"MinerU 等待超过 {timeout} 秒；下次运行继续已有作业")
                result = _api(session, "GET", "/extract-results/batch/" + job["batch_id"])["extract_result"]
                if len(result) != 1:
                    raise ValueError("MinerU 单篇作业返回的文件数量不符")
                item = result[0]
                if item["state"] == "failed":
                    atomic_write_json(job_path, {"batch_id": job["batch_id"], "state": "failed", "error": item.get("err_msg", "")})
                    raise RuntimeError(f"MinerU 解析失败，检查 {job_path}")
                if item["state"] == "done":
                    break
                time.sleep(min(5, max(0.01, deadline - time.monotonic())))
            with tempfile.TemporaryDirectory(prefix=".mineru-", dir=parent) as temporary_name:
                temporary = Path(temporary_name)
                archive = temporary / "result.zip"
                # 该 CDN 在本地代理下曾出现 TLS 错误，明确采用直连。
                with requests.Session() as download:
                    if urlsplit(item["full_zip_url"]).hostname == "cdn-mineru.openxlab.org.cn":
                        download.trust_env = False
                    with _transfer(download, "GET", item["full_zip_url"], stream=True) as response, archive.open("wb") as output:
                        size = 0
                        for block in response.iter_content(1024 * 1024):
                            size += len(block)
                            if size > 512 * 1024**2:
                                raise ValueError("MinerU 下载结果超过 512 MiB")
                            output.write(block)
                _extract(archive, temporary)
                archive.unlink()
                markdown = temporary / "full.md"
                if not markdown.is_file() or not markdown.read_text(encoding="utf-8").strip():
                    raise ValueError("MinerU 未返回有效 full.md")
                shutil.copy2(markdown, temporary / "paper.md")
                shutil.copy2(markdown, temporary / "full_text.txt")
                lists = list(temporary.glob("*_content_list.json"))
                if len(lists) != 1:
                    raise ValueError("MinerU 未返回唯一的结构化 content_list.json")
                blocks = json.loads(lists[0].read_text(encoding="utf-8"))
                figures = [
                    {"path": path.relative_to(temporary).as_posix(), "pages": sorted({
                        int(block["page_idx"]) + 1 for block in blocks if block.get("img_path") == path.relative_to(temporary).as_posix()
                    })} for path in sorted((temporary / "images").rglob("*")) if path.is_file()
                ]
                pages = [{"page": number, "preview": None, "tables": [
                    {"structured_json": lists[0].name, "block_index": index, "status": "extracted_candidate_requires_scientific_review"}
                    for index, block in enumerate(blocks) if block.get("page_idx") == number - 1 and block.get("type") == "table"
                ]} for number in range(1, page_count + 1)]
                manifest = {
                    "schema_version": ASSET_SCHEMA, "pdf_sha256": source_sha, "processor": PROCESSOR,
                    "page_count": page_count, "pages": pages, "figures": figures, "links": links,
                    "markdown": "paper.md", "full_text": "full_text.txt", "structured_content": lists[0].name,
                    "table_count": sum(block.get("type") == "table" for block in blocks),
                    "batch_id": job["batch_id"],
                    "files": [{"path": path.relative_to(temporary).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size}
                              for path in sorted(temporary.rglob("*")) if path.is_file()],
                }
                atomic_write_json(temporary / "manifest.json", manifest)
                os.replace(temporary, destination)
        return destination


def main() -> int:
    """为原生 read_pdf 工具提供简短结果，并将完整资料复制到私有工作区。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    pdf = args.pdf.resolve(strict=True)
    workspace = args.workspace.resolve(strict=True)
    if not pdf.is_relative_to(workspace):
        raise ValueError("PDF 必须位于当前工作区")
    source = preprocess_mineru(pdf, Cache(args.cache_dir))
    manifest = verify_assets(source)
    staged = workspace / "_paper_cache/mineru" / manifest["pdf_sha256"]
    with cache_lock(staged.with_suffix(".lock")):
        if staged.is_dir():
            verify_assets(staged)
        else:
            staged.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".stage-", dir=staged.parent) as name:
                shutil.copytree(source, Path(name), dirs_exist_ok=True)
                os.replace(name, staged)
    print(json.dumps({
        "backend": "mineru", "markdown": str(staged / manifest["markdown"]),
        "structured_content": str(staged / manifest["structured_content"]),
        "images": [str(staged / item["path"]) for item in manifest["figures"]],
        "table_candidates": manifest["table_count"], "pages": manifest["page_count"],
        "text_preview": (staged / manifest["markdown"]).read_text(encoding="utf-8")[:12000],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # 服务错误只保留类型，HTTP 辅助函数已经移除了敏感链接和请求头。
        message = str(error) if isinstance(error, (ValueError, RuntimeError, TimeoutError)) else type(error).__name__
        print(f"{type(error).__name__}: {message}", file=sys.stderr)
        raise SystemExit(1) from None
