"""Offline guardrail and approval tests: uv run python -m unittest tests.test_agent -v"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from agent import Answer,Claim,Evidence,ToolBridge,validate_answer,source_quote,TOOL_LIMIT


class FakeSession:
    def __init__(self):self.calls=[]
    async def call_tool(self,name,arguments):
        self.calls.append((name,arguments))
        data={'status':'added','document_id':'fixture'} if name=='add_document' else {'results':[]}
        return SimpleNamespace(isError=False,content=[SimpleNamespace(text=json.dumps(data))])


class CitationTests(unittest.TestCase):
    def setUp(self):self.seen={'c1':{'text':'Revenue was $885 million in 2019.'}}
    def answer(self,chunk='c1',quote='Revenue was $885 million in 2019.'):
        return Answer(status='answered',claims=[Claim(text='Revenue was $885 million.',evidence=[Evidence(chunk_id=chunk,quote=quote)])],missing=[])
    def test_valid_citation(self):self.assertEqual(validate_answer(self.answer(),self.seen),[])
    def test_invented_chunk_blocked(self):self.assertTrue(validate_answer(self.answer(chunk='invented'),self.seen))
    def test_changed_number_blocked(self):self.assertTrue(validate_answer(self.answer(quote='Revenue was $999 million in 2019.'),self.seen))
    def test_formatting_normalization(self):self.assertEqual(source_quote(r'revenue was\n$885 million in 2019.',self.seen['c1']['text']),self.seen['c1']['text'])
    def test_uncited_complete_answer_blocked(self):self.assertTrue(validate_answer(Answer(status='answered',claims=[],missing=[]),self.seen))
    def test_refusal(self):self.assertFalse(validate_answer(Answer(status='not_found',claims=[],missing=['Later returns']),self.seen))
    def test_real_quote_wrong_financial_amount_blocked(self):
        answer=self.answer();answer.claims[0].text='Revenue was $900 million.'
        self.assertTrue(validate_answer(answer,self.seen))


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_denial_prevents_mcp_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.html';path.write_text('<p>fixture</p>')
            session=FakeSession();bridge=ToolBridge(session,Path(tmp),approval=lambda p:False)
            result=await bridge.call('add_document',{'path':str(path)})
            self.assertEqual(result['status'],'denied');self.assertEqual(session.calls,[])
    async def test_approval_allows_exact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.html';path.write_text('<p>fixture</p>')
            seen=[]
            session=FakeSession();bridge=ToolBridge(session,Path(tmp),approval=lambda p:seen.append(p) or True)
            await bridge.call('add_document',{'path':str(path)})
            self.assertEqual(seen,[str(path.resolve())]);self.assertEqual(len(session.calls),1)
    async def test_changed_file_blocks_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.html';path.write_text('<p>fixture</p>')
            def approve(p):path.write_text('<p>changed</p>');return True
            session=FakeSession();bridge=ToolBridge(session,Path(tmp),approval=approve)
            result=await bridge.call('add_document',{'path':str(path)})
            self.assertEqual(result['status'],'blocked');self.assertEqual(session.calls,[])
    async def test_tool_limit(self):
        session=FakeSession();bridge=ToolBridge(session,Path('.'))
        for _ in range(TOOL_LIMIT+1):result=await bridge.call('search',{'query':'test'})
        self.assertEqual(len(session.calls),TOOL_LIMIT);self.assertEqual(result['status'],'blocked')


if __name__=='__main__':unittest.main()
