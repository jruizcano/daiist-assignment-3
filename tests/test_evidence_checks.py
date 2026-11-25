"""Run locally: uv run python -m unittest tests.test_evidence_checks -v"""
import copy
import unittest
from evaluation.evidence_checks import match_quote


class EvidenceChecks(unittest.TestCase):
    def setUp(self):
        self.record={'question':'What revenue for the year ended December 31, 2019?', 'hits':[
          {'rank':1,'kind':'prose','text':'The company offered 180,000,000 shares.\n\nNo extra shares were issued.'},
          {'rank':2,'kind':'table','text':'Revenue (in millions):\nTotal revenue | $ | 291 | $ | 885 | $ | 587 |',
           'table_grid_rows':[0,1,2],'table_header_rows':[0,1],'table_data_rows':[2],
           'table_grid':[['','','Year Ended December 31,','','Year Ended December 31,','','Nine Months Ended September 30,'],
                         ['','','2018','','2019','','2019'],
                         ['Total revenue','$','291','$','885','$','587']]}]}
        self.item={'passage_rank':2,'quote':'Total revenue | | $ | 885 |',
                   'requirement':'Revenue for the year ended December 31, 2019: $885 million.'}

    def test_exact_is_unchanged(self):
        item={'passage_rank':1,'quote':'The company offered 180,000,000 shares.','requirement':'Shares'}
        self.assertTrue(match_quote(item,self.record,1)['matched'])
        self.assertEqual(match_quote(item,self.record,2)['method'],'exact_text')

    def test_case_and_escaped_newlines(self):
        item={'passage_rank':1,'quote':r'the company offered 180,000,000 shares.\n\nNo extra shares were issued.','requirement':'Shares'}
        self.assertFalse(match_quote(item,self.record,1)['matched'])
        self.assertEqual(match_quote(item,self.record,2)['method'],'formatting_only')

    def test_changed_number_rejected(self):
        item={'passage_rank':1,'quote':'The company offered 190,000,000 shares.','requirement':'Shares'}
        self.assertFalse(match_quote(item,self.record,2)['matched'])

    def test_reworded_negation_rejected(self):
        item={'passage_rank':1,'quote':'extra shares were not issued.','requirement':'Shares'}
        self.assertFalse(match_quote(item,self.record,2)['matched'])

    def test_table_cell_with_context(self):
        self.assertFalse(match_quote(self.item,self.record,1)['matched'])
        result=match_quote(self.item,self.record,2)
        self.assertEqual(result['method'],'structured_table_cell')
        self.assertEqual(result['table_evidence']['grid_column'],4)

    def test_table_wrong_annual_value_rejected(self):
        for amount in ['587','291','999']:
            item=copy.deepcopy(self.item)
            item['quote']=item['quote'].replace('885',amount)
            item['requirement']=item['requirement'].replace('885',amount)
            self.assertFalse(match_quote(item,self.record,2)['matched'],amount)

    def test_wrong_units_rejected(self):
        item=copy.deepcopy(self.item)
        item['requirement']=item['requirement'].replace('million','billion')
        self.assertFalse(match_quote(item,self.record,2)['matched'])

    def test_missing_units_rejected(self):
        self.record['hits'][1]['text']=self.record['hits'][1]['text'].replace('(in millions)','')
        self.assertFalse(match_quote(self.item,self.record,2)['matched'])

    def test_wrong_row_rejected(self):
        self.item['quote']=self.item['quote'].replace('Total revenue','Operating expenses')
        self.assertFalse(match_quote(self.item,self.record,2)['matched'])

    def test_question_year_conflict_rejected(self):
        self.record['question']=self.record['question'].replace('2019','2018')
        self.assertFalse(match_quote(self.item,self.record,2)['matched'])

    def test_invalid_rank_rejected(self):
        self.item['passage_rank']=9
        self.assertEqual(match_quote(self.item,self.record,2)['method'],'invalid_passage')


if __name__=='__main__':
    unittest.main()
