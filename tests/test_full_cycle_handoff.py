"""Full-cycle fact-check gate tests without rendering a PDF."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from test_json_content import H, fixture

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/sf-lecture-materials/scripts/verify_full_cycle_handoff.py"
FACT_SCRIPT = ROOT / "plugins/sf-lecture-materials/skills/sf-fact-check/scripts/verify_fact_check.py"
PDF_SCRIPT = ROOT / "plugins/sf-lecture-materials/skills/sf-lecture-to-golden-pdf/scripts/verify_candidate.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load("full_cycle_gate", SCRIPT)
FACT = load("full_cycle_fact", FACT_SCRIPT)
PDF = load("full_cycle_pdf", PDF_SCRIPT)


def ref(path, kind):
    return {
        "kind": kind,
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper(),
    }


def anchor(text, quote, unit="b1", sources=None):
    start = text.index(quote)
    return {
        "unit_id": unit,
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "source_block_ids": sources or ["s1"],
    }


class FullCycleHandoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        raw = self.root / "source.json"
        sources, draft = fixture(raw)
        raw.write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
        self.checked, _ = H.build(sources, draft)
        self.checked_path = self.root / "lecture-before.json"
        self.checked_path.write_text(json.dumps(self.checked, ensure_ascii=False), encoding="utf-8")
        self.original = "Эффективная годовая ставка учитывает капитализацию процентов."
        self.proposal = "Эффективная годовая ставка учитывает капитализацию при периодическом начислении процентов."
        self.selected = copy.deepcopy(self.checked)
        self.selected["blocks"][0]["content"][0]["runs"][0]["text"] = self.proposal
        self.selected["content_hash"] = H.digest({k: v for k, v in self.selected.items() if k != "content_hash"})
        self.selected_path = self.root / "lecture-selected.json"
        self.selected_path.write_text(json.dumps(self.selected, ensure_ascii=False), encoding="utf-8")
        self.other_path = self.root / "lecture-other.json"
        self.other_path.write_text(json.dumps(self.selected, ensure_ascii=False), encoding="utf-8")

    def report(self, *, coverage="complete", decision="correct", execution="applied", selected=True):
        checked_text = FACT.block_text(self.checked["blocks"][0])
        proposal = {"kind": "qualification", "text": self.proposal}
        review = {"decision": decision, "comment": "", "basis": "", "execution": execution, "application": None}
        if decision != "pending":
            review["comment"] = "TEST USER: решение по FC-001"
            review["basis"] = "Synthetic test message"
        if execution == "applied":
            target = self.selected_path if selected else self.other_path
            selected_text = FACT.block_text(self.selected["blocks"][0])
            review["application"] = {
                "artifact": ref(target, "lecture_json"),
                "anchor": anchor(selected_text, self.proposal),
            }
        return {
            "schema_version": "1.0.0",
            "title": "SYNTHETIC FULL-CYCLE FACT CHECK",
            "input": ref(self.checked_path, "lecture_json"),
            "originals": [],
            "scope": {
                "description": "Synthetic complete cycle",
                "as_of": "2026-09-18",
                "coverage": coverage,
                "reviewed_units": ["b1"],
                "exclusions": [],
                "gaps": [] if coverage == "complete" else ["One stated limit"],
            },
            "evidence": [],
            "claims": [{
                "id": "FC-001",
                "kind": "definition",
                "anchor": anchor(checked_text, self.original),
                "context": "Synthetic qualification for workflow validation",
                "assessment": {
                    "status": "context_dependent",
                    "rationale": "The condition is required for the teaching statement.",
                    "evidence_ids": [],
                    "proposal": proposal,
                },
                "review": review,
            }],
            "assessment_change_reason": "",
        }

    def handoff(self, report_path, *, resolution="complete", comment="", basis=""):
        data = {
            "schema_version": "1.0.0",
            "mode": "full_cycle",
            "checked_lecture": ref(self.checked_path, "lecture_json"),
            "fact_check": ref(report_path, "fact_check"),
            "selected_lecture": ref(self.selected_path, "lecture_json"),
            "coverage_resolution": {"status": resolution, "comment": comment, "basis": basis},
        }
        path = self.root / "full-cycle-handoff.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def write_report(self, report):
        path = self.root / "fact-check.json"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return path

    def test_applied_correction_validates_and_binds_selected_lecture(self):
        path = self.handoff(self.write_report(self.report()))
        receipt = GATE.check(path)
        self.assertEqual(receipt["status"], "FULL_CYCLE_FACT_CHECK_GATE_VALIDATED")
        self.assertEqual(receipt["applied_decisions"], ["FC-001"])
        self.assertEqual(receipt["selected_lecture_sha256"], ref(self.selected_path, "lecture_json")["sha256"])

    def test_pending_actionable_decision_blocks(self):
        report = self.report(decision="pending", execution="pending")
        with self.assertRaisesRegex(ValueError, "pending"):
            GATE.check(self.handoff(self.write_report(report)))

    def test_keep_is_retained_without_changing_assessment(self):
        report = self.report(decision="keep", execution="kept")
        receipt = GATE.check(self.handoff(self.write_report(report)))
        self.assertEqual(receipt["retained_findings"], ["FC-001"])

    def test_partial_coverage_requires_explicit_limit_acceptance(self):
        report_path = self.write_report(self.report(coverage="partial"))
        with self.assertRaisesRegex(ValueError, "limit acceptance"):
            GATE.check(self.handoff(report_path))
        path = self.handoff(report_path, resolution="accepted_limit",
                            comment="TEST USER: принимаю неполный охват", basis="Synthetic test message")
        self.assertEqual(GATE.check(path)["coverage_resolution"], "accepted_limit")

    def test_applied_correction_cannot_target_another_lecture(self):
        report = self.report(selected=False)
        with self.assertRaisesRegex(ValueError, "another selected lecture"):
            GATE.check(self.handoff(self.write_report(report)))

    def test_candidate_workflow_keeps_direct_and_full_cycle_distinct(self):
        direct = {"workflow": {"mode": "direct_skill", "full_cycle_handoff": None}}
        self.assertEqual(PDF.check_workflow(direct, {}), "DIRECT_SKILL_NO_FACT_CHECK_GATE")
        report_path = self.write_report(self.report())
        handoff_path = self.handoff(report_path)
        full = {
            "workflow": {"mode": "full_cycle", "full_cycle_handoff": ref(handoff_path, "full_cycle_handoff")}
        }
        self.assertEqual(
            PDF.check_workflow(full, {"lecture": ref(self.selected_path, "lecture_json")}),
            "FULL_CYCLE_FACT_CHECK_GATE_VALIDATED",
        )
        with self.assertRaisesRegex(ValueError, "workflow"):
            PDF.check_workflow({}, {})


if __name__ == "__main__":
    unittest.main()
