---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: Lohnabrechnung-Hilfstabelle mit echten Beitragsformeln (RV, KV, AV, PV,
  LSt) und Parameter-Sheet.
expected_state:
  description: Datei Lohnabrechnung_Hilfstabelle_2026.xlsx existiert auf Disk und
    Verify ist success
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-04T15:01:47'
name: hr-lohnabrechnung-hilfstabelle
requires_approval: true
successes: 1
---

## Schritte zur Erstellung der Lohnabrechnung-Hilfstabelle

1. **Genehmigung einholen**: Anfrage zur Genehmigung für die Erstellung der Lohnabrechnung-Hilfstabelle gestellt.
2. **Excel-Datei erstellen**: Eine Excel-Datei mit zwei Sheets (Parameter und Abrechnung) erstellt.
3. **Verifizierung**: Die erstellte Datei wurde erfolgreich verifiziert, um sicherzustellen, dass alle erforderlichen Texte vorhanden sind.
4. **Rowboat-Upload**: Der Upload zu Rowboat schlug fehl (Internal Server Error).

### Details der Excel-Datei
- **Dateipfad**: C:/Users/User/Desktop/HR/Lohnabrechnung_Hilfstabelle_2026.xlsx
- **Sheets**: Parameter, Abrechnung
- **Formeln**: In der Abrechnung-Sheet für LSt, Soli, RV-AN, KV-AN, AV-AN, PV-AN, Netto

### Verifikation
- **Erwartete Texte**: 'Brutto', 'Netto', 'Parameter'
- **Verifikation erfolgreich**: Ja

### Rowboat-Upload
- **Erfolg**: Nein, Internal Server Error

### Persistierung
- **Skill gespeichert und indexiert**: Ja
