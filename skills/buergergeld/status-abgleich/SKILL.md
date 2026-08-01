---
agents:
- '*'
app: buergergeld
attempts: 1
confidence: 0.85
description: Gleicht Forderungen aus einem geparsten Behördenschreiben mit dem
  Akten-Stand (todo.yaml + vorhandene Nachweise) ab. Output ist ein Diff-Report
  als Markdown + YAML. Erkennt erfüllt/offen/unklar pro Forderung.
expected_state:
  description: Für eine eingang/*.forderungen.yaml existiert ein ausgang/<datum>_status_report.{md,yaml}
    mit Match-Klassifikation. timeline.yaml hat "status_abgleich_done" Event.
  verification_tool: status_abgleich_verify
inputs:
  - name: forderungen_yaml
    description: Pfad zu eingang/<...>.forderungen.yaml aus parse-eingang
    required: true
last_adjusted: '2026-05-28T00:00:00'
name: buergergeld-status-abgleich
requires_approval: false
successes: 0
---

## Zweck

Nimmt eine geparste Forderungsliste (Output von parse-eingang) und gleicht
sie mit dem aktuellen Akten-Stand ab:
- `todo.yaml` als primäre Quelle (status: open/submitted/confirmed/na)
- Dateinamen in `nachweise/` als Heuristik (z.B. "Stellungnahme_Finanzierung_*.docx"
  → matched Forderung "stellungnahme-finanzierung")
- LLM-Reasoning für semantic match wenn ID-Match nicht eindeutig

## Output-Klassifikation pro Forderung

- `erfuellt` — todo-Item mit Status `confirmed` oder `submitted` existiert
- `offen` — todo-Item existiert mit Status `open` ODER kein Match in Akte
- `na` — todo-Item mit Status `na` (nicht zutreffend, im Schreiben erklärt)
- `unklar` — kein eindeutiger Match, manuelle Prüfung nötig

## Schritte

1. Forderungsliste laden
2. todo.yaml laden
3. Pro Forderung: regelbasierter Match (ID-Slug-Vergleich, Substring)
4. Für unklare Fälle: LLM-Call mit Kontext (Forderung + Kandidaten aus todo)
5. Report nach `ausgang/<datum>_status_report.md` + `.yaml`
6. Timeline-Event anhängen

## Aufruf

```bash
python skills/buergergeld/status-abgleich/run.py eingang/20260414_Jobcenter_MWS.pdf.forderungen.yaml
python skills/buergergeld/status-abgleich/run.py <pfad> --dry-run
```

## Verification

- Gegen das existierende MWS-Schreiben: erwartet ist eine sinnvolle Klassifizierung
  der 20 Forderungen — IAV-Items als offen, ID-Prüfung als erfüllt (laut todo.yaml
  status=confirmed), Gewerbeanmeldung als na (im Stellungnahme-Dokument erklärt).
