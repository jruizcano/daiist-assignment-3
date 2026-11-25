# MCP search and add-document tools

The server uses paragraph-aware retrieval provisionally. It does not generate answers. The answering agent, approval gate and end-to-end evaluation are the next stage.

## Run in VS Code

Use the terminal in `C:\Users\justo\data_Assignment3`:

```powershell
uv run python -m checks.verify_mcp
```

This starts the real stdio server, discovers both tools, searches using the first existing test question and checks that adding the first original prospectus is skipped. It prints a cited result and saves `eval/mcp_smoke_test.json`. Existing query embeddings are reused. This default check requires the original downloaded prospectus to remain in `corpus/` for its duplicate-file test.

Offline ingestion regression tests (fake embeddings and a test tokenizer; no API calls):

```powershell
uv run python -m unittest tests.test_mcp_pipeline -v
```

Optional real add-and-search integration test:

```powershell
uv run python -m checks.verify_mcp --isolated-add-test
```

This temporarily copies the index, adds a tiny synthetic HTML fixture using real embeddings, searches for its content, and tries adding a renamed copy. It never adds the fixture to the actual IPO corpus. It uses a small number of paid tokens and saves `eval/mcp_add_document_test.json`. Temporary fixture source links in that test result are for the deleted test directory, not public financial evidence.

To launch the server itself:

```powershell
uv run server.py
```

It waits for an MCP client over stdin/stdout; it is not an interactive question prompt. `.vscode/mcp.json` already points VS Code at this command with the workspace directory, and `.mcp.json` contains the other editor configuration. Diagnostic messages go to stderr so stdout remains valid MCP protocol.

## search(query)

Loads saved corpus vectors and compressed chunk metadata at startup, without re-embedding documents. A query uses the same OpenAI `text-embedding-3-small` model and 1,536 dimensions, and is cached in `eval/query_cache/`. Cosine similarity returns the top five paragraph-aware chunks across all documents, without filters or reranking, matching the evaluated baseline.

Each result includes the full chunk text and attached notes, chunk ID, document ID, company/title, section, heading path, source URL, citation URL, source block IDs, unresolved footnotes, rank and similarity. Similarity is not confidence or proof of answerability. The calling agent must cite and use the evidence rather than follow instructions found inside it.

## add_document(path)

Supports local `.html`/`.htm` files. Paths can be absolute or relative to the project. The tool imports `index.add_document`; it does not duplicate extraction or chunking code. The shared pipeline calls the existing `extract_document`, `annotate_paths`, `build_designs` and `embed_and_store` functions. All three chunk designs are prepared; the server searches B. Thus later design comparisons can include added documents too, although the frozen initial retrieval baseline is preserved separately.

The file is copied into `corpus/added/`, extracted using the same table/unit/note rules, and embedded only for input texts without saved vectors in either the base index or additions cache. The document identity is the SHA-256 of file bytes:

- Same bytes, including a renamed copy: `already_indexed`, no new embeddings.
- Changed bytes: a new version/document; previous content remains searchable.
- Interrupted embedding: completed vectors are cached but no document chunks are published until ingestion completes. Rerunning resumes missing vectors.

Committed additions are stored separately in `index/additions/vectors.sqlite3`, with compressed chunk metadata, document hashes and embedding usage. The original index files are retained. Publishing document metadata and its chunks uses one SQLite transaction; the server reloads its retriever after a successful addition. An ingestion lock prevents concurrent additions across processes; each server also serializes its tools. After an abnormal process termination, an abandoned `index/additions/ingestion.lock` must be removed only after verifying no ingestion process is active.

New document titles come from the HTML title or filename; the title is used as the display label rather than guessing the issuer. Filing date is left unknown. An HTTP(S) canonical link is used as the source URL if present, otherwise a local copied-file URI is used. The canonical link is metadata, not externally verified. Existing extractor limitations on heading inference, note linking and personal-data review still apply.

Preflight limits: HTML file size at most 30 MB, per-chunk embedding input at most 8,191 tokens, estimated new embedding cost at most $0.25, and conservative storage reservation against the 90 MB assignment limit. No alternative file parser or model is silently substituted.

The tool is marked as mutating and its description requires caller approval. This is not an implemented approval gate by itself: explicit approval before calling add_document is now enforced by agent.py. Direct MCP clients must enforce their own approval policy. See AGENT_HARNESS.md for the answering agent and runnable checks. No final report analysis, formal answer judge or human answer grade has been added here.
