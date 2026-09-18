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
        composition_path=None
        if composition is not None:
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
        composition={"visuals":[{"visual_id":"v1","image":ref(image),"source":ref(image),
                                "text_block_ids":["b1"],"role":"Synthetic diagram",
                                "caption":"Исходное изображение для проверки размещения."}]}
        path,data=self.generate(composition=composition)
        self.assertEqual(check(path)["visuals"],1)

if __name__=="__main__":
    unittest.main()
