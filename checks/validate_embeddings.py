"""Offline completeness and fidelity checks for the saved OpenAI index."""
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import numpy as np

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from index import vector_key, DIMENSIONS

folder = root / 'index'
db = sqlite3.connect(f'file:{(folder / "vectors.sqlite3").as_posix()}?mode=ro', uri=True)
assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
keys = set()
for key, blob in db.execute('SELECT key,vector FROM vectors'):
    vector = np.frombuffer(blob, dtype='<f4')
    assert vector.shape == (DIMENSIONS,) and np.isfinite(vector).all()
    assert abs(float(np.linalg.norm(vector)) - 1) < 0.001
    keys.add(key)
needed = set()
counts = {}
for design in ('a_fixed', 'b_paragraph', 'c_heading_path'):
    originals = [json.loads(line) for line in (root / 'corpus/chunks' / f'{design}.jsonl').read_text(encoding='utf-8').splitlines()]
    with gzip.open(folder / f'{design}.jsonl.gz', 'rt', encoding='utf-8') as stream:
        records = [json.loads(line) for line in stream]
    assert len(originals) == len(records)
    for original, stored in zip(originals, records):
        key = stored.pop('vector_key')
        assert stored == original
        assert key == vector_key(original['embedding_text']) and key in keys
        needed.add(key)
    counts[design] = len(records)
assert keys == needed
manifest = json.loads((folder / 'manifest.json').read_text())
assert manifest['unique_texts'] == len(keys)
assert manifest['design_counts'] == counts
assert manifest['billed_tokens_saved_batches'] == db.execute('SELECT SUM(tokens) FROM usage').fetchone()[0]
db.close()
size = sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
assert size < 90_000_000
print(json.dumps({'status':'passed', 'unique_vectors':len(keys), 'design_counts':counts,
                  'index_MB':round(size/1e6,2), 'saved_batch_cost_USD':manifest['cost_usd_saved_batches'],
                  'checks':['database integrity','vector dimensions and normalization','exact metadata preservation',
                            'all chunks linked to saved vectors','manifest and billing totals','under 90 MB']},indent=2))
