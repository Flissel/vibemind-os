---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Markiere Range + Strg+T = formatiere als Tabelle.
expected_state:
  description: Der Bereich A1:B3 ist als Tabelle formatiert mit blauen alternierenden
    Zeilen und Filter-Arrows.
  verification_tool: vision_analyze
inputs:
- name: range
  type: string
last_adjusted: '2026-05-04T12:26:00'
name: excel-format-as-table
requires_approval: true
successes: 1
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Anfrage zur Genehmigung des Lernlaufs gestellt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Home wurde ausgefuehrt.
4. **Daten einfügen**: Tabelle mit den Werten ['Name','Wert'],['A','1'],['B','2'] in Zelle A1 eingefügt.
5. **Range markieren**: Strg+G wurde ausgefuehrt und 'A1:B3' eingegeben.
6. **Als Tabelle formatieren**: Strg+T wurde ausgefuehrt.
7. **Bestätigen**: Enter wurde gedrückt um 'Hat Tabelle Überschriften?' zu bestätigen.
8. **Ergebnis validieren**: Der Bereich A1:B3 ist jetzt als Tabelle formatiert mit blauen alternierenden Zeilen und Filter-Arrows.
