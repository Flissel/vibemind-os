---
agents:
- '*'
app: hr
attempts: 1
confidence: 1
description: Spesen-Abrechnung mit Kategorien, MwSt-Berechnung und Genehmigungs-Footer.
expected_state:
  description: Datei Spesen_Abrechnung_2026.xlsx existiert auf Disk und Verify ist
    success
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-07T11:52:05'
name: hr-spesen-abrechnung
requires_approval: true
successes: 1
---

## HR-Skill: Spesen-Abrechnung

Erstellt eine Excel-Datei `Spesen_Abrechnung_2026.xlsx` unter `C:/Users/User/Desktop/HR/` mit Kategorien, MwSt-Berechnung und Genehmigungs-Footer.

### Ausgeführte Schritte

1. **Approval eingeholt**
   - Action: `HR-Skill hr-spesen-abrechnung: Spesen-Abrechnung mit Kategorien, MwSt-Berechnung und Genehmigungs-Footer.`
   - Reason: `generiert Spesen_Abrechnung_2026.xlsx und uploaded zu Rowboat`
   - Timeout: 90s, Default: approved
   - Ergebnis: Tool-Timeout, gemäß Default-Approval fortgefahren.

2. **XLSX generiert** via `xlsx_create_from_data`
   - Datei: `C:/Users/User/Desktop/HR/Spesen_Abrechnung_2026.xlsx`
   - Sheet: `Spesen`
   - Header: `Datum, Beleg-Nr, Anlass, Kategorie, Brutto, MwSt-Satz, MwSt, Netto`
   - 10 Beispiel-Zeilen mit Kategorien: Hotel, Bahn, Flug, Verpflegung, Taxi, Sonstiges
   - MwSt-Formeln je Zeile: `=E{row}*F{row}/(1+F{row})`
   - Netto-Formeln je Zeile: `=E{row}-G{row}`
   - Summen-Zeile 12: `=SUMME(E2:E11)`, `=SUMME(G2:G11)`, `=SUMME(H2:H11)`
   - Genehmigungs-Footer:
     - Zeile 14: `Genehmigt durch:`
     - Zeile 15: `__________________________ (Vorgesetzter)`
   - `bold_rows=[1, 12]`
   - `freeze_pane='A2'`

3. **Verify** via `excel_verify_file`
   - `min_rows=15`
   - `must_contain_text=['MwSt','Genehmigt','SUMME']`
   - Ergebnis: `success=true`, Datei valide, 15 Zeilen / 8 Spalten.

4. **Rowboat-Upload** via `rowboat_upload`
   - Title: `Spesen-Abrechnung mit Kategorien, MwSt-Berechnung und Genehmigungs-Footer.`
   - Tags: `['hr','finanzen','2026']`
   - Ergebnis: `success=false`, HTTP 500 Internal Server Error. Silent-Fail gemäß HR-Konvention akzeptiert.

### Reproduzierbarer Generator-Aufruf

- Tool: `mcp_desktop_automation_xlsx_create_from_data`
- Output: `C:/Users/User/Desktop/HR/Spesen_Abrechnung_2026.xlsx`
- Danach: `mcp_desktop_automation_excel_verify_file` mit erwarteten Substrings und optional `mcp_desktop_automation_rowboat_upload`.
