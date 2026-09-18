"""Multi-zone geometry integrated with the complete candidate/content/workflow check."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from verify_candidate import checked, read, require, rectangle

TOLERANCE = 0.7


def load_zones(manifest, refs, plan):
    reference = manifest.get("zone_plan")
    if reference is None:
        return [], {}
    data = read(checked(reference))
    require(data.get("schema_version") == "1.0", "Expected golden-zone plan 1.0")
    for key in ("pdf", "profile", "render_plan"):
        linked = data["artifacts"][key]
        require(checked(linked).resolve() == checked(refs[key]).resolve()
                and linked["sha256"].upper() == refs[key]["sha256"].upper(), f"Zone plan uses another {key}")
    zones = data.get("zones")
    require(isinstance(zones, list) and zones, "Missing Golden zones")
    flow = {row["line_id"]: row for row in plan["flow"]}
    indexed, identifiers = {}, set()
    order = []
    for index, zone in enumerate(zones):
        identifier = zone.get("zone_id")
        require(isinstance(identifier, str) and identifier and identifier not in identifiers, "Duplicate or missing zone ID")
        identifiers.add(identifier)
        require(type(zone.get("page")) is int and 1 <= zone["page"] <= plan["pages"], "Invalid zone page")
        columns = zone.get("columns")
        require(isinstance(columns, list) and len(columns) == 2 and [c.get("column") for c in columns] == [1, 2],
                "Zone must have two ordered column frames")
        require(columns[0].get("line_ids"), "Zone needs left-column content")
        for column in columns:
            lines = column.get("line_ids")
            require(isinstance(lines, list), "Invalid zone line IDs")
            for line in lines:
                require(line in flow and line not in indexed, "Missing or duplicate zone flow line")
                row = flow[line]
                require(row["page"] == zone["page"] and row["column"] == column["column"]
                        and row["flow_id"] == zone["topic_id"], "Zone line has another page, column or topic")
                indexed[line] = index
                order.append(line)
    require(order == [r["line_id"] for r in plan["flow"]], "Zone plan must cover every body line in source order")
    return zones, indexed


def check_geometry(doc, zones, measured, style, lecture):
    import fitz
    titles = {t["topic_id"]: t["title"] for t in lecture["structure"]["topics"]}
    previous_bottom = {}
    seen = set()
    previous_page = 0
    by_line = {r["line_id"]: r for r in measured}
    for zone in zones:
        number, topic = zone["page"], zone["topic_id"]
        require(number >= previous_page and topic in titles, "Reordered zone or unknown topic")
        previous_page = number
        page = doc[number-1]
        boxes = [rectangle(c["bbox"], page) for c in zone["columns"]]
        for i, box in enumerate(boxes):
            require(abs(box.x0-style["x"][i]) <= TOLERANCE, f"Column {i+1} x coordinate differs")
            require(abs(box.width-style["width"]) <= TOLERANCE, f"Column {i+1} width differs")
            require(box.y1 <= style["bottom"]+TOLERANCE, "Column enters navigation area")
        require(abs(boxes[0].y0-boxes[1].y0) <= TOLERANCE
                and abs(boxes[0].y1-boxes[1].y1) <= TOLERANCE, "Column frames must be symmetric")
        divider = zone.get("divider")
        zone_top = boxes[0].y0
        if topic not in seen:
            require(isinstance(divider, dict), "First topic zone requires a divider")
            require(divider.get("title_text") == titles[topic], "Divider title differs from lecture")
        else:
            require(divider is None, "Continuation must not repeat the topic divider")
        seen.add(topic)
        if divider is not None:
            title = rectangle(divider["title_bbox"], page)
            rule = rectangle(divider["rule_bbox"], page)
            require(title.x0 >= style["x"][0]-TOLERANCE and title.x1 <= style["x"][1]+style["width"]+TOLERANCE,
                    "Divider title outside content width")
            region = fitz.Rect(title.x0-TOLERANCE, title.y0-TOLERANCE, title.x1+TOLERANCE, title.y1+TOLERANCE)
            actual = "".join(page.get_text("text", clip=region).split())
            require(actual == "".join(divider["title_text"].split()), "Divider title is missing from PDF")
            require(rule.x0 >= title.x1-TOLERANCE, "Rule overlaps title")
            drawings = page.get_drawings()
            require(any(d.get("rect") and abs(d["rect"].x0-rule.x0) < TOLERANCE
                        and abs(d["rect"].x1-rule.x1) < TOLERANCE
                        and abs((d["rect"].y0+d["rect"].y1)/2-(rule.y0+rule.y1)/2) < TOLERANCE
                        for d in drawings), "Divider rule is absent")
            lower = max(title.y1, rule.y1)
            zone_top = min(title.y0, rule.y0)
            if divider.get("time_pill_bbox") is not None:
                pill = rectangle(divider["time_pill_bbox"], page)
                require(abs(pill.x1-style["x"][1]-style["width"]) <= TOLERANCE
                        and rule.x1 <= pill.x0+TOLERANCE, "Invalid time pill placement")
                require(any(d.get("rect") and max(abs(a-b) for a,b in zip(d["rect"],pill)) <= TOLERANCE
                            for d in drawings), "Time pill is absent")
                time_text = divider.get("time_text")
                require(isinstance(time_text, str) and time_text.strip(), "Missing time text")
                require("".join(page.get_text("text", clip=pill).split()) == "".join(time_text.split()),
                        "Time pill text differs")
                lower = max(lower,pill.y1)
                zone_top = min(zone_top,pill.y0)
            require(boxes[0].y0 >= lower-TOLERANCE, "Body overlaps divider")
        require(zone_top >= previous_bottom.get(number, 0)-TOLERANCE, "Overlapping or reordered zones")
        actual_bottom = boxes[0].y0
        for box, column in zip(boxes, zone["columns"]):
            for identifier in column["line_ids"]:
                row = by_line[identifier]
                require(row["top"] >= box.y0-TOLERANCE and row["top"]+row["height"] <= box.y1+TOLERANCE,
                        "Body outside zone frame")
                actual_bottom = max(actual_bottom, row["top"]+row["height"])
        previous_bottom[number] = actual_bottom
    return {"status": "GOLDEN_ZONE_MECHANICS_VALIDATED", "zones": len(zones),
            "covered_body_lines": len(by_line)}


def check(candidate_manifest_path, zone_plan_path):
    from verify_candidate import check as check_candidate
    candidate = read(candidate_manifest_path)
    reference = candidate.get("zone_plan")
    require(reference is not None and checked(reference).resolve() == Path(zone_plan_path).resolve(),
            "Candidate manifest must reference this zone plan")
    result = check_candidate(candidate_manifest_path)
    require(result.get("zones"), "Candidate has no validated zones")
    return {**result, "candidate_status": result["status"], "status": "GOLDEN_ZONE_MECHANICS_VALIDATED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", required=True)
    parser.add_argument("--zone-plan", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.candidate_manifest, args.zone_plan), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
