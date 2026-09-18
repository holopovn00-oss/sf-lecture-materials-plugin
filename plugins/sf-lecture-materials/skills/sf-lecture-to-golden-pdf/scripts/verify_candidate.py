"""Read-only PDF/text/asset/version checks; not semantic or visual certification.

Requires PyMuPDF and Pillow. All file references are explicit absolute paths.
"""
from __future__ import annotations
import argparse
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
    manifest = read(manifest_path)
    require(manifest.get("schema_version") == "2.0", "Unsupported candidate manifest: expected 2.0")
    from verify_rich_candidate import check_current
    return check_current(manifest_path, check_media_navigation, check_reports)


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
