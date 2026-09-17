"""Measured paragraph/math flow for a lecture's existing two-column renderer.

This component lays out body content only. Cover, contents, media selection and
navigation remain the responsibility of the lecture renderer and Golden rules.
"""
from __future__ import annotations

import math
from pathlib import Path
import re

from lecture_content import formula_items, require
from latex_math import checked_file, validate_asset

MM = 72 / 25.4
FONT_NAME = "SFBodyInter"


def font_metrics(skill_root, size):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(Path(skill_root) / "assets/fonts/Inter-Regular.ttf")))
    return (pdfmetrics.getAscent(FONT_NAME, size), -pdfmetrics.getDescent(FONT_NAME, size))


def text_width(text, size):
    from reportlab.pdfbase import pdfmetrics
    return pdfmetrics.stringWidth(text, FONT_NAME, size)


def geometry(profile, skill_root):
    mm, pt = profile["layout_profile"]["mm"], profile["layout_profile"]["pt"]
    ascent, descent = font_metrics(skill_root, pt["body_size"])
    return {"size": pt["body_size"], "leading": pt["body_leading"], "ascent": ascent, "descent": descent,
            "gap": mm["paragraph_extra_gap"] * MM, "width": mm["column_width"] * MM,
            "indent": mm.get("paragraph_first_line_indent", 0) * MM,
            "math_gap": mm.get("display_math_extra_gap", mm["paragraph_extra_gap"]) * MM,
            "heading_size": pt.get("subtopic_title_size", 11),
            "heading_leading": pt.get("subtopic_title_leading", 14),
            "heading_gap": mm["heading_gap"] * MM,
            "x": [mm["content_x"] * MM, mm["column_second_x"] * MM],
            "bottom": mm["content_bottom"] * MM, "height": profile["surface"][1] * MM,
            "short_lines": profile["pagination"]["short_paragraph_max_lines"],
            "split_lines": profile["pagination"]["minimum_orphan_lines"]}


def content_key(unit):
    return unit["block_id"], unit["content_index"]


def topic_heading(title, style):
    lines = []
    line = ""
    for word in title.split():
        require(text_width(word, style["heading_size"]) <= style["width"], "Topic title word exceeds column")
        candidate = (line + " " + word).strip()
        if line and text_width(candidate, style["heading_size"]) > style["width"]:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    require(lines, "Empty topic title")
    return {"lines": lines, "height": len(lines) * style["heading_leading"] + 2 * style["heading_gap"]}


def boundary_gap(previous, current, gap):
    """Only display formulas retain a vertical gap; prose uses a first-line indent."""
    if previous is None or content_key(previous) == content_key(current):
        return 0.0
    return max(gap, previous.get("math_gap", 0), current.get("math_gap", 0))


def extent(units, gap):
    total = 0.0
    previous = None
    for unit in units:
        total += boundary_gap(previous, unit, gap)
        total += unit["height"]
        previous = unit
    return total


def allowed_break(units, cut, style):
    if cut == 0 or cut == len(units):
        return True
    a, b = units[cut - 1], units[cut]
    if content_key(a) != content_key(b):
        return True
    if a["kind"] != "line" or b["kind"] != "line":
        return False
    count = a["paragraph_lines"]
    return (count > style["short_lines"] and b["line_index"] >= style["split_lines"]
            and count - b["line_index"] >= style["split_lines"])


def valid_fragments(units, style):
    """Check the visible paragraph fragment in a physical column, including page tails."""
    groups = []
    for unit in units:
        if not groups or content_key(groups[-1][-1]) != content_key(unit):
            groups.append([])
        groups[-1].append(unit)
    for group in groups:
        first, last = group[0], group[-1]
        if first["kind"] != "line":
            continue
        split = first["line_index"] > 0 or last["line_index"] + 1 < first["paragraph_lines"]
        if split and (first["paragraph_lines"] <= style["short_lines"] or len(group) < style["split_lines"]):
            return False
    return True


def best_split(units, capacity, style):
    """Find the best legal break for this prefix; never insert blank filler lines."""
    choices = []
    for cut in range(1, len(units)):
        if (allowed_break(units, cut, style) and valid_fragments(units[:cut], style)
                and valid_fragments(units[cut:], style)):
            left, right = extent(units[:cut], style["gap"]), extent(units[cut:], style["gap"])
            if max(left, right) <= capacity + 0.05:
                choices.append((abs(left - right), -left, cut, [left, right]))
    if choices:
        _, _, cut, heights = min(choices)
        return cut, heights, "BEST_LEGAL_SPLIT"
    if (extent(units, style["gap"]) <= capacity + 0.05 and valid_fragments(units, style)
            and not any(allowed_break(units, k, style) for k in range(1, len(units)))):
        return len(units), [extent(units, style["gap"]), 0.0], "INDIVISIBLE_CONTENT"
    return None


def paginate(units, capacity, style):
    require(capacity > 0 and units, "No space or content for body flow")
    result, cursor = [], 0
    while cursor < len(units):
        tail, max_end = units[cursor:], 0
        for end in range(1, len(tail) + 1):
            if extent(tail[:end], style["gap"]) > 2 * capacity + style["gap"] + 0.1:
                break
            max_end = end
        found = None
        for end in range(max_end, 0, -1):
            if not allowed_break(units, cursor + end, style):
                continue
            split = best_split(tail[:end], capacity, style)
            if split:
                found = end, split
                break
        require(found is not None, "An indivisible paragraph or formula exceeds the available body frame")
        end, (cut, heights, reason) = found
        result.append({"columns": [tail[:cut], tail[cut:end]], "heights": heights, "balance_reason": reason})
        cursor += end
    return result


def build_units(lecture, topic_id, assets, profile, skill_root):
    """Measure line breaks without modifying a character of the JSON text."""
    style = geometry(profile, skill_root)
    by_block = {b["text_block_id"]: b for b in lecture["blocks"]}
    selected = [p for p in lecture["structure"]["placements"]
                if topic_id is None or p["topic_id"] in ([topic_id] if isinstance(topic_id, str) else topic_id)]
    require(len({p["section_id"] for p in selected}) <= 1,
            "Build a separate continuous flow for each major section")
    units = []
    for placement in lecture["structure"]["placements"]:
        if topic_id is not None and placement["topic_id"] not in ([topic_id] if isinstance(topic_id, str) else topic_id):
            continue
        block = by_block[placement["text_block_id"]]
        for ci, content in enumerate(block["content"]):
            common = {"block_id": block["text_block_id"], "content_index": ci, "flow_id": placement["topic_id"]}
            if content["type"] == "display_math":
                receipt, _ = validate_asset(content["latex"], "display", style["size"], assets[content["formula_id"]])
                width, height = receipt["size_pt"]
                require(width <= style["width"] + 0.05, f"Formula {content['formula_id']} is wider than the column; use an explicit aligned expression")
                units.append({**common, "kind": "display_math", "formula_id": content["formula_id"],
                              "math_gap": style["math_gap"],
                              "height": height, "ascent": receipt["baseline_pt"], "width": width,
                              "line_index": 0, "paragraph_lines": 1})
                continue
            atoms = []
            for ri, run in enumerate(content["runs"]):
                if run["type"] == "math":
                    receipt, _ = validate_asset(run["latex"], "inline", style["size"], assets[run["formula_id"]])
                    require(receipt["size_pt"][0] <= style["width"], f"Inline formula {run['formula_id']} exceeds the column width")
                    atoms.append({"type": "math", "formula_id": run["formula_id"], "run_index": ri,
                                  "width": receipt["size_pt"][0], "height": receipt["size_pt"][1],
                                  "ascent": receipt["baseline_pt"]})
                else:
                    for match in re.finditer(r"\S+\s*|\s+", run["text"]):
                        # Split a single overlong word without adding a hyphen.
                        start, end = match.span()
                        while start < end:
                            stop = end
                            while stop > start + 1 and text_width(run["text"][start:stop], style["size"]) > style["width"] - style["indent"]:
                                stop -= 1
                            value = run["text"][start:stop]
                            atoms.append({"type": "text", "run_index": ri, "start": start, "end": stop,
                                          "text": value, "width": text_width(value, style["size"])})
                            start = stop
            lines, line, width = [], [], 0.0
            for atom in atoms:
                available = style["width"] - (style["indent"] if not lines else 0)
                require(line or atom["width"] <= available + 0.001,
                        "An inline element exceeds the indented first line; use an explicit display formula")
                if line and width + atom["width"] > available + 0.001:
                    lines.append(line)
                    line, width = [], 0.0
                line.append(atom)
                width += atom["width"]
            if line:
                lines.append(line)
            for li, line in enumerate(lines):
                parts = []
                for atom in line:
                    if parts and atom["type"] == parts[-1]["type"] == "text" and atom["run_index"] == parts[-1]["run_index"]:
                        parts[-1]["end"] = atom["end"]
                        parts[-1]["text"] += atom["text"]
                        parts[-1]["width"] += atom["width"]
                    else:
                        parts.append(dict(atom))
                ascent = max([style["ascent"]] + [p["ascent"] for p in parts if p["type"] == "math"])
                descent = max([style["descent"]] + [p["height"] - p["ascent"] for p in parts if p["type"] == "math"])
                units.append({**common, "kind": "line", "parts": parts, "line_index": li,
                              "paragraph_lines": len(lines), "height": max(style["leading"], ascent + descent),
                              "ascent": ascent, "width": sum(p["width"] for p in parts)})
    require(units, "No content for the requested topic")
    if not isinstance(topic_id, str):
        titles = {t["topic_id"]: t["title"] for t in lecture["structure"]["topics"]}
        seen = set()
        for unit in units:
            if unit["flow_id"] not in seen:
                seen.add(unit["flow_id"])
                unit["heading"] = topic_heading(titles[unit["flow_id"]], style)
                unit["height"] += unit["heading"]["height"]
    return units


def draw_body(canvas, page_layout, page_number, top, style, assets):
    """Draw Inter text and return exact planned positions for vector math insertion."""
    plan = {"text": [], "math": [], "flow": []}
    canvas.setFont(FONT_NAME, style["size"])
    canvas.setFillColorRGB(0.2, 0.2, 0.2)
    for column, units in enumerate(page_layout["columns"], 1):
        y, previous = top, None
        for unit in units:
            y += boundary_gap(previous, unit, style["gap"])
            line_id = f"{unit['block_id']}:{unit['content_index']}:{unit['line_index']}"
            heading = unit.get("heading")
            heading_height = heading["height"] if heading else 0
            baseline, x = y + heading_height + unit["ascent"], style["x"][column - 1]
            if heading:
                from reportlab.pdfbase import pdfmetrics
                size = style["heading_size"]
                asc, desc = pdfmetrics.getAscent(FONT_NAME, size), -pdfmetrics.getDescent(FONT_NAME, size)
                canvas.setFont(FONT_NAME, size)
                canvas.setFillColorRGB(0, .4, .8)
                for index, value in enumerate(heading["lines"]):
                    base = y + style["heading_gap"] + index * style["heading_leading"] + asc
                    canvas.drawString(x, style["height"] - base, value)
                    plan["text"].append({"page": page_number, "text": value, "topic_heading_for": line_id,
                                         "origin": [x, base],
                                         "bbox": [x-.03, base-asc-.05, x+text_width(value,size)+.03, base+desc+.05]})
                canvas.setFont(FONT_NAME, style["size"])
                canvas.setFillColorRGB(.2,.2,.2)
            common = {"page": page_number, "block_id": unit["block_id"], "content_index": unit["content_index"],
                      "column": column, "flow_id": unit["flow_id"], "line_id": line_id}
            plan["flow"].append({**common, "kind": unit["kind"], "line_index": unit["line_index"], "topic_heading": bool(heading)})
            if unit["kind"] == "display_math":
                left = x + (style["width"] - unit["width"]) / 2
                plan["math"].append({**common, "formula_id": unit["formula_id"], "receipt": assets[unit["formula_id"]],
                                     "bbox": [left, y + heading_height, left + unit["width"], y + unit["height"]]})
            else:
                if unit["line_index"] == 0:
                    x += style["indent"]
                for part in unit["parts"]:
                    if part["type"] == "text":
                        canvas.drawString(x, style["height"] - baseline, part["text"])
                        plan["text"].append({**common, "run_index": part["run_index"], "start": part["start"], "end": part["end"],
                                             "text": part["text"], "origin": [x, baseline],
                                             "bbox": [x - 0.03, baseline - style["ascent"] - 0.05,
                                                      x + part["width"] + 0.03, baseline + style["descent"] + 0.05]})
                    else:
                        upper = baseline - part["ascent"]
                        plan["math"].append({**common, "formula_id": part["formula_id"], "receipt": assets[part["formula_id"]],
                                             "bbox": [x, upper, x + part["width"], upper + part["height"]]})
                    x += part["width"]
            y += unit["height"]
            previous = unit
    return plan


def insert_math(source_pdf, output_pdf, math_rows):
    import fitz
    require(Path(source_pdf).resolve() != Path(output_pdf).resolve() and not Path(output_pdf).exists(),
            "Math insertion requires a new PDF path")
    with fitz.open(source_pdf) as doc:
        for row in math_rows:
            import json
            receipt = json.loads(checked_file(row["receipt"]).read_text(encoding="utf-8"))
            with fitz.open(checked_file(receipt["pdf"])) as asset:
                doc[row["page"] - 1].show_pdf_page(fitz.Rect(row["bbox"]), asset, 0)
        doc.save(output_pdf, garbage=3, deflate=True)
