"""Batch orchestration for task generation."""
from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

from researchfactory.core.cache import Cache
from researchfactory.core.config import Settings
from researchfactory.core.pipeline import parallel_map
from researchfactory.taskgen.core.registry import (
    get_agent,
    get_review_type,
    get_task_type,
)
from researchfactory.taskgen.core.stages import (
    built_tasks,
    clean_generated_files,
    execute_agent,
    initialize_workspace,
    validate_and_export,
    write_marker,
)
from researchfactory.taskgen.core.types import (
    AgentAdapter,
    ReviewType,
    SeedResolution,
    TaskgenPlan,
    TaskType,
)
from researchfactory.utils.storage import (
    atomic_write_json,
    read_json_or_none,
    read_jsonl,
    write_jsonl_atomic,
)
from researchfactory.utils.text import filesystem_slug


@dataclass
class TaskOutcome:
    target_id: str
    workspace: str
    status: str                      # ok | rejected | blocked | dry_run | invalid | failed | timeout | error | skipped
    agent_status: str = ""
    validation_ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    duration_s: float = 0.0
    attempts: int = 0
    input_fingerprint: str = ""
    phase: str = "build"
    source_workspace: str = ""
    report_status: str = ""
    tasks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _WorkItem:
    target: dict[str, Any] | None
    plan: TaskgenPlan | None = None


@dataclass(frozen=True)
class _BatchContext:
    settings: Settings
    cache: Cache
    output_root: Path
    agent: AgentAdapter
    agent_name: str
    task_type: TaskType
    task_type_name: str
    resolve_seed: Callable[[dict[str, Any]], SeedResolution]
    model: str
    reasoning_effort: str
    max_turns: int
    timeout: int
    proxy: str
    dry_run: bool
    resume: bool
    skill_path: Path | None
    generation: dict[str, Any] | None
    attempt_number: int = 1


def _load_pool(pool_path: Path, limit: Optional[int]) -> list[dict[str, Any]]:
    rows = read_jsonl(pool_path)
    return rows[:limit] if limit else rows


def _build_work(
    pool_path: Path | None,
    plans: list[TaskgenPlan] | None,
    limit: int | None,
    task_type_name: str,
) -> list[_WorkItem]:
    if plans is None:
        if pool_path is None:
            raise ValueError("pool_path is required when no taskgen plans are supplied")
        return [_WorkItem(row) for row in _load_pool(pool_path, limit)]

    selected = plans[:limit] if limit else plans
    for plan in selected:
        if plan.task_type and plan.task_type != task_type_name:
            raise ValueError(
                f"taskgen plan {plan.plan_id!r} targets {plan.task_type!r}, "
                f"not {task_type_name!r}"
            )
    return [
        _WorkItem(plan.apply_to_target() if plan.matched else None, plan)
        for plan in selected
    ]


def _unsatisfied(plan: TaskgenPlan) -> TaskOutcome:
    return TaskOutcome(
        target_id=plan.slot_id,
        workspace="",
        status="unsatisfied",
        message=plan.unsatisfied_reason or "capability request could not be satisfied",
    )


def _marker_target_id(marker: dict[str, Any]) -> str:
    """Read the generic target identity, including historical markers."""
    return str(marker.get("target_id") or marker.get("paper_id") or "").strip()


def _resume_outcome(
    context: _BatchContext,
    target_id: str,
    workspace: Path,
    input_fingerprint: str,
    plan_id: str,
) -> TaskOutcome | None:
    if not context.resume:
        return None
    prior = read_json_or_none(workspace / "_meta.json")
    same_identity = isinstance(prior, dict) and all((
        _marker_target_id(prior) == target_id,
        prior.get("task_type") == context.task_type_name,
        prior.get("agent") == context.agent_name,
        prior.get("model") == context.model,
        str(prior.get("reasoning_effort") or "") == context.reasoning_effort,
        str(prior.get("plan_id") or "") == plan_id,
        prior.get("input_fingerprint") == input_fingerprint,
    ))
    if (
        same_identity
        and prior.get("status") in {"ok", "blocked"}
        and prior.get("validation_ok") is True
    ):
        for child, _ in built_tasks(workspace, prior):
            if prior.get("tasks") and not (child / "task/task_info.json").is_file():
                raise FileNotFoundError(f"已生成的子任务缺失：{child}；可用 --no-resume 重建")
        return TaskOutcome(
            target_id=target_id,
            workspace=str(workspace),
            status="skipped",
            message="resume: already ok",
            tasks=prior.get("tasks", []),
        )
    return None


def _write_outcome(
    context: _BatchContext,
    item: _WorkItem,
    workspace: Path,
    outcome: TaskOutcome,
    input_fingerprint: str,
) -> TaskOutcome:
    outcome.input_fingerprint = input_fingerprint
    write_marker(
        workspace / "_meta.json",
        outcome,
        agent=context.agent_name,
        model=context.model,
        reasoning_effort=context.reasoning_effort,
        task_type=context.task_type_name,
        plan=item.plan,
        generation=context.generation,
    )
    return outcome


def _seed_unavailable(
    context: _BatchContext,
    item: _WorkItem,
    target_id: str,
    workspace: Path,
    seed: Any,
    input_fingerprint: str,
) -> TaskOutcome:
    outcome = TaskOutcome(
        target_id=target_id,
        workspace=str(workspace),
        status=seed.skip_reason or "no_seed",
        message=seed.meta.get("message") or f"seed unavailable ({seed.skip_reason})",
    )
    return _write_outcome(context, item, workspace, outcome, input_fingerprint)


def _agent_outcome(
    context: _BatchContext,
    item: _WorkItem,
    target_id: str,
    workspace: Path,
    result: Any,
    attempts: int,
    input_fingerprint: str,
) -> TaskOutcome:
    outcome = TaskOutcome(
        target_id=target_id,
        workspace=str(workspace),
        status=result.status,
        agent_status=result.status,
        message=result.message,
        duration_s=result.duration_s,
        attempts=attempts,
    )
    if result.ok and not context.dry_run:
        outcome.tasks = context.task_type.task_series(workspace, item.target or {})
        if outcome.tasks:
            children = [child for child, _ in built_tasks(workspace, {"tasks": outcome.tasks})]
            if any((child / "public").exists() or (child / "public").is_symlink() for child in children):
                raise FileExistsError("子题的 public/ 目录由批处理创建；生成阶段只写 task/")
            for child in children:
                context.task_type.export_public(child / "task", child / "public")
            outcome.validation_ok = True
            outcome.status = "ok"
            outcome.message = f"已生成 {len(outcome.tasks)} 道任务"
            return _write_outcome(context, item, workspace, outcome, input_fingerprint)
        terminal_status, ok, errors, warnings = validate_and_export(
            task_type=context.task_type,
            task_root=workspace / "task",
            public_root=workspace / "public",
        )
        outcome.validation_ok = ok
        outcome.errors = errors
        outcome.warnings = warnings
        outcome.status = terminal_status
    elif result.ok:
        outcome.status = "dry_run"
    return _write_outcome(context, item, workspace, outcome, input_fingerprint)


def _fallback_target_id(item: _WorkItem) -> str:
    target = item.target or {}
    for key in ("target_id", "paper_id", "identity", "id"):
        value = str(target.get(key) or "").strip()
        if value:
            return value
    if item.plan is not None:
        return item.plan.slot_id or item.plan.plan_id
    return f"error:{Cache.key('taskgen_fallback', target)[:16]}"


def _input_fingerprint(
    context: _BatchContext,
    item: _WorkItem,
    target_id: str,
) -> str:
    return Cache.key(
        "taskgen_input",
        "v1",
        context.task_type_name,
        target_id,
        item.target,
        item.plan.to_dict() if item.plan is not None else None,
        context.agent_name,
        context.model,
        context.reasoning_effort,
        context.max_turns,
        context.timeout,
        context.task_type.fingerprint_material(item.target or {}, context.skill_path),
        context.generation,
    )


def workspace_name(task_type: str, target_id: str, plan_id: str = "") -> str:
    """Return a readable, collision-resistant workspace name."""
    slug = filesystem_slug(target_id, max_length=64)
    identity = Cache.key("taskgen_workspace", "v1", task_type, target_id, plan_id)[:12]
    return f"{slug}--{identity}"


def _workspace_name(
    task_type: TaskType,
    task_type_name: str,
    item: _WorkItem,
    target_id: str,
) -> str:
    custom = task_type.workspace_name(item.target or {})
    if custom:
        return custom
    plan_id = item.plan.plan_id if item.plan is not None else ""
    return workspace_name(task_type_name, target_id, plan_id)


def _workspace(context: _BatchContext, item: _WorkItem, target_id: str) -> Path:
    return context.output_root / _workspace_name(
        context.task_type,
        context.task_type_name,
        item,
        target_id,
    )


def _reset_workspace(output_root: Path, workspace: Path) -> None:
    """Start an attempt without files left by an earlier run."""
    if workspace.parent.resolve() != output_root.resolve():
        raise ValueError(f"task workspace escapes output root: {workspace}")
    if workspace.is_symlink():
        workspace.unlink()
    elif workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)


def _error_outcome(
    context: _BatchContext,
    item: _WorkItem,
    target_id: str,
    workspace: Path,
    exc: Exception,
    attempts: int = 0,
    input_fingerprint: str = "",
) -> TaskOutcome:
    detail = " ".join(str(exc).split()) or "no error detail"
    message = f"{type(exc).__name__}: {detail}"[:1000]
    outcome = TaskOutcome(
        target_id=target_id,
        workspace=str(workspace),
        status="error",
        errors=[message],
        message=message,
        attempts=attempts,
    )
    try:
        return _write_outcome(context, item, workspace, outcome, input_fingerprint)
    except Exception as marker_exc:  # noqa: BLE001 - preserve batch isolation if marker IO also fails
        marker_detail = " ".join(str(marker_exc).split()) or "no error detail"
        outcome.warnings.append(
            f"marker_write_failed: {type(marker_exc).__name__}: {marker_detail}"[:1000]
        )
        return outcome


def _run_one(context: _BatchContext, item: _WorkItem) -> TaskOutcome:
    target_id = _fallback_target_id(item)
    workspace = _workspace(context, item, target_id)
    attempts = 0
    input_fingerprint = ""
    try:
        if item.target is None:
            if item.plan is None:
                raise ValueError("unsatisfied work item has no taskgen plan")
            return _unsatisfied(item.plan)

        target_id = context.task_type.target_id(item.target) or target_id
        workspace = _workspace(context, item, target_id)
        # 记录本次开始时使用的提示词版本，避免结束时读到下一轮的版本。
        input_fingerprint = _input_fingerprint(context, item, target_id)
        plan_id = item.plan.plan_id if item.plan is not None else ""
        resumed = _resume_outcome(context, target_id, workspace, input_fingerprint, plan_id)
        if resumed:
            return resumed

        _reset_workspace(context.output_root, workspace)
        seed = context.resolve_seed(item.target)
        if not seed.ok:
            return _seed_unavailable(context, item, target_id, workspace, seed, input_fingerprint)

        prepared = initialize_workspace(
            task_type=context.task_type,
            target=item.target,
            workspace=workspace,
            seed=seed,
            skill_path=context.skill_path,
        )
        attempts = context.attempt_number
        result = execute_agent(
            agent=context.agent,
            prepared=prepared,
            task_type_name=context.task_type_name,
            model=context.model,
            reasoning_effort=context.reasoning_effort,
            max_turns=context.max_turns,
            timeout=context.timeout,
            proxy=context.proxy,
            dry_run=context.dry_run,
        )
        return _agent_outcome(
            context, item, target_id, workspace, result, attempts, input_fingerprint,
        )
    except Exception as exc:  # noqa: BLE001 - one bad task must not abort the batch
        return _error_outcome(context, item, target_id, workspace, exc, attempts, input_fingerprint)


def _validate_workspace_keys(
    work: list[_WorkItem],
    task_type: TaskType,
    task_type_name: str,
) -> None:
    seen: dict[str, str] = {}
    for item in work:
        if item.target is None:
            continue
        target_id = task_type.target_id(item.target) or _fallback_target_id(item)
        name = _workspace_name(task_type, task_type_name, item, target_id)
        previous = seen.get(name)
        if previous is not None:
            raise ValueError(
                f"duplicate taskgen workspace for targets {previous!r} and {target_id!r}"
            )
        seen[name] = target_id


def run_batch(
    *,
    settings: Settings,
    pool_path: Path | None,
    output_root: Path,
    agent_name: str,
    task_type_name: str,
    model: str,
    jobs: int,
    max_turns: int,
    timeout: int,
    reasoning_effort: str = "",
    proxy: str = "",
    dry_run: bool = False,
    resume: bool = True,
    limit: Optional[int] = None,
    skill_path: Optional[Path] = None,
    plans: Optional[list[TaskgenPlan]] = None,
    on_progress: Optional[Callable[[int, int, TaskOutcome], None]] = None,
    generation: Optional[dict[str, Any]] = None,
    agent_adapter: AgentAdapter | None = None,
    cache: Cache | None = None,
    invalid_retries: int = 0,
) -> list[TaskOutcome]:
    if invalid_retries < 0:
        raise ValueError("invalid_retries must be non-negative")
    work = _build_work(pool_path, plans, limit, task_type_name)
    task_type = get_task_type(task_type_name)
    task_type.require_agent(agent_name)
    agent = agent_adapter or get_agent(agent_name)
    _validate_workspace_keys(work, task_type, task_type_name)
    output_root.mkdir(parents=True, exist_ok=True)
    batch_cache = cache or Cache.from_paths(settings.paths)
    resolve_seed = task_type.seed_resolver(
        settings=settings,
        cache=batch_cache,
        targets=[item.target for item in work if item.target is not None],
    )
    context = _BatchContext(
        settings=settings,
        cache=batch_cache,
        output_root=output_root,
        agent=agent,
        agent_name=agent_name,
        task_type=task_type,
        task_type_name=task_type_name,
        resolve_seed=resolve_seed,
        model=model,
        reasoning_effort=reasoning_effort,
        max_turns=max_turns,
        timeout=timeout,
        proxy=proxy,
        dry_run=dry_run,
        resume=resume,
        skill_path=skill_path,
        generation=generation,
    )
    outcomes = parallel_map(
        partial(_run_one, context),
        work,
        workers=max(1, jobs),
        on_done=on_progress,
    )
    if dry_run or invalid_retries == 0:
        return outcomes

    final_by_id = {outcome.target_id: outcome for outcome in outcomes}
    original_order = [outcome.target_id for outcome in outcomes]
    for retry_index in range(1, invalid_retries + 1):
        invalid_ids = {
            target_id
            for target_id, outcome in final_by_id.items()
            if outcome.status == "invalid"
        }
        if not invalid_ids:
            break
        retry_work = [
            item
            for item in work
            if (
                task_type.target_id(item.target or {})
                or _fallback_target_id(item)
            ) in invalid_ids
        ]
        retry_root = output_root.with_name(
            f"{output_root.name}-retry-{retry_index}"
        )
        if retry_root.exists():
            raise ValueError(f"retry output root already exists: {retry_root}")
        retry_root.mkdir(parents=True)
        write_jsonl_atomic(
            retry_root / "_input.jsonl",
            [item.target for item in retry_work if item.target is not None],
        )
        atomic_write_json(retry_root / "_attempt.json", {
            "attempt": retry_index + 1,
            "reason": "invalid",
            "source_output_root": str(output_root),
            "input_count": len(retry_work),
        })
        retry_context = replace(
            context,
            output_root=retry_root,
            resume=False,
            attempt_number=retry_index + 1,
        )
        retried = parallel_map(
            partial(_run_one, retry_context),
            retry_work,
            workers=max(1, jobs),
            on_done=on_progress,
        )
        final_by_id.update(
            (outcome.target_id, outcome) for outcome in retried
        )
    return [final_by_id[target_id] for target_id in original_order]


@dataclass(frozen=True)
class _ReviewSource:
    workspace: Path
    meta: dict[str, Any]


@dataclass(frozen=True)
class _ReviewContext:
    output_root: Path
    review_type: ReviewType
    task_type_name: str
    agent: AgentAdapter
    agent_name: str
    model: str
    reasoning_effort: str
    max_turns: int
    timeout: int
    dry_run: bool
    resume: bool
    skill_path: Path | None
    generation: dict[str, Any] | None


def _review_sources(
    input_root: Path,
    task_type_name: str,
    limit: int | None,
    target_ids: set[str] | None = None,
) -> list[_ReviewSource]:
    root = input_root / "workspaces"
    if not root.is_dir():
        root = input_root
    if not root.is_dir():
        raise ValueError(f"review input root not found: {root}")
    sources = []
    for workspace in sorted(path for path in root.iterdir() if path.is_dir()):
        meta = read_json_or_none(workspace / "_meta.json")
        if target_ids is not None and (
            not isinstance(meta, dict) or _marker_target_id(meta) not in target_ids
        ):
            continue
        if (
            isinstance(meta, dict)
            and meta.get("status") == "ok"
            and meta.get("validation_ok") is True
            and meta.get("task_type") == task_type_name
        ):
            for child, child_meta in built_tasks(workspace, meta):
                if not (child / "task").is_dir():
                    raise FileNotFoundError(f"缺少待审核的任务包：{child}")
                sources.append(_ReviewSource(child, child_meta))
                if limit and len(sources) == limit:
                    return sources
    if not sources:
        raise ValueError(
            f"no successful {task_type_name!r} taskgen workspaces under {root}"
        )
    if target_ids is not None and limit is None:
        missing = target_ids - {_review_id(source) for source in sources}
        if missing:
            raise ValueError(f"review input lacks successful tasks: {sorted(missing)}")
    return sources


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _review_id(source: _ReviewSource) -> str:
    return _marker_target_id(source.meta) or source.workspace.name


def _review_fingerprint(
    context: _ReviewContext,
    source: _ReviewSource,
) -> str:
    return Cache.key(
        "taskgen_review_input",
        "v1",
        context.task_type_name,
        _review_id(source),
        source.meta.get("task_variant"),
        _tree_sha256(source.workspace / "task"),
        context.review_type.fingerprint_material_for_source(
            source.workspace,
            source.meta,
            context.skill_path,
        ),
        context.agent_name,
        context.model,
        context.reasoning_effort,
        context.max_turns,
        context.timeout,
        context.generation,
    )


def _review_workspace(
    context: _ReviewContext,
    source: _ReviewSource,
) -> Path:
    name = context.review_type.workspace_name(source.workspace, source.meta)
    if not name or Path(name).name != name:
        raise ValueError(f"invalid review workspace name: {name!r}")
    return context.output_root / name


def _write_review_outcome(
    context: _ReviewContext,
    workspace: Path,
    outcome: TaskOutcome,
) -> TaskOutcome:
    write_marker(
        workspace / "_meta.json",
        outcome,
        agent=context.agent_name,
        model=context.model,
        reasoning_effort=context.reasoning_effort,
        task_type=context.task_type_name,
        generation=context.generation,
    )
    return outcome


def _resume_review(
    context: _ReviewContext,
    source: _ReviewSource,
    workspace: Path,
    fingerprint: str,
) -> TaskOutcome | None:
    if not context.resume:
        return None
    prior = read_json_or_none(workspace / "_meta.json") or {}
    if (
        prior.get("phase") == "review"
        and _marker_target_id(prior) == _review_id(source)
        and prior.get("task_type") == context.task_type_name
        and prior.get("agent") == context.agent_name
        and prior.get("model") == context.model
        and str(prior.get("reasoning_effort") or "") == context.reasoning_effort
        and prior.get("input_fingerprint") == fingerprint
        and prior.get("status") in {"ok", "rejected"}
        and prior.get("validation_ok") is True
    ):
        return TaskOutcome(
            target_id=_review_id(source),
            workspace=str(workspace),
            status="skipped",
            validation_ok=True,
            input_fingerprint=fingerprint,
            phase="review",
            source_workspace=str(source.workspace),
            report_status=str(prior.get("report_status") or ""),
            message="resume: already completed",
        )
    return None


def _run_review_one(
    context: _ReviewContext,
    source: _ReviewSource,
) -> TaskOutcome:
    target_id = _review_id(source)
    workspace = _review_workspace(context, source)
    fingerprint = ""
    started = time.time()
    try:
        fingerprint = _review_fingerprint(context, source)
        resumed = _resume_review(context, source, workspace, fingerprint)
        if resumed:
            return resumed
        prior = read_json_or_none(workspace / "_meta.json") or {}
        if (
            context.resume and not context.dry_run
            and context.agent.supports_session_resume
            and prior.get("status") in {"timeout", "failed"}
            and prior.get("input_fingerprint") == fingerprint
        ):
            result = context.agent.resume_interrupted(workspace, timeout=context.timeout)
            task_root = workspace / "task"
        else:
            _reset_workspace(context.output_root, workspace)
            prepared = context.review_type.prepare(
                source_workspace=source.workspace,
                workspace=workspace,
                source_meta=source.meta,
                skill_path=context.skill_path,
            )
            task_root = prepared.task_root
            result = execute_agent(
                agent=context.agent,
                prepared=prepared,
                task_type_name=f"{context.task_type_name}_review",
                model=context.model,
                reasoning_effort=context.reasoning_effort,
                max_turns=context.max_turns,
                timeout=context.timeout,
                proxy="",
                dry_run=context.dry_run,
            )
        outcome = TaskOutcome(
            target_id=target_id,
            workspace=str(workspace),
            status="dry_run" if result.ok and context.dry_run else result.status,
            agent_status=result.status,
            message=result.message,
            duration_s=result.duration_s,
            attempts=1,
            input_fingerprint=fingerprint,
            phase="review",
            source_workspace=str(source.workspace),
        )
        if result.ok and not context.dry_run:
            clean_generated_files(task_root)
            context.review_type.finalize(task_root, source_task_root=source.workspace / "task")
            validation = context.review_type.validate(task_root)
            outcome.report_status = context.review_type.report_status(
                task_root
            )
            outcome.validation_ok = validation.ok
            outcome.errors = [
                f"{issue.code}: {issue.message}" for issue in validation.errors
            ]
            outcome.warnings = [
                f"{issue.code}: {issue.message}" for issue in validation.warnings
            ]
            if not validation.ok:
                outcome.status = "invalid"
            elif outcome.report_status in {"accepted", "repaired"}:
                context.review_type.export_public(
                    task_root,
                    workspace / "public",
                )
                outcome.status = "ok"
            elif outcome.report_status == "failed":
                outcome.status = "rejected"
            else:
                outcome.status = "invalid"
        return _write_review_outcome(context, workspace, outcome)
    except Exception as exc:  # noqa: BLE001 - one bad task must not abort the batch
        message = f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:1000]
        outcome = TaskOutcome(
            target_id=target_id,
            workspace=str(workspace),
            status="error",
            errors=[message],
            message=message,
            duration_s=time.time() - started,
            input_fingerprint=fingerprint,
            phase="review",
            source_workspace=str(source.workspace),
        )
        workspace.mkdir(parents=True, exist_ok=True)
        return _write_review_outcome(context, workspace, outcome)


def run_review_batch(
    *,
    input_root: Path,
    output_root: Path,
    task_type_name: str,
    agent_name: str,
    model: str,
    reasoning_effort: str,
    jobs: int,
    max_turns: int,
    timeout: int,
    dry_run: bool = False,
    resume: bool = True,
    limit: int | None = None,
    target_ids: set[str] | None = None,
    skill_path: Path | None = None,
    generation: dict[str, Any] | None = None,
    on_progress: Callable[[int, int, TaskOutcome], None] | None = None,
    agent_adapter: AgentAdapter | None = None,
) -> list[TaskOutcome]:
    review_type = get_review_type(task_type_name)
    review_type.require_agent(agent_name)
    agent = agent_adapter or get_agent(agent_name)
    if not agent.uses_skill:
        raise ValueError(f"review agent must support skills: {agent_name}")
    sources = _review_sources(input_root, task_type_name, limit, target_ids)
    output_root.mkdir(parents=True, exist_ok=True)
    context = _ReviewContext(
        output_root=output_root,
        review_type=review_type,
        task_type_name=task_type_name,
        agent=agent,
        agent_name=agent_name,
        model=model,
        reasoning_effort=reasoning_effort,
        max_turns=max_turns,
        timeout=timeout,
        dry_run=dry_run,
        resume=resume,
        skill_path=skill_path,
        generation=generation,
    )
    return parallel_map(
        partial(_run_review_one, context),
        sources,
        workers=max(1, jobs),
        on_done=on_progress,
    )


def summarize(outcomes: list[TaskOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    return counts
