"""Small real-file regressions for the two read-only quality checkers."""
import copy
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
PDF = load(ROOT / 'sf-lecture-to-golden-pdf/scripts/verify_candidate.py', 'candidate_test')


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
        draft = {'folder_id': 'one', 'title': 'Проверка', 'blocks': [{'text_block_id': 't1', 'text': sources[0]['text'], 'source_block_ids': ['s1'], 'perspective': 'lecturer_direct'}], 'transformation_ledger': [], 'structure': {'sections': [{'section_id': 'sec', 'number': '1', 'title': 'Условия'}], 'topics': [{'topic_id': 'topic', 'section_id': 'sec', 'title': 'Спрос'}], 'placements': [{'text_block_id': 't1', 'section_id': 'sec', 'topic_id': 'topic'}]}}
        self.lecture, self.sources = HANDOFF.build(sources, draft)
        self.package = self.root / 'package'
        HANDOFF.write_package(self.package, self.sources, self.lecture)
        write(self.package/'source-manifest.json', {'files': [{**ref(self.raw), 'order': 1, 'extraction': 'UTF-8 text'}]})
        (self.package/'text-review.md').write_text('Проверено условие спроса.', encoding='utf-8')
        self.review = {'schema_version': '1.0', 'artifacts': {key: ref(self.package/file) for key, file in {'sources': 'source-blocks.json', 'lecture': 'lecture-text.json', 'markdown': 'lecture-text.md', 'source_manifest': 'source-manifest.json', 'review': 'text-review.md'}.items()}, 'semantic_review': {'status': 'COMPLETED', 'covered_source_ids': ['s1'], 'open_issues': []}, 'decisions': [{'id': 'keep-condition', 'decision': 'keep', 'execution': 'verified', 'basis': 'Пользователь: оставить условие.', 'source_block_ids': ['s1'], 'targets': [{'layer': 'text', 'id': 't1', 'expected': 'если спрос не падает', 'count': 1}]}]}
        self.review_path = self.package/'text-review.json'

    def check(self, previous=None):
        self.assertTrue(hasattr(HANDOFF, 'check_package'), 'Package validation not implemented')
        write(self.review_path, self.review)
        return HANDOFF.check_package(self.review_path, previous)

    def reseal_lecture(self, text):
        self.lecture['blocks'][0]['text'] = text
        self.lecture['content_hash'] = HANDOFF.digest({k: v for k, v in self.lecture.items() if k != 'content_hash'})
        self.review['artifacts']['lecture'] = write(self.package/'lecture-text.json', self.lecture)
        (self.package/'lecture-text.md').write_text(HANDOFF.to_markdown(self.lecture), encoding='utf-8')
        self.review['artifacts']['markdown'] = ref(self.package/'lecture-text.md')

    def test_valid_package_does_not_claim_semantic_proof(self):
        result = self.check()
        self.assertEqual(result['status'], 'PACKAGE_VALIDATED')
        self.assertEqual(result['semantic_review'], 'NOT_EVALUATED_BY_SCRIPT')

    def test_markdown_change_rejected_even_with_fresh_file_hash(self):
        p = self.package/'lecture-text.md'
        p.write_text(p.read_text(encoding='utf-8').replace('15%', '50%'), encoding='utf-8')
        self.review['artifacts']['markdown'] = ref(p)
        with self.assertRaises(ValueError):
            self.check()

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


class CandidateChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.text = 'About 15%, if demand holds.'
        self.pdf_path = self.root/'candidate.pdf'
        self.make_pdf(self.text)
        self.lecture = {'title': 'Example', 'blocks': [{'text_block_id': 't1', 'text': self.text}], 'structure': {'topics': [{'topic_id': 'topic'}], 'placements': [{'text_block_id': 't1', 'topic_id': 'topic'}]}}
        self.plan = {'pages': 1, 'text': [{'page': 1, 'bbox': [22*72/25.4-0.2, 48, 106*72/25.4+0.2, 64], 'text': self.text, 'block_id': 't1', 'start': 0, 'end': len(self.text), 'column': 1, 'flow_id': 'topic'}], 'visuals': [], 'links': [], 'bookmarks': []}
        self.composition = {'visuals': []}
        self.manifest = {'schema_version': '1.0', 'artifacts': {'pdf': ref(self.pdf_path), 'lecture': write(self.root/'lecture.json', self.lecture), 'render_plan': write(self.root/'plan.json', self.plan), 'composition': write(self.root/'composition.json', self.composition), 'profile': ref(ROOT/'sf-lecture-to-golden-pdf/references/adapters/a4/2.0.0.json')}, 'text_review': None, 'visual_review': None}
        self.manifest_path = self.root/'candidate-manifest.json'

    def make_pdf(self, text, extra=None, fit_link_target=None):
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont('InterTest', str(ROOT/'sf-lecture-to-golden-pdf/assets/fonts/Inter-Regular.ttf')))
        c = Canvas(str(self.pdf_path), pagesize=(210*72/25.4, 297*72/25.4))
        c.bookmarkPage('first')
        c.setFont('InterTest', 9.5)
        c.drawString(22*72/25.4, 297*72/25.4-60, text)
        if extra:
            c.drawString(50, 297*72/25.4-90, extra)
        if fit_link_target is not None:
            c.linkRect('', 'second' if fit_link_target == 2 else 'first',
                       (20, 297*72/25.4-40, 40, 297*72/25.4-20), thickness=0)
            c.showPage()
            c.bookmarkPage('second')
            c.showPage()
        c.save()

    def check(self):
        self.assertIsNotNone(PDF, 'PDF candidate checker not implemented')
        write(self.manifest_path, self.manifest)
        return PDF.check(self.manifest_path)

    def update_plan(self):
        self.manifest['artifacts']['render_plan'] = write(self.root/'plan.json', self.plan)

    def test_valid_pdf_keeps_visual_review_separate(self):
        result = self.check()
        self.assertEqual(result['status'], 'PDF_MECHANICS_VALIDATED')
        self.assertEqual(result['visual_review'], 'NOT_RECORDED')

    def test_pdf_tampering_rejected(self):
        self.make_pdf('About 50%.')
        with self.assertRaises(ValueError):
            self.check()

    def test_resealed_pdf_with_missing_condition_rejected(self):
        self.make_pdf('About 15%.')
        self.manifest['artifacts']['pdf'] = ref(self.pdf_path)
        with self.assertRaises(ValueError):
            self.check()

    def test_extra_duplicate_outside_plan_rejected(self):
        self.make_pdf(self.text, self.text)
        self.manifest['artifacts']['pdf'] = ref(self.pdf_path)
        with self.assertRaises(ValueError):
            self.check()

    def test_plan_missing_character_rejected(self):
        self.plan['text'][0]['end'] -= 1
        self.plan['text'][0]['text'] = self.text[:-1]
        self.update_plan()
        with self.assertRaises(ValueError):
            self.check()

    def test_unrendered_visual_rejected(self):
        from PIL import Image
        image = self.root/'visual.png'
        Image.new('RGB', (20, 10), 'blue').save(image)
        self.composition['visuals'] = [{'visual_id': 'v1', 'image': ref(image), 'source': ref(image), 'text_block_ids': ['t1'], 'caption': 'Blue example', 'role': 'Example'}]
        self.manifest['artifacts']['composition'] = write(self.root/'composition.json', self.composition)
        with self.assertRaises(ValueError):
            self.check()

    def test_nonexistent_navigation_rejected(self):
        self.plan['links'] = [{'page': 1, 'bbox': [20, 20, 40, 40], 'target_page': 1}]
        self.update_plan()
        with self.assertRaises(ValueError):
            self.check()

    def setup_fit_link(self, actual_target):
        self.make_pdf(self.text, fit_link_target=actual_target)
        self.manifest['artifacts']['pdf'] = ref(self.pdf_path)
        self.plan['pages'] = 2
        self.plan['links'] = [{'page': 1, 'bbox': [20, 20, 40, 40], 'target_page': 2}]
        self.update_plan()

    def test_local_fit_link_matches_real_destination(self):
        self.setup_fit_link(2)
        result = self.check()
        self.assertEqual(result['status'], 'PDF_MECHANICS_VALIDATED')
        self.assertEqual(result['links'], 1)

    def test_local_fit_link_to_wrong_page_is_rejected(self):
        self.setup_fit_link(1)
        with self.assertRaisesRegex(ValueError, 'Missing/wrong internal link'):
            self.check()

    def test_visual_report_for_another_pdf_rejected(self):
        review = {'pdf_sha256': '0'*64, 'status': 'COMPLETED', 'pages': [{'page': 1, 'status': 'PASS'}]}
        self.manifest['visual_review'] = write(self.root/'visual-review.json', review)
        with self.assertRaises(ValueError):
            self.check()


if __name__ == '__main__':
    unittest.main()
