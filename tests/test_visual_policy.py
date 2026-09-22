import hashlib
import tempfile
import unittest
from pathlib import Path
from test_json_content import ROOT
from visual_policy import display_caption, validate_sources, check_pdf_text


class VisualPolicyTests(unittest.TestCase):
    def test_wrapped_extension_and_technical_messages(self):
        self.assertEqual(display_caption('Источник: С комментариями\nпреподавателя.pdf».'), 'Источник: С комментариями\nпреподавателя».')
        for text in ['FC-001', 'Исправленная степень в формуле ниже', 'Ниже дана правильная запись']:
            with self.assertRaises(ValueError): display_caption(text)

    def test_original_inventory_and_output_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'slides.pdf'; p.write_bytes(b'original')
            ref={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            v={'source':ref,'source_page':1,'caption':'Источник: slides','lecturer_photo_review':'absent'}
            c={'source_root':tmp,'source_inventory':[{**ref,'role':'original_visual'}],'visuals':[v]}
            validate_sources(c)
            v['caption']=''; v['blank_caption_authorization']='User requested blank fields'; validate_sources(c)
            del v['blank_caption_authorization']
            with self.assertRaises(ValueError): validate_sources(c)
            v['caption']='Источник: slides'; c['source_inventory'][0]['role']='generated_output'
            with self.assertRaises(ValueError): validate_sources(c)
            c['source_inventory'][0]['role']='original_visual'; p.write_bytes(b'changed')
            with self.assertRaises(ValueError): validate_sources(c)

    def test_pdf_scans_continuation_line(self):
        class Page:
            number=0
            def get_text(self): return 'Источник: длинное название\nпреподавателя.pdf».'
            def get_textbox(self, box): return self.get_text()
        with self.assertRaisesRegex(ValueError, 'page 1'):
            check_pdf_text([Page()],[{"page":1,"bbox":[0,0,100,100]}])

    def test_extensions_are_preserved_outside_source_name(self):
        text='Источник: slides.pdf → Слайд № 2 → Тема: импорт example.pdf и .pptx'
        self.assertEqual(display_caption(text),
            'Источник: slides → Слайд № 2 → Тема: импорт example.pdf и .pptx')
        text='Источник: slides.pdf\nТема: чтение notes.txt'
        self.assertEqual(display_caption(text),'Источник: slides\nТема: чтение notes.txt')
        class Page:
            number=0
            def get_text(self): return 'Откройте example.pdf и notes.txt.'
        check_pdf_text([Page()])

    def test_service_ids_still_blocked_in_actual_pdf(self):
        class Page:
            number=0
            def get_text(self): return 'FC-001'
        with self.assertRaisesRegex(ValueError,'Technical message'):
            check_pdf_text([Page()])

    def test_authored_diagram_requires_honest_provenance_and_permission(self):
        import copy
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'authored.png';path.write_bytes(b'generated diagram')
            reference={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            visual={'origin':'authored','source':reference,'image':reference,
                'caption':'Авторская схема: объяснение','text_block_ids':['b1'],
                'authoring':{'authorization':'User explicitly allowed diagrams','basis':'Checked block b1'},
                'lecturer_photo_review':'absent'}
            validate_sources({'visuals':[visual]})
            for field in ('authorization','basis'):
                broken=copy.deepcopy(visual);broken['authoring'][field]=''
                with self.assertRaises(ValueError):validate_sources({'visuals':[broken]})
            for field,value in [('caption','Источник: slides'),('text_block_ids',[]),('origin','unknown')]:
                broken=copy.deepcopy(visual);broken[field]=value
                with self.assertRaises(ValueError):validate_sources({'visuals':[broken]})
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):validate_sources({'visuals':[visual]})
