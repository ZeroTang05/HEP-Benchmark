"""Workspace initializer for ResearchClawBench-style task packages."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from researchfactory.capabilities import render_capability_brief
from researchfactory.core.cache import Cache
from researchfactory.taskgen.core.registry import register_task_type
from researchfactory.taskgen.core.types import PreparedTaskWorkspace, SeedResolution, TaskType
from researchfactory.taskgen.tasks.researchclawbench.contract import CONTRACT
from researchfactory.taskgen.tasks.researchclawbench.hep_plan import (
    infer_hep_task_plan,
    render_hep_task_plan,
)
from researchfactory.taskgen.tasks.researchclawbench.hep_evidence import (
    normalize_evidence_sources,
    render_evidence_sources,
)
from researchfactory.taskgen.tasks.researchclawbench.hep_paper import ensure_target_paper_identity
from researchfactory.taskgen.tasks.researchclawbench.hep_series import (
    read_series, require_delegated_submission, uses_series, validate_series_task,
)
from researchfactory.taskgen.tasks.researchclawbench.hep_contract import HEP_TASK_TYPES
from researchfactory.taskgen.tasks.researchclawbench.hep_rubric import CAPABILITIES, CAPABILITY_SCHEMA, PACKAGE_METADATA
from researchfactory.utils.storage import atomic_write_json

SKILL_NAME = "researchfactory-taskgen"
DEFAULT_SKILL_PATH = Path(__file__).with_name("SKILL.md")
PROTOCOL = Path(__file__).with_name("hep_protocol.md")
PLAYBOOK = Path(__file__).with_name("hep_research_playbook.md")
CHILD_BRIEF = Path(__file__).with_name("hep_task_builder.md")


def is_hep_target(target: dict[str, Any]) -> bool:
    """判断任务是否显式声明为高能物理任务。"""
    if target.get("hep") is True or target.get("hep_data_contract") == "v1":
        return True
    if target.get("experiment"):
        return True
    source_hint = " ".join(
        str(target.get(key) or "")
        for key in ("title", "source_url", "url")
    ).lower()
    if "alpha magnetic spectrometer" in source_hint or "ams02.space" in source_hint:
        return True
    domain = str(target.get("domain") or target.get("assigned_domain") or "").strip().lower()
    return domain in {
        "hep",
        "high_energy_physics",
        "high energy physics",
        "particle physics",
        "astroparticle physics",
    }


def _id_line(target: dict) -> str:
    parts: list[str] = []
    if target.get("paper_id"):
        parts.append(f"Paper ID: {target['paper_id']}")
    if target.get("arxiv_id"):
        parts.append(f"arXiv: {target['arxiv_id']}")
    if target.get("doi"):
        parts.append(f"DOI: {target['doi']}")
    year = target.get("year")
    parts.append(f"Year: {year if year is not None else '?'}")
    if target.get("venue"):
        parts.append(f"Venue: {target['venue']}")
    return "     ".join(parts)


def build_entry_prompt(target: dict, *, skill_name: str = SKILL_NAME) -> str:
    """Build the short HEP entry prompt; detailed protocol is in SKILL.md."""
    plan = target.get("taskgen_plan")
    capability = ""
    if isinstance(plan, dict) and plan.get("primary"):
        capability = "\n" + render_capability_brief(
            plan, construction_mode=str(plan.get("construction_mode") or "")
        ) + "\n"
    hep_instructions = ""
    series = is_hep_target(target) and uses_series(target)
    if is_hep_target(target):
        focus = target.get("capability_focus")
        if focus is not None and focus not in CAPABILITIES:
            raise ValueError(f"未知的能力抽取重点：{focus!r}")
        evidence_sources = normalize_evidence_sources(target)
        hep_instructions = """
HEP mode is enabled for this task. Declare `hep_data_contract: \"v1\"` in
task/task_info.json. For every measurement table, create the required metadata
sidecar such as `observable.metadata.json` and `source_provenance.json` under task/data/, attach their paths in the
corresponding data[] item, and preserve units, binning, statistical uncertainty,
systematic uncertainty, correlation assumptions, and source hashes. Also declare
`hep_task` with one primary_task_type, at most two secondary_task_types,
observable, independent_variable, required_inputs, required_outputs, and difficulty.
Use only the preinstalled analysis environment. Do not run `pip`, `npm`, `apt`,
or another package installer during task construction; if a required dependency
is absent, record the failure at that point.
"""
        hep_instructions = (
            "\n"
            + (
                f"Supported primary task types: {', '.join(sorted(HEP_TASK_TYPES))}.\n"
                if series else render_hep_task_plan(infer_hep_task_plan(target))
            )
            + "\n"
            + render_evidence_sources(evidence_sources)
            + hep_instructions
            + "\nTag observable scoring criteria with the benchmark capabilities in the HEP protocol. "
            "A task may assess several capabilities.\n"
            + (f"Batch coverage focus: {focus}. Use it when selecting from the paper's "
               "scientific decisions and leave the relevant choice open when the material supports it.\n" if focus else "")
        )
    objective = (
        "Plan an ordered series of independent HEP tasks. Read `_protocol/hep_research_playbook.md` first. "
        "Reconstruct how the paper moves from its motivation and prior evidence to a scientific conclusion. "
        "Select distinct, assessable decisions from the whole investigation, including reasoning and computation "
        "where supported. Write the complete handoff "
        "in `task_series.json`, then call `build_task_series` to build, check and submit the selected tasks. "
        "Write only the manifest. The staged `task/` is a read-only template. Finish when the tool returns."
        if series else "Build one paper-grounded scientific benchmark task package under `task/`."
    )
    if series:
        hep_instructions = "\nBuilder requirements to account for when choosing questions:\n" + hep_instructions
    return f"""{'' if series else '/' + skill_name}

{objective}
Do not search for or substitute a different target paper. Work only inside the
current workspace and follow the staged skill instructions, including the exact
package layout and public/hidden file boundary.

Target Study:
  - Title: {target.get("title", "")}
  - {_id_line(target)}
  - Domain: {target.get("assigned_domain") or "-"}
{capability}

Pre-staged workspace:
  - Target PDF: task/target_study/paper.pdf
  - Task metadata template: task/task_info.json
  - Hidden checklist template: task/target_study/checklist.json
  - Solver data directory: task/data/
  - Related-work PDF directory: task/related_work/
{hep_instructions}
"""


def _resolve_skill_path(skill_path: Path | None) -> Path:
    src = skill_path or DEFAULT_SKILL_PATH
    src = src.expanduser()
    if not src.exists():
        raise FileNotFoundError(f"taskgen skill not found: {src}")
    if not src.is_file():
        raise ValueError(f"taskgen skill path is not a file: {src}")
    return src


@register_task_type
class ResearchClawBenchTaskType(TaskType):
    """ResearchClawBench-style task package type."""

    name = "researchclawbench"
    capability_track = "papers_capabilities"

    def artifact_pool_path(self, settings: Any) -> Path:
        return settings.paths.target_pool

    def capability_index_path(self, settings: Any) -> Path:
        return settings.paths.papers_capability_index

    def fingerprint_material(self, target: dict[str, Any], skill_path: Path | None = None) -> Any:
        """生成协议变更后重新构建，避免复用旧版任务。"""
        files = [_resolve_skill_path(skill_path)]
        if is_hep_target(target):
            files.append(PROTOCOL)
            if uses_series(target):
                files.extend((PLAYBOOK, CHILD_BRIEF))
        return {"instructions": [path.read_text(encoding="utf-8") for path in files],
                "policy": "hep_paper_series.v3", "prompt": build_entry_prompt(target)}

    def target_id(self, target: dict) -> str:
        return str(target.get("paper_id") or "")

    def resolve_seed(
        self,
        *,
        settings: Any,
        cache: Cache,
        target: dict,
    ) -> SeedResolution:
        paper_id = str(target.get("paper_id") or "")
        pdf = cache.resolve(None, fallback=cache.paper_pdf_path(paper_id))
        if pdf is None:
            return SeedResolution(ok=False, kind="pdf", skip_reason="no_pdf",
                                  meta={"message": "target PDF not found in cache"})
        return SeedResolution(ok=True, kind="pdf", path=pdf)

    def prepare(
        self,
        *,
        target: dict,
        workspace: Path,
        seed: SeedResolution,
        skill_path: Path | None = None,
    ) -> PreparedTaskWorkspace:
        workspace.mkdir(parents=True, exist_ok=True)
        if is_hep_target(target):
            ensure_target_paper_identity(seed.path, target)
        task_root = workspace / "task"
        CONTRACT.prepare_workspace(task_root, target, seed.path)
        if is_hep_target(target):
            # 私有元数据用来核对先前文献，题包验收时同时启用分项评分合同。
            atomic_write_json(task_root / "target_study" / PACKAGE_METADATA, {
                key: target.get(key) for key in ("paper_id", "title", "doi", "arxiv_id", "year")
            })
            atomic_write_json(CONTRACT.checklist_path(task_root), {"schema_version": CAPABILITY_SCHEMA, "groups": []})
        if target.get("paper_bundle_path"):
            from researchfactory.recall.paper_assets import stage_paper_bundle
            stage_paper_bundle(target, workspace, task_root)
        # 在工作区重建完成后复制真实输入，避免预下载数据被清理。
        if target.get("input_data_dir"):
            input_data = Path(target["input_data_dir"]).resolve(strict=True)
            if not input_data.is_dir():
                raise NotADirectoryError(input_data)
            shutil.copytree(input_data, task_root / "data", dirs_exist_ok=True)

        skill_src = _resolve_skill_path(skill_path)
        skill_dir = workspace / ".agents" / "skills" / SKILL_NAME
        skill_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(skill_src), str(skill_dir / "SKILL.md"))
        protocol_instruction = ""
        series = is_hep_target(target) and uses_series(target)
        if is_hep_target(target):
            protocol_dir = workspace / "_protocol"
            protocol_dir.mkdir()
            shutil.copy2(PROTOCOL, protocol_dir / PROTOCOL.name)
            if series:
                shutil.copy2(PLAYBOOK, protocol_dir / PLAYBOOK.name)
                (protocol_dir / CHILD_BRIEF.name).write_text(
                    CHILD_BRIEF.read_text(encoding="utf-8") + "\n"
                    + render_evidence_sources(normalize_evidence_sources(target)),
                    encoding="utf-8",
                )
            protocol_instruction = "Read `_protocol/hep_protocol.md` for the exact schemas and validation command.\n"

        return PreparedTaskWorkspace(
            target=target,
            workspace=workspace,
            task_root=task_root,
            prompt=protocol_instruction + build_entry_prompt(target) + (
                "\nThe complete paper has already been preprocessed. Read `_paper_cache/index.json`, "
                "`_paper_cache/README.md`, `_paper_cache/main/paper.md` and the supplements relevant to the investigation. "
                "For calculations, use official raw CSV files directly with pandas or csv; "
                "do not write regex parsers for extracted PDF tables. "
                "Reuse the staged reference figures under `task/target_study/images/` and consult the prior-paper "
                "candidates in `_paper_cache/related_candidates.json`. Do not re-download or re-extract an available source. "
                "The whole `_paper_cache/` directory is private Builder/Reviewer material; keep it out of public inputs.\n"
                if target.get("paper_bundle_path") else ""
            ),
            skill_dir=None if series else skill_dir,
            skill_name=SKILL_NAME,
            meta={
                "skill_path": str(skill_src), "skill_dir": str(skill_dir),
                "research_services": is_hep_target(target),
                "delegate_task_series": series,
            },
        )

    def build_prompt(self, target: dict[str, Any]) -> str:
        return build_entry_prompt(target)

    def validate(self, task_root: Path) -> Any:
        return CONTRACT.validate(task_root)

    def task_series(self, workspace: Path, target: dict[str, Any]) -> list[dict[str, Any]]:
        """全部子题通过独立合同后，才交给后续阶段。"""
        if not is_hep_target(target):
            return []
        if not uses_series(target):
            validation = CONTRACT.validate(workspace / "task", require_hep_package=True)
            if validation.errors:
                raise ValueError("; ".join(f"{issue.code}: {issue.message}" for issue in validation.errors))
            return []
        rows = read_series(workspace, require_decision_checks=True)
        require_delegated_submission(workspace, rows)
        paper = Path(target["paper_assets_dir"]) if target.get("paper_assets_dir") else None
        original = CONTRACT.paper_path(workspace / "task")
        if paper is not None:
            from researchfactory.recall.paper_assets import sha256_file
            expected = json.loads((paper / "manifest.json").read_text(encoding="utf-8"))["pdf_sha256"]
            if sha256_file(original) != expected:
                raise ValueError("任务组的原始论文被修改")
        else:
            ensure_target_paper_identity(original, target)
        for row in rows:
            root = workspace / "tasks" / row["id"] / "task"
            errors = validate_series_task(root, original, decision_checks=row["decision_checks"],
                                          capabilities=row.get("capabilities"),
                                          require_hep_package=True)
            if errors:
                raise ValueError(f"子任务 {row['id']}：" + "; ".join(errors))
        return rows

    def export_public(self, task_root: Path, public_root: Path) -> list[Path]:
        return CONTRACT.export_public(task_root, public_root)
