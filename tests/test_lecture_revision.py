"""Regression cases from the lecture review, without machine-local paths."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_json_content import ROOT, ref
from pdf_flow import wrap_atoms
from render_golden import display_timestamp, topic_timestamp, time_source_labels, visual_layout, PROFILE


class LectureRevisionChecks(unittest.TestCase):
    def test_whole_word_can_fit_after_overwide_hyphen(self):
        atom = dict(type="text", text="abcd", start=0, end=4, hyphen_points=[3], width=4)
        style = dict(size=9.5, width=4.5, indent=0, min_space_ratio=.8, max_space_ratio=1.5)
        with patch("pdf_flow.text_width", side_effect=lambda s, size: 2 if s == "-" else len(s)):
            lines = wrap_atoms([atom], style)
        self.assertEqual(len(lines), 1)
        self.assertEqual("".join(c["text"] for c in lines[0]), "abcd")
        self.assertFalse(lines[0][-1]["hyphen"])

    def test_indivisible_overflow_still_rejected(self):
        style = dict(size=9.5, width=4.5, indent=0, min_space_ratio=.8, max_space_ratio=1.5)
        with patch("pdf_flow.text_width", return_value=1), self.assertRaises(ValueError):
            wrap_atoms([dict(type="math", width=20)], style)

    def lecture(self):
        return dict(structure=dict(placements=[dict(text_block_id="a", topic_id="t"),
                                               dict(text_block_id="b", topic_id="t")]),
                    source_locators={"a":[dict(source_uri="part1.txt", start_ms=30679, end_ms=138040)],
                                     "b":[dict(source_uri="part1.txt", start_ms=135319, end_ms=222239)]})

    def test_display_truncates_fraction_keeps_hours(self):
        self.assertEqual(display_timestamp("01:59:59.999 — 02:00:00.001"), "01:59:59 — 02:00:00")
        self.assertEqual(display_timestamp("00:00:00 — 00:01:00"), "00:00:00 — 00:01:00")
        self.assertEqual(display_timestamp(""), "")

    def test_adjacent_same_source_merged_without_mutating_anchors(self):
        lecture=self.lecture();before=copy.deepcopy(lecture)
        self.assertEqual(topic_timestamp(lecture,"t"), "00:00:30 — 00:03:42")
        self.assertEqual(lecture,before)

    def test_video_labels_use_local_times_and_require_correspondence(self):
        lecture=self.lecture()
        lecture['structure']['placements'][1]['topic_id']='second'
        lecture['structure']['placements'][0]['timestamp_range']='00:00:30.679 — 00:02:18.040'
        lecture['structure']['placements'][1]['timestamp_range']='00:00:06.560 — 00:04:19.040'
        lecture['source_locators']['b'][0].update(source_uri='part2.txt',start_ms=6560,end_ms=259040)
        with self.assertRaisesRegex(ValueError,'explicit video labels'):time_source_labels(lecture,{})
        labels=time_source_labels(lecture,dict(time_sources=[dict(source_uri='part1.txt',label='Видео 1',basis='Part 1'),dict(source_uri='part2.txt',label='Видео 2',basis='Part 2')]))
        self.assertEqual(display_timestamp(topic_timestamp(lecture,'second',labels)),'Видео 2 · 00:00:06 — 00:04:19')

    def test_single_video_needs_explicit_correspondence(self):
        lecture=self.lecture()
        with self.assertRaisesRegex(ValueError,'explicit video labels'):
            time_source_labels(lecture,{})
        labels=time_source_labels(lecture,{"time_sources":[
            {"source_uri":"part1.txt","label":"Видео 1","basis":"Verified transcript/video pair"}]})
        self.assertEqual(topic_timestamp(lecture,"t",labels),"Видео 1 · 00:00:30 — 00:03:42")
        self.assertEqual(time_source_labels({"source_locators":{}},{}),{})

    def test_mixed_missing_reversed_and_nonadjacent_sources_not_merged(self):
        for change in [lambda d:d["source_locators"]["b"][0].update(source_uri="part2.txt"),
                       lambda d:d["source_locators"]["b"][0].update(start_ms=None),
                       lambda d:d["source_locators"]["b"][0].update(start_ms=0),
                       lambda d:d["structure"]["placements"].insert(1,dict(text_block_id="c",topic_id="other"))]:
            lecture=self.lecture();change(lecture)
            self.assertEqual(topic_timestamp(lecture,"t"), "")

    def test_explicit_visual_width_preserves_aspect_ratio_and_limits(self):
        from PIL import Image
        from pdf_flow import geometry, MM
        from render_golden import PDF_ROOT
        profile=json.loads(PROFILE.read_text(encoding="utf-8"));geometry(profile,PDF_ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"source.png";Image.new("RGB",(1600,900),"white").save(path)
            visual=dict(image=ref(path),source=ref(path),caption="Разбор вычисления",display_width_mm=125,
                        size_reason="Проверенная читаемая карточка рядом с объяснением")
            result=visual_layout(visual,profile)
            self.assertAlmostEqual(result["width"],125*MM)
            self.assertAlmostEqual(result["width"]/result["image_height"],1600/900)
            for bad in [0,-1,999,True,float("nan")]:
                with self.assertRaises(ValueError):visual_layout({**visual,"display_width_mm":bad},profile)
            with self.assertRaises(ValueError):visual_layout({**visual,"size_reason":""},profile)


if __name__ == "__main__":
    unittest.main()
