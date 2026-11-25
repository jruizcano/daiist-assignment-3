"""Three offline chunking designs. No network, API client or embeddings.

The fixed-word-window/overlap approach adapts the supplied gemini_rag.ipynb
chunk_document example. HTML paths, provenance and protected-table handling
are new project-specific code.

uv run python -m ingestion.chunk_corpus
uv run python -m ingestion.chunk_corpus --document doordash_2020_ipo --topic Revenue
"""
import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ingestion.heading_paths import annotate_paths

ROOT = Path(__file__).resolve().parents[1]
SIZE, OVERLAP = 400, 80
DESIGNS = ('a_fixed', 'b_paragraph', 'c_heading_path')


def words(text):
    return [w for w in text.split() if w != '|']


def source_text(b):
    return b.get('paragraph_text', b.get('table_text', b['text']))


def attached_notes(ids, lookup):
    note_ids = list(dict.fromkeys(n for i in ids for n in lookup[i].get('attached_note_ids', [])))
    text = '\n\n'.join(source_text(lookup[n]) for n in note_ids)
    return note_ids, text


def make_chunk(design, blocks, lookup, body, spans, extra=None):
    first = blocks[0]
    ids = list(dict.fromkeys(b['id'] for b in blocks))
    note_ids, notes = attached_notes(ids, lookup)
    text = body + ('\n\nAttached notes:\n' + notes if notes else '')
    path = first['heading_path']
    topic_key = hashlib.sha256(json.dumps([first['document_id'], path], ensure_ascii=False).encode()).hexdigest()[:16]
    identity = json.dumps([first['document_id'], ids, spans, body], ensure_ascii=False)
    content_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
    chunk = {
        'id':f'{design}-{content_id}', 'content_id':content_id, 'design':design,
        'document_id':first['document_id'], 'company':first['company'],
        'filing_date':first['filing_date'], 'section':first['section'],
        'source_url':first['source_url'], 'citation_url':first['citation_url'],
        'heading_path':path, 'topic_id':topic_key,
        'source_block_ids':ids, 'source_spans':spans, 'attached_note_ids':note_ids,
        'heading_block_ids':first['heading_block_ids'], 'body':body, 'text':text,
        'embedding_text':text, 'body_word_count':len(words(body)),
        'word_count':len(words(text)), 'kind':'prose',
        'unresolved_footnotes':[ref for b in blocks for ref in b.get('unresolved_footnotes', [])]
    }
    if extra:
        chunk.update(extra)
    chunk['oversize_reason'] = ('headers, complete rows and/or attached notes retained' if chunk['word_count'] > SIZE else None)
    return chunk


def fixed_chunks(run, lookup):
    tokens, owners = [], []
    for b in run:
        ws = words(source_text(b))
        tokens.extend(ws)
        owners.extend((b['id'], i) for i in range(len(ws)))
    output = []
    for start in range(0, len(tokens), SIZE-OVERLAP):
        end = min(start+SIZE, len(tokens))
        if not tokens[start:end]:
            break
        spans = []
        for bid, offset in owners[start:end]:
            if spans and spans[-1]['block_id'] == bid:
                spans[-1]['word_end'] = offset+1
            else:
                spans.append({'block_id':bid, 'word_start':offset, 'word_end':offset+1})
        bs = [lookup[s['block_id']] for s in spans]
        output.append(make_chunk('a_fixed', bs, lookup, ' '.join(tokens[start:end]), spans))
        if end == len(tokens):
            break
    return output


def paragraph_parts(block):
    text = source_text(block)
    all_words = words(text)
    if len(all_words) <= SIZE:
        return [(block, 0, len(all_words), ' '.join(all_words))]
    # Prefer sentence boundaries and a complete-sentence suffix near OVERLAP.
    # If that cannot fit new text within SIZE, fall back to a word-level tail.
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z(])', text)
    ends, offset = [], 0
    for sentence in sentences:
        offset += len(words(sentence))
        ends.append(offset)
    assert offset == len(all_words)
    starts = [0] + ends[:-1]
    parts, start = [], 0
    while start < len(all_words):
        previous_end = parts[-1][2] if parts else 0
        possible_ends = [e for e in ends if max(start,previous_end) < e <= start+SIZE]
        end = max(possible_ends) if possible_ends else min(start+SIZE,len(all_words))
        parts.append((block,start,end,' '.join(all_words[start:end])))
        if end == len(all_words):break
        following_end = next(e for e in ends if e > end)
        candidates = [s for s in starts if start < s < end and following_end-s <= SIZE]
        if end in ends and candidates:
            # Tie-break toward the smaller overlap. Always advance the start.
            start = min(candidates,key=lambda s:(abs((end-s)-OVERLAP),end-s))
        else:
            start = max(start+1,end-OVERLAP)
    return parts


def paragraph_chunks(run, lookup):
    parts = [part for b in run for part in paragraph_parts(b)]
    output, current = [], []
    def emit(items):
        spans = [{'block_id':b['id'],'word_start':s,'word_end':e} for b,s,e,_ in items]
        return make_chunk('b_paragraph',[p[0] for p in items],lookup,'\n\n'.join(p[3] for p in items),spans)
    for part in parts:
        if current and sum(p[2]-p[1] for p in current)+part[2]-part[1]>SIZE:
            output.append(emit(current))
            last=current[-1]
            # Repeat one COMPLETE short paragraph, not a cut-off tail.
            whole = last[1]==0 and last[2]==len(words(source_text(last[0])))
            current=[last] if whole and last[2]-last[1]<=OVERLAP and last[2]-last[1]+part[2]-part[1]<=SIZE else []
        current.append(part)
    if current:output.append(emit(current))
    return output


def table_rows(block):
    rows=defaultdict(list)
    for c in block['source_cells']:
        rows[c['row']].append(c)
    active=[r for r in sorted(rows) if any(c['text'] for c in rows[r])]
    # Identify the first labelled numeric data row. Keep all preceding header
    # rows, including merged period headers and unit qualifiers.
    first_data=None
    for r in active:
        cells=[c for c in rows[r] if c['text']]
        label=cells[0]['text']
        numeric=any(re.search(r'\d',c['text']) and re.fullmatch(r'[\d,.$%() +\-−–—]+',c['text']) for c in cells[1:])
        if re.search(r'[A-Za-z]',label) and numeric and not re.match(r'^(?:year|years|three months|six months|nine months|as of|\(?in |\(?unaudited)',label,re.I):
            first_data=r;break
    if first_data is None:
        return [],active,'No confident data/header boundary; table kept whole.'
    header=[r for r in active if r<first_data]
    data=[r for r in active if r>=first_data]
    return header,data,None


def table_chunks(block, lookup, design):
    headers,data_rows,warning=table_rows(block)
    grid=block['grid']
    def render(row_ids):
        return '\n'.join(' | '.join(grid[r]) for r in row_ids)
    # Repeat any unit/qualification row even if the source places it mid-table.
    qualification_rows=[r for r,row in enumerate(grid) if any(
        re.search(r'\b(?:in thousands|in millions|in billions|unaudited)\b',cell,re.I) for cell in row)]
    headers=sorted(set(headers+qualification_rows))
    body_rows=[r for r in data_rows if r not in headers]
    if not body_rows:
        body_rows=data_rows
    if warning:
        groups=[body_rows]
    else:
        groups=[];current=[];size=0
        for r in body_rows:
            # Do not cut through a rowspan crossing the proposed row boundary.
            crossing=any(c['row']<r<c['row']+c['rowspan'] for c in block['source_cells'])
            n=len(words(render([r])))
            if current and size+n>SIZE and not crossing:
                groups.append(current);current=[];size=0
            current.append(r);size+=n
        if current:groups.append(current)
    output=[]
    for number,group in enumerate(groups):
        row_ids=sorted(set(headers+group))
        context='\n'.join(x['text'] for x in block.get('nearby_unit_text',[]))
        body=(context+'\n' if context else '')+render(row_ids)
        spans=[{'block_id':block['id'],'table_rows':group}]
        output.append(make_chunk(design,[block],lookup,body,spans,{
            'kind':'table','table_part':number+1,'table_parts':len(groups),
            'table_header_rows':headers,'table_data_rows':group,'table_grid_rows':row_ids,
            'table_grid':[grid[r] for r in row_ids], 'table_original_html':block['original_html'],
            'table_boundary_warning':warning}))
    return output


def build_designs(blocks):
    lookup={b['id']:b for b in blocks}
    note_ids={n for b in blocks for n in b.get('attached_note_ids',[])}
    result={'a_fixed':[],'b_paragraph':[]}
    run=[]
    def flush():
        if run:
            result['a_fixed'].extend(fixed_chunks(run,lookup))
            result['b_paragraph'].extend(paragraph_chunks(run,lookup))
            run.clear()
    for b in blocks:
        if b['id'] in note_ids:
            # Content remains attached to its citing text, not indexed twice.
            continue
        if run and (run[-1]['document_id']!=b['document_id'] or run[-1]['heading_path']!=b['heading_path']):
            flush()
        if b.get('table_role')=='data_candidate':
            flush()
            for design in result:result[design].extend(table_chunks(b,lookup,design))
        else:
            run.append(b)
    flush()
    result['c_heading_path']=[]
    for a in result['a_fixed']:
        c=dict(a)
        c['design']='c_heading_path'
        c['id']='c_heading_path-'+c['content_id']
        c['embedding_text']='Heading path: '+' > '.join(c['heading_path'])+'\n\n'+c['text']
        result['c_heading_path'].append(c)
    return result


def write_jsonl(path,records):
    with path.open('w',encoding='utf-8') as f:
        for r in records:f.write(json.dumps(r,ensure_ascii=False)+'\n')


def lookup_topic(chunks, document_id, query, limit=20):
    """Offline topic expansion: heading segment/path substring, scoped by doc.

    A main-heading query includes descendant paths. This is NOT semantic search.
    """
    query=query.casefold()
    matches=[c for c in chunks if (not document_id or c['document_id']==document_id)
             and query in ' > '.join(c['heading_path']).casefold()]
    return {'total_matches':len(matches),'returned':min(limit,len(matches)),
            'chunks':matches[:limit]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--topic')
    parser.add_argument('--document')
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--output-dir',type=Path,help='Optional destination for local chunk files')
    args=parser.parse_args()
    folder=args.output_dir or ROOT/'corpus/chunks'
    if args.topic:
        records=[json.loads(l) for l in (folder/'c_heading_path.jsonl').read_text(encoding='utf-8').splitlines()]
        print(json.dumps(lookup_topic(records,args.document,args.topic,max(1,args.limit)),ensure_ascii=False,indent=2))
        return
    sources=json.loads((ROOT/'corpus_sources.json').read_text(encoding='utf-8'))['documents']
    folder.mkdir(parents=True,exist_ok=True)
    combined={d:[] for d in DESIGNS}
    heading_counts={}
    for source in sources:
        blocks=[json.loads(l) for l in (ROOT/'corpus/processed'/source['id']/'blocks.jsonl').read_text(encoding='utf-8').splitlines()]
        annotated=annotate_paths(blocks,ROOT/'corpus'/source['filename'])
        write_jsonl(folder/(source['id']+'_blocks_with_paths.jsonl'),annotated)
        heading_counts[source['id']]=sum(b['is_heading'] for b in annotated)
        designs=build_designs(annotated)
        for d in DESIGNS:combined[d].extend(designs[d])
        print(source['company']+': '+', '.join(f'{d}={len(designs[d])}' for d in DESIGNS),flush=True)
    summary={'configuration':{'prose_target_words':SIZE,'fixed_overlap_words':OVERLAP,
              'paragraph_overlap':'last complete paragraph if <=80 words',
              'long_paragraph_overlap':'complete trailing sentences closest to 80 words; 80-word tail fallback when needed for progress within 400 words',
              'boundaries':'all designs share document, section and inferred heading boundaries; tables separate',
              'table_policy':'400 data words target; repeat headers, units and attached notes; preserve rowspans',
              'embedding_calls':0,'heading_detection':'HTML styling heuristic; review paths before indexing',
              'corpus_status':'intermediate full extraction; personal-data review still pending'},
             'heading_counts':heading_counts,'designs':{}}
    for d,records in combined.items():
        write_jsonl(folder/(d+'.jsonl'),records)
        summary['designs'][d]={'chunks':len(records),'by_document':dict(Counter(c['document_id'] for c in records)),
             'prose_chunks':sum(c['kind']=='prose' for c in records),'table_chunks':sum(c['kind']=='table' for c in records),
             'average_words':round(sum(c['word_count'] for c in records)/len(records),1),
             'max_words':max(c['word_count'] for c in records),'over_target':sum(c['word_count']>SIZE for c in records),
             'chunks_with_notes':sum(bool(c['attached_note_ids']) for c in records),
             'prepared_embedding_words':sum(len(words(c['embedding_text'])) for c in records)}
    topic_index={}
    for c in combined['c_heading_path']:
        key=c['topic_id']
        topic_index.setdefault(key,{'document_id':c['document_id'],'heading_path':c['heading_path'],'chunk_ids':[]})['chunk_ids'].append(c['id'])
    (folder/'topic_index.json').write_text(json.dumps(topic_index,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved three designs and topic index. Embedding calls: 0.',flush=True)


if __name__=='__main__':
    main()
