import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = (Path(__file__).resolve().parents[1] / 'plugins/sf-lecture-materials/skills') / "sf-transcript-to-lecture/scripts/handoff.py"


class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT.is_file():
            raise AssertionError("The handoff helper has not been implemented")
        spec = importlib.util.spec_from_file_location("handoff", SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.sources = [
            {"source_block_id": "s1", "text": "Обычно около 15%, если спрос не падает.", "source_uri": "part-1.srt", "start_ms": 1000, "end_ms": 8000},
            {"source_block_id": "s2", "text": "Проверяем звук.", "substantive": False, "source_uri": "part-1.srt"},
            {"source_block_id": "s3", "text": "Рассмотрим пример.", "source_uri": "part-2.srt", "start_ms": 0, "end_ms": 3000},
        ]
        self.draft = {
            "schema_version": "3.0.0", "folder_id": "lecture-001", "title": "Тестовая лекция",
            "blocks": [
                {"text_block_id": "t1", "content": [{"type": "paragraph", "runs": [{"type": "text", "text": self.sources[0]["text"]}]}], "source_block_ids": ["s1"]},
                {"text_block_id": "t2", "content": [{"type": "paragraph", "runs": [{"type": "text", "text": self.sources[2]["text"]}]}], "source_block_ids": ["s3"]},
            ],
            "transformation_ledger": [{"source_block_id": "s2", "disposition": "removed_nonsemantic", "reason": "Проверка микрофона"}],
            "structure": {
                "sections": [{"section_id": "sec1", "number": "1", "title": "Условия и пример"}],
                "topics": [{"topic_id": "topic1", "section_id": "sec1", "title": "Условия"}],
                "placements": [
                    {"text_block_id": "t1", "section_id": "sec1", "topic_id": "topic1"},
                    {"text_block_id": "t2", "section_id": "sec1", "topic_id": "topic1"},
                ],
            },
        }

    def test_build_preserves_text_and_local_timestamps(self):
        document, sources = self.module.build(self.sources, self.draft)
        self.module.check(sources, document)
        self.assertEqual(document["blocks"], self.draft["blocks"])
        self.assertEqual(document["structure"]["placements"][1]["timestamp_range"], "00:00:00 — 00:00:03")
        self.assertEqual(document["source_locators"]["t2"][0]["start_ms"], 0)
        self.assertTrue(document["assertions"]["semantic_review_required"])

    def test_missing_source_blocks_completion(self):
        self.draft["blocks"].pop()
        with self.assertRaises(ValueError):
            self.module.build(self.sources, self.draft)

    def test_duplicate_anchor_blocks_completion(self):
        self.draft["blocks"][1]["source_block_ids"] = ["s1", "s3"]
        with self.assertRaises(ValueError):
            self.module.build(self.sources, self.draft)

    def test_reordered_blocks_are_rejected(self):
        self.draft["blocks"].reverse()
        with self.assertRaises(ValueError):
            self.module.build(self.sources, self.draft)

    def test_substantive_removal_is_rejected(self):
        self.sources[1]["substantive"] = True
        with self.assertRaises(ValueError):
            self.module.build(self.sources, self.draft)

    def test_duplicate_placement_is_rejected(self):
        self.draft["structure"]["placements"].append(copy.deepcopy(self.draft["structure"]["placements"][0]))
        with self.assertRaises(ValueError):
            self.module.build(self.sources, self.draft)

    def test_changed_sealed_text_is_rejected(self):
        document, sources = self.module.build(self.sources, self.draft)
        document["blocks"][0]["content"][0]["runs"][0]["text"] = "15%."
        with self.assertRaises(ValueError):
            self.module.check(sources, document)

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "existing"
            target.mkdir()
            sentinel = target / "keep.txt"
            sentinel.write_text("original", encoding="utf-8")
            document, sources = self.module.build(self.sources, self.draft)
            with self.assertRaises(FileExistsError):
                self.module.write_package(target, sources, document)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
