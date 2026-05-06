---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Strg+S = speichert die aktive Mappe (oder oeffnet Save-Dialog wenn neu).
expected_state:
  description: Der Speichern-unter-Dialog ist geöffnet, was bei einer neuen Mappe
    das erwartete Verhalten ist.
  verification_tool: vision_analyze
inputs: []
last_adjusted: '2026-05-04T12:31:00'
name: excel-save-ctrl-s
requires_approval: true
successes: 1
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Genehmigung des Lernlaufs wurde automatisch erteilt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Pos1 wurde ausgeführt.
4. **Skill-Test ausführen**: Strg+S wurde gedrückt, um die Mappe zu speichern. Der Speichern-unter-Dialog wurde geöffnet.

## Ergebnis
- **Erfolg**: Der Speichern-unter-Dialog wurde erfolgreich geöffnet, was bei einer neuen Mappe das erwartete Verhalten ist.
