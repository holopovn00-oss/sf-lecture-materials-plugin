"""Plugin manifest, marketplace and skill-routing integrity checks."""
import json
from pathlib import Path
import re
import unittest

from test_json_content import ROOT

REPO = ROOT.parents[1]
EXPECTED_SKILLS = {
    "sf-lecture-materials": True,
    "sf-transcript-to-lecture": False,
    "sf-fact-check": False,
    "sf-lecture-to-golden-pdf": False,
}


def yaml_bool(text, key):
    match = re.search(r"^\s*" + re.escape(key) + r"\s*:\s*(true|false)\s*$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"Missing boolean {key}")
    return match.group(1) == "true"


class PluginMetadataChecks(unittest.TestCase):
    def test_exactly_one_plugin_manifest_and_expected_skill_root(self):
        manifests = sorted(REPO.rglob("plugin.json"))
        self.assertEqual(manifests, [ROOT / ".codex-plugin" / "plugin.json"])
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "sf-lecture-materials")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["skills"], "./skills/")

    def test_marketplace_entry_resolves_to_the_only_manifest(self):
        marketplace = json.loads((REPO / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        entries = marketplace["plugins"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], "sf-lecture-materials")
        self.assertEqual(entry["source"]["source"], "local")
        path = (REPO / entry["source"]["path"].removeprefix("./")).resolve()
        self.assertEqual(path, ROOT.resolve())
        self.assertTrue((path / ".codex-plugin/plugin.json").is_file())

    def test_skill_names_and_activation_policy_are_unambiguous(self):
        skill_dirs = {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}
        self.assertEqual(skill_dirs, set(EXPECTED_SKILLS))
        for name, implicit in EXPECTED_SKILLS.items():
            skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            declared = re.search(r"^name:\s*([^\s]+)\s*$", skill, re.MULTILINE)
            self.assertIsNotNone(declared)
            self.assertEqual(declared.group(1), name)
            agent = (ROOT / "skills" / name / "agents/openai.yaml").read_text(encoding="utf-8")
            self.assertEqual(yaml_bool(agent, "allow_implicit_invocation"), implicit, name)


if __name__ == "__main__":
    unittest.main()
