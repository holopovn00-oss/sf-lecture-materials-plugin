"""Verify pinned resources only. Does not render PDFs or certify Golden Gate QA."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "references/bundle-manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("Resource manifest must contain pinned files")
    failures = []
    for relative, expected in manifest["files"].items():
        if not isinstance(expected, str) or len(expected) != 64 or any(
            char not in "0123456789ABCDEF" for char in expected
        ):
            failures.append({"path": relative, "error": "invalid expected SHA-256"})
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            failures.append({"path": relative, "error": "path outside skill"})
        elif not path.is_file():
            failures.append({"path": relative, "error": "missing"})
        elif hashlib.sha256(path.read_bytes()).hexdigest().upper() != expected:
            failures.append({"path": relative, "error": "SHA-256 mismatch"})
    return {"status": "BLOCKED" if failures else "RESOURCES_VERIFIED",
            "checked_files": len(manifest["files"]), "failures": failures,
            "candidate_qa": "NOT_PERFORMED", "golden_visual_review": "NOT_PERFORMED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = verify(args.root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"status": "BLOCKED", "reason": str(error)}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["status"] == "RESOURCES_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
