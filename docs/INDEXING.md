# Embedding the IPO chunks

`uv run python main.py index` embeds the existing outputs of `ingestion/chunk_corpus.py`.
`uv run python index.py --dry-run` counts tokens and reports the remaining cost without calling the embedding API.

The user selected OpenAI `text-embedding-3-small` instead of the Gemini model specified by the assignment. This deviation should be disclosed in the report. No answer-generation model is configured by this step.

## How it works

1. Read the three chunk files, including source URLs, sections, heading paths, tables and attached notes.
2. Use each chunk's `embedding_text` as input. A and B embed their text; C includes the heading-path prefix.
3. Deduplicate identical input texts across all designs. Source records remain separate even when they share a vector.
4. Count tokens with the model's tokenizer and reject inputs exceeding 8,191 tokens. The current longest input is 3,894 tokens, so no text is truncated.
5. Send batches of 32 texts to the embeddings endpoint, requesting 1,536 dimensions. Store returned vectors as float32 in `index/vectors.sqlite3`, committing each successful batch.
6. Cache by SHA-256 of model, dimensions and exact input text. A rerun reuses saved vectors. Rate-limit responses are retried with backoff; authentication, insufficient-credit and ambiguous network failures stop with saved work intact. A request that succeeds remotely but loses its response may need to be paid for again on resumption.
7. Save the complete chunk records and their vector keys in one compressed JSONL file per design. `manifest.json` records counts, model, dimensions and usage reported by successful API responses.

The resulting SQLite vectors and compressed metadata are a local index store. A later retrieval stage will load the chosen design, embed the question with the same model and dimensions, and rank vectors by cosine similarity. This step does not yet implement the MCP server or measure retrieval quality.

## Cost and credentials

The initial corpus has 9,506 chunk records and 8,650 unique embedding inputs, totalling 2,865,268 tokens. At $0.02 per million tokens, the initial embedding estimate is $0.05730536 USD, excluding taxes and future query/answer-generation calls. Price reference: https://developers.openai.com/api/docs/models/text-embedding-3-small .

The default preflight limit is $0.25 for remaining input. It is a local estimate, not a provider-enforced account spending cap. `--max-cost` can override it. The account's billing dashboard is authoritative for charges.

Set `OPENAI_API_KEY` in the environment or ignored local `.env` file. The index never stores or prints the key. Keep the existing Gemini key for assignment components that still require it.

`index/` contains saved vectors and belongs in the submission. No commit is made automatically. The script checks the 90 MB size limit after export. Personal-data review of the intermediate corpus remains outstanding, as recorded in the chunking metadata.
