---
agents:
- '*'
app: hr
attempts: 1
confidence: 1.0
description: Offboarding-Checkliste mit Kündigungsfristen, IT-Asset-Rückgabe, Steuer/SV-Abmeldung.
expected_state:
  description: Datei Offboarding_Checkliste_2026.xlsx existiert auf Disk und Verify
    ist success
  verification_tool: excel_verify_file
inputs: []
last_adjusted: '2026-05-04T14:38:50'
name: hr-offboarding-checkliste
requires_approval: true
successes: 1
---

## Schritte zur Erstellung der Offboarding-Checkliste

1. **Genehmigung einholen**: Anfrage zur Genehmigung der Erstellung der Offboarding-Checkliste gestellt und genehmigt.
2. **Excel-Datei erstellen**: Die Datei `Offboarding_Checkliste_2026.xlsx` wurde mit den erforderlichen Aufgaben und Details erstellt.
3. **Verifizierung**: Die Datei wurde erfolgreich verifiziert, dass sie die erwarteten Texte enthält.
4. **Upload-Versuch**: Der Upload zur Rowboat Knowledge Base wurde versucht, jedoch trat ein interner Serverfehler auf.

### Details der Checkliste
- **Bereiche**: Personalwesen, IT, Finanzen, Übergabe
- **Aufgaben**: Kündigung schriftlich bestätigen, Resturlaub berechnen, Arbeitszeugnis-Auftrag, Exit-Interview, Laptop einziehen, etc.

### Verifizierung
- **Erfolgreich**: Ja
- **Erwartete Texte**: VPN, SV-Abmeldung, Arbeitszeugnis

### Upload
- **Erfolgreich**: Nein (Interner Serverfehler)
