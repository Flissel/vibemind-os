---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: Arbeitszeugnis-Vorlage als Word-Dokument mit Standard-Formulierungen
  und Platzhaltern.
expected_state:
  description: Datei Arbeitszeugnis_Vorlage_2026.docx existiert auf Disk und Verify
    ist success
  verification_tool: docx_verify_file
inputs: []
last_adjusted: '2026-05-04T14:42:46'
name: hr-arbeitszeugnis-vorlage
requires_approval: true
successes: 1
---

## Schritte zur Erstellung der Arbeitszeugnis-Vorlage

1. **Genehmigung einholen**: Anfrage zur Genehmigung der Erstellung der Arbeitszeugnis-Vorlage gestellt und genehmigt.
2. **Word-Dokument erstellen**: Die Datei `Arbeitszeugnis_Vorlage_2026.docx` wurde mit den erforderlichen Standard-Formulierungen und Platzhaltern erstellt.
3. **Verifizierung**: Die Datei wurde erfolgreich verifiziert, dass sie die erwarteten Texte und Strukturen enthält.
4. **Upload-Versuch**: Der Upload zur Rowboat Knowledge Base wurde versucht, jedoch trat ein interner Serverfehler auf.

### Details der Vorlage
- **Sektionen**: Tätigkeitsbeschreibung, Leistungsbewertung, Verhalten, Beendigung des Arbeitsverhältnisses, Schlussformel
- **Verifizierung**: Erfolgreich
- **Erwartete Texte**: Arbeitszeugnis, Leistungsbewertung, Schlussformel, {{name}}

### Upload
- **Erfolgreich**: Nein (Interner Serverfehler)
