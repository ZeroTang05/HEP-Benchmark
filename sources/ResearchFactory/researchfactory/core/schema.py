"""HEP 任务生成的配置结构和默认值。"""
from dataclasses import dataclass, field


@dataclass
class PathsSchema:
    """论文清单、运行结果和内容缓存的位置。"""
    run_dir: str = "runs/ams02-hep-taskgen"
    cache_dir: str = ".cache/researchfactory-mineru-rebuild-20261118"
    cache_schema: str = "v2"
    target_pool: str = "inputs/ams02-publications/target_study_pool.jsonl"
    papers_capability_index: str = "inputs/paper_capability_index.jsonl"


@dataclass
class AgentConfigSchema:
    """模型参数由本机的模型配置覆盖。"""
    model: str = "deepseek-chat"
    reasoning_effort: str = "high"


@dataclass
class ReviewSchema:
    """独立审核阶段的资源限制。"""
    agent: str = "deepseek_harness"
    model: str = ""
    reasoning_effort: str = "high"
    jobs: int = 1
    task_timeout: int = 1800
    max_turns: int = 80
    skill_path: str = ""


@dataclass
class TaskgenSchema:
    """任务构建与审核共用 DeepSeek Harness。"""
    agent: str = "deepseek_harness"
    task_type: str = "researchclawbench"
    agent_configs: dict[str, AgentConfigSchema] = field(default_factory=lambda: {
        "deepseek_harness": AgentConfigSchema(),
    })
    review: ReviewSchema = field(default_factory=ReviewSchema)
    jobs: int = 1
    task_timeout: int = 7200
    max_turns: int = 200
    skill_path: str = ""


@dataclass
class RootSchema:
    """本机 HEP 项目支持的全部非敏感配置。"""
    paths: PathsSchema = field(default_factory=PathsSchema)
    taskgen: TaskgenSchema = field(default_factory=TaskgenSchema)
