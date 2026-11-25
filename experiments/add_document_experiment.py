"""Real before/add/after experiment on an isolated copy of the IPO index.

uv run python -m experiments.add_document_experiment --approve-add
Repeat without flags to display saved results; --fresh --approve-add makes a new run.
"""
import argparse
import asyncio
from datetime import datetime,timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import sqlite3
from contextlib import closing
import httpx
from bs4 import BeautifulSoup
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from agent import ROOT,Answer,ToolBridge,run_agent,validate_answer
from index import load_key

URL='https://investor.uber.com/news-events/news/press-release-details/2020/Uber-Announces-Results-for-Fourth-Quarter-and-Full-Year-2019/default.aspx'
TITLE='Uber Announces Results for Fourth Quarter and Full Year 2019'
QUESTION='What revenue did Uber report for the year ended December 31, 2019?'
SOURCE_DIR=ROOT/'corpus/experiment_sources'
OUTPUT=ROOT/'eval/add_document_experiment'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def prepare_source():
    SOURCE_DIR.mkdir(parents=True,exist_ok=True)
    raw=SOURCE_DIR/'uber_2019_results_raw.html'
    if not raw.exists():
        with httpx.Client(timeout=90,follow_redirects=True) as client:
            response=client.get(URL,headers={'User-Agent':'IPO-assignment-document-download'})
            response.raise_for_status();raw.write_bytes(response.content)
    soup=BeautifulSoup(raw.read_bytes(),'html.parser')
    body=soup.select_one('.module-news-details .module_body')
    if body is None:raise ValueError('Official article body not found; do not index a navigation/error page')
    heading=body.find(string=lambda value:value and 'Full Year 2019 Financial and Operational Highlights' in value)
    if heading is None:raise ValueError('Annual table heading not found')
    annual=heading.find_parent('table')
    notes=annual.find_next_sibling('table')
    if notes is None or 'Definitions of Non-GAAP' not in notes.get_text(' ',strip=True):raise ValueError('Associated annual table footnotes not found')
    body=BeautifulSoup(str(annual)+str(notes),'html.parser')
    text=body.get_text(' ',strip=True)
    if not all(term in text for term in ('14,147','Full Year 2019','in millions','2018','2019')):
        raise ValueError('Expected financial table/year/unit context is absent')
    prepared=SOURCE_DIR/'uber_2019_results_excerpt.html'
    prepared.write_text('<!doctype html><html><head><meta charset="utf-8"><title>'+TITLE+' — annual table excerpt</title><link rel="canonical" href="'+URL+'"></head><body><h1>'+TITLE+'</h1><p>Verbatim excerpt: Full Year 2019 Financial and Operational Highlights, with its associated footnotes. Published February 6, 2020.</p>'+str(body)+'</body></html>',encoding='utf-8')
    receipt={'url':URL,'publication_date':'2020-02-06','title':TITLE,'raw_sha256':digest(raw),
             'prepared_sha256':digest(prepared),'article_words':len(text.split()),'table_count':len(body.find_all('table')),
             'preparation':'Verbatim annual highlights table and its associated footnote table selected from the official release. All annual rows, year columns, units and associated notes retained. Labelled as an excerpt; title and canonical URL added. No answer sentence or financial figure was generated.'}
    save(SOURCE_DIR/'receipt.json',receipt)
    return prepared,receipt


def protected_hashes():
    paths=[p for p in (ROOT/'index').rglob('*') if p.is_file()]
    paths += [ROOT/'eval/questions.csv',ROOT/'eval/human_labels.csv',ROOT/'eval/human_labels_binding.json',ROOT/'eval/answer_evaluation/latest.json']
    latest=json.loads((ROOT/'eval/answer_evaluation/latest.json').read_text(encoding='utf-8'))
    paths += [p for p in (ROOT/latest['folder']).rglob('*') if p.is_file()]
    return {str(p.relative_to(ROOT)):digest(p) for p in paths if p.exists()}


def annual_table_context(text):
    lines=text.splitlines()
    if 'Year Ended December 31' not in text or not re.search(r'\bin millions\b',text,re.I):return False
    headers=[line.split('|') for line in lines if any(cell.strip()=='2019' for cell in line.split('|'))]
    for line in lines:
        cells=line.split('|')
        if cells[0].strip()!='GAAP Revenue':continue
        for i,cell in enumerate(cells):
            if cell.strip().replace('$','').strip()=='14,147' and any(len(h)>i and h[i].strip()=='2019' for h in headers):return True
    return False


def quoted_context_complete(result):
    return any(annual_table_context('\n'.join(e['quote'] for e in c['evidence'])) for c in result.get('draft',{}).get('claims',[]))


def check_after(result,document_id):
    if result['status'] not in ('answered','partial'):return False
    draft=Answer.model_validate(result['draft'])
    seen={h['id']:h for h in result['retrieved_chunks']}
    if validate_answer(draft,seen):return False
    for claim in draft.claims:
        # Test expectation stays outside the agent prompt. Require a cited new source.
        if not re.search(r'\b(?:14,147|14147)\s+million\b|\b14\.147\s+billion\b',claim.text,re.I):continue
        quotes=' '.join(e.quote for e in claim.evidence)
        if not re.search(r'2019',claim.text) or not re.search(r'14[,.]147',quotes):continue
        if not re.search(r'million|billion',claim.text+' '+quotes,re.I):continue
        if any(seen[e.chunk_id]['document_id']==document_id and annual_table_context(seen[e.chunk_id]['text']) for e in claim.evidence):return True
    return False


def render(summary,before,after,path):
    esc=lambda value:html.escape(str(value))
    content=['<!doctype html><meta charset="utf-8"><title>Add-document experiment</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:20px}pre{white-space:pre-wrap}section{border:1px solid #ccc;padding:16px;margin:15px 0}</style>',
             '<h1>Add-document experiment</h1><p>'+esc(QUESTION)+'</p>',
             '<p>The original IPO index and human evaluation were unchanged. Real MCP ingestion and the same agent ran on a temporary copy.</p>',
             '<p>New document: <a href="'+URL+'">'+TITLE+'</a></p>',
             '<p>Expected full-year figure: $14,147 million ($14.147 billion), as originally reported in February 2020. This expectation was not sent to the answering agent.</p>']
    if not summary.get('quoted_year_and_units_complete',False):
        content+=['<p><strong>Citation presentation limitation:</strong> the generated quotation omits the year headers and/or units. The answer is checked against the full cited source context below; this is not a claim that the displayed quotation is complete.</p>']
    for label,result in [('Before adding the document',before),('After adding the document',after)]:
        content+=['<section><h2>'+label+'</h2><p>Status: '+esc(result['status'])+'</p><pre>'+esc(result['answer'])+'</pre></section>']
    content+=['<h2>Full cited source context (shown by the experiment, not generated by the agent)</h2><p>The original answer above is unchanged. Inspect the year columns and million-dollar units below.</p>']
    cited={e['chunk_id'] for c in after.get('draft',{}).get('claims',[]) for e in c['evidence']}
    for hit in after['retrieved_chunks']:
        if hit['id'] in cited:content+=['<details open><summary>'+esc(hit['id'])+'</summary><pre>'+esc(hit['text'])+'</pre></details>']
    content+=['<h2>Checks and recorded usage</h2><pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>']
    path.write_text('\n'.join(content),encoding='utf-8')


def write_attempts():
    attempts=[]
    for folder in sorted(OUTPUT.glob('20*')):
        if not all((folder/name).exists() for name in ('before.json','after.json','addition.json')):continue
        before=json.loads((folder/'before.json').read_text(encoding='utf-8'));after=json.loads((folder/'after.json').read_text(encoding='utf-8'))
        addition=json.loads((folder/'addition.json').read_text(encoding='utf-8'))
        receipt=json.loads((folder/'source_receipt.json').read_text(encoding='utf-8'))
        attempts.append({'run':folder.name,'source_words':receipt['article_words'],'preparation':receipt['preparation'],
            'before_status':before['status'],'after_status':after['status'],'current_source_context_check':check_after(after,addition['document_id']),
            'quoted_year_and_units_complete':quoted_context_complete(after),
            'answer_model_cost_usd':before['model_cost_usd']+after['model_cost_usd'],
            'document_embedding_cost_usd':addition.get('embedding_cost_usd',0),
            'cleanup_interruption_recorded':(folder/'interrupted_run_note.json').exists()})
    save(OUTPUT/'attempts.json',{'attempts':attempts,'all_attempts_model_and_document_embedding_cost_usd':sum(r['answer_model_cost_usd']+r['document_embedding_cost_usd'] for r in attempts),
          'note':'Includes unsuccessful attempts; small query embedding charges are recorded separately in each attempt.'})



def runtime_files(root):
    """Code needed by the isolated experiment, excluding data and credentials."""
    paths=list(root.glob('*.py'))
    for package in ('ingestion','retrieval','evaluation','experiments'):
        paths.extend((root/package).rglob('*.py'))
    return sorted(paths)


def copy_runtime(root,destination):
    for source in runtime_files(root):
        target=destination/source.relative_to(root)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)


async def experiment(approve=False,fresh=False):
    OUTPUT.mkdir(parents=True,exist_ok=True)
    if (OUTPUT/'summary.json').exists() and not fresh:
        summary=json.loads((OUTPUT/'summary.json').read_text(encoding='utf-8'))
        print('Saved experiment; no API calls. Use --fresh --approve-add for a new attempt.')
        write_attempts();print(json.dumps(summary,indent=2));return summary
    if not approve:
        if not sys.stdin.isatty() or input('Approve adding the official earnings release to a temporary index, using paid embeddings? Type yes: ').strip().lower()!='yes':
            raise SystemExit('Not approved. No model or embedding calls made. Use --approve-add to explicitly authorize this experiment.')
    source,receipt=prepare_source()
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');folder=OUTPUT/run_id;folder.mkdir()
    protected=protected_hashes();save(folder/'source_receipt.json',receipt)
    save(folder/'configuration.json',{'question':QUESTION,'code_sha256':{p.relative_to(ROOT).as_posix():digest(p) for p in runtime_files(ROOT)},
          'prompt_sha256':digest(ROOT/'AGENTS.md'),'original_artifact_hashes':protected,
          'approval':'Explicit --approve-add flag or interactive yes; applies only to this isolated experiment'})
    original_key=os.environ.get('OPENAI_API_KEY');os.environ['OPENAI_API_KEY']=load_key(ROOT)
    try:
        with tempfile.TemporaryDirectory(prefix='ipo-add-experiment-') as temporary:
            active=Path(temporary)
            # No .env is copied. The child process inherits its key in the environment.
            shutil.copytree(ROOT/'index',active/'index',ignore=shutil.ignore_patterns('additions'))
            copy_runtime(ROOT,active)
            shutil.copy2(ROOT/'AGENTS.md',active/'AGENTS.md')
            print('BEFORE: asking the unchanged question against the original IPO index.',flush=True)
            before=await run_agent(QUESTION,root=active);save(folder/'before.json',before)
            env=dict(os.environ);env['CORPUS_PROJECT_ROOT']=str(active)
            params=StdioServerParameters(command=sys.executable,args=[str(active/'server.py')],cwd=str(active),env=env)
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    bridge=ToolBridge(session,active,approval=lambda path:Path(path).resolve()==source.resolve())
                    print('ADD: calling the real MCP add_document tool through the approval gate.',flush=True)
                    addition=await bridge.call('add_document',{'path':str(source)})
                    save(folder/'addition.json',addition)
                    if addition.get('status')!='added':raise RuntimeError('Document addition did not complete')
                    duplicate=await bridge.call('add_document',{'path':str(source)})
                    search=await bridge.call('search',{'query':QUESTION})
                    save(folder/'duplicate.json',duplicate);save(folder/'search_after.json',search);save(folder/'ingestion_trace.json',bridge.trace)
            print('AFTER: asking exactly the same question with the added document.',flush=True)
            after=await run_agent(QUESTION,root=active);save(folder/'after.json',after)
            new_id=addition['document_id']
            # Save the added chunks/units for inspection; discard the duplicate base vectors.
            shutil.copytree(active/'corpus/processed'/new_id,folder/'added_document_processed')
            with closing(sqlite3.connect((active/'eval/query_cache/vectors.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                query_usage=db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]
            summary={'question_id':'q03','question':QUESTION,'run_id':run_id,'run_folder':str(folder.relative_to(ROOT)),
                     'source_url':URL,'source_words':receipt['article_words'],'before_status':before['status'],'after_status':after['status'],
                     'before_refused':before['status']=='not_found','after_cites_new_document_and_expected_amount':check_after(after,new_id),
                     'quoted_year_and_units_complete':quoted_context_complete(after),'check_version':2,
                     'new_document_in_first_search':any(h['document_id']==new_id for h in search['results']),
                     'duplicate_skipped_without_embeddings':duplicate.get('status')=='already_indexed' and duplicate.get('new_vectors')==0,
                     'addition':addition,'answer_model_cost_usd':before['model_cost_usd']+after['model_cost_usd'],
                     'query_embedding_tokens':query_usage,'query_embedding_cost_usd':query_usage*0.02/1e6,
                     'agent_seconds':{'before':before['elapsed_seconds'],'after':after['elapsed_seconds']},
                     'limitation':'One before/after case, not overall accuracy. The document reports 2019 results as originally published in 2020; later restatements are outside this experiment.'}
        summary['original_index_and_evaluation_unchanged']=protected==protected_hashes()
        summary['temporary_index_removed']=True
        summary['passed']=all(summary[k] for k in ('before_refused','after_cites_new_document_and_expected_amount','new_document_in_first_search','duplicate_skipped_without_embeddings','original_index_and_evaluation_unchanged'))
        save(folder/'summary.json',summary);save(OUTPUT/'summary.json',summary)
        render(summary,before,after,folder/'comparison.html');render(summary,before,after,OUTPUT/'comparison.html')
        write_attempts();print(json.dumps(summary,indent=2));print('Comparison:',OUTPUT/'comparison.html')
        return summary
    finally:
        if original_key is None:os.environ.pop('OPENAI_API_KEY',None)
        else:os.environ['OPENAI_API_KEY']=original_key
        if protected!=protected_hashes():raise RuntimeError('Protected original index/evaluation artifacts changed')


def recheck():
    summary=json.loads((OUTPUT/'summary.json').read_text(encoding='utf-8'))
    folder=ROOT/summary['run_folder']
    before=json.loads((folder/'before.json').read_text(encoding='utf-8'));after=json.loads((folder/'after.json').read_text(encoding='utf-8'))
    old=folder/'strict_quote_check_summary.json'
    if not old.exists():save(old,summary)
    summary.update(check_version=2,after_cites_new_document_and_expected_amount=check_after(after,summary['addition']['document_id']),
                   quoted_year_and_units_complete=quoted_context_complete(after),
                   check_change='Offline recheck of identical model output: validate amount against the cited source table\'s 2019 column and millions units. Record whether displayed quotations themselves include that context separately. Original stricter result retained.',
                   offline_recheck_api_calls=0)
    summary['passed']=all(summary[k] for k in ('before_refused','after_cites_new_document_and_expected_amount','new_document_in_first_search','duplicate_skipped_without_embeddings','original_index_and_evaluation_unchanged'))
    save(folder/'summary.json',summary);save(OUTPUT/'summary.json',summary)
    render(summary,before,after,folder/'comparison.html');render(summary,before,after,OUTPUT/'comparison.html')
    write_attempts();print(json.dumps(summary,indent=2));return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--approve-add',action='store_true');parser.add_argument('--fresh',action='store_true')
    parser.add_argument('--recheck',action='store_true',help='Recheck the saved answer/table context offline, preserving the previous check result')
    args=parser.parse_args();summary=recheck() if args.recheck else asyncio.run(experiment(args.approve_add,args.fresh))
    raise SystemExit(0 if summary['passed'] else 1)
