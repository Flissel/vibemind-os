# Bürgergeld Skill-Suite

Skills für die Verwaltung eines laufenden Bürgergeld-Vorgangs (SGB II).
Domäne: Privat-Antrag eines einzelnen Antragstellers, kein Multi-Tenant-Service.

## Status: Phasen 0-4 fertig (CLI-Pfad funktioniert end-to-end)

Sprach-/Brain-Aktivierung braucht laufenden Stack; CLI-Pipeline läuft jetzt.

## Wofür diese Suite *nicht* ist

- **Keine Rechtsberatung.** Generiert Schreiben aus Daten, ersetzt keinen Anwalt.
- **Kein Multi-Tenant-Service.** Würde unter das RDG fallen.
- **Keine autonome Behörden-Kommunikation.** Stoppt vor Versand am Approval-Gate.

## Pipeline

```
parse-eingang → status-abgleich → anschreiben-generator → sammel-pdf-builder
              → validate-antwort → APPROVAL-GATE (Mensch prüft + sendet manuell)
```

Orchestriert durch `orchestrate.py` (CLI) bzw. Brain multihop_execute (Voice).

## Datenschicht (lokal, NICHT in Git)

```
~/Documents/Buergergeld/
  stammdaten.yaml    timeline.yaml    todo.yaml
  eingang/   ausgang/   nachweise/01-05/   kontoauszuege/   .text-index/   .backups/
```

## Sicherheits-Modell

Sensible Daten (BG-Nr, IBAN, Beträge) bleiben LOKAL. NIE in Rowboat-KB
(plaintext Mongo+Qdrant), NIE in Git. Nur generisches Domänenwissen (SGB,
Templates) darf in Rowboat-KB.

## Skill-Inventar

| Skill | Status | Zweck |
|---|---|---|
| `akte-textify` | ✅ | PDFs/docx aus nachweise/ → MD in .text-index/ (für Fungus) |
| `parse-eingang` | ✅ | Behörden-PDF → strukturierte Forderungsliste (LLM, Groq) |
| `status-abgleich` | ✅ | Forderungen × todo.yaml × Akte → Lücken-Report (md+yaml) |
| `anschreiben-generator` | ✅ | docx-Anschreiben aus stammdaten + Status-Report (python-docx) |
| `sammel-pdf-builder` | ✅ | docx → PDF (Word COM), merge pro Abschnitt + Deckblatt |
| `validate-antwort` | ✅ | Cross-Checks (Frist, Pflichtfelder, Konsistenz) → PASS/WARN/FAIL |
| `orchestrate.py` | ✅ | CLI-Orchestrator: ganze Pipeline bis Approval-Gate |
| `einkommensaufstellung-update` | geplant | Kontoauszüge → Excel-Spalte ergänzen |
| `iav-mail-tracker` | geplant | Gmail-Label polling + Auto-Forward |
| `veraenderungsanzeige` | geplant | § 60 SGB I Meldung-Generator |

## Nutzung (CLI)

Kompletter Antrags-Flow in einem Befehl:

```bash
cd vibemind-os/skills/buergergeld
python orchestrate.py --pdf eingang/<schreiben>.pdf
```

Läuft: parse → abgleich → anschreiben → sammel-pdf → validate → Approval-Gate.
Output in `~/Documents/Buergergeld/ausgang/`. KEIN automatischer Versand.

Einzelne Skills:
```bash
python parse-eingang/run.py eingang/<schreiben>.pdf
python status-abgleich/run.py eingang/<schreiben>.pdf.forderungen.yaml
python anschreiben-generator/run.py ausgang/<datum>_status_report.yaml
python sammel-pdf-builder/run.py --abschnitt 02_Einkommen   # oder --alle
python validate-antwort/run.py ausgang/<datum>_status_report.yaml
```

## Voice / Brain (Phase 4)

- Voice-Intents: `voice/python/swarm/orchestrator/intent_classifier.py` (Space #12)
- Backend-Agent: `spaces/buergergeld/agents/buergergeld_agent.py` (Stream `events:tasks:buergergeld`)
- Brain-Plan: `brain/the_brain/plans/buergergeld_nachreichung.yaml`
- Capabilities: `brain/the_brain/data/capabilities.yaml` (buergergeld_parse_eingang, buergergeld_status_abgleich)

Sprachbefehle:
- "Bereite die Antwort ans Jobcenter vor" → `buergergeld.vorbereiten` (komplette Pipeline)
- "Was fehlt noch für meinen Antrag" → `buergergeld.status`
- "Lies das Schreiben vom Jobcenter" → `buergergeld.parse`

**Aktivierung braucht laufenden Stack** (Brain-Restart lädt capabilities.yaml,
Voice-Daemon lädt intent_classifier.py neu). Bis dahin: `orchestrate.py`.

## LLM-Config

`_lib/llm_config.yml` — Groq direct (free tier, OpenAI-API-kompatibel).
OpenRouter-Account hatte keine Credits für Claude/Sonnet, daher Groq
llama-3.3-70b für parse + abgleich. Bei Bedarf in llm_config.yml umstellen.

## Wichtige Implementierungs-Notiz

Write/Edit-Tool versagt auf manchen Pfaden in diesem Ordner **lautlos** (Dirs
mit `_`-Präfix, manche Datei-Erstellungen). Nach jedem Schreiben mit
`find ... -type f` verifizieren dass die Datei wirklich existiert.
