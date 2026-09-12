# FactCheck 1.0.0

Отчёт `fact-check.json` хранится отдельно от лекции. Комментарии в чате — его читаемое представление, дополнительный DOCX/Markdown не создаётся. Формат проверяет [verify_fact_check.py](../scripts/verify_fact_check.py), Python 3.11+, стандартная библиотека. Это проверка целостности записи, не автоматическое научное рецензирование.

## Корень

- `schema_version`: `"1.0.0"`; `title`: название проверки.
- `input`: `{kind, path, sha256}`. Kind — `text` для UTF-8 TXT/MD или `lecture_json` для LectureText 3.0.0. Path — абсолютный существующий путь; SHA-256 — хеш фактических байтов.
- `originals`: массив `{path, sha256, locator, extraction}` для оригиналов извлечённого текста (DOCX/PDF/экспорт сообщения); при прямом файловом входе — `[]`. Адрес сообщения допускается записать в scope, если отдельного файла оригинала нет; не выдумывай его.
- `scope`: `{description, as_of, coverage, reviewed_units, exclusions, gaps}`. Дата ISO YYYY-MM-DD; coverage — `complete` либо `partial`; остальные поля, кроме description, as_of и coverage, — массивы строк. Complete означает записанное рассмотрение всех единиц входа и отсутствие not_checked; неподтверждённое после реального поиска остаётся unverified. Это не доказательство исчерпывающего выделения фактов.
- `evidence`: массив доказательств с уникальными ID.
- `claims`: массив утверждений с уникальными ID вида FC-001.
- `assessment_change_reason`: строка; пустая в первом отчёте. При изменении вывода/доказательства в повторном отчёте — конкретное новое основание.

## Адреса

Каждая запись claim имеет `{id, kind, anchor, context, assessment, review}`.

Kind: `fact`, `calculation`, `definition`, `hypothesis`, `theory`, `causal`, `statistic`, `normative`, `technical`, `historical`.

Anchor: `{unit_id, start, end, quote, source_block_ids}`. Диапазон [start,end) — индексы Python Unicode-строки, не байты и не UTF-16; точная цитата должна совпасть с этим диапазоном. Для простого текста unit_id=`document`; текст декодируется UTF-8-sig с сохранением исходных CR/LF. Для JSON unit_id — text_block_id, а диапазон считается в точной проекции `lecture_content.block_text(block)`: абзацы разделены двумя LF, inline LaTeX заключён в `\(…\)`, display — в `\[…\]`. Source IDs — существующие привязки блока; для plain text — пустой массив. Не перепечатывай проекцию вручную и не создавай из неё вторую редакцию лекции.

Context — непустая строка с условиями/смыслом утверждения. Повторное вхождение имеет собственный адрес; при необходимости одно замечание ссылается на связанные FC-ID в rationale.

## Доказательства

Числовой расчёт: `{id, kind:"calculation", expression, result, method, applicability, limitations}`. Expression содержит десятичные числа, скобки, +, -, *, /, **; целочисленная степень по модулю не более 100. Result — строка с точным числом/обыкновенной дробью. Десятичные литералы вычисляются как рациональные числа, без ошибки binary float. Это арифметическая модель, не эмуляция поведения программного float. Скрипт не исполняет Python-код из отчёта.

Внешнее доказательство: `{id, kind, title, creator, publication, published, version, url, doi, locator, accessed, read_scope, passage, passage_type, applicability, limitations}`.

- kind: `official_norm`, `official_documentation`, `academic`, `official_data`, `primary_document`.
- Все перечисленные текстовые поля — непустые строки, кроме doi (строка или null). Published может быть `"undated"`, версия — `"not specified"`, если это действительно неизвестно. Unknown не подменяй догадкой.
- URL — прямой HTTPS-адрес прочитанного источника; DOI записывается отдельно при наличии. Locator — страница/раздел/таблица, accessed — дата ISO.
- read_scope — `relevant_passage` или `full_text`. Поисковая выдача, аннотация и метаданные не принимаются как прочитанное содержательное доказательство.
- passage_type — `quote` или `paraphrase`; passage — короткая точная цитата либо явно обозначенный пересказ прочитанного места.
- Applicability объясняет связь с данным утверждением и условия применения. Limitations — реальные ограничения, включая «для указанного узкого определения существенных ограничений не выявлено», если обосновано.

## Вывод и предложение

Assessment: `{status, rationale, evidence_ids, proposal}`. Status:
`confirmed`, `error`, `outdated`, `disputed`, `unverified`, `context_dependent`, `not_checked`.

Confirmed/error/outdated/disputed требуют доказательств. Неарифметическое утверждение нельзя подтвердить одним числовым расчётом. Rationale объясняет цепочку доказательства и предел вывода. Disputed требует явно изложить противоречие; число ссылок само по себе его не удостоверяет.

Proposal — null или `{kind, text}`, где kind — `correction`, `qualification` или `question`. Для error/outdated нужно конкретное предложение. Категорическая correction требует доказательств и статуса error/outdated. Для unverified допустим вопрос либо осторожное уточнение статуса гипотезы с обоснованием; новая фактическая формулировка без доказательств недопустима. Для confirmed/not_checked proposal=null.

## Решение человека

Review: `{decision, comment, basis, execution, application}`.
- decision: `pending`, `correct`, `keep`, `recheck`.
- Pending: comment/basis — пустые строки, execution=`pending`, application=null. Неразрешённый пользовательский комментарий можно сохранить при recheck.
- После решения comment — точные слова человека; basis — доступный адрес/дата сообщения, execution пока `pending`.
- Keep после фиксации имеет execution=`kept`, application=null. Оно не меняет assessment.
- Correct требует proposal и после выполнения execution=`applied`, application=`{artifact:{kind,path,sha256}, anchor}`. Anchor адресует точную одобренную редакцию в новом файле. Исправленная версия не перезаписывает исходную. Проверка сверяет новый хеш и фрагмент, но не полноту всей редакции.
- Recheck остаётся pending до заказанной дополнительной проверки и следующего решения. Нельзя автоматически закрыть его прежним выводом.

Повторное решение по тому же неизменному входу:
`python scripts/verify_fact_check.py --report <новый.json> --previous-report <предыдущий.json>`.
История проверяет сохранение всех прежних ID/цитат, неизменность файла входа и новое основание изменённого вывода/доказательства. Старый отчёт сохраняется. Подлинность комментария пользователя скрипт не удостоверяет.

После изменения текста начни новый аудит для нового хеша. Ссылка на прежний отчёт может оставаться в scope.description. При применении исправлений к LectureText используй [решения текстового этапа](../../sf-transcript-to-lecture/references/package-checks.md); FC-ID связывает замечание, комментарий человека и редакторское решение. Для исходного транскрипта источники сохраняются; новые знания учитываются как явно согласованная поправка.

## Команда и предел результата

`python scripts/verify_fact_check.py --report <абсолютный путь>`

На stdout — JSON, код 0 при `FACT_CHECK_RECORD_VALIDATED`, 1 при `BLOCKED`. При необходимости сохрани stdout в отдельный check.json. Проверка не пишет вход/отчёт и не подключается к сети. В результате отдельно видны unresolved findings, pending decisions, непроверенные утверждения, записанный охват и пересчитанные выражения. Unresolved findings не включает выполненную поправку или явное допущение без предложения; оставленная ошибка остаётся замечанием. Status counts всегда описывает исходный вход данного отчёта. Истина, независимость исследований и полнота инвентаризации остаются `NOT_AUTHENTICATED_BY_SCRIPT`, полномочия человека — `RECORDED_NOT_AUTHENTICATED`.
