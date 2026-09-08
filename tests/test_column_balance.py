"""Real PDF regressions: unequal columns, spacing tricks and false plan labels."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = (Path(__file__).resolve().parents[1] / 'plugins/sf-lecture-materials/skills')
spec = importlib.util.spec_from_file_location('balance_candidate', ROOT/'sf-lecture-to-golden-pdf/scripts/verify_candidate.py')
PDF = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PDF)


def ref(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest().upper()}


class ColumnBalanceChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def candidate(self, left=3, right=3, right_leading=13.1, right_offset=0, split_blocks=False, false_column=False, metadata=True):
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont('BalanceTest', str(ROOT/'sf-lecture-to-golden-pdf/assets/fonts/Inter-Regular.ttf')))
        M = 72/25.4
        pdf_path = self.root/'candidate.pdf'
        c = Canvas(str(pdf_path), pagesize=(210*M, 297*M))
        c.setFont('BalanceTest', 9.5)
        chunks = [f'Sentence number {i+1}. ' for i in range(left+right)]
        blocks = ([{'text_block_id': 'b1', 'text': ''.join(chunks[:left])}, {'text_block_id': 'b2', 'text': ''.join(chunks[left:])}]
                  if split_blocks else [{'text_block_id': 'b1', 'text': ''.join(chunks)}])
        lecture = {'title': 'Example', 'blocks': blocks, 'structure': {'topics': [{'topic_id': 'topic'}], 'placements': [{'text_block_id': b['text_block_id'], 'topic_id': 'topic'} for b in blocks]}}
        plan = {'pages': 1, 'text': [], 'visuals': [], 'links': [], 'bookmarks': []}
        cursors = {b['text_block_id']: 0 for b in blocks}
        for i, chunk in enumerate(chunks):
            col = 1 if i < left else 2
            row_index = i if col == 1 else i-left
            x = (22 if col == 1 else 112)*M
            baseline = 100 + row_index*(13.1 if col == 1 else right_leading) + (right_offset if col == 2 else 0)
            c.drawString(x, 297*M-baseline, chunk.strip())
            identifier = 'b2' if split_blocks and col == 2 else 'b1'
            start = cursors[identifier]
            row = {'page': 1, 'bbox': [x-0.2, baseline-10, x+84*M+0.2, baseline+3], 'text': chunk, 'block_id': identifier, 'start': start, 'end': start+len(chunk)}
            if metadata:
                row.update(column=1 if false_column else col, flow_id='topic')
            plan['text'].append(row)
            cursors[identifier] += len(chunk)
        c.save()
        refs = {'pdf': ref(pdf_path), 'profile': ref(ROOT/'sf-lecture-to-golden-pdf/references/adapters/a4/2.0.0.json')}
        for key, data in [('lecture', lecture), ('render_plan', plan), ('composition', {'visuals': []})]:
            path = self.root/(key+'.json')
            path.write_text(json.dumps(data), encoding='utf-8')
            refs[key] = ref(path)
        path = self.root/'manifest.json'
        path.write_text(json.dumps({'schema_version': '1.0', 'artifacts': refs}), encoding='utf-8')
        return path

    def test_balanced_columns_report_actual_lines(self):
        result = PDF.check(self.candidate())
        self.assertIn('column_balance', result, 'Actual column balance is not checked')
        self.assertEqual(result['column_balance'], 'VALIDATED')
        self.assertEqual(result['column_pairs'][0]['line_counts'], [3, 3])

    def test_odd_line_count_keeps_extra_line_on_left(self):
        result = PDF.check(self.candidate(left=3, right=2))
        self.assertIn('column_pairs', result, 'Actual column balance is not checked')
        self.assertEqual(result['column_pairs'][0]['line_counts'], [3, 2])

    def test_two_source_blocks_share_one_balanced_topic(self):
        result = PDF.check(self.candidate(split_blocks=True))
        self.assertIn('column_pairs', result, 'Actual column balance is not checked')
        self.assertEqual(len(result['column_pairs']), 1)
        self.assertEqual(result['column_pairs'][0]['line_counts'], [3, 3])

    def test_unequal_columns_rejected_despite_complete_text(self):
        with self.assertRaisesRegex(ValueError, 'Unbalanced columns'):
            PDF.check(self.candidate(left=4, right=2))

    def test_empty_right_column_rejected_for_multiline_text(self):
        with self.assertRaisesRegex(ValueError, 'Unbalanced columns'):
            PDF.check(self.candidate(left=6, right=0))

    def test_stretched_line_spacing_cannot_fake_balance(self):
        with self.assertRaisesRegex(ValueError, 'Body line spacing'):
            PDF.check(self.candidate(right_leading=16.1))

    def test_lowered_right_column_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Column tops'):
            PDF.check(self.candidate(right_offset=13.1))

    def test_column_labels_must_match_actual_pdf_positions(self):
        with self.assertRaisesRegex(ValueError, 'Column label'):
            PDF.check(self.candidate(false_column=True))

    def test_missing_layout_metadata_cannot_skip_check(self):
        with self.assertRaisesRegex(ValueError, 'Missing body layout metadata'):
            PDF.check(self.candidate(metadata=False))


if __name__ == '__main__':
    unittest.main()
