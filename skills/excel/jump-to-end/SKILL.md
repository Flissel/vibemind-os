---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Strg+Ende = springe zur letzten verwendeten Zelle.
expected_state:
  description: Zelle A1 ist selektiert oder eine echte Zelle in einer gefüllten Mappe
  verification_tool: vision_analyze
inputs: []
name: excel-jump-to-end
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:06:21.429110+00:00'
---

## Schritte
1. **Excel öffnen oder fokussieren**: Excel wird geöffnet oder fokussiert, wenn es bereits läuft.
2. **Cursor in sauberen Zustand bringen**: Strg+Pos1 drücken, um zur Zelle A1 zu springen.
3. **Skill-Test ausführen**: Strg+Ende drücken, um zur letzten verwendeten Zelle zu springen.
4. **Validierung**: Überprüfen, ob die Zelle A1 ausgewählt ist oder eine echte Zelle in einer gefüllten Mappe.
