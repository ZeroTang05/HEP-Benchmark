# Plan an HEP task series from the paper's scientific decisions

## Reconstruct the investigation

Read the paper's motivation and prior evidence, the methods and supplement, and
the results and limitations in relation to each other. Use the cached text,
captions, and official source index. Follow the scientific role of a passage
even when it has no section heading. Establish what question this paper could
answer, what made the answer uncertain, and which decisions made its evidence
informative. Read the relevant source material before choosing tasks.

For each important decision, identify:

1. **What was available beforehand.** Prior observations or theory, the
   measurement or simulation setting, feasible inputs, and relevant constraints.
2. **What had to be decided.** The specific uncertainty, alternatives, or
   trade-off that affected the investigation.
3. **What would support the decision.** An observable prediction, a defensible
   design, a quantitative contrast, or an interpretation justified by evidence.
4. **What the paper actually established.** Record its route, assumptions,
   strength of evidence, and limits with a source locator. A claim's actual
   result and the information needed to pose the question may come from
   different parts of the paper.

The relationship between context, decision, evidence, and conclusion is the
same across studies. The decisions come from this paper. A precision measurement
may turn on which observable can distinguish components. A detector study may
turn on event classification, calibration, or bias checks. A search may turn
on how to distinguish signal from background or set a limit. An observational
analysis may turn on coverage, competing explanations, or what an association
can establish. Use the decisions and evidence in the study at hand.

## Choose what the Solver should decide

Turn a consequential decision into a task by providing enough context to reach
it, leaving the decision itself open where it is being assessed, and specifying
the evidence the Solver will submit. Keep the paper's answer and the scoring
basis private. A task can require a reasoned explanation, prediction, or
analysis plan even when its scientific answer does not require a computation.
Check the proposed public context on its own. If it already specifies the
choice the Solver is meant to make, move that detail to the private evidence
or ask a different decision that the public facts actually leave open. A
published result may be a premise for a new interpretation. In that case
score the new inference, its assumptions, and its limits.

| Stage | Example of a decision and its assessed deliverable |
| --- | --- |
| `framing` | Turn the motivation and prior evidence into a precise research question, and explain what evidence would answer it. |
| `hypothesis` | From a research question and background, propose testable explanations or one justified prediction. State what observations would distinguish the possibilities. |
| `design` | Given a goal or hypothesis and real measurement constraints, choose what to measure or compare, how to account for nuisance effects, and what would change the conclusion. |
| `measurement` | Estimate or characterize an observable from available inputs, with justified units, scope, and uncertainty. |
| `comparison` | Decide whether specified observations or candidate models differ in a way supported by comparable evidence. |
| `inference` | Explain which physical conclusion is supported by the observations and assumptions, and how far it extends. |
| `robustness` | Identify a consequential alternative assumption or bias check and show what changes under it. |

Use the exact labels in the Stage column for the scientific role of selected
tasks. Write capability IDs in `capabilities`. The stages are not a
required sequence of activities inside the paper or a quota for any stage.
Order selected tasks by their role in the investigation. Each must supply its
own inputs and be solvable without completing a sibling. For example, where
one task asks for a hypothesis and a second asks for a method given that
hypothesis, the second must state its own hypothesis.

When an early decision is genuinely testable from the paper's background and
constraints, consider it alongside downstream analysis before picking tasks.
Reject vague brainstorming and questions that require access to unreleased
measurements. A design task can use a description of available observations
and can ask what additional evidence would be needed. It must not claim that
unavailable data can be analyzed in the task. Only ask the Solver to perform
detector calibration, event selection, or unfolding when the required inputs
are staged.

Use the batch coverage focus to prioritize a suitable candidate where possible.
For a focus such as hypothesis generation or analysis design, prefer a task
that actually leaves the relevant scientific choice open when the material
supports it. An explicitly prescribed calculation can still test understanding,
execution, and physical consistency. Assign the eight capability IDs from
`hep_protocol.md` after identifying the choices and their observable evidence.
One task may assess multiple capabilities. Comparing published model
predictions gives evidence of scientific interpretation. Model invocation
requires the question to ask for running or applying a model with usable inputs.

Select a set of substantive and distinct decisions, usually two to four if
supported. Group variants that change only an isotope, period, interval, or
plot if the same calculation answers the same scientific question. A hypothesis
and a method can share a topic and still be distinct decisions. In `omissions`,
record consequential candidates you could not select and the specific reason,
including missing inputs or lack of a fair test of the batch focus.

## Make each decision assessable

For open reasoning, use the paper as a scientific reference. Identify the
essential reasoning, evidence, and constraints that make a response valid.
Credit different justified hypotheses or methods that satisfy those conditions.
A testable hypothesis remains acceptable even if the paper's later result
disfavored it. Require the author's particular procedure only for an explicit,
sufficiently constrained reconstruction task. For a quantitative task, name
the result that decides the question and use released measurements, units,
scope, uncertainty assumptions, and a justified numerical tolerance.

Check the physical constraints relevant to the selected decision:

- Match observables, units, bin edges, and common observation windows before
  comparing them. Rigidity and kinetic energy per nucleon are different
  coordinates. When coarsening, use shared whole bins and account for bin
  widths for differential fluxes. Check actual columns and representative
  rows, including positivity for log fits, when a task requires computation.
  Use the official tables and distinguish released measurements from the
  derived answer. A released ratio can be used as input for a shape fit and
  must be private if the task asks to reconstruct it.
- Trace shared measurements and shared uncertainty in ratios, joint fits and
  normalization. Use released covariances where available and document any
  approximation. A diagonal fit or a fit-statistic improvement at a parameter
  boundary does not by itself establish a calibrated significance or physical cause.
- Separate a model assumption from a finding. For example, a fit with a fixed
  flat branch estimates a transition conditional on that assumption. Testing
  for a plateau requires letting the slope vary or comparing justified models.
  For a source decomposition, check whether the templates distinguish the
  components and how template errors enter. For comparisons between nested
  models, refit the remaining free parameters for each model.
  For a limit, define the physical boundary, target quantity, and interval
  construction. Use a clearly labeled conditional endpoint where warranted.
- For a time-dependent claim, account for gaps, common coverage, smoothing,
  and serial correlation. State the sign convention and compare over the
  shared range. A searched-for peak or window contrast is descriptive unless
  the background and selection procedure support an inferential statement.

## Hand off the independent tasks

Write private `task_series.json` in the workspace root. Each task has:

| Field | What the builder needs |
| --- | --- |
| `id`, `stage` | Unique lowercase ID using letters, digits and hyphens, and the task's primary scientific role. |
| `question` | The actual decision or question the Solver must address. |
| `public_context` | The pre-decision facts, constraints, and materials to supply, with exact file/column names when data is needed. Summarize source passages without revealing the decision's outcome. |
| `evidence` | Private source locators and the basis for judging the response, including the paper's route, essential criteria, or scientifically valid alternatives. |
| `method` | Which choices stay open and which constraints are fixed, or the fixed recipe if execution is the task. |
| `decision_output` | Describe the Solver's actual deliverable in words, including what it must show. |
| `decision_checks` | Name the checks for that deliverable as bare numerical check names or `judge:<rubric-item-id>`. |
| `capabilities` | The capability IDs backed by observable evidence in the task. |

The manifest contains `tasks` and `omissions`. If a single task is selected,
state why only that candidate supports an independent, assessable question in
`single_task_reason`. Finalize the manifest before calling `build_task_series`
and finish when it returns. The tool delegates package preparation, prior-work
search, and task validation. Keep the staged template and cache unchanged.
