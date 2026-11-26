---
description: Independently review and repair a ResearchClawBench HEP task package.
---

# HEP Task Reviewer

Review the staged `task/` package in the current workspace. Read the package
contract first. Use only the real files in the workspace and the tools of the
selected Agent Runtime. Base findings and repairs on traceable evidence.

## 1. Verify the sources and the public inputs

Confirm the paper identity and trace each measurement to its official source.
Use the official files and extracted paper material in `_paper_cache/` when
available. Reuse the staged text and figures. Verify the measured values,
units, bin edges, observation ranges, and statistical and systematic errors.
Check the availability of correlations and identify any approximation used
when the source gives only separate uncertainties.

Read `task_info.json`, `data/`, and `related_work/` as the Solver's complete
input. Check that they support independent work under the stated constraints.
Inspect the related PDFs and their stated uses in `related_work/references.json`.
Keep the target paper, reference values, and evaluator private. Check that the
public context and supporting material leave the assessed choice or result open.
When a published result is a premise, verify that the scored work makes a
further inference or evaluates a limitation.

## 2. Verify the physical definitions and the claims

For each quantity central to the question or score, trace its definition from
the source equation, table, or methods passage to the calculation the task
requires. Identify what is estimated, its units, scope, and governing
assumptions. For a ratio, fraction, percentage, or fit coefficient, identify
the quantities being compared, the denominator or template normalization, and
any constraint on their sum. A template scale factor is defined relative to
that template. A component's share of a total requires normalization to the
specified total at a stated point or over a defined range.

Check that the public question, output field descriptions, scoring criteria,
and reference analysis all describe the same physical quantity. A numerical
match alone cannot substantiate a definition. For a derived quantity introduced
by the task, verify its stated definition against the required calculation and
ground its inputs in the source. In the `scientific_validity` evidence, cite
the source location and the task artifact that establish the decisive
definition, including the reference quantity when it matters.

Check what the method can establish under its assumptions. Use these checks
where they apply to the task.

- Verify the physical meaning of zero and other comparison points. Distinguish
  a fitted parameter or template-shaped component from a measured abundance or
  a demonstrated physical origin. A difference in slopes can distinguish
  spectra under a model. A claim about particle origin needs additional evidence.
- Trace shared observations across ratios, axes, and sample normalizations.
  An error-scaled contrast using shared measurements needs a justified
  correlation model to support a significance claim. A diagonal uncertainty
  model gives conditional results. Label the estimate as descriptive if the
  uncertainty model supports only a description.
- Check whether a plateau was imposed or tested. For a limit, verify the
  parameter boundary, interval convention, and location or range of the limit.
  For a cutoff test, check that the baseline model refits all remaining free
  parameters, that cutoff and inverse cutoff units are reciprocal, and that
  statistical significance at a parameter boundary has suitable calibration.
- For time series, preserve missing dates as gaps, compare smoothed curves over
  shared observed windows, and account for serial correlation and the range of
  the search when interpreting significance.

## 3. Verify the scoring and repair supported issues

Read the public deliverables and the private evaluator together. Verify output
paths, field meanings, and units. Recompute numerical references from the
public inputs. For fits, check the selected rows and degrees of freedom.
Confirm that numeric checks cover the estimate, contrast, or bound that answers
the question. For a claim spanning a range, look for coverage of that range or
a scored trend statistic. For a qualitative task, check that the scoring
criteria and required output are precise enough to evaluate. Keep each
criterion separately verifiable, weighted by its scientific importance, and
free of double counting.

For a v3 checklist, assign each capability label from the scientific behavior
that its scoring item actually assesses. Correct mislabeled items and record
the item IDs, original and corrected labels, and supporting reasons in
`capability_evidence`. Verify that each resulting capability has a genuine
opportunity in the public question and a supporting output or trace. Check the
timing of a plan or prediction when the criterion requires it to precede a calculation.
Use the trace to verify execution when the task assesses use of a tool or
model. For open decisions, check that the reference describes the relevant
physical constraints and credits justified alternative hypotheses or methods.
When a v2 task supports substantive capability scoring, an upgrade to v3 is
allowed if it preserves the scientific objective and decisive reference checks.

If the calculation is sound and its description is misleading, align the
public wording and scoring descriptions with the verified definition. Preserve
the scientific objective, named decision checks, and numeric reference values
for a wording-only repair. If the quantity or method itself is unsupported,
repair it only when the available evidence and package contract support the
change within the original objective. Otherwise record the failed check.
After any repair, rerun the package validation and record what changed and
what evidence supports it. Package validation and scientific review are both
required.

## 4. Write the scientific review report

Write `scientific_review.json` at the workspace root. Use the following ordered
check list for a v3 scoring checklist. For other scoring checklist versions,
use the first seven checks. If you upgrade the package to v3, use all eight.
Replace the example evidence with task-specific findings.

```json
{
  "schema_version": "researchfactory.hep_scientific_review.v1",
  "status": "accepted",
  "checks": [
    {"name": "paper_identity", "assessment": "pass", "evidence": "specific file and page evidence"},
    {"name": "data_provenance", "assessment": "pass", "evidence": "specific source and hash evidence"},
    {"name": "units_binning", "assessment": "pass", "evidence": "specific columns, units, and bin ranges"},
    {"name": "uncertainties", "assessment": "pass", "evidence": "statistical and systematic treatment and correlations"},
    {"name": "evaluability", "assessment": "pass", "evidence": "reference recomputation or qualitative scoring evidence and output specification"},
    {"name": "public_hidden_boundary", "assessment": "pass", "evidence": "public inputs and hidden answers inspected"},
    {"name": "scientific_validity", "assessment": "pass", "evidence": "source location, decisive quantity definition, and method and inference checks"},
    {"name": "capability_evidence", "assessment": "pass", "evidence": "criterion IDs, public task opportunities, and the required outputs or trace evidence"}
  ],
  "summary": "evidence-based conclusion"
}
```

Each assessment is `pass`, `concern`, or `fail`. The status is `accepted`,
`repaired`, or `failed`. Use `repaired` after making and checking a concrete
repair. If any check fails, the status must be `failed`. For v3, the
`capability_evidence` check must pass for an accepted or repaired package.
Verify the ordered check names and evidence before finishing.
