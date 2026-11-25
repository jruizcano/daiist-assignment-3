# Before and after: quotation checks

From the project directory in the VS Code terminal:

```powershell
uv run python -m experiments.compare_passage_checks
```

This command runs entirely offline and prints all totals, per-design counts, resolved question/design pairs and remaining flags. It does not read the API key or call a model. Open `eval/check_comparison/comparison.html` for the comparison, including each quoted excerpt, full source passage, match rule and your existing notes.

Run the checker regression tests with:

```powershell
uv run python -m unittest tests.test_evidence_checks -v
```

## What is held constant

On the first run, the command snapshots the final AI draft assessments, retrieved passages, original summary, rubric, human decisions and original review scripts into `eval/check_comparison/before/`. Hashes in `before/manifest.json` protect those inputs. Later runs reuse that snapshot and fail if a baseline input was altered. Before computing the new results, the command verifies that its v1 check reproduces every original validation error exactly.

The same 90 assessments are evaluated by both versions. No retrieval, embeddings, questions, references, judge labels or human decisions are changed. In particular, `q10` can have its quote warning resolved while its evidence still remains partially sufficient.

## Changes to the check

1. **Continuous prose quotations:** v1 normalized whitespace but otherwise required a literal, case-sensitive substring. v2 additionally normalizes literal escaped newline/tab characters and uses case-insensitive matching. It retains numbers, words and punctuation. It does not use fuzzy similarity or treat paraphrases as exact quotations.
2. **Conservative table exception:** v2 can verify a shortened citation of a single annual monetary value against the saved table grid. It requires an exact row label and amount, the dollar currency cell, a matching year and annual period in that value's column, and explicit matching units attached to that same table. It rejects wrong years, interim columns, wrong units, wrong rows and ambiguous matches. This is recorded as `structured_table_cell`, not as a verbatim quotation. The current implementation does not attempt all possible table layouts, per-share values, multiple-value summaries or inferred units.
3. **Other checks:** passage-rank validity, answerable/unanswerable label restrictions and label/missing-evidence consistency are unchanged.

The check still does not prove semantic entailment. A valid quote can be interpreted incorrectly; a partial excerpt can omit important context. A resolved warning is not a finding that an answer is correct. We deliberately do not erase every flag merely because the human judged the retrieved evidence sufficient.

## Generated measurements

The script reports 90 assessments, with 17 flagged by v1 and 13 by v2: four resolved and zero newly flagged. Per design: fixed-size 6 to 6; paragraph-aware 5 to 2; heading-path 6 to 5.

Resolved cases:

- q07, paragraph-aware: escaped line breaks and capitalization.
- q10, paragraph-aware: escaped line breaks.
- q14, paragraph-aware: escaped line breaks.
- q02, heading-path: the shortened revenue-row citation now matches the exact table value under the 2019 annual column with dollar currency and millions units.

The remaining 13 flags concern quotations the conservative v2 rules still cannot verify. They are not 13 established retrieval failures. User decisions remain a separate record.

## Output files

- `eval/check_comparison/before/`: frozen original input files, hashes and v1 validation results.
- `eval/check_comparison/after/validation_results.jsonl`: new check results and match details.
- `eval/check_comparison/after/evidence_checks.py`: copy of the revised checker used.
- `eval/check_comparison/comparison.jsonl`: per-assessment before/after checks, unchanged AI labels and human notes.
- `eval/check_comparison/summary.json`: code-calculated counts, resolved and remaining assessment IDs, input/checker hashes and zero API cost.
- `eval/check_comparison/comparison.html`: readable evidence and side-by-side outcomes.

The original `evaluation/passage_review.py` workflow and its HTML retain v1 flags for reproducibility and link to the revised comparison. Use `experiments/compare_passage_checks.py` for the v2 validation experiment. Your report is not edited: use these generated measurements to describe an improvement to evaluation robustness, not a retrieval accuracy improvement. The checker changes were motivated by inspected cases, so they are not a held-out evaluation.
