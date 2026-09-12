"""JSON-only handoff, exact partial-removal provenance and protected LaTeX."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "plugins/sf-lecture-materials"
spec = importlib.util.spec_from_file_location("json_handoff", ROOT / "skills/sf-transcript-to-lecture/scripts/handoff.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def ref(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper()}


def fixture(source_path):
    sources = [
        {"source_block_id": "s1", "text": "Эффективная годовая ставка учитывает капитализацию. Извините за почерк.",
         "source_uri": str(source_path), "start_ms": 1000, "end_ms": 12000, "locator": "blocks[0].text"},
        {"source_block_id": "s2", "text": "EAR=(1+r/m)^m-1. r — номинальная годовая ставка, m — число начислений в год.",
         "source_uri": str(source_path), "start_ms": 12000, "end_ms": 30000, "locator": "blocks[1].text"},
        {"source_block_id": "s3", "text": "Условия: неизменная ставка и реинвестирование процентов.",
         "source_uri": str(source_path), "start_ms": 30000, "end_ms": 41000, "locator": "blocks[2].text"},
        {"source_block_id": "s4", "text": "Проверка звука.", "substantive": False,
         "source_uri": str(source_path), "locator": "blocks[3].text"},
    ]
    quote = "Извините за почерк."
    start = sources[0]["text"].index(quote)
    paragraph = lambda text: {"type": "paragraph", "runs": [{"type": "text", "text": text}]}
    draft = {"schema_version": "3.0.0", "folder_id": "control", "title": "Эффективная годовая ставка",
             "blocks": [{"text_block_id": "b1", "source_block_ids": ["s1", "s2", "s3"], "content": [
                 paragraph("Эффективная годовая ставка учитывает капитализацию процентов."),
                 {"type": "display_math", "formula_id": "ear", "latex": r"\mathrm{EAR}=\left(1+\frac{r}{m}\right)^m-1", "source_block_ids": ["s2"]},
                 {"type": "paragraph", "runs": [
                     {"type": "math", "formula_id": "nominal", "latex": "r", "source_block_ids": ["s2"]},
                     {"type": "text", "text": " — номинальная годовая ставка; "},
                     {"type": "math", "formula_id": "periods", "latex": "m", "source_block_ids": ["s2"]},
                     {"type": "text", "text": " — число начислений в год."}]},
                 paragraph(sources[2]["text"])]}],
             "transformation_ledger": [
                 {"source_block_id": "s1", "disposition": "removed_nonsemantic_span", "start": start, "end": start + len(quote),
                  "quote": quote, "reason": "Комментарий о записи не объясняет предмет."},
                 {"source_block_id": "s4", "disposition": "removed_nonsemantic", "reason": "Техническая проверка звука."}],
             "structure": {"sections": [{"section_id": "sec", "number": "1", "title": "Доходность"}],
                           "topics": [{"topic_id": "topic", "section_id": "sec", "title": "Эффективная ставка"}],
                           "placements": [{"text_block_id": "b1", "section_id": "sec", "topic_id": "topic"}]}}
    return sources, draft


class CurrentJsonChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw.json"
        self.sources, self.draft = fixture(self.raw)
        self.raw.write_text(json.dumps(self.sources, ensure_ascii=False), encoding="utf-8")

    def package(self):
        doc, sources = H.build(self.sources, self.draft)
        out = self.root / "package"
        H.write_package(out, sources, doc)
        manifest = out / "source-manifest.json"
        manifest.write_text(json.dumps({"files": [{**ref(self.raw), "order": 1, "extraction": "JSON blocks"}]}), encoding="utf-8")
        review = {"schema_version": "2.0", "artifacts": {"sources": ref(out / "source-blocks.json"),
                  "lecture": ref(out / "lecture-text.json"), "source_manifest": ref(manifest)},
                  "semantic_review": {"status": "COMPLETED", "covered_source_ids": [s["source_block_id"] for s in sources],
                                      "open_issues": [], "observations": ["Проверены определение, формула и условие реинвестирования."]},
                  "decisions": []}
        path = out / "text-review.json"
        path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        return out, path, doc, review

    def test_json_only_output_and_lossless_latex(self):
        doc, sources = H.build(self.sources, self.draft)
        out = self.root / "out"
        H.write_package(out, sources, doc)
        self.assertEqual({p.name for p in out.iterdir()}, {"lecture-text.json", "source-blocks.json"})
        actual = json.loads((out / "lecture-text.json").read_text(encoding="utf-8"))
        self.assertEqual(actual["blocks"][0]["content"], self.draft["blocks"][0]["content"])
        self.assertEqual(actual["editorial_mode"], "study_guide")
        self.assertEqual(H.check(sources, actual)["formulas"], 3)
        self.assertEqual(actual["structure"]["placements"][0]["timestamp_range"], "00:00:01 — 00:00:41")

    def test_current_package_needs_no_markdown(self):
        out, path, _, _ = self.package()
        result = H.check_package(path)
        self.assertEqual(result["status"], "PACKAGE_VALIDATED")
        self.assertEqual(result["semantic_review"], "NOT_EVALUATED_BY_SCRIPT")
        self.assertGreater(result["word_counts"]["removed_spans"], 0)
        self.assertTrue(all(p.suffix == ".json" for p in out.iterdir()))

    def test_wrong_partial_quote_is_rejected(self):
        self.draft["transformation_ledger"][0]["quote"] = "другая цитата"
        with self.assertRaisesRegex(ValueError, "source quote"):
            H.build(self.sources, self.draft)

    def test_overlapping_removals_are_rejected(self):
        self.draft["transformation_ledger"].append(copy.deepcopy(self.draft["transformation_ledger"][0]))
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            H.build(self.sources, self.draft)

    def test_partial_removal_cannot_erase_content_block(self):
        row = self.draft["transformation_ledger"][0]
        row.update(start=0, end=len(self.sources[0]["text"]), quote=self.sources[0]["text"])
        with self.assertRaisesRegex(ValueError, "entire source"):
            H.build(self.sources, self.draft)

    def test_formula_source_must_belong_to_block(self):
        self.draft["blocks"][0]["content"][1]["source_block_ids"] = ["s4"]
        with self.assertRaisesRegex(ValueError, "Formula has unknown"):
            H.build(self.sources, self.draft)

    def test_duplicate_formula_id_is_rejected(self):
        self.draft["blocks"][0]["content"][2]["runs"][0]["formula_id"] = "ear"
        with self.assertRaisesRegex(ValueError, "formula ID"):
            H.build(self.sources, self.draft)

    def test_formula_change_breaks_seal(self):
        doc, sources = H.build(self.sources, self.draft)
        doc["blocks"][0]["content"][1]["latex"] = r"r^m"
        with self.assertRaisesRegex(ValueError, "content hash"):
            H.check(sources, doc)

    def test_paragraph_breaks_cannot_hide_in_runs(self):
        self.draft["blocks"][0]["content"][0]["runs"][0]["text"] += "\nЕщё абзац."
        with self.assertRaisesRegex(ValueError, "Paragraph boundaries"):
            H.build(self.sources, self.draft)

    def test_formula_must_not_be_plain_text(self):
        self.draft["blocks"][0]["content"][0]["runs"][0]["text"] = r"EAR = \frac{1}{2}"
        with self.assertRaisesRegex(ValueError, "math run"):
            H.build(self.sources, self.draft)

    def test_malformed_or_document_latex_is_rejected(self):
        for latex in (r"\frac{r}{m", r"\input{secret}", r"\begin{document}x\end{document}", "$r^2$"):
            with self.subTest(latex=latex), self.assertRaises(ValueError):
                draft = copy.deepcopy(self.draft)
                draft["blocks"][0]["content"][1]["latex"] = latex
                H.build(self.sources, draft)

    def test_missing_source_and_reordered_blocks_are_rejected(self):
        self.draft["blocks"][0]["source_block_ids"] = ["s2", "s1", "s3"]
        with self.assertRaisesRegex(ValueError, "reordered"):
            H.build(self.sources, self.draft)

    def test_grouped_time_cannot_be_invented(self):
        self.draft["structure"]["placements"][0]["timestamp_range"] = "00:00:00 — 00:01:00"
        with self.assertRaisesRegex(ValueError, "timestamp differs"):
            H.build(self.sources, self.draft)

    def test_multifile_local_clocks_are_not_merged(self):
        self.sources[2]["source_uri"] = str(self.root / "part2.json")
        self.sources[2].update(start_ms=0, end_ms=1000)
        doc, _ = H.build(self.sources, self.draft)
        self.assertIsNone(doc["structure"]["placements"][0]["timestamp_range"])
        self.assertEqual(doc["source_locators"]["b1"][2]["start_ms"], 0)

    def test_protected_latex_decision_survives_resealing(self):
        out, path, doc, review = self.package()
        review["decisions"] = [{"id": "ear-minus-one", "decision": "keep", "execution": "verified",
                                "basis": "Сохранить вычитание единицы по источнику.", "source_block_ids": ["s2"],
                                "targets": [{"layer": "formula", "id": "ear", "expected": "-1", "count": 1}]}]
        doc["blocks"][0]["content"][1]["latex"] = r"\mathrm{EAR}=\left(1+\frac{r}{m}\right)^m"
        doc["content_hash"] = H.digest({k: v for k, v in doc.items() if k != "content_hash"})
        (out / "lecture-text.json").write_text(json.dumps(doc), encoding="utf-8")
        review["artifacts"]["lecture"] = ref(out / "lecture-text.json")
        path.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Decision not preserved"):
            H.check_package(path)

    def test_completed_review_requires_observations(self):
        _, path, _, review = self.package()
        review["semantic_review"]["observations"] = []
        path.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "observations"):
            H.check_package(path)


if __name__ == "__main__":
    unittest.main()
