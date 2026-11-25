# Adding a document: reproducible before/after experiment

The selected question is q03: **What revenue did Uber report for the year ended December 31, 2019?** The original Uber IPO prospectus was published before the year ended, so it cannot establish full-year 2019 revenue.

The new document is a clearly labelled **verbatim excerpt of the full-year highlights table and its associated footnotes** from Uber's **Uber Announces Results for Fourth Quarter and Full Year 2019**, published February 6, 2020. The selected source content is 149 words and directly supplies the missing financial period:

https://investor.uber.com/news-events/news/press-release-details/2020/Uber-Announces-Results-for-Fourth-Quarter-and-Full-Year-2019/default.aspx

The same release is filed with the SEC:

https://www.sec.gov/Archives/edgar/data/1543151/000154315120000005/uberq419earningspressrelea.htm

Its full-year table reports GAAP revenue of **$14,147 million**, or **$14.147 billion**. Do not confuse this with fourth-quarter revenue of $4,069 million, or adjusted net revenue. This experiment uses the figures as originally published in February 2020, not subsequent restatements.

## Run from VS Code

Open `C:\Users\justo\data_Assignment3` and run:

```powershell
# First run: explicit approval for the temporary document addition and small API charges
uv run python -m experiments.add_document_experiment --approve-add

# Display the saved experiment; no API calls when results already exist
uv run python -m experiments.add_document_experiment

# Recheck the saved answer against its cited table, with no model calls
uv run python -m experiments.add_document_experiment --recheck

# Optional fresh replication; keeps earlier attempts and incurs new API charges
uv run python -m experiments.add_document_experiment --fresh --approve-add

# Offline checks of the experiment's success criteria
uv run python -m unittest tests.test_add_document_experiment -v
```

Without the approval flag, an uncached run asks for approval in an interactive terminal and exits if approval is unavailable. The flag authorizes only the known source file in this temporary experiment. It is not a general bypass of the agent's approval gate.

## How it works

1. **Download and prepare the source.** Save the official HTML under `corpus/experiment_sources/`. Select the full annual highlights table and the following footnote table verbatim, retaining every row, year column, unit statement and associated note. Label it as an excerpt and add a descriptive title and canonical source URL for citations. The raw full download, prepared excerpt and hash receipt are retained. No factual sentence or financial amount is generated to create the source. This is intentionally a curated, simple document, not a claim that arbitrary long reports work equally well.
2. **Protect the existing evaluation.** Hash the original index, questions, human grades and current evaluation artifacts. Create a temporary copy of the original three-prospectus index and the current agent code. Do not copy `.env`; the child process inherits its key in the environment. Existing added-document indexes are excluded so the starting point is the original IPO corpus.
3. **Ask before adding.** Run the normal LangChain/MCP agent on the unchanged q03 question. Save the displayed answer, passages, tool trace, tokens, cost and time. The expected numerical answer is not supplied to the agent.
4. **Add through MCP.** Use the existing `ToolBridge` approval check and the real `add_document(path)` tool. That imports the shared ingestion pipeline: HTML extraction, tables/units/footnotes, heading paths, chunking and saved-vector embeddings. Only the new document's uncached chunks are embedded; the base corpus is not re-embedded.
5. **Check ingestion.** Call `add_document` again with the same file to demonstrate duplicate detection. A successful duplicate check returns `already_indexed` and zero new vectors. Search the same question and save the returned passages.
6. **Ask after adding.** Start the same agent again against the augmented temporary index, with exactly the same question and prompt. This checks that a new server process loads the saved addition too. Save the answer and citations.
7. **Verify and clean up.** Check for a before refusal, retrieval of the new document, and an after answer citing the new document with the expected amount. Check the full cited table: 14,147 must be in the 2019 column and the table must state millions and Year Ended December 31. Reuse the existing citation/quotation validator. Separately record whether the model's displayed quotation itself includes those headers and units. Save all artifacts, close database handles, remove the temporary duplicate index, and verify the protected files' hashes are unchanged.

## Why use a temporary copy?

Your 30 human grades apply to the original IPO-only corpus. Adding a later earnings release would change q03's answerability and invalidate its old unanswerable reference for future evaluations. The temporary copy demonstrates real ingestion and answer generation without silently changing that graded corpus or reference answers. The new document is genuinely indexed and searched during the experiment; it is not inserted directly into the model prompt.

Normal `main.py ask` continues using the original corpus afterward. To inspect this experiment, use its saved comparison or run a fresh replication. This distinction should be stated in the report.

## Saved outputs

Open `eval/add_document_experiment/comparison.html` for the latest before/after display. `summary.json` contains the automatically calculated checks, document counts, costs and timings. Each attempt has its own timestamped folder containing:

- `configuration.json` and `source_receipt.json`: source/code/prompt hashes and approval scope.
- `before.json` and `after.json`: complete real agent runs, not scripted answers.
- `addition.json`, `duplicate.json` and `ingestion_trace.json`: real MCP tool outcomes.
- `search_after.json`: the retrieval results immediately after ingestion.
- `added_document_processed/`: extracted text, tables and unit context for inspection.
- `summary.json` and `comparison.html`: that attempt's outcome, retained even if checks fail.

`attempts.json` lists all completed before/after attempts and their recorded model/document-embedding costs, including unsuccessful attempts. The first attempt stopped during cleanup; its raw outcomes and `interrupted_run_note.json` are retained rather than silently discarded.

## Observed outcomes and check refinement

The first attempt used the full 7,099-word release. Ingestion and retrieval succeeded, but the model misquoted the table and called the amount dollars instead of millions. The guardrail blocked the answer. Windows cleanup then failed because the query-usage helper left its SQLite connection open. The experiment now explicitly closes that connection; the leftover temporary directory was verified and removed.

The second attempt used the labelled 149-word annual-table/footnote excerpt. Before ingestion, the agent said it was not sure. After ingestion, it answered **14,147 million dollars** with a citation to the new document. Seven new embedding vectors were created; a duplicate addition created none. The original index, questions and human grades were unchanged.

The first experiment check required the displayed quotation itself to contain the year and units, so it marked that answer as failing: the model quoted only the revenue row. An offline recheck now distinguishes **answer support in the full cited table** from **complete context in the displayed quotation**. The number is in the 2019 column and the table states millions, so source support passes; displayed quotation completeness remains **false**. The page shows the full source context separately, without editing the agent's answer. `strict_quote_check_summary.json` preserves the initial failed check; no model rerun or altered answer was used to obtain the recheck result. Do not claim that this fixed the agent's quotation-completeness limitation.

The source download is under the existing ignored `corpus/` directory. A clean checkout can download it again using the script. The large temporary copy of the index is deleted, so the experiment does not duplicate the committed 77 MB index in your submission. Recorded answers and extracted evidence remain available.

## Interpretation for the report

Describe the missing financial period, the official document selected, the identical question and unchanged model/prompt, the approval/ingestion process, and the observed before/after answers with citations. Report the saved outcome rather than assuming every stochastic run succeeds.

This is one targeted demonstration, not evidence of overall accuracy. The local checks verify selected answer properties and provenance; you should still inspect the table's annual column and units in the saved evidence. Cached replay makes no API calls. A fresh replication uses small paid answer-model and query/document embedding calls, with their usage saved separately. Nothing in this script writes your report or changes your human grades.
