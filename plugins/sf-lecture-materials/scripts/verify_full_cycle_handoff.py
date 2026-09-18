"""Read-only full-cycle fact-check gate before Golden Gate PDF."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def keys(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected.split()),
            "Missing or unknown fields")


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def load_fact_check():
    path = Path(__file__).resolve().parents[1] / "skills/sf-fact-check/scripts/verify_fact_check.py"
    spec = importlib.util.spec_from_file_location("sf_full_cycle_fact_check", path)
    module = importlib.util.module_from_spec(spec)
    require(spec and spec.loader, "Cannot load fact-check validator")
    spec.loader.exec_module(module)
    return module


def checked_ref(ref, kind):
    keys(ref, "kind path sha256")
    require(ref["kind"] == kind and nonempty(ref["path"]), "Unexpected referenced artifact kind")
    path = Path(ref["path"])
    require(path.is_absolute() and path.is_file(), "Expected an existing absolute file path")
    require(isinstance(ref["sha256"], str) and re.fullmatch(r"[0-9a-fA-F]{64}", ref["sha256"]),
            "Invalid SHA-256")
    actual = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    require(actual == ref["sha256"].upper(), "File hash mismatch")
    return path


def same_ref(left, right):
    return (left["kind"] == right["kind"] and
            Path(left["path"]).resolve() == Path(right["path"]).resolve() and
            left["sha256"].upper() == right["sha256"].upper())


def check(handoff_path):
    raw = Path(handoff_path).read_bytes()
    handoff = json.loads(raw.decode("utf-8-sig"))
    keys(handoff, "schema_version mode checked_lecture fact_check selected_lecture coverage_resolution")
    require(handoff["schema_version"] == "1.0.0" and handoff["mode"] == "full_cycle",
            "Unsupported full-cycle handoff")
    checked_lecture = handoff["checked_lecture"]
    fact_check = handoff["fact_check"]
    selected_lecture = handoff["selected_lecture"]
    checked_ref(checked_lecture, "lecture_json")
    fact_path = checked_ref(fact_check, "fact_check")
    checked_ref(selected_lecture, "lecture_json")

    fact = load_fact_check()
    fact.input_units(checked_lecture)
    fact.input_units(selected_lecture)
    report = json.loads(fact_path.read_bytes().decode("utf-8-sig"))
    receipt = fact.validate(report)
    require(same_ref(report["input"], checked_lecture),
            "Fact check input is not the lecture checked in this full cycle")
    require(not receipt["pending_decisions"], "Fact-check decisions are still pending")

    applied, retained = [], []
    for claim in report["claims"]:
        review = claim["review"]
        identifier = claim["id"]
        if review["decision"] == "recheck":
            raise ValueError("Fact-check recheck is still pending")
        if review["decision"] == "correct":
            require(review["execution"] == "applied", "Correct decision is not applied")
            artifact = review["application"]["artifact"]
            require(same_ref(artifact, selected_lecture),
                    "Applied correction targets another selected lecture")
            applied.append(identifier)
        elif review["decision"] == "keep":
            require(review["execution"] == "kept", "Keep decision is not recorded as kept")
            if claim["assessment"]["proposal"] is not None:
                retained.append(identifier)
        elif review["decision"] == "pending":
            require(claim["assessment"]["proposal"] is None,
                    "Actionable fact-check decision is pending")
        else:
            raise ValueError("Unknown fact-check decision")

    resolution = handoff["coverage_resolution"]
    keys(resolution, "status comment basis")
    incomplete = report["scope"]["coverage"] == "partial" or bool(receipt["not_checked"])
    if incomplete:
        require(resolution["status"] == "accepted_limit" and nonempty(resolution["comment"]) and nonempty(resolution["basis"]),
                "Incomplete fact-check coverage requires recorded limit acceptance")
    else:
        require(resolution["status"] == "complete" and resolution["comment"] == "" and resolution["basis"] == "",
                "Complete fact-check coverage must not invent a limit acceptance")

    return {
        "status": "FULL_CYCLE_FACT_CHECK_GATE_VALIDATED",
        "handoff_sha256": hashlib.sha256(raw).hexdigest().upper(),
        "checked_lecture_sha256": checked_lecture["sha256"].upper(),
        "selected_lecture_sha256": selected_lecture["sha256"].upper(),
        "fact_check_sha256": fact_check["sha256"].upper(),
        "applied_decisions": applied,
        "retained_findings": retained,
        "coverage_resolution": resolution["status"],
        "pdf_gate": "READY_FOR_PDF",
        "input_write": "NEVER",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.handoff), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
