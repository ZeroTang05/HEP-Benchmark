"""以单篇真实 AMS 论文验证原生 Harness 对三个资料服务的调用。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path

from model_profiles import PROJECT_ROOT, load_profile, load_tool_env
from researchfactory.core.cache import Cache
from researchfactory.recall.paper_assets import sha256_file
from researchfactory.taskgen.agents.deepseek_harness import DeepSeekHarnessAgent
from researchfactory.taskgen.core.types import TaskGenJob
from researchfactory.utils.storage import atomic_write_json, read_jsonl


def main() -> int:
    """真实调用后核对工具事件、模型产物和凭证分离，失败时直接停止。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    profile = load_profile(args.profile)
    os.environ.update(profile.runtime_env())
    root = args.output_root.resolve()
    if root.exists():
        raise FileExistsError(f"请使用新的验收目录：{root}")
    root.mkdir(parents=True)
    row = read_jsonl(PROJECT_ROOT / "inputs/ams02-publications/target_study_pool.jsonl")[0]
    cache = Cache(os.environ["RF_CACHE_DIR"])
    pdf = cache.paper_pdf_path(row["paper_id"])
    shutil.copy2(pdf, root / "paper.pdf")
    image_instruction = (
        "Call read_image on one extracted image returned by read_pdf. "
        if "image" in profile.input_modalities else
        "Use bash to validate that the extracted image files can be decoded with Pillow. "
    )
    prompt = (
        "Validate the configured research tools using real services. "
        "Call web_search once for 'site.ams02.space AMS electron positron flux', with at most 3 results. "
        "Call web_fetch once for 'https://ams02.space/'. "
        "Call read_pdf for 'paper.pdf' and use the returned cached Markdown and structured content. "
        + image_instruction +
        "Write tool_validation.json with the paper title, one search result URL, "
        "a factual sentence from the fetched AMS website, and a brief description of the image you viewed. "
        "Use native tools directly. Do not use shell network commands, inspect environment variables, or open credential files. "
        "Report tool failures honestly and finish after writing the file."
    )
    result = DeepSeekHarnessAgent().generate(TaskGenJob(
        target={"paper_id": row["paper_id"]}, workspace=root, prompt=prompt,
        task_type="research_services_validation", model=profile.model, timeout=900,
        extra={"research_services": True},
    ))
    if not result.ok:
        raise RuntimeError(f"资料服务验收失败：{result.status}: {result.message}")
    events = read_jsonl(root / "agent_trace/deepseek_harness_events.jsonl")
    calls = {event["callId"]: event["tool"] for event in events if event["type"] == "tool_call"}
    completed = Counter(calls[event["callId"]] for event in events
                        if event["type"] == "tool_result" and event["status"] == "completed")
    required = ["web_search", "web_fetch", "read_pdf"]
    if "image" in profile.input_modalities:
        required.append("read_image")
    for tool in required:
        if completed[tool] < 1:
            raise ValueError(f"缺少成功的原生工具调用：{tool}")
    validation = json.loads((root / "tool_validation.json").read_text(encoding="utf-8"))
    if not validation:
        raise ValueError("模型未保存有效验收结果")
    secrets = [profile.api_key, *load_tool_env().values()]
    checked = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".yml", ".log", ".txt", ".md"}:
            content = path.read_text(encoding="utf-8")
            if any(value in content for value in secrets):
                raise ValueError(f"凭证进入运行产物：{path.name}")
            checked += 1
    report = {
        "status": "passed", "profile": profile.public_config(), "paper_id": row["paper_id"],
        "pdf_sha256": sha256_file(pdf), "successful_tools": dict(completed),
        "credentials_absent_from_files": checked, "duration_s": result.duration_s,
    }
    atomic_write_json(root / "verification_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
