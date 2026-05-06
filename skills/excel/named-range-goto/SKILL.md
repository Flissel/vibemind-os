---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Strg+G + Range/Name eingeben + Enter.
expected_state:
  description: Der Excel-Cursor befindet sich in der angegebenen Zelle oder dem benannten
    Bereich.
  verification_tool: vision_analyze
inputs:
- name: range_or_name
  type: string
name: excel-named-range-goto
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:10:41.706517+00:00'
---

## Schritte

1. Excel öffnen und sicherstellen, dass es fokussiert ist.
2. Mit `Ctrl+Home` den Cursor in einen sauberen Zustand bringen.
3. `Ctrl+G` drücken, um das 'Gehe zu'-Dialogfenster zu öffnen.
4. Den Zellbereich oder Namen eingeben, z.B. 'B5'.
5. `Enter` drücken, um den Cursor zur angegebenen Zelle zu bewegen.
6. Validieren, dass der Cursor in der richtigen Zelle ist.
