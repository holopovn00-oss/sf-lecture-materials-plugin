# SF Lecture Materials

Плагин Codex для SF Education. Он превращает русскоязычный транскрипт в полный учебный текст JSON, проверяет отдельные утверждения по внешним источникам и собирает из выбранной редакции A4 PDF со смысловыми абзацами и формулами LaTeX.

Текущая версия плагина: **0.1.0**.

## Два режима работы

1. **Полный цикл через \`sf-lecture-materials\`.** Материалы → принятый образец → полный учебный текст → фактчек → решения человека → новая выбранная редакция → PDF-кандидат по Golden Gate.
2. **Прямой выбранный навык.** \`$sf-transcript-to-lecture\`, \`$sf-fact-check\` или \`$sf-lecture-to-golden-pdf\` выполняют только собственный этап и не переходят к следующему автоматически.

В полном цикле PDF не создается, пока fact-check не связан с выбранной новой редакцией и не пройдет \`FULL_CYCLE_FACT_CHECK_GATE_VALIDATED\`. Решение «оставить» фиксирует исключение, но не превращает исходный claim в подтвержденный факт. Прямой PDF отмечается как \`DIRECT_SKILL_NO_FACT_CHECK_GATE\`: это отдельный режим без неявного фактчека.

## Навыки

| Навык | Вход | Результат |
|---|---|---|
| \`sf-lecture-materials\` | Материалы лекции | Полный цикл до PDF-кандидата с проверяемым fact-check gate |
| \`sf-transcript-to-lecture\` | Полный транскрипт и исходные привязки | \`lecture-text.json\` с учебным текстом, LaTeX и редакторским отчетом |
| \`sf-fact-check\` | Текст или \`lecture-text.json\` | \`fact-check.json\`, action-карточки и обработка решений |
| \`sf-lecture-to-golden-pdf\` | Явно выбранный готовый текст или full-cycle handoff | A4 PDF, план размещения и технические проверки |

## Что требует вашего решения

Перед обработкой всей лекции примите один образец редактуры. Фактчек показывает только реальные ошибки и оговорки, меняющие учебный смысл; решения задаются адресно, например \`FC-001 — исправить\`. При неполном охвате фактчека PDF полного цикла требует отдельного принятия точно описанного ограничения.

Автоматические проверки оценивают структуру, связанные хеши и механику PDF. Они не заменяют содержательную сверку, просмотр страниц и ручную приемку.

## Подключение и проверка

Устанавливайте текущую версию из ветки \`main\`. Для воспроизводимой установки закрепляйте SHA коммита. Порядок подключения и обновления описан в [инструкции по обновлению](docs/UPDATING.md).

Для локальной структурной проверки из корня репозитория выполните:

~~~powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
~~~

Для изменений PDF-контура используйте [GitHub-gates](docs/GITHUB_GOVERNANCE.md). Полный PDF-релиз требует отдельной визуальной и ручной проверки.

## Документация

- [Общий сценарий работы](plugins/sf-lecture-materials/skills/sf-lecture-materials/SKILL.md)
- [Контракт full-cycle handoff](plugins/sf-lecture-materials/skills/sf-lecture-materials/references/full-cycle-handoff-1.0.0.md)
- [Правила фактчека и решений](plugins/sf-lecture-materials/skills/sf-fact-check/SKILL.md)
- [Предъявление результатов фактчека](plugins/sf-lecture-materials/skills/sf-fact-check/references/human-review.md)
- [Подготовка Golden Gate PDF](plugins/sf-lecture-materials/skills/sf-lecture-to-golden-pdf/SKILL.md)
- [Автоматические проверки PDF](plugins/sf-lecture-materials/skills/sf-lecture-to-golden-pdf/references/automated-checks.md)
- [Индекс документации](docs/README.md)
- [Сведения о сторонних ресурсах](THIRD_PARTY_NOTICES.md)
