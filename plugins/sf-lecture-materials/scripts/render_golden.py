"""Reusable Golden A4 renderer: typed lecture, supplied raster visuals, zones and navigation.

No editorial generation, installation or acceptance. Output is a review candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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


def divider_layout(title, time, y, profile, style):
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
        scale = min(mm["image_max_width"]*MM/im.width, mm["image_max_height"]*MM/im.height)
        width, height = im.width*scale, im.height*scale
    lines = wrap(visual["caption"], mm["image_max_width"]*MM, pt["caption_size"])
    total = 2*mm["card_padding"]*MM+height+mm["image_caption_gap"]*MM+len(lines)*pt["caption_leading"]
    return {"visual": visual, "width": width, "image_height": height, "height": total, "caption_lines": lines}


def layout_body(lecture, composition, assets, profile, style):
    mm = profile["layout_profile"]["mm"]
    top_default = mm["topic_title_top"]*MM+profile["layout_profile"]["pt"]["small_label_leading"]
    gap = mm["heading_gap"]*MM
    placements = lecture["structure"]["placements"]
    topics = lecture["structure"]["topics"]
    by_block = {p["text_block_id"]: p for p in placements}
    media = {}
    for visual in composition["visuals"]:
        require(visual.get("role") and visual.get("caption") and visual.get("text_block_ids"), "Incomplete visual")
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
                time_values = [p.get("timestamp_range") for p in placements if p["topic_id"] == tid]
                # Never synthesize a combined time interval from unrelated sources.
                time = time_values[0] if len(time_values) == 1 and time_values[0] else ""
                media_height = sum(c["height"]+mm["card_following_gap"]*MM for c in cards)
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
                        card_y += card["height"]+mm["card_following_gap"]*MM
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
    # Content entries wrap using measured font metrics and may span multiple TOC pages.
    toc_pages, toc, ty, tc = [], [], mm["toc_top"]*MM, 0
    def next_toc_column():
        nonlocal toc, tc, ty
        if tc == 0:
            tc = 1
        else:
            toc_pages.append(toc)
            toc, tc = [], 0
        ty = mm["toc_top"]*MM

    for section in lecture["structure"]["sections"]:
        section_topics = [t for t in topics if t["section_id"] == section["section_id"]]
        if not section_topics:
            continue
        name_width = (mm["toc_column_width"]-mm["toc_number_field_min"]-mm["toc_number_gap"]-mm["toc_section_text_offset"])*MM
        entries = []
        for topic in section_topics:
            lines = wrap(topic["title"],name_width,pt["toc_topic_size"])
            entries.append({"topic":topic,"lines":lines,"section_heading":False,
                            "height":len(lines)*pt["toc_topic_leading"]+mm["toc_topic_gap"]*MM})
        heading = wrap(section["title"],name_width,pt["toc_section_size"],bold_font)
        head_height = len(heading)*pt["toc_section_leading"]+mm["toc_first_topic_gap"]*MM
        available = (mm["toc_bottom"]-mm["toc_top"])*MM
        total_height = head_height+sum(e["height"] for e in entries)
        if total_height <= available and ty+total_height > mm["toc_bottom"]*MM:
            next_toc_column()
        if ty+head_height+entries[0]["height"] > mm["toc_bottom"]*MM:
            next_toc_column()
        toc.append({"topic":section_topics[0],"lines":heading,"section_heading":True,"top":ty,"column":tc})
        ty += head_height
        for entry in entries:
            require(head_height+entry["height"] <= available,"TOC heading and entry exceed column")
            if ty+entry["height"] > mm["toc_bottom"]*MM:
                next_toc_column()
                continued=wrap(section["title"]+" (продолжение)",name_width,pt["toc_section_size"],bold_font)
                toc.append({"topic":entry["topic"],"lines":continued,"section_heading":True,"top":ty,"column":tc})
                ty += len(continued)*pt["toc_section_leading"]+mm["toc_first_topic_gap"]*MM
                require(ty+entry["height"] <= mm["toc_bottom"]*MM,"TOC continuation exceeds column")
            toc.append({**entry,"top":ty,"column":tc})
            ty += entry["height"]
        ty += mm["toc_section_gap"]*MM
    if toc: toc_pages.append(toc)
    offset = 1+len(toc_pages)
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
                index=next(i for i,s in enumerate(lecture["structure"]["sections"]) if s["section_id"]==entry["topic"]["section_id"])
                label=str(index+1).zfill(2)
                service(label,x+(diameter-text_width(label,pt["toc_page_size"]))/2,
                        entry["top"]+(diameter-pt["toc_page_size"])/2,pt["toc_page_size"],"#FFFFFF")
            else:
                diameter=mm["toc_bullet_diameter"]*MM
                canvas.circle(x+diameter/2,height-entry["top"]-size/2,diameter/2,fill=1,stroke=0)
            boxes=[service(line,text_x,entry["top"]+i*leading,size,"text" if is_heading else "brand",bold=is_heading)
                   for i,line in enumerate(entry["lines"])]
            target=destinations[entry["topic"]["topic_id"]]
            number=str(target)
            service(number,x+mm["toc_column_width"]*MM-text_width(number,pt["toc_page_size"]),entry["top"],pt["toc_page_size"])
            box=[boxes[0][0],boxes[0][1],x+mm["toc_column_width"]*MM,boxes[-1][3]]
            plan["links"].append({"page":current_number,"target_page":target,"bbox":box})
        canvas.showPage()
    for page in pages:
        current_number+=1
        internal(page["section_id"],pattern=current_number > offset+1)
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
                                                  max(r["bbox"][2] for r in rows),rows[-1]["bbox"][3]]}]
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
