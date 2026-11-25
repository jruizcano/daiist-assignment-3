"""Resumable local index for the three IPO chunking designs.

Only missing unique texts are sent to OpenAI. No answer-generation calls.
SQLite stores float32 vectors; gzip files preserve all citation metadata.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing

import httpx
import numpy as np
import tiktoken

ROOT = Path(__file__).resolve().parent
MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
PRICE_PER_MILLION = 0.02
DESIGNS = ('a_fixed', 'b_paragraph', 'c_heading_path')


def load_key(root=ROOT):
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key and (root / '.env').is_file():
        for line in (root / '.env').read_text(encoding='utf-8-sig').splitlines():
            name, sep, value = line.strip().partition('=')
            if sep and name.strip() == 'OPENAI_API_KEY':
                key = value.strip().strip('\"').strip("'")
    if not key:
        raise RuntimeError('Set OPENAI_API_KEY in the environment or local .env.')
    return key


def vector_key(text):
    return hashlib.sha256(f'{MODEL}\0{DIMENSIONS}\0{text}'.encode('utf-8')).hexdigest()


def load_chunks(root):
    result = {}
    for design in DESIGNS:
        path = root / 'corpus/chunks' / f'{design}.jsonl'
        if not path.is_file():
            raise RuntimeError('Run chunk_corpus.py first; chunk files are missing.')
        result[design] = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    return result


def connect(folder):
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / 'vectors.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, tokens INTEGER NOT NULL, created REAL NOT NULL)')
    db.commit()
    return db


def request_embeddings(client, texts, log=print):
    for attempt in range(6):
        try:
            response = client.post('https://api.openai.com/v1/embeddings', json={
                'model': MODEL, 'input': texts, 'dimensions': DIMENSIONS, 'encoding_format': 'float'})
        except httpx.TransportError:
            # An ambiguous timeout may already have been billed: stop, don't blindly retry.
            raise RuntimeError('Network interruption. Saved batches remain available; rerun to resume.') from None
        if response.status_code == 200:
            result = response.json()
            data = sorted(result['data'], key=lambda item: item['index'])
            if [item['index'] for item in data] != list(range(len(texts))):
                raise RuntimeError('Unexpected embedding response ordering/count; batch not saved.')
            vectors = np.asarray([item['embedding'] for item in data], dtype='<f4')
            if vectors.shape != (len(texts), DIMENSIONS) or not np.isfinite(vectors).all():
                raise RuntimeError('Invalid embedding dimensions or non-finite values.')
            if np.any(np.linalg.norm(vectors, axis=1) == 0):
                raise RuntimeError('Zero embedding returned.')
            return vectors, int(result['usage']['total_tokens'])
        try:
            code = response.json().get('error', {}).get('code')
        except ValueError:
            code = None
        if response.status_code == 429 and code != 'insufficient_quota' and attempt < 5:
            log('Rate limit reached; waiting before retry.', flush=True)
            time.sleep(min(2 ** (attempt + 1), 60))
            continue
        if response.status_code == 401:
            raise RuntimeError('OpenAI rejected the API key (401). Check the local key.')
        if code == 'insufficient_quota':
            raise RuntimeError('OpenAI reports insufficient quota. Enable API billing or add credits, then resume.')
        raise RuntimeError(f'OpenAI returned HTTP {response.status_code}; saved batches retained.')


def embed_and_store(texts, db, key, batch_size=32, log=print):
    """Reusable embedding stage; cache identity includes exact text/model/dimensions."""
    unique = {vector_key(text): text for text in texts}
    saved = {row[0] for row in db.execute('SELECT key FROM vectors')}
    pending = [(k, text) for k, text in unique.items() if k not in saved]
    with httpx.Client(headers={'Authorization': f'Bearer {key}'}, timeout=120) as client:
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            vectors, tokens = request_embeddings(client, [text for _, text in batch], log=log)
            with db:
                db.executemany('INSERT INTO vectors VALUES (?, ?)',
                               [(k, vector.tobytes()) for (k, _), vector in zip(batch, vectors)])
                db.execute('INSERT INTO usage(tokens,created) VALUES (?,?)', (tokens, time.time()))
            log(f'Saved {min(start + batch_size, len(pending))}/{len(pending)} new vectors; batch tokens={tokens}', flush=True)


def export_metadata(chunks, folder):
    for design, records in chunks.items():
        path = folder / f'{design}.jsonl.gz'
        temporary = path.with_suffix('.tmp')
        with gzip.open(temporary, 'wt', encoding='utf-8') as stream:
            for record in records:
                stream.write(json.dumps({**record, 'vector_key': vector_key(record['embedding_text'])}, ensure_ascii=False) + '\n')
        temporary.replace(path)


def build(root=ROOT, dry_run=False, max_cost=0.25):
    chunks = load_chunks(root)
    texts = list(dict.fromkeys(c['embedding_text'] for records in chunks.values() for c in records))
    encoding = tiktoken.encoding_for_model(MODEL)
    counts = {vector_key(text): len(encoding.encode(text, disallowed_special=())) for text in texts}
    if max(counts.values()) > 8191 or min(counts.values()) == 0:
        raise RuntimeError('An input is empty or exceeds the per-text token limit; no API calls made.')
    folder = root / 'index'
    with closing(connect(folder)) as db:
        saved = {row[0] for row in db.execute('SELECT key FROM vectors')}
        remaining_tokens = sum(n for k, n in counts.items() if k not in saved)
        estimated_cost = remaining_tokens * PRICE_PER_MILLION / 1_000_000
        plan = {'model': MODEL, 'dimensions': DIMENSIONS, 'design_counts': {d: len(v) for d, v in chunks.items()},
                'unique_texts': len(texts), 'total_input_tokens': sum(counts.values()),
                'max_input_tokens': max(counts.values()), 'remaining_texts': len(set(counts) - saved),
                'remaining_tokens': remaining_tokens, 'estimated_remaining_usd': round(estimated_cost, 6)}
        print(json.dumps(plan, indent=2), flush=True)
        if dry_run:
            return plan
        if estimated_cost > max_cost:
            raise RuntimeError(f'Estimated remaining cost exceeds ${max_cost:.2f}; no API calls made.')
        if remaining_tokens:
            embed_and_store(texts, db, load_key(root))
        export_metadata(chunks, folder)
        total_billed = db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]
        plan.update({'status': 'complete', 'provider': 'openai', 'billed_tokens_saved_batches': total_billed,
                     'cost_usd_saved_batches': round(total_billed * PRICE_PER_MILLION / 1_000_000, 6),
                     'price_usd_per_million_tokens': PRICE_PER_MILLION,
                     'assignment_deviation': 'OpenAI embeddings instead of Gemini, explicitly requested by user.'})
        (folder / 'manifest.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
        size = sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
        print(f'Index complete: {size / 1e6:.2f} MB. Saved-batch embedding cost: ${plan["cost_usd_saved_batches"]:.6f}', flush=True)
        if size > 90_000_000:
            raise RuntimeError('Index exceeds the assignment 90 MB limit.')
        return plan


def chunk_file(path, source, output_dir):
    """Shared HTML ingestion: existing extractor, heading inference and all chunk designs."""
    from ingestion.extract_corpus import extract_document
    from ingestion.heading_paths import annotate_paths
    from ingestion.chunk_corpus import build_designs
    extract_document(Path(path), source, Path(output_dir))
    blocks = [json.loads(l) for l in (Path(output_dir)/'blocks.jsonl').read_text(encoding='utf-8').splitlines()]
    return build_designs(annotate_paths(blocks,Path(path)))


def add_document(path, root=ROOT, max_cost=0.25):
    """Index a local HTML file through the same extraction/chunk/embedding functions."""
    from ingestion.document_ingestion import ingest_html
    return ingest_html(path,Path(root),max_cost=max_cost)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--max-cost', type=float, default=0.25)
    args = parser.parse_args()
    try:
        build(args.root, args.dry_run, args.max_cost)
    except (RuntimeError, KeyboardInterrupt) as error:
        raise SystemExit(str(error) or 'Interrupted; saved vectors will be reused.') from None


if __name__ == '__main__':
    main()
