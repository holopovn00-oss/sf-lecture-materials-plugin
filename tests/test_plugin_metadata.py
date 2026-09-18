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

    def test_general_metadata_matches_the_two_mode_workflow(self):
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        description = manifest["description"].lower()
        self.assertIn("полный цикл", description)
        self.assertIn("прямые навыки", description)
        self.assertNotIn("предложи подходящие варианты", manifest["interface"]["defaultPrompt"].lower())
        general = (ROOT / "skills/sf-lecture-materials/agents/openai.yaml").read_text(encoding="utf-8")
        fact_check = (ROOT / "skills/sf-fact-check/agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("полный цикл", general.lower())
        self.assertIn("action-карточ", fact_check.lower())
        self.assertIn("не переходи к pdf автоматически", fact_check.lower())

    def test_runtime_requirements_and_preflight_are_declared(self):
        runtime = json.loads((REPO / "runtime-requirements.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["schema_version"], "0.1.0")
        self.assertEqual(runtime["python"]["requirements_file"], "requirements.txt")
        self.assertEqual(runtime["runtime"]["tectonic"]["managed_version"], "0.17.0")
        preflight = (ROOT / "scripts/dependency_preflight.py").read_text(encoding="utf-8")
        self.assertIn("--approve-install", preflight)
        self.assertIn("bundled_codex_latex", preflight)
        self.assertIn("SHA-256", preflight)

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
