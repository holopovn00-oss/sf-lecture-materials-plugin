import hashlib
import tempfile
import unittest
from pathlib import Path
from test_json_content import ROOT
from visual_policy import display_caption, caption_fields, validate_sources, check_pdf_text


class VisualPolicyTests(unittest.TestCase):
    def test_wrapped_extension_and_technical_messages(self):
        self.assertEqual(display_caption('Источник: С комментариями\nпреподавателя.pdf».'), 'Источник: С комментариями\nпреподавателя».')
        for text in ['FC-001', 'Исправленная степень в формуле ниже', 'Ниже дана правильная запись']:
            with self.assertRaises(ValueError): display_caption(text)

    def test_original_inventory_and_output_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'slides.pdf'; p.write_bytes(b'original')
            ref={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            v={'source':ref,'source_page':1,'caption':'Источник: slides\nСлайд 1.\nТема: Пример','lecturer_photo_review':'absent'}
            c={'source_root':tmp,'source_inventory':[{**ref,'role':'original_visual'}],'visuals':[v]}
            validate_sources(c)
            v['caption']=''; v['blank_caption_authorization']='User requested blank fields'; validate_sources(c)
            del v['blank_caption_authorization']
            with self.assertRaises(ValueError): validate_sources(c)
            v['caption']='Источник: slides\nСлайд 1.\nТема: Пример'; c['source_inventory'][0]['role']='generated_output'
            with self.assertRaises(ValueError): validate_sources(c)
            c['source_inventory'][0]['role']='original_visual'; p.write_bytes(b'changed')
            with self.assertRaises(ValueError): validate_sources(c)

    def test_pdf_scans_continuation_line(self):
        class Page:
            number=0
            def get_text(self): return 'Источник: длинное название\nпреподавателя.pdf».\nСлайд 1.\nТема: Пример'
            def get_textbox(self, box): return self.get_text()
        with self.assertRaisesRegex(ValueError, 'page 1'):
            check_pdf_text([Page()],[{"page":1,"bbox":[0,0,100,100]}])

    def test_extensions_are_preserved_outside_source_name(self):
        text='Источник: slides.pdf\nСлайд 2.\nТема: импорт example.pdf и .pptx'
        self.assertEqual(display_caption(text),
            'Источник: slides\nСлайд 2.\nТема: импорт example.pdf и .pptx')
        text='Источник: report.pdf\nСтраница 2.\nТема: чтение notes.txt'
        self.assertEqual(display_caption(text),'Источник: report\nСтраница 2.\nТема: чтение notes.txt')
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
                'caption':'Источник: Авторская схема\nСлайд: не применимо.\nТема: Объяснение','text_block_ids':['b1'],
                'authoring':{'authorization':'User explicitly allowed diagrams','basis':'Checked block b1'},
                'lecturer_photo_review':'absent'}
            validate_sources({'visuals':[visual]})
            for field in ('authorization','basis'):
                broken=copy.deepcopy(visual);broken['authoring'][field]=''
                with self.assertRaises(ValueError):validate_sources({'visuals':[broken]})
            for field,value in [('caption','Источник: slides'),('text_block_ids',[]),('origin','unknown')]:
                broken=copy.deepcopy(visual);broken[field]=value
                with self.assertRaises(ValueError):validate_sources({'visuals':[broken]})
            broken=copy.deepcopy(visual)
            broken['caption']='Источник: Авторская схема\nСлайд 1.\nТема: Объяснение'
            with self.assertRaisesRegex(ValueError,'original slide number'):
                validate_sources({'visuals':[broken]})
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):validate_sources({'visuals':[visual]})

    def test_caption_fields_require_order_and_explicit_newlines(self):
        valid='Источник: Презентация\nСлайд 12.\nТема: Расчет'
        self.assertEqual(caption_fields(valid), ['Презентация','12','Расчет'])
        for invalid in [valid.replace('\n',' → '), 'Обычная подпись',
                        'Источник: Презентация\nТема: Расчет\nСлайд 12.',
                        valid+'\nТема: Повтор', 'Источник: Презентация\nТема: Расчет',
                        'Источник: Презентация\nСлайд № 12\nТема: Расчет',
                        'Источник: Презентация\nСлайд 0.\nТема: Расчет',
                        'Источник: Презентация\nСлайд 12\nТема: Расчет']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                caption_fields(invalid)
        self.assertEqual(caption_fields('Источник: Отчет\nСтраница 7.\nТема: Баланс'),
                         ['Отчет','7','Баланс'])
        self.assertEqual(caption_fields('Источник: Авторская схема\nСлайд: не применимо.\nТема: Порядок'),
                         ['Авторская схема','не применимо','Порядок'])

    def test_wrapped_fields_and_explicit_blank_permission(self):
        text='Источник: Длинное название\nпрезентации\nСлайд 12.\nТема: Первое\nпродолжение'
        self.assertEqual(caption_fields(text), ['Длинное название\nпрезентации','12','Первое\nпродолжение'])
        empty='Источник: Презентация\nСлайд:\nТема: Расчет'
        for permission in [None, '', ' ', True]:
            with self.subTest(permission=permission), self.assertRaises(ValueError):
                caption_fields(empty, permission)
        self.assertEqual(caption_fields(empty, 'User explicitly requested empty slide field')[1], '')

    def test_actual_pdf_caption_requires_field_breaks(self):
        class Page:
            number=0
            def get_text(self): return 'Источник: Презентация → Слайд 12. → Тема: Расчет'
            def get_textbox(self, box): return self.get_text()
        with self.assertRaisesRegex(ValueError, 'Caption field layout on page 1'):
            check_pdf_text([Page()], [{'page':1,'bbox':[0,0,100,100]}])

    def test_video_and_time_are_rejected_only_inside_visual_caption(self):
        base='Источник: Презентация\nСлайд 3.\nТема: Ставки'
        for extra in ('\nВидео: 00:01:04 – 00:03:21', '\nВидео 1: 00:01:04',
                      '\n00:01:04 – 00:03:21', ' 00:01:04'):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'Video label and timing'):
                caption_fields(base+extra)
        class Page:
            number=0
            def get_text(self): return 'Видео 1 00:01:04 – 00:03:21\n'+base
            def get_textbox(self, box): return base
        check_pdf_text([Page()], [{'page':1,'bbox':[0,0,100,100]}])
