"""Run two live smoke checks; save traces and an offline guardrail demonstration.

uv run python -m checks.verify_agent          # paid answer-model calls
uv run python -m checks.verify_agent --offline-only  # no API calls
These are smoke checks, not human grades or the full evaluation.
"""
import argparse
import asyncio
import json
from agent import ROOT, Answer, Claim, Evidence, run_agent, save_run, validate_answer


def guardrail_demo():
    seen={'fixture':{'text':'Revenue was $885 million in 2019.'}}
    cases=[]
    for label,amount,chunk in [('valid',885,'fixture'),('wrong_amount',900,'fixture'),('invented_citation',885,'invented')]:
        answer=Answer(status='answered',claims=[Claim(text=f'Revenue was ${amount} million.',evidence=[Evidence(chunk_id=chunk,quote=seen['fixture']['text'])])])
        errors=validate_answer(answer,seen)
        cases.append({'case':label,'synthetic':True,'draft':answer.model_dump(),'errors':errors,'passed':bool(errors)==(label!='valid')})
    path=ROOT/'eval/guardrail_demo.json'
    path.write_text(json.dumps(cases,indent=2),encoding='utf-8')
    print(f'Offline guardrail cases passed: {sum(c["passed"] for c in cases)}/{len(cases)}; saved {path}')
    return all(c['passed'] for c in cases)


async def main(offline):
    ok=guardrail_demo()
    if offline:return ok
    cases=[('answerable','What revenue did Airbnb report for the year ended December 31, 2019?','answered'),
           ('unanswerable','What was DoorDash stock performance during its first year after its IPO?','not_found')]
    rows=[]
    for label,question,expected in cases:
        print(f'Running {label}: {question}',flush=True)
        result=await run_agent(question)
        path=save_run(result)
        passed=result['status']==expected and not result['guardrail_errors']
        rows.append({'case':label,'expected_status':expected,'actual_status':result['status'],'passed':passed,
                     'guardrail_errors':result['guardrail_errors'],'initial_guardrail_errors':result.get('initial_guardrail_errors',[]),
                     'run_file':str(path.relative_to(ROOT)),'model_calls':len(result['model_usage']),
                     'tool_trace_entries':len(result['tool_trace']),'model_cost_usd':result['model_cost_usd'],
                     'elapsed_seconds':result['elapsed_seconds']})
        print(result['answer'])
        print(f'Status check: {"PASS" if passed else "FAIL"}; model cost ${result["model_cost_usd"]:.6f}',flush=True)
    summary={'scope':'Two live status/citation smoke checks, not answer-accuracy grading. Inspect the saved claims and quotations.',
             'cases':rows,'all_passed':ok and all(row['passed'] for row in rows),
             'total_model_cost_usd':sum(row['model_cost_usd'] for row in rows)}
    path=ROOT/'eval/agent_smoke_test.json'
    path.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(f'Saved: {path}')
    return summary['all_passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline-only',action='store_true')
    args=parser.parse_args()
    raise SystemExit(0 if asyncio.run(main(args.offline_only)) else 1)
