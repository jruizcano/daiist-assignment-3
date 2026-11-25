# Answering agent: implementation and reproduction

Run these commands in the VS Code terminal with `C:\Users\justo\data_Assignment3` open:

```powershell
uv run python main.py ask "What revenue did Airbnb report in 2019?"
uv run python -m unittest tests.test_agent -v
uv run python -m checks.verify_agent --offline-only
uv run python -m checks.verify_agent
```

The first and last commands use the paid answer model. The middle two make no API calls. `checks/verify_agent.py` saves `eval/agent_smoke_test.json`, individual traces in `eval/agent_runs/`, and a synthetic guardrail demonstration in `eval/guardrail_demo.json`. Each normal ask also saves a timestamped trace and updates `eval/agent_last_run.json`. Old runs, including failures, remain available.

## How a question becomes an answer

1. `main.py ask` invokes `agent.py`. The harness starts the actual `server.py` as a local MCP subprocess and connects through MCP's stdio client.
2. The harness performs an initial search. The MCP server retrieves the top five paragraph-aware chunks from the saved index. This initial search is included in the tool trace and call budget.
3. LangChain `create_agent` receives `AGENTS.md` as its system prompt, the question, and the search results. The OpenAI model is `gpt-5-nano-2025-08-07`, with low reasoning effort. It may request focused searches, or request a document addition when the user asks for one.
4. The model returns structured claims, evidence entries (chunk IDs and quotations), missing topics, and a status: answered, partial, not_found or action_only.
5. Local code checks the draft. IDs must have been retrieved during this run. Quotations must occur in the cited passage, allowing case, whitespace and escaped-newline normalization. Dollar amounts in claims must occur in their quoted evidence with compatible thousand/million/billion units. If validation fails and budget remains, the model gets one correction attempt; otherwise the answer is blocked.
6. The renderer uses the actual matched source text and the retrieved metadata for source links. A not_found result produces an explicit uncertainty response. Operational failures are recorded as errors, not as evidence that the corpus cannot answer.

There are limits of six model calls and six MCP calls, including the initial search. Further ordinary tools are removed near the budget limit to leave room for a structured answer. Model calls have a timeout and the initial agent invocation has a 240-second timeout; a correction invocation has a separate 120-second timeout. These are operational limits, not accuracy guarantees.

## Approval and guardrails

The harness resolves an add-document path, checks it is an existing HTML file, and asks for explicit approval in an interactive terminal before making the MCP mutation call. Without interactive input, approval defaults to denial. It also verifies that file contents have not changed during approval. The server itself does not implement this interactive gate: clients calling MCP directly must enforce their own approval policy.

The citation guard is additional to that approval gate. The offline tests cover valid citations, invented IDs, altered quotations, wrong dollar amounts in otherwise genuine quotations, uncertainty responses, missing claims, approval/denial, changed files and the tool budget. The saved synthetic demonstration is clearly labelled synthetic; it is not a human grade or evidence of a live hallucination rate.

Quotation matching does **not** establish that a claim follows from the quotation. The dollar check catches some numerical mismatches, but does not establish the right metric, company, year, table column, footnote interpretation, or completeness. It also does not enforce sentence boundaries. It is conservative about calculated dollar results that do not appear explicitly in the evidence, and does not validate every numerical format or non-dollar amount. Those limitations still need the later answer evaluation and human review.

## What the saved results mean

The smoke script checks one answerable revenue question and one unanswerable post-IPO performance question. Passing means the expected response status and local citation checks passed. It is not a percentage answer-accuracy score. Inspect the saved claims and quotations as well.

Run JSON includes the question, model, design, final draft/status, validation errors, initial errors if a correction was attempted, retrieved passages, tool trace, model output messages, token usage, estimated model cost and elapsed time. The estimate uses configured rates of $0.05 per million input tokens, $0.005 cached input, and $0.40 output; it is not an invoice. Query/document embedding usage is tracked separately and is excluded from `model_cost_usd`.

Early development runs hit call limits. A subsequent draft quoted a real sentence containing different financial figures from the requested revenue figure. That motivated the dollar-evidence check and correction attempt. These development cases remain in `eval/agent_runs/`; do not confuse them with the original passage-check comparison, which remains unchanged.

## Remaining assignment work

This implements the LangChain harness stage. It does not finish end-to-end evaluation. `main.py evaluate` now runs retrieval plus the cached answer-generation/judge workflow described in ANSWER_EVALUATION.md. At least 20 human answer grades still need to be supplied by the student; the evaluator then calculates human/judge agreement. Earlier human passage reviews are a different task and cannot be counted as those answer grades.

The search-only editor subagent configuration is in `.claude/agents/corpus-researcher.md`, but an actual Copilot/Claude Code transcript is still required. The later add-document before/after experiment is also outstanding. The project uses OpenAI embeddings instead of the assignment's stated Gemini embeddings by the user's choice; document that deviation. This file explains the implementation for reference; the report's analysis and decisions remain the student's work.

Before submission, exclude `.env` and API keys from the repository and backup ZIP, and check saved artifacts for secrets.


See ANSWER_FIXES.md for the subsequent failure audit, exact citation-link repair, revised judge and preserved before/after experiments.
