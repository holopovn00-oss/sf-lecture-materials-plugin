"""Validate actual multi-zone Golden Gate mechanics against a candidate PDF.

This read-only checker complements verify_candidate.py. It verifies the concrete
full-width topic divider and its two symmetric columns using an addressable
golden-zone-plan.json. It does not certify semantics, visual judgment, or user
acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

MM_TO_PT = 72 / 25.4
TOLERANCE_PT = 0.7


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def as_rect(value, label):
    require(isinstance(value, list) and len(value) == 4 and
            all(type(item) in {int, float} and math.isfinite(item) for item in value),
            f"Invalid {label} rectangle")
    x0, y0, x1, y1 = value
    require(x1 > x0 and y1 > y0, f"Empty {label} rectangle")
    return (float(x0), float(y0), float(x1), float(y1))


def checked(reference, label):
    require(isinstance(reference, dict), f"Missing {label} reference")
    path = Path(reference.get("path", ""))
    expected = reference.get("sha256")
    require(path.is_absolute() and path.is_file(), f"Missing absolute {label}: {path}")
    require(isinstance(expected, str) and len(expected) == 64, f"Invalid {label} SHA-256")
    actual = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    require(actual == expected.upper(), f"Changed {label}: {path}")
    return path


def contains(outer, inner, tolerance=TOLERANCE_PT):
    return (outer[0] - tolerance <= inner[0] and outer[1] - tolerance <= inner[1] and
            inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance)


def overlaps(left, right, tolerance=TOLERANCE_PT):
    return not (left[2] < right[0] - tolerance or right[2] < left[0] - tolerance or
                left[3] < right[1] - tolerance or right[3] < left[1] - tolerance)


def close(actual, expected, label):
    require(abs(actual - expected) <= TOLERANCE_PT,
            f"{label}: expected {expected:.2f}pt, got {actual:.2f}pt")


def drawing_exists(page, target):
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is not None and overlaps((rect.x0, rect.y0, rect.x1, rect.y1), target):
            return True
    return False


def text_exists(page, text, target):
    matches = page.search_for(text)
    require(matches, f"Divider/body text is absent from PDF: {text!r}")
    require(any(overlaps((box.x0, box.y0, box.x1, box.y1), target) for box in matches),
            f"PDF text is outside recorded rectangle: {text!r}")


def ref_matches(candidate, zones, name):
    candidate_ref = candidate[name]
    zone_ref = zones[name]
    candidate_path = checked(candidate_ref, name)
    zone_path = checked(zone_ref, name)
    require(candidate_path.resolve() == zone_path.resolve(), f"Zone plan uses another {name}")
    require(candidate_ref["sha256"].upper() == zone_ref["sha256"].upper(),
            f"Zone plan uses another {name} hash")
    return candidate_path


def check(candidate_manifest_path, zone_plan_path):
    import fitz

    candidate = read_json(candidate_manifest_path)
    require(candidate.get("schema_version") == "2.0", "Expected candidate manifest 2.0")
    artifacts = candidate.get("artifacts")
    require(isinstance(artifacts, dict), "Missing candidate artifacts")

    zones = read_json(zone_plan_path)
    require(zones.get("schema_version") == "1.0", "Expected golden-zone plan 1.0")
    zone_artifacts = zones.get("artifacts")
    require(isinstance(zone_artifacts, dict), "Missing golden-zone artifacts")

    pdf_path = ref_matches(artifacts, zone_artifacts, "pdf")
    profile_path = ref_matches(artifacts, zone_artifacts, "profile")
    plan_path = ref_matches(artifacts, zone_artifacts, "render_plan")
    profile = read_json(profile_path)
    render_plan = read_json(plan_path)

    require(profile.get("layout_revision") == "2026-09-18", "Unsupported layout revision")
    layout = profile.get("layout_profile", {}).get("mm", {})
    required = ("content_x", "content_right", "column_width", "column_gap", "topic_title_top")
    require(all(type(layout.get(name)) in {int, float} for name in required), "Incomplete A4 profile")
    content_x = layout["content_x"] * MM_TO_PT
    content_right = layout["content_right"] * MM_TO_PT
    column_width = layout["column_width"] * MM_TO_PT
    column_gap = layout["column_gap"] * MM_TO_PT

    text_rows = render_plan.get("text")
    flow_rows = render_plan.get("flow")
    require(isinstance(text_rows, list) and isinstance(flow_rows, list), "Incomplete render plan")
    text_by_line = {}
    for row in text_rows:
        line_id = row.get("line_id")
        if line_id is not None:
            require(isinstance(line_id, str) and line_id not in text_by_line, "Duplicate text line_id")
            text_by_line[line_id] = row
    flow_by_line = {}
    for row in flow_rows:
        line_id = row.get("line_id")
        require(isinstance(line_id, str) and line_id and line_id not in flow_by_line, "Invalid/duplicate flow line_id")
        flow_by_line[line_id] = row

    entries = zones.get("zones")
    require(isinstance(entries, list) and entries, "Golden-zone plan has no zones")
    zone_ids = set()
    covered_lines = set()
    zone_topics = set()

    with fitz.open(pdf_path) as document:
        require(not document.needs_pass and len(document) > 0, "Unreadable candidate PDF")
        for zone in entries:
            require(isinstance(zone, dict), "Invalid zone")
            zone_id = zone.get("zone_id")
            page_number = zone.get("page")
            topic_id = zone.get("topic_id")
            require(isinstance(zone_id, str) and zone_id and zone_id not in zone_ids, "Invalid/duplicate zone_id")
            require(type(page_number) is int and 1 <= page_number <= len(document), "Zone page is outside PDF")
            require(isinstance(topic_id, str) and topic_id, "Zone has no topic_id")
            zone_ids.add(zone_id)
            zone_topics.add(topic_id)
            page = document[page_number - 1]

            divider = zone.get("divider")
            require(isinstance(divider, dict), "Zone has no divider")
            title = divider.get("title_text")
            title_box = as_rect(divider.get("title_bbox"), "divider title")
            rule_box = as_rect(divider.get("rule_bbox"), "divider rule")
            pill_value = divider.get("time_pill_bbox")
            pill_box = None if pill_value is None else as_rect(pill_value, "time pill")
            page_box = (0.0, 0.0, float(page.rect.width), float(page.rect.height))
            require(contains(page_box, title_box) and contains(page_box, rule_box), "Divider is outside page")
            require(title_box[0] >= content_x - TOLERANCE_PT and title_box[2] <= content_right + TOLERANCE_PT,
                    "Divider title is outside content width")
            require(rule_box[0] >= title_box[2] - TOLERANCE_PT and rule_box[2] <= content_right + TOLERANCE_PT,
                    "Divider rule does not follow title within content width")
            text_exists(page, title, title_box)
            require(drawing_exists(page, rule_box), "Divider rule is absent from PDF")
            if pill_box is not None:
                require(contains(page_box, pill_box), "Time pill is outside page")
                close(pill_box[2], content_right, "Time pill right edge")
                require(rule_box[2] <= pill_box[0] + TOLERANCE_PT, "Divider rule overlaps time pill")
                require(drawing_exists(page, pill_box), "Time pill drawing is absent from PDF")

            columns = zone.get("columns")
            require(isinstance(columns, list) and len(columns) == 2, "Zone must have exactly two columns")
            require([item.get("column") for item in columns] == [1, 2], "Columns must be ordered 1 then 2")
            balance = zone.get("balance")
            require(isinstance(balance, dict) and balance.get("status") in {"BEST_LEGAL_SPLIT", "INDIVISIBLE_CONTENT"} and
                    isinstance(balance.get("reason"), str) and balance["reason"].strip(), "Missing balance decision")

            assigned = []
            for index, column in enumerate(columns):
                box = as_rect(column.get("bbox"), f"column {index + 1}")
                expected_x = content_x + index * (column_width + column_gap)
                close(box[0], expected_x, f"Column {index + 1} x coordinate")
                close(box[2] - box[0], column_width, f"Column {index + 1} width")
                require(box[1] > title_box[3] - TOLERANCE_PT, "Column starts before its divider")
                line_ids = column.get("line_ids")
                require(isinstance(line_ids, list) and line_ids and len(set(line_ids)) == len(line_ids),
                        f"Column {index + 1} has invalid line IDs")
                assigned.extend(line_ids)
                for line_id in line_ids:
                    require(line_id not in covered_lines, "Body line is assigned to more than one zone")
                    flow = flow_by_line.get(line_id)
                    text_row = text_by_line.get(line_id)
                    require(flow is not None and text_row is not None, "Zone line is missing from render plan")
                    require(flow.get("page") == page_number and text_row.get("page") == page_number,
                            "Zone line belongs to another page")
                    require(flow.get("column") == index + 1, "Zone line is assigned to another column")
                    require(flow.get("flow_id") == topic_id, "Zone line belongs to another topic")
                    text_box = as_rect(text_row.get("bbox"), f"body line {line_id}")
                    require(contains(box, text_box), "Body line is outside recorded column")
                    body_text = text_row.get("text")
                    require(isinstance(body_text, str) and body_text.strip(), "Body line has no text")
                    text_exists(page, body_text, text_box)
                    covered_lines.add(line_id)
            require(len(assigned) >= 2, "Divider is not kept with two body lines")
            if balance["status"] == "BEST_LEGAL_SPLIT":
                require(all(column.get("line_ids") for column in columns),
                        "Best legal split must occupy both columns")

    expected_lines = {
        line_id for line_id, row in flow_by_line.items()
        if row.get("flow_id") in zone_topics
    }
    require(covered_lines == expected_lines,
            "Golden-zone plan does not cover exactly the body flow for its declared topics")
    return {
        "status": "GOLDEN_ZONE_MECHANICS_VALIDATED",
        "zones": len(entries),
        "covered_body_lines": len(covered_lines),
        "semantic_review": "NOT_EVALUATED_BY_SCRIPT",
        "manual_acceptance": "NOT_EVALUATED_BY_SCRIPT",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--zone-plan", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.candidate_manifest, args.zone_plan), ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
