---
description: Build independently scorable tasks from a preselected scientific paper and its staged data.
---

# Paper-Grounded Benchmark Task Package

Build the one task assigned in the entry prompt. All package paths below are
relative to your working directory. Follow the entry prompt for the location
and scope of the staged research sources.

In this package, the Solver is a future agent or user who receives only the
public inputs and must produce an answer; the hidden files under `target_study/`
are used later to judge that answer.

## Required Layout

Produce this layout per task:

- `task_info.json`: one JSON object with `task` and `data`.
- `data/`: real Solver input files when needed, or empty for a context-only
  qualitative HEP task allowed by the HEP protocol.
- `related_work/`: prior related-work PDFs.
- `target_study/paper.pdf`: the hidden target paper, already staged.
- `target_study/checklist.json`: hidden scoring checklist.
- `target_study/images/`: hidden reference images for image checklist items.

The Solver-visible inputs are only `task_info.json`, `data/`, and
`related_work/`. The `target_study/` directory is a hidden answer-key area used
for validation and must not be copied into public inputs.

Do not create alternate schema files. Update the staged templates in place.

## Task Description

Keep `task_info.task` focused on the public research goal: inputs, expected
output, scientific target, and scope boundary. It is only the task description;
do not put the agent execution protocol there. Put provenance and assumptions in
data descriptions or the hidden checklist when needed.

## Capability Plan

When the entry prompt includes a capability plan, treat it as authoritative:

- The **Primary capability** must be the main action the Solver is evaluated on.
- Use at most the listed secondary capabilities, and only as supporting actions.
- Prerequisites may be required to complete the work but must not replace the
  Primary capability as the benchmark target.
- Do not make a forbidden capability necessary for solving or scoring the task.
- Match the requested target level: `basic` is a bounded single operation,
  `applied` uses the capability on real inputs, `integrated` coordinates several
  steps, and `advanced` requires expert judgment or a demanding implementation.

Follow the requested RCB construction mode:

- `reproduce`: reproduce a method, figure, table, or quantitative finding from
  the public inputs.
- `analyze`: derive and validate a new analysis over the supplied data.
- `design`: design and justify a method, experiment, or evaluation protocol with
  concrete, checkable outputs.

Legacy runs without a plan may choose the most defensible paper-grounded mode.

Every item in `task_info.data` must reference a real file under `task/data/`.
Every data path must start with `./data/`.

## Related Work

For each HEP question, use `web_search` with the observable, experiment or method
to find prior work, including when the question fully specifies the calculation.
Select at least one relevant open PDF and explain which method, convention or
comparison it supports. Use the related-work format in the HEP protocol. Keep
the target study and its alternative versions private. The public question must
still state the calculation and the assumptions needed to solve it.

## Dataset

The Solver must be able to actually compute, fit, train, evaluate, or analyze
from what you place in `task/data/`. A CSV transcribed from the target paper's
own text or tables is not sufficient as the sole data, because that is circular
verification.

Try these sources in order, stopping once you have enough real computable input:

- the code repository released with the paper, copying released data files,
- Zenodo, Figshare, institutional repositories, or public project storage,
- publisher supplementary materials,
- public data portals the paper builds on,
- paper-table transcription only as a last resort, and only when the values are
  themselves valid Solver inputs; say so explicitly in the data description.

Never put PDFs in `task/data/`. Never fabricate values. Use real bytes.

## Data Integrity

Solver-visible files are inputs, never outputs or answer keys:

- For prediction, regression, or classification, the target that the Solver must
  predict must not appear in any Solver-visible file.
- Official measurement tables may serve as inputs for a new calculation (such
  as fitting measured fluxes). Values that directly answer the current question
  stay hidden, including published ratios, fit parameters, and derived fractions
  when the task asks the Solver to calculate them.

## HEP Data Contract

When the entry prompt enables HEP mode, add `"hep_data_contract": "v1"` to
`task_info.json`. Every measurement table must have a metadata sidecar and a
source provenance sidecar under `task/data/`. Attach both paths to the same
`task_info.data[]` item:

```json
{
  "name": "observable.csv",
  "path": "./data/observable.csv",
  "type": "measurement_data",
  "metadata": "./data/observable.metadata.json",
  "provenance": "./data/source_provenance.json"
}
```

Also add a `hep_task` object. It must contain one supported
`primary_task_type`, at most two `secondary_task_types`, an `observable`, an
`independent_variable`, non-empty `required_inputs`, non-empty
`required_outputs`, and one difficulty level from `basic`, `applied`,
`integrated`, or `advanced`. The primary type describes the main scientific
operation that the Solver will be evaluated on.

Use the analysis environment already present on the machine. Do not run
`pip install`, `npm install`, `apt install`, or another package installer while
building a task. A missing dependency must be reported at the point where it is
needed so the pilot can be fixed at the environment level.

When `_paper_cache/` is present, start with its index, extracted Markdown and
official measurement files. Inspect page images or staged reference figures
only when required by a selected figure task. Reuse these assets. Check the
private `_paper_cache/related_candidates.json` for a matching prior PDF after searching.
Keep `_paper_cache/` private. For quantitative inputs, prefer the official raw
measurement tables to values read from a plotted curve. Retain the raw source URL
and hash when preparing a measurement table and its metadata.

The metadata sidecar must use schema
`researchfactory.hep_data_metadata.v1` and declare the observable, independent
variable name and unit, `bin_edges` or `bin_centers`, value column and unit,
`quantity_type: "differential"`, statistical and total systematic uncertainty
columns, correlation availability or its assumption, missing-value policy, and
the log-fit positivity rule. The table must contain the declared columns, finite
bin values, non-overlapping ordered bins, non-negative uncertainties, and
positive values when the log-fit rule is enabled.

The provenance sidecar must use schema
`researchfactory.hep_source_provenance.v1`. Its `files[]` entry for each table
must contain the relative path, the exact SHA-256 of the table bytes, a source
URL, and a source type such as `measurement_input`,
`supplementary_measurement`, or `public_data`. A paper's final fitted result is
not a valid measurement input.

HEP tasks must also create the hidden evaluator contract:

```text
task/target_study/evaluator/schema.json
task/target_study/evaluator/reference.json
```

`schema.json` must use `researchfactory.hep_evaluator.v1`, list at least one
required Solver output, and give each numeric check an output path, JSON pointer,
reference key, and non-negative absolute tolerance. `reference.json` must use
`researchfactory.hep_evaluator_reference.v1` and contain a `values` object.
For a dedicated qualitative task, follow the v3 protocol for Judge criteria
and omit numerical values. Keep the evaluator hidden.

Specify the required paths, JSON fields, units and analysis constraints as
applicable. Leave genuine planning, hypothesis or method choices to the
Solver when they are being assessed. For numerical checks, compute the hidden
reference from the staged public data using an executable analysis script and
use scientifically justified tolerances.

## Checklist

For HEP tasks, use the two-level JSON contract in the HEP protocol. Group small
criteria by scientific goal. Give every small criterion one observable
requirement and its own share of the total score. Continue to Self Consistency once the HEP checklist is ready.

For non-HEP tasks, write `task/target_study/checklist.json` as a top-level JSON array, never an
object wrapper.

Checklist requirements:

- 3-6 items.
- Weights sum to exactly 1.0.
- Use an `"image"` item with a relevant reference figure when the task scores a
  plot.
- Item type must be only `"text"` or `"image"`.
- Text items must have `"path": null`.
- Image item paths are relative to `target_study/`, for example
  `images/reference.png`; never use `task/target_study/images/...`.

Checklist items evaluate scientific outcomes only: figure reproduction,
quantitative agreement with paper-stated results, methodology fidelity,
uncertainty, validation, or interpretation. They must not name target-paper
structural labels such as "Figure 4", "Table 2", or "Section 3.2", because the
Solver has no access to the target paper. Phrase deliverables by content and
shape, not output file paths.

Checklist `content` and `keywords` must not cite target-paper result values.

## Self Consistency

Before validation, check that the text, data and related papers give the Solver
everything it needs for the checklist. Add any necessary public information or
revise the checklist. Run the reference script and the package validator. End
the session when your package passes these checks. The separate Reviewer and
Solver stages test the science and independent solvability.

## Workspace Hygiene

Before exiting:

- Replace every `PLACEHOLDER_` value in `task/task_info.json` and
  `task/target_study/checklist.json`.
- Every file under `task/data/` must be referenced in `task_info.json` `data[]`.
- Delete trial, scratch, and unused files from `task/`.
- Delete HTML landing pages, error responses, and zero-byte files from
  `task/related_work/`.
- `task/target_study/` may only contain `paper.pdf`, `checklist.json`, `package_contract.json`, `images/`,
  and `evaluator/`.
