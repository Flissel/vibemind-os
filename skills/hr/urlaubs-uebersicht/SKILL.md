---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: Urlaubs-Übersicht mit Anträge-Sheet (Tage via NETTOARBEITSTAGE) + Resturlaub-Sheet.
expected_state:
  description: Datei Urlaubs_Uebersicht_2026.xlsx existiert auf Disk und Verify ist
    success
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-04T15:11:01'
name: hr-urlaubs-uebersicht
requires_approval: true
successes: 1
---

## Schritte zur Erstellung der Urlaubs-Übersicht

1. **Genehmigung einholen**: Anfrage zur Genehmigung für die Erstellung der Urlaubs-Übersicht gestellt.
2. **Excel-Datei erstellen**: Eine Excel-Datei mit zwei Sheets (Anträge und Resturlaub) erstellt.
3. **Verifizierung**: Die erstellte Datei wurde erfolgreich verifiziert, um sicherzustellen, dass alle erforderlichen Texte vorhanden sind.
4. **Rowboat-Upload**: Der Upload zu Rowboat schlug fehl (Internal Server Error).

### Details der Excel-Datei
- **Dateipfad**: C:/Users/User/Desktop/HR/Urlaubs_Uebersicht_2026.xlsx
- **Sheets**: Anträge, Resturlaub
- **Formeln**: In den Anträge-Sheet für Tageberechnung via NETTOARBEITSTAGE, im Resturlaub-Sheet für Restberechnung

### Verifikation
- **Erwartete Texte**: 'Resturlaub', 'genehmigt', 'NETTO'
- **Verifikation erfolgreich**: Ja

### Rowboat-Upload
- **Erfolg**: Nein, Internal Server Error

### Persistierung
- **Skill gespeichert und indexiert**: Ja
