"""读取 HEP 配置，解析本机路径，使用标准 OmegaConf 处理命令参数。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from omegaconf import OmegaConf

from researchfactory.core.schema import RootSchema

_PACKAGE = Path(__file__).resolve().parents[1]
_PROJECT = _PACKAGE.parents[2]


@dataclass(frozen=True)
class Paths:
    """统一使用绝对路径，避免运行目录影响论文与结果定位。"""
    run_dir: Path
    cache_dir: Path
    cache_schema: str
    target_pool: Path
    papers_capability_index: Path

    @property
    def taskgen_plans_dir(self) -> Path:
        return self.run_dir / "taskgen_plans"


@dataclass(frozen=True)
class TaskgenAgentConfig:
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class TaskgenReviewSettings:
    agent: str
    model: str
    reasoning_effort: str
    jobs: int
    task_timeout: int
    max_turns: int
    skill_path: Path | None


@dataclass(frozen=True)
class TaskgenSettings:
    agent: str
    task_type: str
    agent_configs: Mapping[str, TaskgenAgentConfig]
    review: TaskgenReviewSettings
    jobs: int
    task_timeout: int
    max_turns: int
    skill_path: Path | None

    def agent_config(self, name: str) -> TaskgenAgentConfig:
        """未知运行框架在启动前直接报错。"""
        return self.agent_configs[name]


@dataclass(frozen=True)
class Settings:
    paths: Paths
    taskgen: TaskgenSettings


def compose(overrides: Sequence[str] | None = None, *, apply_compat: bool = True,
            environ: Mapping[str, str] | None = None) -> Any:
    """合并结构、项目配置、运行路径和命令行的 key=value 参数。"""
    env = os.environ if environ is None else environ
    cfg = OmegaConf.merge(OmegaConf.structured(RootSchema), OmegaConf.load(_PACKAGE / "config/taskgen.yaml"))
    if apply_compat:
        paths = {field: env[key] for field, key in (("cache_dir", "RF_CACHE_DIR"), ("run_dir", "RF_RUN_DIR")) if env.get(key)}
        cfg = OmegaConf.merge(cfg, {"paths": paths})
    if overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(list(overrides)))
    return cfg


def load_config(overrides: Sequence[str] | None = None, *, apply_compat: bool = True,
                environ: Mapping[str, str] | None = None) -> Settings:
    """返回带类型的运行配置；不从配置文件读取任何密钥。"""
    env = os.environ if environ is None else environ
    root = Path(env.get("RESEARCHFACTORY_BUNDLE_ROOT", str(_PROJECT))).resolve()
    data = OmegaConf.to_container(compose(overrides, apply_compat=apply_compat, environ=env), resolve=True)

    def path(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()

    def optional(value: str) -> Path | None:
        return path(value) if value else None

    task = data["taskgen"]
    review = task["review"]
    if task["agent"] != "deepseek_harness" or review["agent"] != "deepseek_harness":
        raise ValueError("HEP 流水线使用 deepseek_harness")
    if task["task_type"] != "researchclawbench":
        raise ValueError("HEP 任务使用 researchclawbench 数据合同")
    if min(task["jobs"], review["jobs"], task["task_timeout"], review["task_timeout"]) <= 0:
        raise ValueError("并发数与超时时间必须大于零")
    raw_paths = data["paths"]
    return Settings(
        paths=Paths(**{key: value if key == "cache_schema" else path(value) for key, value in raw_paths.items()}),
        taskgen=TaskgenSettings(
            agent=task["agent"], task_type=task["task_type"],
            agent_configs={name: TaskgenAgentConfig(**entry) for name, entry in task["agent_configs"].items()},
            review=TaskgenReviewSettings(**{**review, "skill_path": optional(review["skill_path"])}),
            jobs=task["jobs"], task_timeout=task["task_timeout"], max_turns=task["max_turns"],
            skill_path=optional(task["skill_path"]),
        ),
    )


def config_yaml(overrides: Sequence[str] | None = None, *, apply_compat: bool = True) -> str:
    """显示非敏感配置。"""
    return OmegaConf.to_yaml(compose(overrides, apply_compat=apply_compat))
