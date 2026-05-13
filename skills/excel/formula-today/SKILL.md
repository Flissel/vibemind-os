---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Schreibt =HEUTE() und validiert ein Datum.
expected_state:
  description: Der Wert in A1 zeigt das aktuelle Datum im Format DD.MM.YYYY oder MM/DD/YYYY
    an.
  verification_tool: vision_analyze
inputs:
- description: A1
  name: target
  type: string
last_adjusted: '2026-05-04T12:23:00'
name: excel-formula-today
requires_approval: true
successes: 1
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Anfrage zur Genehmigung des Lernlaufs gestellt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Home wurde ausgefuehrt.
4. **Formel eingeben**: =HEUTE() in Zelle A1 eingegeben.
5. **Ergebnis validieren**: Der Wert in A1 wurde als Datum erkannt.
