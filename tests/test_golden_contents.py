"""Regression coverage for the user-confirmed Golden TOC appearance."""
import io
import json
import unittest
import fitz
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from test_json_content import ROOT
from render_golden import layout_toc, draw_toc_entries, PROFILE, PDF_ROOT
from pdf_flow import geometry, FONT_NAME, MM, text_width

class GoldenContentsChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=json.loads(PROFILE.read_text(encoding="utf-8"))
        geometry(cls.profile,PDF_ROOT)
        pdfmetrics.registerFont(TTFont("SFHeadingInter",str(PDF_ROOT/"assets/fonts/Inter-Bold.ttf")))
        cls.mm=cls.profile["layout_profile"]["mm"]
        cls.pt=cls.profile["layout_profile"]["pt"]

    def structure(self, sections=6, topics=4):
        return {"sections":[{"section_id":str(s),"title":"Раздел "+str(s+1)} for s in range(sections)],
                "topics":[{"section_id":str(s),"topic_id":f"{s}-{t}",
                           "title":"Тема с подробным объяснением последовательности действий и результата"}
                          for s in range(sections) for t in range(topics)]}

    def test_balanced_groups_keep_order(self):
        structure=self.structure()
        pages=layout_toc(structure,self.profile)
        self.assertEqual(len(pages),1)
        headings=[e for e in pages[0] if e["section_heading"]]
        self.assertEqual([e["column"] for e in headings],[0,0,0,1,1,1])
        self.assertEqual([e["topic"]["topic_id"] for e in pages[0] if not e["section_heading"]],
                         [t["topic_id"] for t in structure["topics"]])
        self.assertEqual(headings[0]["top"],headings[3]["top"])

    def test_long_section_continues_with_heading_without_loss(self):
        structure=self.structure(1,85)
        pages=layout_toc(structure,self.profile)
        self.assertGreater(len(pages),1)
        found=[]
        for page in pages:
            for col in (0,1):
                entries=[e for e in page if e["column"]==col]
                if not entries:
                    continue
                self.assertTrue(entries[0]["section_heading"])
                self.assertFalse(entries[1]["section_heading"])
                for e in entries:
                    self.assertLessEqual(e["top"]+e["height"],self.mm["toc_bottom"]*MM+.01)
                    if not e["section_heading"]:
                        found.append(e["topic"]["topic_id"])
        self.assertEqual(found,[t["topic_id"] for t in structure["topics"]])
        self.assertTrue(any("продолжение" in " ".join(e["lines"]) for p in pages[1:] for e in p))

    def test_actual_underlines_dividers_and_long_page_labels(self):
        structure=self.structure(2,2)
        destinations={t["topic_id"]:1000+i for i,t in enumerate(structure["topics"])}
        number_width=max(text_width("стр. "+str(v),self.pt["toc_page_size"]) for v in destinations.values())
        entries=layout_toc(structure,self.profile,number_width)[0]
        stream=io.BytesIO()
        height=297*MM
        canvas=Canvas(stream,pagesize=(210*MM,height))
        printed=[]
        def service(value,x,top,size,color="text",extra=None,bold=False):
            font="SFHeadingInter" if bold else FONT_NAME
            canvas.setFillColor(HexColor(self.profile["visual_tokens"].get(color,color)))
            canvas.setFont(font,size)
            asc,desc=pdfmetrics.getAscent(font,size),-pdfmetrics.getDescent(font,size)
            canvas.drawString(x,height-top-asc,value)
            box=[x-.05,top-.15,x+pdfmetrics.stringWidth(value,font,size)+.05,top+asc+desc+.15]
            printed.append((value,box,color,bold))
            return box
        links=draw_toc_entries(canvas,entries,structure,destinations,self.profile,service)
        canvas.showPage()
        canvas.save()
        with fitz.open(stream=stream.getvalue(),filetype="pdf") as doc:
            page=doc[0]
            spans=[s for b in page.get_text("dict")["blocks"] if b["type"]==0 for l in b["lines"] for s in l["spans"]]
            labels=[s for s in spans if s["text"].startswith("стр. ")]
            self.assertEqual(len(labels),4)
            self.assertTrue(all(s["color"]==0x777777 for s in labels))
            self.assertEqual(len(links),4)
            lines=[(d,item) for d in page.get_drawings() for item in d["items"] if item[0]=="l"]
            gray=[item for d,item in lines if d["color"] and max(d["color"])-min(d["color"])<.001]
            self.assertEqual(len(gray),2)
            blue=[item for d,item in lines if d["color"] and d["color"][2]>.9 and d["color"][0]<.01]
            expected=sum(len(e["lines"]) for e in entries if not e["section_heading"])
            self.assertEqual(len(blue),expected)
            for value,box,color,bold in printed:
                self.assertEqual(page.get_textbox(fitz.Rect(box)).strip(),value)
            for label in labels:
                self.assertTrue(any(abs(label["bbox"][2]-right*MM)<.1 for right in (101,196)))

    def test_pinned_golden_has_reference_toc_and_actual_destinations(self):
        with fitz.open(PDF_ROOT/"assets/golden/golden.pdf") as doc:
            page=doc[1]
            labels=[s for b in page.get_text("dict")["blocks"] if b["type"]==0
                    for l in b["lines"] for s in l["spans"] if s["text"].startswith("стр. ")]
            links=page.get_links()
            topics=[entry for entry in doc.get_toc() if entry[0]==2]
            self.assertEqual(len(labels),len(topics))
            self.assertEqual(len(links),len(topics))
            for label in labels:
                matching=[link for link in links if abs(link["from"].y0-label["bbox"][1])<1
                          and link["from"].contains(fitz.Point(label["bbox"][0]+1,label["bbox"][1]+1))]
                self.assertEqual(len(matching),1)
                self.assertEqual(int(label["text"].split()[-1]),matching[0]["page"]+1)

if __name__=="__main__":
    unittest.main()
