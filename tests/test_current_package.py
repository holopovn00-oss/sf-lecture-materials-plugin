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

    def test_assets_are_pinned_and_golden_is_unchanged(self):
        import fitz
        manifest = json.loads((PDF_ROOT / "references/bundle-manifest.json").read_text(encoding="utf-8"))
        for path in (PDF_ROOT / "assets").rglob("*"):
            if path.is_file():
                key = path.relative_to(PDF_ROOT).as_posix()
                self.assertIn(key, manifest["files"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest().upper(), manifest["files"][key])
        path = PDF_ROOT / "assets/golden/golden.pdf"
        self.assertEqual(manifest["golden_sha256"], "333AC8285320A1CCE0D5EE1D3CFB0F8601C6D1B18377E034D249969C49E0BB55")
        with fitz.open(path) as doc:
            self.assertEqual(len(doc), 28)
            self.assertEqual(len(doc), manifest["golden_pages"])

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
