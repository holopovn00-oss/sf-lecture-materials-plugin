"""Source provenance and reader-facing caption checks (not visual certification)."""
import hashlib
import re
from pathlib import Path

EXT = re.compile(r'\.(?:pdf|pptx?|docx?|mp4|mov|mkv|txt|vtt|srt|png|jpe?g)(?=\b|[»”])', re.I)
SERVICE = re.compile(r'\bFC-\d+\b|ниже дана правильная запись|исправленная степень|нижняя схема приближенная', re.I)
# Only the explicitly marked source-name field is normalized, not teaching text.
SOURCE_NAME = re.compile(
    r'(?:^|\n)(Источник[ \t]*:[ \t]*)(.*?)'
    r'(?=\n(?:Слайд(?=[ \t:]|$)|Страница(?=[ \t:]|$)|Тема[ \t]*:)|\Z)',
    re.I | re.S,
)
CAPTION_FIELD = re.compile(
    r'^(Источник[ \t]*:|Слайд(?=[ \t:]|$)[ \t]*:?|'
    r'Страница(?=[ \t:]|$)[ \t]*:?|Тема[ \t]*:)[ \t]*', re.M,
)
CAPTION_TIME = re.compile(r'\b\d{2}:\d{2}:\d{2}\b')
CAPTION_VIDEO_LINE = re.compile(r'^\s*Видео(?:[ \t]+\d+)?(?:[ \t]*:|[ \t]+\d{2}:\d{2})', re.I | re.M)


def caption_fields(text, blank_authorization=None):
    """Three ordered fields; visual captions never contain video/time badges."""
    if not isinstance(text, str):
        raise ValueError('Caption must be text')
    authorized = isinstance(blank_authorization, str) and bool(blank_authorization.strip())
    if not text.strip():
        if authorized:
            return []
        raise ValueError('Blank caption requires recorded user instruction')
    if CAPTION_TIME.search(text) or CAPTION_VIDEO_LINE.search(text):
        raise ValueError('Video label and timing are not allowed in a visual caption')
    matches = list(CAPTION_FIELD.finditer(text))
    if (len(matches) != 3 or matches[0].start() != 0 or
            matches[0].group(1).split()[0].rstrip(':') != 'Источник' or
            matches[1].group(1).split()[0].rstrip(':') not in ('Слайд', 'Страница') or
            matches[2].group(1).split()[0].rstrip(':') != 'Тема'):
        raise ValueError('Caption requires three separate fields in order: Источник, Слайд/Страница, Тема')
    fields = [text[m.end():matches[i + 1].start() if i < 2 else len(text)].strip()
              for i, m in enumerate(matches)]
    if any(not field for field in fields) and not authorized:
        raise ValueError('Blank caption field requires recorded user instruction')
    locator = fields[1]
    has_colon = matches[1].group(1).rstrip().endswith(':')
    if not ((not has_colon and re.fullmatch(r'[1-9]\d*\.', locator)) or
            (has_colon and locator == 'не применимо.') or
            (has_colon and not locator and authorized)):
        raise ValueError('Second caption line must be Слайд N., Страница N. or Слайд: не применимо.')
    fields[1] = locator.removesuffix('.')
    return fields


def display_caption(text):
    if SERVICE.search(text):
        raise ValueError('Technical review message in lecture caption')
    return SOURCE_NAME.sub(lambda m: m.group(0).replace(m.group(2), EXT.sub('', m.group(2)), 1), text)


def check_pdf_text(doc, caption_rows=()):
    for page in doc:
        text = page.get_text()
        if SERVICE.search(text):
            raise ValueError(f'Technical message on page {page.number + 1}')
    # Read all lines of each actual caption, not just its first planned line.
    for row in caption_rows:
        page = doc[row['page'] - 1]
        text = page.get_textbox(row['bbox'])
        # Blank-field permission is checked against the composition by validate_sources.
        # Here check actual line breaks, not just the caption stored in render-plan.
        try:
            caption_fields(text, blank_authorization='Checked separately against composition')
        except ValueError as exc:
            raise ValueError(f'Caption field layout on page {row["page"]}: {exc}') from exc
        if display_caption(text) != text:
            raise ValueError(f'File extension in caption source name on page {row["page"]}')


def validate_sources(composition):
    if not composition.get('visuals'):
        return
    visuals = composition['visuals']
    if any(v.get('origin', 'original') not in ('original', 'authored') for v in visuals):
        raise ValueError('Unknown visual origin')
    originals = [v for v in visuals if v.get('origin', 'original') == 'original']
    if originals and (not composition.get('source_root') or not Path(composition['source_root']).is_absolute()):
        raise ValueError('Explicit absolute lecture source_root required')
    root = Path(composition['source_root']).resolve() if originals else None
    inventory = composition.get('source_inventory', [])
    allowed = {}
    for item in inventory:
        path = Path(item['path']).resolve()
        if root is None or not path.is_relative_to(root) or item.get('role') != 'original_visual':
            continue
        if path.suffix.lower() in {'.txt', '.vtt', '.srt'} or re.search(r'конспект|candidate|lecture[-_ ]text', path.stem, re.I):
            raise ValueError('Transcript or generated lecture cannot be a visual source')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest().lower() != item.get('sha256', '').lower():
            raise ValueError('Changed or missing original visual source')
        allowed[path] = item['sha256'].lower()
    for visual in composition['visuals']:
        src = visual['source']; path = Path(src['path']).resolve()
        if visual.get('origin', 'original') == 'authored':
            authoring = visual.get('authoring')
            if not isinstance(authoring, dict) or any(not isinstance(authoring.get(k), str) or
                    not authoring[k].strip() for k in ('authorization', 'basis')):
                raise ValueError('Authored diagram requires user authorization and checked factual basis')
            anchors = visual.get('text_block_ids')
            if not isinstance(anchors, list) or not anchors or any(not isinstance(a, str) or not a.strip() for a in anchors):
                raise ValueError('Authored diagram requires linked lecture blocks')
            image = visual.get('image', {})
            if (not image.get('path') or Path(image['path']).resolve() != path or
                    image.get('sha256', '').lower() != src.get('sha256', '').lower() or
                    not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest().lower() != src.get('sha256', '').lower()):
                raise ValueError('Authored diagram source must be its hash-bound generated image')
            fields = caption_fields(visual.get('caption', ''), visual.get('blank_caption_authorization'))
            if not fields or not re.match(r'^Авторская схема(?:\s|[.:]|$)', fields[0]):
                raise ValueError('Caption must identify the authored diagram, not an original slide')
            if fields[1] and fields[1].lower() != 'не применимо':
                raise ValueError('Authored diagram must not claim an original slide number')
        elif path not in allowed or src.get('sha256', '').lower() != allowed[path]:
            raise ValueError('Visual source must be an inventoried original in the lecture folder')
        elif not (type(visual.get('source_page')) is int and visual['source_page'] > 0) and not (
                type(visual.get('pts_seconds')) in (int, float) and visual['pts_seconds'] >= 0) and not visual.get('source_locator'):
            raise ValueError('Missing slide/page/frame locator')
        if visual.get('lecturer_photo_review') not in ('absent', 'excluded'):
            raise ValueError('Lecturer photo review required for every visual')
        if visual.get('lecturer_photo_review') == 'excluded' and not visual.get('extraction'):
            raise ValueError('Document extraction excluding lecturer photo')
        caption_fields(visual.get('caption', ''), visual.get('blank_caption_authorization'))
        display_caption(visual.get('caption', ''))
