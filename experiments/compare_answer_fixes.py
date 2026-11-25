"""Reproduce the failure audit and controlled judge-only/full-system comparisons.

uv run python -m experiments.compare_answer_fixes --run  # cached paid runs when missing
uv run python -m experiments.compare_answer_fixes        # offline audit and comparison
"""
import argparse
import asyncio
from collections import Counter
import html
import json
from pathlib import Path
from agent import Answer,repair_citation_links,source_quote,validate_answer
from evaluation.answer_evaluation import ROOT,evaluate_answers,file_hash,write_json

BASELINE='eval/answer_evaluation/e53b846b83c7dce2'


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def audit(root,baseline):
    rows=[]
    for path in sorted((baseline/'answers').glob('*.json')):
        saved=read(path);r=saved['result'];qid=saved['question']['id']
        judge_path=baseline/'judges'/path.name
        j=read(judge_path) if judge_path.exists() else {}
        if r['status']!='blocked' and j.get('status')!='needs_review':continue
        seen={h['id']:h for h in r['retrieved_chunks']}
        row={'id':qid,'question':saved['question']['question'],'answer_status':r['status'],
             'answer_errors':r['guardrail_errors'],'judge_errors':j.get('validation_errors',[]),'quote_diagnostics':[]}
        if r['status']=='blocked':
            fixed,repairs=repair_citation_links(Answer.model_validate(r['draft']),seen)
            row.update(exact_citation_repairs=repairs,remaining_errors_after_relink=validate_answer(fixed,seen))
        for origin,entries in [('answer',[e for c in r.get('draft',{}).get('claims',[]) for e in c['evidence']]),('judge',(j.get('grade') or {}).get('evidence',[]))]:
            for e in entries:
                if e['chunk_id'] in seen and source_quote(e['quote'],seen[e['chunk_id']]['text']):continue
                matches=[h['id'] for h in seen.values() if source_quote(e['quote'],h['text'])]
                row['quote_diagnostics'].append({'origin':origin,**e,'exact_matches_in_other_retrieved_chunks':matches,
                    'finding':'wrong_citation_link' if matches else 'quotation_not_found_in_retrieved_text'})
        rows.append(row)
    return rows


def compare(root,base,state):
    stages={'before':root/BASELINE}
    stages.update({name:root/path for name,path in state.get('stages',{}).items()})
    summaries={name:read(folder/'summary.json') for name,folder in stages.items()}
    rows=[]
    for q in read(stages['before']/'manifest.json')['questions']:
        row={'id':q['id'],'question':q['question']}
        for name,folder in stages.items():
            a=folder/'answers'/f'{q["id"]}.json';j=folder/'judges'/f'{q["id"]}.json'
            result=read(a)['result'] if a.exists() else {}
            judge=read(j) if j.exists() else {}
            row[name]={'answer_status':result.get('status'),'judge_status':judge.get('status'),
                       'answer_errors':result.get('guardrail_errors',[])+result.get('errors',[]),
                       'judge_errors':judge.get('validation_errors',[]),
                       'citation_link_repairs':len(result.get('citation_link_repairs',[])),
                       'correct':(judge.get('grade') or {}).get('correct') if judge.get('status')=='graded' else None,
                       'supported':(judge.get('grade') or {}).get('supported') if judge.get('status')=='graded' else None}
        rows.append(row)
    equal=None
    if 'judge_only' in stages:
        equal=all(file_hash(p)==file_hash(stages['judge_only']/'answers'/p.name) for p in (stages['before']/'answers').glob('*.json'))
        if not equal:raise ValueError('Judge-only comparison changed an answer')
    comparison={'stages':{name:str(folder.relative_to(root)) for name,folder in stages.items()},
                'judge_only_answers_byte_identical':equal,'summaries':summaries,'questions':rows,
                'limitations':'Same questions used for development. Judge-only holds answers/evidence fixed; updated_system is a fresh full run with potentially different retrieval/model outputs. Passing checks is not human verification.'}
    comparison['new_experiment_model_cost_usd']=summaries.get('judge_only',{}).get('judge_model_cost_usd',0)+sum(summaries.get('updated_system',{}).get(k,0) for k in ('answer_model_cost_usd','judge_model_cost_usd'))
    comparison['cost_note']='Stored experiment model usage, excluding original reused answers and query embeddings; cached repeats do not incur this cost again.'
    comparison['selected_answer_replacements']={name:read(folder/'answer_replacements.json') for name,folder in stages.items() if (folder/'answer_replacements.json').exists()}
    if comparison['selected_answer_replacements']:
        comparison['limitations']+=' Selected answers were subsequently replaced at the user\'s request; consult selected_answer_replacements. The current updated results are not an untouched single-run benchmark.'
    write_json(base/'comparison.json',comparison)
    esc=lambda x:html.escape(str(x))
    parts=['<!doctype html><meta charset="utf-8"><title>Answer fixes comparison</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto}td,th{padding:10px;border:1px solid #ccc}table{border-collapse:collapse}pre{white-space:pre-wrap}details{padding:12px;border:1px solid #ccc}</style><h1>Answer and judge fixes</h1>',
           '<p>'+esc(comparison['limitations'])+'</p><p>Judge-only answers byte-identical: '+esc(equal)+'</p><table><tr><th>Stage</th><th>Answers</th><th>Blocked/errors</th><th>Judge valid</th><th>Judge flagged</th><th>Correct among valid</th></tr>']
    for name,s in summaries.items():
        parts.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (name,s['answers_saved'],s['operational_failures_or_blocks'],s['judge_valid_grades'],s['judge_needs_review'],s['judge_correct']))+'</tr>')
        print(f'{name}: {s["answers_saved"]} answers; {s["operational_failures_or_blocks"]} blocked/errors; {s["judge_valid_grades"]} valid judge grades; {s["judge_needs_review"]} flagged; {s["judge_correct"]} correct among valid grades')
    parts.append('</table><h2>Per-question changes</h2>')
    for row in rows:
        links=[]
        for name,folder in stages.items():
            href='../'+folder.relative_to(root/'eval').as_posix()+'/answers/'+row['id']+'.json'
            links.append('<a href="'+esc(href)+'">'+esc(name)+' answer and evidence</a>')
        parts.append('<details><summary>'+esc(row['id']+' — '+row['question'])+'</summary><p>'+' | '.join(links)+'</p><pre>'+esc(json.dumps(row,indent=2))+'</pre></details>')
    parts.append('<h2>Original failure audit (not human grades)</h2>')
    for row in read(base/'audit.json'):parts.append('<details><summary>'+esc(row['id']+' '+row['question'])+'</summary><pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></details>')
    (base/'comparison.html').write_text('\n'.join(parts),encoding='utf-8')
    print(f'Comparison: {base / "comparison.html"}')


async def main(run=False):
    root=ROOT;base=root/'eval/answer_fixes';base.mkdir(exist_ok=True);baseline=root/BASELINE
    state_path=base/'state.json';state=read(state_path) if state_path.exists() else {'baseline':BASELINE,'stages':{}}
    hashes={str(p.relative_to(baseline)):file_hash(p) for p in baseline.rglob('*') if p.is_file()}
    if 'baseline_hashes' in state and state['baseline_hashes']!=hashes:raise ValueError('Original baseline changed; refusing to compare silently')
    state['baseline_hashes']=hashes;write_json(state_path,state)
    findings=audit(root,baseline);write_json(base/'audit.json',findings)
    blocked=[r for r in findings if r['answer_status']=='blocked']
    write_json(base/'audit_summary.json',{'original_blocked_answers':len(blocked),
        'original_flagged_judges':sum(bool(r['judge_errors']) for r in findings),
        'blocked_answers_with_exact_link_repairs':sum(bool(r['exact_citation_repairs']) for r in blocked),
        'blocked_answers_passing_checks_after_exact_relink_only':sum(not r['remaining_errors_after_relink'] for r in blocked),
        'note':'Offline replay of identical old drafts, not a new answer generation or semantic accuracy assessment.'})
    print('Original failures:',dict(Counter('blocked_answer' if r['answer_status']=='blocked' else 'flagged_judge' for r in findings)))
    if run:
        for stage,reuse in [('judge_only',baseline),('updated_system',None)]:
            await evaluate_answers(root,reuse_answers_from=reuse)
            state['stages'][stage]=read(root/'eval/answer_evaluation/latest.json')['folder']
            write_json(state_path,state)
    compare(root,base,state)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true')
    asyncio.run(main(parser.parse_args().run))
