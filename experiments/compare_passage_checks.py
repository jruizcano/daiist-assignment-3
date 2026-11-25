"""Offline before/after comparison on frozen AI assessments. No API calls.

uv run python -m experiments.compare_passage_checks
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import uuid
from evaluation.evidence_checks import VERSION,validate

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root,folder):
    before=folder/'before'
    if before.exists():
        manifest=json.loads((before/'manifest.json').read_text(encoding='utf-8'))
        for name,checksum in manifest['files'].items():
            if sha(before/name)!=checksum:
                raise ValueError('Baseline integrity check failed: '+name)
        return before
    files=['eval/retrieval_results.jsonl','eval/passage_reviews.jsonl','eval/passage_review_summary.json',
           'eval/passage_human_reviews.json','eval/passage_review_rubric.txt','evaluation/passage_review.py','evaluation/human_review.py']
    staging=folder/('baseline-staging-'+uuid.uuid4().hex)
    staging.mkdir(parents=True)
    hashes={}
    for name in files:
        source=root/name
        target=staging/source.name
        shutil.copy2(source,target)
        hashes[target.name]=sha(target)
    (staging/'manifest.json').write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),
             'files':hashes,'description':'Frozen final v1 AI reviews, retrieved evidence and human decisions; never overwritten on rerun.'},indent=2),encoding='utf-8')
    staging.rename(before)
    return before


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def write_rows(path,rows):
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')


def render(comparisons,summary,records,path):
    esc=lambda s:html.escape(str(s))
    output=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Before and after: evidence checks</title>',
      '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:9px}details{border:1px solid #ccc;margin:12px 0;padding:12px}pre{white-space:pre-wrap}blockquote{border-left:3px solid #777;padding-left:12px}.notice{padding:14px;background:#fff3cd}</style>',
      '<h1>Before and after: evidence checks</h1><p class="notice">Same retrieved passages and same AI assessments. Only the mechanical check changed. '
      'Resolving a quote warning does not establish that the answer or reviewer interpretation is correct.</p>',
      f'<p>Assessments: <b>{summary["assessments"]}</b> · Before flagged: <b>{summary["before_flagged"]}</b> · After flagged: <b>{summary["after_flagged"]}</b> · Resolved: <b>{summary["resolved"]}</b> · New flags: <b>{summary["new_flags"]}</b></p>',
      '<table><tr><th>Design</th><th>Before flagged</th><th>After flagged</th><th>Resolved</th></tr>']
    for design,counts in summary['by_design'].items():
        output.append(f'<tr><td>{esc(design)}</td><td>{counts["before_flagged"]}</td><td>{counts["after_flagged"]}</td><td>{counts["resolved"]}</td></tr>')
    output.append('</table><p>Each item below was flagged before or after. Expand it to inspect the exact check, quoted text and source passage. The full JSON outputs include all 90 assessments.</p>')
    for c in comparisons:
        if not c['before_flagged'] and not c['after_flagged']:continue
        source=records[c['id'],c['design']]
        state='RESOLVED' if c['before_flagged'] and not c['after_flagged'] else 'STILL FLAGGED'
        output.append(f'<details><summary><b>{state}: {esc(c["id"])} · {esc(c["design"])}</b> — {esc(source["question"])}</summary>')
        output.append(f'<p>AI label unchanged: {esc(c["ai_label"])}. Your recorded label: {esc(c["human_label"] or "Not reviewed")}.</p>')
        output.append(f'<p>Your notes: {esc(c["human_notes"])}</p>')
        for check in c['after_checks']:
            output.append(f'<h3>Requirement {check["requirement_number"]}: {esc(check["method"])}</h3><p>{esc(check["requirement"])}</p><p>{esc(check["reason"])}</p><blockquote>{esc(check["quote"])}</blockquote>')
            if 'table_evidence' in check:
                output.append('<pre>'+esc(json.dumps(check['table_evidence'],ensure_ascii=False,indent=2))+'</pre>')
            hit=next((h for h in source['hits'] if h['rank']==check['passage_rank']),None)
            if hit:
                output.append(f'<details><summary>Full source passage {hit["rank"]}</summary><pre>{esc(hit["text"])}</pre></details>')
        output.append('</details>')
    output.append('</html>')
    path.write_text('\n'.join(output),encoding='utf-8')


def run(root=ROOT):
    root=Path(root)
    folder=root/'eval/check_comparison'
    folder.mkdir(parents=True,exist_ok=True)
    before=snapshot(root,folder)
    records={(r['id'],r['design']):r for r in read_rows(before/'retrieval_results.jsonl')}
    reviews=read_rows(before/'passage_reviews.jsonl')
    human={(r['id'],r['design'],r['evidence_hash']):r for r in json.loads((before/'passage_human_reviews.json').read_text(encoding='utf-8'))}
    comparisons=[]
    for review in reviews:
        record=records[review['id'],review['design']]
        old_errors,old_details=validate(review['review'],record,version=1)
        if old_errors != review['validation_errors']:
            raise ValueError('v1 check did not reproduce stored errors: '+review['id']+' '+review['design'])
        new_errors,new_details=validate(review['review'],record,version=2)
        user=human.get((review['id'],review['design'],review['input_hash']),{})
        comparisons.append({'id':review['id'],'design':review['design'],'kind':review['kind'],
                            'ai_label':review['review']['label'],'human_label':user.get('label',''),
                            'human_notes':user.get('notes',''),
                            'before_flagged':bool(old_errors),'after_flagged':bool(new_errors),
                            'before_errors':old_errors,'after_errors':new_errors,
                            'before_checks':old_details,'after_checks':new_details})
    def counts(rows):
        return {'assessments':len(rows),'before_flagged':sum(r['before_flagged'] for r in rows),
                'after_flagged':sum(r['after_flagged'] for r in rows),
                'resolved':sum(r['before_flagged'] and not r['after_flagged'] for r in rows),
                'new_flags':sum(not r['before_flagged'] and r['after_flagged'] for r in rows)}
    summary={**counts(comparisons),'validator_version':VERSION,
             'by_design':{d:counts([r for r in comparisons if r['design']==d]) for d in sorted({r['design'] for r in comparisons})},
             'resolved_assessments':[{'id':r['id'],'design':r['design']} for r in comparisons if r['before_flagged'] and not r['after_flagged']],
             'still_flagged':[{'id':r['id'],'design':r['design']} for r in comparisons if r['after_flagged']],
             'new_match_methods':dict(Counter(b['method'] for r in comparisons for a,b in zip(r['before_checks'],r['after_checks']) if not a['matched'] and b['matched'])),
             'api_calls':0,'cost_usd':0,'baseline_manifest_sha256':sha(before/'manifest.json'),
             'validator_sha256':sha(ROOT / 'evaluation/evidence_checks.py'),
             'scope':'Mechanical citation validation only. Retrieval, AI labels, references and human decisions are fixed. No accuracy claim.'}
    old_results=[{'id':r['id'],'design':r['design'],'flagged':r['before_flagged'],'errors':r['before_errors'],'checks':r['before_checks']} for r in comparisons]
    old_path=before/'validation_results.jsonl'
    if old_path.exists():
        if read_rows(old_path)!=old_results:raise ValueError('Saved before-check results differ; baseline not overwritten')
    else:write_rows(old_path,old_results)
    after=folder/'after'
    after.mkdir(exist_ok=True)
    write_rows(after/'validation_results.jsonl',[{'id':r['id'],'design':r['design'],'flagged':r['after_flagged'],'errors':r['after_errors'],'checks':r['after_checks']} for r in comparisons])
    shutil.copy2(ROOT / 'evaluation/evidence_checks.py',after/'evidence_checks.py')
    write_rows(folder/'comparison.jsonl',comparisons)
    (folder/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    render(comparisons,summary,records,folder/'comparison.html')
    print('\nEVIDENCE CHECK COMPARISON (same evidence and AI drafts)')
    print(f'Assessments: {summary["assessments"]}\nFlagged before: {summary["before_flagged"]}\nFlagged after: {summary["after_flagged"]}\nResolved: {summary["resolved"]}\nNew flags: {summary["new_flags"]}')
    for design,c in summary['by_design'].items():print(f'  {design}: {c["before_flagged"]} -> {c["after_flagged"]}')
    for heading,key in [('RESOLVED','resolved_assessments'),('STILL FLAGGED','still_flagged')]:
        print('\n'+heading)
        for r in summary[key]:print(f'  {r["id"]} | {r["design"]}')
    print('\nAPI calls: 0. Cost: $0.00. Original flags and human decisions unchanged.')
    print('Open eval/check_comparison/comparison.html; counts saved in eval/check_comparison/summary.json.')
    return comparisons,summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    run(args.root)
