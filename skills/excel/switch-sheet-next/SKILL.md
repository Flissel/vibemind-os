---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Strg+Bild-ab = naechstes Tabellenblatt aktivieren.
expected_state:
  description: Das aktive Tabellenblatt ist nicht das originale (z.B. 'Tabelle1').
  verification_tool: vision_analyze
inputs: []
name: excel-switch-sheet-next
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:07:43.805592+00:00'
---

## Schritte
1. **Excel öffnen oder fokussieren**: Excel wird geöffnet oder fokussiert, wenn es bereits läuft.
2. **Cursor in sauberen Zustand bringen**: Strg+Pos1 drücken, um zur Zelle A1 zu springen.
3. **Neues Tabellenblatt anlegen**: Shift+F11 drücken, um ein neues Blatt zu erstellen.
4. **Zurück zu Tabelle1 wechseln**: Strg+Bild-auf drücken, um zum vorherigen Blatt zu wechseln.
5. **Zum nächsten Blatt wechseln**: Strg+Bild-ab drücken, um zum nächsten Blatt zu wechseln.
6. **Validierung**: Überprüfen, ob das aktive Tabellenblatt nicht das originale ist (z.B. 'Tabelle1').
