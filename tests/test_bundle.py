import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = (Path(__file__).resolve().parents[1] / 'plugins/sf-lecture-materials/skills') / "sf-lecture-to-golden-pdf/scripts/verify_bundle.py"
spec = importlib.util.spec_from_file_location("verify_bundle", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class BundleTests(unittest.TestCase):
    def make_fixture(self, root):
        (root / "references").mkdir()
        data = b"pinned asset"
        (root / "asset.bin").write_bytes(data)
        manifest = {"files": {"asset.bin": hashlib.sha256(data).hexdigest().upper()}}
        (root / "references/bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_real_packaged_resources(self):
        receipt = verifier.verify(SCRIPT.parents[1])
        self.assertEqual(receipt["status"], "RESOURCES_VERIFIED")
        self.assertEqual(receipt["checked_files"], 16)
        self.assertEqual(receipt["candidate_qa"], "NOT_PERFORMED")

    def test_changed_resource_is_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_fixture(root)
            (root / "asset.bin").write_bytes(b"changed")
            self.assertEqual(verifier.verify(root)["status"], "BLOCKED")

    def test_missing_resource_is_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_fixture(root)
            (root / "asset.bin").unlink()
            self.assertEqual(verifier.verify(root)["status"], "BLOCKED")

    def test_empty_manifest_cannot_certify_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "references").mkdir()
            (root / "references/bundle-manifest.json").write_text('{"files": {}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                verifier.verify(root)


if __name__ == "__main__":
    unittest.main()
