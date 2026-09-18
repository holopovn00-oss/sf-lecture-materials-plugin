"""Regression checks for the addressable multi-zone Golden Gate checker."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import fitz
from reportlab.pdfgen.canvas import Canvas

from test_json_content import ROOT

PDF_ROOT = ROOT / "skills/sf-lecture-to-golden-pdf"
spec = importlib.util.spec_from_file_location("golden_zones", PDF_ROOT / "scripts/verify_golden_zones.py")
ZONES = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ZONES)
MM_TO_PT = 72 / 25.4


def ref(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper()}


class GoldenZoneChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = PDF_ROOT / "references/adapters/a4/2.1.0.json"
        self.candidate, self.zone_plan = self.make_candidate()

    def make_candidate(self):
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        mm = profile["layout_profile"]["mm"]
        width, height = [value * MM_TO_PT for value in profile["surface"]]
        x0 = mm["content_x"] * MM_TO_PT
        right = mm["content_right"] * MM_TO_PT
        column_width = mm["column_width"] * MM_TO_PT
        gap = mm["column_gap"] * MM_TO_PT
        pdf = self.root / "candidate.pdf"
        canvas = Canvas(str(pdf), pagesize=(width, height))
        canvas.setFont("Helvetica", 11)
        title_y_top = 24 * MM_TO_PT
        canvas.drawString(x0, height - title_y_top - 11, "Topic title")
        rule_y_top = 29 * MM_TO_PT
        rule_left = 58 * MM_TO_PT
        pill_left = 166 * MM_TO_PT
        canvas.setLineWidth(0.75)
        canvas.line(rule_left, height - rule_y_top, pill_left, height - rule_y_top)
        pill_top = (rule_y_top - 2.5 * MM_TO_PT)
        pill_height = 5 * MM_TO_PT
        canvas.roundRect(pill_left, height - pill_top - pill_height, right - pill_left, pill_height,
                         2.5 * MM_TO_PT, stroke=1, fill=0)
        canvas.setFont("Helvetica", 9.5)
        left_y_top, right_y_top = 52 * MM_TO_PT, 66 * MM_TO_PT
        canvas.drawString(x0, height - left_y_top - 9.5, "Left body line")
        canvas.drawString(x0 + column_width + gap, height - right_y_top - 9.5, "Right body line")
        canvas.save()

        with fitz.open(pdf) as document:
            page = document[0]
            def bbox(value):
                matches = page.search_for(value)
                self.assertEqual(len(matches), 1, value)
                box = matches[0]
                return [box.x0, box.y0, box.x1, box.y1]
            title_box = bbox("Topic title")
            left_box = bbox("Left body line")
            right_box = bbox("Right body line")

        render_plan = {
            "pages": 1,
            "text": [
                {"line_id": "l-1", "page": 1, "text": "Left body line", "bbox": left_box},
                {"line_id": "l-2", "page": 1, "text": "Right body line", "bbox": right_box},
            ],
            "flow": [
                {"line_id": "l-1", "page": 1, "column": 1, "flow_id": "topic-1"},
                {"line_id": "l-2", "page": 1, "column": 2, "flow_id": "topic-1"},
            ],
        }
        render_path = self.root / "render-plan.json"
        render_path.write_text(json.dumps(render_plan), encoding="utf-8")
        artifacts = {"pdf": ref(pdf), "profile": ref(self.profile), "render_plan": ref(render_path)}
        candidate = self.root / "candidate-manifest.json"
        candidate.write_text(json.dumps({"schema_version": "2.1", "artifacts": artifacts}), encoding="utf-8")

        top = 43 * MM_TO_PT
        bottom = 120 * MM_TO_PT
        zone = {
            "schema_version": "1.0",
            "artifacts": artifacts,
            "zones": [{
                "zone_id": "zone-1",
                "page": 1,
                "topic_id": "topic-1",
                "divider": {
                    "title_text": "Topic title",
                    "title_bbox": title_box,
                    "rule_bbox": [rule_left, rule_y_top - 0.4, pill_left, rule_y_top + 0.4],
                    "time_pill_bbox": [pill_left, pill_top, right, pill_top + pill_height],
                },
                "columns": [
                    {"column": 1, "bbox": [x0, top, x0 + column_width, bottom], "line_ids": ["l-1"]},
                    {"column": 2, "bbox": [x0 + column_width + gap, top, right, bottom], "line_ids": ["l-2"]},
                ],
                "balance": {"status": "BEST_LEGAL_SPLIT", "reason": "Both columns contain the legal split."},
            }],
        }
        zone_path = self.root / "golden-zone-plan.json"
        zone_path.write_text(json.dumps(zone), encoding="utf-8")
        return candidate, zone_path

    def test_valid_multizone_plan_checks_actual_pdf(self):
        result = ZONES.check(self.candidate, self.zone_plan)
        self.assertEqual(result["status"], "GOLDEN_ZONE_MECHANICS_VALIDATED")
        self.assertEqual(result["covered_body_lines"], 2)

    def test_shifted_column_is_rejected(self):
        value = json.loads(self.zone_plan.read_text(encoding="utf-8"))
        value["zones"][0]["columns"][1]["bbox"][0] += 4
        self.zone_plan.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Column 2 x coordinate"):
            ZONES.check(self.candidate, self.zone_plan)

    def test_unlisted_topic_line_is_rejected(self):
        value = json.loads(self.zone_plan.read_text(encoding="utf-8"))
        value["zones"][0]["columns"][1]["line_ids"] = []
        self.zone_plan.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid line IDs"):
            ZONES.check(self.candidate, self.zone_plan)


if __name__ == "__main__":
    unittest.main()
