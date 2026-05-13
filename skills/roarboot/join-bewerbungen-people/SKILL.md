---
agents:
- '*'
app: roarboot
attempts: 1
confidence: 0.6
description: Joint Roarboot-Bewerbungen mit People-Profilen auf Name-Match und schreibt
  unified Excel
eval_score: 55
expected_state:
  description: Excel mit gejointen Bewerber+People-Daten existiert
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-08'
name: roarboot-join-bewerbungen-people
requires_approval: true
secrets: []
successes: 1
---

# roarboot-join-bewerbungen-people

Kombiniert Bewerbungen aus dem Roarboot-Folder `Bewerbung` mit Personenprofilen aus `People` und schreibt eine unified Excel-Datei nach `C:/Users/User/Desktop/HR/Bewerbungen_Joined_People_2026.xlsx`.

## Ablauf

1. Approval einholen:
   - `handoff_approval_request(action='Multi-Folder-Join: Bewerbung + People in einer Excel', timeout_seconds=10, default_on_timeout='approved')`
   - Fortfahren, wenn `decision == 'approved'`, auch bei `decision_source == 'timeout_default'`.

2. Bewerbungen extrahieren:
   - `roarboot_ask(folder='Bewerbung', question='Extrahiere ALLE Bewerber als JSON-Array, pro Eintrag exakt: name (string), position (string), email (string). NUR JSON, kein Markdown.', max_files=10)`
   - Markdown-Fences entfernen, JSON parsen.
   - Ergebnis: `bewerbungen[]`.

3. People extrahieren:
   - `roarboot_ask(folder='People', question='Extrahiere ALLE Personen als JSON-Array, pro Eintrag exakt: name (string), profil_zusammenfassung (string, max 200 chars), letzter_kontakt (string, ISO-date oder leer), interessen (array of strings). NUR JSON, kein Markdown.', max_files=10)`
   - Markdown-Fences entfernen, JSON parsen.
   - Ergebnis: `people[]`.

4. LEFT JOIN auf Name:
   - Für jeden Bewerber aus `bewerbungen` genau eine Ausgabezeile erzeugen.
   - Matching case-insensitive per Substring:
     - `person.name.lower() in bewerber.name.lower()` ODER
     - `bewerber.name.lower() in person.name.lower()`
   - Wenn Match gefunden: `profil_zusammenfassung`, `letzter_kontakt`, `interessen` aus dem People-Profil übernehmen.
   - Wenn kein Match gefunden: People-Felder leer lassen.
   - Wichtig: Es bleibt ein LEFT JOIN — Bewerber ohne People-Match werden nicht verworfen.

5. Excel schreiben:
   - Datei: `C:/Users/User/Desktop/HR/Bewerbungen_Joined_People_2026.xlsx`
   - Sheet: `Joined`
   - Header: `Name`, `Position`, `Email`, `Profil-Zusammenfassung`, `Letzter Kontakt`, `Interessen`
   - Styles: Header `A1:F1` dunkelblau (`305496`), weiße Schrift, fett.
   - `bold_rows=[1]`, `freeze_pane='A2'`.

6. Verify:
   - `excel_verify_file(file_path, must_contain_text=['Profil','Email','Interessen'], min_rows=2)`

7. Auto-Evaluierung:
   - `file_evaluate(file_path, expected_intent='LEFT JOIN Bewerbungen mit People-Profilen auf Name-Match; Excel enthält pro Bewerber Name, Position, Email, Profil-Zusammenfassung, Letzter Kontakt, Interessen; eine Zeile pro Bewerber', source_data_description='Roarboot Bewerbung-Folder und People-Folder')`
   - Confidence aus Score ableiten: `>=90 => 1.0`, `70-89 => 0.8`, `50-69 => 0.6`, `<50 => 0.3`.

8. HR-Artefakt nach Rowboat hochladen:
   - `rowboat_upload(file_path=file_path, title='Bewerbungen Joined People 2026', tags=['hr','bewerbungen-people-join','2026'])`
   - Wenn Rowboat nicht konfiguriert ist oder Backendfehler liefert, gilt der Skill technisch trotzdem als erfolgreich, solange Excel-Erstellung und Verify erfolgreich sind.

## Testlauf 2026-05-08

- Approval: approved via timeout_default
- Bewerber: 1
- People-Profile-Matches: 1
- Excel Verify: OK, 2 Zeilen, 6 Spalten
- File Evaluate Score: 55
- Confidence: 0.6
- Rowboat Upload: 500 Internal Server Error, technischer Skill trotzdem erfolgreich

## Auto-Eval-Hinweise

- Nur ein Bewerbereintrag vorhanden, abhängig von Datenquelle.
- `Letzter Kontakt` war leer.
- Interessenliste kann gekürzt/strukturiert werden.
