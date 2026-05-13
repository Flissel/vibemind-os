---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Loescht den Inhalt der aktuellen Zelle (Entf-Taste).
expected_state:
  description: Die Zelle A1 ist leer.
  verification_tool: vision_analyze
inputs: []
name: excel-clear-cell
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:13:32.447320+00:00'
---

## Schritte

1. Excel öffnen und sicherstellen, dass es fokussiert ist.
2. Mit `Ctrl+Home` den Cursor in einen sauberen Zustand bringen.
3. Wert 'SCHROTT' in Zelle A1 einfügen.
4. Cursor in A1 via `Ctrl+Home` positionieren.
5. Inhalt der aktuellen Zelle mit `Entf` löschen.
6. Validieren, dass Zelle A1 leer ist.
