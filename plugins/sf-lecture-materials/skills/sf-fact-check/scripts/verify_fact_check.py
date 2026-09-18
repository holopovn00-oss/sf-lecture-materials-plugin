"""Read-only integrity checks for FactCheck 1.0.0; no network or automatic editing."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import date
from fractions import Fraction
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from lecture_content import block_text, digest, require, validate_content

VERSION = "1.0.0"
SOURCE_KINDS = {"official_norm", "official_documentation", "academic", "official_data", "primary_document"}
CLAIM_KINDS = {"fact", "calculation", "definition", "hypothesis", "theory", "causal",
               "statistic", "normative", "technical", "historical"}
STATUSES = {"confirmed", "error", "outdated", "disputed", "unverified", "context_dependent", "not_checked"}


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and all(nonempty(x) for x in value)


def keys(row, expected):
    require(isinstance(row, dict) and set(row) == set(expected.split()), "Missing or unknown fields")


def iso_date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "Use an ISO date")
    date.fromisoformat(value)


def file_bytes(ref):
    require(isinstance(ref, dict) and nonempty(ref.get("path")), "Missing file path")
    path = Path(ref["path"])
    require(path.is_absolute() and path.is_file(), "Expected an existing absolute file path")
    value = path.read_bytes()
    require(isinstance(ref.get("sha256"), str) and
            hashlib.sha256(value).hexdigest().upper() == ref["sha256"].upper(), "File hash mismatch")
    return value


def input_units(ref):
    keys(ref, "kind path sha256")
    raw = file_bytes(ref)
    if ref["kind"] == "text":
        # Preserve CR/LF and count Unicode codepoints, not bytes or UTF-16.
        return {"document": (raw.decode("utf-8-sig"), [])}
    require(ref["kind"] == "lecture_json", "Unknown input kind")
    doc = json.loads(raw.decode("utf-8-sig"))
    require(doc.get("schema_version") == "3.0.0", "FactCheck requires LectureText 3.0.0")
    require(doc.get("content_hash") == digest({k: v for k, v in doc.items() if k != "content_hash"}),
            "Lecture content hash mismatch")
    validate_content(doc["blocks"])
    result = {}
    for block in doc["blocks"]:
        identifier = block["text_block_id"]
        require(nonempty(identifier) and identifier not in result, "Repeated or missing text block ID")
        require(strings(block["source_block_ids"]) and block["source_block_ids"], "Missing source anchors")
        result[identifier] = (block_text(block), block["source_block_ids"])
    require(result, "Empty lecture")
    return result


def check_anchor(anchor, units):
    keys(anchor, "unit_id start end quote source_block_ids")
    require(isinstance(anchor["unit_id"], str) and anchor["unit_id"] in units, "Unknown unit ID")
    text, source_ids = units[anchor["unit_id"]]
    a, b = anchor["start"], anchor["end"]
    require(type(a) is int and type(b) is int and 0 <= a < b <= len(text), "Invalid quote range")
    require(nonempty(anchor["quote"]) and text[a:b] == anchor["quote"], "Quote does not match input")
    require(strings(anchor["source_block_ids"]) and
            len(set(anchor["source_block_ids"])) == len(anchor["source_block_ids"]) and
            set(anchor["source_block_ids"]) <= set(source_ids), "Unknown or repeated source anchor")
    if source_ids:
        require(anchor["source_block_ids"], "Lecture claim needs source anchors")


def arithmetic(expression):
    """Exact decimal/rational arithmetic; never eval or execute supplied code."""
    require(nonempty(expression) and len(expression) <= 1000, "Invalid arithmetic expression")
    expression = expression.strip()
    tree = ast.parse(expression, mode="eval")
    require(len(list(ast.walk(tree))) <= 100, "Arithmetic expression is too complex")

    def bounded(value):
        require(max(value.numerator.bit_length(), value.denominator.bit_length()) <= 16384,
                "Arithmetic result exceeds the supported size")
        return value

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            literal = ast.get_source_segment(expression, node)
            require(re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?", literal),
                    "Only decimal numeric literals are supported")
            return bounded(Fraction(literal))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        require(isinstance(node, ast.BinOp), "Only numeric arithmetic is allowed")
        left, right = visit(node.left), visit(node.right)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            result = left / right
        elif isinstance(node.op, ast.Pow):
            require(right.denominator == 1 and abs(right.numerator) <= 1000, "Unsupported exponent; use numeric mode for fractional powers")
            exponent = right.numerator
            require(max(left.numerator.bit_length(), left.denominator.bit_length()) * abs(exponent) <= 16384,
                    "Power exceeds the supported size")
            result = left ** exponent
        else:
            raise ValueError("Unsupported arithmetic operation")
        return bounded(result)

    return visit(tree.body)


def decimal_arithmetic(expression, precision=50):
    """Bounded decimal evaluator for financial powers, sqrt, ln/log and exp."""
    require(nonempty(expression) and len(expression) <= 1000, "Invalid arithmetic expression")
    require(type(precision) is int and 28 <= precision <= 100, "Precision must be 28..100")
    expression = expression.strip()
    tree = ast.parse(expression, mode="eval")
    require(len(list(ast.walk(tree))) <= 100, "Arithmetic expression is too complex")

    def bounded(value):
        require(value.is_finite() and (not value or abs(value.adjusted()) <= 1000), "Numeric result exceeds bounds")
        return value

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            literal = ast.get_source_segment(expression, node)
            require(re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?", literal), "Invalid numeric literal")
            return bounded(Decimal(literal))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.Call):
            require(isinstance(node.func, ast.Name) and node.func.id in {"sqrt", "ln", "log", "exp"}
                    and len(node.args) == 1 and not node.keywords, "Unsupported numeric function")
            value = visit(node.args[0])
            if node.func.id == "exp":
                require(abs(value) <= 1000, "Exponential argument exceeds bounds")
                return bounded(value.exp())
            return bounded(value.sqrt() if node.func.id == "sqrt" else value.ln())
        require(isinstance(node, ast.BinOp), "Only bounded numeric arithmetic is allowed")
        left, right = visit(node.left), visit(node.right)
        if isinstance(node.op, ast.Add): result = left + right
        elif isinstance(node.op, ast.Sub): result = left - right
        elif isinstance(node.op, ast.Mult): result = left * right
        elif isinstance(node.op, ast.Div): result = left / right
        elif isinstance(node.op, ast.Pow):
            require(abs(right) <= 1000, "Exponent exceeds bounds")
            result = left ** right
        else: raise ValueError("Unsupported arithmetic operation")
        return bounded(result)

    with localcontext() as context:
        context.prec = precision
        context.Emax, context.Emin = 2000, -2000
        return visit(tree.body)


def decision_required(claim):
    assessment = claim["assessment"]
    # Legacy reports remain conservative; new reports classify proposals explicitly.
    return assessment.get("decision_required", assessment["proposal"] is not None)


def evidence_rows(rows):
    require(isinstance(rows, list), "Evidence must be an array")
    indexed, calculations = {}, {}
    for row in rows:
        require(isinstance(row, dict) and nonempty(row.get("id")) and row["id"] not in indexed,
                "Missing or repeated evidence ID")
        kind = row.get("kind")
        if kind == "calculation":
            keys(row, "id kind expression result method applicability limitations" + (" numeric" if "numeric" in row else ""))
            require(all(nonempty(row[k]) for k in row if k != "numeric"), "Empty calculation field")
            require(len(row["result"]) <= 10000, "Calculation result is too long")
            if "numeric" in row:
                numeric = row["numeric"]
                keys(numeric, "precision absolute_tolerance relative_tolerance")
                absolute, relative = (Decimal(numeric[k]) for k in ("absolute_tolerance", "relative_tolerance"))
                require(all(v.is_finite() and 0 <= v <= Decimal("0.000001") for v in (absolute, relative)),
                        "Numeric tolerance must be finite and at most 1e-6")
                actual = decimal_arithmetic(row["expression"], numeric["precision"])
                expected = decimal_arithmetic(row["result"], numeric["precision"])
                require(abs(actual-expected) <= max(absolute, relative*abs(actual)), "Calculation result is incorrect")
            else:
                actual = arithmetic(row["expression"])
                require(actual == arithmetic(row["result"]), "Calculation result is incorrect")
            calculations[row["id"]] = str(actual)
        else:
            require(kind in SOURCE_KINDS, "Source category is not an allowed evidence basis")
            keys(row, "id kind title creator publication published version url doi locator accessed "
                      "read_scope passage passage_type applicability limitations")
            require(all(nonempty(v) for k, v in row.items() if k != "doi"), "Empty bibliographic field")
            require(row["doi"] is None or nonempty(row["doi"]), "Invalid DOI")
            url = urlparse(row["url"])
            require(url.scheme == "https" and url.hostname and not url.username and not url.password,
                    "Evidence requires a direct HTTPS source URL")
            iso_date(row["accessed"])
            require(row["read_scope"] in {"full_text", "relevant_passage"},
                    "Search snippets, abstracts and metadata are not substantive evidence")
            require(row["passage_type"] in {"quote", "paraphrase"}, "Unknown passage type")
        indexed[row["id"]] = row
    return indexed, calculations


def check_review(review, assessment, input_ref):
    keys(review, "decision comment basis execution application")
    decision, execution = review["decision"], review["execution"]
    require(decision in {"pending", "correct", "keep", "recheck"}, "Unknown human decision")
    if decision == "pending":
        require(review["comment"] == "" and review["basis"] == "" and execution == "pending" and
                review["application"] is None, "Pending decision cannot have an executed change")
        return
    require(nonempty(review["comment"]) and nonempty(review["basis"]), "Human decision needs recorded words and origin")
    permitted = {"correct": {"pending", "applied"}, "keep": {"pending", "kept"}, "recheck": {"pending"}}
    require(execution in permitted[decision], "Decision and execution disagree")
    if decision == "correct":
        require(assessment["proposal"] is not None, "Correction needs a proposal")
    if execution != "applied":
        require(review["application"] is None, "Unapplied decision cannot reference an applied correction")
        return
    application = review["application"]
    keys(application, "artifact anchor")
    target = application["artifact"]
    require(Path(target["path"]).resolve() != Path(input_ref["path"]).resolve(),
            "Applied correction must be in a new file")
    units = input_units(target)
    check_anchor(application["anchor"], units)
    require(application["anchor"]["quote"] == assessment["proposal"]["text"],
            "Applied text does not match the approved proposal")


def validate(report, previous=None):
    keys(report, "schema_version title input originals scope evidence claims assessment_change_reason")
    require(report["schema_version"] == VERSION and nonempty(report["title"]), "Invalid FactCheck version/title")
    units = input_units(report["input"])
    require(isinstance(report["originals"], list), "Originals must be an array")
    for original in report["originals"]:
        keys(original, "path sha256 locator extraction")
        file_bytes(original)
        require(nonempty(original["locator"]) and nonempty(original["extraction"]), "Missing extraction provenance")
    scope = report["scope"]
    keys(scope, "description as_of coverage reviewed_units exclusions gaps")
    require(nonempty(scope["description"]) and scope["coverage"] in {"complete", "partial"}, "Invalid scope")
    iso_date(scope["as_of"])
    require(strings(scope["exclusions"]) and strings(scope["gaps"]), "Invalid scope gaps")
    reviewed = scope["reviewed_units"]
    require(strings(reviewed) and len(set(reviewed)) == len(reviewed) and set(reviewed) <= set(units),
            "Unknown or repeated reviewed unit")
    evidence, calculations = evidence_rows(report["evidence"])
    require(isinstance(report["claims"], list), "Claims must be an array")
    claims = {}
    for row in report["claims"]:
        keys(row, "id kind anchor context assessment review")
        require(isinstance(row["id"], str) and re.fullmatch(r"FC-[0-9]{3,}", row["id"]) and
                row["id"] not in claims, "Missing or repeated claim ID")
        require(row["kind"] in CLAIM_KINDS and nonempty(row["context"]), "Invalid claim type/context")
        check_anchor(row["anchor"], units)
        assessment = row["assessment"]
        keys(assessment, "status rationale evidence_ids proposal" + (" decision_required" if "decision_required" in assessment else ""))
        require(type(decision_required(row)) is bool, "decision_required must be boolean")
        status, ids = assessment["status"], assessment["evidence_ids"]
        require(status in STATUSES and nonempty(assessment["rationale"]), "Missing status or reasoning")
        require(strings(ids) and len(set(ids)) == len(ids) and set(ids) <= set(evidence), "Unknown/repeated evidence reference")
        if status in {"confirmed", "error", "outdated", "disputed"}:
            require(ids, "Categorical assessment requires evidence")
            if row["kind"] != "calculation":
                require(any(evidence[i]["kind"] in SOURCE_KINDS for i in ids),
                        "Non-arithmetic claim requires external evidence")
        proposal = assessment["proposal"]
        if status in {"error", "outdated"}:
            require(proposal is not None, "Discrepancy needs a proposed correction or clarification")
            require(decision_required(row), "Confirmed errors require a human decision")
        require(not decision_required(row) or proposal is not None, "A required decision needs a proposal")
        if proposal is not None:
            keys(proposal, "kind text")
            require(proposal["kind"] in {"correction", "qualification", "question"} and nonempty(proposal["text"]),
                    "Invalid proposal")
            require(status not in {"confirmed", "not_checked"}, "This status cannot propose a change")
            if proposal["kind"] == "correction":
                require(status in {"error", "outdated"} and ids, "Categorical correction lacks evidence")
        if status != "not_checked":
            require(row["anchor"]["unit_id"] in reviewed, "Assessed claim is outside recorded review")
        check_review(row["review"], assessment, report["input"])
        claims[row["id"]] = row
    if scope["coverage"] == "complete":
        require(set(reviewed) == set(units) and all(c["assessment"]["status"] != "not_checked" for c in claims.values()),
                "Complete coverage has unreviewed content")
    require(isinstance(report["assessment_change_reason"], str), "Invalid assessment change reason")
    if previous is not None:
        require(previous["schema_version"] == VERSION and previous["input"] == report["input"],
                "Decision history requires the same immutable input; changed text needs a new audit")
        old = {row["id"]: row for row in previous["claims"]}
        require(set(old) <= set(claims), "Previous claim disappeared")
        for identifier, prior in old.items():
            current = claims[identifier]
            require(current["anchor"] == prior["anchor"] and current["kind"] == prior["kind"],
                    "Previous claim identity changed")
            if current["assessment"] != prior["assessment"] or report["evidence"] != previous["evidence"]:
                require(nonempty(report["assessment_change_reason"]), "Changed assessment needs new evidence/reasoning")
            if current["review"] != prior["review"]:
                require(nonempty(current["review"]["comment"]) and nonempty(current["review"]["basis"]),
                        "A decision transition needs a recorded human comment")
    counts = Counter(c["assessment"]["status"] for c in claims.values())
    unresolved = [i for i, c in claims.items() if c["review"]["execution"] != "applied" and
                  (c["assessment"]["status"] in {"error", "outdated", "disputed", "unverified"} or
                   (c["assessment"]["status"] == "context_dependent" and c["assessment"]["proposal"] is not None))]
    pending = [i for i, c in claims.items() if c["review"]["execution"] == "pending" and
               (decision_required(c) or c["review"]["decision"] == "recheck")]
    actionable = [i for i, c in claims.items() if decision_required(c)]
    retained = [i for i, c in claims.items() if c["review"]["execution"] == "kept" and
                c["assessment"]["proposal"] is not None]
    resolved = [i for i, c in claims.items() if c["review"]["execution"] in {"applied", "kept"} and
                c["assessment"]["proposal"] is not None]
    return {"status": "FACT_CHECK_RECORD_VALIDATED", "schema_version": VERSION,
            "input_sha256": report["input"]["sha256"], "claims": len(claims), "status_counts": dict(counts),
            "recorded_coverage": scope["coverage"], "unresolved_findings": unresolved,
            "pending_decisions": pending, "actionable_findings": actionable, "retained_findings": retained,
            "resolved_actionable_findings": resolved,
            "not_checked": [i for i, c in claims.items() if c["assessment"]["status"] == "not_checked"],
            "recalculated": calculations, "history": "CHECKED" if previous is not None else "NOT_REQUESTED",
            "truth_and_source_quality": "NOT_AUTHENTICATED_BY_SCRIPT",
            "claim_inventory_completeness": "NOT_AUTHENTICATED_BY_SCRIPT",
            "human_authority": "RECORDED_NOT_AUTHENTICATED", "input_write": "NEVER"}


def check(report_path, previous_path=None):
    raw = Path(report_path).read_bytes()
    previous_raw = Path(previous_path).read_bytes() if previous_path else None
    result = validate(json.loads(raw.decode("utf-8-sig")),
                      json.loads(previous_raw.decode("utf-8-sig")) if previous_raw is not None else None)
    result["report_sha256"] = hashlib.sha256(raw).hexdigest().upper()
    result["previous_report_sha256"] = hashlib.sha256(previous_raw).hexdigest().upper() if previous_raw is not None else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--previous-report", type=Path)
    args = parser.parse_args()
    try:
        result = check(args.report, args.previous_report)
    except (ValueError, TypeError, KeyError, OSError, SyntaxError, ArithmeticError, RecursionError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
