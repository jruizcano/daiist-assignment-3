"""Cosine search over saved IPO vectors; query embeddings are cached separately."""
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import zlib
from contextlib import closing
import numpy as np
from index import MODEL, DIMENSIONS, connect, embed_and_store, load_key, vector_key

ROOT = Path(__file__).resolve().parents[1]


def rank_vectors(matrix, query, k):
    if k < 1:
        raise ValueError('k must be positive')
    query = np.asarray(query, dtype=np.float32)
    norm = np.linalg.norm(query)
    if query.shape != (matrix.shape[1],) or not np.isfinite(query).all() or norm == 0:
        raise ValueError('Invalid query vector')
    scores = matrix @ (query / norm)
    order = np.argsort(-scores, kind='stable')[:k]
    return [(int(i), float(scores[i])) for i in order]


class Retriever:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        folder = self.root / 'index'
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['model'] != MODEL or manifest['dimensions'] != DIMENSIONS:
            raise ValueError('Saved index uses a different model or dimension count')
        self.records = {}
        self.matrices = {}
        with closing(sqlite3.connect((folder / "vectors.sqlite3").resolve().as_uri()+"?mode=ro", uri=True)) as db:
            vectors = {key: np.frombuffer(blob, dtype='<f4') for key, blob in db.execute('SELECT key,vector FROM vectors')}
        additions = {d:[] for d in ('a_fixed','b_paragraph','c_heading_path')}
        extra = folder/'additions'/'vectors.sqlite3'
        if extra.exists():
            with closing(sqlite3.connect(extra.resolve().as_uri()+"?mode=ro",uri=True)) as db:
                vectors.update({key:np.frombuffer(blob,dtype='<f4') for key,blob in db.execute('SELECT key,vector FROM vectors')})
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'chunks' in tables:
                    for design,blob in db.execute('SELECT design,record FROM chunks ORDER BY document_id,ordinal'):
                        additions[design].append(json.loads(zlib.decompress(blob)))
        for design in ('a_fixed', 'b_paragraph', 'c_heading_path'):
            with gzip.open(folder / f'{design}.jsonl.gz', 'rt', encoding='utf-8') as stream:
                records = [json.loads(line) for line in stream]
            records.extend(additions[design])
            matrix = np.stack([vectors[c['vector_key']] for c in records])
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            if matrix.shape[1] != DIMENSIONS or not np.isfinite(matrix).all() or np.any(norms == 0):
                raise ValueError('Invalid saved corpus vectors')
            self.records[design] = records
            self.matrices[design] = matrix / norms

    def embed_queries(self, questions):
        """Embed question text only; never send references or expected locations."""
        with closing(connect(self.root / 'eval/query_cache')) as db:
            saved = {row[0] for row in db.execute('SELECT key FROM vectors')}
            pending = [q for q in dict.fromkeys(questions) if vector_key(q) not in saved]
            if pending:
                embed_and_store(pending, db, load_key(self.root), log=lambda message,**kwargs:print(message,file=sys.stderr,**kwargs))
            return {q: np.frombuffer(db.execute('SELECT vector FROM vectors WHERE key=?',
                                              (vector_key(q),)).fetchone()[0], dtype='<f4').copy()
                    for q in questions}

    def search_vector(self, query_vector, design='a_fixed', k=5):
        ranked = rank_vectors(self.matrices[design], query_vector, k)
        return [{'rank': rank, 'score': score, **self.records[design][i]}
                for rank, (i, score) in enumerate(ranked, 1)]

    def search(self, query, design='a_fixed', k=5):
        if not query.strip():
            raise ValueError('Query must not be blank')
        import tiktoken
        if len(tiktoken.encoding_for_model(MODEL).encode(query,disallowed_special=())) > 8191:
            raise ValueError('Query exceeds the embedding token limit')
        return self.search_vector(self.embed_queries([query])[query], design, k)
