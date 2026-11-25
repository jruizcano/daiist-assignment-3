# Project guide

This map follows the assignment stages. Run all commands from the repository root in the VS Code terminal. For code inside a folder, use `python -m package.module` (without `.py`) so Python resolves imports consistently.

## Where to start

- [REPORT.md](../REPORT.md): the student's analysis and conclusions.
- [README.md](../README.md): the original task, requirements, and submission instructions.
- [SETUP.md](../SETUP.md): environment and API setup.
- [eval/questions.csv](../eval/questions.csv): the test set.
- [eval/editor_subagent_transcript.txt](../eval/editor_subagent_transcript.txt): the actual editor conversation.

## Assignment stages and files

| Stage | Implementation | Guide / saved evidence |
|---|---|---|
| Corpus | `corpus_sources.json`, `ingestion/prepare_corpus.py` | Raw and cleaned documents under `corpus/` (ignored by Git) |
| Extraction and chunking | `ingestion/extract_corpus.py`, `ingestion/heading_paths.py`, `ingestion/chunk_corpus.py` | [Chunking](CHUNKING.md) |
| Embeddings and index | `index.py`, `ingestion/document_ingestion.py` | [Indexing](INDEXING.md), saved vectors in `index/` |
| Retrieval comparison | `retrieval/search.py`, `evaluate.py` | [Retrieval](RETRIEVAL.md), `eval/retrieval_summary.json` |
| MCP tools and editor subagent | `server.py`, `.mcp.json`, `.claude/agents/corpus-researcher.md` | [MCP tools](MCP_TOOLS.md), editor transcript in `eval/` |
| Harness and guardrails | `agent.py`, `AGENTS.md` | [Harness](AGENT_HARNESS.md) |
| Passage and answer evaluation | `evaluation/` | [Passage review](PASSAGE_REVIEW.md), [Answer evaluation](ANSWER_EVALUATION.md), `eval/human_labels.csv` |
| Before/after experiments | `experiments/` | [Check comparison](CHECK_COMPARISON.md), [Answer fixes](ANSWER_FIXES.md), [Add document](ADD_DOCUMENT_EXPERIMENT.md) |
| Verification | `checks/`, `tests/`, unchanged assignment `check.py` | Commands below |

`evaluation/` contains Python code; `eval/` contains questions, human labels, cached runs, HTML reviews and measured results. These are separate on purpose. Historical code snapshots inside `eval/` are preserved experimental evidence, not the current implementation.

## Required entry points (unchanged)

```powershell
uv run python main.py index
uv run python main.py ask "What final IPO offer price per share is stated in Airbnb's prospectus?"
uv run python main.py evaluate
uv run python check.py
```

The index command needs the prepared chunks in `corpus/chunks/`. The committed index already supports search; the server does not rebuild the corpus. `main.py evaluate` runs retrieval and answer evaluation and can make paid calls when the code or inputs differ from a saved run.

## Reproduce individual stages

```powershell
# Prepare documents, then extract and chunk (writes corpus/)
uv run python -m ingestion.prepare_corpus
uv run python -m ingestion.extract_corpus
uv run python -m ingestion.chunk_corpus

# Retrieval comparison only; existing question embeddings are cached
uv run python evaluate.py --retrieval-only

# Recount the saved answer evaluation and human agreement, no API calls
uv run python -m evaluation.answer_evaluation --offline

# Repeat the before/after checks on saved evidence, no API calls
uv run python -m experiments.compare_passage_checks
uv run python -m experiments.compare_answer_fixes

# Display the saved add-document attempt, no API calls
uv run python -m experiments.add_document_experiment

# Offline regression tests (API responses are mocked)
uv run python -m unittest discover -s tests -v

# Validate saved chunk/index consistency without embedding anything
uv run python -m checks.validate_chunk_outputs
uv run python -m checks.validate_embeddings .

# Real MCP search and duplicate-document smoke test
uv run python -m checks.verify_mcp
```

The saved add-document command returns exit code 1 when the recorded attempt failed its success checks; this reports the existing blocked answer, not a module-loading failure.

For importing human review exports and optional paid reruns, use the stage guides linked above. Old chat commands such as `python passage_review.py` now become `python -m evaluation.passage_review`. Old filenames inside historical transcripts and snapshots are left intact.

## Results preserved during reorganization

The folder change did not regenerate answers, embeddings or grades. The saved baseline datasets, latest-results pointer, human labels, transcript, and index remain at their original paths. REPORT.md was not rewritten.

Evaluation uses source hashes to identify each run. Moving modules and adjusting imports changes those hashes. A new full evaluation therefore creates a different dataset; old human grades are not silently attached to new answers. Use `evaluation.answer_evaluation --offline` to recount the existing reviewed dataset for the report. No new paid evaluation was run as part of the reorganization.

## Submission

Commit the implementation packages, `docs/`, the required root scripts, `index/` and the saved evaluation evidence. Follow the assignment's fork/PR/Blackboard/ZIP instructions. Keep `.env`, `.venv`, Python caches and credentials out of the submission ZIP as well as Git.
