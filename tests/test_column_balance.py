"""Current-format real PDF checks, including navigation and protected text."""
import json
import tempfile
import unittest
from pathlib import Path

from test_math_pdf import H, PDF, fixture, make_candidate, ref, write


class ColumnBalanceChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        sources, draft = fixture(self.root / "source.json")
        self.text = "Рабочий документ содержит последовательность действий и объяснение результата. " * 12
        draft["blocks"][0]["content"] = [
            {"type": "paragraph", "runs": [{"type": "text", "text": self.text}]},
            {"type": "paragraph", "runs": [{"type": "text", "text": "Условия и исходные ограничения сохраняются при проверке результата. " * 5}]},
        ]
        self.lecture, _ = H.build(sources, draft)

    def candidate(self, **kwargs):
        self.path, self.manifest, self.plan = make_candidate(self.root, self.lecture, {}, **kwargs)
        return self.path

    def reseal_plan(self):
        self.manifest["artifacts"]["render_plan"] = write(self.root / "render_plan.json", self.plan)
        write(self.path, self.manifest)

    def check(self):
        write(self.path, self.manifest)
        return PDF.check(self.path)

    def test_balanced_current_pdf_preserves_text_and_review_boundary(self):
        result = PDF.check(self.candidate())
        self.assertEqual(result["status"], "PDF_MECHANICS_VALIDATED")
        self.assertEqual(result["column_balance"], "VALIDATED")
        self.assertEqual(result["visual_review"], "NOT_RECORDED")
        self.assertEqual(result["semantic_review"], "NOT_EVALUATED_BY_SCRIPT")
        rows = [r for r in self.plan["text"] if r.get("content_index") == 0 and r.get("block_id") == "b1"]
        self.assertEqual("".join(r["text"] for r in rows), self.text)

    def test_legacy_manifest_rejected(self):
        self.candidate()
        self.manifest["schema_version"] = "1.0"
        with self.assertRaisesRegex(ValueError, "Unsupported candidate manifest"):
            self.check()

    def test_lowered_right_column_rejected(self):
        with self.assertRaises(ValueError):
            PDF.check(self.candidate(right_offset=13.1))

    def test_wrong_first_line_indent_rejected(self):
        with self.assertRaises(ValueError):
            PDF.check(self.candidate(indent_extra=-2))

    def test_ragged_columns_rejected(self):
        with self.assertRaises(ValueError):
            PDF.check(self.candidate(ragged=True))

    def test_stretched_spacing_rejected(self):
        with self.assertRaises(ValueError):
            PDF.check(self.candidate(gap_extra=4))

    def test_false_column_metadata_rejected(self):
        self.candidate()
        for row in self.plan["flow"]:
            row["column"] = 1
        self.reseal_plan()
        with self.assertRaises(ValueError):
            self.check()

    def test_missing_layout_metadata_rejected(self):
        self.candidate()
        del self.plan["flow"][0]["column"]
        self.reseal_plan()
        with self.assertRaises((ValueError, KeyError)):
            self.check()

    def test_missing_character_rejected(self):
        self.candidate()
        row = next(r for r in self.plan["text"] if r.get("block_id"))
        row["end"] -= 1
        row["text"] = row["text"][:-1]
        self.reseal_plan()
        with self.assertRaises(ValueError):
            self.check()

    def test_pdf_bytes_tampering_rejected(self):
        self.candidate()
        with (self.root / "candidate.pdf").open("ab") as stream:
            stream.write(b"\n% changed\n")
        with self.assertRaises(ValueError):
            self.check()

    def test_resealed_pdf_with_extra_text_rejected(self):
        import fitz
        self.candidate()
        extra = self.root / "extra.pdf"
        with fitz.open(self.root / "candidate.pdf") as doc:
            doc[0].insert_text((30, 30), "unplanned duplicate")
            doc.save(extra)
        self.manifest["artifacts"]["pdf"] = ref(extra)
        with self.assertRaises(ValueError):
            self.check()

    def test_resealed_pdf_with_missing_text_rejected(self):
        import fitz
        self.candidate()
        broken = self.root / "missing.pdf"
        row = next(r for r in self.plan["text"] if r.get("block_id"))
        with fitz.open(self.root / "candidate.pdf") as doc:
            page = doc[row["page"] - 1]
            page.add_redact_annot(fitz.Rect(row["bbox"]))
            page.apply_redactions()
            doc.save(broken)
        self.manifest["artifacts"]["pdf"] = ref(broken)
        with self.assertRaises(ValueError):
            self.check()

    def test_unrendered_visual_rejected(self):
        from PIL import Image
        self.candidate()
        image = self.root / "visual.png"
        Image.new("RGB", (20, 10), "blue").save(image)
        composition = {"visuals": [{"visual_id": "v1", "image": ref(image), "source": ref(image),
            "text_block_ids": ["b1"], "caption": "Blue example", "role": "Example"}]}
        self.manifest["artifacts"]["composition"] = write(self.root / "composition.json", composition)
        with self.assertRaises(ValueError):
            self.check()

    def test_nonexistent_navigation_rejected(self):
        self.candidate()
        self.plan["links"] = [{"page": 1, "bbox": [20, 20, 40, 40], "target_page": 1}]
        self.reseal_plan()
        with self.assertRaises(ValueError):
            self.check()

    def test_real_navigation_and_wrong_target(self):
        import fitz
        self.candidate()
        linked = self.root / "linked.pdf"
        with fitz.open(self.root / "candidate.pdf") as doc:
            doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(20, 20, 40, 40), "page": 0})
            doc.save(linked)
        self.manifest["artifacts"]["pdf"] = ref(linked)
        self.plan["links"] = [{"page": 1, "bbox": [20, 20, 40, 40], "target_page": 1}]
        self.reseal_plan()
        self.assertEqual(self.check()["links"], 1)
        self.plan["links"][0]["target_page"] = 999
        self.reseal_plan()
        with self.assertRaises(ValueError):
            self.check()

    def test_direct_fit_destination_uses_real_page_object(self):
        import fitz
        from reportlab.pdfgen.canvas import Canvas
        path = self.root / "fit.pdf"
        canvas = Canvas(str(path))
        canvas.bookmarkPage("first")
        canvas.linkRect("", "second", (20, 20, 40, 40), thickness=0)
        canvas.showPage()
        canvas.bookmarkPage("second")
        canvas.showPage()
        canvas.save()
        with fitz.open(path) as doc:
            link = doc[0].get_links()[0]
            self.assertEqual(PDF.internal_link_target(doc, link), 1)
            doc.xref_set_key(link["xref"], "Dest", f"[{doc.page_xref(0)} 0 R /Fit]")
            self.assertNotEqual(PDF.internal_link_target(doc, link), 1)

    def test_visual_review_bound_to_exact_pdf(self):
        self.candidate()
        self.manifest["visual_review"] = write(self.root / "visual-review.json",
            {"pdf_sha256": "0" * 64, "pages": []})
        with self.assertRaises(ValueError):
            self.check()


if __name__ == "__main__":
    unittest.main()
