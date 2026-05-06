---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Strg+H = Suchen und Ersetzen.
expected_state:
  description: Der Text in A1 ist 'neuertext'.
  verification_tool: vision_analyze
inputs:
- name: find
  type: string
- name: replace
  type: string
last_adjusted: '2026-05-04T12:26:00'
name: excel-find-replace
requires_approval: true
successes: 1
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Anfrage zur Genehmigung des Lernlaufs gestellt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Home wurde ausgefuehrt.
4. **Daten einfügen**: Text 'altertext' in Zelle A1 eingefügt.
5. **Suchen und Ersetzen öffnen**: Strg+H wurde ausgeführt.
6. **Suchtext eingeben**: 'alter' wurde eingegeben.
7. **Ersetzungstext eingeben**: 'neuer' wurde eingegeben.
8. **Alle ersetzen**: Alt+A wurde ausgeführt.
9. **Info-Dialog bestätigen**: Enter wurde gedrückt.
10. **Dialog schließen**: Escape wurde gedrückt.
11. **Ergebnis validieren**: Der Text in A1 ist 'neuertext'.
