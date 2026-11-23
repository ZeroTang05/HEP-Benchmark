"""Small orchestration steps for taskgen batch runs."""
from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from researchfactory.taskgen.core.types import (
    AgentAdapter,
    PreparedTaskWorkspace,
    TaskGenJob,
    TaskgenPlan,
)
from researchfactory.utils.storage import atomic_write_json


def built_tasks(workspace: Path, meta: dict[str, Any]) -> Iterator[tuple[Path, dict[str, Any]]]:
    """展开任务组，保留论文标识，用独立目录名区分同一篇的各题。"""
    if not meta.get("tasks"):
        yield workspace, meta
        return
    for variant in meta["tasks"]:
        key = variant["id"]
        yield workspace / "tasks" / key, {
            **meta, "task_variant": variant, "paper_workspace": str(workspace),
            "task_workspace_name": f"{workspace.name}--{key}",
        }


def initialize_workspace(
    *,
    task_type: Any,
    target: dict[str, Any],
    workspace: Path,
    seed: Any,
    skill_path: Path | None,
) -> PreparedTaskWorkspace:
    """Stage a workspace through the selected task type."""
    return task_type.prepare(target=target, workspace=workspace, seed=seed, skill_path=skill_path)


def execute_agent(
    *,
    agent: AgentAdapter,
    prepared: PreparedTaskWorkspace,
    task_type_name: str,
    model: str,
    reasoning_effort: str,
    max_turns: int,
    timeout: int,
    proxy: str,
    dry_run: bool,
) -> Any:
    """Run one prepared job through the selected agent adapter."""
    job = TaskGenJob(
        target=prepared.target,
        workspace=prepared.workspace,
        prompt=prepared.prompt,
        task_type=task_type_name,
        model=model,
        reasoning_effort=reasoning_effort,
        system_prompt=prepared.system_prompt,
        skill_dir=prepared.skill_dir,
        skill_name=prepared.skill_name,
        additional_skill_dirs=prepared.additional_skill_dirs,
        secret_env_names=prepared.secret_env_names,
        max_turns=max_turns,
        timeout=timeout,
        proxy=proxy,
        dry_run=dry_run,
        read_only_paths=prepared.read_only_paths,
        tmpfs_paths=prepared.tmpfs_paths,
        extra=prepared.meta,
    )
    return agent.generate(job)


def clean_generated_files(task_root: Path) -> None:
    """Remove Python bytecode accidentally produced while constructing a package."""
    for path in task_root.rglob("*.pyc"):
        if not path.is_symlink():
            path.unlink()
    cache_dirs = sorted(
        (
            path
            for path in task_root.rglob("__pycache__")
            if path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in cache_dirs:
        shutil.rmtree(path)


def validate_and_export(
    *,
    task_type: Any,
    task_root: Path,
    public_root: Path,
) -> tuple[str, bool, list[str], list[str]]:
    """Validate a completed package and export public files when valid."""
    clean_generated_files(task_root)
    vres = task_type.validate(task_root)
    errors = [f"{i.code}: {i.message}" for i in vres.errors]
    warnings = [f"{i.code}: {i.message}" for i in vres.warnings]
    terminal_status = task_type.terminal_status(task_root) if vres.ok else "invalid"
    if terminal_status == "ok":
        task_type.export_public(task_root, public_root)
    return terminal_status, bool(vres.ok), errors, warnings


def write_marker(
    marker: Path,
    outcome: Any,
    *,
    agent: str,
    model: str,
    reasoning_effort: str,
    task_type: str,
    plan: TaskgenPlan | None = None,
    generation: dict[str, Any] | None = None,
) -> None:
    """Write the per-task metadata marker."""
    payload = asdict(outcome)
    payload.update({
        "agent": agent,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "task_type": task_type,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    if generation:
        payload["generation"] = dict(generation)
    if plan is not None:
        # Keep the marker deliberately small.  The full stable contract lives
        # in taskgen_plans/<task_type>.jsonl; these fields are enough to trace a
        # generated package back to it.
        payload.update({
            "plan_id": plan.plan_id,
            "catalog_version": plan.catalog_version,
            "primary_capability_id": plan.primary,
        })
    atomic_write_json(marker, payload)
