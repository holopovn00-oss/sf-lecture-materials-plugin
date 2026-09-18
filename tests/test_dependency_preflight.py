"""Dependency preflight: no installation occurs before an explicit consent flag."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_json_content import ROOT

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import dependency_preflight as preflight


class DependencyPreflightChecks(unittest.TestCase):
    def test_runtime_manifest_matches_supported_python_requirements(self):
        requirements = preflight.read_runtime_requirements()
        self.assertEqual(requirements["schema_version"], "0.1.0")
        self.assertEqual(requirements["python"]["minimum_version"], "3.11")
        self.assertEqual(requirements["python"]["requirements_file"], "requirements.txt")
        self.assertEqual(
            {item["distribution"] for item in requirements["python"]["packages"]},
            {"PyMuPDF", "Pillow", "reportlab", "pyphen"},
        )

    def test_version_constraints_are_evaluated_without_packaging_dependency(self):
        self.assertTrue(preflight.satisfies_specifier("1.24.0", ">=1.24,<2"))
        self.assertFalse(preflight.satisfies_specifier("2.0.0", ">=1.24,<2"))
        self.assertTrue(preflight.satisfies_specifier("0.17.2", "==0.17.2"))
        self.assertFalse(preflight.satisfies_specifier("0.17.1", "==0.17.2"))

    def test_official_artifacts_are_pinned_with_sha256(self):
        self.assertIn(("windows", "x86_64"), preflight.TECTONIC_ARTIFACTS)
        for artifact in preflight.TECTONIC_ARTIFACTS.values():
            self.assertIn(preflight.MANAGED_TECTONIC_VERSION, artifact["filename"])
            self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(artifact["archive"], {"zip", "tar.gz"})

    def test_bundled_codex_runtime_is_found_before_managed_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / ".codex"
            binary = home / "plugins/cache/openai-bundled/latex/0.17.0/bin/tectonic.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"not executed in this test")
            with patch.object(preflight, "codex_home", return_value=home),                  patch.object(preflight, "_probe_tectonic", return_value="0.17.0"),                  patch.object(preflight, "managed_tectonic_path", return_value=None),                  patch.object(preflight.shutil, "which", return_value=None),                  patch.dict(os.environ, {"SF_TECTONIC": ""}, clear=False):
                result = preflight.resolve_tectonic()
        self.assertEqual(result["source"], "bundled_codex_latex")
        self.assertEqual(result["version"], "0.17.0")

    def test_missing_components_produce_actions_not_installation(self):
        packages = [{
            "distribution": "pyphen",
            "import": "pyphen",
            "required": "==0.17.2",
            "installed": None,
            "status": "MISSING",
        }]
        with patch.object(preflight, "check_python_runtime", return_value={"status": "READY"}),              patch.object(preflight, "check_python_packages", return_value=packages),              patch.object(preflight, "resolve_tectonic", return_value=None),              patch.object(preflight, "artifact_for_current_platform", return_value={"filename": "tectonic.zip"}),              patch.object(preflight, "pip_is_available", return_value=True):
            report = preflight.inspect_dependencies()
        self.assertEqual(report["status"], "ACTION_REQUIRED")
        self.assertEqual(
            [item["id"] for item in report["actions"]],
            ["INSTALL_PYTHON_REQUIREMENTS", "INSTALL_TECTONIC"],
        )
        self.assertTrue(all(item["requires_user_confirmation"] for item in report["actions"]))

    def test_install_flag_without_approval_never_calls_installer(self):
        with patch.object(preflight, "install_missing_dependencies") as install,              patch.object(preflight, "print_report"):
            exit_code = preflight.main(["--install"])
        self.assertEqual(exit_code, 1)
        install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
