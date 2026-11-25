"""Exercise the real stdio MCP connection: uv run python -m checks.verify_mcp.

--isolated-add-test additionally uses a temporary index and small paid embeddings
to test adding and retrieving a synthetic document without altering the IPO corpus.
"""
import argparse
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime,timezone
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]


def decoded(result):
    if getattr(result,'is_error',False) or getattr(result,'isError',False):
        raise RuntimeError('MCP tool error: '+' '.join(getattr(c,'text','') for c in result.content))
    return json.loads('\n'.join(getattr(c,'text','') for c in result.content))


async def verify(root=ROOT,isolated=False):
    report={'test':'isolated_add_document' if isolated else 'search_and_duplicate',
            'created_utc':datetime.now(timezone.utc).isoformat(),'checks':[]}
    with (root/'eval/questions.csv').open(encoding='utf-8-sig',newline='') as f:
        query=next(csv.DictReader(f))['question']
    vector_hash=hashlib.sha256((root/'index/vectors.sqlite3').read_bytes()).hexdigest()
    temp=None
    env=dict(os.environ)
    if isolated:
        temp=tempfile.TemporaryDirectory(prefix='mcp-integration-')
        active=Path(temp.name)
        shutil.copytree(root/'index',active/'index')
        from index import load_key
        env['OPENAI_API_KEY']=load_key(root)
    else:active=root
    env['CORPUS_PROJECT_ROOT']=str(active)
    params=StdioServerParameters(command=sys.executable,args=[str(root/'server.py')],cwd=str(root),env=env)
    try:
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools=await session.list_tools()
                names=sorted(t.name for t in tools.tools)
                assert names==['add_document','search'],names
                print('PASS: MCP tools registered: '+', '.join(names))
                report['tools']=names;report['checks'].append('stdio initialization and tool discovery')
                if isolated:
                    query='What code name does the synthetic integration-test observatory use?'
                    path=active/'fixture.html'
                    path.write_text('<html><head><title>Integration-test observatory</title></head><body><h1>Synthetic fixture</h1><p>This is a synthetic integration-test document, not financial evidence. The synthetic observatory uses code name Copper Finch 482. Its code name is Copper Finch 482.</p></body></html>',encoding='utf-8')
                    first=decoded(await session.call_tool('add_document',{'path':str(path)}))
                    assert first['status']=='added',first
                    print('PASS: new document added in isolated index: '+json.dumps(first))
                    report['addition']=first;report['checks'].append('new document ingestion via MCP')
                    renamed=active/'renamed.html';renamed.write_bytes(path.read_bytes())
                    duplicate=decoded(await session.call_tool('add_document',{'path':str(renamed)}))
                    assert duplicate['status']=='already_indexed' and duplicate['new_vectors']==0
                    print('PASS: renamed duplicate skipped without new embeddings')
                    report['checks'].append('renamed duplicate skipped')
                results=decoded(await session.call_tool('search',{'query':query}))
                assert results['design']=='b_paragraph' and len(results['results'])==5
                assert all(h['id'] and h['citation_url'] and h['section'] and h['text'] for h in results['results'])
                if isolated:
                    assert any(h['document_id']==first['document_id'] and 'Copper Finch 482' in h['text'] for h in results['results'])
                    print('PASS: newly added evidence retrieved through MCP')
                    report['checks'].append('newly added evidence retrieved')
                else:
                    manifest=json.loads((root/'corpus_sources.json').read_text(encoding='utf-8'))
                    source=manifest['documents'][0]
                    duplicate=decoded(await session.call_tool('add_document',{'path':str(root/'corpus'/source['filename'])}))
                    assert duplicate['status']=='already_indexed' and duplicate['new_vectors']==0
                    print('PASS: original prospectus duplicate skipped')
                    report['checks'].append('original prospectus duplicate skipped')
                print('PASS: search returned five passages with source citations')
                print(json.dumps({'query':query,'top_result':results['results'][0]},ensure_ascii=False,indent=2))
                report['search']={'query':query,'design':results['design'],'result_count':len(results['results']),'top_result':results['results'][0]}
                report['checks'].append('five results with citations')
    finally:
        if temp:temp.cleanup()
    assert hashlib.sha256((root/'index/vectors.sqlite3').read_bytes()).hexdigest()==vector_hash
    print('PASS: original corpus vectors unchanged')
    report['checks'].append('original corpus vectors unchanged')
    report['status']='passed'
    report['temporary_test_index_removed']=isolated
    root.joinpath('eval').mkdir(exist_ok=True)
    destination=root/'eval'/('mcp_add_document_test.json' if isolated else 'mcp_smoke_test.json')
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved verification: '+str(destination))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-add-test',action='store_true')
    args=parser.parse_args()
    asyncio.run(asyncio.wait_for(verify(ROOT,args.isolated_add_test),180))
