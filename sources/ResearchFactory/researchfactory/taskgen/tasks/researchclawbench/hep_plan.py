"""HEP 任务计划推断和提示渲染。

任务计划在 Agent 开始工作前确定主科学操作，减少 Agent 在论文、数据和
输出范围之间反复试探。显式 ``hep_task_plan`` 优先；没有显式计划时，
根据目标论文的可观测量和来源信息生成一个可追踪的默认计划。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from researchfactory.taskgen.tasks.researchclawbench.hep_contract import HEP_TASK_TYPES


_DIFFICULTIES = frozenset({"basic", "applied", "integrated", "advanced"})


@dataclass(frozen=True)
class HepTaskPlan:
    """生成阶段使用的 HEP 任务计划。"""

    primary_task_type: str
    secondary_task_types: tuple[str, ...]
    observable: str
    independent_variable: str
    required_inputs: tuple[str, ...]
    required_outputs: tuple[str, ...]
    difficulty: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        """转换成普通字典，便于写入请求 metadata。"""
        return {
            "primary_task_type": self.primary_task_type,
            "secondary_task_types": list(self.secondary_task_types),
            "observable": self.observable,
            "independent_variable": self.independent_variable,
            "required_inputs": list(self.required_inputs),
            "required_outputs": list(self.required_outputs),
            "difficulty": self.difficulty,
            "source": self.source,
        }


def _text(target: Mapping[str, Any]) -> str:
    """合并目标的稳定文本字段，用于关键词推断。"""
    values: list[str] = []
    for key in (
        "title", "abstract", "observable", "experiment", "domain",
        "assigned_domain", "url", "source_url",
    ):
        value = target.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    links = target.get("supplemental_links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, Mapping):
                values.extend(str(link.get(key) or "") for key in ("title", "url"))
    return " ".join(values).lower()


def _validate_plan(raw: Mapping[str, Any], *, source: str) -> HepTaskPlan:
    """验证显式计划，错误立即抛出，避免 Agent 收到不完整边界。"""
    primary = str(raw.get("primary_task_type") or "").strip()
    secondary_raw = raw.get("secondary_task_types", [])
    if not isinstance(secondary_raw, list):
        raise ValueError("hep_task_plan.secondary_task_types must be a list")
    secondary = tuple(str(item or "").strip() for item in secondary_raw)
    if primary not in HEP_TASK_TYPES:
        raise ValueError(f"unsupported HEP primary_task_type: {primary!r}")
    if len(secondary) > 2 or any(item not in HEP_TASK_TYPES for item in secondary):
        raise ValueError("hep_task_plan.secondary_task_types contains an unsupported value")
    if primary in secondary or len(set(secondary)) != len(secondary):
        raise ValueError("hep_task_plan.secondary_task_types must be unique and differ from primary")
    difficulty = str(raw.get("difficulty") or "").strip()
    if difficulty not in _DIFFICULTIES:
        raise ValueError("hep_task_plan.difficulty must be basic, applied, integrated, or advanced")
    observable = str(raw.get("observable") or "").strip()
    independent_variable = str(raw.get("independent_variable") or "").strip()
    required_inputs = tuple(str(item or "").strip() for item in raw.get("required_inputs", []))
    required_outputs = tuple(str(item or "").strip() for item in raw.get("required_outputs", []))
    if not observable or not independent_variable:
        raise ValueError("hep_task_plan.observable and independent_variable are required")
    for name, values in (("required_inputs", required_inputs), ("required_outputs", required_outputs)):
        if not values or any(not value for value in values):
            raise ValueError(f"hep_task_plan.{name} must be a non-empty list")
        if len(set(values)) != len(values):
            raise ValueError(f"hep_task_plan.{name} must not contain duplicates")
    return HepTaskPlan(
        primary_task_type=primary,
        secondary_task_types=secondary,
        observable=observable,
        independent_variable=independent_variable,
        required_inputs=required_inputs,
        required_outputs=required_outputs,
        difficulty=difficulty,
        source=source,
    )


def infer_hep_task_plan(target: Mapping[str, Any]) -> HepTaskPlan:
    """为 HEP 目标返回显式或推断的任务计划。"""
    explicit = target.get("hep_task_plan")
    if isinstance(explicit, Mapping):
        return _validate_plan(explicit, source="explicit")

    text = _text(target)
    if any(token in text for token in ("ratio", "abundance", "p/s", "cl/ar", "k/ca", "flux ratio")):
        return HepTaskPlan(
            primary_task_type="ratio_analysis",
            secondary_task_types=("spectrum_fit", "systematics_propagation"),
            observable="differential flux ratios and component fractions",
            independent_variable="rigidity_GV",
            required_inputs=("bin edges", "flux values", "statistical uncertainty", "systematic uncertainty"),
            required_outputs=("ratio tables", "fit parameters", "uncertainty summary", "scientific report"),
            difficulty="integrated",
            source="inferred:ratio",
        )
    if any(token in text for token in ("time series", "solar cycle", "modulation", "time-dependent")):
        return HepTaskPlan(
            primary_task_type="time_series_modulation",
            secondary_task_types=("systematics_propagation",),
            observable="time-dependent flux or count rate",
            independent_variable="time",
            required_inputs=("time bins", "measurements", "statistical uncertainty", "systematic uncertainty"),
            required_outputs=("modulation parameters", "uncertainty summary", "scientific report"),
            difficulty="applied",
            source="inferred:time_series",
        )
    if any(token in text for token in ("unfold", "response matrix", "migration matrix")):
        return HepTaskPlan(
            primary_task_type="unfolding",
            secondary_task_types=("systematics_propagation",),
            observable="unfolded differential measurement",
            independent_variable="kinematic_variable",
            required_inputs=("reconstructed bins", "response matrix", "measured values", "uncertainty"),
            required_outputs=("unfolded spectrum", "closure or stability check", "scientific report"),
            difficulty="advanced",
            source="inferred:unfolding",
        )
    if any(token in text for token in ("calibration", "alignment", "detector response")):
        return HepTaskPlan(
            primary_task_type="detector_calibration",
            secondary_task_types=("systematics_propagation",),
            observable="detector calibration observable",
            independent_variable="calibration_variable",
            required_inputs=("calibration samples", "reference values", "measurement uncertainty"),
            required_outputs=("calibration parameters", "residual checks", "scientific report"),
            difficulty="applied",
            source="inferred:calibration",
        )
    if any(token in text for token in ("fit", "spectrum", "cross section", "measurement")):
        return HepTaskPlan(
            primary_task_type="spectrum_fit",
            secondary_task_types=("systematics_propagation",),
            observable="published differential measurement",
            independent_variable="kinematic_variable",
            required_inputs=("bin values", "measurement values", "statistical uncertainty", "systematic uncertainty"),
            required_outputs=("fit parameters", "uncertainty interval", "scientific report"),
            difficulty="applied",
            source="inferred:spectrum",
        )
    return HepTaskPlan(
        primary_task_type="reproducibility_audit",
        secondary_task_types=(),
        observable="paper-defined scientific observable",
        independent_variable="paper-defined independent variable",
        required_inputs=("primary measurement data", "source provenance", "uncertainty definitions"),
        required_outputs=("reproduction result", "validation checks", "scientific report"),
        difficulty="applied",
        source="inferred:reproducibility",
    )


def render_hep_task_plan(plan: HepTaskPlan) -> str:
    """把计划渲染成 Agent 入口提示。"""
    secondary = ", ".join(plan.secondary_task_types) if plan.secondary_task_types else "none"
    inputs = ", ".join(plan.required_inputs)
    outputs = ", ".join(plan.required_outputs)
    return f"""HEP task plan (authoritative for this task):
  - primary_task_type: {plan.primary_task_type}
  - secondary_task_types: {secondary}
  - observable: {plan.observable}
  - independent_variable: {plan.independent_variable}
  - required_inputs: {inputs}
  - required_outputs: {outputs}
  - difficulty: {plan.difficulty}

Keep the public research goal centered on this plan. Do not broaden the task into
an unrelated analysis. Record the same primary and secondary task types in
task_info.json under `hep_task`.
"""
