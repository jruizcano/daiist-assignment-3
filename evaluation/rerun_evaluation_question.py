"""Replace one current evaluation answer, refresh its judge and recount.

uv run python -m evaluation.rerun_evaluation_question q02
--from-run PATH uses an already completed matching run rather than paying to rerun.
Records replacement metadata; does not create a separate answer dataset.
"""
import argparse
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
from evaluation.answer_evaluation import ROOT,write_json,file_hash,mechanical,judge_payload,parse_judge,publish
from agent import run_agent
from index import load_key
from evaluation.passage_review import call_judge,digest


def read(path):return json.loads(path.read_text(encoding='utf-8'))


async def main(qid,from_run=None):
    latest=read(ROOT/'eval/answer_evaluation/latest.json')
    folder=ROOT/latest['folder'];manifest=read(folder/'manifest.json')
    question=next((q for q in manifest['questions'] if q['id']==qid),None)
    if question is None:raise ValueError('Question ID is not in the current evaluation')
    # Do not silently leave an existing human grade attached to a changed answer.
    import csv
    with (ROOT/'eval/human_labels.csv').open(encoding='utf-8-sig',newline='') as f:
        if any(r['id']==qid for r in csv.DictReader(f)):
            raise ValueError('Remove or revise this question\'s existing human grade before replacing its answer.')
    result=read(Path(from_run)) if from_run else await run_agent(question['question'],root=ROOT)
    if result['question']!=question['question']:raise ValueError('Run question does not match the evaluation question')
    check=mechanical(result);judged=None
    if check['citation_check'] is True:
        payload=judge_payload(question,result,manifest['rubric'])
        cache=folder/'judge_cache'/f'{digest(payload)}.json'
        saved=await asyncio.to_thread(call_judge,payload,cache,None if cache.exists() else load_key(ROOT))
        judged=parse_judge(saved,result)
    answer_path=folder/'answers'/f'{qid}.json';judge_path=folder/'judges'/f'{qid}.json'
    history_path=folder/'answer_replacements.json'
    history=read(history_path) if history_path.exists() else []
    before=read(answer_path)
    entry={'id':qid,'time_utc':datetime.now(timezone.utc).isoformat(),'previous_status':before['result']['status'],
           'previous_answer_sha256':file_hash(answer_path),'new_status':result['status'],
           'note':'User-requested replacement of a selected answer; this dataset is no longer a single untouched full run.'}
    write_json(answer_path,{'question':question,'result':result})
    if judged:write_json(judge_path,judged)
    elif judge_path.exists():judge_path.unlink()
    entry['new_answer_sha256']=file_hash(answer_path);history.append(entry);write_json(history_path,history)
    summary=publish(ROOT,folder,manifest)
    print(result['answer'])
    print(f'Replaced {qid}: {entry["previous_status"]} -> {result["status"]}')
    print('Judge status:',judged['status'] if judged else 'not applicable')
    if judged:print('Judge grades:',judged.get('grade'))
    print('Blocked/errors:',summary['operational_failures_or_blocks'],'; judge flags:',summary['judge_needs_review'])
    print('Review page:',folder/'human_review.html')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('id');parser.add_argument('--from-run',type=Path)
    args=parser.parse_args();asyncio.run(main(args.id,args.from_run))
