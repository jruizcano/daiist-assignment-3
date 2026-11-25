"""Integrity checks on offline chunks; not retrieval-quality evaluation."""
import json
from collections import defaultdict
from pathlib import Path
from ingestion.chunk_corpus import words, source_text, lookup_topic, paragraph_parts


def validate_long_paragraph_overlap():
    for lengths in ([50]*12, [350,100,100], [100,700,100], [900], [200,200,200], [390,390,390]):
        text=' '.join(' '.join([f'Sentence{i}']+['word']*(n-2)+['end.']) for i,n in enumerate(lengths))
        parts=paragraph_parts({'text':text})
        assert all(0 < end-start <= 400 for _,start,end,_ in parts)
        covered={i for _,start,end,_ in parts for i in range(start,end)}
        assert covered==set(range(len(words(text))))
        for previous,current in zip(parts,parts[1:]):
            assert previous[1]<current[1]<previous[2]<current[2], 'Missing overlap or forward progress'
        if lengths==[50]*12:
            assert all(a[2]-b[1]==100 for a,b in zip(parts,parts[1:]))
        if lengths==[900]:
            assert all(a[2]-b[1]==80 for a,b in zip(parts,parts[1:]))
    print('Long paragraph overlap, size limit and forward progress: PASS',flush=True)


def validate(folder):
    validate_long_paragraph_overlap()
    folder=Path(folder)
    blocks={}
    for p in folder.glob('*_blocks_with_paths.jsonl'):
        for line in p.read_text(encoding='utf-8').splitlines():
            b=json.loads(line);blocks[b['id']]=b
    designs={d:[json.loads(l) for l in (folder/(d+'.jsonl')).read_text(encoding='utf-8').splitlines()]
             for d in ['a_fixed','b_paragraph','c_heading_path']}
    assert len(designs['a_fixed'])==len(designs['c_heading_path'])
    for a,c in zip(designs['a_fixed'],designs['c_heading_path']):
        for key in ['body','text','source_spans','heading_path','attached_note_ids','content_id']:
            assert a[key]==c[key],key
        assert c['embedding_text']=='Heading path: '+' > '.join(c['heading_path'])+'\n\n'+a['text']
        assert a['embedding_text']==a['text']
    required_notes={n for b in blocks.values() for n in b.get('attached_note_ids',[])}
    for design,chunks in designs.items():
        assert len({c['id'] for c in chunks})==len(chunks)
        covered=defaultdict(set);table_rows=defaultdict(set);attached=set()
        for c in chunks:
            assert c['text'] and c['embedding_text']
            for bid in c['source_block_ids']:
                assert blocks[bid]['document_id']==c['document_id']
                assert blocks[bid]['section']==c['section']
                assert blocks[bid]['heading_path']==c['heading_path']
                assert set(blocks[bid].get('attached_note_ids',[]))<=set(c['attached_note_ids'])
            attached.update(c['attached_note_ids'])
            for nid in c['attached_note_ids']:
                assert source_text(blocks[nid]) in c['text'],(c['id'],nid)
            if c['kind']=='prose':
                assert c['body_word_count']<=400,(design,c['id'],c['body_word_count'])
                for span in c['source_spans']:
                    bid=span['block_id'];start=span['word_start'];end=span['word_end']
                    assert 0<=start<end<=len(words(source_text(blocks[bid])))
                    assert ' '.join(words(source_text(blocks[bid]))[start:end]) in c['body'].replace('\n\n',' ')
                    covered[bid].update(range(start,end))
                    if design=='b_paragraph' and len(words(source_text(blocks[bid])))<=400:
                        assert start==0 and end==len(words(source_text(blocks[bid]))),('split short paragraph',bid)
            else:
                bid=c['source_block_ids'][0]
                assert len(c['table_grid_rows'])==len(c['table_grid'])
                assert set(c['table_header_rows'])<=set(c['table_grid_rows'])
                for r,row in zip(c['table_grid_rows'],c['table_grid']):
                    assert row==blocks[bid]['grid'][r]
                    table_rows[bid].add(r)
        if design in ('a_fixed','c_heading_path'):
            def positions(c):
                return [(s['block_id'],i) for s in c['source_spans']
                        for i in range(s['word_start'],s['word_end'])]
            for previous,current in zip(chunks,chunks[1:]):
                if previous['kind']==current['kind']=='prose' and previous['document_id']==current['document_id'] and previous['heading_path']==current['heading_path']:
                    assert positions(previous)[-80:]==positions(current)[:80],('overlap',current['id'])
        assert required_notes<=attached,(design,'missing attached notes')
        for bid,b in blocks.items():
            if bid in required_notes:continue
            if b.get('table_role')=='data_candidate':
                expected={c['row'] for c in b['source_cells'] if c['text']}
                assert expected<=table_rows[bid],(design,bid,'lost table rows')
            else:
                assert covered[bid]==set(range(len(words(source_text(b))))),(design,bid,'lost text')
        print(f'{design}: PASS — coverage, boundaries, notes, table cells, IDs',flush=True)
    c=designs['c_heading_path']
    found=lookup_topic(c,'doordash_2020_ipo','Components of Results of Operations > Revenue',100)
    assert found['total_matches']>0
    assert all(x['document_id']=='doordash_2020_ipo' for x in found['chunks'])
    assert any('doordash_2020_ipo-b01007' in x['source_block_ids'] for x in found['chunks'])
    print('A/C parity, B paragraph preservation and topic lookup: PASS',flush=True)
    return {'status':'passed','design_counts':{d:len(v) for d,v in designs.items()},
            'checks':['A/C identical bodies','source word coverage','document/section/path boundaries',
                      'complete attached notes','short paragraphs unbroken in B','table row/cell fidelity',
                      'unique IDs','local topic lookup'],'embedding_calls':0}


if __name__=='__main__':
    import sys
    result=validate(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]/'corpus/chunks')
    print(json.dumps(result,indent=2))
