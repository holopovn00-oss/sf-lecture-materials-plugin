"""Current text-package provenance and protected-decision regressions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = (Path(__file__).resolve().parents[1] / 'plugins/sf-lecture-materials/skills')


def load(path, name):
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HANDOFF = load(ROOT / 'sf-transcript-to-lecture/scripts/handoff.py', 'handoff_test')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return ref(path)


def ref(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest().upper()}


class PackageChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / 'source.txt'
        self.raw.write_text('Обычно около 15%, если спрос не падает.', encoding='utf-8')
        sources = [{'source_block_id': 's1', 'text': self.raw.read_text(encoding='utf-8'), 'source_uri': str(self.raw)}]
        draft = {'schema_version': '3.0.0', 'folder_id': 'one', 'title': 'Проверка', 'blocks': [{'text_block_id': 't1', 'content': [{'type': 'paragraph', 'runs': [{'type': 'text', 'text': sources[0]['text']}]}], 'source_block_ids': ['s1']}], 'transformation_ledger': [], 'structure': {'sections': [{'section_id': 'sec', 'number': '1', 'title': 'Условия'}], 'topics': [{'topic_id': 'topic', 'section_id': 'sec', 'title': 'Спрос'}], 'placements': [{'text_block_id': 't1', 'section_id': 'sec', 'topic_id': 'topic'}]}}
        self.lecture, self.sources = HANDOFF.build(sources, draft)
        self.package = self.root / 'package'
        HANDOFF.write_package(self.package, self.sources, self.lecture)
        write(self.package/'source-manifest.json', {'files': [{**ref(self.raw), 'order': 1, 'extraction': 'UTF-8 text'}]})
        self.review = {'schema_version': '2.0', 'artifacts': {key: ref(self.package/file) for key, file in {'sources': 'source-blocks.json', 'lecture': 'lecture-text.json', 'source_manifest': 'source-manifest.json'}.items()}, 'semantic_review': {'status': 'COMPLETED', 'covered_source_ids': ['s1'], 'open_issues': [], 'observations': ['s1: условие спроса сохранено в t1.']}, 'decisions': [{'id': 'keep-condition', 'decision': 'keep', 'execution': 'verified', 'basis': 'Пользователь: оставить условие.', 'source_block_ids': ['s1'], 'targets': [{'layer': 'text', 'id': 't1', 'expected': 'если спрос не падает', 'count': 1}]}]}
        self.review_path = self.package/'text-review.json'

    def check(self, previous=None):
        self.assertTrue(hasattr(HANDOFF, 'check_package'), 'Package validation not implemented')
        write(self.review_path, self.review)
        return HANDOFF.check_package(self.review_path, previous)

    def reseal_lecture(self, text):
        self.lecture['blocks'][0]['content'][0]['runs'][0]['text'] = text
        self.lecture['content_hash'] = HANDOFF.digest({k: v for k, v in self.lecture.items() if k != 'content_hash'})
        self.review['artifacts']['lecture'] = write(self.package/'lecture-text.json', self.lecture)

    def test_valid_package_does_not_claim_semantic_proof(self):
        result = self.check()
        self.assertEqual(result['status'], 'PACKAGE_VALIDATED')
        self.assertEqual(result['semantic_review'], 'NOT_EVALUATED_BY_SCRIPT')

    def test_changed_original_file_rejected(self):
        self.raw.write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.check()

    def test_source_uri_must_belong_to_manifest(self):
        self.review['artifacts']['source_manifest'] = write(self.package/'source-manifest.json', {'files': []})
        with self.assertRaises(ValueError):
            self.check()

    def test_preserved_decision_detects_lost_condition_in_resealed_text(self):
        self.reseal_lecture('Обычно около 15%.')
        with self.assertRaises(ValueError):
            self.check()

    def test_omitted_previous_decision_rejected(self):
        previous = self.package/'previous-review.json'
        write(previous, self.review)
        self.review['decisions'] = []
        with self.assertRaises(ValueError):
            self.check(previous)

    def test_changed_previous_decision_requires_new_basis(self):
        previous = self.package/'previous-review.json'
        write(previous, self.review)
        self.review['decisions'][0]['targets'][0]['expected'] = 'Обычно'
        with self.assertRaises(ValueError):
            self.check(previous)

    def test_incomplete_coverage_cannot_be_recorded_complete(self):
        self.review['semantic_review']['covered_source_ids'] = []
        with self.assertRaises(ValueError):
            self.check()


if __name__ == '__main__':
    unittest.main()
