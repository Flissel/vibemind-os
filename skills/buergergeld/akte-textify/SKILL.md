---
agents:
- '*'
app: buergergeld
attempts: 1
confidence: 1.0
description: Konvertiert PDFs (text + OCR fallback) und docx aus der Bürgergeld-Akte
  in .text-index/*.md, damit Fungus-Search semantisch indizieren kann. Idempotent
  über mtime-Vergleich.
expected_state:
  description: Für jede PDF/docx in eingang/, ausgang/, nachweise/ existiert eine
    entsprechende .md in .text-index/ und timeline.yaml hat ein Event "akte_textified".
  verification_tool: akte_textify_verify
inputs: []
last_adjusted: '2026-05-28T00:00:00'
name: buergergeld-akte-textify
requires_approval: false
successes: 0
---

## Zweck

Bereitet die Bürgergeld-Akte für semantische Suche vor. PDF/docx werden zu
Markdown konvertiert. Fungus-Search kann dann mit `fungus_reindex(codebase_path=
"~/Documents/Buergergeld/.text-index")` einen separaten Index aufbauen.

## Schritte

1. **Akten-Root finden** via `BUERGERGELD_ROOT` env oder `~/Documents/Buergergeld`.
2. **Quellen einsammeln** aus `eingang/`, `ausgang/`, `nachweise/**`.
3. **Pro Datei prüfen** ob `.text-index/<relpath>.md` existiert und hash gleich
   Quelle ist → skip.
4. **PDF parsen** via pypdf.
5. **docx parsen** via python-docx, Tabellen als Markdown-Tabellen.
6. **xlsx parsen** via openpyxl, Zellen zeilenweise als Markdown.
7. **Schreiben** nach `.text-index/<relpath>.md` mit YAML-Frontmatter
   (source-Pfad, mtime, Hash) für Idempotenz-Check.
8. **Timeline-Event** anhängen mit Anzahl konvertierter Dateien.

## Aufruf

```bash
python skills/buergergeld/akte-textify/run.py
python skills/buergergeld/akte-textify/run.py --dry-run
```

## Output

```
.text-index/
├── eingang/
│   └── 20260414_Jobcenter_MWS.pdf.md
├── nachweise/
│   ├── 01_Stellungnahme/...
│   └── ...
└── _manifest.yaml    ← Liste konvertierter Dateien mit hash/mtime
```
