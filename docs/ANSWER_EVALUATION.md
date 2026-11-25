# Full answer evaluation: commands and method

Open `C:\Users\justo\data_Assignment3` in VS Code and use its terminal. No calculations depend on this chat.

## Commands

```powershell
# Assignment entry point: retrieval comparison, then answer evaluation
uv run python main.py evaluate

# Answer evaluation alone; resumes the current unchanged dataset
uv run python -m evaluation.answer_evaluation

# Recalculate counts and human/judge agreement from saved outputs: no API calls
uv run python -m evaluation.answer_evaluation --offline

# Optional partial smoke run; clearly reports coverage out of the full 30
uv run python -m evaluation.answer_evaluation --limit 2

# Retrieval only, preserving the earlier workflow
uv run python evaluate.py --retrieval-only

# Offline evaluation-code tests
uv run python -m unittest tests.test_answer_evaluation -v
```

Live evaluation uses the configured OpenAI answer model and judge, with separate small query-embedding charges for uncached searches. It does not re-embed the corpus. Network access and the existing `.env` key are needed for uncached calls. Do not put a key in a command or report.

## Processing each question

1. Read the question and reference from `eval/questions.csv`. Call the actual LangChain/MCP answering agent with **only the question**. Do not give it the reference answer, expected section, or human grades. Use the provisionally selected paragraph-aware design B. This evaluates one selected system, not all three designs again.
2. Save the full agent result: displayed answer, structured draft, retrieved chunks, citations, searches, validation errors, tokens, cost and elapsed time. Evaluation automatically denies document mutations so the corpus stays fixed.
3. Repeat the local citation checks. Count explicit `not_found` responses as refusals. Keep operational errors and blocked drafts separate: an API error is not a correct refusal. A citation check is not proof that the answer is true.
4. For eligible final outputs, call a separate judge using `answer_rubric.txt`. The judge sees the question, reference, **displayed final answer**, and every passage retrieved during that answer run. It does not see human labels, design-comparison scores, or rejected intermediate drafts. The reference is a fallible checklist; quotations from passages provide evidence.
5. Validate the judge's structured response and evidence quotations. A malformed or unsupported judge quotation becomes `needs_review`; it does not automatically mean the answer was wrong. Such grades are excluded from valid-grade metrics until adjudicated. API failures stop the invocation with already completed outputs saved; rerun to resume.
6. Produce saved totals, per-question results and a human-review page. After you import your grades, calculate agreement automatically.

The judge uses the same low-cost model family as the answering agent, `gpt-5-nano-2025-08-07`. It is a separate call, not an independent human evaluator; shared model biases are a limitation. Both prompts and the exact inputs/outputs are saved for inspection.

## Rubric approved for this project

`correct = 1` means the answer satisfies the whole essential request, including all companies, periods and necessary qualifications, or correctly refuses an unanswerable question. Partial answers get `correct = 0`.

`supported = 1` means all factual claims are backed by retrieved evidence. A partially complete answer can therefore have `correct = 0, supported = 1`. A plain uncertainty response with no factual claims also has `supported = 1`; it is only correct when refusal is appropriate.

The judge checks units, column years, footnotes, comparisons, calculations and whether a quoted figure describes the requested metric. Explicitly reported rounded figures are allowed unless exact precision was requested. Risk examples need not be identical to the examples in the reference. Possible reference problems are flagged rather than silently edited.

Failure labels can overlap: retrieval miss, wrong answer from the right passage, unsupported claim, wrong refusal and bad test question. Operational errors and guardrail blocks are counted separately by code. Missing facts in retrieved passages do not prove absence from the entire corpus. Failure diagnoses remain provisional AI assessments.

## Saved files and reproducibility

`eval/answer_evaluation/latest.json` points to the current dataset folder. Each folder contains:

- `manifest.json`: questions, rubric, model and hashes of agent/pipeline code, dependencies and index files.
- `answers/`: individual immutable-by-normal-rerun answer records, saved before judging.
- `judges/` and `judge_cache/`: validated grade records and raw cached judge responses.
- `answers.jsonl`: combined answers, mechanics and judge outputs. **Do not read this before completing your blind grades.**
- `judge_results.jsonl`: judge grades, reasons and validation problems.
- `per_question.csv`: per-answer status, citation check, grades, diagnosis, tokens, costs and latency.
- `summary.json`: denominators, rates, coverage, failures, human progress and agreement.
- `human_review.html`: answers, reference and retrieved passages with **no judge verdicts**.
- `invocations.json`: processing time and new query-embedding usage for each invocation.

Run only one evaluation process at a time; the evaluator does not currently implement a cross-process run lock. Unchanged runs reuse answers and judge responses; they do not regenerate answers to obtain a better score. A failed/blocked answer is retained as an observed system outcome rather than silently retried. If a judge request fails before a response is cached, rerunning can complete that missing judge call. A received but invalid judge response is preserved for review rather than repeatedly sampled until it passes.

Changing the agent, test set, index, dependencies, evaluator or rubric creates a new fingerprint and a separate dataset. Historical folders remain intact. The script version and rubric should therefore be frozen before collecting human grades. Different datasets cannot reuse human grades automatically. Do not edit individual saved answers by hand: their association with human reviews assumes these files remain unchanged.

## Human grading, before viewing judge verdicts

1. Open the current dataset's `human_review.html` in your browser. Review at least 20 generated answers. Read the answer and relevant passages; the reference alone is not evidence.
2. Select correct and supported as 1/0. Explain every zero in the notes. The page remembers edits locally in that browser, but export is required to record them in the project.
3. Click **Export human answer grades**, saving `answer_human_reviews.json`.
4. Import it from VS Code:

```powershell
uv run python -m evaluation.answer_evaluation --import-human "C:\Users\justo\Downloads\answer_human_reviews.json"
```

This validates IDs, labels, required notes and the exact dataset identifier, then writes your own decisions to `eval/human_labels.csv`. It also saves the original export and a dataset-binding file. It never invents human grades or copies judge labels into your CSV. Subsequent non-conflicting exports merge with existing grades; conflicting changes require explicit resolution. Earlier passage reviews are not imported because they assess retrieval, not generated answers.

The importer recalculates agreement offline. You can also edit the already-bound CSV yourself and rerun `--offline`; maintain its four columns and explain zero grades. If you enter grades directly into an initially unbound CSV, they are deliberately excluded until their dataset association is resolved. The export/import route avoids that ambiguity.

## Interpreting the numbers in your report

- Overall correctness is available only when every question has either a valid judge grade or an observed operational failure/block. Failures/blocks count as unsuccessful in this rate. If judge grades are missing or invalid, overall correctness is null, and coverage is shown explicitly.
- Correctness and unsupported-claim rates among valid judge grades use that smaller denominator. Do not report them as covering all 30 when they do not. Invalid judge grades may concentrate on difficult questions, so this subset is not necessarily representative. A low unsupported-claim rate also does not erase blocked answers or incomplete responses.
- Correct refusals require an unanswerable test question, an explicit refusal, and a positive correctness grade. The raw count of refusals on answerable questions does not establish why the model refused.
- Human/judge agreement is calculated separately for correctness and support on matching, validly judged answers. It reports matching count, denominator, proportion, confusion counts and Cohen's kappa. Kappa is null when undefined, including unanimous labels with no variation. Fewer than 20 human grades is explicitly marked incomplete.
- Saved answer/judge costs are estimated from recorded API usage, not invoices; they describe those stored runs even when a later invocation uses the cache for free. Query embedding costs are recorded separately by invocation, not added repeatedly to model totals. Model timings describe original runs, not cache lookup speed.
- The test questions were already used for design selection, so results are exploratory performance on this test set, not held-out generalization estimates.

For your report, describe the pipeline and your approved rubric, then use the generated counts with their denominators. Explain real failures and disagreements after inspecting them. The script does not write your report or make the final decision about whether users should rely on the system.

This stage does not perform the later add-document before/after experiment, collect the actual editor-subagent transcript, or replace your 20 human answer grades. Before submission, remove secrets from the repository and backup ZIP and verify `.env` is excluded.


See ANSWER_FIXES.md for the subsequent failure audit, exact citation-link repair, revised judge and preserved before/after experiments.
