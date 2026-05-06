---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Pastet eine 2D-Liste an Cell start_cell.
expected_state:
  description: Die Werte der 2D-Liste sind korrekt in den angegebenen Zellen platziert.
  verification_tool: vision_analyze
inputs:
- name: rows
  type: array
- name: start_cell
  type: string
name: excel-fill-range
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:12:07.122731+00:00'
---

## Schritte

1. Excel öffnen und sicherstellen, dass es fokussiert ist.
2. Mit `Ctrl+Home` den Cursor in einen sauberen Zustand bringen.
3. Eine 2D-Liste in die Zwischenablage kopieren und an der angegebenen Startzelle einfügen.
4. Validieren, dass die Werte korrekt in den Zellen platziert sind.
