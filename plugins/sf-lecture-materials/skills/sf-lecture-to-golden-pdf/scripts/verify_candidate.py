"""Read-only PDF/text/asset/version checks; not semantic or visual certification.

Requires PyMuPDF and Pillow. All file references are explicit absolute paths.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def checked(record):
    require(isinstance(record, dict) and isinstance(record.get("path"), str), "Missing file reference")
    path = Path(record["path"])
    expected = record.get("sha256")
    require(path.is_absolute() and path.is_file(), f"Missing absolute file: {path}")
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected), "Invalid SHA-256")
    require(hashlib.sha256(path.read_bytes()).hexdigest().upper() == expected.upper(), f"Changed file: {path}")
    return path


def normalized(text):
    return " ".join(text.split())


def rectangle(values, page):
    import fitz
    require(isinstance(values, list) and len(values) == 4 and all(type(v) in {int, float} and math.isfinite(v) for v in values),
            "Invalid rectangle")
    rect = fitz.Rect(values)
    require(not rect.is_empty and page.rect.contains(rect), "Rectangle outside page")
    return rect


def internal_link_target(doc, link):
    """Resolve a local page, including MuPDF's named representation of /Fit.

    A reported numeric string alone is insufficient: verify the annotation's
    actual direct destination and the referenced page object in this document.
    Other named destinations and external/action links remain unsupported.
    """
    import fitz
    number = link.get("page")
    if link.get("kind") == fitz.LINK_GOTO:
        return number if type(number) is int and 0 <= number < len(doc) else None
    if (link.get("kind") != fitz.LINK_NAMED or link.get("view") != "Fit"
            or not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]*", number)):
        return None
    target = int(number) - 1
    xref = link.get("xref")
    if not 0 <= target < len(doc) or type(xref) is not int or not 0 < xref < doc.xref_length():
        return None
    if doc.xref_get_key(xref, "A")[0] != "null":
        return None
    kind, destination = doc.xref_get_key(xref, "Dest")
    match = re.fullmatch(r"\[\s*([1-9][0-9]*)\s+[0-9]+\s+R\s*/Fit\s*\]", destination) if kind == "array" else None
    if match and int(match.group(1)) == doc.page_xref(target):
        return target
    return None


def check_columns(doc, lecture, text_rows, profile):
    """Measure actual ordinary body lines, grouped by source topic and page.

    Plan labels identify source membership; glyph coordinates, sizes and
    baselines determine the actual columns. Missing metadata cannot skip this
    mandatory check. Special non-horizontal body layouts do not receive PASS.
    """
    structure = lecture.get("structure", {})
    topics = structure.get("topics")
    placements = structure.get("placements")
    require(isinstance(topics, list) and topics and isinstance(placements, list), "Missing body topic structure")
    topic_ids = [t.get("topic_id") for t in topics]
    require(all(isinstance(t, str) and t for t in topic_ids) and len(set(topic_ids)) == len(topic_ids), "Invalid body topic IDs")
    by_block = {}
    for item in placements:
        identifier, topic = item.get("text_block_id"), item.get("topic_id")
        require(isinstance(identifier, str) and identifier not in by_block and topic in topic_ids, "Invalid body topic placement")
        by_block[identifier] = topic
    require(set(by_block) == {b["text_block_id"] for b in lecture["blocks"]}, "Incomplete body topic placements")
    mm, pt = profile["layout_profile"]["mm"], profile["layout_profile"]["pt"]
    scale = 72 / 25.4
    starts = [mm["content_x"]*scale, mm["column_second_x"]*scale]
    width, bottom = mm["column_width"]*scale, mm["content_bottom"]*scale
    size, leading, tolerance = pt["body_size"], pt["body_leading"], 0.2
    pairs = {}
    previous = None
    for row in text_rows:
        if "block_id" not in row:
            continue
        column, topic = row.get("column"), row.get("flow_id")
        require(type(column) is int and column in {1, 2} and isinstance(topic, str), "Missing body layout metadata")
        require(topic == by_block[row["block_id"]], "Body flow differs from source topic")
        page = doc[row["page"]-1]
        rect = rectangle(row["bbox"], page)
        lines = [line for block in page.get_text("dict", clip=rect)["blocks"] if "lines" in block
                 for line in block["lines"] if any(s["text"].strip() for s in line["spans"])]
        require(bool(lines), "No actual body lines")
        for line in lines:
            require(max(abs(a-b) for a, b in zip(line["dir"], (1, 0))) <= 0.001, "Unsupported body writing direction")
            spans = [s for s in line["spans"] if s["text"].strip()]
            require(all(abs(s["size"]-size) <= 0.05 for s in spans), "Body font size differs from profile")
            baseline = spans[0]["origin"][1]
            require(all(abs(s["origin"][1]-baseline) <= tolerance for s in spans), "Unsupported body baseline layout")
            x = min(s["origin"][0] for s in spans)
            require(abs(x-starts[column-1]) <= tolerance, "Column label/indent differs from actual PDF")
            top = min(s["bbox"][1] for s in spans)
            low = max(s["bbox"][3] for s in spans)
            require(top >= -tolerance and low <= bottom+tolerance
                    and all(s["bbox"][0] >= starts[column-1]-tolerance and s["bbox"][2] <= starts[column-1]+width+tolerance for s in spans),
                    "Body text exceeds column bounds")
            key = (row["page"], column, baseline)
            require(previous is None or key > previous, "Body reading order/duplicate line differs from columns")
            previous = key
            pair = pairs.setdefault((row["page"], topic), [[], []])
            pair[column-1].append({"baseline": baseline, "top": top, "bottom": low})
    require(bool(pairs), "No body column pairs")
    result = []
    for (page, topic), columns in pairs.items():
        counts = [len(c) for c in columns]
        total = sum(counts)
        require(counts == [(total+1)//2, total//2], f"Unbalanced columns on page {page}, topic {topic}: {counts}")
        for rows in columns:
            require(all(abs(b["baseline"]-a["baseline"]-leading) <= tolerance for a, b in zip(rows, rows[1:])),
                    f"Body line spacing differs from profile on page {page}")
        if columns[1]:
            require(abs(columns[0][0]["baseline"]-columns[1][0]["baseline"]) <= tolerance,
                    f"Column tops differ on page {page}")
        heights = [c[-1]["bottom"]-c[0]["top"] if c else 0 for c in columns]
        require(abs(heights[0]-heights[1]) <= leading+tolerance, f"Column heights differ on page {page}")
        result.append({"page": page, "topic_id": topic, "line_counts": counts,
                       "heights_pt": [round(h, 4) for h in heights], "height_difference_pt": round(abs(heights[0]-heights[1]), 4),
                       "first_baselines_pt": [round(c[0]["baseline"], 4) if c else None for c in columns],
                       "body_size_pt": size, "leading_pt": leading})
    return result


def check_media_navigation(doc, composition, plan, block_ids, text_rows):
    import fitz
    from PIL import Image
    def page_at(number):
        require(type(number) is int and 1 <= number <= len(doc), "Unknown page")
        return doc[number - 1]
    visuals = composition.get("visuals")
    placed = plan.get("visuals")
    require(isinstance(visuals, list) and isinstance(placed, list), "Missing visual lists")
    ids = [v["visual_id"] for v in visuals]
    require(len(set(ids)) == len(ids) and [v["visual_id"] for v in placed] == ids, "Missing/reordered/duplicate visual")
    for visual, placement in zip(visuals, placed):
        image_path, source_path = checked(visual["image"]), checked(visual["source"])
        require(isinstance(visual.get("role"), str) and visual["role"].strip() and isinstance(visual.get("caption"), str) and visual["caption"].strip(), "Missing visual role/caption")
        anchors = visual.get("text_block_ids")
        require(isinstance(anchors, list) and anchors and set(anchors) <= set(block_ids), "Unknown visual text anchors")
        if "frame" in visual:
            frame_path = checked(visual["frame"])
            pts = visual.get("pts_seconds")
            require(type(pts) in {int, float} and math.isfinite(pts) and pts >= 0, "Missing decoded frame time")
            require(isinstance(visual.get("video_binding"), str) and visual["video_binding"].strip(), "Missing video binding")
            with Image.open(frame_path) as frame, Image.open(image_path) as image:
                box = visual.get("crop_box")
                require(isinstance(box, list) and len(box) == 4 and all(type(v) is int for v in box), "Invalid crop box")
                require(0 <= box[0] < box[2] <= frame.width and 0 <= box[1] < box[3] <= frame.height, "Crop outside source frame")
                crop = frame.convert("RGB").crop(tuple(box))
                require(crop.size == image.size and crop.tobytes() == image.convert("RGB").tobytes(), "Crop pixels differ from original frame")
        page = page_at(placement["page"])
        rect = rectangle(placement["bbox"], page)
        pix = fitz.Pixmap(str(image_path))
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        if pix.n != 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        require(abs(rect.width/rect.height - pix.width/pix.height) <= 0.002, "Visual aspect ratio changed")
        matches = [i for i in page.get_image_info(hashes=True) if i["digest"] == pix.digest and max(abs(a-b) for a,b in zip(i["bbox"], rect)) < 0.6]
        require(len(matches) == 1, "Chosen visual is missing/changed in PDF")
        require(any(r.get("visual_id") == visual["visual_id"] and r["text"] == visual["caption"] for r in text_rows), "Visual caption is not tied to planned PDF text")
    links = plan.get("links")
    require(isinstance(links, list), "Missing link plan")
    actual_links = [(page.number, link, internal_link_target(doc, link)) for page in doc for link in page.get_links()]
    for item in links:
        page = page_at(item["page"])
        target = page_at(item["target_page"])
        rect = rectangle(item["bbox"], page)
        require(any(number == page.number and destination == target.number and max(abs(a-b) for a,b in zip(link["from"], rect)) < 0.6 for number,link,destination in actual_links), "Missing/wrong internal link")
    require(len(actual_links) == len(links) and all(destination is not None for _,_,destination in actual_links), "Unexpected/broken navigation")
    require(doc.get_toc() == plan.get("bookmarks"), "Bookmarks differ from plan")
    require(all(type(row[2]) is int and 1 <= row[2] <= len(doc) for row in plan["bookmarks"]), "Bookmark target outside PDF")
    return visuals, links


def check_reports(doc, manifest, refs, lecture, by_id):
    visual_status = "NOT_RECORDED"
    if manifest.get("visual_review") is not None:
        review = read(checked(manifest["visual_review"]))
        require(review.get("pdf_sha256", "").upper() == refs["pdf"]["sha256"].upper(), "Visual report belongs to a different PDF")
        pages = review.get("pages")
        require(isinstance(pages, list) and [p.get("page") for p in pages] == list(range(1,len(doc)+1)), "Incomplete visual report coverage")
        for row in pages:
            require(row.get("status") in {"PASS", "REVIEW_REQUIRED"} and isinstance(row.get("observation"), str) and row["observation"].strip(), "Missing page observation")
            checked(row["raster"])
        visual_status = "RECORDED_NOT_AUTHENTICATED"
    text_status = "NOT_RECORDED"
    if manifest.get("text_review") is not None:
        review = read(checked(manifest["text_review"]))
        require(review["artifacts"]["lecture"]["sha256"].upper() == refs["lecture"]["sha256"].upper(), "Text review belongs to another lecture")
        for decision in review["decisions"]:
            if decision["execution"] == "pending":
                continue
            for target in decision["targets"]:
                if target["layer"] == "formula":
                    from lecture_content import validate_content
                    value = validate_content(lecture["blocks"])[target["id"]]["latex"]
                else:
                    value = lecture["title"] if target["layer"] == "title" else by_id[target["id"]]
                require(value.count(target["expected"]) == target["count"], "Recorded text decision is not preserved")
        text_status = "RECORDED_NOT_AUTHENTICATED"
    return visual_status, text_status


def check(manifest_path):
    import fitz
    from PIL import Image
    manifest = read(manifest_path)
    if manifest.get("schema_version") == "2.0":
        from verify_rich_candidate import check_current
        return check_current(manifest_path, check_media_navigation, check_reports)
    require(manifest.get("schema_version") == "1.0", "Unsupported candidate manifest")
    refs = manifest.get("artifacts")
    require(isinstance(refs, dict) and set(refs) == {"pdf", "lecture", "composition", "render_plan", "profile"}, "Incomplete candidate artifacts")
    paths = {key: checked(value) for key, value in refs.items()}
    lecture, composition, plan, profile = (read(paths[k]) for k in ("lecture", "composition", "render_plan", "profile"))
    blocks = lecture.get("blocks")
    require(isinstance(blocks, list) and bool(blocks), "No lecture blocks")
    block_ids = [b["text_block_id"] for b in blocks]
    require(len(set(block_ids)) == len(block_ids), "Duplicate lecture block IDs")
    by_id = {b["text_block_id"]: b["text"] for b in blocks}
    require(all(isinstance(v, str) and v.strip() for v in by_id.values()), "Empty lecture block")
    text_rows = plan.get("text")
    require(isinstance(text_rows, list) and bool(text_rows), "Empty text plan")
    cursor = {key: 0 for key in block_ids}
    groups = []
    with fitz.open(paths["pdf"]) as doc:
        require(type(plan.get("pages")) is int and plan["pages"] == len(doc) > 0, "Wrong page count")
        require(not doc.needs_pass, "Encrypted PDF")
        def page_at(number):
            require(type(number) is int and 1 <= number <= len(doc), "Unknown page")
            return doc[number - 1]
        page_expected = [[] for _ in doc]
        for row in text_rows:
            page = page_at(row.get("page"))
            rect = rectangle(row.get("bbox"), page)
            expected = row.get("text")
            require(isinstance(expected, str) and expected.strip(), "Empty planned text")
            actual = page.get_textbox(rect)
            require(normalized(actual) == normalized(expected), f"PDF text mismatch on page {row['page']}: {expected[:60]}")
            page_expected[row["page"] - 1].append(expected)
            if "block_id" in row:
                identifier = row["block_id"]
                require(identifier in by_id, "Unknown text block")
                start, end = row.get("start"), row.get("end")
                require(type(start) is int and type(end) is int and start == cursor[identifier] < end <= len(by_id[identifier]), "Missing/repeated text range")
                require(by_id[identifier][start:end] == expected, "Plan differs from exact source range")
                cursor[identifier] = end
                if not groups or groups[-1] != identifier:
                    groups.append(identifier)
        require(groups == block_ids and all(cursor[k] == len(by_id[k]) for k in block_ids), "Text blocks incomplete or reordered")
        width, height = [value * 72 / 25.4 for value in profile["surface"]]
        for i, page in enumerate(doc):
            require(abs(page.rect.width-width) <= 0.05*72/25.4 and abs(page.rect.height-height) <= 0.05*72/25.4, "Wrong page size")
            require(Counter(page.get_text().split()) == Counter(" ".join(page_expected[i]).split()), f"Unplanned/missing text on page {i+1}")
            used = [s for b in page.get_text("dict")["blocks"] if "lines" in b for line in b["lines"] for s in line["spans"]]
            require(all("Inter" in s["font"] for s in used), "Non-Inter text in PDF")
            for font in page.get_fonts(full=True):
                if "Inter" in font[3]:
                    require(bool(doc.extract_font(font[0])[3]), "Inter font is not embedded")
        column_pairs = check_columns(doc, lecture, text_rows, profile)
        visuals, links = check_media_navigation(doc, composition, plan, block_ids, text_rows)
        visual_status, text_status = check_reports(doc, manifest, refs, lecture, by_id)
        return {"status": "PDF_MECHANICS_VALIDATED", "pdf_sha256": refs["pdf"]["sha256"].upper(), "pages": len(doc),
                "text_blocks": len(block_ids), "visuals": len(visuals), "links": len(links), "bookmarks": len(plan["bookmarks"]),
                "column_balance": "VALIDATED", "column_pairs": column_pairs,
                "semantic_review": "NOT_EVALUATED_BY_SCRIPT", "visual_review": visual_status,
                "text_review": text_status, "manual_acceptance": "NOT_EVALUATED_BY_SCRIPT",
                "golden_gate": "NOT_CERTIFIED_BY_THIS_CHECKER"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check(args.manifest)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError, RuntimeError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
