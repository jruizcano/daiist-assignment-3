# Retrieval comparison

Run `uv run python evaluate.py --retrieval-only` for the retrieval comparison alone. `uv run python main.py evaluate` also runs answer generation and judging. The measurements below describe the original retrieval comparison.

`retrieval/search.py` loads the committed SQLite vectors and compressed chunk metadata. It normalizes the vectors and uses cosine similarity, with deterministic original-index ordering for ties. Each design searches all three documents without company filters, query rewriting or reranking. The expected answers and locations do not influence retrieval or get sent to the embedding endpoint.

The 30 question texts are embedded once with the same `text-embedding-3-small` model and 1,536 dimensions as the corpus. Identical query vectors are used across the three designs. Query vectors and usage are cached separately in `eval/query_cache/`, leaving the corpus index unchanged. The first run used 582 tokens, costing approximately $0.00001164 at $0.02 per million tokens. Cached reruns make no embedding requests.

## Metrics and interpretation

- Section hit@k: at least one of the top k chunks has both an expected document ID and an expected section. The denominator is 25 answerable questions. A hit is a section-level proxy; it does not establish that the answer is present in the chunk.
- All-source coverage@k: for the four questions requiring multiple documents, every expected document must have at least one returned chunk in an expected section. This does not establish that every required claim is supported.
- The other three multi-passage questions require multiple passages within one document. Section hit rate does not measure their full evidence sufficiency; review the passages manually.
- The five unanswerable questions are retrieved and saved for inspection but excluded from these rates. Retrieval always returns matches; the agent stage must later test refusal behavior.
- Expected sources and sections are independent lists, not positional pairs. A returned chunk must satisfy both lists. Alternative valid sections absent from the question's list can be counted as misses.
- The words metric is the mean sum of whitespace-separated words in retrieved `text` fields, including attached notes and repeated/overlapping text, across all 30 questions. It excludes future prompt instructions, citation labels and other prompt formatting. No answer-generation prompt has yet been sent.
- These questions are being used for design comparison; these are not held-out test results.

## Initial measurements

| Design | Section hit@1 | Section hit@3 | Section hit@5 | All-source coverage@5 (4 multi-document questions) | Mean top-5 text words |
|---|---:|---:|---:|---:|---:|
| Fixed-size | 14/25 (56%) | 19/25 (76%) | 20/25 (80%) | 1/4 | 1,281.8 |
| Paragraph-aware | 17/25 (68%) | 20/25 (80%) | 22/25 (88%) | 3/4 | 1,132.3 |
| Heading-path prefix | 16/25 (64%) | 20/25 (80%) | 22/25 (88%) | 0/4 | 1,301.0 |

## Saved outputs

- `eval/retrieval_summary.json`: counts, methods, metric definitions, limitations, question-file hash and query-cache usage.
- `eval/retrieval_results.jsonl`: all 90 question/design combinations with full top-five chunks, scores, IDs, sections, paths, notes and source URLs.
- `eval/retrieval_review.html`: expandable question-by-question review with reference answers and complete passages. Review whether the chunks support the actual answer, not just whether the section matches. For multi-passage questions, check all necessary facts, companies, years and units.

The user's questions and report have not been edited. The final design decision and report interpretation remain for the user after reviewing the evidence. A future retrieval change should be saved as a new experiment rather than silently replacing this baseline.
