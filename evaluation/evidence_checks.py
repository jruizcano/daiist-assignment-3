"""Deterministic quote checks: original v1 and conservative formatting-aware v2.

Neither version proves that quoted text entails a reviewer's interpretation.
"""
import re

VERSION = '2.0'
LABELS = {'sufficient','partially_sufficient','insufficient','not_established','potential_reference_issue'}
FIELDS = {'label','reason','supported_requirements','missing_requirements','concerns'}


def whitespace(text):
    return re.sub(r'\s+', ' ', text).strip()


def formatted(text):
    # Decode only escaped whitespace; never broadly decode Unicode or delete words/punctuation.
    return whitespace(text.replace(r'\r\n','\n').replace(r'\n','\n').replace(r'\r','\n').replace(r'\t','\t')).casefold()


def table_cell_match(item, hit, question):
    """Accept only a single monetary annual cell with row, period and units verified.

    A shortened row is structured evidence, not a verbatim quotation. Ambiguous
    periods, missing units, multi-value summaries and unsupported tables fail closed.
    """
    if hit.get('kind') != 'table' or not hit.get('table_grid'):
        return None
    cells = [whitespace(c) for c in item['quote'].split('|') if c.strip()]
    if len(cells) != 3 or cells[1] != '$' or not re.fullmatch(r'\d[\d,]*(?:\.\d+)?',cells[2]):
        return None
    label, amount = cells[0],cells[2]
    # Per-share and percentage units need a different schema; do not guess them here.
    if re.search(r'\b(share|shares|percent|percentage)\b',label,re.I):
        return None
    claim = formatted(item['requirement'])
    years = set(re.findall(r'\b(?:19|20)\d{2}\b',claim))
    question_years = set(re.findall(r'\b(?:19|20)\d{2}\b',question))
    if len(years) != 1 or (question_years and years != question_years):
        return None
    year = next(iter(years))
    period = re.search(r'\byear ended ([a-z]+ \d{1,2})',claim)
    unit = re.search(r'\b(thousand|million|billion)s?\b',claim)
    if not period or not unit or '$' not in claim:
        return None
    if amount not in re.findall(r'\d[\d,]*(?:\.\d+)?',claim):
        return None
    # Unit context must be attached to THIS retrieved table, not borrowed from another passage.
    prefix = formatted(hit['text'].split('|',1)[0])
    if not re.search(r'\bin '+unit.group(1)+r's\b',prefix):
        return None
    rows = hit['table_grid']
    row_ids = hit.get('table_grid_rows',[])
    header_ids = set(hit.get('table_header_rows',[]))
    data_ids = set(hit.get('table_data_rows',[]))
    if len(row_ids) != len(rows):
        return None
    headers = [row for row_id,row in zip(row_ids,rows) if row_id in header_ids]
    matches = []
    for row_id,row in zip(row_ids,rows):
        if row_id not in data_ids or not row or formatted(row[0]) != formatted(label):
            continue
        for column,value in enumerate(row):
            if whitespace(value) != amount:
                continue
            preceding = [c for c in row[:column] if c.strip()]
            if not preceding or preceding[-1] != '$':
                continue
            column_headers = [formatted(h[column]) for h in headers if column < len(h)]
            if year not in column_headers:
                continue
            if not any('year ended '+period.group(1) in h for h in column_headers):
                continue
            matches.append({'row_label':row[0], 'value':value, 'currency':'$', 'year':year,
                            'period':'year ended '+period.group(1), 'units':unit.group(1)+'s',
                            'source_table_row':row_id, 'grid_column':column,
                            'column_headers':column_headers})
    return matches[0] if len(matches)==1 else None


def match_quote(item, record, version=2):
    rank, quote = item.get('passage_rank'),item.get('quote','')
    hit = next((h for h in record['hits'][:5] if h['rank']==rank),None)
    if hit is None:
        return {'matched':False,'method':'invalid_passage','reason':f'Passage {rank} was not retrieved.'}
    if not isinstance(quote,str) or not quote.strip():
        return {'matched':False,'method':'empty_quote','reason':'The quotation is empty.'}
    if whitespace(quote) in whitespace(hit['text']):
        return {'matched':True,'method':'exact_text','reason':'Continuous source text matches after whitespace normalization.'}
    if version==2:
        if formatted(quote) in formatted(hit['text']):
            return {'matched':True,'method':'formatting_only','reason':'Text matches after escaped-whitespace normalization and case folding; numbers, words and punctuation retained.'}
        table = table_cell_match(item,hit,record['question'])
        if table:
            return {'matched':True,'method':'structured_table_cell', 'table_evidence':table,
                    'reason':'Shortened table citation verified by row label, annual column, exact value, currency and attached units; not a verbatim quote.'}
    return {'matched':False,'method':'quote_mismatch',
            'reason':f'Quote in passage {rank} is not continuous matching text; no verified structured-table match. It may omit/rewrite words or cite the wrong passage.'}


def validate(review,record,version=2):
    if not isinstance(review,dict) or set(review)!=FIELDS or review.get('label') not in LABELS:
        return ['Invalid review schema'],[]
    errors,details=[],[]
    for number,item in enumerate(review['supported_requirements'],1):
        detail = {'requirement_number':number,'requirement':item['requirement'],
                  'passage_rank':item['passage_rank'],'quote':item['quote'],**match_quote(item,record,version)}
        details.append(detail)
        if not detail['matched']:
            errors.append(f'Unsupported quote or passage rank: {item.get("passage_rank")}')
    label=review['label']
    if record['kind']=='unanswerable' and label not in ('not_established','potential_reference_issue'):
        errors.append('Unanswerable question given answerable label')
    if record['kind']!='unanswerable' and label in ('not_established','potential_reference_issue'):
        errors.append('Answerable question given unanswerable label')
    if label in ('sufficient','partially_sufficient','potential_reference_issue') and not review['supported_requirements']:
        errors.append('Positive evidence assessment has no supporting quotes')
    if label=='sufficient' and review['missing_requirements']:
        errors.append('Sufficient label lists missing requirements')
    if label=='partially_sufficient' and not review['missing_requirements']:
        errors.append('Partial label has no missing requirement')
    return errors,details
