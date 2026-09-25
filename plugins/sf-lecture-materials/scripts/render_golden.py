"""Reusable Golden A4 renderer: typed lecture, supplied raster visuals, zones and navigation.

No editorial generation, installation or acceptance. Output is a review candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

from lecture_content import formula_items, require
from latex_math import compile_math
from text_typography import cover_title
from pdf_flow import (FONT_NAME, MM, build_units, draw_body, geometry, insert_math,
                      paginate, text_width)

PLUGIN = Path(__file__).resolve().parents[1]
PDF_ROOT = PLUGIN / "skills/sf-lecture-to-golden-pdf"
sys.path.insert(0, str(PDF_ROOT / "scripts"))
from verify_candidate import check, check_workflow, checked

PROFILE = PDF_ROOT / "references/adapters/a4/2.1.0.json"


def layout_toc(structure, profile, page_number_width=None):
    """Measured section groups, balanced final page, no orphaned headings."""
    mm, pt = profile["layout_profile"]["mm"], profile["layout_profile"]["pt"]
    bold = "SFHeadingInter"
    start, bottom = mm["toc_top"]*MM, mm["toc_bottom"]*MM
    available = bottom-start
    number_width = max(mm["toc_number_field_min"]*MM, page_number_width or 0)
    topic_width = (mm["toc_column_width"]-mm["toc_topic_text_offset"]-mm["toc_number_gap"])*MM-number_width
    heading_width = (mm["toc_column_width"]-mm["toc_section_text_offset"])*MM
    def head(topic, title):
        lines = wrap(title, heading_width, pt["toc_section_size"], bold)
        divider = max(mm["toc_badge_size"]*MM,
                      len(lines)*pt["toc_section_leading"])+mm["toc_divider_gap"]*MM
        return {"topic":topic,"lines":lines,"section_heading":True,
                "divider_offset":divider,"height":divider+mm["toc_first_topic_gap"]*MM}
    groups = []
    for section in structure["sections"]:
        topics = [t for t in structure["topics"] if t["section_id"] == section["section_id"]]
        if not topics:
            continue
        entries = [head(topics[0], section["title"])]
        for topic in topics:
            lines = wrap(topic["title"], topic_width, pt["toc_topic_size"])
            entries.append({"topic":topic,"lines":lines,"section_heading":False,
                            "height":len(lines)*pt["toc_topic_leading"]+mm["toc_topic_gap"]*MM})
        groups.append((section, entries))
    columns, column, y = [], [], start
    def flush():
        nonlocal column, y
        if column:
            columns.append(column)
        column, y = [], start
    for section, entries in groups:
        group_height = sum(e["height"] for e in entries)
        if group_height <= available and y+group_height > bottom:
            flush()
        first = entries[0]
        if y+first["height"]+entries[1]["height"] > bottom:
            flush()
        require(first["height"]+entries[1]["height"] <= available, "TOC heading and entry exceed column")
        column.append({**first,"top":y})
        y += first["height"]
        for entry in entries[1:]:
            if y+entry["height"] > bottom:
                flush()
                continued = head(entry["topic"],section["title"]+" (продолжение)")
                require(continued["height"]+entry["height"] <= available,"TOC continuation exceeds column")
                column.append({**continued,"top":y})
                y += continued["height"]
            column.append({**entry,"top":y})
            y += entry["height"]
        y += mm["toc_section_gap"]*MM
    flush()
    # Rebalance complete groups on the last page, retaining reading order.
    last_start = ((len(columns)-1)//2)*2 if columns else 0
    tail = [e for col in columns[last_start:] for e in col]
    blocks = []
    for entry in tail:
        if entry["section_heading"]:
            blocks.append([])
        blocks[-1].append(entry)
    gap = mm["toc_section_gap"]*MM
    def block_height(items):
        return sum(e["height"] for b in items for e in b)+max(0,len(items)-1)*gap
    splits = [(abs(block_height(blocks[:i])-block_height(blocks[i:])),
               -block_height(blocks[:i]),i) for i in range(1,len(blocks))
              if max(block_height(blocks[:i]),block_height(blocks[i:])) <= available]
    if splits:
        split = min(splits)[2]
        balanced = []
        for half in (blocks[:split],blocks[split:]):
            placed, y = [], start
            for block in half:
                for entry in block:
                    placed.append({**entry,"top":y})
                    y += entry["height"]
                y += gap
            balanced.append(placed)
        columns[last_start:] = balanced
    return [[{**e,"column":ci%2} for ci in range(i,min(i+2,len(columns))) for e in columns[ci]]
            for i in range(0,len(columns),2)]


def draw_toc_entries(canvas, entries, structure, destinations, profile, service):
    """Draw the reference TOC; return clickable topic rectangles and destinations."""
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    mm, pt = profile["layout_profile"]["mm"], profile["layout_profile"]["pt"]
    tokens, height = profile["visual_tokens"], profile["surface"][1]*MM
    links = []
    for entry in entries:
        x=(mm["toc_margin_x"]+entry["column"]*(mm["toc_column_width"]+mm["toc_column_gap"]))*MM
        is_heading=entry["section_heading"]
        leading=pt["toc_section_leading"] if is_heading else pt["toc_topic_leading"]
        size=pt["toc_section_size"] if is_heading else pt["toc_topic_size"]
        text_x=x+mm["toc_section_text_offset" if is_heading else "toc_topic_text_offset"]*MM
        canvas.setFillColor(HexColor(tokens["brand"]))
        if is_heading:
            diameter=mm["toc_badge_size"]*MM
            canvas.circle(x+diameter/2,height-entry["top"]-diameter/2,diameter/2,fill=1,stroke=0)
            index=next(i for i,s in enumerate(structure["sections"]) if s["section_id"]==entry["topic"]["section_id"])
            label=str(index+1).zfill(2)
            service(label,x+(diameter-text_width(label,pt["toc_page_size"]))/2,
                    entry["top"]+(diameter-pt["toc_page_size"])/2,pt["toc_page_size"],"#FFFFFF")
            canvas.setStrokeColor(HexColor(tokens["border"]))
            canvas.setLineWidth(pt["rule_width"])
            line_y=height-entry["top"]-entry["divider_offset"]
            canvas.line(x,line_y,x+mm["toc_column_width"]*MM,line_y)
        else:
            diameter=mm["toc_bullet_diameter"]*MM
            canvas.circle(x+diameter/2,height-entry["top"]-size/2,diameter/2,fill=1,stroke=0)
        boxes=[]
        for i,line in enumerate(entry["lines"]):
            top=entry["top"]+i*leading
            boxes.append(service(line,text_x,top,size,"text" if is_heading else "brand",bold=is_heading))
            if not is_heading:
                canvas.setStrokeColor(HexColor(tokens["brand"]))
                canvas.setLineWidth(.35)
                underline_y=height-top-pdfmetrics.getAscent(FONT_NAME,size)-.8
                canvas.line(text_x,underline_y,text_x+text_width(line,size),underline_y)
        if not is_heading:
            target=destinations[entry["topic"]["topic_id"]]
            number="стр. "+str(target)
            number_x=x+mm["toc_column_width"]*MM-text_width(number,pt["toc_page_size"])
            require(max(b[2] for b in boxes)+mm["toc_number_gap"]*MM <= number_x+.1,
                    "TOC topic overlaps page number")
            service(number,number_x,entry["top"],pt["toc_page_size"],"muted")
            links.append({"target_page":target,"bbox":[boxes[0][0],boxes[0][1],
                          x+mm["toc_column_width"]*MM,boxes[-1][3]]})
    return links


def ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper()}


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return ref(path)


def wrap(text, width, size, font=FONT_NAME):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines, line = [], ""
    for word in text.split():
        require(stringWidth(word, font, size) <= width, "Heading/caption word exceeds its frame")
        candidate = (line+" "+word).strip()
        if line and stringWidth(candidate, font, size) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    require(lines, "Empty heading or caption")
    return lines


def display_timestamp(value):
    """Truncate fractional seconds for display; do not modify source anchors."""
    return re.sub(r"(\b\d{2,}:\d{2}:\d{2})[.,]\d+", r"\1", value)


def topic_timestamp(lecture, topic_id, source_labels=None):
    """Combine only adjacent blocks with ordered anchors in the same source file."""
    placements = lecture["structure"]["placements"]
    selected = [(i, p) for i, p in enumerate(placements) if p["topic_id"] == topic_id]
    source_labels = source_labels or {}
    source_uris = {a.get("source_uri") for _, p in selected
                   for a in lecture.get("source_locators", {}).get(p["text_block_id"], [])}
    label = source_labels.get(next(iter(source_uris)), "") if len(source_uris) == 1 else ""
    def labeled(value):
        return (label+" · " if label and value else "")+value
    if len(selected) == 1:
        return labeled(selected[0][1].get("timestamp_range") or "")
    if not selected or [i for i, _ in selected] != list(range(selected[0][0], selected[-1][0]+1)):
        return ""
    intervals, sources = [], set()
    for _, placement in selected:
        anchors = lecture.get("source_locators", {}).get(placement["text_block_id"], [])
        if not anchors or any(not a.get("source_uri") or not isinstance(a.get("start_ms"), int)
                              or not isinstance(a.get("end_ms"), int) or a["start_ms"] < 0
                              or a["end_ms"] < a["start_ms"] for a in anchors):
            return ""
        sources.update(a["source_uri"] for a in anchors)
        intervals.append((min(a["start_ms"] for a in anchors), max(a["end_ms"] for a in anchors)))
    if len(sources) != 1 or any(b[0] < a[0] or b[1] < a[1] for a, b in zip(intervals, intervals[1:])):
        return ""
    def stamp(ms):
        seconds = ms // 1000
        return f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}"
    return labeled(stamp(intervals[0][0])+" — "+stamp(intervals[-1][1]))


def time_source_labels(lecture, composition):
    """Use explicit video/transcript correspondence, never cumulative offsets."""
    known = {a["source_uri"] for anchors in lecture.get("source_locators", {}).values()
             for a in anchors if a.get("source_uri") and a.get("start_ms") is not None}
    labels = {}
    for row in composition.get("time_sources", []):
        uri, label = row.get("source_uri"), row.get("label")
        require(uri in known and uri not in labels, "Unknown or duplicate timed source")
        require(isinstance(label, str) and label.strip() and label not in labels.values(),
                "Missing or duplicate video label")
        require(isinstance(row.get("basis"), str) and row["basis"].strip(),
                "Video label requires a source correspondence basis")
        labels[uri] = label
    if known:
        require(set(labels) == known, "Timed sources require explicit video labels, including a single video")
    return labels


def divider_layout(title, time, y, profile, style):
    time = display_timestamp(time)
    bar = profile["topic_bar"]
    right = style["x"][1]+style["width"]
    size, leading = bar["title_size_pt"], bar["title_leading_pt"]
    padding = bar["pill_padding_mm"]*MM
    pill_width = text_width(time, size)+2*padding if time else 0
    reserved = bar["title_time_reserved_gap_mm"]*MM
    title_right = right-pill_width-reserved
    lines = wrap(title, title_right-style["x"][0], size)
    title_width = max(text_width(line, size) for line in lines)
    height = max(len(lines)*leading, bar["pill_height_mm"]*MM)
    return {"lines": lines, "time": time, "top": y, "height": height, "title_width": title_width,
            "pill_width": pill_width, "size": size, "leading": leading}


def visual_layout(visual, profile):
    from PIL import Image
    mm, pt = profile["layout_profile"]["mm"], profile["layout_profile"]["pt"]
    image = checked(visual["image"])
    checked(visual["source"])
    with Image.open(image) as im:
        requested_width = visual.get("display_width_mm", mm["image_max_width"])
        require(type(requested_width) in (int, float) and math.isfinite(requested_width)
                and 0 < requested_width <= mm["image_max_width"], "Invalid visual display width")
        if "display_width_mm" in visual:
            require(isinstance(visual.get("size_reason"), str) and visual["size_reason"].strip(),
                    "Explicit visual size requires a readability/layout reason")
        scale = min(requested_width*MM/im.width, mm["image_max_height"]*MM/im.height)
        width, height = im.width*scale, im.height*scale
    following_gap = visual.get("following_gap_mm", mm["card_following_gap"])
    require(type(following_gap) in (int, float) and math.isfinite(following_gap)
            and mm["card_following_gap_min"] <= following_gap <= mm["card_following_gap"],
            "Visual following gap outside the A4 profile")
    if "following_gap_mm" in visual:
        require(isinstance(visual.get("layout_reason"), str) and visual["layout_reason"].strip(),
                "Explicit visual following gap requires a layout reason")
    lines = [line for paragraph in visual["caption"].splitlines() if paragraph.strip()
             for line in wrap(paragraph, mm["image_max_width"]*MM, pt["caption_size"])]
    require(lines or visual.get("blank_caption_authorization"), "Empty visual caption")
    total = 2*mm["card_padding"]*MM+height+mm["image_caption_gap"]*MM+len(lines)*pt["caption_leading"]
    return {"visual": visual, "width": width, "image_height": height, "height": total,
            "caption_lines": lines, "following_gap": following_gap*MM}


def layout_body(lecture, composition, assets, profile, style):
    mm = profile["layout_profile"]["mm"]
    top_default = mm["topic_title_top"]*MM+profile["layout_profile"]["pt"]["small_label_leading"]
    gap = mm["heading_gap"]*MM
    placements = lecture["structure"]["placements"]
    topics = lecture["structure"]["topics"]
    source_labels = time_source_labels(lecture, composition)
    by_block = {p["text_block_id"]: p for p in placements}
    media = {}
    for visual in composition["visuals"]:
        require(visual.get("role") and (visual.get("caption") or visual.get("blank_caption_authorization")) and visual.get("text_block_ids"), "Incomplete visual")
        anchor = visual["text_block_ids"][0]
        require(anchor in by_block, "Unknown visual anchor")
        media.setdefault(anchor, []).append(visual_layout(visual, profile))
    pages, current, y = [], None, top_default
    seen_topics = set()
    seen_sections = set()

    def start_page(section):
        nonlocal current, y
        current = {"section_id": section, "zones": [], "cards": []}
        pages.append(current)
        y = top_default
        if section not in seen_sections:
            title = next(s["title"] for s in lecture["structure"]["sections"] if s["section_id"] == section)
            lines = wrap(title,mm["heading_width"]*MM,profile["layout_profile"]["pt"]["topic_title_size"],"SFHeadingInter")
            current["section_title"] = lines
            y = mm["topic_title_top"]*MM+len(lines)*profile["layout_profile"]["pt"]["topic_title_leading"]+gap
            seen_sections.add(section)

    for topic in topics:
        tid, section = topic["topic_id"], topic["section_id"]
        units = build_units(lecture, tid, assets, profile, PDF_ROOT)
        # A provided visual starts a new chunk at its exact source-block boundary.
        starts = [0]
        for i in range(1, len(units)):
            if units[i]["block_id"] != units[i-1]["block_id"] and units[i]["block_id"] in media:
                starts.append(i)
        starts.append(len(units))
        for begin, end in zip(starts, starts[1:]):
            tail = units[begin:end]
            cards = media.get(tail[0]["block_id"], [])
            while tail:
                if current is None or current["section_id"] != section:
                    start_page(section)
                first = tid not in seen_topics
                time = topic_timestamp(lecture, tid, source_labels)
                media_height = sum(c["height"]+c["following_gap"] for c in cards)
                divider = divider_layout(topic["title"], time, y+media_height, profile, style) if first else None
                body_top = y+media_height+(divider["height"]+gap if divider else 0)
                capacity = style["bottom"]-body_top
                try:
                    layout = paginate(tail, capacity, style, first_page_only=True)[0]
                except ValueError:
                    if current["zones"]:
                        start_page(section)
                        continue
                    raise ValueError(f"Topic {tid}: visual and first indivisible body element cannot fit; explicit layout decision needed")
                zone = {"topic_id": tid, "divider_layout": divider, "body_top": body_top, "layout": layout}
                if cards:
                    card_y = y
                    for card in cards:
                        current["cards"].append({**card, "top": card_y})
                        card_y += card["height"]+card["following_gap"]
                    cards = []
                current["zones"].append(zone)
                seen_topics.add(tid)
                count = sum(len(c) for c in layout["columns"])
                tail = tail[count:]
                y = body_top+max(layout["heights"])+gap
                if tail:
                    start_page(section)
    return pages


def render(lecture_path, out, *, composition_path=None, handoff_path=None, workflow_mode="direct_skill",
           confirmed_ordinal=None):
    import fitz
    from PIL import Image
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.colors import HexColor
    from verify_bundle import verify
    bundle = verify(PDF_ROOT)
    require(bundle["status"] == "RESOURCES_VERIFIED", "Golden resource bundle is invalid")
    # Input validator also verifies typed content and its sealed hash.
    module_path = PLUGIN / "skills/sf-transcript-to-lecture/scripts/handoff.py"
    spec = importlib.util.spec_from_file_location("sf_render_handoff", module_path)
    handoff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handoff)
    lecture = json.loads(Path(lecture_path).read_text(encoding="utf-8-sig"))
    handoff.validate_lecture(lecture)
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    style = geometry(profile, PDF_ROOT)
    bold_font = "SFHeadingInter"
    if bold_font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_font, str(PDF_ROOT/"assets/fonts/Inter-Bold.ttf")))
    mm, pt = profile["layout_profile"]["mm"], profile["layout_profile"]["pt"]
    tokens = profile["visual_tokens"]
    composition = json.loads(Path(composition_path).read_text(encoding="utf-8-sig")) if composition_path else {"visuals": []}
    require(isinstance(composition.get("visuals"), list), "Missing composition visuals")
    from visual_policy import validate_sources, display_caption
    validate_sources(composition)
    for visual in composition["visuals"]:
        visual["caption"] = display_caption(visual.get("caption", ""))
    workflow = {"mode": workflow_mode, "full_cycle_handoff":
                {**ref(handoff_path), "kind": "full_cycle_handoff"} if handoff_path else None}
    check_workflow({"workflow": workflow}, {"lecture": ref(lecture_path)})
    out = Path(out).resolve()
    require(not out.exists(), "Use a new output directory")
    out.mkdir(parents=True)
    assets = {}
    for block in lecture["blocks"]:
        for _, ri, formula in formula_items(block):
            target = out / "math" / formula["formula_id"]
            compile_math(formula["latex"], "display" if ri is None else "inline", style["size"], target)
            assets[formula["formula_id"]] = ref(target / "math.json")
    pages = layout_body(lecture, composition, assets, profile, style)
    topics = lecture["structure"]["topics"]
    # Page-label width is measured before final TOC pagination; body destinations
    # depend on the resulting TOC page count. No hard-coded page-number capacity.
    number_width = mm["toc_number_field_min"]*MM
    max_passes = json.loads((PDF_ROOT/"references"/profile["document_grammar_contract"]).read_text(
        encoding="utf-8"))["navigation"]["fixed_point_max_passes"]
    for _ in range(max_passes):
        toc_pages = layout_toc(lecture["structure"], profile, number_width)
        offset = 1+len(toc_pages)
        required_width = text_width("стр. "+str(offset+len(pages)),pt["toc_page_size"])
        if required_width <= number_width:
            break
        number_width = required_width
    else:
        raise ValueError("TOC page-label width did not converge; diagnose pagination without changing the reference style")
    destinations = {}
    for i, page in enumerate(pages, offset+1):
        for zone in page["zones"]:
            destinations.setdefault(zone["topic_id"], i)
    total = offset+len(pages)
    bookmarks = [[1, "Содержание", 2]]
    for section in lecture["structure"]["sections"]:
        children = [t for t in topics if t["section_id"] == section["section_id"]]
        if children:
            bookmarks.append([1, section["title"], destinations[children[0]["topic_id"]]])
            bookmarks.extend([2, t["title"], destinations[t["topic_id"]]] for t in children)
    plan = {"pages": total, "text": [], "math": [], "flow": [], "visuals": [], "links": [],
            "bookmarks": bookmarks}
    width, height = [v*MM for v in profile["surface"]]
    canvas = Canvas(str(out/"base.pdf"), pagesize=(width,height))
    zones = []
    current_number = 1

    def service(value, x, top, size, color="text", extra=None, bold=False):
        font = bold_font if bold else FONT_NAME
        canvas.setFillColor(HexColor(tokens.get(color, color)))
        canvas.setFont(font, size)
        asc, desc = pdfmetrics.getAscent(font,size), -pdfmetrics.getDescent(font,size)
        canvas.drawString(x,height-top-asc,value)
        box = [x-.05,top-.15,x+pdfmetrics.stringWidth(value,font,size)+.05,top+asc+desc+.15]
        plan["text"].append({"page":current_number,"text":value,"bbox":box,**(extra or {})})
        return box

    def image_asset(name, x, top, w, h=None):
        path = PDF_ROOT/profile["brand_assets"][name]["path"]
        require(ref(path)["sha256"] == profile["brand_assets"][name]["sha256"], "Changed brand asset")
        if h is None:
            with Image.open(path) as im: h=w*im.height/im.width
        canvas.drawImage(str(path),x,height-top-h,width=w,height=h,mask="auto")

    def internal(section=None, *, pattern=False):
        canvas.setFillColor(HexColor(tokens["canvas"]))
        canvas.rect(0,0,width,height,fill=1,stroke=0)
        if pattern:
            image_asset("internal_pattern",mm["internal_pattern_x"]*MM,mm["internal_pattern_y"]*MM,
                        mm["internal_pattern_width"]*MM,mm["internal_pattern_height"]*MM)
        image_asset("internal_lockup",mm["internal_sf_x"]*MM,mm["internal_sf_y"]*MM,mm["internal_sf_width"]*MM)
        if section:
            sections=lecture["structure"]["sections"]
            index=next(i for i,s in enumerate(sections) if s["section_id"]==section)
            canvas.setFillColor(HexColor(tokens[profile["section_rail"]["cycle"][index%len(profile["section_rail"]["cycle"])]]))
            canvas.roundRect(mm["rail_x"]*MM,mm["rail_bottom_margin"]*MM,mm["rail_width"]*MM,
                             height-(mm["rail_top"]+mm["rail_bottom_margin"])*MM,mm["rail_radius"]*MM,fill=1,stroke=0)
            # Header title must stay clear of the logo.
            for i,line in enumerate(wrap(sections[index]["title"],mm["heading_width"]*MM,pt["small_label_size"])):
                require(i < 2, "Section header requires a layout decision")
                service(line,style["x"][0],mm["section_label_top"]*MM+i*pt["small_label_leading"],pt["small_label_size"],"brand")

    def footer(topic=None):
        canvas.setStrokeColor(HexColor(tokens["border"]))
        canvas.setLineWidth(pt["rule_width"])
        canvas.line(style["x"][0],height-mm["footer_rule_y"]*MM,mm["content_right"]*MM,height-mm["footer_rule_y"]*MM)
        x, y = style["x"][0], mm["footer_text_top"]*MM
        links = [("К содержанию",2)]
        if current_number > offset+1: links.insert(0,("Назад",current_number-1))
        if topic: links.append(("К началу темы",destinations[topic]))
        if current_number < total: links.append(("Вперед",current_number+1))
        for label,target in links:
            box=service(label,x,y,pt["footer_size"],"link")
            plan["links"].append({"page":current_number,"target_page":target,"bbox":box})
            x=box[2]+mm["footer_link_gap"]*MM
        number=str(current_number).zfill(profile["layout_profile"]["scalar"]["minimum_page_digits"])
        service(number,mm["content_right"]*MM-text_width(number,pt["page_number_size"]),y,pt["page_number_size"],"muted")

    canvas.setFillColor(HexColor(tokens["brand"]))
    canvas.rect(0,0,width,height,fill=1,stroke=0)
    canvas.saveState()
    canvas.setFillAlpha(profile["layout_profile"]["scalar"]["cover_pattern_opacity"])
    image_asset("cover_pattern",0,0,width,height)
    canvas.restoreState()
    for name,x,w in (("partner_mincifry","mincifry_x","mincifry_width"),("partner_skolkovo","skolkovo_x","skolkovo_width"),
                     ("cover_lockup","cover_sf_x","cover_sf_width")):
        image_asset(name,mm[x]*MM,mm["cover_header_top"]*MM,mm[w]*MM)
    title=cover_title(lecture["title"], confirmed_ordinal=confirmed_ordinal)
    lines=wrap(title,mm["cover_title_width"]*MM,pt["cover_title_size"],bold_font)
    title_top=mm["cover_title_bottom"]*MM-len(lines)*pt["cover_title_leading"]
    require(title_top > (mm["cover_header_top"]+mm["cover_header_height"]+mm["cover_label_gap"])*MM,
            "Cover title exceeds available frame")
    service("ЛЕКЦИОННЫЙ КОНСПЕКТ",mm["cover_title_x"]*MM,title_top-mm["cover_label_gap"]*MM,pt["small_label_size"],"#FFFFFF")
    for i,line in enumerate(lines):
        service(line,mm["cover_title_x"]*MM,title_top+i*pt["cover_title_leading"],pt["cover_title_size"],"#FFFFFF",bold=True)
    canvas.setStrokeColor(HexColor("#FFFFFF"))
    canvas.line(mm["cover_title_x"]*MM,height-mm["cover_rule_y"]*MM,
                (mm["cover_title_x"]+mm["cover_rule_width"])*MM,height-mm["cover_rule_y"]*MM)
    canvas.showPage()
    for entries in toc_pages:
        current_number+=1
        internal()
        service("Содержание",mm["toc_margin_x"]*MM,mm["toc_title_y"]*MM,pt["topic_title_size"],bold=True)
        links = draw_toc_entries(canvas,entries,lecture["structure"],destinations,profile,service)
        plan["links"].extend({"page":current_number,**link} for link in links)
        canvas.showPage()
    for page in pages:
        current_number+=1
        internal(page["section_id"],pattern=not bool(page.get("section_title")))
        for i,line in enumerate(page.get("section_title",[])):
            service(line,style["x"][0],mm["topic_title_top"]*MM+i*pt["topic_title_leading"],
                    pt["topic_title_size"],bold=True)
        for card in page["cards"]:
            visual=card["visual"]
            card_width=mm["card_width"]*MM
            left=style["x"][0]+(mm["content_width"]*MM-card_width)/2
            top=card["top"]
            canvas.setFillColor(HexColor(tokens["surface"]))
            canvas.setStrokeColor(HexColor(tokens["border"]))
            canvas.roundRect(left,height-top-card["height"],card_width,card["height"],mm["card_radius"]*MM,fill=1,stroke=1)
            ix=left+(card_width-card["width"])/2
            iy=top+mm["card_padding"]*MM
            canvas.drawImage(str(checked(visual["image"])),ix,height-iy-card["image_height"],width=card["width"],height=card["image_height"],mask="auto")
            plan["visuals"].append({"visual_id":visual["visual_id"],"page":current_number,
                                   "bbox":[ix,iy,ix+card["width"],iy+card["image_height"]]})
            caption_start=len(plan["text"])
            cy=iy+card["image_height"]+mm["image_caption_gap"]*MM
            for i,line in enumerate(card["caption_lines"]):
                service(line,left+mm["card_padding"]*MM,cy+i*pt["caption_leading"],pt["caption_size"])
            rows=plan["text"][caption_start:]
            plan["text"][caption_start:]=[{"page":current_number,"visual_id":visual["visual_id"],"text":visual["caption"],
                                          "bbox":[min(r["bbox"][0] for r in rows),rows[0]["bbox"][1],
                                                  max(r["bbox"][2] for r in rows),rows[-1]["bbox"][3]]}] if rows else []
        for zone in page["zones"]:
            divider=None
            data=zone["divider_layout"]
            if data:
                boxes=[service(line,style["x"][0],data["top"]+i*data["leading"],data["size"],"brand")
                       for i,line in enumerate(data["lines"])]
                title_box=[boxes[0][0],boxes[0][1],max(b[2] for b in boxes),boxes[-1][3]]
                right=style["x"][1]+style["width"]
                pill=None
                bar=profile["topic_bar"]
                rule_y=data["top"]+data["height"]/2
                rule_left=title_box[2]+bar["line_clearance_mm"]*MM
                rule_right=right-data["pill_width"]-(bar["line_clearance_mm"]*MM if data["time"] else 0)
                canvas.setStrokeColor(HexColor(tokens["border"]))
                canvas.setLineWidth(bar["line_width_pt"])
                canvas.line(rule_left,height-rule_y,rule_right,height-rule_y)
                if data["time"]:
                    ph=bar["pill_height_mm"]*MM
                    pill=[right-data["pill_width"],rule_y-ph/2,right,rule_y+ph/2]
                    canvas.setFillColor(HexColor(tokens["blue"]))
                    canvas.roundRect(pill[0],height-pill[3],data["pill_width"],ph,bar["pill_radius_mm"]*MM,fill=1,stroke=0)
                    asc=pdfmetrics.getAscent(FONT_NAME,data["size"])
                    desc=-pdfmetrics.getDescent(FONT_NAME,data["size"])
                    service(data["time"],pill[0]+bar["pill_padding_mm"]*MM,rule_y-(asc+desc)/2,data["size"],"link")
                divider={"title_text":next(t["title"] for t in topics if t["topic_id"]==zone["topic_id"]),
                         "title_bbox":title_box,"rule_bbox":[rule_left,rule_y-.1,rule_right,rule_y+.1],
                         "time_pill_bbox":pill,"time_text":data["time"]}
            rows=draw_body(canvas,zone["layout"],current_number,zone["body_top"],style,assets)
            for key in ("text","math","flow"): plan[key].extend(rows[key])
            bottom=zone["body_top"]+max(zone["layout"]["heights"])+.1
            columns=[{"column":col,"bbox":[style["x"][col-1],zone["body_top"],style["x"][col-1]+style["width"],bottom],
                      "line_ids":[r["line_id"] for r in rows["flow"] if r["column"]==col]} for col in (1,2)]
            zones.append({"zone_id":f"zone-{len(zones)+1}","page":current_number,"topic_id":zone["topic_id"],
                          "divider":divider,"columns":columns,
                          "balance":{"status":zone["layout"]["balance_reason"],"reason":"Measured best legal split"}})
        footer(page["zones"][0]["topic_id"])
        canvas.showPage()
    canvas.save()
    insert_math(out/"base.pdf",out/"math-pages.pdf",plan["math"])
    with fitz.open(out/"math-pages.pdf") as document:
        document.set_toc(plan["bookmarks"])
        for link in plan["links"]:
            document[link["page"]-1].insert_link({"kind":fitz.LINK_GOTO,"from":fitz.Rect(link["bbox"]),
                                                "page":link["target_page"]-1})
        document.save(out/"candidate.pdf",garbage=3,deflate=True)
    artifacts={"pdf":ref(out/"candidate.pdf"),"lecture":ref(lecture_path),"profile":ref(PROFILE),
               "composition":write(out/"composition.json",composition),"render_plan":write(out/"render-plan.json",plan)}
    zone_ref=write(out/"golden-zone-plan.json",{"schema_version":"1.0","artifacts":
                   {k:artifacts[k] for k in ("pdf","profile","render_plan")},"zones":zones})
    manifest={"schema_version":"2.1","artifacts":artifacts,"workflow":workflow,"zone_plan":zone_ref,
              "text_review":None,"visual_review":None}
    manifest_path=out/"candidate-manifest.json"
    write(manifest_path,manifest)
    result=check(manifest_path)
    write(out/"qa-report.json",result)
    return {"manifest":str(manifest_path),"pdf":artifacts["pdf"],"pages":total,
            "status":"REVIEW_REQUIRED","reason":"Mechanical checks passed; full visual review and human acceptance remain"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lecture",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--composition",type=Path)
    parser.add_argument("--handoff",type=Path)
    parser.add_argument("--workflow",choices=["direct_skill","full_cycle"],required=True)
    parser.add_argument("--confirmed-ordinal",help="Explicitly confirmed lesson-number prefix to omit on the cover")
    args=parser.parse_args()
    try:
        print(json.dumps(render(args.lecture,args.out,composition_path=args.composition,
                                handoff_path=args.handoff,workflow_mode=args.workflow,
                                confirmed_ordinal=args.confirmed_ordinal),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
        print(json.dumps({"status":"BLOCKED","reason":str(error)},ensure_ascii=False))
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
