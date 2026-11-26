"""使用 DeepSeek Harness 独立审核任务，合并科学意见与程序合同检查。"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterator

from researchfactory.taskgen.core.registry import register_review_type
from researchfactory.taskgen.core.types import PreparedTaskWorkspace, ReviewType
from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import (
    CAPABILITY_SCHEMA, PACKAGE_METADATA, rubric_enabled, rubric_path,
)


SKILL_NAME = "researchfactory-hep-reviewer"
DEFAULT_SKILL_PATH = Path(__file__).with_name("reviewer_SKILL.md")
REPORT_NAME = "hep_review_report.json"
REPORT_SCHEMA = "researchfactory.hep_review.v2"
SCIENTIFIC_REPORT_NAME = "scientific_review.json"
SCIENTIFIC_SCHEMA = "researchfactory.hep_scientific_review.v1"
SCIENTIFIC_CHECKS = (
    "paper_identity", "data_provenance", "units_binning", "uncertainties",
    "evaluability", "public_hidden_boundary", "scientific_validity",
)


def read_scientific_review(workspace: Path, *, capability_rubric: bool = False) -> dict[str, Any]:
    """要求独立 Agent 的逐项科学审核，缺失时直接失败。"""
    report = json.loads((workspace / SCIENTIFIC_REPORT_NAME).read_text(encoding="utf-8"))
    if report["schema_version"] != SCIENTIFIC_SCHEMA:
        raise ValueError("科学审核报告 schema_version 不符")
    if report["status"] not in {"accepted", "repaired", "failed"} or not report["summary"].strip():
        raise ValueError("科学审核报告缺少有效状态或总结")
    expected = SCIENTIFIC_CHECKS + (("capability_evidence",) if capability_rubric else ())
    if tuple(check["name"] for check in report["checks"]) != expected:
        raise ValueError(f"科学审核报告必须覆盖规定的检查：{', '.join(expected)}")
    for check in report["checks"]:
        if check["assessment"] not in {"pass", "concern", "fail"} or not check["evidence"].strip():
            raise ValueError("科学审核必须记录每项判断和文件证据")
    if report["status"] != "failed" and any(check["assessment"] == "fail" for check in report["checks"]):
        raise ValueError("存在失败的科学检查，审核状态必须为 failed")
    if capability_rubric and report["status"] != "failed" and report["checks"][-1]["assessment"] != "pass":
        raise ValueError("通过审核的能力评分项必须有可观察的证据")
    return report


def _json_strings(value: Any) -> Iterator[str]:
    """读取 JSON 解码后的文本，保留字符串里的真实换行。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        yield from value
        for item in value.values():
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def _hidden_key_leaks(task_root: Path) -> list[str]:
    """检查 evaluator 内部键名是否进入 Solver 可见文本。"""
    evaluator = task_root / "target_study" / "evaluator"
    reference_path = evaluator / "reference.json"
    schema_path = evaluator / "schema.json"
    if not reference_path.is_file() or not schema_path.is_file():
        return []
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    keys = {
        str(key)
        for key in (reference.get("values") or {})
        if len(str(key)) >= 6 or "_" in str(key)
    }
    keys.update(
        str(item.get("name"))
        for item in (schema.get("numeric_checks") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    )
    # 提交格式中的字段名需要告知 Solver；这些名称属于公开输出合同。
    public_fields = {
        str(key) for output in schema.get("required_outputs", [])
        for key in output.get("required_keys", [])
    }
    public_fields.update(
        str(item["json_pointer"]).rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
        for item in schema.get("numeric_checks", []) if item.get("json_pointer")
    )
    keys.difference_update(public_fields)
    if not keys:
        return []
    with tempfile.TemporaryDirectory(prefix="hep-review-public-") as directory:
        public_root = Path(directory) / "public"
        CONTRACT.export_public(task_root, public_root)
        leaks: list[str] = []
        for path in public_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if path.suffix.lower() == ".json":
                text = "\n".join(_json_strings(json.loads(text)))
            for key in sorted(keys):
                if re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", text):
                    leaks.append(f"{key} in {path.relative_to(public_root).as_posix()}")
        return leaks


def _resolve_skill_path(skill_path: Path | None) -> Path:
    path = (skill_path or DEFAULT_SKILL_PATH).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"HEP Reviewer skill not found: {path}")
    return path


def build_entry_prompt(task_id: str, *, skill_name: str = SKILL_NAME) -> str:
    """构造 Reviewer 的入口提示。"""
    return f"""/{skill_name}

Independently review the staged ResearchClawBench task `{task_id}`.
Check the HEP data contract, source provenance, units, binning, statistical and
systematic uncertainties, hidden evaluator contract, public/hidden boundary,
and whether the public inputs can support the stated task. Repair only concrete
issues that are allowed by this skill. Leave the task package in a valid state
or record the failed checks in the review report.
"""


def _report_path(task_root: Path) -> Path:
    return task_root.parent / REPORT_NAME


@register_review_type
class ResearchClawBenchReviewType(ReviewType):
    """RCB/HEP 任务的独立审查适配器。"""

    name = "researchclawbench"

    def fingerprint_material(self, skill_path: Path | None = None) -> Any:
        path = _resolve_skill_path(skill_path)
        return {
            "policy": REPORT_SCHEMA,
            "reviewer_code": Path(__file__).read_text(encoding="utf-8"),
            "skill": path.read_text(encoding="utf-8"),
            "protocol": Path(__file__).with_name("hep_protocol.md").read_text(encoding="utf-8"),
            "provider": os.environ.get("DEEPSEEK_PROVIDER"),
            "base_url": os.environ.get("DEEPSEEK_BASE_URL"),
        }

    def workspace_name(self, source_workspace: Path, source_meta: dict[str, Any]) -> str:
        return source_meta.get("task_workspace_name") or source_workspace.name

    def prepare(
        self,
        *,
        source_workspace: Path,
        workspace: Path,
        source_meta: dict[str, Any],
        skill_path: Path | None = None,
    ) -> PreparedTaskWorkspace:
        source_task = source_workspace / "task"
        if not source_task.is_dir():
            raise FileNotFoundError(f"RCB source task directory not found: {source_task}")
        workspace.mkdir(parents=True, exist_ok=True)
        task_root = workspace / "task"
        shutil.copytree(source_task, task_root)
        paper_workspace = Path(source_meta["paper_workspace"]) if "paper_workspace" in source_meta else source_workspace
        if (paper_workspace / "_paper_cache").is_dir():
            shutil.copytree(paper_workspace / "_paper_cache", workspace / "_paper_cache")
        protocol_dir = workspace / "_protocol"
        protocol_dir.mkdir()
        shutil.copy2(Path(__file__).with_name("hep_protocol.md"), protocol_dir / "hep_protocol.md")
        skill = _resolve_skill_path(skill_path)
        skill_dir = workspace / ".agents" / "skills" / SKILL_NAME
        skill_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill, skill_dir / "SKILL.md")
        target_id = str(source_meta.get("target_id") or source_meta.get("paper_id") or source_workspace.name)
        variant = source_meta.get("task_variant")
        series_context = ""
        if variant:
            series_context = (
                f"\nThis task is step {variant['order']} ({variant['stage']}) of a private paper series. "
                f"Verify its question ({variant['question']}) against the claimed evidence ({variant['evidence']}). "
                "The Solver must be able to solve it without any sibling task or its output.\n"
            )
            if variant.get("public_context"):
                series_context += (
                    f"The planned public starting context is {variant['public_context']}. "
                    "Check that it enables the assigned decision and leaves the assessed choice open.\n"
                )
            if variant.get("method") and variant.get("decision_output"):
                series_context += (
                    f"The planned core calculation is {variant['method']}. The planned decision output is "
                    f"{variant['decision_output']}. Check that the scored output actually implements both.\n"
                )
            if variant.get("decision_checks"):
                names = ", ".join(variant["decision_checks"])
                series_context += (
                    f"Inspect the named decision checks ({names}) against the public question and "
                    "their numerical or observable evidence. Preserve their names when repairing the package.\n"
                )
        return PreparedTaskWorkspace(
            target={
                "target_id": target_id,
                "source_workspace": str(source_workspace),
            },
            workspace=workspace,
            task_root=task_root,
            prompt="Read `_protocol/hep_protocol.md` for exact contracts and the package validation command.\n" + build_entry_prompt(target_id) + series_context,
            skill_dir=skill_dir,
            skill_name=SKILL_NAME,
            meta={
                "phase": "review",
                "source_workspace": str(source_workspace),
                "review_report": str(_report_path(task_root)),
            },
        )

    def validate(self, task_root: Path) -> Any:
        result = CONTRACT.validate(task_root)
        leaks = _hidden_key_leaks(task_root)
        for leak in leaks:
            result.error("hep_answer_key_leak", f"hidden evaluator key leaked into public input: {leak}")
        report_path = _report_path(task_root)
        if not report_path.is_file():
            result.error("hep_review_report_missing", f"missing {REPORT_NAME}")
            return result
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            result.error("hep_review_report_parse", f"cannot parse {REPORT_NAME}: {exc}")
            return result
        if not isinstance(report, dict):
            result.error("hep_review_report_shape", f"{REPORT_NAME} must contain a JSON object")
            return result
        if report.get("schema_version") != REPORT_SCHEMA:
            result.error("hep_review_report_schema", f"{REPORT_NAME} schema_version is invalid")
        if report.get("status") not in {"accepted", "repaired", "failed"}:
            result.error("hep_review_report_status", f"{REPORT_NAME} status is invalid")
        if not isinstance(report.get("checks"), list) or not report.get("checks"):
            result.error("hep_review_report_checks", f"{REPORT_NAME} checks must be non-empty")
        return result

    def finalize(self, task_root: Path, *, source_task_root: Path | None = None) -> None:
        """合并科学审核与程序校验，新版题包沿用原始论文身份。"""
        require_hep_package = source_task_root is not None and rubric_enabled(source_task_root)
        validation = CONTRACT.validate(task_root, require_hep_package=require_hep_package)
        capability_rubric = rubric_enabled(task_root) and rubric_path(task_root).is_file() and (
            json.loads(rubric_path(task_root).read_text(encoding="utf-8")).get("schema_version") == CAPABILITY_SCHEMA
        )
        scientific = read_scientific_review(task_root.parent, capability_rubric=capability_rubric)
        if require_hep_package and rubric_enabled(task_root):
            relative = Path("target_study") / PACKAGE_METADATA
            if (task_root / relative).read_bytes() != (source_task_root / relative).read_bytes():
                validation.error("hep_package_contract_changed", "review changed the target paper metadata")
        checks = [
            {
                "code": issue.code,
                "level": issue.level,
                "message": issue.message,
            }
            for issue in validation.issues
        ]
        leaks = _hidden_key_leaks(task_root)
        for leak in leaks:
            validation.error("hep_answer_key_leak", f"hidden evaluator key leaked into public input: {leak}")
            checks.append({
                "code": "hep_answer_key_leak",
                "level": "error",
                "message": f"hidden evaluator key leaked into public input: {leak}",
            })
        if validation.ok:
            checks.append({
                "code": "package_contract",
                "level": "pass",
                "message": "RCB and HEP contracts passed",
            })
        checks.extend({
            "code": f"science:{check['name']}",
            "level": {"pass": "pass", "concern": "warning", "fail": "error"}[check["assessment"]],
            "message": check["evidence"],
        } for check in scientific["checks"])
        report = {
            "schema_version": REPORT_SCHEMA,
            "status": scientific["status"] if validation.ok else "failed",
            "checks": checks,
            "scientific_review": scientific,
        }
        _report_path(task_root).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def report_status(self, task_root: Path) -> str:
        try:
            report = json.loads(_report_path(task_root).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ""
        return str(report.get("status") or "") if isinstance(report, dict) else ""

    def export_public(self, task_root: Path, public_root: Path) -> list[Path]:
        return CONTRACT.export_public(task_root, public_root)
