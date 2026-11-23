# HEP package protocol

Read `_paper_cache/README.md` and `_paper_cache/index.json` first. Use the
official raw CSVs listed there as the primary measurement sources. Their headers
already contain the column names and units. Parse them with `pandas.read_csv`
or Python's `csv` module. Strip surrounding whitespace from headers when
reading and use those names in metadata. Keep the numeric values and uncertainties.
Use extracted PDF tables and page images for cross-checking. Do not transcribe
PDF tables or create regular-expression parsers when an official CSV is present.

## Exact measurement metadata shape

Adapt column names and units to the real source. The field is `schema_version`.
This is a structure example, not a measurement table:

```json
{
  "schema_version": "researchfactory.hep_data_metadata.v1",
  "observable": "particle_flux",
  "independent_variable": {
    "name": "rigidity",
    "unit": "GV",
    "binning": "bin_edges",
    "columns": {"low": "R_min", "high": "R_max"}
  },
  "value": {
    "name": "flux",
    "unit": "m^-2 sr^-1 s^-1 GV^-1",
    "column": "flux",
    "quantity_type": "differential"
  },
  "uncertainties": [
    {"name": "statistical", "column": "stat", "unit": "m^-2 sr^-1 s^-1 GV^-1"},
    {"name": "systematic_total", "column": "sys", "unit": "m^-2 sr^-1 s^-1 GV^-1"}
  ],
  "correlations": {
    "available": false,
    "assumption": "State the actual justified assumption and its limitations."
  },
  "missing_value_policy": "blank_is_missing",
  "positive_value_required_for_log_fit": true
}
```

`bin_centers` uses `columns: {"center": "actual_column"}` instead of low/high.
When a raw table repeats the binned axis for separate observation windows or
categories, add `group_by` inside `independent_variable` with the exact
column names identifying that group, e.g. `["begin date", "end date"]`.
Bins must remain ordered and non-overlapping within each group. To match
two tables, use the window columns and both bin edges as join keys.
The other supported missing-value policy is `null_is_missing`.
Do not invent bin-to-bin or cross-species covariance. State an approximation
explicitly when the released tables do not provide it.

## Exact provenance shape

```json
{
  "schema_version": "researchfactory.hep_source_provenance.v1",
  "files": [
    {
      "path": "observable.csv",
      "sha256": "REPLACE_WITH_SHA256_OF_THE_ACTUAL_STAGED_CSV",
      "source_url": "REPLACE_WITH_THE_OFFICIAL_MEASUREMENT_SOURCE_URL",
      "source_type": "supplementary_measurement"
    }
  ]
}
```

Keep transformation details and raw-source hashes as extra provenance fields
when useful. Attach metadata and provenance paths to the corresponding entry
in `task_info.json` `data[]`. A sidecar is attached metadata; it does not need
its own measurement-data entry.

## Hidden evaluator

Write `task/target_study/evaluator/schema.json` and `reference.json`.
The schema uses `researchfactory.hep_evaluator.v1`. `required_outputs` is a list
of objects with `path`, `format`, and `description`. Supported formats are
`json`, `csv`, `figure`, and `text`. Output paths are relative to the Solver's
solution directory. Each `numeric_checks` entry has `name`, `output`,
`json_pointer`, `reference`, and nonnegative `abs_tolerance`.
The reference uses `researchfactory.hep_evaluator_reference.v1` with a numeric
`values` mapping. Compute the declared numerical values with executable scientific
analysis over the actual public inputs. Keep the script inside the evaluator
directory, using an allowed name such as `numeric_checks.py`.
For a dedicated qualitative task, use an empty `numeric_checks` list and empty
`values` mapping. Score the decisive deliverable with a named v3 Judge item,
using real scientific material as input. Keep any qualitative scoring anchors
or accepted evidence in the hidden criterion.
When that task needs background and constraints rather than measurement files,
put the real context in `task_info.json`, declare `data: []`, and keep the
empty `data/` directory. Supply relevant prior work in `related_work/`.
Use `research_framing`, `hypothesis_generation`, or `analysis_design` as
`hep_task.primary_task_type` when it describes the primary scientific operation.

The public task must specify the output filenames, JSON field names and units,
while reference values and internal reference keys stay hidden. Use different
internal reference keys from public output-field names so source/reference
identifiers remain separate. Required plots must be real plots from the data.

## Related work

Search for prior work specific to your question. Use `web_search` and prefer
open copies from arXiv, the experiment, or the publisher. You can reuse a
matching cached candidate. For a new PDF, use `add_related_pdf` with its direct
HTTPS URL and a local filename ending in `.pdf`. Use the returned SHA-256.
Keep at least one useful, readable PDF from no later than the target year.
The target study and its preprint stay private. Exclude references that reproduce the
answer to the assigned calculation. Describe how each reference supports the
question without quoting the expected result. In `task/related_work/`, keep
only the selected PDFs and a `references.json` of this shape:

```json
{
  "search_queries": ["search terms actually used"],
  "papers": [
    {"file": "prior-paper.pdf", "title": "Published title", "year": 2014,
     "identifier": "doi:... or arxiv:...", "url": "https://...",
     "sha256": "file digest", "use": "Which convention or comparison this paper supports"}
  ]
}
```

## Two-level scoring checklist

Write `task/target_study/checklist.json` as an object. A group's `weight` is
the sum of its items' weights. All group weights sum to 1. Each item weight
is a fraction of the entire score. For example, 0.20 earns 20 points out of
100. Use unique lowercase IDs for groups and items. Example for a task with one fitted number:

```json
{
  "schema_version": "researchfactory.hep_checklist.v3",
  "groups": [
    {"id": "results", "title": "Fit results", "weight": 0.75, "items": [
      {"id": "slope", "criterion": "Report the signed fitted slope within tolerance.",
       "weight": 0.75, "check": "numeric:signed_slope", "capability": "data_analysis"}
    ]},
    {"id": "method", "title": "Fit procedure", "weight": 0.25, "items": [
      {"id": "initial_plan", "criterion": "Before the first fit, identify the inputs, the fitting steps and a check of fit quality.",
       "weight": 0.15, "check": "judge", "capability": "task_planning", "evidence": "trace"},
      {"id": "limitations", "criterion": "Explain the effect of assuming independent bins.",
       "weight": 0.10, "check": "judge", "output": "report.md", "capability": "physical_consistency"}
    ]}
  ]
}
```

Use `numeric:<name>` for each numerical check in `schema.json` exactly once.
Use `required_output:<path>` only when file presence and readability deserve
points. Use `judge` for checks needing scientific reading, with `output` set
to one required Solver file. For observable process evidence, use
`"evidence": "trace"` on a Judge item. Leave `output` off for a trace-only item,
or name a required file when both sources are necessary. Ask for the behavior
in the public task so the Solver has a fair opportunity to provide evidence. Give
separate items to separate fit parameters, uncertainty calculations or plot
properties as relevant. Avoid duplicate credit for results from the same check.
Use scientific quantities and explicit evidence in criteria. Keep reference
answers in the evaluator and leave `package_contract.json` in place.

An item has at most one `capability`, while a task can assess multiple:

| ID | Observable evidence |
| --- | --- |
| `knowledge_understanding` | Interprets relevant literature, definitions, figures, or data conventions. |
| `task_planning` | Identifies needed inputs, orders steps, and proposes checks before analysis. |
| `hypothesis_generation` | States a testable prediction and how observations could weaken it, before testing. |
| `analysis_design` | Chooses and justifies a method or control, and tests the consequence when appropriate. |
| `tool_use` | Correctly runs a scientific tool, inspects its result, or reproduces a check. |
| `model_invocation` | Selects, configures, and actually applies a relevant model, then interprets its output. |
| `physical_consistency` | Applies units, physical assumptions, uncertainty limits, or independent consistency checks. |
| `data_analysis` | Produces correct transformations, measurements, comparisons, or visualizations from real data. |

Tag substantive evidence. Leave routine file-presence items untagged. For
trace criteria, identify which statement or tool action must happen before
which result. Require scientific adequacy, not a count of attempts or mentions.

## Validation before completion

Run this command with the native Shell tool after building or repairing:

```bash
python -m researchfactory.taskgen.tasks.researchclawbench.validate_package --task-root task
```

Read the errors, fix the actual package, and rerun this command before exiting.
It checks the same contract as the host runner. A missing input or unresolved
scientific issue must be reported; do not fabricate data to make validation pass.
