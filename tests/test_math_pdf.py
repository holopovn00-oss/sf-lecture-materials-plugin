"""Real TeX/PDF regressions; set SF_TECTONIC and SF_TECTONIC_CACHE for release checks."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from test_json_content import ROOT, H, fixture, ref
from lecture_content import formula_items
from latex_math import compile_math, validate_asset, placed_matches
from pdf_flow import build_units, draw_body, geometry, insert_math, paginate, valid_fragments, FONT_NAME

PDF_ROOT = ROOT / "skills/sf-lecture-to-golden-pdf"
spec = importlib.util.spec_from_file_location("math_candidate", PDF_ROOT / "scripts/verify_candidate.py")
PDF = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PDF)
TECTONIC = os.environ.get("SF_TECTONIC") or shutil.which("tectonic")
CACHE = os.environ.get("SF_TECTONIC_CACHE")


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return ref(path)


def make_candidate(root, lecture, assets, *, right_offset=0, gap_extra=0, wrong_assets=None, service_heading=False, continuous=False, indent_extra=0):
    from reportlab.pdfgen.canvas import Canvas
    profile = json.loads((PDF_ROOT / "references/adapters/a4/2.1.0.json").read_text(encoding="utf-8"))
    style = geometry(profile, PDF_ROOT)
    units = build_units(lecture, None if continuous else "topic", assets, profile, PDF_ROOT)
    top = 165.0
    pages = paginate(units, style["bottom"] - top, style)
    plan = {"pages": len(pages), "text": [], "math": [], "flow": [], "visuals": [], "links": [], "bookmarks": []}
    base, pdf_path = root / "base.pdf", root / "candidate.pdf"
    canvas = Canvas(str(base), pagesize=tuple(v * 72 / 25.4 for v in profile["surface"]))
    for number, page in enumerate(pages, 1):
        canvas.setFillColorRGB(246/255, 246/255, 246/255)
        canvas.rect(0, 0, 210*72/25.4, style["height"], fill=1, stroke=0)
        if service_heading and number == 1:
            from reportlab.pdfbase import pdfmetrics
            for index, value in enumerate(("Многострочный", "заголовок")):
                heading_top, size, x = 40 + index*29, 26, style["x"][0]
                ascent = pdfmetrics.getAscent(FONT_NAME, size)
                descent = -pdfmetrics.getDescent(FONT_NAME, size)
                width = pdfmetrics.stringWidth(value, FONT_NAME, size)
                canvas.setFillColorRGB(.2,.2,.2)
                canvas.setFont(FONT_NAME, size)
                canvas.drawString(x, style["height"]-heading_top-ascent, value)
                plan["text"].append({"page":number,"text":value,"bbox":[x-.05,heading_top-.15,x+width+.05,heading_top+ascent+descent+.15]})
        for col in range(2):
            layout = {**page, "columns": [page["columns"][0] if col == 0 else [], page["columns"][1] if col == 1 else []]}
            changed_style = {**style, "gap": style["gap"] + gap_extra, "indent": style["indent"] + indent_extra}
            rows = draw_body(canvas, layout, number, top + (right_offset if col else 0), changed_style, assets)
            for name in ("text", "math", "flow"):
                plan[name].extend(rows[name])
        canvas.showPage()
    canvas.save()
    actual_math = copy.deepcopy(plan["math"])
    for row in actual_math:
        if wrong_assets and row["formula_id"] in wrong_assets:
            row["receipt"] = wrong_assets[row["formula_id"]]
    insert_math(base, pdf_path, actual_math)
    refs = {"pdf": ref(pdf_path), "profile": ref(PDF_ROOT / "references/adapters/a4/2.1.0.json")}
    for name, value in (("lecture", lecture), ("composition", {"visuals": []}), ("render_plan", plan)):
        refs[name] = write(root / (name + ".json"), value)
    manifest = {"schema_version": "2.0", "artifacts": refs, "text_review": None, "visual_review": None}
    path = root / "manifest.json"
    write(path, manifest)
    return path, manifest, plan


class ParagraphBoundaryChecks(unittest.TestCase):
    def test_one_line_continuation_fragment_is_not_allowed(self):
        units = [{"block_id":"b", "content_index":0, "kind":"line", "line_index":4, "paragraph_lines":8}]
        self.assertFalse(valid_fragments(units, {"short_lines":3,"split_lines":2}))

    def test_two_line_continuation_fragment_is_allowed(self):
        units = [{"block_id":"b", "content_index":0, "kind":"line", "line_index":i, "paragraph_lines":8} for i in (4,5)]
        self.assertTrue(valid_fragments(units, {"short_lines":3,"split_lines":2}))


@unittest.skipUnless(TECTONIC, "Tectonic is required for the LaTeX/PDF integration gate")
class MathPdfChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache_tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.cache_tmp.cleanup)
        cls.cache_root = Path(cls.cache_tmp.name)
        sources, draft = fixture(cls.cache_root / "source.json")
        cls.lecture, _ = H.build(sources, draft)
        cls.assets = {}
        for block in cls.lecture["blocks"]:
            for _, ri, formula in formula_items(block):
                target = cls.cache_root / formula["formula_id"]
                compile_math(formula["latex"], "display" if ri is None else "inline", 9.5, target,
                             tectonic=TECTONIC, cache_dir=CACHE)
                cls.assets[formula["formula_id"]] = ref(target / "math.json")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_real_paragraphs_inline_and_display_math(self):
        path, _, _ = make_candidate(self.root, self.lecture, self.assets)
        result = PDF.check(path)
        self.assertEqual(result["status"], "PDF_MECHANICS_VALIDATED")
        self.assertEqual(result["formulas"], 3)
        self.assertEqual(result["paragraphs"], 3)
        self.assertEqual(result["math_placement"], "ACTUAL_GLYPHS_AND_STROKES_VALIDATED")
        self.assertEqual(result["semantic_review"], "NOT_EVALUATED_BY_SCRIPT")

    def test_missing_formula_is_rejected(self):
        path, manifest, plan = make_candidate(self.root, self.lecture, self.assets)
        plan["math"].pop(0)
        manifest["artifacts"]["render_plan"] = write(self.root / "render_plan.json", plan)
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, "formula occurrence"):
            PDF.check(path)

    def test_wrong_first_line_indent_is_rejected(self):
        path, _, _ = make_candidate(self.root, self.lecture, self.assets, indent_extra=-2)
        with self.assertRaisesRegex(ValueError, "indent"):
            PDF.check(path)

    def test_continuous_topics_with_actual_headings(self):
        lecture = copy.deepcopy(self.lecture)
        block = copy.deepcopy(lecture["blocks"][0])
        block["text_block_id"] = "b2"
        block["content"] = [block["content"][0]]
        lecture["blocks"].append(block)
        lecture["source_locators"]["b2"] = copy.deepcopy(lecture["source_locators"]["b1"])
        lecture["structure"]["topics"].append({"topic_id":"next", "section_id":"sec", "title":"Следующая подтема"})
        lecture["structure"]["placements"].append({**lecture["structure"]["placements"][0], "text_block_id":"b2", "topic_id":"next"})
        lecture["content_hash"] = H.digest({k:v for k,v in lecture.items() if k != "content_hash"})
        path, _, _ = make_candidate(self.root, lecture, self.assets, continuous=True)
        result = PDF.check(path)
        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["column_pairs"][0]["topic_ids"], ["topic", "next"])

    def test_multiline_service_heading_keeps_separate_lines(self):
        path, _, _ = make_candidate(self.root, self.lecture, self.assets, service_heading=True)
        self.assertEqual(PDF.check(path)["status"], "PDF_MECHANICS_VALIDATED")

    def test_inline_formula_followed_by_comma(self):
        lecture = copy.deepcopy(self.lecture)
        lecture["blocks"][0]["content"][2]["runs"][1]["text"] = ", номинальная годовая ставка; "
        lecture["content_hash"] = H.digest({k:v for k,v in lecture.items() if k != "content_hash"})
        path, _, _ = make_candidate(self.root, lecture, self.assets)
        self.assertEqual(PDF.check(path)["status"], "PDF_MECHANICS_VALIDATED")

    def test_changed_latex_with_old_asset_is_rejected(self):
        lecture = copy.deepcopy(self.lecture)
        path, manifest, _ = make_candidate(self.root, lecture, self.assets)
        lecture["blocks"][0]["content"][1]["latex"] += "+1"
        lecture["content_hash"] = H.digest({k: v for k, v in lecture.items() if k != "content_hash"})
        manifest["artifacts"]["lecture"] = write(self.root / "lecture.json", lecture)
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, "receipt differs"):
            PDF.check(path)

    def test_wrong_actual_formula_is_rejected(self):
        path, _, _ = make_candidate(self.root, self.lecture, self.assets, wrong_assets={"ear": self.assets["nominal"]})
        with self.assertRaisesRegex(ValueError, "math glyph|math strokes"):
            PDF.check(path)

    def test_missing_fraction_rule_is_rejected(self):
        import fitz
        path, manifest, plan = make_candidate(self.root, self.lecture, self.assets)
        row = plan["math"][0]
        _, asset_path = validate_asset(self.lecture["blocks"][0]["content"][1]["latex"], "display", 9.5, row["receipt"])
        with fitz.open(asset_path) as asset:
            rules = [d["rect"] for d in asset[0].get_drawings() if d["rect"].width > 2 and d["rect"].height < 1]
        self.assertTrue(rules, "The real TeX fraction must contain a vector rule")
        box = fitz.Rect(rules[0])
        box = fitz.Rect(box.x0 + row["bbox"][0] - .2, box.y0 + row["bbox"][1] - .3,
                        box.x1 + row["bbox"][0] + .2, box.y1 + row["bbox"][1] + .3)
        broken = self.root / "without-rule.pdf"
        with fitz.open(self.root / "candidate.pdf") as doc:
            doc[row["page"] - 1].draw_rect(box, fill=(1, 1, 1), color=None, overlay=True)
            doc.save(broken)
        manifest["artifacts"]["pdf"] = ref(broken)
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, "math strokes"):
            PDF.check(path)

    def test_lowered_right_column_is_rejected(self):
        path, _, plan = make_candidate(self.root, self.lecture, self.assets, right_offset=12)
        self.assertTrue(any(r["column"] == 2 for r in plan["flow"]))
        with self.assertRaisesRegex(ValueError, "column top"):
            PDF.check(path)

    def test_false_paragraph_spacing_is_rejected(self):
        path, _, _ = make_candidate(self.root, self.lecture, self.assets, gap_extra=3)
        with self.assertRaisesRegex(ValueError, "Paragraph gap"):
            PDF.check(path)

    def test_paragraphs_and_math_continue_across_pages(self):
        lecture = copy.deepcopy(self.lecture)
        for i in range(14):
            lecture["blocks"][0]["content"].append({"type": "paragraph", "runs": [{"type": "text", "text":
                ("Long control paragraph preserves its complete text and its precise source order. " * 8) + str(i)}]})
        lecture["content_hash"] = H.digest({k: v for k, v in lecture.items() if k != "content_hash"})
        path, _, plan = make_candidate(self.root, lecture, self.assets)
        result = PDF.check(path)
        self.assertGreater(plan["pages"], 1)
        self.assertEqual(result["column_balance"], "VALIDATED")

    def test_tex_syntax_error_has_no_success_receipt(self):
        target = self.root / "syntax-error"
        with self.assertRaisesRegex(ValueError, "compilation failed"):
            compile_math(r"\frac{1}", "display", 9.5, target, tectonic=TECTONIC, cache_dir=CACHE)
        self.assertFalse((target / "math.json").exists())

    def test_math_vocabulary_is_really_typeset(self):
        expressions = [r"\sqrt{x_1^2+\alpha^2}", r"\sum_{i=1}^{n}i=\frac{n(n+1)}{2}",
                       r"\begin{aligned}A&=B+C\\&=D\end{aligned}", r"\int_0^1 x^2\,dx=\frac{1}{3}"]
        for i, latex in enumerate(expressions):
            with self.subTest(latex=latex):
                target = self.root / f"vocabulary-{i}"
                receipt = compile_math(latex, "display", 9.5, target, tectonic=TECTONIC, cache_dir=CACHE)
                self.assertEqual(receipt["syntax"], "COMPILED")
                validate_asset(latex, "display", 9.5, ref(target / "math.json"))


if __name__ == "__main__":
    unittest.main()
