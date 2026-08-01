---
agents: ['*']
app: buergergeld
attempts: 1
confidence: 0.85
description: Generiert ein formales Nachreichungs-Anschreiben als docx aus dem
  Status-Report (status-abgleich) und stammdaten.yaml. Listet offene Positionen,
  erklärt N/A-Fälle, verweist auf bereits Eingereichtes.
expected_state:
  description: ausgang/<datum>_Anschreiben_<Typ>.docx existiert mit Absender,
    Empfänger, Betreff, Unterschriftszeile. timeline.yaml hat "anschreiben_generiert".
  verification_tool: docx_verify_file
inputs:
  - name: report
    description: Pfad zu ausgang/<datum>_status_report.yaml
    required: true
last_adjusted: '2026-06-01T00:00:00'
name: buergergeld-anschreiben-generator
requires_approval: false
successes: 1
---

## Zweck

Baut programmatisch (python-docx) ein Behörden-Anschreiben. 3 Abschnitte:
nachzureichende Unterlagen, Erläuterungen zu N/A-Punkten, Hinweis auf bereits
Eingereichtes. Formal mit Absender/Empfänger/Betreff/Unterschrift.

## Aufruf

```bash
python anschreiben-generator/run.py ausgang/<datum>_status_report.yaml
python anschreiben-generator/run.py <report> --typ nachreichung
```

Typen: nachreichung | mietschuldenuebernahme | veraenderungsanzeige

## Verification

Generiertes docx muss Name + BG-Nr aus stammdaten.yaml enthalten,
Unterschriftszeile haben, offene Forderungen aus Report listen.
