"""Offline tests for evaluation bookkeeping, validation and human-grade isolation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from contextlib import redirect_stdout
import io
from evaluation.answer_evaluation import agreement,mechanical,parse_judge,validate_labels,load_human,import_human,summarize,judge_payload
from evaluation.answer_evaluation import evaluate_answers


def result(status='answered'):
    return {'status':status,'answer':'Revenue was $885 million.','guardrail_errors':[],
            'draft':{'status':status,'claims':[{'text':'Revenue was $885 million.','evidence':[{'chunk_id':'c1','quote':'Revenue was $885 million.'}]}] if status=='answered' else [],'missing':[]},
            'retrieved_chunks':[{'id':'c1','text':'Revenue was $885 million.','company':'Fixture','section':'Summary'}],
            'elapsed_seconds':2,'model_cost_usd':0.001}


def grade(correct=1,supported=1):
    return {'correct':correct,'supported':supported,'correct_reason':'Reason','supported_reason':'Reason',
            'evidence_sufficiency':'full','failure_categories':[],'failure_reason':'None',
            'evidence':[{'chunk_id':'c1','quote':'Revenue was $885 million.'}]}


def saved(g):return {'response':{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(g)}}]},'cost_usd':0.001,'cache_key':'fixture'}


class EvaluationTests(unittest.TestCase):
    def test_mechanical_citation(self):self.assertTrue(mechanical(result())['citation_check'])
    def test_operational_error_is_not_refusal(self):
        check=mechanical(result('error'));self.assertFalse(check['refusal']);self.assertIsNone(check['citation_check'])
    def test_wrong_quote_blocked(self):
        r=result();r['draft']['claims'][0]['evidence'][0]['quote']='Revenue was $900 million.'
        self.assertFalse(mechanical(r)['citation_check'])
    def test_valid_judge(self):self.assertEqual(parse_judge(saved(grade()),result())['status'],'graded')
    def test_judge_invented_quote_flagged(self):
        g=grade();g['evidence'][0]['quote']='Revenue was $999 million.'
        self.assertEqual(parse_judge(saved(g),result())['status'],'needs_review')
    def test_judge_inconsistent_failure_flagged(self):self.assertEqual(parse_judge(saved(grade(0,0)),result())['status'],'needs_review')
    def test_agreement_and_kappa(self):
        rows=[{'id':'a','judge':{'status':'graded','grade':grade()}},{'id':'b','judge':{'status':'graded','grade':grade(0,0)}}]
        labels={'a':{'correct':1,'supported':1},'b':{'correct':0,'supported':1}}
        a=agreement(rows,labels);self.assertEqual(a['correct']['cohen_kappa'],1);self.assertEqual(a['supported']['agreement'],0.5)
    def test_no_human_grades_no_agreement(self):self.assertIsNone(agreement([], {})['correct']['agreement'])
    def test_partial_coverage_no_overall_accuracy(self):
        r={'id':'a','kind':'single','result':result(),'mechanical':mechanical(result()),'judge':{'status':'graded','grade':grade()}}
        self.assertIsNone(summarize([r],{},30)['correct_rate_over_all_questions'])
    def test_human_zero_requires_reason(self):
        with self.assertRaises(ValueError):validate_labels([{'id':'a','correct':'0','supported':'1','notes':''}],{'a'})
    def test_duplicate_human_id_rejected(self):
        r={'id':'a','correct':'1','supported':'1','notes':''}
        with self.assertRaises(ValueError):validate_labels([r,r],{'a'})
    def test_import_binding_and_preserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'eval').mkdir();p=root/'labels.json'
            p.write_text(json.dumps({'dataset':'one','labels':[{'id':'a','correct':1,'supported':1,'notes':''}]}))
            manifest={'dataset':'one','questions':[{'id':'a'}]};import_human(root,p,manifest)
            self.assertEqual(len(load_human(root,'one',{'a'})[0]),1)
            self.assertEqual(load_human(root,'two',{'a'})[0],{})
            with self.assertRaises(ValueError):import_human(root,p,{'dataset':'two','questions':[{'id':'a'}]})
            self.assertEqual(len(load_human(root,'one',{'a'})[0]),1)
    def test_judge_inputs_exclude_human_and_intermediate_drafts(self):
        r=result();r['model_messages']=[{'content':'bad intermediate output'}]
        p=judge_payload({'question':'Revenue?','kind':'single','reference_answer':'885 million'},r,'rubric')
        self.assertNotIn('bad intermediate output',json.dumps(p));self.assertNotIn('human_labels',json.dumps(p))


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_and_rubric_change_preserve_old_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'eval').mkdir()
            (root/'eval/questions.csv').write_text('id,question,kind,reference_answer\nq01,Revenue?,single,885 million\n',encoding='utf-8')
            (root/'answer_rubric.txt').write_text('rubric v1')
            r=result();r['tokens']={'input':10,'output_including_reasoning':10}
            with patch('evaluation.answer_evaluation.provenance',return_value={'fixture':'hash'}),patch('evaluation.answer_evaluation.query_tokens',return_value=0),patch('evaluation.answer_evaluation.load_key',return_value='fixture'),patch('evaluation.answer_evaluation.run_agent',new_callable=AsyncMock,return_value=r) as agent,patch('evaluation.answer_evaluation.call_judge',return_value=saved(grade())) as judge,redirect_stdout(io.StringIO()):
                first=await evaluate_answers(root)
                second=await evaluate_answers(root)
                self.assertEqual(agent.await_count,1);self.assertEqual(judge.call_count,1)
                self.assertEqual(first['dataset'],second['dataset'])
                offline=await evaluate_answers(root,offline=True)
                self.assertEqual(agent.await_count,1);self.assertEqual(offline['judge_correct'],1)
                (root/'answer_rubric.txt').write_text('rubric v2')
                third=await evaluate_answers(root)
                self.assertEqual(agent.await_count,2);self.assertNotEqual(first['dataset'],third['dataset'])
                old=root/'eval/answer_evaluation'/first['dataset'][:16]
                self.assertTrue((old/'answers/q01.json').exists())
                self.assertNotIn('correct_reason',(old/'human_review.html').read_text(encoding='utf-8'))
                reused=await evaluate_answers(root,reuse_answers_from=old)
                self.assertEqual(agent.await_count,2,'Judge-only must not call the answering agent')
                new=root/'eval/answer_evaluation'/reused['dataset'][:16]
                self.assertEqual((old/'answers/q01.json').read_bytes(),(new/'answers/q01.json').read_bytes())
                self.assertEqual(json.loads((new/'manifest.json').read_text())['answer_origin_dataset'],first['dataset'])


if __name__=='__main__':unittest.main()
