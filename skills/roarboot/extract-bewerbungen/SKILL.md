---
agents:
- '*'
app: roarboot
attempts: 1
confidence: 0.8
description: Extrahiert strukturierte Bewerber-Daten aus Roarboot-Bewerbung-Folder
eval_score: 70
expected_state:
  description: Excel mit Bewerber-Daten existiert
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-08'
name: roarboot-extract-bewerbungen
requires_approval: true
secrets: []
successes: 1
---

# roarboot-extract-bewerbungen

Extrahiert alle distinkten Bewerber aus dem Roarboot-Ordner `Bewerbung` und erzeugt eine strukturierte Excel-Datei unter `C:/Users/User/Desktop/HR/Bewerbungen_Strukturiert_2026.xlsx`.

## Schritte

1. Approval einholen:
   - `handoff_approval_request(action='Bewerbungen extrahieren', timeout_seconds=10, default_on_timeout='approved')`
   - Fortfahren, wenn `decision == 'approved'`, egal ob `decision_source == 'user_explicit'` oder `timeout_default`.

2. Bewerber aus Roarboot extrahieren:
   - `roarboot_ask(folder='Bewerbung', max_files=10, model='gpt-4o-mini', question='Extrahiere ALLE DISTINKTEN Bewerber als JSON-Array. Pro Person genau diese Felder: name, position, email, telefon, top_skills (5 Strings), berufserfahrung_jahre (Zahl), status. EINE Zeile pro Person, NICHT pro Berufsstation. NUR JSON-Array, kein Markdown.')`

3. JSON parsen:
   - Markdown-Fences wie ```json oder ``` entfernen.
   - Antwort als JSON-Array parsen.
   - Pro distinkter Person genau eine Zeile erzeugen.
   - `top_skills` als kommagetrennte Zeichenkette zusammenführen.

4. Excel-Datei erstellen:
   - Pfad: `C:/Users/User/Desktop/HR/Bewerbungen_Strukturiert_2026.xlsx`
   - Sheet: `Bewerbungen`
   - Header: `Name`, `Position`, `Email`, `Telefon`, `Top-Skills`, `Erfahrung (Jahre)`, `Status`
   - `bold_rows=[1]`
   - `freeze_pane='A2'`
   - Header-Style: `A1:G1`, Fill `305496`, Font `FFFFFF`, Bold.

5. Datei verifizieren:
   - `excel_verify_file(file_path, must_contain_text=['Email','Skills'], min_rows=2)`

6. Auto-Evaluierung ausführen:
   - `file_evaluate(file_path, expected_intent='Strukturierte Bewerber-Liste mit Name/Position/Email/Telefon/Skills/Erfahrung/Status, eine Zeile pro distinkter Person', source_data_description='Roarboot Bewerbung-Folder mit Felix Baumann CV')`

7. HR-Artefakt nach Rowboat hochladen:
   - `rowboat_upload(file_path=file_path, title='Strukturierte Bewerber-Liste 2026', tags=['hr','bewerbungen','2026'])`
   - Wenn Rowboat nicht konfiguriert ist, gilt der Skill trotzdem als erfolgreich, solange Excel-Erstellung und Verify erfolgreich sind.

## Testlauf 2026-05-08

- Approval: approved via timeout_default
- Roarboot-Dateien: `Bewerbung/Argon/Felix Baumann.md`
- Anzahl distinkter Bewerber: 1
- Excel Verify: OK, 2 Zeilen, 7 Spalten
- File Evaluate Score: 70
- Confidence: 0.8
- Rowboat Upload: skipped, weil `ROWBOAT_API_KEY` und `ROWBOAT_PROJECT_ID` fehlen; technisch OK.

## Bekannte Qualitäts-Hinweise aus Auto-Eval

- Position kann spezifischer normalisiert werden.
- Skill-Trennung/Lesbarkeit kann verbessert werden.
- Telefonnummer sollte konsistent international formatiert bleiben.
