# Reviewing evidence in the retrieved passages

Run `uv run python -m evaluation.passage_review` from the project directory. This is a separate optional AI-assisted review stage; `main.py evaluate` runs retrieval plus end-to-end answer evaluation. The passage reviewer reads the existing `eval/retrieval_results.jsonl` and does not change the questions, embeddings, ranking, report or assignment human grades.

## What is assessed

Each of the 30 questions is assessed against the combined top-five passages from each of the three designs: 90 assessments. The reference answer helps identify the requested facts but is never treated as source evidence. The reviewer does not see the design label, similarity scores or previous section-hit verdicts. It checks the actual passage text, including financial units, years, qualifiers, footnotes and all requested companies. Rounded figures may answer a question that does not require exact precision, but cannot support additional digits copied from the reference.

For answerable questions:

- `sufficient`: all essential requested facts are supported.
- `partially_sufficient`: some essential facts are supported and some are missing.
- `insufficient`: no substantive requested fact is supported.

For unanswerable questions:

- `not_established`: the retrieved passages do not establish the requested answer. This says nothing conclusive about the entire corpus and is not a refusal-quality score.
- `potential_reference_issue`: the retrieved passages appear to establish an answer despite the unanswerable reference; requires inspection.

These are proposed operational definitions for this evidence review, not the final assignment judge rubric. The user should review and own the final rubric and analysis. This stage does not generate or grade agent answers and does not fulfill the requirement to personally grade 20+ agent answers.

## How assessments are produced and checked

The script uses `gpt-5-nano-2025-08-07` with low reasoning effort, structured JSON output and a 4,096-token completion cap including reasoning. It includes the question, reference and full retrieved passage texts; no external search or tools are available to the reviewer. The original prospectuses and reference text are treated as data, not instructions.

Every supported requirement must include a passage rank and quotation. Local code checks the passage exists, the quote occurs in its text (allowing whitespace differences), and the label is consistent with the question type and missing-evidence fields. Exact quote presence does NOT establish that the quotation supports the claimed interpretation.

Drafts failing these mechanical checks receive at most one correction call with the errors explained and allowed labels restricted by question type. Both original and correction responses are retained. Remaining failures appear as `needs_review` in the summary and are not counted as validated sufficient/partial/insufficient verdicts. No automated semantic assessment is presented as a human grade.

Responses are cached by the complete request, including the model, rubric, schema, question, reference and evidence text. Changed inputs invalidate the cache. Cached reruns make no additional model calls. Requests run with four concurrent workers. The default $0.25 preflight budget uses conservative prompt overhead and the maximum possible completion length; it is a local guard, not an account-wide provider spending cap. Actual usage is saved, including reasoning and cached-input tokens.

Price reference: https://developers.openai.com/api/docs/models/gpt-5-nano ($0.05 input, $0.005 cached input, $0.40 output per million tokens at implementation). The model was available to the user's API project when run. Availability and pricing can change.

## Files and human review

- `eval/passage_review.html`: readable review with full passages and expandable AI drafts. Use the dropdowns and notes to make YOUR decisions. Browser-local storage is a convenience only. Use **Export my review decisions** and save the JSON in the project's `eval/` folder as `passage_human_reviews.json`.
- `eval/passage_human_reviews.json`: separate human decisions, initially blank; existing annotations are preserved on reruns. Labels carry a hash of their assessment inputs so changed evidence/rubrics do not silently inherit old decisions.
- `eval/passage_reviews.jsonl`: AI draft labels, explanations, quotations, missing requirements, validation errors, model, usage and cache references.
- `eval/passage_review_summary.json`: provisional aggregate counts and costs. `cost_usd` refers to responses selected for the final drafts; `all_cached_calls_cost_usd` includes initial calls, the preliminary smoke check and corrections.
- `eval/passage_review_cache/`: raw responses and usage, allowing audit and resumption.
- `eval/passage_review_rubric.txt`: exact draft instructions used.

Read the passages before opening the AI draft if you want to reduce anchoring. Prioritize questions flagged `needs_review`, multi-company comparisons, tables and any draft marked sufficient where essential information appears absent. Correct the human review fields, not the cached AI output. For final independent human grading of agent answers later, use the assignment's separate `eval/human_labels.csv` without first consulting the judge verdicts.

The report and final design choice remain the user's work. Do not describe these draft sufficiency counts as answer accuracy or human-verified retrieval quality.
