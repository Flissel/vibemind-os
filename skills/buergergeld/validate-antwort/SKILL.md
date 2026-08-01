---
agents: ['*']
app: buergergeld
attempts: 1
confidence: 0.85
description: Cross-Checks vor dem Versand. Prüft Stammdaten-Vollständigkeit,
  Frist-Plausibilität, Anschreiben-Inhalt (Name/BG-Nr/Unterschrift), Konsistenz
  offener Forderungen. Gibt PASS/WARN/FAIL-Verdict. FAIL blockiert Approval-Gate.
expected_state:
  description: ausgang/<datum>_validation_report.yaml mit verdict + findings.
    timeline.yaml hat "validation_done".
  verification_tool: validate_antwort_verify
inputs:
  - name: report
    description: Pfad zu ausgang/<datum>_status_report.yaml
    required: true
last_adjusted: '2026-06-01T00:00:00'
name: buergergeld-validate-antwort
requires_approval: false
successes: 1
---

## Zweck

Haftungsrelevanter Cross-Check. Ersetzt KEINE menschliche Prüfung, fängt aber
offensichtliche Fehler ab bevor Felix gegenzeichnet. Checks:
- Stammdaten-Pflichtfelder (Name, BG-Nr, Adresse, Jobcenter)
- Frist-Plausibilität (überschritten? wie viele Tage?)
- Anschreiben enthält Name + BG-Nr + Unterschriftsbereich
- Konsistenz: verdächtige "offen"-Items die eigentlich N/A sind (z.B. Mieterhöhung)
- Rechtlicher Disclaimer (immer WARN)

## Aufruf

```bash
python validate-antwort/run.py ausgang/<datum>_status_report.yaml
python validate-antwort/run.py <report> --anschreiben ausgang/<datum>_Anschreiben_Nachreichung.docx
```

## Verdict

- PASS: keine Findings → grün
- WARN: nur Warnungen → Mensch prüft, kann fortfahren
- FAIL: harte Fehler (fehlende Pflichtdaten) → Approval-Gate blockiert
