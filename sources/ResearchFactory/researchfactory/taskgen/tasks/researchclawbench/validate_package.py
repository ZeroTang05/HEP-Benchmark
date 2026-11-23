"""让构建与审核 Agent 用原生 Shell 工具检查当前任务的真实合同。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import rubric_enabled


def main() -> int:
    """输出逐项问题，合同不满足时返回非零退出状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--task-root", type=Path, default=Path("task"))
    mode.add_argument("--series", action="store_true", help="检查当前工作区的全部子题")
    mode.add_argument("--submit-series", action="store_true", help="校验全部子题并通知生成程序交付")
    mode.add_argument("--init-task", metavar="ID", help="将共享模板复制到一个新子题目录")
    mode.add_argument("--prepare-delegation", action="store_true", help="检查定题清单并初始化各子题目录")
    args = parser.parse_args()
    if args.prepare_delegation:
        from researchfactory.taskgen.tasks.researchclawbench.hep_series import prepare_delegation
        print(json.dumps(prepare_delegation(Path.cwd()), ensure_ascii=False))
        return 0
    if args.init_task:
        from researchfactory.taskgen.tasks.researchclawbench.hep_series import initialize_task
        print(initialize_task(Path.cwd(), args.init_task))
        return 0
    if args.series or args.submit_series:
        from researchfactory.taskgen.tasks.researchclawbench.hep_series import SUBMISSION, read_series, validate_series_task
        from researchfactory.utils.storage import atomic_write_json
        workspace = Path.cwd()
        rows = read_series(workspace, require_decision_checks=args.submit_series)
        paper = CONTRACT.paper_path(workspace / "task")
        results = {
            row["id"]: validate_series_task(
                workspace / "tasks" / row["id"] / "task", paper,
                decision_checks=row.get("decision_checks"),
                capabilities=row.get("capabilities"),
                require_hep_package=rubric_enabled(workspace / "task"),
            )
            for row in rows
        }
        print(json.dumps(results, ensure_ascii=False, indent=2))
        if args.submit_series and not any(results.values()):
            atomic_write_json(workspace / SUBMISSION, {"tasks": list(results)})
        return int(any(results.values()))
    root = args.task_root.resolve()
    from researchfactory.taskgen.tasks.researchclawbench.hep_series import MANIFEST, read_series, validate_series_task

    # 子代理验收时也检查定题承诺，避免全部完成后才发现打分项改名。
    if root.name == "task" and root.parent.parent.name == "tasks":
        workspace = root.parent.parent.parent
        if (workspace / MANIFEST).is_file():
            rows = read_series(workspace, require_decision_checks=True, require_packages=False)
            row = next((entry for entry in rows if entry["id"] == root.parent.name), None)
            if row is None:
                raise ValueError(f"任务未列入定题清单：{root.parent.name}")
            errors = validate_series_task(
                root, CONTRACT.paper_path(workspace / "task"),
                decision_checks=row["decision_checks"], capabilities=row.get("capabilities"),
                require_hep_package=rubric_enabled(workspace / "task"),
            )
            print(json.dumps({
                "valid": not errors,
                "errors": [{"code": "hep_series_task", "message": error} for error in errors],
                "warnings": [],
            }, ensure_ascii=False, indent=2))
            return int(bool(errors))
    result = CONTRACT.validate(args.task_root)
    if result.ok and rubric_enabled(args.task_root):
        from researchfactory.taskgen.tasks.researchclawbench.reviewer import _hidden_key_leaks

        for leak in _hidden_key_leaks(args.task_root):
            result.error("hep_answer_key_leak", f"hidden evaluator key leaked into public input: {leak}")
    print(json.dumps({
        "valid": result.ok,
        "errors": [{"code": issue.code, "message": issue.message} for issue in result.errors],
        "warnings": [{"code": issue.code, "message": issue.message} for issue in result.warnings],
    }, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
