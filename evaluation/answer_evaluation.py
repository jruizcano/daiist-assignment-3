"""Cached end-to-end answer evaluation. See ANSWER_EVALUATION.md."""
import argparse
import asyncio
from collections import Counter
import csv
import hashlib
import html
import json
from pathlib import Path
import sqlite3
import time
import shutil
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from agent import Answer, Evidence, MODEL, run_agent, source_quote, validate_answer
from index import load_key, PRICE_PER_MILLION
from evaluation.passage_review import call_judge, digest

ROOT=Path(__file__).resolve().parents[1]


class Judgment(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    correct:Literal[0,1]
    supported:Literal[0,1]
    correct_reason:str
    supported_reason:str
    evidence_sufficiency:Literal['full','partial','none','uncertain']
    failure_categories:list[Literal['retrieval_miss','wrong_answer_from_right_passage','unsupported_claim','wrong_refusal','bad_test_question']]
    failure_reason:str
    evidence:list[Evidence]=Field(max_length=5)


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)


def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def provenance(root):
    names=['agent.py','AGENTS.md','server.py','retrieval/search.py','index.py','ingestion/document_ingestion.py','ingestion/extract_corpus.py','ingestion/chunk_corpus.py','ingestion/heading_paths.py','uv.lock']
    paths=[root/name for name in names]
    paths+=sorted(p for p in (root/'index').rglob('*') if p.is_file() and p.suffix not in ('.lock',) and not p.name.endswith(('-shm','-wal')))
    return {str(p.relative_to(root)):file_hash(p) for p in paths}


def mechanical(result):
    status=result['status']
    if status in ('error','blocked'):
        return {'status':status,'refusal':False,'citation_check':None,'errors':result.get('guardrail_errors',[])}
    try:
        draft=Answer.model_validate(result['draft'])
        errors=validate_answer(draft,{h['id']:h for h in result['retrieved_chunks']})
        if draft.status!=status:errors.append('Draft and final status disagree')
        if status=='action_only':errors.append('Question evaluation requires an answer or refusal, not an action-only result')
    except (ValueError,KeyError,TypeError):errors=['Malformed answer record']
    return {'status':status,'refusal':status=='not_found','citation_check':not errors,'errors':errors}


def judge_payload(question,result,rubric):
    data={'question':question['question'],'kind':question['kind'],'reference_answer':question['reference_answer'],
          'final_answer':result['answer'],'answer_status':result['status'],
          'retrieved_passages':[{'chunk_id':h['id'],'company':h['company'],'section':h['section'],'text':h['text']} for h in result['retrieved_chunks']]}
    schema=Judgment.model_json_schema()
    # OpenAI strict structured output requires closed objects throughout.
    for definition in schema.get('$defs',{}).values():
        if definition.get('type')=='object':definition['additionalProperties']=False
    return {'model':MODEL,'store':False,'reasoning_effort':'medium','max_completion_tokens':8192,
            'messages':[{'role':'system','content':rubric},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],
            'response_format':{'type':'json_schema','json_schema':{'name':'answer_grade','strict':True,'schema':schema}}}


def parse_judge(saved,result):
    errors=[];grade=None
    try:
        choice=saved['response']['choices'][0]
        if choice['finish_reason']!='stop' or choice['message'].get('refusal'):raise ValueError('Incomplete judge response')
        grade=Judgment.model_validate_json(choice['message']['content']).model_dump()
        seen={h['id']:h['text'] for h in result['retrieved_chunks']}
        for e in grade['evidence']:
            if e['chunk_id'] not in seen or not source_quote(e['quote'],seen[e['chunk_id']]):
                errors.append('Judge evidence quote or chunk ID does not match')
        if grade['supported']==0 and 'unsupported_claim' not in grade['failure_categories']:
            errors.append('Unsupported grade requires unsupported_claim diagnosis')
        if grade['correct']==0 and not grade['failure_categories']:errors.append('Incorrect grade requires a failure diagnosis')
    except (ValueError,KeyError,TypeError):errors.append('Malformed or incomplete judge response')
    return {'status':'needs_review' if errors else 'graded','grade':grade,'validation_errors':errors,
            'cost_usd':saved['cost_usd'],'usage':saved['response'].get('usage',{}),'cache_key':saved['cache_key']}


def agreement(rows,labels):
    paired=[(r['judge']['grade'],labels[r['id']]) for r in rows if r['id'] in labels and r.get('judge',{}).get('status')=='graded']
    out={'paired_answers':len(paired)}
    for field in ('correct','supported'):
        matrix=Counter(f'human_{h[field]}_judge_{j[field]}' for j,h in paired)
        n=len(paired);same=sum(j[field]==h[field] for j,h in paired)
        observed=same/n if n else None
        pj=sum(j[field] for j,h in paired)/n if n else 0
        ph=sum(h[field] for j,h in paired)/n if n else 0
        expected=pj*ph+(1-pj)*(1-ph)
        out[field]={'agree':same,'total':n,'agreement':observed,'confusion_counts':dict(matrix),
                    'cohen_kappa':(observed-expected)/(1-expected) if n and expected<1 else None}
    return out


def load_human(root,dataset,ids):
    path=root/'eval/human_labels.csv';binding=root/'eval/human_labels_binding.json'
    if not path.exists():return {},[]
    with path.open(encoding='utf-8-sig',newline='') as f:raw=list(csv.DictReader(f))
    if not raw:return {},[]
    if not binding.exists() or json.loads(binding.read_text(encoding='utf-8')).get('dataset')!=dataset:
        return {},['Human labels are not bound to this answer dataset; excluded from agreement. Import the blind review export.']
    labels=validate_labels(raw,ids)
    return labels,[]


def validate_labels(raw,ids):
    labels={}
    for row in raw:
        if row.get('id') not in ids or row['id'] in labels:raise ValueError('Unknown or duplicate human question ID')
        if str(row.get('correct')) not in ('0','1') or str(row.get('supported')) not in ('0','1'):raise ValueError('Human grades must both be 0 or 1')
        if ('0' in (str(row['correct']),str(row['supported']))) and not row.get('notes','').strip():raise ValueError('Every zero grade requires a note')
        labels[row['id']]={'id':row['id'],'correct':int(row['correct']),'supported':int(row['supported']),'notes':row.get('notes','')}
    return labels


def import_human(root,path,manifest):
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if data['dataset']!=manifest['dataset']:raise ValueError('Human export belongs to different answers; original grades are preserved')
    ids={q['id'] for q in manifest['questions']}
    labels=validate_labels(data['labels'],ids)
    previous,warnings=load_human(root,manifest['dataset'],ids)
    if warnings:raise ValueError(warnings[0])
    for qid,row in labels.items():
        if qid in previous and row!=previous[qid]:raise ValueError(f'Conflicting existing grade for {qid}; resolve explicitly before import')
    previous.update(labels)
    write_json(root/'eval/answer_human_imports'/f'{digest(data)}.json',data)
    path=root/'eval/human_labels.csv'
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['id','correct','supported','notes']);writer.writeheader();writer.writerows(previous.values())
    write_json(root/'eval/human_labels_binding.json',{'dataset':manifest['dataset']})
    print(f'Imported human answer grades: {len(previous)} (none generated by the evaluator)')


def summarize(rows,labels,total):
    graded=[r for r in rows if r.get('judge',{}).get('status')=='graded']
    correct=sum(r['judge']['grade']['correct'] for r in graded)
    unsupported=sum(not r['judge']['grade']['supported'] for r in graded)
    operational=sum(r['mechanical']['status'] in ('blocked','error') for r in rows)
    failures=Counter(c for r in graded for c in r['judge']['grade']['failure_categories'])
    for r in rows:
        if r['mechanical']['status'] in ('blocked','error'):failures['guardrail_block' if r['mechanical']['status']=='blocked' else 'operational_error']+=1
    ua=[r for r in rows if r['kind']=='unanswerable'];ans=[r for r in rows if r['kind']!='unanswerable']
    return {'questions_total':total,'answers_saved':len(rows),'judge_valid_grades':len(graded),
            'judge_needs_review':sum(r.get('judge',{}).get('status')=='needs_review' for r in rows),
            'operational_failures_or_blocks':operational,'judge_correct':correct,
            'correct_rate_over_all_questions':correct/total if len(graded)+operational==total and total else None,
            'correct_rate_among_valid_judge_grades':correct/len(graded) if graded else None,
            'unsupported_final_answers':unsupported,'unsupported_rate_among_valid_judge_grades':unsupported/len(graded) if graded else None,
            'unanswerable_questions':len(ua),'correct_refusals':sum(r['mechanical']['refusal'] and r.get('judge',{}).get('status')=='graded' and r['judge']['grade']['correct']==1 for r in ua),
            'answerable_questions':len(ans),'answerable_refusal_count':sum(r['mechanical']['refusal'] for r in ans),
            'failure_category_counts':dict(failures),'human_answers_graded':len(labels),'human_minimum_met':len(labels)>=20,
            'human_judge_agreement':agreement(rows,labels),
            'answer_model_cost_usd':sum(r['result']['model_cost_usd'] for r in rows),
            'judge_model_cost_usd':sum(r.get('judge',{}).get('cost_usd',0) for r in rows),
            'mean_agent_seconds':sum(r['result']['elapsed_seconds'] for r in rows)/len(rows) if rows else None,
            'note':'AI grades, not verified accuracy. Failure categories overlap. Refusal counts are not necessarily correct refusals. Same questions used for design selection; no held-out set.'}


def write_blind_review(rows,manifest,path):
    esc=lambda x:html.escape(str(x))
    content=['<!doctype html><meta charset="utf-8"><title>Human answer review</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:20px}pre{white-space:pre-wrap}article,details{border:1px solid #ccc;padding:12px;margin:12px 0}textarea{width:95%}select{margin:12px}button{padding:12px}</style>',
    '<h1>Human answer review — judge grades hidden</h1><p>Grade at least 20 answers before opening judge_results.jsonl. Correct = fully answers the question or correctly refuses. Supported = every factual claim has retrieved support. Partial answers get correct = 0. Pure uncertainty has supported = 1. Explain every zero. This page saves entries locally; export and import to record them in the project.</p><button onclick="download()">Export human answer grades</button>']
    for row in rows:
        q=row['question'];r=row['result'];qid=esc(row['id'])
        content.append(f'<article data-id="{qid}"><h2>{qid}: {esc(q["question"])}</h2><p>Reference (not evidence): {esc(q["reference_answer"])}</p><pre>{esc(r["answer"])}</pre><p>Run status: {esc(r["status"])}</p><label>Correct<select class="correct"><option value="">Not graded</option><option>1</option><option>0</option></select></label><label>Supported<select class="supported"><option value="">Not graded</option><option>1</option><option>0</option></select></label><textarea placeholder="Your reason, required for any zero"></textarea>')
        for h in r['retrieved_chunks']:content.append(f'<details><summary>{esc(h["company"])} — {esc(h["section"])} — {esc(h["id"])}</summary><pre>{esc(h["text"])}</pre></details>')
        content.append('</article>')
    content.append('<script>const dataset='+json.dumps(manifest['dataset'])+''';
const key='answer-review-'+dataset;const articles=[...document.querySelectorAll('article')];
function collect(){return articles.map(a=>({id:a.dataset.id,correct:a.querySelector('.correct').value,supported:a.querySelector('.supported').value,notes:a.querySelector('textarea').value}));}
try{const saved=JSON.parse(localStorage.getItem(key)||'[]');for(const r of saved){const a=articles.find(a=>a.dataset.id===r.id);if(a){a.querySelector('.correct').value=r.correct;a.querySelector('.supported').value=r.supported;a.querySelector('textarea').value=r.notes;}}}catch(e){}
document.addEventListener('input',()=>{try{localStorage.setItem(key,JSON.stringify(collect()));}catch(e){}});
function download(){const labels=collect().filter(r=>r.correct!==''||r.supported!=='');if(labels.some(r=>r.correct===''||r.supported===''||((r.correct==='0'||r.supported==='0')&&!r.notes.trim()))){alert('Complete both grades and explain every zero.');return;}const blob=new Blob([JSON.stringify({dataset,labels},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='answer_human_reviews.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
</script>''')
    path.write_text('\n'.join(content),encoding='utf-8')


def query_tokens(root):
    path=root/'eval/query_cache/vectors.sqlite3'
    if not path.exists():return 0
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        return db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]


def publish(root,folder,manifest):
    rows=[]
    for q in manifest['questions']:
        path=folder/'answers'/f'{q["id"]}.json'
        if not path.exists():continue
        saved=json.loads(path.read_text(encoding='utf-8'));result=saved['result']
        row={'id':q['id'],'kind':q['kind'],'question':q,'result':result,'mechanical':mechanical(result)}
        judge_path=folder/'judges'/f'{q["id"]}.json'
        if judge_path.exists():row['judge']=json.loads(judge_path.read_text(encoding='utf-8'))
        rows.append(row)
    labels,warnings=load_human(root,manifest['dataset'],{q['id'] for q in manifest['questions']})
    summary=summarize(rows,labels,len(manifest['questions']));summary.update(dataset=manifest['dataset'],warnings=warnings)
    write_json(folder/'summary.json',summary)
    (folder/'answers.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    (folder/'judge_results.jsonl').write_text(''.join(json.dumps({'id':r['id'],**r['judge']},ensure_ascii=False)+'\n' for r in rows if 'judge' in r),encoding='utf-8')
    write_blind_review(rows,manifest,folder/'human_review.html')
    with (folder/'per_question.csv').open('w',encoding='utf-8',newline='') as f:
        fields=['id','kind','status','citation_check','correct','supported','failure_categories','answer_cost_usd','judge_cost_usd','agent_seconds','input_tokens','output_tokens']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in rows:
            result=r['result'];j=r.get('judge',{});g=j.get('grade') or {}
            writer.writerow({'id':r['id'],'kind':r['kind'],'status':result['status'],'citation_check':r['mechanical']['citation_check'],
                             'correct':g.get('correct') if j.get('status')=='graded' else '', 'supported':g.get('supported') if j.get('status')=='graded' else '',
                             'failure_categories':';'.join(g.get('failure_categories',[])), 'answer_cost_usd':result['model_cost_usd'],
                             'judge_cost_usd':j.get('cost_usd',0),'agent_seconds':result['elapsed_seconds'],
                             'input_tokens':result['tokens']['input'],'output_tokens':result['tokens']['output_including_reasoning']})
    return summary


async def evaluate_answers(root=ROOT,offline=False,limit=None,import_path=None,reuse_answers_from=None):
    root=Path(root);base=root/'eval/answer_evaluation';base.mkdir(exist_ok=True)
    latest=base/'latest.json'
    if offline or import_path:
        if not latest.exists():raise ValueError('Run the evaluation first')
        folder=root/json.loads(latest.read_text(encoding='utf-8'))['folder']
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if import_path:import_human(root,Path(import_path),manifest)
        summary=publish(root,folder,manifest)
        print(json.dumps(summary,indent=2));return summary
    with (root/'eval/questions.csv').open(encoding='utf-8-sig',newline='') as f:questions=list(csv.DictReader(f))
    if not questions or len({q['id'] for q in questions})!=len(questions):raise ValueError('Empty test set or duplicate IDs')
    if any(not q['id'].isalnum() for q in questions):raise ValueError('Question IDs must be alphanumeric')
    rubric=(root/'answer_rubric.txt').read_text(encoding='utf-8')
    config={'questions':questions,'pipeline_files':provenance(root),'rubric':rubric,'evaluator_sha256':file_hash(Path(__file__)),'model':MODEL}
    if reuse_answers_from:
        reuse_answers_from=Path(reuse_answers_from)
        old=json.loads((reuse_answers_from/'manifest.json').read_text(encoding='utf-8'))
        if old['questions']!=questions:raise ValueError('Cannot reuse answers for different questions')
        config['pipeline_files']=old['pipeline_files']
        config['answer_origin_dataset']=old['dataset']
        config['reused_answer_hashes']={p.name:file_hash(p) for p in sorted((reuse_answers_from/'answers').glob('*.json'))}
        if len(config['reused_answer_hashes'])!=len(questions):raise ValueError('Reuse requires a complete answer dataset')
    dataset=digest(config);folder=base/dataset[:16];folder.mkdir(exist_ok=True)
    manifest={'dataset':dataset,**config};write_json(folder/'manifest.json',manifest)
    write_json(latest,{'dataset':dataset,'folder':str(folder.relative_to(root))})
    for name in ('answers','judges','judge_cache'):(folder/name).mkdir(exist_ok=True)
    if reuse_answers_from:
        for source in (reuse_answers_from/'answers').glob('*.json'):
            target=folder/'answers'/source.name
            if not target.exists():shutil.copy2(source,target)
    completed=0;key=None;started=time.perf_counter();before_tokens=query_tokens(root)
    selected=questions[:limit] if limit else questions
    print(f'Answer evaluation: {len(selected)}/{len(questions)} questions; dataset {dataset[:16]}. Saved answers/judgments are reused.',flush=True)
    try:
        for q in selected:
            answer_path=folder/'answers'/f'{q["id"]}.json'
            if answer_path.exists():result=json.loads(answer_path.read_text(encoding='utf-8'))['result'];cached=True
            else:
                print(f'{q["id"]}: generating answer',flush=True)
                result=await run_agent(q['question'],root=root,approval=None)
                write_json(answer_path,{'question':q,'result':result});cached=False
            check=mechanical(result);judge_path=folder/'judges'/f'{q["id"]}.json'
            if not judge_path.exists() and check['citation_check'] is True:
                payload=judge_payload(q,result,rubric);cache_path=folder/'judge_cache'/f'{digest(payload)}.json'
                if not cache_path.exists() and key is None:key=load_key(root)
                t=time.perf_counter()
                saved=await asyncio.to_thread(call_judge,payload,cache_path,key)
                judged=parse_judge(saved,result);judged['elapsed_seconds']=time.perf_counter()-t
                write_json(judge_path,judged)
            completed+=1
            print(f'{q["id"]}: {result["status"]}; {"cached" if cached else "new"}; judge '+('saved' if judge_path.exists() else 'not applicable'),flush=True)
            publish(root,folder,manifest)
    finally:
        summary=publish(root,folder,manifest)
        history_path=folder/'invocations.json';history=json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else []
        history.append({'completed_this_invocation':completed,'elapsed_seconds':time.perf_counter()-started,
                        'new_query_embedding_tokens':query_tokens(root)-before_tokens,
                        'new_query_embedding_cost_usd':(query_tokens(root)-before_tokens)*PRICE_PER_MILLION/1e6})
        write_json(history_path,history)
    print(json.dumps(summary,indent=2));print(f'Blind human review: {folder / "human_review.html"}')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--offline',action='store_true',help='Recount latest saved run without API calls')
    parser.add_argument('--limit',type=int,help='First N questions for an explicitly partial smoke run')
    parser.add_argument('--import-human',type=Path,help='Import the blind page export and recount offline')
    parser.add_argument('--reuse-answers-from',type=Path,help='Rejudge a complete saved dataset without regenerating answers')
    args=parser.parse_args()
    if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
    asyncio.run(evaluate_answers(args.root,args.offline,args.limit,args.import_human,args.reuse_answers_from))
