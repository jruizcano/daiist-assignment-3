"""Regression checks for package paths and isolated experiment copies."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from experiments.add_document_experiment import copy_runtime
from ingestion import extract_corpus, chunk_corpus, prepare_corpus
from retrieval import search
from evaluation import answer_evaluation, passage_review

ROOT = Path(__file__).resolve().parents[1]


class ProjectLayoutTests(unittest.TestCase):
    def test_modules_resolve_repository_root(self):
        for module in (extract_corpus, chunk_corpus, prepare_corpus, search, answer_evaluation, passage_review):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.ROOT, ROOT)

    def test_isolated_experiment_copies_packages_without_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            copy_runtime(ROOT, destination)
            self.assertFalse((destination / '.env').exists())
            self.assertFalse((destination / 'eval').exists())
            for path in ('server.py', 'agent.py', 'index.py', 'ingestion/extract_corpus.py', 'retrieval/search.py'):
                self.assertTrue((destination / path).is_file(), path)
            result = subprocess.run([sys.executable, '-c',
                'from pathlib import Path; from ingestion.extract_corpus import ROOT; '
                'from retrieval.search import Retriever; from index import chunk_file; '
                'assert ROOT == Path.cwd()'], cwd=destination, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
