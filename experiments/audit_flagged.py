"""Reproduce the saved assistant audit of all 17 original flags, without API calls.

Semantic findings are explicitly assistant-authored in eval/flagged_audit_notes.json.
This script verifies their evidence anchors, diagnoses quote mismatches, counts
the findings and renders a review. It does not fabricate human grades or claim
that semantic judgments were established by substring checks.
"""
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def formatting_normalized(text):
    return normalized(text.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t')).casefold().strip('"')


def main():
    folder = ROOT / 'eval'
    baseline = folder / 'flagged_audit_baseline'
    if not baseline.exists():
        baseline.mkdir()
        for name in ('passage_reviews.jsonl', 'retrieval_results.jsonl', 'passage_review_summary.json'):
            shutil.copy2(folder / name, baseline / name)
        shutil.copy2(ROOT / 'evaluation/passage_review.py', baseline / 'passage_review_before.py')
    reviews = read_jsonl(baseline / 'passage_reviews.jsonl')
    retrieved = {(r['id'],r['design']):r for r in read_jsonl(baseline / 'retrieval_results.jsonl')}
    flagged = {(r['id'],r['design']):r for r in reviews if r['status']=='needs_review'}
    notes = json.loads((folder / 'flagged_audit_notes.json').read_text(encoding='utf-8'))
    assert len(notes)==len(flagged) and {(n['id'],n['design']) for n in notes}==set(flagged), 'Audit notes must cover every baseline flag exactly once.'
    findings = []
    for note in notes:
        key = note['id'],note['design']
        original = flagged[key]
        record = retrieved[key]
        hits = {h['rank']:h for h in record['hits']}
        evidence = []
        for rank,anchor in note['evidence']:
            text = hits[rank]['text']
            assert anchor in text, (key,rank,'Audit evidence anchor not in source',anchor)
            pos = text.index(anchor)
            evidence.append({'rank':rank,'anchor':anchor,'chunk_id':hits[rank]['id'],
                             'source_url':hits[rank]['citation_url'],
                             'context':text[max(0,pos-120):pos+len(anchor)+180]})
        mismatches = []
        for item in original['review']['supported_requirements']:
            rank,quote = item['passage_rank'],item['quote']
            source = hits.get(rank,{}).get('text','')
            if quote.strip() and normalized(quote) in normalized(source):
                continue
            reformatted = formatting_normalized(quote)
            mismatches.append({'cited_rank':rank,'claimed_quote':quote,
                'matches_after_newline_case_wrapper_normalization':bool(reformatted) and reformatted in formatting_normalized(source),
                'matching_ranks_after_same_normalization':[n for n,h in hits.items() if reformatted and reformatted in formatting_normalized(h['text'])]})
        findings.append({**note,'reviewer':'Codex assistant audit; not user human grading',
            'question':record['question'],'original_draft_label':original['review']['label'],
            'original_flag_reasons':original['validation_errors'],'checked_evidence':evidence,
            'quote_diagnostics':mismatches,'passages':record['hits']})
    counts = Counter(f['evidence_verdict'] for f in findings)
    summary = {'total_audited':len(findings),'assistant_evidence_verdict_counts':dict(counts),
        'fully_explained_by_newline_case_wrapper_normalization':sum(bool(f['quote_diagnostics']) and all(q['matches_after_newline_case_wrapper_normalization'] for q in f['quote_diagnostics']) for f in findings),
        'original_flags_unchanged':True,'api_calls':0,
        'semantic_judgment_source':'Assistant-authored notes, not automatic proof or user human grades.',
        'scope':'Only the 17 original flagged assessments; not all 90 results.',
        'source_sha256':{name:hashlib.sha256((baseline/name).read_bytes()).hexdigest() for name in ('passage_reviews.jsonl','retrieval_results.jsonl')},
        'notes_sha256':hashlib.sha256((folder/'flagged_audit_notes.json').read_bytes()).hexdigest()}
    (folder/'flagged_audit.json').write_text(json.dumps({'summary':summary,'findings':findings},ensure_ascii=False,indent=2),encoding='utf-8')
    esc = lambda x:html.escape(str(x))
    out=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Audit of flagged assessments</title>',
         '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}td,th{padding:8px;border:1px solid #ccc}table{border-collapse:collapse}details{padding:12px;border:1px solid #ccc;margin:12px 0}pre{white-space:pre-wrap}.note{background:#fff3cd;padding:14px}</style>',
         '<h1>Audit of the 17 original flagged assessments</h1><p class="note">Assistant judgments for your verification, not your human grades. The original flags and retrieval results are preserved. A sufficient evidence verdict does not mean the AI reviewer quoted or interpreted every fact correctly.</p>',
         '<p>Reproduce counts and checks locally: <code>uv run python -m experiments.audit_flagged</code>. The script checks evidence anchors and counts saved assistant findings; it does not automatically prove the semantic judgments.</p>',
         '<pre>'+esc(json.dumps(summary,indent=2))+'</pre>',
         '<table><tr><th>Question/design</th><th>Evidence verdict</th><th>Flag cause</th></tr>']
    for f in findings:
        ident=f['id']+'-'+f['design']
        out.append(f'<tr><td><a href="#{esc(ident)}">{esc(ident)}</a></td><td>{esc(f["evidence_verdict"])}</td><td>{esc(f["flag_cause"])}</td></tr>')
    out.append('</table>')
    for f in findings:
        out.append(f'<details id="{esc(f["id"]+"-"+f["design"])}"><summary>{esc(f["id"])} · {esc(f["design"])} · {esc(f["evidence_verdict"])}</summary><p><b>{esc(f["question"])}</b></p><p>{esc(f["reason"])}</p><p class="note">{esc(f["reviewer_caution"])}</p>')
        out.append('<h3>Verified source anchors</h3>')
        for e in f['checked_evidence']:
            out.append(f'<p>Passage {e["rank"]} · <a href="{esc(e["source_url"])}">Original source</a></p><pre>{esc(e["context"])}</pre>')
        out.append('<h3>Original quote mismatches</h3>')
        for q in f['quote_diagnostics']:
            out.append(f'<p>Cited passage {q["cited_rank"]}; matches after newline/case/wrapper normalization: {q["matches_after_newline_case_wrapper_normalization"]}</p><pre>{esc(q["claimed_quote"])}</pre>')
        out.append('<details><summary>All five retrieved passages</summary>')
        for h in f['passages']:
            out.append(f'<h4>Passage {h["rank"]}: {esc(h["company"])} · {esc(h["section"])}</h4><pre>{esc(h["text"])}</pre>')
        out.append('</details></details>')
    out.append('</html>')
    (folder/'flagged_audit.html').write_text('\n'.join(out),encoding='utf-8')
    print('AUDIT OF ORIGINAL FLAGS (assistant judgments, not human grades)')
    print(f'Total audited: {len(findings)}')
    for label,count in sorted(counts.items()):print(f'  {label}: {count}')
    print('All quote mismatches explained by newline/case/wrapper formatting:',summary['fully_explained_by_newline_case_wrapper_normalization'])
    for f in findings:
        print(f'{f["id"]} | {f["design"]} | {f["evidence_verdict"]} | {f["flag_cause"]}')
        print('  '+f['reason'])
        print('  Caution: '+f['reviewer_caution'])
    print('Saved eval/flagged_audit.json and eval/flagged_audit.html. Original flags, human decisions and report unchanged. API calls: 0.')


if __name__=='__main__':
    main()
