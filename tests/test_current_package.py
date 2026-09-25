"""Current package integrity, real Golden bytes, links and old-format rejection."""
import hashlib
import json
from pathlib import Path
import re
import unittest
from urllib.parse import unquote

from test_json_content import H, ROOT, fixture

REPO = ROOT.parents[1]
PDF_ROOT = ROOT / "skills/sf-lecture-to-golden-pdf"


class CurrentPackageChecks(unittest.TestCase):
    def test_plugin_release_is_consistent(self):
        plugin = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        bundle = json.loads((PDF_ROOT / "references/bundle-manifest.json").read_text(encoding="utf-8"))
        acceptance = json.loads((PDF_ROOT / "references/golden-accepted.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "0.1.0")
        self.assertEqual(bundle["plugin_version"], plugin["version"])
        self.assertEqual(acceptance["version"], plugin["version"])

    def test_assets_and_golden_are_pinned(self):
        import fitz
        manifest = json.loads((PDF_ROOT / "references/bundle-manifest.json").read_text(encoding="utf-8"))
        for path in (PDF_ROOT / "assets").rglob("*"):
            if path.is_file():
                key = path.relative_to(PDF_ROOT).as_posix()
                self.assertIn(key, manifest["files"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest().upper(), manifest["files"][key])
        path = PDF_ROOT / "assets/golden/golden.pdf"
        self.assertEqual(manifest["golden_sha256"], "5E70D344159C76CF1B3FFCFB42159EEA76E56D82C27397A04791B398202C108B")
        with fitz.open(path) as doc:
            self.assertEqual(len(doc), 28)
            self.assertEqual(len(doc), manifest["golden_pages"])

    def test_golden_captions_and_topic_times_are_separate(self):
        import fitz
        path = PDF_ROOT / "assets/golden/golden.pdf"
        with fitz.open(path) as doc:
            captions = 0
            topic_times = 0
            for page in doc:
                self.assertAlmostEqual(page.rect.width, fitz.paper_rect("a4").width, delta=1)
                self.assertAlmostEqual(page.rect.height, fitz.paper_rect("a4").height, delta=1)
                lines = page.get_text().splitlines()
                topic_times += sum(bool(re.fullmatch(r"\d{2}:\d{2}:\d{2}\s*[–-]\s*\d{2}:\d{2}:\d{2}", line))
                                   for line in lines)
                for index, line in enumerate(lines):
                    if line.startswith("Источник:"):
                        captions += 1
                        self.assertRegex(lines[index + 1], r"^(?:Слайд|Страница) [1-9]\d*\.$")
                        self.assertTrue(lines[index + 2].startswith("Тема:"))
                        self.assertNotRegex(lines[index + 3], r"^(?:Видео\b|\d{2}:\d{2}:\d{2})")
            self.assertEqual(captions, 17)
            self.assertEqual(topic_times, 32)
            self.assertEqual(len(doc[1].get_links()), 32)
            self.assertIn("сильно.", doc[11].get_text())
            self.assertTrue(doc[12].get_text().startswith("ОТЧЕТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ"))

    def test_local_markdown_links_resolve(self):
        for path in REPO.rglob("*.md"):
            if ".git" in path.parts:
                continue
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                target = target.strip("<>").split("#", 1)[0]
                if not target or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                    continue
                resolved = (path.parent / unquote(target)).resolve()
                self.assertTrue(resolved.is_relative_to(REPO.resolve()), (path, target))
                self.assertTrue(resolved.exists(), (path, target))

    def test_old_lecture_schema_rejected(self):
        sources, draft = fixture(Path("source.json"))
        lecture, sources = H.build(sources, draft)
        lecture["schema_version"] = "2.0.0"
        with self.assertRaises(ValueError):
            H.check(sources, lecture)


if __name__ == "__main__":
    unittest.main()
