"""Regressions for the user-approved print typography and continuous topic flow."""
import copy
import json
from pathlib import Path
import unittest

from test_json_content import ROOT, fixture, H
from pdf_flow import build_units, geometry, paginate, boundary_gap
from text_typography import normalize_prose, cover_title

PDF_ROOT = ROOT / "skills/sf-lecture-to-golden-pdf"


class PrintLayoutTests(unittest.TestCase):
    def test_cover_requires_confirmed_ordinal(self):
        self.assertEqual(cover_title("1. Концепция", confirmed_ordinal=1), "Концепция")
        self.assertEqual(cover_title("90 минут"), "90 минут")
        self.assertEqual(cover_title("3 способа оценки"), "3 способа оценки")
        with self.assertRaises(ValueError):
            cover_title("3 способа оценки", confirmed_ordinal=3)

    def test_normalization_with_exact_exception_and_audit(self):
        source = "Ёмкость — расчет; всё."
        start = source.index("всё")
        result, changes = normalize_prose(source, [(start, start+3)])
        self.assertEqual(result, "Емкость – расчет; всё.")
        self.assertEqual(len(changes), 2)
        self.assertEqual(source, "Ёмкость — расчет; всё.")
        with self.assertRaises(ValueError):
            normalize_prose(source, [(1,5), (4,8)])

    def test_indent_and_continuous_topic_geometry(self):
        profile = json.loads((PDF_ROOT / "references/adapters/a4/2.1.0.json").read_text(encoding="utf-8"))
        style = geometry(profile, PDF_ROOT)
        self.assertAlmostEqual(style["indent"], 5*72/25.4)
        self.assertEqual(style["gap"], 0)
        sources, draft = fixture(Path("source.json"))
        draft["blocks"][0]["content"] = [{"type":"paragraph", "runs":[{"type":"text", "text":"Полный учебный текст. "*20}]}]
        second = copy.deepcopy(draft["blocks"][0])
        second["text_block_id"] = "b2"
        draft["blocks"].append(second)
        draft["structure"]["topics"].append({"topic_id":"next", "section_id":"sec", "title":"Следующая подтема"})
        draft["structure"]["placements"].append({"text_block_id":"b2", "topic_id":"next", "section_id":"sec"})
        units = build_units(draft, None, {}, profile, PDF_ROOT)
        pages = paginate(units, style["bottom"] - 100, style)
        self.assertEqual(len(pages), 1)
        self.assertEqual({u["flow_id"] for u in units}, {"topic", "next"})
        self.assertEqual(sum("heading" in u for u in units), 2)
        for unit in units:
            self.assertLessEqual(unit["width"], style["width"] - (style["indent"] if unit["line_index"] == 0 else 0) + .001)
        self.assertEqual(boundary_gap(units[0], units[-1], 0), 0)

    def test_display_gap_is_not_paragraph_gap(self):
        a = {"block_id":"a", "content_index":0}
        b = {"block_id":"a", "content_index":1,"math_gap":4*72/25.4}
        self.assertAlmostEqual(boundary_gap(a,b,0), 4*72/25.4)


if __name__ == "__main__":
    unittest.main()
