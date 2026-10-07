# Marketing-Ops — Status snapshot

> **Cockpit evidence contract:** [COCKPIT_CONTRACT.md](docs/COCKPIT_CONTRACT.md)
> is authoritative for the static inventory (migrations, event-to-tool mappings,
> pytest definitions) and for the rule that nothing is `verified_live` without
> fresh evidence. This file is a dated snapshot; the numbers were measured on
> the committed tree, not read from older notes.

**Stand: 2026-10-07, Code-Stand origin/master 7c86e868.**

Einstieg: [README.md](README.md) (Aufbau, Dienste, Betrieb), [AGENTS.md](AGENTS.md)
(Regeln, Gate-Graph).

## Messwerte (statisch, aus dem Repo-Stand)

| Groesse | Wert | Wo nachzaehlen |
|---|---|---|
| HTTP-Routen | 133 Dekoratoren plus 2 statische Mounts (`/mockup`, `/curator`) | `api/*.py` |
| davon `api/server.py` | 84 | |
| `api/pult.py` (`/api/pult/*`) | 16 | |
| `api/bilder.py` | 10 | |
| `api/chat.py` | 18 | |
| `api/medien_mandant.py` | 4 | |
| `api/gestaltung.py` | 1 | |
| Migrationsdateien (nummeriert) | 62: `001`-`062`, `039` fehlt, zwei Dateien `013` | `db/` |
| Pruefskripte `verify_*.sql` | 21 | `db/` |
| Testdateien (`test_*.py`) | 76 | unter `spaces/marketing` |
| Testdefinitionen (`def test_*`, AST-Zaehlung) | 1285 | |
| MCP-Werkzeuge definiert / im Sidecar registriert | 27 / 27 | `claw/werkzeuge.py`, `claw/server.py` (`WERKZEUGE`) |
| MCP-Werkzeuge in der Shim-Allow-List (`mcp__marketing__*`) | 25 | `claw/shim/marketing-mcp.json` |
| `EVENT_TO_TOOL` im MarketingBackendAgent | 13 | `agents/marketing_agent.py` |
| Operationen des Gestaltungs-Agenten | 17 | `claw/agent_werkzeuge.py` |
| n8n-Workflows | 7 (Nummer 06 fehlt) | `n8n_workflows/` |

Die Zahlen sind Zaehlungen, keine Aussage, dass Migrationen angewendet wurden
oder die Tests gruen laufen. Ein Gesamtlauf der Suite wurde fuer diesen Stand
nicht ausgefuehrt; nur der Drift-Waechter `tests/test_cockpit_contract.py`.

## Phase

Marketing versendet nichts selbst. Seit dem Betreiber-Entscheid vom 2026-09-12
ist sales-claw der einzige Versandweg: `versand_beauftragen` -> `POST
/api/versandauftraege` -> `marketing.versandauftrag_anlegen` (Migration 043) ->
hoechstens ein offener Entwurf in sales-claw, den ein Mensch freigibt. Die
Versender `_send_paranoid.py`, `_send_telegram.py`, `_send_openfang.py` sind
durch `tools/versandsperre.py` im LIVE-Modus gesperrt (Umgehung nur mit
`MARKETING_VERSAND_TROTZDEM=1`, wird als Warnung protokolliert). Fuer
Versandauftraege akzeptiert das Werkzeug `whatsapp`, `email`, `linkedin`,
`linkedin_post`.

Mutierende Vorschlags-Routen bleiben hinter `MARKETING_PROPOSAL_API_KEY`
(503 bei fehlender Umgebungsvariable, fail-closed).

## Components live

"Live" ist hier nur, was ein datiertes Dokument oder ein datierter Commit
ausdruecklich belegt. Alles andere ist statisch (Code vorhanden).

| Komponente | Port | Beleg / Einordnung |
|---|---|---|
| marketing-api | 5510 | Code `api/server.py`; Startskript benennt den Dienst; laut Kommentar im Startskript lagen Dienste vom 12. bis 15.09.2026 still (gemessen 15.09.) |
| marketing-claw MCP-Sidecar | 8130 | Code `claw/server.py`; E2E-Beleg liegt ausserhalb dieses Verzeichnisses (Projektnotiz), hier nicht neu geprueft |
| Marketing-Shim | 8117 | Code `claw/shim/marketing_shim.py`, vom Startskript mit `SHIM_EXTRA_MCP_CONFIG` gestartet; Gateway-Seed zeigt auf :8117 |
| Vorlagen-, Bild-, Chat-Arbeiter | 8132, 8133, 8134 | Code vorhanden, vom Startskript gestartet; Migrationen 047-049, 056-061 |
| ComfyUI (FLUX.1-schnell) | 8188 | `claw/bild_comfy.py`, Workflows `bilder/*.json` |
| Mandanten `vibemind`, `fin2gether` | | Commit 2026-10-06 "Migration 062 Bildzuordnung je Mandant, fin2gether aktiv"; Markenwissen-Commits vom selben Tag |
| marketing-api auf der VM | 5510 (loopback, Tailnet) | laut Betriebsnotiz seit 2026-09-25; Skript im Submodul sales-claw, hier nicht pruefbar |
| openclaw-Gateway `marketing-claw` | 18895 | `claw/gateway/docker-compose.yml`; Healthcheck-Kommentar gemessen 2026-09-03 |
| Worker A/B/C (`sync/`) | | Code vorhanden; kein datierter Lauf-Beleg in diesem Verzeichnis |
| Worker D `delivered_webhook` | 5512 | Code vorhanden; schreibt als einziger `delivered_at` |

## HTTP routes

133 Routen plus die Mounts `/mockup` und `/curator` (Zaehlung oben). Auth:
global `X-API-Key` (`MARKETING_API_KEY`); `/api/pult/*` zusaetzlich `X-Pult-Key`
(`MARKETING_PULT_KEY`); Arbeiter-Routen `X-Bild-Key` (`MARKETING_BILD_KEY`);
mutierende Vorschlags-Routen `MARKETING_PROPOSAL_API_KEY`. Die vollstaendige
Liste steht im Code (`grep` auf `@router.` und `@app.` in `api/`), nicht hier.

## Migrations

62 nummerierte Dateien `001`-`062` in `db/`; `039` fehlt; zwei Dateien tragen
`013` (`013_compliance_sperrliste.sql`, `013_metrics_views.sql`). Gruppen:
001-038 Grundschema, Sync, Webhooks, Tracking, Bubble-Pipeline; 040
Crowdfunding; 041-043 Bruecke zu sales-claw (`versandauftraege`); 044-049
Layout- und Formularvorlagen; 050-052 Pult; 053-055 Newsletter-Bloecke;
056-059 Bildauftraege, Ueberarbeiten, Profi-Vorlagen, Freistellen; 060
Gestaltung und `chat_auftraege`; 061 Chat live; 062 `medien_mandant`.

## Bekannte Luecken

- **Allow-List und Sidecar stimmen nicht ueberein.** Der Sidecar registriert 27
  Werkzeuge. Die Allow-List `claw/shim/marketing-mcp.json` fuehrt 25 davon;
  `newsletter_bildplaetze` und `newsletter_bild_beauftragen` fehlen. Die
  Claude-CLI (`--strict-mcp-config`) kann sie ueber den Shim damit nicht rufen.
  Dieselbe Liste enthaelt 26 `mcp__laura__*`-Eintraege, darunter schreibende wie
  `import_media`, `edit_timeline`, `render_timeline`. Codeaenderung steht aus.
- **Veraltete Kopfzeilen:** `api/server.py` nennt sich "Phase 1 read-only API"
  und "all read-only in Phase 1"; die Datei hat laengst mutierende Routen.
- **Port-Vorgaben auf :8114:** `claw/llm.py` (`MARKETING_CLAW_LLM_URL`),
  `claw/shim/marketing_shim.py` (`--port`-Vorgabe) und
  `workers/bubble_classifier_runner.py` zeigen standardmaessig auf die
  gemeinsame Shim-Instanz :8114. Die Marketing-Instanz ist :8117; das
  Startskript und das Gateway setzen das explizit.
- **n8n:** Workflow `06_*` fehlt in der Nummernfolge (vorhanden: 01-05, 07, 08);
  das README dort fuehrt nur 01-03.
- **Telegram:** Das CHECK in Migration 043 erlaubt `telegram`, das Werkzeug
  `versand_beauftragen` nicht (kein Telegram-Dispatcher in sales-claw).
- **Dokumentation:** Detaildokumente unter `docs/` stammen teils aus Juni/Juli
  2026 und beschreiben den Stand vor der Versandsperre.
