"""生成 HEP 源码包，或附带凭证和已验证论文缓存的私有运行包。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "hep-research-pipeline"
AMS_POOL = Path("inputs/ams02-publications/target_study_pool.jsonl")
# 与运行入口的默认缓存路径一致，解压后可直接复用。
CACHE_PATH = Path(".cache/researchfactory-mineru-rebuild-20261118")
RELEASE_FILES = (
    ".env.example",
    ".gitignore",
    ".python-version",
    "README.md",
    "HEP_AUTOMATION_ZH.md",
    "MODEL_SWITCHING_ZH.md",
    "RCB_HEP_DEEPSEEK_HARNESS_DESIGN.md",
    "pyproject.toml",
    "uv.lock",
    "config/model_profiles.json",
    "inputs/target_study_pool.jsonl",
    AMS_POOL,
    "runtime/deepseek-harness/README.md",
    "runtime/deepseek-harness/package.json",
    "runtime/deepseek-harness/package-lock.json",
    "runtime/deepseek-harness/build_task_series.mjs",
    "runtime/deepseek-harness/research_services.mjs",
    "runtime/deepseek-harness/run_task.mjs",
    "sources/ResearchFactory/AGENTS.md",
)
SOURCE_GLOBS = (
    "scripts/*.py",
    "scripts/*.sh",
    "sources/ResearchFactory/researchfactory/**/*.py",
    "sources/ResearchFactory/researchfactory/config/*.yaml",
    "sources/ResearchFactory/researchfactory/capabilities/data/*.json",
    "sources/ResearchFactory/researchfactory/taskgen/tasks/researchclawbench/*.md",
    "sources/ResearchFactory/tests/test_*.py",
    "sources/ResearchFactory/tests/conftest.py",
)


def release_paths() -> list[Path]:
    """收集可发布文件，写包前检查文件存在且未通过软链接引用机器上的其他内容。"""
    paths = {ROOT / name for name in RELEASE_FILES}
    for pattern in SOURCE_GLOBS:
        matches = set(ROOT.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"发布清单无匹配文件：{pattern}")
        paths.update(matches)
    for path in sorted(paths):
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"发布文件必须是项目内的普通文件：{path.relative_to(ROOT)}")
    return sorted(paths)


def read_jsonl(path: Path) -> list[dict]:
    """读取运行清单，每行对应一篇论文。"""
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def prepare_cache(workdir: Path) -> dict[Path, Path]:
    """调用现有预处理入口，按论文和解析清单选出可搬到新机器的文件。"""
    cache_root = (ROOT / os.environ.get("RF_CACHE_DIR", CACHE_PATH)).resolve()
    env = {**os.environ, "RF_CACHE_DIR": str(cache_root)}
    env.pop("HEP_LIMIT", None)
    subprocess.run(
        ["bash", str(ROOT / "scripts/run_ams_hep_batch.sh"), "--pool", str(ROOT / AMS_POOL),
         "--prepare-only", "--output-root", str(workdir)],
        cwd=ROOT, env=env, check=True,
    )
    prepared = read_jsonl(workdir / "target_study_pool.jsonl")
    if [row["paper_id"] for row in prepared] != [row["paper_id"] for row in read_jsonl(ROOT / AMS_POOL)]:
        raise ValueError("已准备的论文与 AMS 运行清单不一致")

    files: dict[Path, Path] = {}

    def include(path: Path) -> None:
        """归档到解压后运行入口使用的相对位置。"""
        relative = path.relative_to(cache_root)
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"缓存文件必须是普通文件：{relative}")
        files[CACHE_PATH / relative] = path

    def include_assets(root: Path) -> None:
        """只收录解析清单登记的完成文件。"""
        manifest_path = root / "manifest.json"
        if CACHE_PATH / manifest_path.relative_to(cache_root) in files:
            return
        include(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            path = root / item["path"]
            path.resolve().relative_to(root)
            include(path)

    # 论文包索引含机器上的绝对路径，启动时会根据这些缓存文件重新生成。
    for row in prepared:
        bundle = json.loads(Path(row["paper_bundle_path"]).read_text(encoding="utf-8"))
        pdf = Path(bundle["pdf"])
        include(pdf)
        include(pdf.with_suffix(".pdf.source.json"))
        include_assets(Path(bundle["assets_dir"]))
        for supplement in bundle["supplements"]:
            path = Path(supplement["path"])
            include(path)
            include(path.with_suffix(path.suffix + ".source.json"))
            if "assets_dir" in supplement:
                include_assets(Path(supplement["assets_dir"]))
    print(f"论文缓存：{len(prepared)} 篇，{len(files)} 个文件", flush=True)
    return files


def main() -> None:
    """先完整写入临时 ZIP，再替换发布文件；打包失败时保留上一版。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", action="store_true", help="包含真实 .env 和 AMS 论文缓存，仅供私下传输")
    args = parser.parse_args()
    paths = release_paths()
    credentials = ROOT / ".env"
    if args.private and (credentials.is_symlink() or not credentials.is_file() or not credentials.stat().st_size):
        raise ValueError("私有包需要项目内非空的 .env 文件")
    archive_name = f"{PACKAGE_NAME}-private.zip" if args.private else f"{PACKAGE_NAME}.zip"
    output = ROOT / "dist" / archive_name
    output.parent.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        workdir = Path(directory)
        cache_files = prepare_cache(workdir) if args.private else {}
        temporary = workdir / output.name
        temporary.touch(mode=0o600 if args.private else 0o644)
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for path in paths:
                archive.write(path, Path(PACKAGE_NAME) / path.relative_to(ROOT))
            if args.private:
                entry = ZipInfo.from_file(credentials, f"{PACKAGE_NAME}/.env")
                entry.external_attr = 0o100600 << 16
                archive.writestr(entry, credentials.read_bytes(), compress_type=ZIP_DEFLATED)
            for relative, path in sorted(cache_files.items()):
                archive.write(path, Path(PACKAGE_NAME) / relative)
        temporary.replace(output)
    print(output)


if __name__ == "__main__":
    main()
