"""以真实 AMS 云解析结果验证正文、图表、完整性和缓存复用。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pymupdf
from PIL import Image

from researchfactory.core.cache import Cache
from researchfactory.core.config import load_config
from researchfactory.recall.mineru_assets import preprocess_mineru
from researchfactory.recall.paper_assets import enrich_pool, sha256_file, verify_assets
from researchfactory.utils.storage import read_jsonl

PROJECT = Path(__file__).parents[3]
INPUT_POOL = PROJECT / "inputs/ams02-publications/target_study_pool.jsonl"
CACHE = Cache.from_paths(load_config().paths)


def test_real_mineru_cache_preserves_scientific_assets() -> None:
    """验证实际服务返回的每个文件，并检查结构化表格和可解码图像。"""
    target = read_jsonl(INPUT_POOL)[0]
    pdf = CACHE.paper_pdf_path(target["paper_id"])
    bundle_path = CACHE.directory("papers.sources") / (Cache.key("bundle", target["paper_id"], "mineru") + ".json")
    assets = Path(json.loads(bundle_path.read_text())["assets_dir"])
    manifest = verify_assets(assets)
    assert manifest["processor"]["backend"] == "mineru"
    assert manifest["pdf_sha256"] == sha256_file(pdf)
    with pymupdf.open(pdf) as document:
        assert manifest["page_count"] == document.page_count == 10
    text = (assets / manifest["markdown"]).read_text()
    assert "Alpha Magnetic Spectrometer" in text
    assert "$" in text
    assert manifest["links"]
    blocks = json.loads((assets / manifest["structured_content"]).read_text())
    assert sum(block.get("type") == "table" for block in blocks) == manifest["table_count"] == 1
    assert len(manifest["figures"]) == 14
    indexed = {figure["path"]: figure for figure in manifest["figures"]}
    for block in blocks:
        if block.get("img_path"):
            assert block["page_idx"] + 1 in indexed[block["img_path"]]["pages"]
    for figure in manifest["figures"]:
        with Image.open(assets / figure["path"]) as image:
            image.verify()


def test_real_mineru_result_is_reused_without_new_uploads() -> None:
    """已完成解析再次读取时，文件与作业记录均保持原样。"""
    target = read_jsonl(INPUT_POOL)[0]
    pdf = CACHE.paper_pdf_path(target["paper_id"])
    parent = CACHE.directory("papers.assets") / sha256_file(pdf)
    before = {path.relative_to(parent): path.stat().st_mtime_ns for path in parent.rglob("*")
              if path.is_file() and path.suffix != ".lock"}
    result = preprocess_mineru(pdf, CACHE)
    after = {path.relative_to(parent): path.stat().st_mtime_ns for path in parent.rglob("*")
             if path.is_file() and path.suffix != ".lock"}
    assert result.name.startswith("mineru-")
    assert after == before


def test_mineru_paper_can_use_a_cached_reference_without_an_extraction(tmp_path: Path) -> None:
    """真实引用的 PDF 和来源记录足以生成引用条目。"""
    candidates = read_jsonl(INPUT_POOL)
    target = candidates[0]
    reference = next(row for row in candidates if row["doi"] == "10.1103/PhysRevLett.130.211002")
    isolated = Cache(tmp_path)
    source_pdf = CACHE.paper_pdf_path(reference["paper_id"])
    reference_pdf = isolated.paper_pdf_path(reference["paper_id"])
    reference_pdf.parent.mkdir(parents=True)
    shutil.copy2(source_pdf, reference_pdf)
    shutil.copy2(source_pdf.with_suffix(".pdf.source.json"), reference_pdf.with_suffix(".pdf.source.json"))
    bundle_dir = CACHE.directory("papers.sources")
    bundle_path = bundle_dir / (Cache.key("bundle", target["paper_id"], "mineru") + ".json")
    bundle = {**json.loads(bundle_path.read_text()), "bundle_path": str(bundle_path)}

    enriched = enrich_pool([target], [bundle], candidates=candidates, cache=isolated)

    assert [item["paper_id"] for item in enriched[0]["cached_related_papers"]] == [reference["paper_id"]]
    assert enriched[0]["cached_related_papers"][0]["pdf"] == str(reference_pdf)
