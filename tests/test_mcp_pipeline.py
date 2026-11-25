"""Offline tests: uv run python -m unittest tests.test_mcp_pipeline -v"""
import gzip
import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
import index
import ingestion.document_ingestion as document_ingestion
from retrieval.search import Retriever

HTML='<html><head><title>Fixture Corporation</title></head><body><h1>Financial results</h1><p>Revenue is reported in millions.</p><table><tr><th>Year</th><th>Revenue</th></tr><tr><td>2019</td><td>885</td></tr></table><p>Test fixture only; not an actual prospectus.</p></body></html>'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        tokenizer=patch.object(document_ingestion.tiktoken,'encoding_for_model',return_value=SimpleNamespace(encode=lambda text,**kwargs:text.split()))
        tokenizer.start();self.addCleanup(tokenizer.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        folder=self.root/'index';folder.mkdir()
        self.seed={'id':'seed','document_id':'seed_doc','company':'Seed','section':'Overview',
                   'text':'A baseline document.','embedding_text':'A baseline document.',
                   'vector_key':index.vector_key('A baseline document.')}
        with closing(index.connect(folder)) as db:
            v=np.ones(index.DIMENSIONS,dtype='<f4');v/=np.linalg.norm(v)
            db.execute('INSERT INTO vectors VALUES (?,?)',(self.seed['vector_key'],v.tobytes()));db.commit()
        (folder/'manifest.json').write_text(json.dumps({'model':index.MODEL,'dimensions':index.DIMENSIONS}))
        for design in index.DESIGNS:
            with gzip.open(folder/f'{design}.jsonl.gz','wt',encoding='utf-8') as f:f.write(json.dumps({**self.seed,'design':design})+'\n')
        self.path=self.root/'new.html';self.path.write_text(HTML)
        self.api_calls=[]

    def tearDown(self):self.tmp.cleanup()

    def fake_api(self,client,texts,log=print):
        self.api_calls.extend(texts)
        rows=[]
        for text in texts:
            rng=np.random.default_rng(int(hashlib.sha256(text.encode()).hexdigest()[:8],16))
            v=rng.normal(size=index.DIMENSIONS).astype('<f4');rows.append(v/np.linalg.norm(v))
        return np.stack(rows),sum(len(t.split()) for t in texts)

    def add(self,path=None):
        with patch.object(document_ingestion,'load_key',return_value='test-only'),patch.object(index,'request_embeddings',side_effect=self.fake_api):
            return index.add_document(path or self.path,self.root)

    def test_ingest_reload_duplicates_and_new_version(self):
        base_hash=hashlib.sha256((self.root/'index/vectors.sqlite3').read_bytes()).hexdigest()
        first=self.add();self.assertEqual(first['status'],'added')
        self.assertGreater(first['new_vectors'],0)
        engine=Retriever(self.root)
        for d in index.DESIGNS:
            self.assertEqual(len(engine.records[d]),1+first['chunks_by_design'][d])
        records=engine.records['b_paragraph']
        new=[r for r in records if r['document_id']==first['document_id']]
        self.assertTrue(any('885' in r['text'] for r in new))
        self.assertTrue(any('millions' in r['text'] for r in new))
        calls=len(self.api_calls)
        renamed=self.root/'renamed.html';renamed.write_bytes(self.path.read_bytes())
        self.assertEqual(self.add(renamed)['status'],'already_indexed')
        self.assertEqual(calls,len(self.api_calls))
        self.path.write_text(HTML.replace('885','900'))
        second=self.add();self.assertNotEqual(first['document_id'],second['document_id'])
        self.assertEqual(base_hash,hashlib.sha256((self.root/'index/vectors.sqlite3').read_bytes()).hexdigest())

    def test_failed_embedding_does_not_publish_document(self):
        with patch.object(document_ingestion,'load_key',return_value='test-only'),patch.object(index,'request_embeddings',side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):index.add_document(self.path,self.root)
        self.assertEqual(len(Retriever(self.root).records['b_paragraph']),1)
        self.assertFalse((self.root/'index/additions/ingestion.lock').exists())
        self.assertEqual(self.add()['status'],'added')

    def test_reject_non_html(self):
        path=self.root/'example.pdf';path.write_text('Not HTML')
        with self.assertRaises(ValueError):index.add_document(path,self.root)

    def test_empty_query_no_api(self):
        with self.assertRaises(ValueError):Retriever(self.root).search('  ')

    def test_existing_base_content_skips_ingestion(self):
        (self.root/'corpus').mkdir()
        (self.root/'corpus/base.html').write_bytes(self.path.read_bytes())
        (self.root/'corpus_sources.json').write_text(json.dumps({'documents':[{'id':'seed_doc','filename':'base.html'}]}))
        with patch.object(document_ingestion,'load_key',side_effect=AssertionError('No API expected')):
            result=index.add_document(self.path,self.root)
        self.assertEqual(result['document_id'],'seed_doc')
        self.assertEqual(result['status'],'already_indexed')


if __name__=='__main__':unittest.main()
