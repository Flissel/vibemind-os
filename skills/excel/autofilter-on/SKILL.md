---
agents:
- '*'
app: excel
attempts: 1
confidence: 0.0
description: Aktiviert AutoFilter (Strg+Shift+L).
expected_state:
  description: Filter-Pfeil-Icons sind in der Header-Zeile A1:B1 sichtbar.
  verification_tool: vision_analyze
inputs:
- description: A1:B1
  name: header_range
  type: string
last_adjusted: '2026-05-04T12:23:00'
name: excel-autofilter-on
requires_approval: true
successes: 0
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Anfrage zur Genehmigung des Lernlaufs gestellt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Home wurde ausgefuehrt.
4. **Daten einfügen**: Tabelle mit den Werten Name, Wert, A, 1, B, 2 in Zellen A1 bis B3 eingefügt.
5. **AutoFilter aktivieren**: Strg+Shift+L wurde ausgeführt.
6. **Ergebnis validieren**: Die Filter-Pfeil-Icons waren nicht sichtbar.
