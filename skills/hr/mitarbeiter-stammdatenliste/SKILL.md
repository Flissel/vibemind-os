---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: Mitarbeiter-Stammdatenliste mit allen relevanten HR-Feldern, Header bold
  + frozen, Status-Farbcodierung.
expected_state:
  description: Datei Mitarbeiter_Stammdaten_2026.xlsx existiert auf Disk und Verify
    ist success
  verification_tool: excel_verify_file
inputs: []
name: hr-mitarbeiter-stammdatenliste
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T11:49:33.712466+00:00'
---

## Schritte

1. **Erstellung der Datei**: Eine Mitarbeiter-Stammdatenliste wird direkt als .xlsx-Datei auf den Desktop geschrieben.
   - **Dateipfad**: `C:/Users/User/Desktop/HR/Mitarbeiter_Stammdaten_2026.xlsx`
   - **Tabellenblattname**: 'Stammdaten'
   - **Fettgedruckte Zeilen**: 1
   - **Automatische Spaltenbreite**: Ja
   - **Gefrorene Zeile**: A2
   - **Zellenstil**: Header-Zeile mit blauer Hintergrundfarbe und weißer Schrift

2. **Validierung der Datei**: Die Datei wird hart validiert, um sicherzustellen, dass sie korrekt erstellt wurde.
   - **Erwartete Zellen**: A1='ID', L1='Status'
   - **Mindestanzahl an Zeilen**: 11
   - **Mindestanzahl an Spalten**: 12
   - **Erforderliche Inhalte**: 'Müller', 'Schmidt', 'Eintrittsdatum'

3. **Upload zu Rowboat**: Die Datei wird zu Rowboat hochgeladen (wenn konfiguriert).

4. **Speicherung und Indexierung**: Der Skill wird mit den oben genannten Details gespeichert und indexiert.
