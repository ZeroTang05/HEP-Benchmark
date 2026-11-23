"""HEP 运行框架、任务合同与审核合同注册表。"""
from __future__ import annotations

import importlib
from typing import Type

from researchfactory.core.registry import register_named_class, registered_module_classes
from researchfactory.taskgen.core.types import (
    AgentAdapter,
    ReviewType,
    TaskType,
)

# Default modules to import so the registries populate. Import errors propagate.
_DEFAULT_MODULES = (
    "researchfactory.taskgen.agents.deepseek_harness",
    "researchfactory.taskgen.tasks.researchclawbench.initializer",
    "researchfactory.taskgen.tasks.researchclawbench.reviewer",
)
_DEFAULT_AGENTS = {"deepseek_harness"}
_DEFAULT_TASK_TYPES = {"researchclawbench"}
_DEFAULT_REVIEW_TYPES = {"researchclawbench"}
_imported = False


_AGENTS: dict[str, Type[AgentAdapter]] = {}
_TASK_TYPES: dict[str, Type[TaskType]] = {}
_REVIEW_TYPES: dict[str, Type[ReviewType]] = {}


# ---- agents ---------------------------------------------------------------
def register_agent(cls: Type[AgentAdapter]) -> Type[AgentAdapter]:
    return register_named_class(
        _AGENTS, cls, base=AgentAdapter, kind="taskgen agent"
    )


def get_agent(name: str) -> AgentAdapter:
    import_defaults()
    if name not in _AGENTS:
        valid = ", ".join(sorted(_AGENTS)) or "(none)"
        raise ValueError(f"unknown taskgen agent {name!r}; valid: {valid}")
    return _AGENTS[name]()


def available_agents() -> list[str]:
    import_defaults()
    return sorted(_AGENTS)


# ---- task types -----------------------------------------------------------
def register_task_type(cls: Type[TaskType]) -> Type[TaskType]:
    return register_named_class(
        _TASK_TYPES, cls, base=TaskType, kind="taskgen task type"
    )


def get_task_type(name: str) -> TaskType:
    import_defaults()
    if name not in _TASK_TYPES:
        valid = ", ".join(sorted(_TASK_TYPES)) or "(none)"
        raise ValueError(f"unknown taskgen task_type {name!r}; valid: {valid}")
    return _TASK_TYPES[name]()


def available_task_types() -> list[str]:
    import_defaults()
    return sorted(_TASK_TYPES)


# ---- review types ---------------------------------------------------------
def register_review_type(cls: Type[ReviewType]) -> Type[ReviewType]:
    return register_named_class(
        _REVIEW_TYPES,
        cls,
        base=ReviewType,
        kind="taskgen review type",
    )


def get_review_type(name: str) -> ReviewType:
    import_defaults()
    if name not in _REVIEW_TYPES:
        valid = ", ".join(sorted(_REVIEW_TYPES)) or "(none)"
        raise ValueError(f"unknown taskgen review type {name!r}; valid: {valid}")
    return _REVIEW_TYPES[name]()


def available_review_types() -> list[str]:
    import_defaults()
    return sorted(_REVIEW_TYPES)


# ---- judgement types -----------------------------------------------------






def _register_module_classes(module: object) -> None:
    """Restore registrations if tests clear registries after modules are imported."""
    for cls in registered_module_classes(module, AgentAdapter):
        register_agent(cls)
    for cls in registered_module_classes(module, TaskType):
        register_task_type(cls)
    for cls in registered_module_classes(module, ReviewType):
        register_review_type(cls)


def import_defaults() -> None:
    global _imported
    defaults_present = (
        _DEFAULT_AGENTS.issubset(_AGENTS)
        and _DEFAULT_TASK_TYPES.issubset(_TASK_TYPES)
        and _DEFAULT_REVIEW_TYPES.issubset(_REVIEW_TYPES)
    )
    if _imported and defaults_present:
        return
    for mod in _DEFAULT_MODULES:
        module = importlib.import_module(mod)
        _register_module_classes(module)
    _imported = True
