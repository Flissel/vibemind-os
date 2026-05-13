---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: Stundenzettel-Monatsvorlage mit Wochen-Sheets (KW18-KW21) + Summary-Sheet
  mit Cross-Sheet-Formeln.
expected_state:
  description: Datei Stundenzettel_2026.xlsx existiert auf Disk und Verify ist success
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-04T15:08:47'
name: hr-stundenzettel-monatsvorlage
requires_approval: true
successes: 1
---

## Schritte zur Erstellung der Stundenzettel-Monatsvorlage

1. **Genehmigung einholen**: Anfrage zur Genehmigung für die Erstellung der Stundenzettel-Monatsvorlage gestellt.
2. **Excel-Datei erstellen**: Eine Excel-Datei mit fünf Sheets (KW18 bis KW21 und Summary) erstellt.
3. **Verifizierung**: Die erstellte Datei wurde erfolgreich verifiziert, um sicherzustellen, dass alle erforderlichen Texte vorhanden sind.
4. **Rowboat-Upload**: Der Upload zu Rowboat schlug fehl (Internal Server Error).

### Details der Excel-Datei
- **Dateipfad**: C:/Users/User/Desktop/HR/Stundenzettel_2026.xlsx
- **Sheets**: KW18, KW19, KW20, KW21, Summary
- **Formeln**: In den Wochen-Sheets für Stundenberechnung und Summen, im Summary-Sheet für Monatssumme

### Verifikation
- **Erwartete Texte**: 'Monatssumme', 'KW18'
- **Verifikation erfolgreich**: Ja

### Rowboat-Upload
- **Erfolg**: Nein, Internal Server Error

### Persistierung
- **Skill gespeichert und indexiert**: Ja
