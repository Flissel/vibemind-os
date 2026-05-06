---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Springe in Excel zur Zelle A1 (Strg+Pos1/Strg+Home)
expected_state:
  description: Zelle A1 ist selektiert, Namensbox zeigt A1
  verification_tool: vision_analyze
inputs: []
name: excel-jump-to-a1
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:02:52.878482+00:00'
---

## Schritte
1. **Excel öffnen oder fokussieren**: Excel wird geöffnet oder fokussiert, wenn es bereits läuft.
2. **Cursor in nicht-A1 Zelle bringen**: Strg+G, Eingabe 'C5', Enter.
3. **Pre-Check**: Sicherstellen, dass der Cursor in C5 ist.
4. **Skill-Aktion**: Strg+Pos1 drücken, um zur Zelle A1 zu springen.
5. **Validierung**: Überprüfen, ob die Zelle A1 ausgewählt ist und die Namensbox A1 zeigt.
