"""Read-only measurements for LectureText 3.0.0 paragraphs and vector LaTeX."""
from __future__ import annotations

from collections import Counter
import importlib.util
import json
import math
from pathlib import Path

from lecture_content import VERSION, block_text, formula_items, require
from latex_math import checked_file, glyphs, placed_matches, validate_asset
from pdf_flow import allowed_break, best_split, boundary_gap, content_key, extent, geometry, text_width, topic_heading, line_parts, valid_hyphen


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def rect_at(value, page):
    import fitz
    require(isinstance(value, list) and len(value) == 4
            and all(type(v) in {int, float} and math.isfinite(v) for v in value), "Invalid rich-content rectangle")
    rect = fitz.Rect(value)
    require(not rect.is_empty and page.rect.contains(rect), "Rich-content rectangle outside page")
    return rect


def check_current(manifest_path, media_check, reports_check):
    import fitz
    manifest = read(manifest_path)
    refs = manifest.get("artifacts")
    require(isinstance(refs, dict) and set(refs) == {"pdf", "lecture", "composition", "render_plan", "profile"},
            "Incomplete candidate artifacts")
    paths = {key: checked_file(value) for key, value in refs.items()}
    lecture, composition, plan, profile = (read(paths[k]) for k in ("lecture", "composition", "render_plan", "profile"))
    handoff_path = Path(__file__).resolve().parents[2] / "sf-transcript-to-lecture/scripts/handoff.py"
    spec = importlib.util.spec_from_file_location("sf_rich_handoff", handoff_path)
    handoff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handoff)
    handoff.validate_lecture(lecture)
    require(profile.get("version") == "2.1.0" and profile.get("content_version") == VERSION,
            "Paragraph/math candidates require the versioned 2.1.0 profile")
    style = geometry(profile, Path(__file__).resolve().parents[1])
    blocks = {b["text_block_id"]: b for b in lecture["blocks"]}
    block_ids = list(blocks)
    by_topic = {p["text_block_id"]: p["topic_id"] for p in lecture["structure"]["placements"]}
    topic_titles = {t["topic_id"]: t["title"] for t in lecture["structure"]["topics"]}
    heading_rows = {}
    run_text, formulas, content_order = {}, {}, []
    for bid, block in blocks.items():
        for ci, item in enumerate(block["content"]):
            content_order.append((bid, ci))
            if item["type"] == "paragraph":
                for ri, run in enumerate(item["runs"]):
                    if run["type"] == "text":
                        run_text[(bid, ci, ri)] = run["text"]
        for ci, ri, formula in formula_items(block):
            formulas[formula["formula_id"]] = (bid, ci, ri, formula)
    text_rows, math_rows, flow = plan.get("text"), plan.get("math"), plan.get("flow")
    require(isinstance(text_rows, list) and isinstance(math_rows, list) and isinstance(flow, list) and flow,
            "Missing text, math or flow plan")
    require([r.get("formula_id") for r in math_rows] == list(formulas), "Missing, reordered or repeated formula occurrence")
    indexed, content_groups, line_counts = {}, [], Counter()
    for row in flow:
        identifier = row.get("line_id")
        bid, ci = row.get("block_id"), row.get("content_index")
        key = (bid, ci)
        require(isinstance(identifier, str) and identifier and identifier not in indexed, "Repeated or missing flow line ID")
        require(bid in blocks and type(ci) is int and 0 <= ci < len(blocks[bid]["content"]), "Unknown flow content address")
        kind = "line" if blocks[bid]["content"][ci]["type"] == "paragraph" else "display_math"
        require(row.get("kind") == kind and type(row.get("line_index")) is int
                and row["line_index"] == line_counts[key], "Flow line kind or order differs from source")
        require(row.get("flow_id") == by_topic[bid] and type(row.get("column")) is int and row["column"] in {1, 2},
                "Missing or incorrect rich-content column metadata")
        line_counts[key] += 1
        if not content_groups or content_groups[-1] != key:
            content_groups.append(key)
        indexed[identifier] = {"row": row, "parts": []}
    require(content_groups == content_order, "Paragraphs/formulas are incomplete, repeated or reordered")
    for key, count in line_counts.items():
        if blocks[key[0]]["content"][key[1]]["type"] == "display_math":
            require(count == 1, "A display formula cannot be split into unrelated flow lines")
    cursors = {key: 0 for key in run_text}
    observed_runs = []
    with fitz.open(paths["pdf"]) as doc:
        require(not doc.needs_pass and type(plan.get("pages")) is int and plan["pages"] == len(doc) > 0,
                "Wrong page count or encrypted PDF")
        def page_at(number):
            require(type(number) is int and 1 <= number <= len(doc), "Unknown page")
            return doc[number - 1]
        page_expected = [Counter() for _ in doc]
        math_rects = [[] for _ in doc]
        def match_line(row):
            require(row.get("line_id") in indexed, "Unplanned body element")
            item = indexed[row["line_id"]]
            require(all(row.get(k) == item["row"].get(k) for k in ("page", "column", "flow_id", "block_id", "content_index")),
                    "Body element differs from its flow line")
            return item
        for row in text_rows:
            page = page_at(row.get("page"))
            rect = rect_at(row.get("bbox"), page)
            expected = row.get("text")
            require(isinstance(expected, str) and expected, "Empty planned text")
            page_expected[row["page"] - 1].update(c for c in expected if not c.isspace())
            if "block_id" not in row:
                require("line_id" not in row, "Unanchored body text")
                # Heading font boxes can overlap adjacent baselines; select actual
                # glyph origins as for body/math instead of intersecting font boxes.
                service_glyphs = glyphs(page, rect)
                require("".join(c["c"] for c in service_glyphs) == "".join(expected.split()), "PDF service text mismatch")
                require(all("Inter" in c["font"] for c in service_glyphs), "Non-Inter service text")
                if "topic_heading_for" in row:
                    anchor = row["topic_heading_for"]
                    require(anchor in indexed and indexed[anchor]["row"].get("topic_heading") is True,
                            "Unanchored topic heading")
                    origin = row.get("origin")
                    require(isinstance(origin, list) and len(origin) == 2, "Missing topic heading origin")
                    for char, (offset, _) in zip(service_glyphs, [(i,c) for i,c in enumerate(expected) if not c.isspace()]):
                        require(abs(char["size"] - style["heading_size"]) < .05
                                and abs(char["origin"][0] - origin[0] - text_width(expected[:offset], style["heading_size"])) < .2
                                and abs(char["origin"][1] - origin[1]) < .2, "Topic heading glyph geometry differs")
                    heading_rows.setdefault(anchor, []).append(row)
                continue
            item = match_line(row)
            key = (row["block_id"], row["content_index"], row.get("run_index"))
            require(key in run_text, "Unknown text run")
            start, end = row.get("start"), row.get("end")
            require(type(start) is int and type(end) is int and start == cursors[key] < end <= len(run_text[key])
                    and run_text[key][start:end] == expected, "Missing, repeated or changed text-run range")
            cursors[key] = end
            if not observed_runs or observed_runs[-1] != key:
                observed_runs.append(key)
            origin = row.get("origin")
            require(isinstance(origin, list) and len(origin) == 2
                    and all(type(v) in {int, float} and math.isfinite(v) for v in origin), "Missing text origin")
            actual = glyphs(page, rect)
            word_space, trim = row.get("word_space", 0), row.get("trim_tail", 0)
            require(type(word_space) in {int, float} and math.isfinite(word_space)
                    and word_space >= (style.get("min_space_ratio",1)-1)*text_width(" ",style["size"])-.001
                    and type(trim) is int and 0 <= trim <= len(expected), "Invalid text spacing metadata")
            painted = expected[:len(expected)-trim] if trim else expected
            require(not trim or expected[len(expected)-trim:].isspace(), "Trimmed non-whitespace source text")
            hyphen = row.get("hyphen", False)
            require(type(hyphen) is bool, "Invalid hyphen flag")
            if hyphen:
                require(style.get("hyphenate") and trim == 0 and valid_hyphen(run_text[key], end),
                        "Unapproved discretionary hyphen")
                painted += "-"
                page_expected[row["page"]-1].update("-")
            characters = [(i, c) for i, c in enumerate(painted) if not c.isspace()]
            require(len(actual) == len(characters), "Missing or extra actual body glyph")
            for char, (offset, value) in zip(actual, characters):
                require(char["c"] == value and "Inter" in char["font"] and abs(char["size"] - style["size"]) < 0.05,
                        "Body glyph, font or size differs")
                require(abs(char["origin"][0] - origin[0] - text_width(expected[:offset], style["size"])
                            - expected[:offset].count(" ") * word_space) <= 0.2
                        and abs(char["origin"][1] - origin[1]) <= 0.2, "Body text origin or spacing differs from the actual PDF")
            width = text_width(painted, style["size"]) + painted.count(" ") * word_space
            box = [origin[0], origin[1] - style["ascent"], origin[0] + width, origin[1] + style["descent"]]
            require(max(abs(a - b) for a, b in zip(rect, box)) <= 0.2, "Body text box differs from measured font metrics")
            item["parts"].append({"type": "text", "run_index": key[2], "start": start, "end": end,
                                  "left": origin[0], "baseline": origin[1], "width": width, "text": expected,
                                  "word_space": word_space, "trim_tail": trim, "hyphen": hyphen})
        require(observed_runs == list(run_text) and all(cursors[k] == len(v) for k, v in run_text.items()),
                "Text runs incomplete or reordered")
        for row in math_rows:
            page = page_at(row.get("page"))
            rect = rect_at(row.get("bbox"), page)
            item = match_line(row)
            bid, ci, ri, formula = formulas[row["formula_id"]]
            require((bid, ci) == (row["block_id"], row["content_index"]), "Math is attached to different content")
            mode = "display" if ri is None else "inline"
            receipt, asset = validate_asset(formula["latex"], mode, style["size"], row.get("receipt"))
            require(not any(rect.intersects(old) for old in math_rects[row["page"] - 1]), "Overlapping math rectangles")
            page_expected[row["page"] - 1].update(placed_matches(page, rect, asset))
            math_rects[row["page"] - 1].append(rect)
            item["parts"].append({"type": "math", "run_index": ri, "formula_id": row["formula_id"],
                                  "left": rect.x0, "top": rect.y0, "width": rect.width, "height": rect.height,
                                  "baseline": rect.y0 + receipt["baseline_pt"], "ascent": receipt["baseline_pt"]})
        width, height = [v * 72 / 25.4 for v in profile["surface"]]
        for i, page in enumerate(doc):
            require(abs(page.rect.width - width) < 0.15 and abs(page.rect.height - height) < 0.15, "Wrong page size")
            actual = glyphs(page)
            require(Counter(c["c"] for c in actual) == page_expected[i], f"Unplanned or missing glyphs on page {i + 1}")
            for char in actual:
                require("Inter" in char["font"] or any(r.contains(fitz.Point(char["origin"])) for r in math_rects[i]),
                        "Math font used outside a verified formula")
            # PDF generators may declare an unused base font (for example Helvetica).
            # Require embedding for every font that actually paints text on this page.
            used_fonts = {c["font"].split("+")[-1] for c in actual}
            embedded_fonts = {f[3].split("+")[-1] for f in page.get_fonts(full=True)
                              if bool(doc.extract_font(f[0])[3])}
            require(used_fonts <= embedded_fonts, "PDF font is not embedded")
        measured = []
        seen_columns = set()
        seen_topics = set()
        previous_order = None
        for item in indexed.values():
            row, parts = item["row"], item["parts"]
            require(parts, "Empty flow line")
            key = content_key(row)
            x = style["x"][row["column"] - 1]
            if row["kind"] == "display_math":
                require(len(parts) == 1 and parts[0]["type"] == "math" and parts[0]["run_index"] is None,
                        "Display formula has missing or extra content")
                part = parts[0]
                require(abs(part["left"] - x - (style["width"] - part["width"]) / 2) <= 0.2,
                        "Display formula is not centered in its actual column")
                top, ascent, line_height = part["top"], part["ascent"], part["height"]
            else:
                if row["line_index"] == 0:
                    x += style["indent"]
                require(all(p["run_index"] is not None for p in parts), "Display math inside a text line")
                parts.sort(key=lambda p: (p["run_index"], p.get("start", 0)))
                require(not any(p.get("hyphen") for p in parts[:-1])
                        and (not parts[-1].get("hyphen") or row["line_index"]+1 < line_counts[key]),
                        "Discretionary hyphen must end a non-final paragraph line")
                require(abs(parts[0]["left"] - x) <= .2, "Body first-line indent differs")
                expected_parts = line_parts(parts, row["line_index"], line_counts[key], style)
                for actual_part, expected_part in zip(parts, expected_parts):
                    if actual_part["type"] == "text":
                        require(abs(actual_part["word_space"] - expected_part.get("word_space", 0)) < .001
                                and actual_part["trim_tail"] == expected_part.get("trim_tail", 0),
                                "Body justification or paragraph-final alignment differs")
                        require(abs(actual_part["width"] - expected_part["width"]) < .2, "Justified width differs")
                baseline = parts[0]["baseline"]
                require(all(abs(p["baseline"] - baseline) <= 0.2 for p in parts), "Inline formula baseline differs from text")
                for part in parts:
                    require(abs(part["left"] - x) <= 0.2, "Body run order, indent or spacing differs from actual columns")
                    x += part["width"]
                require(x <= style["x"][row["column"] - 1] + style["width"] + 0.2, "Body content exceeds column width")
                ascent = max([style["ascent"]] + [p["ascent"] for p in parts if p["type"] == "math"])
                descent = max([style["descent"]] + [p["height"] - p["ascent"] for p in parts if p["type"] == "math"])
                top, line_height = baseline - ascent, max(style["leading"], ascent + descent)
            if row.get("topic_heading"):
                require(row["flow_id"] not in seen_topics, "Topic heading repeated inside its flow")
                heading = topic_heading(topic_titles[row["flow_id"]], style)
                headers = heading_rows.get(row["line_id"], [])
                require([h["text"] for h in headers] == heading["lines"], "Missing or changed topic title")
                top -= heading["height"]
                line_height += heading["height"]
                from reportlab.pdfbase import pdfmetrics
                from pdf_flow import FONT_NAME
                for j, h in enumerate(headers):
                    require(h["page"] == row["page"] and abs(h["origin"][0] - style["x"][row["column"]-1]) < .2
                            and abs(h["origin"][1] - (top + style["heading_gap"] + j*style["heading_leading"]
                                + pdfmetrics.getAscent(FONT_NAME, style["heading_size"]))) < .2,
                            "Topic heading placement differs")
            heading_top_gap = style["heading_gap"] if row.get("topic_heading") and style.get("trim_heading_top") else 0
            column_key = (row["page"], row["column"])
            first_trim = heading_top_gap if column_key not in seen_columns else 0
            top += first_trim
            seen_columns.add(column_key)
            seen_topics.add(row["flow_id"])
            require(top >= 0 and top + line_height - first_trim <= style["bottom"] + 0.2, "Body content exceeds vertical frame")
            order = (row["page"], row["column"], top)
            require(previous_order is None or order > previous_order, "Body flow is physically repeated or reordered")
            previous_order = order
            measured.append({**row, "top": top, "height": line_height, "ascent": ascent,
                             "heading_top_gap": heading_top_gap,
                             "math_gap": style["math_gap"] if row["kind"] == "display_math" else 0,
                             "paragraph_lines": line_counts[key]})
        pairs = {}
        for unit in measured:
            pairs.setdefault(unit["page"], [[], []])[unit["column"] - 1].append(unit)
        section_by_topic = {t["topic_id"]: t["section_id"] for t in lecture["structure"]["topics"]}
        reports = []
        for page, columns in pairs.items():
            topics = list(dict.fromkeys(u["flow_id"] for c in columns for u in c))
            require(len({section_by_topic[t] for t in topics}) == 1, "Major sections must not share a page")
            if profile["pagination"].get("new_topic_starts_new_page", True):
                require(len(topics) == 1, "Legacy profile requires separate topic pages")
            if len(topics) > 1:
                require(all(any(u.get("topic_heading") for u in measured if u["flow_id"] == t) for t in topics),
                        "Continuous topics require visible headings")
            require(columns[0], "Right column cannot precede an empty left column")
            top = columns[0][0]["top"]
            for units in columns:
                y, previous = top, None
                for unit in units:
                    y += boundary_gap(previous, unit, style["gap"])
                    require(abs(unit["top"] - y) <= 0.2, "Paragraph gap, line spacing or column top differs from actual content")
                    y += unit["height"] - (unit.get("heading_top_gap", 0) if previous is None else 0)
                    previous = unit
            together = columns[0] + columns[1]
            optimum = best_split(together, style["bottom"] - top, style)
            require(optimum and optimum[0] == len(columns[0]), "Unbalanced rich columns: a better legal split exists")
            heights = [extent(c, style["gap"]) for c in columns]
            reports.append({"page": page, "topic_ids": topics, "line_counts": [len(c) for c in columns],
                            "heights_pt": [round(h, 4) for h in heights], "height_difference_pt": round(abs(heights[0] - heights[1]), 4),
                            "balance_basis": optimum[2], "measurement": "ACTUAL_GLYPHS_AND_VERIFIED_MATH"})
        for i in range(1, len(measured)):
            if (measured[i]["page"], measured[i]["column"]) != (measured[i-1]["page"], measured[i-1]["column"]):
                require(allowed_break(measured, i, style), "Paragraph widow/orphan or indivisible content was split")
        visuals, links = media_check(doc, composition, plan, block_ids, text_rows)
        visual_status, text_status = reports_check(doc, manifest, refs, lecture, {bid: block_text(b) for bid, b in blocks.items()})
        return {"status": "PDF_MECHANICS_VALIDATED", "pdf_sha256": refs["pdf"]["sha256"].upper(),
                "pages": len(doc), "text_blocks": len(blocks), "paragraphs": sum(c["type"] == "paragraph" for b in blocks.values() for c in b["content"]),
                "formulas": len(formulas), "math_placement": "ACTUAL_GLYPHS_AND_STROKES_VALIDATED",
                "math_compile_evidence": "RECORDED_NOT_AUTHENTICATED", "column_balance": "VALIDATED", "column_pairs": reports,
                "visuals": len(visuals), "links": len(links), "bookmarks": len(plan["bookmarks"]),
                "semantic_review": "NOT_EVALUATED_BY_SCRIPT", "visual_review": visual_status, "text_review": text_status,
                "manual_acceptance": "NOT_EVALUATED_BY_SCRIPT", "golden_gate": "NOT_CERTIFIED_BY_THIS_CHECKER"}
