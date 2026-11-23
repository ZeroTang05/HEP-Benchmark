"""HEP 任务构建和独立审核的命令编排。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from researchfactory.core.cache import Cache
from researchfactory.core.config import config_yaml, load_config
from researchfactory.taskgen.core.planner import plan_taskgen, read_taskgen_plan, write_taskgen_plan
from researchfactory.taskgen.core.registry import get_agent, get_task_type
from researchfactory.taskgen.core.runner import run_batch, run_review_batch, summarize
from researchfactory.taskgen.core.types import TaskGenJob
from researchfactory.utils.storage import read_jsonl


def _progress(done: int, total: int, outcome: Any) -> None:
    """按论文显示当前结果。"""
    print(f"[{done}/{total}] {outcome.status}: {outcome.target_id} {outcome.message[:160]}", flush=True)


def taskgen_command(args: Any, overrides: list[str]) -> int:
    """从论文池或已匹配计划启动任务，审核使用独立工作区。"""
    settings = load_config(overrides)
    taskgen = settings.taskgen
    review = taskgen.review
    task_type_name = args.task_type or taskgen.task_type
    task_type = get_task_type(task_type_name)
    agent_name = args.agent or (review.agent if args.phase == "review" else taskgen.agent)
    config = taskgen.agent_config(agent_name)
    model = args.model or os.environ.get("DEEPSEEK_MODEL") or (review.model if args.phase == "review" else "") or config.model
    effort = review.reasoning_effort if args.phase == "review" else config.reasoning_effort
    jobs = args.jobs if args.jobs is not None else (review.jobs if args.phase == "review" else taskgen.jobs)
    if jobs < 1 or (args.limit is not None and args.limit < 1) or args.retry_invalid < 0:
        raise ValueError("并发数、论文数量必须大于零；重试次数不能为负数")
    pool = Path(args.pool) if args.pool else task_type.artifact_pool_path(settings)
    output = Path(args.output_root) if args.output_root else settings.paths.run_dir / args.phase
    skill = Path(args.skill) if args.skill else (review.skill_path if args.phase == "review" else taskgen.skill_path)
    generation = {"config_sha256": hashlib.sha256(config_yaml(overrides).encode()).hexdigest()}
    print(f"HEP {args.phase}: {agent_name}, model={model}, jobs={jobs}", flush=True)

    if args.phase == "review":
        if any((args.plan, args.capability_request, args.plan_output, args.plan_only, args.print_prompt, args.target_id, args.retry_invalid)):
            raise ValueError("审核阶段只接收已构建任务和论文范围")
        if not args.input_root:
            raise ValueError("审核阶段需要 --input-root")
        target_ids = {task_type.target_id(row) for row in read_jsonl(pool)} if args.pool else None
        outcomes = run_review_batch(
            input_root=Path(args.input_root), output_root=output, task_type_name=task_type_name,
            agent_name=agent_name, model=model, reasoning_effort=effort, jobs=jobs,
            max_turns=review.max_turns, timeout=review.task_timeout, dry_run=args.dry_run,
            resume=not args.no_resume, limit=args.limit, target_ids=target_ids, skill_path=skill,
            generation=generation, on_progress=_progress,
        )
    else:
        plans = read_taskgen_plan(Path(args.plan)) if args.plan else None
        if plans is not None and not plans:
            raise ValueError("任务计划为空")
        rows = [] if plans is not None else read_jsonl(pool)
        if args.capability_request:
            plans = plan_taskgen(rows, read_jsonl(task_type.capability_index_path(settings)),
                                 Path(args.capability_request), task_type=task_type_name)
            write_taskgen_plan(Path(args.plan_output) if args.plan_output else settings.paths.taskgen_plans_dir / "researchclawbench.jsonl", plans)
        elif args.plan_output:
            raise ValueError("--plan-output 需要 --capability-request")
        if plans is not None and any(plan.task_type != task_type_name for plan in plans):
            raise ValueError("任务计划与 HEP 任务类型不一致")
        if args.plan_only:
            if plans is None:
                raise ValueError("--plan-only 需要任务计划或能力请求")
            return 0 if all(plan.matched for plan in plans) else 1
        if args.print_prompt:
            candidates = [plan.apply_to_target() for plan in plans if plan.matched] if plans is not None else rows
            if args.target_id:
                candidates = [row for row in candidates if task_type.target_id(row) == args.target_id]
            if not candidates:
                raise ValueError("没有匹配的论文输入")
            target = candidates[0]
            job = TaskGenJob(target=target, workspace=output, prompt=task_type.build_prompt(target), model=model)
            system, prompt = get_agent(agent_name).resolve_prompts(job)
            print("\n\n".join(part for part in (system, prompt) if part))
            return 0
        if not rows and plans is None:
            raise ValueError(f"论文池为空：{pool}")
        outcomes = run_batch(
            settings=settings, pool_path=pool if plans is None else None, output_root=output,
            agent_name=agent_name, task_type_name=task_type_name, model=model,
            reasoning_effort=effort, jobs=jobs, max_turns=taskgen.max_turns,
            timeout=taskgen.task_timeout, dry_run=args.dry_run, resume=not args.no_resume,
            limit=args.limit, skill_path=skill, plans=plans, on_progress=_progress,
            generation=generation, cache=Cache.from_paths(settings.paths), invalid_retries=args.retry_invalid,
        )
    counts = summarize(outcomes)
    print(f"HEP {args.phase} 完成：{counts}", flush=True)
    successful = {"ok", "skipped", "dry_run", "rejected"} if args.phase == "review" else {"ok", "skipped", "dry_run", "blocked"}
    return 0 if all(outcome.status in successful for outcome in outcomes) else 1
