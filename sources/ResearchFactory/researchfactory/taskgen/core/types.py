"""Core task-generation interfaces and value types.

Task generation has four independent extension points:

* agent adapters execute an external agent in a prepared workspace,
* task types initialize and validate task packages,
* reviewers inspect completed task packages through independent agents.
* judges compare completed packages with their source generation without
  changing either package.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from researchfactory.core.cache import Cache


CAPABILITY_LEVELS = ("basic", "applied", "integrated", "advanced")


@dataclass
class TaskGenJob:
    """One unit of work: build a task package for ``target`` in ``workspace``."""

    target: dict[str, Any]            # a target_study_pool.jsonl row
    workspace: Path                   # fresh, repo-external working dir
    prompt: str                       # user prompt built by the task type
    task_type: str = "researchclawbench"
    model: str = ""                   # agent model (e.g. gpt-5.6-sol)
    reasoning_effort: str = ""        # low | medium | high | xhigh | max
    system_prompt: Optional[str] = None
    skill_dir: Optional[Path] = None   # staged native skill/plugin directory for compatible agents
    skill_name: str = ""              # slash-invoked skill name, when applicable
    additional_skill_dirs: tuple[Path, ...] = ()
    secret_env_names: tuple[str, ...] = ()
    max_turns: int = 150
    timeout: int = 1800               # wall-clock seconds; 0 => no timeout
    proxy: str = ""                   # 子进程网络设置，由运行框架处理
    dry_run: bool = False             # prepare workspace/prompt but do not call the agent
    read_only_paths: tuple[Path, ...] = ()
    tmpfs_paths: tuple[Path, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def target_id(self) -> str:
        return str(
            self.target.get("target_id")
            or self.target.get("paper_id")
            or ""
        )


@dataclass
class AgentResult:
    """Outcome of a single generation attempt."""

    status: str                       # ok | failed | timeout | error | skipped
    returncode: Optional[int] = None
    message: str = ""
    duration_s: float = 0.0
    workspace: Optional[Path] = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class PreparedTaskWorkspace:
    """A staged workspace ready for an agent adapter."""

    target: dict[str, Any]
    workspace: Path
    task_root: Path
    prompt: str
    system_prompt: str | None = None
    skill_dir: Path | None = None
    skill_name: str = ""
    additional_skill_dirs: tuple[Path, ...] = ()
    secret_env_names: tuple[str, ...] = ()
    read_only_paths: tuple[Path, ...] = ()
    tmpfs_paths: tuple[Path, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(ABC):
    """Base class for pluggable task-generation agents."""

    name: str = ""
    uses_skill: bool = False
    supports_session_resume: bool = False

    def resume_interrupted(self, workspace: Path, *, timeout: int) -> AgentResult:
        """恢复已超时的原生会话；仅支持持久会话的适配器实现此接口。"""
        raise NotImplementedError(f"{self.name} does not support session resume")

    def resolve_prompts(self, job: TaskGenJob) -> tuple[str | None, str]:
        """Return the system and user prompts used for this job."""
        return job.system_prompt, job.prompt

    @abstractmethod
    def generate(self, job: TaskGenJob) -> AgentResult:
        """Run the agent for ``job`` and return an :class:`AgentResult`."""
        raise NotImplementedError


@dataclass
class SeedResolution:
    """The source artifact a task type grounds a package in.

    Task types differ in what "seeds" a package: the papers track resolves a
    目标论文 PDF（``kind="pdf"``），路径保存在 ``path`` 中。
    ``ok=False`` tells the runner to skip the row with
    ``skip_reason`` (e.g. ``"no_pdf"``).
    """

    ok: bool = True
    kind: str = "none"                 # none | pdf | image | repo_snapshot | ...
    path: Optional[Path] = None        # local file, when the seed is on disk
    ref: str = ""                      # remote reference (e.g. docker image)
    meta: dict[str, Any] = field(default_factory=dict)
    skip_reason: str = ""              # set when ok is False


@dataclass(frozen=True)
class CapabilityRequestSlot:
    """One quota in a capability-aware task-generation request.

    ``count`` is expanded into individual deterministic plan slots by the
    planner.  Context and resource constraints remain deliberately generic:
    they describe artifact properties, not nodes in the capability catalog.
    """

    primary: str
    count: int = 1
    target_level: str = "applied"
    context: dict[str, Any] = field(default_factory=dict)
    resource_constraints: dict[str, Any] = field(default_factory=dict)
    construction_mode: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "primary": self.primary,
            "count": self.count,
            "target_level": self.target_level,
            "context": dict(self.context),
            "resource_constraints": dict(self.resource_constraints),
            "construction_mode": self.construction_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityRequestSlot":
        resources = data.get("resource_constraints")
        if resources is None:
            resources = data.get("resources") or {}
        return cls(
            name=str(data.get("name") or data.get("id") or "").strip(),
            primary=str(data.get("primary") or data.get("capability") or "").strip(),
            count=int(data.get("count", 1)),
            target_level=str(data.get("target_level") or data.get("level") or "applied").strip(),
            context=dict(data.get("context") or {}),
            resource_constraints=dict(resources or {}),
            construction_mode=str(
                data.get("construction_mode") or data.get("mode") or ""
            ).strip(),
        )


@dataclass(frozen=True)
class CapabilityRequest:
    """Normalized contents of one capability request YAML/JSON document."""

    task_type: str
    slots: tuple[CapabilityRequestSlot, ...]
    catalog_version: str = ""
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "task_type": self.task_type,
            "catalog_version": self.catalog_version,
            "slots": [slot.to_dict() for slot in self.slots],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityRequest":
        raw_slots = data.get("slots") or data.get("requests") or data.get("capabilities") or []
        if isinstance(raw_slots, dict):
            normalized: list[dict[str, Any]] = []
            for primary, value in raw_slots.items():
                if isinstance(value, dict):
                    row = dict(value)
                    row.setdefault("primary", primary)
                else:
                    row = {"primary": primary, "count": value}
                normalized.append(row)
            raw_slots = normalized
        if not isinstance(raw_slots, (list, tuple)):
            raise ValueError("capability request slots must be a list or mapping")
        return cls(
            request_id=str(data.get("request_id") or data.get("id") or "").strip(),
            task_type=str(data.get("task_type") or "").strip(),
            catalog_version=str(data.get("catalog_version") or "").strip(),
            slots=tuple(CapabilityRequestSlot.from_dict(dict(row)) for row in raw_slots),
        )


@dataclass(frozen=True)
class TaskgenPlan:
    """Stable contract for one matched or unsatisfied task-generation slot."""

    plan_id: str
    request_id: str
    slot_id: str
    status: str                       # matched | unsatisfied
    task_type: str
    catalog_version: str
    primary: str
    target_level: str
    construction_mode: str
    artifact_id: str = ""
    secondary: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    requested_context: dict[str, Any] = field(default_factory=dict)
    resource_constraints: dict[str, Any] = field(default_factory=dict)
    capability_context: dict[str, Any] = field(default_factory=dict)
    target: dict[str, Any] = field(default_factory=dict)
    unsatisfied_reason: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("plan_id", "request_id", "slot_id", "task_type", "primary"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"TaskgenPlan.{field_name} must be non-empty")
            object.__setattr__(self, field_name, value)
        if self.status not in {"matched", "unsatisfied"}:
            raise ValueError("TaskgenPlan.status must be 'matched' or 'unsatisfied'")
        if self.target_level not in CAPABILITY_LEVELS:
            raise ValueError(
                f"invalid TaskgenPlan.target_level {self.target_level!r}; "
                f"expected one of {', '.join(CAPABILITY_LEVELS)}"
            )

        for field_name in ("secondary", "prerequisites", "forbidden"):
            values: list[str] = []
            for raw in getattr(self, field_name):
                value = str(raw or "").strip()
                if value and value not in values:
                    values.append(value)
            object.__setattr__(self, field_name, tuple(values))
        if len(self.secondary) > 3:
            raise ValueError("TaskgenPlan allows at most three secondary capabilities")
        if self.primary in self.secondary:
            raise ValueError("TaskgenPlan primary cannot also be secondary")
        selected = {self.primary, *self.secondary, *self.prerequisites}
        conflicts = selected.intersection(self.forbidden)
        if conflicts:
            raise ValueError(
                "TaskgenPlan forbidden capabilities conflict with selected capabilities: "
                + ", ".join(sorted(conflicts))
            )

        for field_name in (
            "requested_context", "resource_constraints", "capability_context",
            "target", "diagnostics",
        ):
            object.__setattr__(self, field_name, dict(getattr(self, field_name) or {}))
        if self.matched:
            if not str(self.artifact_id or "").strip():
                raise ValueError("matched TaskgenPlan requires artifact_id")
            if not self.target:
                raise ValueError("matched TaskgenPlan requires the Artifact Pool target row")
        elif not str(self.unsatisfied_reason or "").strip():
            raise ValueError("unsatisfied TaskgenPlan requires unsatisfied_reason")

    @property
    def matched(self) -> bool:
        return self.status == "matched"

    def capability_payload(self) -> dict[str, Any]:
        """Return planning metadata suitable for prompts and run markers."""
        return {
            "plan_id": self.plan_id,
            "request_id": self.request_id,
            "slot_id": self.slot_id,
            "catalog_version": self.catalog_version,
            "primary": self.primary,
            "secondary": list(self.secondary),
            "prerequisites": list(self.prerequisites),
            "forbidden": list(self.forbidden),
            "target_level": self.target_level,
            "construction_mode": self.construction_mode,
            "requested_context": dict(self.requested_context),
            "resource_constraints": dict(self.resource_constraints),
            "capability_context": dict(self.capability_context),
        }

    def apply_to_target(self) -> dict[str, Any]:
        """Return a runner-ready pool row without task-type-specific branches."""
        if not self.matched:
            raise ValueError(f"cannot run unsatisfied taskgen plan {self.plan_id}")
        row = dict(self.target)
        row["taskgen_plan"] = self.capability_payload()
        return row

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "request_id": self.request_id,
            "slot_id": self.slot_id,
            "status": self.status,
            "task_type": self.task_type,
            "catalog_version": self.catalog_version,
            "artifact_id": self.artifact_id,
            "primary": self.primary,
            "secondary": list(self.secondary),
            "prerequisites": list(self.prerequisites),
            "forbidden": list(self.forbidden),
            "target_level": self.target_level,
            "construction_mode": self.construction_mode,
            "requested_context": dict(self.requested_context),
            "resource_constraints": dict(self.resource_constraints),
            "capability_context": dict(self.capability_context),
            "target": dict(self.target),
            "unsatisfied_reason": self.unsatisfied_reason,
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskgenPlan":
        return cls(
            plan_id=str(data.get("plan_id") or ""),
            request_id=str(data.get("request_id") or ""),
            slot_id=str(data.get("slot_id") or ""),
            status=str(data.get("status") or ""),
            task_type=str(data.get("task_type") or ""),
            catalog_version=str(data.get("catalog_version") or ""),
            artifact_id=str(data.get("artifact_id") or ""),
            primary=str(data.get("primary") or ""),
            secondary=tuple(str(x) for x in data.get("secondary") or ()),
            prerequisites=tuple(str(x) for x in data.get("prerequisites") or ()),
            forbidden=tuple(str(x) for x in data.get("forbidden") or ()),
            target_level=str(data.get("target_level") or ""),
            construction_mode=str(data.get("construction_mode") or ""),
            requested_context=dict(data.get("requested_context") or {}),
            resource_constraints=dict(data.get("resource_constraints") or {}),
            capability_context=dict(data.get("capability_context") or {}),
            target=dict(data.get("target") or {}),
            unsatisfied_reason=str(data.get("unsatisfied_reason") or data.get("reason") or ""),
            diagnostics=dict(data.get("diagnostics") or {}),
        )


class TaskType(ABC):
    """Base class for task-package types."""

    name: str = ""
    capability_track: str = ""

    @abstractmethod
    def artifact_pool_path(self, settings: Any) -> Path:
        """Return this task type's default Artifact Pool path."""
        raise NotImplementedError

    @abstractmethod
    def capability_index_path(self, settings: Any) -> Path:
        """Return this task type's optional capability-index path."""
        raise NotImplementedError

    def target_id(self, target: dict[str, Any]) -> str:
        """Stable id for a pool row (used for the workspace slug + marker).

        Explicit ``target_id`` values take precedence. Tracks keyed on a
        domain field such as ``paper_id`` or environment ``identity`` may
        override this method.
        """
        return str(target.get("target_id") or target.get("paper_id") or "")

    def workspace_name(self, target: dict[str, Any]) -> str:
        """Return a task-specific workspace name, or empty for the default."""
        return ""

    def require_agent(self, agent_name: str) -> None:
        """Raise when this task type does not support the selected agent."""
        _ = agent_name

    def fingerprint_material(
        self,
        target: dict[str, Any],
        skill_path: Path | None = None,
    ) -> Any:
        """Return adapter-owned material that invalidates build resume state."""
        _ = target
        return str(skill_path or "")

    def resolve_seed(
        self,
        *,
        settings: Any,
        cache: Cache,
        target: dict[str, Any],
    ) -> SeedResolution:
        """Resolve the source artifact for ``target``.

        The default is a no-op seed (nothing to fetch). Task types that need a
        local file (a PDF) or a remote reference (a Docker image) override this
        and may return ``ok=False`` to skip the row.
        """
        return SeedResolution(ok=True, kind="none")

    def seed_resolver(
        self,
        *,
        settings: Any,
        cache: Cache,
        targets: Sequence[dict[str, Any]],
    ) -> Callable[[dict[str, Any]], SeedResolution]:
        """Create the resolver shared by one task-generation batch."""
        _ = targets

        def resolve(target: dict[str, Any]) -> SeedResolution:
            return self.resolve_seed(
                settings=settings,
                cache=cache,
                target=target,
            )

        return resolve

    @abstractmethod
    def prepare(
        self,
        *,
        target: dict[str, Any],
        workspace: Path,
        seed: SeedResolution,
        skill_path: Path | None = None,
    ) -> PreparedTaskWorkspace:
        """Stage ``workspace`` and return the agent entry prompt."""
        raise NotImplementedError

    @abstractmethod
    def build_prompt(self, target: dict[str, Any]) -> str:
        """Build the entry prompt without touching the filesystem."""
        raise NotImplementedError

    @abstractmethod
    def validate(self, task_root: Path) -> Any:
        """Validate a completed task package."""
        raise NotImplementedError

    def terminal_status(self, task_root: Path) -> str:
        """Return the normal terminal status of a valid package."""
        return "ok"

    def task_series(self, workspace: Path, target: dict[str, Any]) -> list[dict[str, Any]]:
        """返回校验通过、按顺序排列的子任务。单题模式返回空列表。"""
        return []

    @abstractmethod
    def export_public(self, task_root: Path, public_root: Path) -> list[Path]:
        """Export Solver-visible files from a completed task package."""
        raise NotImplementedError


class ReviewType(ABC):
    """Task-specific adapter for independent package review and repair."""

    name: str = ""

    def require_agent(self, agent_name: str) -> None:
        """Reject agent backends that this task type does not support."""
        _ = agent_name

    def fingerprint_material(self, skill_path: Path | None = None) -> Any:
        """Return adapter-owned material that invalidates resume state."""
        return str(skill_path or "")

    def fingerprint_material_for_source(
        self,
        source_workspace: Path,
        source_meta: dict[str, Any],
        skill_path: Path | None = None,
    ) -> Any:
        """Return source-aware material for task families with phase policies."""
        _ = source_workspace, source_meta
        return self.fingerprint_material(skill_path)

    @abstractmethod
    def workspace_name(
        self,
        source_workspace: Path,
        source_meta: dict[str, Any],
    ) -> str:
        """Return the output workspace name for one source taskgen workspace."""
        raise NotImplementedError

    @abstractmethod
    def prepare(
        self,
        *,
        source_workspace: Path,
        workspace: Path,
        source_meta: dict[str, Any],
        skill_path: Path | None = None,
    ) -> PreparedTaskWorkspace:
        """Copy and stage one successful task package for the Reviewer."""
        raise NotImplementedError

    @abstractmethod
    def validate(self, task_root: Path) -> Any:
        """Validate a completed or explicitly rejected review workspace."""
        raise NotImplementedError

    def finalize(self, task_root: Path, *, source_task_root: Path | None = None) -> None:
        """用审核产物和原始题包记录程序校验结果。"""
        _ = task_root, source_task_root

    def export_public(self, task_root: Path, public_root: Path) -> list[Path]:
        """Export an accepted or repaired package for downstream use."""
        _ = task_root, public_root
        return []

    @abstractmethod
    def report_status(self, task_root: Path) -> str:
        """Return the task-specific review report status."""
        raise NotImplementedError


