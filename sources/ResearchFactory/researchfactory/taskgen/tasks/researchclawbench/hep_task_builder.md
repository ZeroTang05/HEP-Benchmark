# Build one planned HEP benchmark task

Use the assigned entry to build a self-contained public question and its
private evaluation. Keep its question, chosen scope, decisive output, and named
`decision_checks` aligned. Edit only this working directory's `task/` package.
Read `../../_protocol/hep_protocol.md` for the contract. Keep exploratory
scripts outside the package and remove them when complete.

## Public task

Use the entry's `public_context` and verify it against the real sources. Give
the Solver the background, physical constraints and input materials needed to
reach the decision. Summarize only the facts that precede the choice. If the
Solver must propose a hypothesis, provide the motivating evidence and question.
If the Solver must choose a method, provide the goal or hypothesis and realistic
resource and observation constraints. Preserve the choice the entry assigns to
the Solver when defining the deliverable. A purely qualitative task may keep
`data: []` with the real scientific context in the task description and the
supporting literature in `related_work/`.

When calculation needs measurements, use the actual files named in the plan
from `../../_paper_cache/`. Check its `index.json`, use pandas or Python's
`csv` module on the official files, and preserve units and uncertainties.
Search for relevant prior work and save a useful open PDF with
`add_related_pdf`, or reuse a matching cached candidate. Record the sources
and their purposes in `task/related_work/references.json`. Check that neither
the context nor a supporting document gives away the assessed decision or result.
Keep the shared cache and target study private. Use the existing environment.

## Private evaluation

Use `evidence` to ground the v3 rubric in the paper. For open responses, write
the essential scientific criteria, the reasoning that would satisfy them, and
any relevant limitations or acceptable alternatives. Require the author's
specific choice when reconstructing that choice is the stated question.
Use the exact names in `decision_checks`, including capitalization. For
`judge:<id>`, use that rubric ID with `"check": "judge"`. When scoring
calculations, run the hidden reference script on the public inputs and verify
bin counts, fit settings and reference values. Use separate public field names
and private reference keys.

Give the promised capabilities individual scoring criteria. Use
`"evidence": "trace"` when the evidence is an observable action or its timing,
such as proposing a prediction before testing it. Give the Solver enough
information to know what to submit. Require actual execution where tool or model
operation is assessed.

Run `python -m researchfactory.taskgen.tasks.researchclawbench.validate_package --task-root task`
and resolve the reported issues before finishing.
