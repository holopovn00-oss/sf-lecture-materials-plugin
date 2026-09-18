"""Behavioral integrity checks, not simulated proof of scientific correctness."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/sf-lecture-materials/skills/sf-fact-check/scripts/verify_fact_check.py"
spec = importlib.util.spec_from_file_location("fact_check", SCRIPT)
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


def ref(path, kind="text"):
    return {"kind": kind, "path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper()}


def anchor(text, quote, unit="document", sources=None):
    start = text.index(quote)
    return {"unit_id": unit, "start": start, "end": start + len(quote), "quote": quote,
            "source_block_ids": sources or []}


def fixture(path):
    text = path.read_bytes().decode("utf-8-sig")
    return {"schema_version": "1.0.0", "title": "SYNTHETIC STRUCTURAL TEST",
            "input": ref(path), "originals": [],
            "scope": {"description": "Single test claim", "as_of": "2026-09-12", "coverage": "complete",
                      "reviewed_units": ["document"], "exclusions": [], "gaps": []},
            "evidence": [{"id": "E1", "kind": "calculation", "expression": "2+1", "result": "3",
                          "method": "Exact addition", "applicability": "Integers", "limitations": "Arithmetic only"}],
            "claims": [{"id": "FC-001", "kind": "calculation", "anchor": anchor(text, "2+1=5"),
                        "context": "Asserted integer equality",
                        "assessment": {"status": "error", "rationale": "The sum is 3, not 5.",
                                       "evidence_ids": ["E1"], "proposal": {"kind": "correction", "text": "2+1=3"}},
                        "review": {"decision": "pending", "comment": "", "basis": "", "execution": "pending",
                                   "application": None}}],
            "assessment_change_reason": ""}


def external():
    return {"id": "E2", "kind": "academic", "title": "TEST-ONLY invented metadata",
            "creator": "Synthetic fixture", "publication": "Not a real publication", "published": "undated",
            "version": "test", "url": "https://example.org/test-only", "doi": None, "locator": "Fixture paragraph",
            "accessed": "2026-09-12", "read_scope": "relevant_passage", "passage": "Synthetic test evidence",
            "passage_type": "paraphrase", "applicability": "Structural test only",
            "limitations": "Cannot support a real-world conclusion; source authenticity is not checked"}


class FactCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input = self.root / "source.txt"
        self.input.write_bytes("Контекст 😀\r\n2+1=5".encode("utf-8"))
        self.report = fixture(self.input)

    def test_exact_math_and_read_only_cli(self):
        report_path = self.root / "report.json"
        report_path.write_text(json.dumps(self.report, ensure_ascii=False), encoding="utf-8")
        before = {p: p.read_bytes() for p in (self.input, report_path)}
        result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "--report", str(report_path)],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["recalculated"], {"E1": "3"})
        self.assertEqual(receipt["pending_decisions"], ["FC-001"])
        self.assertEqual(receipt["actionable_findings"], ["FC-001"])
        self.assertEqual(receipt["truth_and_source_quality"], "NOT_AUTHENTICATED_BY_SCRIPT")
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_changed_input_hash_is_blocked(self):
        self.input.write_text("2+1=3", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            F.validate(self.report)

    def test_wrong_occurrence_or_utf16_offset_is_blocked(self):
        self.report["claims"][0]["anchor"]["start"] += 1
        with self.assertRaisesRegex(ValueError, "Quote does not match"):
            F.validate(self.report)

    def test_empty_evidence_cannot_support_error(self):
        self.report["claims"][0]["assessment"]["evidence_ids"] = []
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            F.validate(self.report)

    def test_incorrect_calculation_is_blocked(self):
        self.report["evidence"][0]["result"] = "5"
        with self.assertRaisesRegex(ValueError, "incorrect"):
            F.validate(self.report)

    def test_rational_arithmetic_preserves_decimals(self):
        self.assertEqual(F.arithmetic("0.1+0.2"), F.arithmetic("3/10"))
        self.assertEqual(F.arithmetic("(1+0.12/4)**4-1"), F.arithmetic("0.12550881"))

    def test_daily_compounding_and_fractional_powers(self):
        from decimal import Decimal
        exact = F.arithmetic("(1+0.06/365)**365-1")
        numeric = F.decimal_arithmetic("(1+0.06/365)**365-1")
        self.assertLess(abs(float(exact)-float(numeric)), 1e-14)
        self.assertLess(abs(F.decimal_arithmetic("1.1**(1/12)-1")-Decimal("0.007974140428903741")), Decimal("1e-17"))
        self.assertLess(abs(F.decimal_arithmetic("ln(exp(0.06))")-Decimal("0.06")), Decimal("1e-45"))

    def test_numeric_evidence_checks_declared_tolerance(self):
        self.report["evidence"][0].update(expression="sqrt(2)", result="1.414213562373095",
            numeric={"precision":50,"absolute_tolerance":"1e-14","relative_tolerance":"0"})
        F.validate(self.report)
        self.report["evidence"][0]["result"]="1.41"
        with self.assertRaisesRegex(ValueError, "incorrect"):
            F.validate(self.report)

    def test_decimal_evaluator_rejects_code_and_resource_abuse(self):
        for text in ("__import__('os')", "exp(1001)", "2**10000", "open('x')", "1e9999", "sqrt(-1)"):
            with self.subTest(text=text), self.assertRaises((ValueError, ArithmeticError)):
                F.decimal_arithmetic(text)

    def test_known_error_cannot_be_hidden_as_minor(self):
        self.report["claims"][0]["assessment"]["decision_required"] = False
        with self.assertRaisesRegex(ValueError, "errors require"):
            F.validate(self.report)

    def test_code_and_unbounded_arithmetic_are_rejected(self):
        for expression in ("__import__('os').system('echo no')", "open('x')", "True+1", "2**10000",
                           "9**9**9", "[1][0]", "1/0", "1e9999", "1" * 1001):
            with self.subTest(expression=expression[:60]):
                with self.assertRaises((ValueError, ArithmeticError)):
                    F.arithmetic(expression)

    def test_blog_or_abstract_is_not_evidence(self):
        for change in ({"kind": "blog"}, {"read_scope": "abstract"}, {"read_scope": "search_snippet"}):
            self.report["evidence"] = [dict(external(), id="E1", **change)]
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    F.validate(self.report)

    def test_non_numeric_claim_cannot_use_only_arithmetic(self):
        self.report["claims"][0]["kind"] = "theory"
        with self.assertRaisesRegex(ValueError, "external evidence"):
            F.validate(self.report)

    def test_unverified_hypothesis_cannot_be_categorically_corrected(self):
        c = self.report["claims"][0]
        c["kind"] = "hypothesis"
        c["assessment"].update(status="unverified", evidence_ids=[])
        with self.assertRaisesRegex(ValueError, "Categorical correction"):
            F.validate(self.report)
        c["assessment"]["proposal"] = {"kind": "question", "text": "Specify the hypothesis and evidence."}
        receipt = F.validate(self.report)
        self.assertEqual(receipt["status_counts"], {"unverified": 1})

    def test_keep_does_not_confirm_a_known_error(self):
        previous = copy.deepcopy(self.report)
        self.report["claims"][0]["review"].update(decision="keep", comment="TEST USER: keep this teaching example",
                                                   basis="Synthetic test message", execution="kept")
        receipt = F.validate(self.report, previous)
        self.assertEqual(receipt["status_counts"], {"error": 1})
        self.assertEqual(receipt["unresolved_findings"], ["FC-001"])
        self.assertEqual(receipt["pending_decisions"], [])
        self.report["claims"][0]["assessment"].update(status="confirmed", proposal=None)
        with self.assertRaisesRegex(ValueError, "new evidence/reasoning"):
            F.validate(self.report, previous)

    def test_execution_requires_recorded_human_decision(self):
        self.report["claims"][0]["review"]["execution"] = "applied"
        with self.assertRaisesRegex(ValueError, "Pending decision"):
            F.validate(self.report)

    def test_applied_correction_requires_actual_new_text(self):
        corrected = self.root / "corrected.txt"
        corrected.write_text("2+1=3", encoding="utf-8")
        c = self.report["claims"][0]
        c["review"].update(decision="correct", comment="TEST USER: apply FC-001", basis="Synthetic test message",
                           execution="applied", application={"artifact": ref(corrected), "anchor": anchor("2+1=3", "2+1=3")})
        self.assertEqual(F.validate(self.report)["pending_decisions"], [])
        c["assessment"]["proposal"]["text"] = "2+1=4"
        with self.assertRaisesRegex(ValueError, "approved proposal"):
            F.validate(self.report)

    def test_recheck_remains_pending(self):
        self.report["claims"][0]["review"].update(decision="recheck", comment="TEST USER: check context",
                                                   basis="Synthetic test message")
        self.assertEqual(F.validate(self.report)["pending_decisions"], ["FC-001"])

    def test_explicit_assumption_is_not_an_unresolved_error(self):
        c = self.report["claims"][0]
        c["kind"] = "hypothesis"
        c["assessment"].update(status="context_dependent", evidence_ids=[], proposal=None,
                               rationale="Explicit teaching assumption; no factual correction is proposed.")
        self.assertEqual(F.validate(self.report)["unresolved_findings"], [])

    def test_resolved_application_retains_original_assessment(self):
        corrected = self.root / "corrected.txt"
        corrected.write_text("2+1=3", encoding="utf-8")
        self.report["claims"][0]["review"].update(
            decision="correct", comment="TEST USER: apply FC-001", basis="Synthetic test message",
            execution="applied", application={"artifact": ref(corrected), "anchor": anchor("2+1=3", "2+1=3")})
        receipt = F.validate(self.report)
        self.assertEqual(receipt["status_counts"], {"error": 1})
        self.assertEqual(receipt["unresolved_findings"], [])

    def test_previous_claim_cannot_disappear(self):
        previous = copy.deepcopy(self.report)
        self.report["claims"] = []
        with self.assertRaisesRegex(ValueError, "disappeared"):
            F.validate(self.report, previous)

    def test_not_checked_prevents_complete_coverage(self):
        self.report["claims"][0]["assessment"].update(status="not_checked", proposal=None, evidence_ids=[])
        with self.assertRaisesRegex(ValueError, "unreviewed content"):
            F.validate(self.report)
        self.report["scope"]["coverage"] = "partial"
        self.assertEqual(F.validate(self.report)["not_checked"], ["FC-001"])

    def test_lecture_json_formula_anchor_and_sealed_hash(self):
        from test_json_content import H, fixture as lecture_fixture
        sources, draft = lecture_fixture(self.input)
        doc, _ = H.build(sources, draft)
        lecture = self.root / "lecture.json"
        lecture.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        text = F.block_text(doc["blocks"][0])
        self.report["input"] = ref(lecture, "lecture_json")
        self.report["scope"]["reviewed_units"] = ["b1"]
        c = self.report["claims"][0]
        c["anchor"] = anchor(text, draft["blocks"][0]["content"][1]["latex"], "b1", ["s2"])
        c["assessment"].update(status="context_dependent", evidence_ids=[], proposal=None,
                               rationale="Control checks formula location only, not mathematical correctness.")
        self.assertEqual(F.validate(self.report)["claims"], 1)
        c["anchor"]["source_block_ids"] = ["invented"]
        with self.assertRaisesRegex(ValueError, "source anchor"):
            F.validate(self.report)

    def test_source_metadata_is_not_authenticated_by_validator(self):
        # Deliberately synthetic metadata can satisfy the record contract.
        # The receipt must never claim this amounts to an authenticated source.
        self.report["evidence"] = [dict(external(), id="E1")]
        self.report["claims"][0]["kind"] = "definition"
        self.assertEqual(F.validate(self.report)["truth_and_source_quality"], "NOT_AUTHENTICATED_BY_SCRIPT")


if __name__ == "__main__":
    unittest.main()
