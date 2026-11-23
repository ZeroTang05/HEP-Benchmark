"""选择模型，自动完成论文准备、任务生成、审核、解题、评分和科学评审。"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from model_profiles import PROJECT_ROOT, load_profile, load_tool_env
from researchfactory.recall.paper_assets import DEFAULT_PDF_BACKEND


def positive_int(value: str) -> int:
    """让非法并发数或论文数量在启动模型前报错。"""
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("请输入大于零的整数")
    return result


def build_parser() -> argparse.ArgumentParser:
    """定义模型切换与小规模试运行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="连接配置；默认使用 local，读取本机 .env")
    parser.add_argument("--model", help="临时覆盖 .env 中的默认模型名")
    parser.add_argument(
        "--pool", type=Path,
        default=os.environ.get("HEP_POOL", str(PROJECT_ROOT / "inputs/ams02-publications/target_study_pool.jsonl")),
    )
    parser.add_argument("--output-root", type=Path, default=os.environ.get("HEP_OUTPUT_ROOT"))
    parser.add_argument("--plan-output", type=Path, default=os.environ.get("HEP_PLAN_OUTPUT"))
    parser.add_argument("--coverage-report", type=Path, help="从已有批次报告读取未覆盖的能力，供新一批抽题")
    parser.add_argument(
        "--jobs", type=positive_int, default=os.environ.get("HEP_JOBS", "1"),
        help="同时处理的论文数；默认 1",
    )
    parser.add_argument(
        "--limit", type=positive_int, default=os.environ.get("HEP_LIMIT") or None,
        help="只处理前几篇论文",
    )
    parser.add_argument("--dry-run", action="store_true", help="只准备任务目录和请求，不调用模型")
    parser.add_argument("--show-config", action="store_true", help="显示选中的端点和模型，不调用模型")
    parser.add_argument("--prepare-only", action="store_true", help="只下载和预处理论文，不调用模型")
    parser.add_argument("--cache-workers", type=positive_int, default=2, help="论文缓存处理进程数；默认 2")
    parser.add_argument(
        "--pdf-backend", choices=("pymupdf", "mineru"), default=DEFAULT_PDF_BACKEND,
        help="主论文解析方式；默认 MinerU，已有结果直接复用",
    )
    parser.add_argument("--through", choices=("build", "review", "judge"), default="judge", help="执行到哪个阶段；默认完成 Judge")
    parser.add_argument("--build-timeout", type=positive_int, default=7200)
    parser.add_argument("--review-timeout", type=positive_int, default=1800)
    parser.add_argument("--solver-timeout", type=positive_int, default=1800)
    parser.add_argument("--judge-timeout", type=positive_int, default=900)
    return parser


def main() -> int:
    """按依赖顺序启动各阶段，失败时停止，重跑时复用已完成的有效结果。"""
    parser = build_parser()
    args = parser.parse_args()
    from researchfactory.utils.storage import atomic_write_json, read_jsonl, write_jsonl_atomic

    cache_dir = Path(os.environ["RF_CACHE_DIR"])
    if args.prepare_only:
        prepared = (args.output_root or PROJECT_ROOT / ".cache/ams02-prepared") / "target_study_pool.jsonl"
        command = [
            sys.executable, "-m", "researchfactory.recall.paper_assets", "--pool", str(args.pool),
            "--cache-dir", str(cache_dir), "--output-pool", str(prepared), "--workers", str(args.cache_workers),
            "--pdf-backend", args.pdf_backend,
        ]
        if args.limit is not None:
            command.extend(["--limit", str(args.limit)])
        return subprocess.run(command, cwd=PROJECT_ROOT, env={**os.environ, **load_tool_env()}, check=False).returncode

    profile = load_profile(args.profile, model=args.model)
    print(json.dumps(profile.public_config(), ensure_ascii=False, indent=2), flush=True)
    if args.show_config:
        return 0

    # 按配置和模型分目录，便于比较不同模型的结果。
    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", profile.model)
    output_root = args.output_root or PROJECT_ROOT / "runs/ams02-hep-taskgen" / profile.name / model_slug
    output_root = output_root.resolve()
    plan_output = args.plan_output or output_root / "hep_batch_plan.jsonl"
    prepared_pool = output_root / "prepared_pool.jsonl"
    review_root = output_root / "reviewed"
    env = profile.runtime_env()
    prior_coverage = json.loads(args.coverage_report.read_text(encoding="utf-8")) if args.coverage_report else None
    command = [sys.executable, "-m", "researchfactory"]
    cache_args = [
        sys.executable, "-m", "researchfactory.recall.paper_assets", "--pool", str(args.pool),
        "--cache-dir", str(cache_dir), "--output-pool", str(prepared_pool),
        "--workers", str(args.cache_workers),
        "--pdf-backend", args.pdf_backend,
    ]
    plan_args = ["hep-plan", "--pool", str(prepared_pool), "--output", str(plan_output)]
    if args.coverage_report:
        plan_args.extend(["--coverage-report", str(args.coverage_report.resolve())])
    task_args = [
        "taskgen", "--task-type", "researchclawbench",
        "--agent", "deepseek_harness", "--model", profile.model,
        "--pool", str(prepared_pool), "--output-root", str(output_root),
        "--jobs", str(args.jobs), f"taskgen.task_timeout={args.build_timeout}",
    ]
    if args.limit is not None:
        cache_args.extend(["--limit", str(args.limit)])
    if args.dry_run:
        task_args.append("--dry-run")
    stages = [
        ("cache", cache_args), ("plan", command + plan_args), ("build", command + task_args),
        ("review", command + [
            "taskgen", "--phase", "review", "--task-type", "researchclawbench",
            "--agent", "deepseek_harness", "--model", profile.model,
            "--input-root", str(output_root), "--output-root", str(review_root),
            "--pool", str(prepared_pool),
            "--jobs", str(args.jobs), f"taskgen.review.task_timeout={args.review_timeout}",
        ]),
        ("assessment", [
            sys.executable, "-m", "researchfactory.taskgen.tasks.researchclawbench.hep_assessment",
            "--review-root", str(review_root), "--build-root", str(output_root),
            "--output-root", str(output_root), "--model", profile.model,
            "--pool", str(prepared_pool),
            "--solver-timeout", str(args.solver_timeout), "--judge-timeout", str(args.judge_timeout),
        ]),
    ]
    if args.coverage_report:
        stages[-1][1].extend(["--coverage-report", str(args.coverage_report.resolve())])
    workflow = {
        "schema_version": "researchfactory.hep_workflow.v1", "model": profile.public_config(),
        "stages": ["cache", "plan", "build", "review", "solver", "score", "judge", "report"],
        "through": args.through, "dry_run": args.dry_run,
        "paths": {"build": str(output_root), "review": str(review_root), "solver": str(output_root / "solver"), "score": str(output_root / "scores"), "judge": str(output_root / "judge")},
    }
    atomic_write_json(output_root / "workflow_plan.json", workflow)
    state = {"status": "running", "completed_stages": [], "current_stage": "cache"}
    for name, arguments in stages:
        if args.dry_run and name in {"review", "assessment"}:
            break
        state.update(current_stage=name, updated_at=datetime.now(timezone.utc).isoformat())
        atomic_write_json(output_root / "workflow_state.json", state)
        print(f"[workflow] {name}", flush=True)
        completed = subprocess.run(arguments, cwd=PROJECT_ROOT, env=env, check=False)
        if completed.returncode:
            state = json.loads((output_root / "workflow_state.json").read_text(encoding="utf-8"))
            state.update(status="failed", exit_code=completed.returncode)
            atomic_write_json(output_root / "workflow_state.json", state)
            return completed.returncode
        state["completed_stages"].extend(["solver", "score", "judge", "report"] if name == "assessment" else [name])
        if name == "cache":
            rows = read_jsonl(prepared_pool)
            route = {key: profile.public_config()[key] for key in ("provider", "base_url", "model")}
            write_jsonl_atomic(prepared_pool, [dict(row, taskgen_runtime=route) for row in rows])
        if name == "plan":
            focus_by_paper = {row["paper_id"]: row["capability_focus"] for row in read_jsonl(plan_output)}
            rows = read_jsonl(prepared_pool)
            write_jsonl_atomic(
                prepared_pool, [dict(row, capability_focus=focus_by_paper[row["paper_id"]]) for row in rows],
            )
        if name == args.through:
            break
    if not args.dry_run and args.through in {"build", "review"}:
        from researchfactory.taskgen.tasks.researchclawbench.hep_batch_report import (
            collect_batch_report,
            write_batch_report,
        )

        # 只汇总本轮输入；提前结束时也交付实际完成的阶段结果。
        target_ids = {row["paper_id"] for row in read_jsonl(prepared_pool)}
        report = collect_batch_report(
            output_root,
            review_root=review_root if args.through == "review" else None,
            target_ids=target_ids,
            prior_coverage=prior_coverage,
        )
        write_batch_report(output_root / "batch_report.json", report)
        state["completed_stages"].append("report")
    state.update(status="dry_run" if args.dry_run else "completed", current_stage=None, current_task=None)
    atomic_write_json(output_root / "workflow_state.json", state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
