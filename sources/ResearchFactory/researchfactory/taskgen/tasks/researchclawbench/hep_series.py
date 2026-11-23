"""读取一篇论文的任务清单，按科研步骤交给下游逐题处理。"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from researchfactory.recall.paper_assets import sha256_file
from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT, SOLVER_VISIBLE
from researchfactory.taskgen.tasks.researchclawbench.hep_evaluator import CONTRACT as EVALUATOR
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import (
    CAPABILITIES, CAPABILITY_SCHEMA, rubric_enabled, rubric_path, rubric_capabilities,
)
from researchfactory.taskgen.tasks.researchclawbench.reviewer import _hidden_key_leaks

MANIFEST = "task_series.json"
SUBMISSION = "task_series.submitted.json"
DELEGATION = "task_series.delegation.json"
EARLY_STAGES = ("framing", "hypothesis", "design")
STAGES = (*EARLY_STAGES, "measurement", "comparison", "inference", "robustness")
TASK_KEY = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def uses_series(target: dict[str, Any]) -> bool:
    """论文自由抽题使用任务组；明确指定的单题计划仍只生成指定任务。"""
    return target.get("hep_task_plan") is None and target.get("taskgen_plan") is None


def task_workspace(workspace: Path, key: str) -> Path:
    if not TASK_KEY.fullmatch(key) or len(key) > 48:
        raise ValueError(f"无效的任务短名：{key!r}")
    return workspace / "tasks" / key


def initialize_task(workspace: Path, key: str) -> Path:
    """为一题复制公共模板和原始论文，模型只需填写该题的内容。"""
    root = task_workspace(workspace, key) / "task"
    shutil.copytree(workspace / "task", root)
    return root


def read_series(
    workspace: Path, *, require_decision_checks: bool = False, require_packages: bool = True,
) -> list[dict[str, Any]]:
    """读取所选题目与科研顺序；未入选的草稿留在工作区。"""
    manifest = json.loads((workspace / MANIFEST).read_text(encoding="utf-8"))
    template = workspace / "task"
    capability_series = rubric_enabled(template) and (
        json.loads(rubric_path(template).read_text(encoding="utf-8"))["schema_version"] == CAPABILITY_SCHEMA
    )
    rows = manifest["tasks"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("task_series.json 的 tasks 必须是非空数组")
    if len(rows) == 1 and not str(manifest.get("single_task_reason") or "").strip():
        raise ValueError("只交付一题时，须在 single_task_reason 说明其他问题为何不能独立作答和评分")
    keys: set[str] = set()
    questions: set[str] = set()
    result = []
    previous = -1
    task_dir = workspace / "tasks"
    if task_dir.is_symlink():
        raise ValueError("tasks/ 必须是工作区内的实际目录")
    for order, row in enumerate(rows, 1):
        if any(not isinstance(row[field], str) or not row[field].strip()
               for field in ("id", "stage", "question", "evidence")):
            raise ValueError(f"第 {order} 题的 id、stage、question、evidence 必须是非空字符串")
        key = row["id"]
        if row["stage"] not in STAGES:
            raise ValueError(f"第 {order} 题的 stage {row['stage']!r} 无效，可用值：{', '.join(STAGES)}")
        stage = STAGES.index(row["stage"])
        question = " ".join(row["question"].casefold().split())
        if key in keys or question in questions or stage < previous:
            raise ValueError(f"任务短名或问题重复，或科研阶段顺序不符：{key}")
        child = task_workspace(workspace, key)
        if require_packages and (child.is_symlink() or not (child / "task").is_dir()):
            raise ValueError(f"任务目录缺失：{child / 'task'}")
        keys.add(key)
        questions.add(question)
        previous = stage
        fields = (
            "id", "stage", "question", "public_context", "evidence", "method", "decision_output",
            "decision_checks", "capabilities",
        )
        if row["stage"] in EARLY_STAGES or "public_context" in row:
            context = row.get("public_context")
            if not isinstance(context, str) or not context.strip():
                raise ValueError(f"第 {order} 题的 public_context 必须说明解题者可获得的背景和约束")
        for field in ("method", "decision_output"):
            if require_decision_checks or field in row:
                if isinstance(row.get(field), str) and row[field].strip():
                    continue
                raise ValueError(f"第 {order} 题的 {field} 必须是非空字符串")
        if require_decision_checks or "decision_checks" in row:
            checks = row.get("decision_checks")
            if not isinstance(checks, list) or not checks or any(
                not isinstance(check, str) or not check.strip() for check in checks
            ) or len(checks) != len(set(checks)):
                raise ValueError(f"第 {order} 题的 decision_checks 必须列出不重复的检查名称")
            if row.get("decision_output") in checks:
                raise ValueError(f"第 {order} 题的 decision_output 须描述答题者的实际交付物")
        if capability_series or "capabilities" in row:
            capabilities = row.get("capabilities")
            if (not isinstance(capabilities, list) or not capabilities
                    or any(name not in CAPABILITIES for name in capabilities)
                    or len(capabilities) != len(set(capabilities))):
                raise ValueError(f"第 {order} 题必须列出实际考查的能力，且不得重复")
        result.append({**{field: row[field] for field in fields if field in row}, "order": order})
    return result


def prepare_delegation(workspace: Path) -> list[dict[str, Any]]:
    """先检查整份定题清单，再给每道题复制一份空题包。"""
    rows = read_series(workspace, require_decision_checks=True, require_packages=False)
    # 用一个新目录接收委派，避免复用旧题包或从同一清单重复启动。
    (workspace / "tasks").mkdir()
    for row in rows:
        initialize_task(workspace, row["id"])
    return rows


def require_delegated_submission(workspace: Path, rows: list[dict[str, Any]]) -> None:
    """只有每道题对应一个已完成的子会话，且整组通过校验，才能导出。"""
    if not (workspace / DELEGATION).is_file() or not (workspace / SUBMISSION).is_file():
        raise ValueError("缺少 DSH 子会话完成记录或任务组提交标记")
    receipt = json.loads((workspace / DELEGATION).read_text(encoding="utf-8"))
    submitted = json.loads((workspace / SUBMISSION).read_text(encoding="utf-8"))["tasks"]
    ids = [row["id"] for row in rows]
    children = receipt["tasks"]
    sessions = [child["session_id"] for child in children]
    if (
        receipt["plan"] != rows or submitted != ids or [child["task_id"] for child in children] != ids
        or not receipt["parent_session_id"]
        or any(child["stop_reason"] != "completed" for child in children)
        or any(not session or session == receipt["parent_session_id"] for session in sessions)
        or len(set(sessions)) != len(ids)
    ):
        raise ValueError("任务清单、DSH 子会话完成记录与整组提交不一致")


def validate_series_task(
    root: Path, paper: Path, *, decision_checks: list[str] | None = None,
    capabilities: list[str] | None = None, require_hep_package: bool = False,
) -> list[str]:
    """沿用单题合同，并检查任务没有引用相邻题目或私有文件。"""
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        return ["任务包须包含实际文件，不能使用符号链接"]
    validation = CONTRACT.validate(root, require_hep_package=require_hep_package)
    errors = [f"{issue.code}: {issue.message}" for issue in validation.errors]
    if errors:
        return errors
    info = json.loads((root / "task_info.json").read_text(encoding="utf-8"))
    if info.get("hep_data_contract") != "v1":
        errors.append('缺少 hep_data_contract: "v1"')
    if any(not (root / name).exists() for name in SOLVER_VISIBLE):
        errors.append("任务缺少公开目录或题目描述")
    schema, reference = EVALUATOR.load_spec(root)
    outputs = {entry["path"] for entry in schema["required_outputs"] if entry["format"] == "json"}
    checks = schema.get("numeric_checks", [])
    rubric = json.loads(rubric_path(root).read_text(encoding="utf-8")) if rubric_enabled(root) else {}
    tagged = rubric.get("schema_version") == CAPABILITY_SCHEMA
    if (not checks and not tagged) or any(
        check["output"] not in outputs or check["reference"] not in reference["values"] for check in checks
    ):
        errors.append("数值任务需要独立的数值检查，输出和参考键必须指向实际定义")
    if capabilities and not set(capabilities).issubset(rubric_capabilities(rubric.get("groups", [])) if tagged else []):
        errors.append("题包评分小项须覆盖清单中承诺的能力")
    decision_names = Counter(check["name"] for check in checks)
    if tagged:
        decision_names.update(
            f"judge:{item['id']}" for group in rubric["groups"] for item in group["items"]
            if item["check"] == "judge" and "capability" in item
        )
    for name in decision_checks or []:
        if decision_names[name] != 1:
            errors.append(f"decision_checks 中的 {name!r} 必须对应题包里的检查")
    if checks and not any(path.name in {"numeric_checks.py", "score.py"} for path in EVALUATOR.evaluator_dir(root).glob("*.py")):
        errors.append("缺少可复算的参考脚本 numeric_checks.py 或 score.py")
    paper_hash = sha256_file(paper)
    if sha256_file(CONTRACT.paper_path(root)) != paper_hash:
        errors.append("任务的目标论文与原始输入不一致")
    if any(sha256_file(path) == paper_hash for path in (root / "related_work").glob("*.pdf")):
        errors.append("公开参考文献包含目标论文")
    errors.extend(f"内部答案字段出现在公开区：{key}" for key in _hidden_key_leaks(root))
    return errors
