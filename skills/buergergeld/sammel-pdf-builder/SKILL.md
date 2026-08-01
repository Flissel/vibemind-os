---
agents: ['*']
app: buergergeld
attempts: 1
confidence: 0.85
description: Bündelt Akten-Abschnitte (01-05) zu Sammel-PDFs. Konvertiert docx →
  PDF via MS Word COM (docx2pdf), Fallback reportlab. Merge mit vorhandenen PDFs,
  Deckblatt mit Inhaltsverzeichnis voran.
expected_state:
  description: ausgang/<datum>_Sammel_<Abschnitt>.pdf existiert mit Deckblatt +
    allen Abschnitts-Dateien. timeline.yaml hat "sammel_pdf_gebaut".
  verification_tool: pdf_verify_file
inputs:
  - name: abschnitt
    description: Abschnittsname z.B. 02_Einkommen (oder --alle)
    required: false
last_adjusted: '2026-06-01T00:00:00'
name: buergergeld-sammel-pdf-builder
requires_approval: false
successes: 1
---

## Zweck

Erstellt einreichfertige Sammel-PDFs pro Akten-Abschnitt. docx→PDF nutzt
MS Word COM (hier verfügbar), Fallback reportlab-Text-Rendering. _root_-Duplikate
und Lock-Files werden übersprungen.

## Aufruf

```bash
python sammel-pdf-builder/run.py --abschnitt 02_Einkommen
python sammel-pdf-builder/run.py --alle
python sammel-pdf-builder/run.py --dateien <pfad1> <pfad2>
```

## Verification

Generiertes PDF hat Deckblatt (Seite 1) + alle konvertierten Dokumente.
Getestet: 04_Vermögen=4 Seiten, 02_Einkommen=69 Seiten, 03_Unterkunft=35 Seiten.
