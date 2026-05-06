---
agents:
- '*'
app: excel
attempts: 1
confidence: 0.0
description: Shift+F11 = neues Tabellenblatt.
expected_state:
  description: Ein neues Tabellenblatt wird erstellt, die Anzahl der Tabs erhöht sich
    um eins.
  verification_tool: vision_analyze
inputs: []
last_adjusted: '2026-05-04T12:28:00'
name: excel-new-sheet
requires_approval: true
successes: 0
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Genehmigung des Lernlaufs wurde automatisch erteilt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Anzahl der Tabs ermitteln**: Vor dem Test waren 3 Tabs sichtbar.
4. **Neues Tabellenblatt erstellen**: Shift+F11 wurde ausgeführt.
5. **Ergebnis validieren**: Die Anzahl der Tabs blieb unverändert bei 3.

## Fehleranalyse
- **Fehlerursache**: Die erwartete Erhöhung der Tab-Anzahl trat nicht ein.
- **Mögliche Ursachen**: Falsche Tastenkombination, Excel reagiert nicht wie erwartet.
- **Nächste Schritte**: Überprüfung der Tastenkombination und Excel-Einstellungen.
