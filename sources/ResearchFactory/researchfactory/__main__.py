"""本机 HEP 任务生成、审核、解题评分和结果汇总命令。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from researchfactory.core.config import config_yaml


def _build_parser() -> argparse.ArgumentParser:
    """只公开 HEP 流水线使用的命令和参数。"""
    parser = argparse.ArgumentParser(prog="researchfactory", description=__doc__)
    parser.add_argument("--print-config", action="store_true", help="显示非敏感运行配置")
    commands = parser.add_subparsers(dest="cmd")
    task = commands.add_parser("taskgen", help="生成或独立审核 HEP 任务")
    task.add_argument("--phase", choices=("build", "review"), default="build")
    task.add_argument("--task-type", choices=("researchclawbench",))
    task.add_argument("--agent", choices=("deepseek_harness",))
    task.add_argument("--model")
    task.add_argument("--jobs", type=int)
    task.add_argument("--pool")
    task.add_argument("--output-root")
    task.add_argument("--input-root")
    task.add_argument("--limit", type=int)
    task.add_argument("--retry-invalid", type=int, default=0)
    task.add_argument("--skill")
    plans = task.add_mutually_exclusive_group()
    plans.add_argument("--capability-request")
    plans.add_argument("--plan")
    task.add_argument("--plan-output")
    task.add_argument("--plan-only", action="store_true")
    task.add_argument("--dry-run", action="store_true")
    task.add_argument("--no-resume", action="store_true")
    task.add_argument("--print-prompt", action="store_true")
    task.add_argument("--target-id")

    score = commands.add_parser("hep-score", help="使用隐藏参考答案执行数值评分")
    score.add_argument("--task-root", required=True)
    score.add_argument("--solver-root", required=True)
    score.add_argument("--output")
    judge = commands.add_parser("hep-judge", help="独立评审解题方法、误差和解释")
    for name in ("task-root", "solver-root", "evaluation", "workspace"):
        judge.add_argument("--" + name, required=True)
    judge.add_argument("--agent", choices=("deepseek_harness",), default="deepseek_harness")
    judge.add_argument("--model", default="deepseek-chat")
    judge.add_argument("--reasoning-effort", default="high")
    judge.add_argument("--timeout", type=int, default=900)
    judge.add_argument("--dry-run", action="store_true")
    judge.add_argument("--solver-trace", type=Path, help="解题过程的 DeepSeek Harness 事件文件")
    plan = commands.add_parser("hep-plan", help="检查论文输入并生成物理任务计划")
    plan.add_argument("--pool", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--limit", type=int)
    plan.add_argument("--coverage-report", type=Path, help="从已有批次报告读取未覆盖的能力")
    report = commands.add_parser("hep-batch-report", help="汇总构建、审核、数值评分和模型评审")
    report.add_argument("--input-root", required=True)
    for name in ("review-root", "score-root", "judge-root"):
        report.add_argument("--" + name)
    report.add_argument("--output", required=True)
    return parser


def _split_overrides(extra: list[str]) -> list[str]:
    overrides, bad = [], []
    for tok in extra:
        if "=" in tok and not tok.startswith("-"):
            overrides.append(tok)
        else:
            bad.append(tok)
    if bad:
        raise SystemExit(f"error: unrecognized arguments: {' '.join(bad)}")
    return overrides


def _hep_score_command(args) -> int:
    from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import CONTRACT

    try:
        result = CONTRACT.evaluate(Path(args.task_root), Path(args.solver_root))
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 0 if result.passed else 1
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _hep_judge_command(args) -> int:
    from researchfactory.taskgen.tasks.researchclawbench.hep_judge import observable_trace, run_hep_judge

    try:
        result = run_hep_judge(
            task_root=Path(args.task_root),
            solver_root=Path(args.solver_root),
            evaluation_path=Path(args.evaluation),
            workspace=Path(args.workspace),
            agent_name=args.agent,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
            dry_run=args.dry_run,
            solver_trace=observable_trace(args.solver_trace) if args.solver_trace else None,
        )
        print(json.dumps({
            "status": result.status,
            "returncode": result.returncode,
            "message": result.message,
            "workspace": str(result.workspace) if result.workspace else str(args.workspace),
            "meta": result.meta,
        }, ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _hep_plan_command(args) -> int:
    from researchfactory.taskgen.tasks.researchclawbench.hep_batch_plan import (
        build_batch_plan,
        read_pool,
        write_batch_plan,
    )

    try:
        if args.limit is not None and args.limit <= 0:
            raise ValueError("--limit must be positive")
        rows = read_pool(Path(args.pool), limit=args.limit)
        coverage = json.loads(args.coverage_report.read_text(encoding="utf-8")) if args.coverage_report else None
        plan_rows, groups = build_batch_plan(rows, coverage_report=coverage)
        write_batch_plan(Path(args.output), plan_rows)
        print(json.dumps({
            "schema_version": "researchfactory.hep_batch_plan_summary.v1",
            "rows": len(plan_rows),
            "groups": groups,
            "output": str(Path(args.output).resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _hep_batch_report_command(args) -> int:
    from researchfactory.taskgen.tasks.researchclawbench.hep_batch_report import (
        collect_batch_report,
        write_batch_report,
    )

    try:
        report = collect_batch_report(
            Path(args.input_root),
            review_root=Path(args.review_root) if args.review_root else None,
            score_root=Path(args.score_root) if args.score_root else None,
            judge_root=Path(args.judge_root) if args.judge_root else None,
        )
        write_batch_report(Path(args.output), report)
        print(json.dumps({
            "schema_version": "researchfactory.hep_batch_report_summary.v1",
            "count": report["count"],
            "status_counts": report["status_counts"],
            "output": str(Path(args.output).resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2



def main(argv: list[str] | None = None) -> int:
    """分发 HEP 命令，输入与运行错误保留明确退出码。"""
    parser = _build_parser()
    args, extra = parser.parse_known_args(argv)
    overrides = _split_overrides(extra)
    if args.print_config:
        print(config_yaml(overrides), end="")
        return 0
    if args.cmd == "taskgen":
        from researchfactory.taskgen.cli import taskgen_command
        return taskgen_command(args, overrides)
    handlers = {
        "hep-score": _hep_score_command, "hep-judge": _hep_judge_command,
        "hep-plan": _hep_plan_command, "hep-batch-report": _hep_batch_report_command,
    }
    if args.cmd in handlers:
        return handlers[args.cmd](args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
