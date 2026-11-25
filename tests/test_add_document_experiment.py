import unittest
from experiments.add_document_experiment import check_after,quoted_context_complete


def result(doc='new',amount='14,147',year='2019'):
    text=f'Year Ended December 31, (in millions)\nMetric | 2017 | {year}\nGAAP Revenue | $11,270 | ${amount}'
    return {'status':'answered','draft':{'status':'answered','claims':[{'text':'Uber revenue for 2019 was $14.147 billion.',
          'evidence':[{'chunk_id':'c1','quote':text}]}],'missing':[]},
          'retrieved_chunks':[{'id':'c1','document_id':doc,'text':text}]}


class ExperimentChecks(unittest.TestCase):
    def test_correct_annual_amount_and_new_source(self):self.assertTrue(check_after(result(),'new'))
    def test_old_source_not_accepted(self):self.assertFalse(check_after(result('old'),'new'))
    def test_quarterly_amount_not_accepted(self):self.assertFalse(check_after(result(amount='4,069'),'new'))
    def test_wrong_year_not_accepted(self):self.assertFalse(check_after(result(year='2018'),'new'))
    def test_refusal_not_accepted(self):self.assertFalse(check_after({'status':'not_found'},'new'))
    def test_wrong_magnitude_without_dollar_symbol_rejected(self):
        r=result();r['draft']['claims'][0]['text']='Uber revenue in 2019 was 14.147 million dollars.'
        self.assertFalse(check_after(r,'new'))
    def test_correct_source_context_but_short_quote_reported(self):
        r=result();r['draft']['claims'][0]['text']='Uber revenue for 2019 was 14,147 million dollars.'
        r['draft']['claims'][0]['evidence'][0]['quote']=r['retrieved_chunks'][0]['text'].splitlines()[-1]
        self.assertTrue(check_after(r,'new'));self.assertFalse(quoted_context_complete(r))
    def test_wrong_column_rejected(self):
        r=result();r['retrieved_chunks'][0]['text']=r['retrieved_chunks'][0]['text'].replace('2017 | 2019','2019 | 2018')
        r['draft']['claims'][0]['evidence'][0]['quote']=r['retrieved_chunks'][0]['text']
        self.assertFalse(check_after(r,'new'))


if __name__=='__main__':unittest.main()
