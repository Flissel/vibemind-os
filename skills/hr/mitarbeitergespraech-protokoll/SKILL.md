---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: Mitarbeitergespräch-Protokoll als Word-Dokument mit Sektionen für Rückblick,
  Ziele, Entwicklung, Gehalt.
expected_state:
  description: Datei Mitarbeitergespraech_Protokoll_2026.docx existiert auf Disk und
    Verify ist success
  verification_tool: docx_verify_file
inputs: []
last_adjusted: '2026-05-04T14:40:50'
name: hr-mitarbeitergespraech-protokoll
requires_approval: true
successes: 1
---

## Schritte zur Erstellung des Mitarbeitergespräch-Protokolls

1. **Genehmigung einholen**: Anfrage zur Genehmigung der Erstellung des Mitarbeitergespräch-Protokolls gestellt und genehmigt.
2. **Word-Dokument erstellen**: Die Datei `Mitarbeitergespraech_Protokoll_2026.docx` wurde mit den erforderlichen Sektionen und Details erstellt.
3. **Verifizierung**: Die Datei wurde erfolgreich verifiziert, dass sie die erwarteten Texte und Strukturen enthält.
4. **Upload-Versuch**: Der Upload zur Rowboat Knowledge Base wurde versucht, jedoch trat ein interner Serverfehler auf.

### Details des Protokolls
- **Sektionen**: Rückblick, Ziele, Entwicklungsfelder, Gehalt
- **Verifizierung**: Erfolgreich
- **Erwartete Texte**: Mitarbeitergespräch, Entwicklungsfelder, Unterschrift

### Upload
- **Erfolgreich**: Nein (Interner Serverfehler)
