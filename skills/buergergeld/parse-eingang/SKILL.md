---
agents:
- '*'
app: buergergeld
attempts: 1
confidence: 0.9
description: Parsed ein Behörden-PDF (z.B. Jobcenter MWS) in strukturierte Forderungsliste
  YAML. Nutzt LLM (Claude Sonnet 4.5 via OpenRouter) mit deterministischem
  Schema. Hängt Event in timeline.yaml.
expected_state:
  description: Für eine PDF in eingang/ existiert eine entsprechende .forderungen.yaml
    mit forderungen[], absender, frist, eingangsdatum. timeline.yaml hat ein
    "schreiben_eingang_parsed" Event.
  verification_tool: parse_eingang_verify
inputs:
  - name: pdf_path
    description: Pfad zur Behörden-PDF (relativ oder absolut)
    required: true
last_adjusted: '2026-05-28T00:00:00'
name: buergergeld-parse-eingang
requires_approval: false
successes: 0
---

## Zweck

Liest ein Behörden-PDF aus `eingang/` und extrahiert strukturiert:
- Absender (Jobcenter, Agentur für Arbeit, Krankenkasse, etc.)
- Datum + Frist
- Liste der Forderungen mit Kategorie + zitierten Paragraphen
- Verweise auf bestehende Akten-Items wenn erkennbar

## Schritte

1. **Akten-Root resolven** via `BUERGERGELD_ROOT` oder Default
2. **PDF-Text extrahieren** via pypdf (Text-Layer)
3. **LLM-Call** mit Sonnet 4.5, strukturierter Prompt + Schema
4. **YAML-Output** validieren (Pydantic), schreiben nach
   `eingang/<dateiname>.forderungen.yaml`
5. **Timeline-Event** anhängen via `_lib.timeline_helper.append_event`

## LLM-Schema (Output)

```yaml
absender:
  name: "Jobcenter München"
  art: jobcenter | agentur_fuer_arbeit | krankenkasse | sonstige
  adresse: "Berg-am-Laim-Str. 47, 81673 München"

bezug:
  bg_nummer: "84308//0200188"
  kundennummer: "843E387738"

datum: 2026-04-14
frist: 2026-04-28
betreff: "Mitwirkungsersuchen — fehlende Unterlagen"

forderungen:
  - id: stellungnahme_finanzierung
    titel: "Schriftliche Stellungnahme zur Finanzierung"
    kategorie: persoenliche_daten | einkommen | unterkunft | vermoegen | sonstiges
    beschreibung: "..."
    paragraph: "§ 60 SGB I" (optional)

konsequenzen:
  - "Bei Nichteinreichung können Leistungen versagt werden (§§ 60, 66, 67 SGB I)"
```

## Aufruf

```bash
python skills/buergergeld/parse-eingang/run.py eingang/20260414_Jobcenter_MWS.pdf
python skills/buergergeld/parse-eingang/run.py --pdf <pfad> --dry-run
```

## Output

`eingang/<dateiname>.forderungen.yaml` mit YAML-Frontmatter
(source_pdf, parsed_at, llm_model).

## Verification

- LLM-Output muss valides YAML sein → Pydantic-Parse
- Mindestens 1 Forderung gefunden
- Absender + Frist erkannt
- Test gegen `eingang/20260414_Jobcenter_MWS.pdf`: erwartet 13 Forderungen
  in 4 Kategorien (persönliche Daten, Einkommen, Unterkunft, Vermögen)
