import unittest
from agent import Answer,Claim,Evidence,repair_citation_links,validate_answer


class CitationRepairTests(unittest.TestCase):
    def answer(self,quote='Revenue was $885 million.',chunk='wrong'):
        return Answer(status='answered',claims=[Claim(text='Revenue was $885 million.',evidence=[Evidence(chunk_id=chunk,quote=quote)])])
    def seen(self):return {'c1':{'id':'c1','document_id':'d1','text':'Revenue was $885 million.'}}
    def test_exact_quote_relinked_and_original_unchanged(self):
        original=self.answer();fixed,repairs=repair_citation_links(original,self.seen())
        self.assertEqual(original.claims[0].evidence[0].chunk_id,'wrong')
        self.assertEqual(fixed.claims[0].evidence[0].chunk_id,'c1');self.assertEqual(len(repairs),1)
        self.assertEqual(validate_answer(fixed,self.seen()),[])
    def test_paraphrase_not_repaired(self):
        fixed,repairs=repair_citation_links(self.answer('Reported revenue was $885 million.'),self.seen())
        self.assertEqual(repairs,[]);self.assertTrue(validate_answer(fixed,self.seen()))
    def test_cross_document_ambiguity_not_repaired(self):
        seen=self.seen();seen['c2']={'id':'c2','document_id':'d2','text':seen['c1']['text']}
        self.assertEqual(repair_citation_links(self.answer(),seen)[1],[])
    def test_duplicate_same_document_safe(self):
        seen=self.seen();seen['c2']={**seen['c1'],'id':'c2'}
        self.assertEqual(len(repair_citation_links(self.answer(),seen)[1]),1)
    def test_relink_does_not_bypass_money_check(self):
        a=self.answer();a.claims[0].text='Revenue was $900 million.'
        fixed,_=repair_citation_links(a,self.seen());self.assertTrue(validate_answer(fixed,self.seen()))


if __name__=='__main__':unittest.main()
