---
agents:
- '*'
app: excel
attempts: 1
confidence: 0.0
description: Setzt Seitenausrichtung auf Querformat.
expected_state:
  description: Die Excel-Seitenansicht ist im Querformat (breiter als hoch).
  verification_tool: vision_analyze
inputs: []
last_adjusted: '2026-05-04T12:32:00'
name: excel-page-setup-landscape
requires_approval: true
successes: 0
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Genehmigung des Lernlaufs wurde automatisch erteilt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Pos1 wurde ausgeführt.
4. **Skill-Test ausführen**: Alt+P, O, L wurde ausgeführt, um die Seitenausrichtung auf Querformat zu setzen.

## Ergebnis
- **Fehler**: Die Excel-Seitenansicht ist nicht im Querformat, sondern im Hochformat.
