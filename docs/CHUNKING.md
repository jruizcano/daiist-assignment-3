# Offline chunking experiment

Run from the assignment folder:

```powershell
uv run python -m ingestion.chunk_corpus
uv run python -m checks.validate_chunk_outputs
```

These commands read the downloaded HTML and extracted blocks locally. They do
not call Gemini, create embeddings, modify the test questions, or create a vector
database. `main.py index` is still a separate unfinished assignment stage.

## Three designs

- **A (`a_fixed`)** uses up to 400 body words and 80 words of overlap. It adapts
  the fixed-window idea from the supplied `gemini_rag.ipynb` example. It may cut
  sentences or paragraphs at window boundaries.
- **B (`b_paragraph`)** packs complete paragraphs up to 400 body words. It repeats
  the last complete paragraph if it has at most 80 words and fits alongside the
  next paragraph. Thus overlap is variable, sometimes zero, not exactly 80 words.
  Paragraphs over 400 words are divided at sentence boundaries and repeat complete
  trailing sentences, choosing the overlap closest to 80 words that leaves room
  for new text. If no complete-sentence overlap fits, use an 80-word tail instead.
  Sentences over 400 words also use this word-window fallback. This guarantees
  overlap and forward progress while keeping prose bodies within 400 words.
- **C (`c_heading_path`)** copies A's chunk bodies and notes exactly. Only its
  prepared `embedding_text` gains a prefix containing the heading path. All designs
  retain source paths as metadata for auditing. Only C includes the path in the
  text intended for embedding; no embedding has been performed.

All three use the same document, section, inferred-subheading and table boundaries.
This prevents misleading paths and keeps the A/C comparison controlled. A is a
fixed-window baseline within topics, not a window across an entire document.

## Heading paths

`ingestion/heading_paths.py` reads the source HTML. The existing extractor's anchor-derived
section is retained exactly. Short, fully bold headings become subsection labels;
short italic or bold-italic headings become nested labels. HTML heading tags are
also recognized. Labels over 20 words or 180 characters, common unit statements,
and sentence-like labels ending in a period or semicolon are excluded.

An example checked against the actual DoorDash HTML is:

```text
DoorDash > MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS
         > Components of Results of Operations > Revenue
```

The hierarchy below the main section is a formatting-based heuristic, not an
author-supplied semantic outline. Some headings can be missed or misclassified;
inspect the samples before relying on topic expansion. Original `section` strings
remain unchanged so the question CSV still matches extraction metadata.

## Tables and footnotes

Data tables are separate from prose. Source cell coordinates and merged-cell grids
are preserved in the extraction. Chunking uses the grid and groups complete data
rows, targeting 400 data words; it repeats headers, unit/qualification rows and
attached notes in each part. Rows are not separated across a spanning cell. If a
header boundary cannot be identified confidently, the whole table is retained and
flagged. This rule is shared by all three designs.

For prose, every chunk containing part of a paragraph also carries that paragraph's
attached notes. This is conservative: a chunk may carry a note for a sentence in
another part of that paragraph, but no attached qualification is lost. Original
note blocks already attached to a parent are not independently chunked a second
time. Supporting tables inside notes remain in the attached note text.

The 400-word number is a body target, not a hard total-size limit. Repeated headers,
complete rows and notes may make a chunk longer. The summary reports those cases.

## Outputs and provenance

`corpus/chunks/` contains each design as JSONL, blocks annotated with paths, a topic
index, and `summary.json`. This directory is ignored by Git as part of `corpus/`;
the final saved-vector index will be a different stage under `index/`.

Every chunk records its design, stable content hash, source document, company,
filing date, section, source URL, heading path, original block IDs and word spans
or table-row indices. `body` is the passage; `text` adds notes; `embedding_text` is
the prepared input for a later embedding stage. These outputs cover the current
intermediate corpus; personal-data review and final corpus selection remain pending.

## Local topic lookup

```powershell
uv run python -m ingestion.chunk_corpus --document doordash_2020_ipo --topic "Components of Results of Operations > Revenue" --limit 20
```

This filters C's stored heading paths, returning matching chunks and a total match
count. Searching a broader heading includes its descendant paths. It is local
text matching, not vector/semantic search. Topic expansion should be evaluated
separately from the initial A/C embedding comparison.

The integrity checker tests source coverage, fixed overlap, B's paragraph
preservation, table-cell fidelity, note retention, unique IDs, A/C parity and
topic lookup. Retrieval hit rates and answer quality have NOT yet been measured.
