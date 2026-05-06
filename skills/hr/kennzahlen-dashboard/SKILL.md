---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: HR-Kennzahlen-Dashboard mit Daten-Sheet (monatlich) und KPIs-Sheet (Fluktuation,
  Krankenquote, Recruiting-Trichter).
expected_state:
  description: Datei HR_Kennzahlen_Dashboard_2026.xlsx existiert auf Disk und Verify
    ist success
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-04T15:02:50'
name: hr-kennzahlen-dashboard
requires_approval: true
successes: 1
---

## Schritte zur Erstellung des HR-Kennzahlen-Dashboards

1. **Genehmigung einholen**: Anfrage zur Genehmigung für die Erstellung des HR-Kennzahlen-Dashboards gestellt.
2. **Excel-Datei erstellen**: Eine Excel-Datei mit zwei Sheets (Daten und KPIs) erstellt.
3. **Verifizierung**: Die erstellte Datei wurde erfolgreich verifiziert, um sicherzustellen, dass alle erforderlichen Texte vorhanden sind.
4. **Rowboat-Upload**: Der Upload zu Rowboat schlug fehl (Internal Server Error).

### Details der Excel-Datei
- **Dateipfad**: C:/Users/User/Desktop/HR/HR_Kennzahlen_Dashboard_2026.xlsx
- **Sheets**: Daten, KPIs
- **Formeln**: In der KPIs-Sheet für Fluktuationsrate, Kranken-Quote, Recruiting-Conversion, Interview-zu-Hire, Avg Headcount, Total Eintritte, Total Austritte

### Verifikation
- **Erwartete Texte**: 'Fluktuation', 'Kranken', 'Recruiting'
- **Verifikation erfolgreich**: Ja

### Rowboat-Upload
- **Erfolg**: Nein, Internal Server Error

### Persistierung
- **Skill gespeichert und indexiert**: Ja
