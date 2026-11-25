"""Transactional additions alongside the unchanged original three-design index."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import zlib
from bs4 import BeautifulSoup
import tiktoken
from index import MODEL, PRICE_PER_MILLION, chunk_file, connect, embed_and_store, load_key, vector_key


def log(message,**kwargs):
    print(message,file=sys.stderr,**kwargs)


def base_duplicate(root, fingerprint):
    manifest=root/'corpus_sources.json'
    if not manifest.exists():return None
    for source in json.loads(manifest.read_text(encoding='utf-8'))['documents']:
        summary=root/'corpus/processed'/source['id']/'summary.json'
        if summary.exists() and json.loads(summary.read_text(encoding='utf-8')).get('source_sha256')==fingerprint:
            return source['id']
        raw=root/'corpus'/source['filename']
        if raw.exists() and hashlib.sha256(raw.read_bytes()).hexdigest()==fingerprint:
            return source['id']
    return None


def ingest_html(path,root,max_cost=0.25):
    path=Path(path).expanduser()
    if not path.is_absolute():path=root/path
    path=path.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in ('.html','.htm'):
        raise ValueError('add_document supports local .html and .htm files only.')
    if path.stat().st_size>30_000_000:
        raise ValueError('HTML file exceeds the 30 MB ingestion limit.')
    data=path.read_bytes()
    fingerprint=hashlib.sha256(data).hexdigest()
    old=base_duplicate(root,fingerprint)
    if old:return {'status':'already_indexed','document_id':old,'new_vectors':0,'reason':'Identical content is in the original corpus.'}
    folder=root/'index/additions'
    folder.mkdir(parents=True,exist_ok=True)
    lock=folder/'ingestion.lock'
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise RuntimeError('Another document ingestion is active. Retry after it finishes.') from None
    os.close(fd)
    try:
        with closing(connect(folder)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS documents (fingerprint TEXT PRIMARY KEY, document_id TEXT UNIQUE, metadata TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, document_id TEXT, design TEXT, ordinal INTEGER, record BLOB)')
            db.commit()
            existing=db.execute('SELECT document_id FROM documents WHERE fingerprint=?',(fingerprint,)).fetchone()
            if existing:return {'status':'already_indexed','document_id':existing[0],'new_vectors':0,'reason':'Identical file content is already indexed.'}
            document_id='added_'+fingerprint[:24]
            raw=root/'corpus/added'/f'{document_id}.html'
            raw.parent.mkdir(parents=True,exist_ok=True)
            raw.write_bytes(data)
            soup=BeautifulSoup(data,'html.parser')
            title=soup.title.get_text(' ',strip=True) if soup.title else path.stem
            canonical=soup.find('link',rel='canonical')
            url=canonical.get('href','') if canonical else ''
            if not url.startswith(('https://','http://')):url=raw.as_uri()
            source={'id':document_id,'company':title,'title':title,'filing_date':None,'url':url,
                    'original_filename':path.name,'source_sha256':fingerprint}
            designs=chunk_file(raw,source,root/'corpus/processed'/document_id)
            records=[{**r,'vector_key':vector_key(r['embedding_text'])} for rows in designs.values() for r in rows]
            if not records:raise ValueError('No indexable text was extracted; document was not published.')
            unique={r['vector_key']:r['embedding_text'] for r in records}
            encoder=tiktoken.encoding_for_model(MODEL)
            counts={k:len(encoder.encode(t,disallowed_special=())) for k,t in unique.items()}
            if not all(0<n<=8191 for n in counts.values()):raise ValueError('A chunk exceeds the embedding token limit; no new embeddings requested.')
            with closing(sqlite3.connect((root/"index/vectors.sqlite3").resolve().as_uri()+"?mode=ro",uri=True)) as base:
                saved={r[0] for r in base.execute('SELECT key FROM vectors')}
            saved.update(r[0] for r in db.execute('SELECT key FROM vectors'))
            pending=[t for k,t in unique.items() if k not in saved]
            token_count=sum(n for k,n in counts.items() if k not in saved)
            expected_cost=token_count*PRICE_PER_MILLION/1e6
            if expected_cost>max_cost:raise ValueError(f'Estimated embedding cost exceeds ${max_cost:.2f}.')
            compressed=[zlib.compress(json.dumps(r,ensure_ascii=False).encode()) for r in records]
            size=sum(p.stat().st_size for p in (root/'index').rglob('*') if p.is_file())
            reserve=len(pending)*8192+sum((math.ceil((len(blob)+256)/4096)+1)*4096 for blob in compressed)+65536
            if size+reserve>90_000_000:raise ValueError('This addition may exceed the assignment 90 MB index limit; no new embeddings requested.')
            usage_before=db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]
            if pending:embed_and_store(pending,db,load_key(root),log=log)
            # Publication is atomic. Partial embedding runs remain reusable but not searchable.
            with db:
                db.execute('INSERT INTO documents VALUES (?,?,?)',(fingerprint,document_id,json.dumps(source)))
                db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?)',[(r['id'],document_id,r['design'],i,blob) for i,(r,blob) in enumerate(zip(records,compressed))])
            billed=db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]-usage_before
            return {'status':'added','document_id':document_id,'source_url':url,
                    'chunks_by_design':{d:len(v) for d,v in designs.items()},'new_vectors':len(pending),
                    'embedding_tokens':billed,'embedding_cost_usd':billed*PRICE_PER_MILLION/1e6,
                    'index_MB':round(sum(p.stat().st_size for p in (root/'index').rglob('*') if p.is_file())/1e6,3)}
    finally:
        lock.unlink(missing_ok=True)
