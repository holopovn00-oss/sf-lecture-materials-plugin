# Контракт передачи: LectureText 3.0.0

Единственный редактируемый текстовый результат — lecture-text.json. Формулы LaTeX и смысловые абзацы находятся внутри него. [Текущая JSON Schema](lecture-text.schema.json) описывает форму; scripts/handoff.py дополнительно проверяет связи, исходное покрытие и хеши. [Схема 2.0.0](lecture-text-2.0.0.schema.json) сохранена для проверки исторических пакетов без молчаливой миграции.

## Исходники

source-blocks.json — массив блоков в подтверждённом порядке. Каждый содержит source_block_id, полный исходный text, substantive, абсолютный source_uri, locator и известные start_ms/end_ms/page/slide. Время — миллисекунды конкретного файла с 0; страницы/слайды — с 1. Неизвестные значения остаются null. Файлы, SHA-256 исходных байтов, порядок и способ извлечения отдельно фиксируются в source-manifest.json. Блоки размечаются до редактуры.

## Черновик

draft.json содержит ровно schema_version, folder_id, title, blocks, transformation_ledger, structure. Версия — 3.0.0. У блока ровно text_block_id, source_block_ids, content. Пример формы одного блока (его содержание не переносится в другие лекции):

~~~json
{
  "text_block_id": "b1",
  "source_block_ids": ["s1"],
  "content": [
    {"type": "paragraph", "runs": [{"type": "text", "text": "Пояснение формулы из источника."}]},
    {"type": "display_math", "formula_id": "ear", "latex": "\\mathrm{EAR}=\\left(1+\\frac{r}{m}\\right)^m-1", "source_block_ids": ["s1"]},
    {"type": "paragraph", "runs": [
      {"type": "math", "formula_id": "nominal", "latex": "r", "source_block_ids": ["s1"]},
      {"type": "text", "text": " — обозначение из источника."}
    ]}
  ]
}
~~~

В JSON обратная косая черта экранируется; фактическая строка LaTeX содержит один символ \. Не добавляй долларовые обёртки, команды документа, чтения файлов или макросы. Ограниченная математическая лексика AMS проверяется общим scripts/lecture_content.py в корне плагина; синтаксис устанавливается реальной компиляцией. Текстовые runs не содержат LaTeX и переводов строки; новый абзац — отдельный элемент content.

Каждое появление формулы имеет уникальный formula_id и непустые source_block_ids, входящие в привязки её блока. Для сверки по кадру/слайду добавь evidence с path, sha256, locator; файл должен присутствовать в описи источников. Формула и её повторное обозначение могут ссылаться на один источник, но имеют разные ID.

structure содержит sections (section_id, number, title), topics (topic_id, section_id, title) и placements (text_block_id, section_id, topic_id). Разделы/подтемы непрерывны и идут в исходном порядке. Итоговый блок может объединять соседние исходные ID; каждый содержательный исходный блок сопоставляется ровно один раз.

## Учёт удаления

Полный технический блок с обоснованным substantive=false:

~~~json
{"source_block_id":"noise","disposition":"removed_nonsemantic","reason":"Проверка микрофона без связи с темой."}
~~~

Частичное удаление из смешанного блока имеет disposition=removed_nonsemantic_span, source_block_id, start, end, quote, reason. Границы [start,end) — индексы Unicode-строки Python, не байты и не UTF-16. Цитата должна точно совпадать с исходным диапазоном. Диапазоны не пересекаются и не покрывают весь блок; содержательная часть остаётся сопоставленной. Другие редакторские изменения и решения ведутся в text-review.json.

## Сборка

Из папки текстового навыка:

~~~powershell
python scripts/handoff.py build --sources 'C:/Task/source-blocks.json' --draft 'C:/Task/draft.json' --out 'C:/Task/text-v1'
python scripts/handoff.py check --sources 'C:/Task/text-v1/source-blocks.json' --lecture 'C:/Task/text-v1/lecture-text.json'
python scripts/handoff.py check-package --review 'C:/Task/text-v1/text-review.json'
~~~

build создаёт новую папку с двумя JSON: нормализованными источниками и полным текстом. Существующую папку не перезаписывает. source-manifest.json и text-review.json подготовь отдельно по [формату комплекта](package-checks.md). DOCX, Markdown и текстовый отчёт вне JSON не создаются.

editorial_mode=study_guide, source_blocks_hash, source_locators, assertions и content_hash вычисляет помощник. Канонизация: UTF-8 JSON, ensure_ascii=false, сортировка ключей, разделители запятая/двоеточие, SHA-256 в верхнем регистре. content_hash вычисляется без самого этого поля и отличается от SHA-256 байтов файла.

Исходные локальные интервалы неизменны. Обобщённый интервал блока возможен только внутри одного файла с известными крайними метками; разные файлы остаются отдельными locators без суммарной временной шкалы.

STRUCTURE_VALIDATED означает совпадение структуры, хешей и покрытия. Смысл — NOT_EVALUATED_BY_SCRIPT. На PDF передаются эти JSON, отчёт и результат проверки. При явно выбранном внешнем готовом документе зафиксируй его как новую основу с собственным хешем, не выдавай его за прежний JSON.
