"""使用真实的 30 篇 AMS 论文与官方附件验收缓存复用、来源和完整性。"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from researchfactory.core.cache import Cache
from researchfactory.core.config import load_config
from researchfactory.recall.paper_assets import MAX_FULL_SUPPLEMENT_PAGES, preprocess_pdf, sha256_file, verify_assets
from researchfactory.taskgen.tasks.researchclawbench.hep_paper import ensure_target_paper_identity
from researchfactory.utils.storage import read_jsonl

PROJECT = Path(__file__).parents[3]
CACHE = Cache.from_paths(load_config().paths)
POOL = PROJECT / ".cache/ams02-prepared/target_study_pool.jsonl"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def papers():
    """使用真实准备命令的产物，不生成代替论文的测试 PDF。"""
    if not POOL.is_file():
        pytest.skip("先运行 scripts/prepare_ams_papers.sh 准备真实论文")
    rows = read_jsonl(POOL)
    assert len(rows) == 30
    return rows


def test_all_papers_and_official_sources_are_complete(papers) -> None:
    formats = Counter()
    checked_assets = set()
    for target in papers:
        bundle = json.loads(Path(target["paper_bundle_path"]).read_text())
        pdf = Path(bundle["pdf"])
        ensure_target_paper_identity(pdf, target)
        assert sha256_file(pdf) == bundle["pdf_sha256"]
        main_assets = verify_assets(Path(bundle["assets_dir"]))
        assert main_assets["pdf_sha256"] == bundle["pdf_sha256"]
        assert main_assets["processor"]["backend"] == "mineru"
        for related in target["cached_related_papers"]:
            path = Path(related["pdf"])
            assert path.is_relative_to(CACHE.root)
            assert sha256_file(path) == related["sha256"]
        for source in bundle["supplements"]:
            assert sha256_file(Path(source["path"])) == source["sha256"]
            formats[source["format"]] += 1
            if "assets_dir" in source and source["assets_dir"] not in checked_assets:
                verify_assets(Path(source["assets_dir"]))
                checked_assets.add(source["assets_dir"])
            if source["format"] == "csv":
                assert source["columns"] and source["data_rows"] > 0
                assert 0 < len(source["preview"]) <= 3
    assert formats == {"csv": 128, "pdf": 30, "root": 3}


def test_preparation_report_covers_official_sources_and_extractions(papers) -> None:
    """准备命令的报告需要同时包含正文、附件与实际提取规模。"""
    report = json.loads(POOL.with_suffix(".cache-report.json").read_text())
    assert report["papers"] == len(papers) == 30
    assert report["supplements"] == 161
    assert report["supplement_formats"] == {"csv": 128, "pdf": 30, "root": 3}
    assert report["main_pages"] == 375
    assert report["pdf_pages_with_supplements"] == 11327
    assert report["long_appendices"] == 7
    assert report["preprocessed_pdfs"] == 60
    assert report["page_previews"] == 557
    assert report["figure_candidates"] == 961
    assert report["table_candidates"] == 218


def test_cached_extraction_keeps_existing_artifacts(papers) -> None:
    """验证补充 PDF 继续复用本地解析结果。"""
    bundle = json.loads(Path(papers[0]["paper_bundle_path"]).read_text())
    supplement = next(source for source in bundle["supplements"] if source["format"] == "pdf")
    directory = Path(supplement["assets_dir"])
    before = {path.relative_to(directory): path.stat().st_mtime_ns for path in directory.rglob("*") if path.is_file()}
    actual = preprocess_pdf(Path(supplement["path"]), CACHE)
    after = {path.relative_to(directory): path.stat().st_mtime_ns for path in directory.rglob("*") if path.is_file()}
    assert actual == directory
    assert after == before


def test_modified_cached_text_is_rejected(papers, tmp_path: Path) -> None:
    directory = tmp_path / "assets"
    shutil.copytree(papers[0]["paper_assets_dir"], directory)
    path = directory / "full_text.txt"
    path.write_text(path.read_text() + "\nchanged", encoding="utf-8")
    with pytest.raises(ValueError, match="指纹不符"):
        verify_assets(directory)


def test_dense_time_series_pages_keep_full_visual_context(papers, tmp_path: Path) -> None:
    """用真实论文验证可选的 PyMuPDF 解析，测试产物留在临时目录。"""
    target = next(row for row in papers if row["doi"] == "10.1103/PhysRevLett.128.231102")
    directory = preprocess_pdf(CACHE.paper_pdf_path(target["paper_id"]), Cache(tmp_path))
    manifest = verify_assets(directory)
    dense = [page for page in manifest["pages"] if page["figure_strategy"] == "full_page_dense_vectors"]
    assert dense
    for page in dense:
        assert (directory / page["preview"]).stat().st_size > 1000
        assert (directory / page["text"]).is_file()
    assert manifest["figures"]


def test_long_appendices_keep_complete_text_and_page_index(papers) -> None:
    """核对真实超长附录的页索引和预览，确认瘦身后仍能检索全部正文。"""
    checked = set()
    for target in papers:
        bundle = json.loads(Path(target["paper_bundle_path"]).read_text())
        for source in bundle["supplements"]:
            directory = source.get("assets_dir")
            if not directory or directory in checked:
                continue
            manifest = verify_assets(Path(directory))
            if manifest["page_count"] <= MAX_FULL_SUPPLEMENT_PAGES:
                continue
            checked.add(directory)
            assert manifest["processor"]["mode"] == "long_supplement.v1"
            assert len(manifest["pages"]) == manifest["page_count"]
            assert sum(bool(page["preview"]) for page in manifest["pages"]) == 8
            lines = (Path(directory) / "full_text.txt").read_text().split("\n")
            for page in manifest["pages"]:
                first = page["first_line"] - 1
                assert lines[first] == ""
                assert lines[first + 1] == f"## Page {page['page']}"
    assert len(checked) == 7
