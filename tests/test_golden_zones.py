"""Actual PDFs from the shipped renderer, including multiple zones and continuations."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import fitz
from test_json_content import ROOT, H, fixture, ref
from render_golden import render
from verify_candidate import check
from verify_golden_zones import check as check_zones


class GoldenZoneChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        sources,draft=fixture(self.root/"source.json")
        draft["blocks"][0]["content"]=[{"type":"paragraph","runs":[{"type":"text","text":
            "Рабочий документ содержит последовательность действий и объяснение результата. "*3}]} for _ in range(2)]
        self.lecture,_=H.build(sources,draft)
        block=copy.deepcopy(self.lecture["blocks"][0])
        block["text_block_id"]="b2"
        self.lecture["blocks"].append(block)
        self.lecture["source_locators"]["b2"]=copy.deepcopy(self.lecture["source_locators"]["b1"])
        self.lecture["structure"]["topics"].append({"topic_id":"second","section_id":"sec","title":"Следующая тема"})
        self.lecture["structure"]["placements"].append({**self.lecture["structure"]["placements"][0],
                                                        "text_block_id":"b2","topic_id":"second"})

    def generate(self, *, composition=None, workflow="direct_skill"):
        self.lecture["content_hash"]=H.digest({k:v for k,v in self.lecture.items() if k!="content_hash"})
        path=self.root/"lecture.json"
        path.write_text(json.dumps(self.lecture,ensure_ascii=False),encoding="utf-8")
        composition=copy.deepcopy(composition) if composition is not None else {"visuals":[]}
        uris=sorted({a["source_uri"] for anchors in self.lecture["source_locators"].values()
                     for a in anchors if a.get("start_ms") is not None})
        composition.setdefault("time_sources",[
            {"source_uri":uri,"label":f"Видео {i}","basis":"Explicit synthetic fixture correspondence"}
            for i,uri in enumerate(uris,1)])
        composition_path=self.root/"composition-input.json"
        composition_path.write_text(json.dumps(composition,ensure_ascii=False),encoding="utf-8")
        result=render(path,self.root/"output",composition_path=composition_path,workflow_mode=workflow)
        manifest=Path(result["manifest"])
        return manifest,json.loads(manifest.read_text(encoding="utf-8"))

    def change_zones(self, manifest, data, change):
        path=Path(data["zone_plan"]["path"])
        zones=json.loads(path.read_text(encoding="utf-8"))
        change(zones)
        path.write_text(json.dumps(zones,ensure_ascii=False),encoding="utf-8")
        data["zone_plan"]=ref(path)
        manifest.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")

    def test_multiple_zones_share_page_and_pass_complete_check(self):
        path,data=self.generate()
        result=check(path)
        self.assertEqual(result["status"],"PDF_MECHANICS_VALIDATED")
        self.assertEqual(result["zones"],2)
        self.assertEqual(result["pages"],3)
        self.assertGreater(result["links"],0)
        self.assertEqual(check_zones(path,data["zone_plan"]["path"])["workflow"],"DIRECT_SKILL_NO_FACT_CHECK_GATE")

    def test_each_section_starts_without_pattern(self):
        self.lecture["structure"]["sections"].append(dict(section_id="sec2",number="2",title="Второй раздел"))
        self.lecture["structure"]["topics"][1]["section_id"]="sec2"
        self.lecture["structure"]["placements"][1]["section_id"]="sec2"
        path,data=self.generate()
        with fitz.open(data["artifacts"]["pdf"]["path"]) as doc:
            starts=[row[2] for row in doc.get_toc() if row[0]==1 and row[1]!="Содержание"]
            self.assertEqual(len(starts),2)
            for number in starts:
                self.assertFalse(any((im["width"],im["height"])==(1392,1883)
                                     for im in doc[number-1].get_image_info()))

    def test_file_extensions_in_body_survive_complete_validation(self):
        self.lecture["blocks"][0]["content"]=[{"type":"paragraph","runs":[{"type":"text","text":
            "Откройте файл example.pdf. Сохраните презентацию в формате .pptx. Прочитайте notes.txt."}]}]
        path,data=self.generate()
        self.assertEqual(check(path)["status"],"PDF_MECHANICS_VALIDATED")
        with fitz.open(data["artifacts"]["pdf"]["path"]) as doc:
            text=" ".join(p.get_text() for p in doc)
            self.assertIn("example.pdf",text)
            self.assertIn("Видео 1",text)

    def test_authorized_diagram_passes_renderer_and_validator(self):
        from PIL import Image
        image=self.root/"authored.png"
        Image.new("RGB",(320,160),"white").save(image)
        composition={"visuals":[{"visual_id":"authored","origin":"authored",
            "image":ref(image),"source":ref(image),"text_block_ids":["b1"],
            "role":"explanation","caption":"Источник: Авторская схема\nСлайд: не применимо.\nТема: Последовательность проверки",
            "authoring":{"authorization":"Explicit synthetic test permission","basis":"Selected block b1"},
            "lecturer_photo_review":"absent"}]}
        path,data=self.generate(composition=composition)
        self.assertEqual(check(path)["status"],"PDF_MECHANICS_VALIDATED")

    def test_long_source_wraps_before_explicit_blank_slide_field(self):
        from PIL import Image
        from visual_policy import caption_fields
        image=self.root/"visual.png"
        Image.new("RGB",(400,180),"navy").save(image)
        source_name="Название исходного учебного материала " * 5
        caption=f"Источник: {source_name.strip()}.pdf\nСлайд:\nТема: Разбор example.pdf"
        composition={"source_root":str(self.root),
            "source_inventory":[{**ref(image),"role":"original_visual"}],
            "visuals":[{"visual_id":"wrapped","image":ref(image),"source":ref(image),
                "source_locator":"whole supplied image","lecturer_photo_review":"absent",
                "text_block_ids":["b1"],"role":"Synthetic example","caption":caption,
                "blank_caption_authorization":"User explicitly requested an empty slide field"}]}
        path,data=self.generate(composition=composition)
        self.assertEqual(check(path)["visuals"],1)
        plan=json.loads(Path(data["artifacts"]["render_plan"]["path"]).read_text(encoding="utf-8"))
        row=next(r for r in plan["text"] if r.get("visual_id")=="wrapped")
        with fitz.open(data["artifacts"]["pdf"]["path"]) as doc:
            actual=doc[row["page"]-1].get_textbox(row["bbox"])
        fields=caption_fields(actual,"Explicit synthetic blank field permission")
        self.assertIn("\n",fields[0])
        self.assertEqual(" ".join(fields[0].split()),source_name.strip())
        self.assertEqual(fields[1],"")
        self.assertEqual(fields[2],"Разбор example.pdf")

    def test_short_indivisible_tail_uses_empty_right_column(self):
        self.lecture["blocks"][1]["content"]=[{"type":"paragraph","runs":[{"type":"text","text":"Короткое определение."}]}]
        path,data=self.generate()
        zones=json.loads(Path(data["zone_plan"]["path"]).read_text(encoding="utf-8"))["zones"]
        self.assertEqual(zones[-1]["balance"]["status"],"INDIVISIBLE_CONTENT")
        self.assertEqual(zones[-1]["columns"][1]["line_ids"],[])
        self.assertEqual(check(path)["zones"],2)

    def test_continuation_has_no_repeated_title(self):
        self.lecture["blocks"][0]["content"]*=16
        path,data=self.generate()
        zones=json.loads(Path(data["zone_plan"]["path"]).read_text(encoding="utf-8"))["zones"]
        continuation=[z for z in zones if z["topic_id"]=="topic"][1:]
        self.assertTrue(continuation)
        self.assertTrue(all(z["divider"] is None for z in continuation))
        check(path)

    def test_omitted_entire_topic_is_rejected(self):
        path,data=self.generate()
        self.change_zones(path,data,lambda z:z["zones"].pop())
        with self.assertRaisesRegex(ValueError,"every body line"):
            check(path)

    def test_false_balance_declaration_is_rejected(self):
        path,data=self.generate()
        self.change_zones(path,data,lambda z:z["zones"][0]["balance"].update(status="INDIVISIBLE_CONTENT"))
        with self.assertRaisesRegex(ValueError,"balance"):
            check(path)

    def test_shifted_column_is_rejected(self):
        path,data=self.generate()
        def change(z):
            z["zones"][0]["columns"][1]["bbox"][0]+=4
        self.change_zones(path,data,change)
        with self.assertRaisesRegex(ValueError,"Column 2"):
            check(path)

    def test_full_cycle_requires_handoff_before_rendering(self):
        with self.assertRaisesRegex(ValueError,"handoff"):
            self.generate(workflow="full_cycle")
        self.assertFalse((self.root/"output").exists())

    def test_supplied_visual_and_caption_are_preserved(self):
        from PIL import Image
        image=self.root/"visual.png"
        Image.new("RGB",(400,180),"navy").save(image)
        composition={"source_root":str(self.root),"source_inventory":[{**ref(image),"role":"original_visual"}],"visuals":[{"lecturer_photo_review":"absent","source_locator":"whole supplied image","visual_id":"v1","image":ref(image),"source":ref(image),
                                "text_block_ids":["b1"],"role":"Synthetic diagram",
                                "caption":"Источник: Учебная презентация.pdf\nСлайд 12.\nТема: Проверка размещения example.pdf"}]}
        path,data=self.generate(composition=composition)
        self.assertEqual(check(path)["visuals"],1)
        with fitz.open(data["artifacts"]["pdf"]["path"]) as doc:
            lines=[line.strip() for page in doc for line in page.get_text().splitlines()]
            start=lines.index("Источник: Учебная презентация")
            self.assertEqual(lines[start:start+3], [
                "Источник: Учебная презентация", "Слайд 12.", "Тема: Проверка размещения example.pdf"])

    def test_local_card_gap_is_bounded_and_requires_a_reason(self):
        from PIL import Image
        from render_golden import PDF_ROOT, PROFILE, visual_layout
        from pdf_flow import MM, font_metrics
        image=self.root/"visual.png"
        Image.new("RGB",(400,180),"navy").save(image)
        visual={"image":ref(image),"source":ref(image),
                "caption":"Источник: Учебная презентация\nСлайд 3.\nТема: Пример"}
        profile=json.loads(PROFILE.read_text(encoding="utf-8"))
        font_metrics(PDF_ROOT, profile["layout_profile"]["pt"]["caption_size"])
        self.assertAlmostEqual(visual_layout(visual,profile)["following_gap"],5*MM)
        visual.update(following_gap_mm=3.6,layout_reason="Keep a short related paragraph on the page")
        self.assertAlmostEqual(visual_layout(visual,profile)["following_gap"],3.6*MM)
        for bad in (2.9,5.1,float("nan"),True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                visual_layout({**visual,"following_gap_mm":bad},profile)
        with self.assertRaises(ValueError):
            visual_layout({**visual,"layout_reason":""},profile)

if __name__=="__main__":
    unittest.main()
