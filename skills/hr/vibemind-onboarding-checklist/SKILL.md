---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Erstellt Onboarding-Checkliste fuer neuen VibeMind-Mitarbeiter direkt
  als xlsx via openpyxl, oeffnet in Excel, validiert via openpyxl
expected_state:
  description: Datei Onboarding_VibeMind_<Jahr>.xlsx liegt auf Desktop, hat min 22
    Zeilen, enthaelt Mitarbeiter-Name + VibeMind-spezifische Onboarding-Schritte (GitHub-Repo,
    OpenFang, MCP-Setup, Probezeit-Review)
  verification_tool: excel_verify_file
inputs:
- name: employee_name
  type: string
- name: start_date
  type: string
- name: role
  type: string
- name: buddy
  type: string
name: hr-vibemind-onboarding-checklist
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:59:36.720636+00:00'
---

## Schritte

1. **Erstellung der Datei**: Die Onboarding-Checkliste wird direkt als .xlsx-Datei auf den Desktop geschrieben, ohne Verwendung der Excel-UI zum Speichern.
   - **Dateipfad**: `C:/Users/User/Desktop/Onboarding_VibeMind_2026.xlsx`
   - **Tabellenblattname**: 'Onboarding'
   - **Fettgedruckte Zeilen**: 1, 5
   - **Automatische Spaltenbreite**: Ja

2. **Validierung der Datei**: Die Datei wird hart validiert, um sicherzustellen, dass sie korrekt erstellt wurde.
   - **Erwartete Zellen**: A1='Onboarding', A5='Phase', A6='Vorab', B5='Aufgabe'
   - **Mindestanzahl an Zeilen**: 22
   - **Erforderliche Inhalte**: 'Max Mustermann', 'VibeMind', 'GitHub', 'OpenFang', 'Probezeit'

3. **Öffnen in Excel**: Die erstellte Datei wird in Excel geöffnet, um die Sichtbarkeit zu überprüfen.

4. **Speicherung und Indexierung**: Der Skill wird mit den oben genannten Details gespeichert und indexiert.
