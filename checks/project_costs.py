"""Total recorded API costs without calling an API. Run: uv run python -m checks.project_costs."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def total_costs(root=ROOT):
    totals = {}
    counts = {}
    seen_runs = set()
    groups = {
        'Agent evaluation runs': sorted(root.glob('eval/answer_evaluation/*/answers/*.json')),
        'Other agent runs': sorted(root.glob('eval/agent_runs/*.json')),
        'Add-document experiment agent runs': sorted(root.glob('eval/add_document_experiment/*/before.json')) + sorted(root.glob('eval/add_document_experiment/*/after.json')),
    }
    for label, paths in groups.items():
        total = 0.0
        count = 0
        for path in paths:
            data = read(path)
            result = data.get('result', data)
            # Identical reused runs retain their timing, usage and question.
            identity = {k: result.get(k) for k in ('question', 'model', 'model_usage', 'elapsed_seconds')}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            if key in seen_runs:
                continue
            seen_runs.add(key)
            total += result.get('model_cost_usd', 0)
            count += 1
        totals[label] = total
        counts[label] = count
    seen_responses = set()
    for label, paths in {
        'Passage-review judge calls': sorted(root.glob('eval/passage_review_cache/*.json')),
        'Answer-evaluation judge calls': sorted(root.glob('eval/answer_evaluation/*/judge_cache/*.json')),
    }.items():
        total = 0.0
        count = 0
        for path in paths:
            data = read(path)
            key = data.get('response', {}).get('id') or hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            if key in seen_responses:
                continue
            seen_responses.add(key)
            total += data.get('cost_usd', 0)
            count += 1
        totals[label] = total
        counts[label] = count
    for label, path in {
        'Original corpus embeddings': root/'index/vectors.sqlite3',
        'Saved query embeddings': root/'eval/query_cache/vectors.sqlite3',
    }.items():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            tokens = db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]
        totals[label] = tokens * 0.02 / 1_000_000
    additions = list(root.glob('eval/add_document_experiment/*/addition.json'))
    totals['Experiment document embeddings'] = sum(read(p).get('embedding_cost_usd', 0) for p in additions)
    query_tokens = 0
    for folder in (root/'eval/add_document_experiment').iterdir():
        if not folder.is_dir():
            continue
        source = folder/'summary.json'
        if not source.exists():
            source = folder/'interrupted_run_note.json'
        if source.exists():
            query_tokens += read(source).get('query_embedding_tokens', 0)
    totals['Recorded experiment query embeddings'] = query_tokens * 0.02 / 1_000_000
    smoke = root/'eval/mcp_add_document_test.json'
    totals['MCP ingestion test embeddings'] = read(smoke).get('addition', {}).get('embedding_cost_usd', 0) if smoke.exists() else 0
    return {'costs_usd': totals, 'unique_saved_calls_or_runs': counts, 'recorded_total_usd': sum(totals.values()),
        'limitations': 'Usage-derived estimate, not an account invoice. Excludes unsaved or overwritten calls, unrecorded temporary query embeddings, Claude Code and ChatGPT subscriptions/usage, taxes and any unrelated API usage. Reused agent results and copied judge responses counted once.'}


if __name__ == '__main__':
    print(json.dumps(total_costs(), indent=2))
