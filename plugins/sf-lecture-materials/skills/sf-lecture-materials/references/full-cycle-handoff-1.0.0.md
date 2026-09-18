# Full-cycle fact-check handoff 1.0.0

Это проверяемая передача из общего полного цикла в PDF-навык. Она не является доказательством научной истинности, полномочий человека или визуального качества. Ее назначение узкое: не допустить PDF полного цикла, пока фактчек не связан с выбранной финальной редакцией и не закрыты требующие решения пункты.

## Файл

\`full-cycle-handoff.json\`:

~~~json
{
  "schema_version": "1.0.0",
  "mode": "full_cycle",
  "checked_lecture": {
    "kind": "lecture_json",
    "path": "C:/Task/lecture-before-fact-check.json",
    "sha256": "<SHA-256>"
  },
  "fact_check": {
    "kind": "fact_check",
    "path": "C:/Task/fact-check.json",
    "sha256": "<SHA-256>"
  },
  "selected_lecture": {
    "kind": "lecture_json",
    "path": "C:/Task/lecture-selected-after-decisions.json",
    "sha256": "<SHA-256>"
  },
  "coverage_resolution": {
    "status": "complete",
    "comment": "",
    "basis": ""
  }
}
~~~

Все пути абсолютные; каждый SHA-256 относится к фактическим байтам. \`checked_lecture\` должен совпасть с \`input\` отчета фактчека. \`selected_lecture\` — версия, из которой будет собран PDF.

## Правила gate

Выбранная редакция может отличаться только точными примененными заменами correct. Остальные строки, структура и метаданные сохраняются. При отсутствии correct можно выбрать исходный неизмененный файл. Необязательные proposal с decision_required=false не блокируют передачу; error/outdated всегда требуют решения.

- Любой \`correct\` должен иметь \`execution:"applied"\`; artifact каждой такой application должен быть точно равен \`selected_lecture\`.
- Любой \`keep\` должен иметь \`execution:"kept"\`. Такой claim остается retained finding: исходный assessment не меняется.
- \`pending\` на actionable claim и любой \`recheck\` блокируют handoff.
- При complete coverage без \`not_checked\` используй \`coverage_resolution.status:"complete"\` и пустые comment/basis.
- При partial coverage или хотя бы одном \`not_checked\` PDF возможен только с \`status:"accepted_limit"\`, непустыми точными словами решения человека в comment и доступным locator/датой в basis.

Проверь файл:

~~~powershell
python scripts/verify_full_cycle_handoff.py --handoff C:/Task/full-cycle-handoff.json
~~~

Успех: \`FULL_CYCLE_FACT_CHECK_GATE_VALIDATED\` и \`pdf_gate:"READY_FOR_PDF"\`. Эта команда только читает файлы.
