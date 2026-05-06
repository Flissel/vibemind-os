---
agents:
- '*'
app: excel
attempts: 1
confidence: 0.0
description: Doppelklick auf Sheet-Tab oder F2/Alt+H,O,R = Sheet umbenennen.
expected_state:
  description: Der Name des aktiven Tabellenblatts wird zu 'MeinNeuerName' geändert.
  verification_tool: vision_analyze
inputs:
- name: new_name
  type: string
last_adjusted: '2026-05-04T12:30:00'
name: excel-rename-sheet
requires_approval: true
successes: 0
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Genehmigung des Lernlaufs wurde automatisch erteilt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Tab umbenennen**: Alt+H, O, R wurde ausgeführt, gefolgt von der Eingabe 'MeinNeuerName' und Enter.
4. **Ergebnis validieren**: Der Name des aktiven Tabs wurde nicht geändert.

## Fehleranalyse
- **Fehlerursache**: Der Tab-Name wurde nicht wie erwartet geändert.
- **Mögliche Ursachen**: Falsche Tastenkombination, Excel reagiert nicht wie erwartet.
- **Nächste Schritte**: Überprüfung der Tastenkombination und Excel-Einstellungen.
