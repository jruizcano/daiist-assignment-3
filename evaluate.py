"""Retrieval comparison plus cached answer/judge/human-agreement evaluation."""
import argparse
from collections import Counter
import csv
import hashlib
import html
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import time
from retrieval.search import Retriever
from index import MODEL, DIMENSIONS, PRICE_PER_MILLION

ROOT = Path(__file__).resolve().parent
DESIGNS = ('a_fixed', 'b_paragraph', 'c_heading_path')


def parts(value):
    return set(value.split(';')) if value else set()


def score_question(question, hits, k):
    if question['kind'] == 'unanswerable':
        return {'section_hit': None, 'all_expected_sources_hit': None}
    sources = parts(question['expected_source'])
    sections = parts(question['expected_location'])
    matched = {c['document_id'] for c in hits[:k]
               if c['document_id'] in sources and c['section'] in sections}
    return {'section_hit': bool(matched), 'all_expected_sources_hit': sources <= matched}


def aggregate(results):
    answerable = [r for r in results if r['kind'] != 'unanswerable']
    multi_doc = [r for r in answerable if len(parts(r['expected_source'])) > 1]
    summary = {}
    for k in (1, 3, 5):
        n = sum(r['metrics'][str(k)]['section_hit'] for r in answerable)
        all_sources = sum(r['metrics'][str(k)]['all_expected_sources_hit'] for r in multi_doc)
        summary[str(k)] = {'section_hits': n, 'answerable_questions': len(answerable),
                           'section_hit_rate': n / len(answerable) if answerable else None,
                           'multi_document_all_source_hits': all_sources,
                           'multi_document_questions': len(multi_doc),
                           'multi_document_all_source_rate': all_sources / len(multi_doc) if multi_doc else None,
                           'mean_retrieved_words': sum(sum(len(c['text'].split()) for c in r['hits'][:k]) for r in results) / len(results)}
    return summary


def write_review(results, summary, path):
    esc = lambda value: html.escape(str(value))
    content = ['<!doctype html><html lang="en"><meta charset="utf-8"><title>Retrieval comparison</title>',
               '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}table{border-collapse:collapse}td,th{padding:10px;border:1px solid #ccc}pre{white-space:pre-wrap;line-height:1.5}details{padding:12px;border:1px solid #ddd;margin:10px 0}.meta{color:#555}</style>',
               '<h1>Retrieval comparison</h1><p>Section matches are a proxy, not proof that the chunk answers the question. '
               'Unanswerable questions are excluded from hit rates. No answer generation or judge was run.</p>',
               '<table><tr><th>Design</th><th>Section hit@1</th><th>Section hit@3</th><th>Section hit@5</th><th>All companies@5 (multi-document)</th></tr>']
    for design, metrics in summary['designs'].items():
        content.append('<tr><td>' + esc(design) + '</td>' + ''.join(f'<td>{metrics[str(k)]["section_hits"]}/{metrics[str(k)]["answerable_questions"]} ({metrics[str(k)]["section_hit_rate"]:.1%})</td>' for k in (1,3,5))
                       + f'<td>{metrics["5"]["multi_document_all_source_hits"]}/{metrics["5"]["multi_document_questions"]}</td></tr>')
    content.append('</table><p>Each question below includes its reference answer and the full top-five passages for each design. '
                   'Review whether the passages actually support the answer, including units, periods and required companies.</p>')
    grouped = {}
    for result in results:
        grouped.setdefault(result['id'], []).append(result)
    for qid, group in grouped.items():
        q = group[0]
        content.append(f'<details><summary><b>{esc(qid)} — {esc(q["question"])}</b></summary><p>Kind: {esc(q["kind"])}</p><p>Reference: {esc(q["reference_answer"])}</p><p>Expected documents: {esc(q["expected_source"])}<br>Expected sections: {esc(q["expected_location"])}</p>')
        for result in group:
            content.append(f'<h3>{esc(result["design"])}</h3>')
            for c in result['hits']:
                match = c['document_id'] in parts(q['expected_source']) and c['section'] in parts(q['expected_location'])
                content.append(f'<details><summary>#{c["rank"]} {esc(c["company"])} — {esc(c["section"])} — cosine {c["score"]:.4f} — section match: {match}</summary><p class="meta">{esc(c["id"])}<br>{esc(" > ".join(c["heading_path"]))}</p><p><a href="{esc(c["citation_url"])}">Original source</a></p><pre>{esc(c["text"])}</pre></details>')
        content.append('</details>')
    content.append('</html>')
    path.write_text('\n'.join(content), encoding='utf-8')


def evaluate(root=ROOT):
    root = Path(root)
    question_path = root / 'eval/questions.csv'
    with question_path.open(encoding='utf-8-sig', newline='') as stream:
        questions = list(csv.DictReader(stream))
    if not questions or len({q['id'] for q in questions}) != len(questions):
        raise ValueError('Missing questions or duplicate IDs')
    retriever = Retriever(root)
    pairs = {(c['document_id'], c['section']) for c in retriever.records['a_fixed']}
    for q in questions:
        if q['kind'] not in ('single', 'multi', 'unanswerable'):
            raise ValueError(f'Invalid kind for {q["id"]}')
        if q['kind'] != 'unanswerable':
            sources, sections = parts(q['expected_source']), parts(q['expected_location'])
            if not sources or not sections or any(not any((s,l) in pairs for l in sections) for s in sources):
                raise ValueError(f'Unknown expected source/section for {q["id"]}')
    started = time.perf_counter()
    vectors = retriever.embed_queries([q['question'] for q in questions])
    query_time = time.perf_counter() - started
    results, summary = [], {'stage': 'retrieval_only', 'model': MODEL, 'dimensions': DIMENSIONS,
        'question_counts': dict(Counter(q['kind'] for q in questions)),
        'questions_sha256': hashlib.sha256(question_path.read_bytes()).hexdigest(),
        'method': 'Unfiltered cosine search across all three documents; identical question vectors for all designs; no reranking.',
        'metric': 'Section hit@k requires expected document AND expected section on the same returned chunk. Broad-section proxy only.',
        'multi_document_metric': 'Each expected document must have a section-matching chunk. Does not prove all claims/passages are present.',
        'limitations': ['Exact passage relevance and full multi-passage sufficiency require human review.',
                       'Alternative correct sections absent from expected_location may count as misses.',
                       'Unanswerable questions are retrieved for inspection but excluded from hit rates; refusals need the agent stage.',
                       'The same 30 questions are used for design comparison; this is not held-out performance.'],
        'query_embedding_or_cache_seconds': query_time, 'designs': {}}
    for design in DESIGNS:
        design_results = []
        for q in questions:
            started = time.perf_counter()
            hits = retriever.search_vector(vectors[q['question']], design, 5)
            elapsed = time.perf_counter() - started
            record = {**q, 'design': design, 'search_seconds': elapsed, 'hits': hits,
                      'metrics': {str(k): score_question(q, hits, k) for k in (1,3,5)}}
            design_results.append(record)
        summary['designs'][design] = aggregate(design_results)
        results.extend(design_results)
    with closing(sqlite3.connect(root / 'eval/query_cache/vectors.sqlite3')) as db:
        tokens = db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]
    summary['query_cache_lifetime_tokens'] = tokens
    summary['query_cache_lifetime_cost_usd'] = tokens * PRICE_PER_MILLION / 1_000_000
    destination = root / 'eval'
    (destination / 'retrieval_results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in results), encoding='utf-8')
    (destination / 'retrieval_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    write_review(results, summary, destination / 'retrieval_review.html')
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--retrieval-only', action='store_true')
    args = parser.parse_args()
    evaluate(args.root)
    if not args.retrieval_only:
        import asyncio
        from evaluation.answer_evaluation import evaluate_answers
        asyncio.run(evaluate_answers(args.root))
