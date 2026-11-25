"""LangChain answering agent over the real MCP server.

uv run python main.py ask "How does Uber intend to use its IPO proceeds?"
run_agent() returns an answer, evidence, tool trace, usage and elapsed time.
"""
import argparse
import asyncio
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel,Field
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware,wrap_model_call
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage,AIMessage,ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from index import load_key

ROOT=Path(__file__).resolve().parent
MODEL='gpt-5-nano-2025-08-07'
MODEL_LIMIT=6
TOOL_LIMIT=6


class Evidence(BaseModel):
    chunk_id:str
    quote:str=Field(min_length=1,description='Exact full supporting sentence, or complete table row/header/unit statement.')


class Claim(BaseModel):
    text:str
    evidence:list[Evidence]=Field(min_length=1)


class Answer(BaseModel):
    status:Literal['answered','partial','not_found','action_only']
    claims:list[Claim]=Field(default_factory=list)
    missing:list[str]=Field(default_factory=list,description='Requested topics not established; do not invent factual explanations.')


def source_quote(quote,text):
    quote=quote.replace(r'\r\n','\n').replace(r'\n','\n').replace(r'\t','\t')
    tokens=quote.split()
    if not tokens:return None
    match=re.search(r'\s+'.join(re.escape(t) for t in tokens),text,re.IGNORECASE)
    return match.group(0) if match else None


def validate_answer(answer,seen):
    errors=[]
    if answer.status in ('answered','partial') and not answer.claims:
        errors.append('Answer status requires cited claims.')
    if answer.status in ('not_found','action_only') and answer.claims:
        errors.append('Non-answer status cannot contain factual claims.')
    if answer.status=='answered' and answer.missing:
        errors.append('Complete answer lists missing evidence.')
    if answer.status=='partial' and not answer.missing:
        errors.append('Partial answer must identify missing topics.')
    for n,claim in enumerate(answer.claims,1):
        for item in claim.evidence:
            if item.chunk_id not in seen:
                errors.append(f'Claim {n}: citation ID was not retrieved.')
            elif not source_quote(item.quote,seen[item.chunk_id]['text']):
                errors.append(f'Claim {n}: quotation does not match the cited passage.')
        quoted='\n'.join(e.quote for e in claim.evidence)
        scales={'thousand':Decimal(1000),'million':Decimal(1000000),'billion':Decimal(1000000000)}
        unit_matches=set(re.findall(r'\bin (thousand|million|billion)s\b',quoted,re.I))
        fallback=scales[next(iter(unit_matches)).lower()] if len(unit_matches)==1 else Decimal(1)
        def amounts(text,default):
            return {Decimal(number.replace(',',''))*scales.get(unit.lower(),default)
                    for number,unit in re.findall(r'\$\s*(?:\|\s*)*(\d[\d,]*(?:\.\d+)?)(?:\s+(thousand|million|billion))?',text,re.I)}
        if not amounts(claim.text,Decimal(1)) <= amounts(quoted,fallback):
            errors.append(f'Claim {n}: dollar amount is not established in the quoted evidence (including units).')
    return errors


def repair_citation_links(answer,seen):
    """Relink unchanged exact quotes only within one identifiable source document.

    Never fuzzy-match, change the quote or claim, or resolve cross-document ambiguity.
    Duplicate overlapping chunks from the same document use the first retrieved match.
    """
    answer=answer.model_copy(deep=True);repairs=[]
    for n,claim in enumerate(answer.claims,1):
        for e in claim.evidence:
            if e.chunk_id in seen and source_quote(e.quote,seen[e.chunk_id]['text']):continue
            matches=[h for h in seen.values() if source_quote(e.quote,h['text'])]
            documents={h.get('document_id') for h in matches}
            if matches and len(documents)==1 and None not in documents:
                old=e.chunk_id;e.chunk_id=matches[0]['id']
                repairs.append({'claim':n,'old_chunk_id':old,'new_chunk_id':e.chunk_id,'quote':e.quote,
                                'reason':'Exact quotation found in retrieved chunks from one document'})
    return answer,repairs


class UsageRecorder(AsyncCallbackHandler):
    def __init__(self):self.calls=[];self.messages=[]
    async def on_llm_end(self,response,**kwargs):
        for group in response.generations:
            message=getattr(group[0],'message',None)
            if message is not None:
                self.messages.append({'content':message.content,'tool_calls':message.tool_calls})
            usage=getattr(message,'usage_metadata',None)
            if usage:self.calls.append(dict(usage))


class ToolBridge:
    def __init__(self,session,root,approval=None):
        self.session,self.root,self.approval=session,Path(root),approval
        self.trace=[];self.seen={};self.calls=0;self.mutex=asyncio.Lock()

    async def call(self,name,arguments):
        async with self.mutex:
            if self.calls>=TOOL_LIMIT:
                self.trace.append({'tool':name,'status':'blocked','reason':'tool_call_limit'})
                return {'status':'blocked','reason':'Tool call limit reached; use existing evidence.'}
            if name=='add_document':
                path=Path(arguments['path']).expanduser()
                if not path.is_absolute():path=self.root/path
                try:
                    path=path.resolve(strict=True)
                    if not path.is_file() or path.suffix.lower() not in ('.html','.htm'):
                        raise ValueError('Only local HTML files can be added.')
                    before=hashlib.sha256(path.read_bytes()).hexdigest()
                except (OSError,ValueError):
                    self.trace.append({'tool':name,'status':'blocked','reason':'invalid_html_path'})
                    return {'status':'blocked','reason':'Invalid local HTML file path.'}
                approved=self.approval(str(path)) if self.approval else False
                if not approved:
                    self.trace.append({'tool':name,'arguments':{'path':str(path)},'status':'denied','reason':'user_approval_required'})
                    return {'status':'denied','reason':'User did not approve this document addition.'}
                if hashlib.sha256(path.read_bytes()).hexdigest()!=before:
                    self.trace.append({'tool':name,'status':'blocked','reason':'file_changed_after_approval'})
                    return {'status':'blocked','reason':'File changed after approval. Nothing added.'}
                arguments={'path':str(path)}
            self.calls+=1
            started=time.perf_counter()
            result=await self.session.call_tool(name,arguments)
            failed=bool(getattr(result,'is_error',False) or getattr(result,'isError',False))
            text='\n'.join(getattr(c,'text','') for c in result.content)
            if failed:
                data={'status':'error','reason':'MCP tool failed; do not assume the action succeeded.'}
            else:
                data=json.loads(text)
            self.trace.append({'tool':name,'arguments':arguments,'status':'error' if failed else 'completed',
                               'elapsed_seconds':time.perf_counter()-started,'result':data})
            if name=='search' and not failed:
                for hit in data['results']:self.seen[hit['id']]=hit
            return data


def render_answer(answer,seen,trace):
    if answer.status=='not_found':
        return "I’m not sure: the retrieved documents do not provide enough evidence to answer this question."
    if answer.status=='action_only':
        actions=[t for t in trace if t['tool']=='add_document']
        if not actions:return 'No document was added.'
        last=actions[-1]
        if last['status']=='denied':return 'Document addition was not approved; no document was added.'
        data=last.get('result',{})
        if data.get('status')=='added':return 'Document added: '+data['document_id']
        if data.get('status')=='already_indexed':return 'That document is already indexed; no duplicate was added.'
        return 'Document addition did not complete.'
    lines=[]
    for claim in answer.claims:
        lines.append(claim.text)
        for item in claim.evidence:
            hit=seen[item.chunk_id]
            actual=source_quote(item.quote,hit['text'])
            lines.extend([f'[{hit["id"]}] {hit["company"]} — {hit["section"]}',
                          '> '+actual.replace('\n','\n> '),f'Source: {hit["citation_url"]}'])
        lines.append('')
    if answer.status=='partial':
        lines.append('I could not establish the remaining requested information from the retrieved evidence.')
        lines.extend('- '+topic for topic in answer.missing)
    return '\n'.join(lines).strip()


async def run_agent(question,root=ROOT,approval=None):
    root=Path(root)
    if not question.strip():raise ValueError('Question must not be blank')
    started=time.perf_counter();usage=UsageRecorder();bridge=None
    result={'question':question,'model':MODEL,'design':'b_paragraph','guardrail_errors':[]}
    env=dict(os.environ);env['CORPUS_PROJECT_ROOT']=str(root)
    params=StdioServerParameters(command=sys.executable,args=[str(root/'server.py')],cwd=str(root),env=env)
    try:
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                bridge=ToolBridge(session,root,approval)
                @tool
                async def search(query:str)->str:
                    """Search IPO evidence through MCP. Returns five passages with IDs and citations. Refine by company/topic if essential evidence is missing."""
                    return json.dumps(await bridge.call('search',{'query':query}),ensure_ascii=False)
                @tool
                async def add_document(path:str)->str:
                    """Add a local HTML file only at the user's request. Harness approval is required before the actual MCP mutation."""
                    return json.dumps(await bridge.call('add_document',{'path':path}),ensure_ascii=False)
                first=await bridge.call('search',{'query':question})
                model=ChatOpenAI(model=MODEL,api_key=load_key(root),reasoning_effort='low',
                                 max_completion_tokens=4096,timeout=120,max_retries=0,
                                 model_kwargs={'parallel_tool_calls':False})
                @wrap_model_call
                async def reserve_final_answer(request,handler):
                    # Leave the structured Answer tool available while removing further MCP tools.
                    # Reserve the final model call for an answer/partial answer/refusal.
                    if len(usage.calls)>=MODEL_LIMIT:
                        raise RuntimeError('Total model-call budget reached')
                    if bridge.calls>=TOOL_LIMIT-2 or len(usage.calls)>=MODEL_LIMIT-2:
                        request=request.override(tools=[])
                    return await handler(request)
                agent=create_agent(model=model,tools=[search,add_document],
                    system_prompt=(root/'AGENTS.md').read_text(encoding='utf-8'),
                    response_format=ToolStrategy(Answer,handle_errors=True),
                    middleware=[ModelCallLimitMiddleware(run_limit=MODEL_LIMIT,exit_behavior='error'),reserve_final_answer])
                messages=[HumanMessage(content=question),
                    AIMessage(content='',tool_calls=[{'name':'search','args':{'query':question},'id':'initial_search','type':'tool_call'}]),
                    ToolMessage(content=json.dumps(first,ensure_ascii=False),tool_call_id='initial_search',name='search')]
                state=await asyncio.wait_for(agent.ainvoke({'messages':messages},config={'callbacks':[usage],'recursion_limit':24}),240)
                answer=state.get('structured_response')
                if not isinstance(answer,Answer):raise RuntimeError('Agent did not return a structured answer')
                result['original_draft']=answer.model_dump()
                answer,repairs=repair_citation_links(answer,bridge.seen)
                result['citation_link_repairs']=repairs
                errors=validate_answer(answer,bridge.seen)
                if errors and len(usage.calls)<MODEL_LIMIT:
                    result['initial_guardrail_errors']=errors
                    correction=state['messages']+[HumanMessage(content='Local citation validation rejected this draft: '+json.dumps(errors)+'. Correct the Answer using existing retrieved evidence. Copy exact chunk IDs from this catalogue: '+json.dumps(list(bridge.seen))+'. Do not use source block IDs. Copy complete sentences or whole table lines verbatim, including all cells and pipes. Never add ellipses or placeholders. Do not round dollar amounts yourself; use the precise quoted amount. Price to Public is gross proceeds; proceeds before expenses are AFTER underwriting fees and are not gross proceeds. Include every requested item or label partial. For not_found, claims must be an empty list. If you cannot support a claim, omit it.')]
                    state=await asyncio.wait_for(agent.ainvoke({'messages':correction},config={'callbacks':[usage],'recursion_limit':24}),120)
                    answer=state.get('structured_response')
                    if not isinstance(answer,Answer):raise RuntimeError('No corrected structured answer')
                    answer,repairs=repair_citation_links(answer,bridge.seen)
                    result['citation_link_repairs'].extend(repairs)
                    errors=validate_answer(answer,bridge.seen)
                result['draft']=answer.model_dump()
                result['guardrail_errors']=errors
                result['status']='blocked' if errors else answer.status
                result['answer']='I could not verify the generated citations or quotations, so I am not presenting this answer as established.' if errors else render_answer(answer,bridge.seen,bridge.trace)
    except Exception as error:
        result.update(status='error',answer='The agent run did not complete. Inspect the saved run status before retrying.',error_type=type(error).__name__)
        def describe(exc):
            if isinstance(exc,BaseExceptionGroup):return [item for sub in exc.exceptions for item in describe(sub)]
            return [{'type':type(exc).__name__,'message':re.sub(r'sk-[A-Za-z0-9_-]+','[REDACTED]',str(exc))[:800]}]
        result['errors']=describe(error)
    result['tool_trace']=bridge.trace if bridge else []
    result['retrieved_chunks']=list(bridge.seen.values()) if bridge else []
    result['model_usage']=usage.calls
    result['model_messages']=usage.messages
    input_tokens=sum(c.get('input_tokens',0) for c in usage.calls)
    output_tokens=sum(c.get('output_tokens',0) for c in usage.calls)
    cached=sum(c.get('input_token_details',{}).get('cache_read',0) for c in usage.calls)
    result['tokens']={'input':input_tokens,'cached_input':cached,'output_including_reasoning':output_tokens}
    result['model_cost_usd']=((input_tokens-cached)*0.05+cached*0.005+output_tokens*0.40)/1e6
    result['cost_scope']='Answer-model cost only; query and document embeddings are recorded in their separate caches.'
    result['elapsed_seconds']=time.perf_counter()-started
    return result


def save_run(result,root=ROOT):
    folder=Path(root)/'eval/agent_runs';folder.mkdir(parents=True,exist_ok=True)
    filename=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+hashlib.sha256(result['question'].encode()).hexdigest()[:8]+'.json'
    path=folder/filename
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder.parent/'agent_last_run.json').write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
    return path


def terminal_approval(path):
    if not sys.stdin.isatty():return False
    print(f'\nApproval required: add this HTML document to the index?\n{path}\nThis writes local files and may use paid embeddings (up to $0.25 preflight limit).',file=sys.stderr)
    return input('Type yes to approve, or anything else to deny: ').strip().lower()=='yes'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question')
    args=parser.parse_args()
    result=asyncio.run(run_agent(args.question,approval=terminal_approval))
    path=save_run(result)
    print(result['answer'])
    print(f'\nStatus: {result["status"]}; model cost: ${result["model_cost_usd"]:.6f}; elapsed: {result["elapsed_seconds"]:.2f}s\nRun saved: {path}',file=sys.stderr)
    if result['status']=='error':raise SystemExit(1)
