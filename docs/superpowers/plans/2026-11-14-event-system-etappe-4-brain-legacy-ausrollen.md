# Event-Management-System Etappe 4: Brain, Legacy, Ausrollen – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Teil B (Live) wird NICHT von Subagenten ausgeführt**, sondern vom Controller selbst, Phase für Phase, jeweils nach ausdrücklichem OK von Felix.

**Goal:** Das in Etappe 1–3 gebaute Event-System geht auf `offload-vm` in Betrieb. Die Legacy-Festivalgruppe läuft dabei ohne Unterbrechung über `ev-legacy` weiter, und der Brain arbeitet über eigene Betreiber-Schlüssel mit dem Katalog.

**Architecture:** Der Plan hat zwei Teile.
- **Teil A (Tasks 1–6)** ist reine Repo-Arbeit und läuft lokal gegen den Wegwerf-Postgres, mit Prüf-Doubles statt Live-Diensten:
  - Images für Fachschicht und Oberfläche bauen.
  - `ev-legacy` und den Legacy-Katalogeintrag anlegen.
  - Den Brain auf eigene Schlüssel umstellen und die `verlauf`-Prüfung einbauen.
  - Die 80-%-Warnung einbauen.
  - Ein Ausroll-Skript schreiben, das jede Phase nur mit Schalter ausführt.
- **Teil B (Tasks 7–9)** ist der Live-Betrieb: messen, ausrollen, abnehmen. Jede Phase hat eine Rückweg-Anweisung und braucht Felix' OK.

**Tech Stack:**
- Python 3.12, FastAPI, psycopg 3 und `mcp` 1.27 für `event-api` und `event-mcp`.
- Next 16 und React 19 für `event-web`.
- Docker Compose (Profil `event-system`), OpenClaw 2026.7.1, systemd-Timer.
- Supabase-Postgres auf `offload-vm`.
- Brain (`brain/the_brain`) in vibemind-os. Das Budget-Modul liegt in `scripts/claude_budget.py` im äußeren Repo.

**Spec:** `docs/superpowers/specs/2026-11-12-event-management-system-design.md`, Abschnitte 4 bis 7 und „Etappen“ Punkt 4. Dazu gehören:
- die Ledger-Archive `.superpowers/etappe2-ledger-archiv.md` und `.superpowers/etappe3-ledger-archiv.md` im Worktree (dort stehen die Carries „→ Etappe 4“);
- `spaces/event_management/deploy/messen.md` mit den Messpunkten M1–M6 und dem Abschnitt „Ausrollen“.

## Global Constraints

- **Nichts Live ohne Felix' OK, je Phase einzeln.** Das betrifft:
  - Deploy, Build auf der VM, Migrationen auf Supabase, `anbinden.sh` und `messen.sh`;
  - systemd-Units, `tailscale serve` und Brain-Umgebung.

  Ein OK gilt nur für die genannte Phase. Teil A berührt nichts Live.
- Vor Live-Arbeit ist ein Claim Pflicht:
  - im WORKBOARD des äußeren Repos;
  - in der maschinenweiten Koordination `C:\Users\User\Desktop\secondbrain\00_Meta\002_Koordination_Live.md`.

  Nur die eigene Zeile stagen.
- Nie `service update` oder `stack deploy` auf dem vibemind-Swarm. Die Brain-Umgebung wird nur über den dokumentierten Launcher- bzw. Secret-Weg gesetzt.
- `sales-auto` nicht starten. Nichts an pm-claw ändern. ComfyUI nie beenden. Prozesse nur per exakter PID beenden.
- **Keine Geheimnisse in Git, Logs, argv oder Ausgaben.**
  - Das gilt für Schlüssel (`evk_…`), Bot- und Gateway-Token, DSN und Prüfcodes.
  - Werte gehen nur über stdin oder Dateien mit Modus 0600 (`laufzeit/`, `_FILE`-Variablen).
  - `bot\d+:…` immer maskieren.
- vibemind-os ist **ÖFFENTLICH**. Ins Repo kommen keine echten Firmen, Personen, Gruppen- oder Chat-IDs, Adressen, Event-Namen, Daten oder Orte. Echte Werte stehen nur in `werte.local.json` und `laufzeit/` auf der VM. Beide sind nicht versioniert.
- **Ladenfest:** Der zweite Laden bucht nie auf Schema oder IDs des Betreibers. Für dieses System heißt das: Firmen werden nur über `event`-Schema und RLS getrennt, es gibt keine geteilten IDs mit sales-claw.
- **Builds auf der VM nur gedeckelt.** Memory „VM-Web-Build nur gedeckelt“: BuildKit ohne Deckel bringt die VM in Swap-Not. Builds laufen deshalb einzeln, nie parallel. Grund ist der Memory-Eintrag „VM-dockerd-Absturz = BuildKit-Race“.
- Das Netz `event-agent` bleibt unverändert, ohne `ip_range` (Ruling T9-a). `event-mcp` bekommt `10.250.10.14`.
- `migrationen` werden vor dem Anwenden immer mit `pg_dump -n event` gesichert. Sie laufen als `supabase_admin` mit `db/anwenden.py`.
- Tests:
  - pytest immer mit `--basetemp` auf C: und `-p no:playwright`;
  - die volle Space-Suite in Teilen und im Vordergrund;
  - Web mit `npm test`, `npm run typen` und `npm run build`.
- Commits:
  - Conventional Commits, Trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Gearbeitet wird auf dem Zweig `cc/event-management-brain` im Worktree `C:\Users\User\ClaudeWork\wt-vos-em-brain`.
  - Integration wie in Etappe 2/3: Rebase auf `origin/master`, fast-forward-Push, Pin-Bump im äußeren Repo über `wt-board`.

## Review Focus

1. **Ein erneuter `anbinden.sh`-Lauf nach dem Ausrollen** muss `ev-legacy`, die Brain-Server und alle `ev-/ld-/bo-`-Agenten erhalten. Kein Agent darf verschwinden oder Werkzeuge verlieren. Test in Task 2.
2. **Ein Brain-Auftrag mit mehrdeutigem Event-Namen** liefert die Trefferliste und schreibt nichts. Ein Legacy-Event läuft nie über `bn-*`-Werkzeuge. Test in Task 4 (SOUL-Regeln und Katalog).
3. **Migrationen doppelt oder nach Teilausfall angewendet:** `anwenden.py` überspringt bereits Angewandtes, und eine abgebrochene Migration hinterlässt keinen halben Stand. Test in Task 3 mit 001–009 zweimal und einer künstlich scheiternden 009.
4. **VibeMind ändert im Legacy-Event:** Die Prüfung erkennt das weiter über den alten Weg. VibeMind ändert in einem neuen Event: Die Prüfung erkennt das über `verlauf`. Fremde Bot-Änderungen zählen nicht. Test in Task 5.
5. **Ausroll-Phase ohne Schalter oder in falscher Reihenfolge:** Das Skript tut nichts und nennt die fehlende Vorbedingung, ohne Geheimnisse. Test in Task 6.

---

## Teil A – Repo-Arbeit (SDD, lokal)

### Task 1: Images für `event-api`/`event-mcp` und `event-web`

**Files:**
- Create: `spaces/event_management/Dockerfile.system`, `spaces/event_management/web/Dockerfile`, `spaces/event_management/.dockerignore`, `spaces/event_management/web/.dockerignore`, `spaces/event_management/requirements.txt` (falls nicht vorhanden; sonst Modify)
- Modify: `spaces/event_management/docker-compose.yml` (Image-Tags, `build:`-Abschnitte), `spaces/event_management/web/next.config.ts`
- Test: `spaces/event_management/tests/test_images.py`

**Interfaces:**
- Produces:
  - Image `event-management/system:<git-kurz-sha>`. Ein Image für `event-api`, `event-mcp` und die CLIs (`bereitstellung`, `nachrichten`, `schluessel`, `legacy`).
  - Image `event-management/web:<git-kurz-sha>`.
  - Compose verweist über `${EVENT_SYSTEM_TAG}`/`${EVENT_WEB_TAG}` darauf. Ohne gesetzte Variable bricht Compose mit klarer Meldung ab (`${VAR:?…}`). Vorsicht wegen Memory „stack deploy zerlegt ${VAR:?msg}“: Die Meldung enthält **keinen Bindestrich**.
- **Ruling zur Rewrite-Frage aus Etappe 2:** Der Build bekommt das Argument `EVENT_API_URL=http://event-api:8170`, also den Dienstnamen im Compose-Netz. `next.config.ts` liest es zur Build-Zeit. Es gibt keinen Runtime-Proxy. Kosten, falls falsch: Bei einem Wechsel des Dienstnamens muss neu gebaut werden.
  - Port 8170 ist in `docker-compose.yml` bestaetigt.

- [ ] **Step 1: Tests schreiben**, nur strukturell, ohne Docker-Aufruf:
  - `test_dockerfile_system_basis_gepinnt`: `FROM python:3.12-slim@sha256:` oder ein fester Patch-Tag, kein `latest`.
  - `test_dockerfiles_laufen_nicht_als_root`: `USER` ist nicht root, in beiden Dateien.
  - `test_keine_geheimnisse_im_kontext`: `.dockerignore` schließt `laufzeit/`, `werte.local.json`, `*.env`, `build/`, `node_modules/`, `.next/`, `tests/` und `.superpowers/` aus.
  - `test_web_rewrite_aus_build_arg`: `next.config.ts` liest `process.env.EVENT_API_URL`, und `web/Dockerfile` hat `ARG EVENT_API_URL`.
  - `test_compose_tags_pflicht`: `image:` von `event-api`, `event-mcp` und `event-web` nutzt `${EVENT_SYSTEM_TAG:?…}` bzw. `${EVENT_WEB_TAG:?…}`, und die Meldung enthält keinen `-`.
  - `test_web_standalone`: `output: 'standalone'` in `next.config.ts`, und das Dockerfile kopiert `.next/standalone`.
- [ ] **Step 2:** Die Tests laufen lassen und rot sehen.
- [ ] **Step 3:** Dockerfiles, `.dockerignore` und `requirements.txt` schreiben.
  - Abhängigkeiten werden aus den tatsächlichen Importen von `system/` ermittelt und mit Version gepinnt.
  - Mehrstufiger Web-Build (deps → build → runner). Der Runner hat nur `node`, `standalone`, `static` und `public`.
  - Die Healthchecks der Compose-Datei bleiben.
- [ ] **Step 4:** Die Tests sind grün, ebenso `tests/test_event_management.py` (Compose-Struktur).
  - Optional ein lokaler `docker build` beider Images auf dem PC mit `--memory 2g`. Hängt Docker Desktop (Memory „Docker hängt: WSL 0x8007054f“), wird das übersprungen und im Report vermerkt.
- [ ] **Step 5: Commit** `feat(event-management): Images fuer Fachschicht und Oberflaeche`

### Task 2: `ev-legacy` – Legacy-Gruppe bekommt ihren eigenen Agenten

**Files:**
- Modify: `spaces/event_management/config/openclaw.json`, `spaces/event_management/deploy/anbinden.sh` (Saat-Schleife, Schutz), `spaces/event_management/docker-compose.yml` (Mount `workspace-legacy`, falls die Arbeitsbereiche einzeln gemountet sind)
- Test: `spaces/event_management/tests/test_anbinden.py`, `spaces/event_management/tests/test_event_management.py`

**Interfaces:**
- Produces in `openclaw.json`:
  - Agent `{"id": "ev-legacy", "workspace": "/home/node/.openclaw/workspace-legacy", "tools": {"allow": ["event-app__*"]}}`.
  - Binding `{"agentId": "ev-legacy", "match": {"channel": "telegram", "peer": {"kind": "group", "id": "{{TELEGRAM_GRUPPE}}"}}}`.
  - `channels.telegram.groups."{{TELEGRAM_GRUPPE}}".requireMention` bleibt `false`.
  - `main` behält genau `["melden__gruppe_melden", "melden__chat_melden"]`.
- Der Schutz aus Ruling T8-a in `anbinden.sh` bleibt bestehen. Er ist jetzt erfüllt, weil ein Nicht-`main`-Agent an `{{TELEGRAM_GRUPPE}}` gebunden ist.
- Die `anbinden.sh`-Saat-Schleife umfasst `['workspace', 'workspace-brain', 'workspace-legacy']`.
- `ev-legacy` passt **nicht** auf das Arbeiter-Muster `^(ev|ld|bo)-[0-9a-f]{8}$` (Ruling T8-b). Er gehört also zur Saat, nicht zum Arbeiter. Den Test darauf festnageln.

- [ ] **Step 1: Tests schreiben**
  - `test_ev_legacy_an_legacy_gruppe_gebunden`: genau ein Binding auf `{{TELEGRAM_GRUPPE}}`, agentId `ev-legacy`, `peer.kind == "group"`.
  - `test_ev_legacy_nur_alte_werkzeuge`: `tools.allow == ["event-app__*"]`.
  - `test_main_bleibt_empfang`: `main` hat genau die zwei melden-Werkzeuge und kein Binding.
  - `test_schutz_laesst_ausrollen_zu`: Der Schutz-Abschnitt von `anbinden.sh` gibt mit der echten Saat-Datei Exit 0 zurück. Bisher war es 3.
  - `test_saat_kennt_workspace_legacy`.
  - `test_ev_legacy_ist_kein_arbeiter_agent`: Das Muster aus `anbinden.sh` passt nicht auf `ev-legacy`.
  - `test_zweiter_lauf_erhaelt_alles` (Review Focus 1):
    - Ausgangslage: aktuelle Config mit `ev-legacy`, `bn-lesen`, `bn-auftrag` (Task 4) und einem Arbeiter-Agenten `ev-1a2b3c4d`, dazu eine abweichende Saat.
    - Nach dem Ersetzen sind alle vier samt Bindings und Servern da, und der Vergleich meldet beim zweiten Lauf „unverändert“.
    - Läuft über die vorhandene node-Logik gegen Temp-Ordner, wie in den bestehenden `test_anbinden`-Tests.
- [ ] **Step 2:** rot. **Step 3:** umsetzen. **Step 4:** grün, dazu `tests/test_anbinden.py` komplett.
- [ ] **Step 5: Commit** `feat(event-management): ev-legacy uebernimmt die Legacy-Gruppe`

### Task 3: Legacy-Katalogeintrag und Migrationsprüfung

**Files:**
- Create: `spaces/event_management/system/legacy.py`
- Modify: `spaces/event_management/db/anwenden.py` (nur falls Step 1 eine Lücke zeigt), `spaces/event_management/werte.beispiel.json` (Platzhalter für den Legacy-Eintrag)
- Test: `spaces/event_management/tests/system/test_legacy.py`, `spaces/event_management/tests/system/test_migration.py`

**Interfaces:**
- Produces:
  - `legacy.eintragen(conn_admin, *, firma_kurz: str, name: str, beginn: datetime, ende: datetime, ort: str | None, legacy_url: str) -> UUID`.
    - Kategorie `festival`, `quelle='legacy'`, Status wie bei neuen Events.
    - Idempotent über `legacy_url`: Ein zweiter Aufruf mit derselben URL aktualisiert Eckdaten und liefert dieselbe ID.
    - Gibt es die Firma nicht, folgt `ValueError("Firma nicht gefunden")`.
    - `legacy_url` muss `https://` sein.
  - CLI `python -m spaces.event_management.system.legacy eintragen <datei.json>`.
    - Die Datei enthält `{"firma_kurz", "name", "beginn", "ende", "ort", "legacy_url"}`.
    - Admin-DSN nur aus `EVENT_ADMIN_DSN_FILE`.
    - Ausgabe: nur die Event-ID, keine Werte aus der Datei.
- Die Firma des Legacy-Events legt Felix in der Oberfläche oder per `firma_anlegen` an, nicht dieses Skript.

- [ ] **Step 1: Tests schreiben**
  - `test_eintragen_legt_legacy_event_an`: quelle `legacy`, Kategorie `festival`, `legacy_url` gesetzt.
  - `test_eintragen_idempotent_ueber_url`.
  - `test_unbekannte_firma_abgelehnt`.
  - `test_url_nur_https`.
  - `test_cli_gibt_nur_id_aus`: capsys enthält weder Name noch Ort noch URL.
  - `test_legacy_event_fuer_bot_gesperrt`:
    - `marke_ausstellen` lehnt mit dem Legacy-Text ab.
    - `werkzeuge.ausfuehren` mit einem `betreiber`-Schlüssel auf `aufgaben_anlegen` liefert den Legacy-Text.
  - In `test_migration.py` (Review Focus 3):
    - `test_alle_migrationen_zweimal`: 001–009 zweimal anwenden, der zweite Lauf ändert nichts.
    - `test_abgebrochene_migration_hinterlaesst_nichts`: Eine Test-Migration mit Fehler in der letzten Anweisung hinterlässt weder Tabelle noch Eintrag in der Protokolltabelle.
- [ ] **Step 2–4:** rot, umsetzen, grün.
- [ ] **Step 5: Commit** `feat(event-management): Legacy-Festival als Katalogeintrag`

### Task 4: Brain auf eigene Schlüssel und den Katalog umstellen

**Files:**
- Create: `spaces/event_management/db/009_schluessel_name.sql`
- Modify:
  - `spaces/event_management/system/schluessel.py` (Name), `system/kontext.py` bzw. `schluessel.kontext_fuer` (actor)
  - `spaces/event_management/config/openclaw.json` (Server `bn-lesen` und `bn-auftrag`, Allow-Listen), `spaces/event_management/config/workspace-brain/SOUL.md`
  - `spaces/event_management/deploy/anbinden.sh` (stdin-Zeilen 6 und 7)
  - `brain/the_brain/data/capabilities.yaml` (Muster `event_ask`/`event_do` um die Kategorien), `config/space_agent_registry.yml` (keywords)
- Test: `tests/system/test_schluessel.py`, `tests/test_anbinden.py`, `tests/test_event_management.py`, `tests/test_brain_anmeldung.py`, `brain/the_brain/tests/` (Muster-Treffer)

**Interfaces:**
- Produces:
  - `009`: `alter table event.schluessel add column if not exists name text check (name ~ '^[A-Za-z0-9_]{1,40}$')`. Die Spaltenrechte für `event_app` bleiben wie in 004.
  - `schluessel.ausstellen(..., name: str | None = None)`. CLI `--name`.
  - Mit Name gilt `Kontext.actor = f"bot:{art}:{name}"`, sonst wie bisher `f"bot:{art}"`.
  - Server in `openclaw.json`, beide mit Header-Platzhalter. Die Werte setzt `anbinden.sh` aus stdin:
    - `mcp.servers.bn-lesen = {url: "{{EVENT_MCP_URL}}/mcp", transport: "streamable-http", headers: {Authorization: …}}`
    - `mcp.servers.bn-auftrag` analog.
  - Allow-Listen:
    - `brain-lesen.tools.allow` = die bisherigen 9 lesenden `event-app__…` plus `"bn-lesen__*"`.
    - `brain-auftrag.tools.allow` = die bisherigen 20 `event-app__…` plus `"bn-auftrag__*"`.
  - `anbinden.sh` liest genau 7 Zeilen. Neu sind:
    - Zeile 6: `Authorization` von `bn-lesen` (Schlüssel der Art `betreiber_lesen`, Name `VibeMind`).
    - Zeile 7: `Authorization` von `bn-auftrag` (Art `betreiber`, Name `VibeMind`).
    - Beide sind Pflicht und nicht leer. Kopfkommentar und Schlüsselvergleich werden entsprechend erweitert.
  - Brain-SOUL (`workspace-brain/SOUL.md`) mit Regeln, nicht wörtlich vorgegeben:
    - (1) Jeder Auftrag und jede Frage nennt ein Event. Zuerst kommt `event_waehlen`.
    - (2) Bei mehreren Treffern antwortet der Agent mit der Trefferliste und ändert nichts.
    - (3) Liefert die Wahl den Legacy-Text, oder ist das Event das Legacy-Festival, nimmt er die `event-app__…`-Werkzeuge, sonst nie.
    - (4) Jede Antwort nennt Event und Firma.
    - (5) Riskante Aktionen laufen zweistufig mit Rückfrage. Aus dem Brain heißt das: Er beschreibt, statt auszuführen, und meldet „Bestätigung nötig“.
  - `capabilities.yaml`: Die Orga-Wörter in `event_do`/`event_ask` werden um `webinar|hackathon|messe|weihnachtsfeier|team.?meeting|fest\b` erweitert. Die bestehenden Ausschlüsse (Kalender, Meeting im Kalendersinn, fremde Spaces) bleiben.
- Ruling: `team.?meeting` kollidiert mit dem Kalender-Ausschluss `meeting\w*`. Der Ausschluss gewinnt, Team-Meetings werden also nur über die Event-Wörter erkannt. Kosten: Der Satz „Team-Meeting Aufgabe anlegen“ wird nicht erkannt. Das wird im Test festgehalten.

- [ ] **Step 1: Tests schreiben**
  - `test_schluessel_name_im_actor`.
  - `test_name_nur_erlaubte_zeichen`.
  - `test_event_app_kann_name_nicht_setzen` (RLS bzw. Spaltenrecht).
  - `test_brain_server_und_allow_listen`: exakte Mengen.
  - `test_anbinden_braucht_sieben_zeilen`: 6 Zeilen geben Abbruch, eine leere Zeile 6 oder 7 auch, und es wird kein Wert ausgegeben.
  - `test_brain_soul_regeln`: Die SOUL enthält `event_waehlen`, die Trefferliste-Regel, die Legacy-Regel und den Hinweis zur Zweistufigkeit. Keine echten Daten, nur Platzhalter.
  - Muster-Tests im Brain:
    - „Lege für den Hackathon eine Aufgabe Catering an“ trifft `event_do`.
    - „Was steht im Kalender für das Meeting?“ trifft nichts.
    - Bisherige Festival-Sätze treffen weiter.
- [ ] **Step 2–4:** rot, umsetzen, grün. Dazu die Brain-Tests der Event-Capabilities und `tests/test_brain_anmeldung.py`.
- [ ] **Step 5: Commit** `feat(event-management): Brain mit eigenen Betreiber-Schluesseln am Katalog`

### Task 5: Wahrheitsprüfung über `verlauf` (zweite Quelle)

**Files:**
- Modify: `spaces/event_management/system/werkzeuge.py` (neues Werkzeug), `brain/the_brain/core/world_observer.py`, `brain/the_brain/data/capabilities.yaml` (Validator von `event_do`)
- Test: `spaces/event_management/tests/system/test_werkzeuge.py`, `brain/the_brain/tests/test_world_observer_event_app.py` (oder neue Datei `test_world_observer_event_verlauf.py`)

**Interfaces:**
- Produces:
  - Werkzeug `aenderungen_seit(minuten: int)`, mit `minuten` zwischen 1 und 1440.
    - **Nur** für die Arten `betreiber_lesen` und `betreiber`. Es gibt keine Marke, weil der Betreiber alles sieht.
    - Liefert JSON `{"aenderungen": [{"event": name, "firma": kurz, "zeitpunkt": ISO-UTC, "actor", "quelle", "baustein", "aktion"}]}`, höchstens 100, neueste zuerst.
    - **Ohne** Inhalte oder Titel der Zeilen.
  - Prüfung `_check_event_change(spec)` im `world_observer`, registriert als `"event_change"`.
    - spec: `{actor_verlauf: "bot:betreiber:VibeMind", actor_legacy: "VibeMind", since_s: 600}`.
    - Erst wird `verlauf` über `EVENT_MCP_URL` und `EVENT_VERLAUF_TOKEN[_FILE]` gefragt. Das ist ein eigener `betreiber_lesen`-Schlüssel mit Name `Pruefung`, unabhängig vom Bot.
    - Danach kommt der bestehende Legacy-Weg `_check_event_app_change`.
    - `True`, wenn eine Quelle `True` liefert. `False` nur, wenn beide klar `False` liefern. Sonst `None`.
    - `detail` nennt nur Zählwerte.
  - `capabilities.yaml` `event_do`: `kind: "truth:event_change"`, `on_fail: "report"` bleibt, postcondition `check: event_change` mit obigen Feldern.

- [ ] **Step 1: Tests schreiben**
  - In `test_werkzeuge.py`:
    - `test_aenderungen_seit_nur_betreiber`: Für die Schlüssel `event`, `leitung` und `melden` ist das Werkzeug nicht im Katalog.
    - `test_aenderungen_ohne_inhalte`: Ein Aufgabentitel taucht in der Antwort nicht auf.
    - `test_aenderungen_grenze_100`.
    - `test_minuten_ausserhalb_abgelehnt`.
  - Im Brain, mit einem lokalen HTTP-Double nach dem Muster der bestehenden `test_world_observer_event_app.py` (Review Focus 4):
    - `test_verlauf_treffer_true`.
    - `test_fremder_actor_zaehlt_nicht`: `bot:betreiber` ohne Name und `bot:event` zählen nicht.
    - `test_legacy_weg_greift_wenn_verlauf_leer`.
    - `test_beide_unklar_none`.
    - `test_kein_token_kein_wert_im_detail`.
- [ ] **Step 2–4:** rot, umsetzen, grün.
- [ ] **Step 5: Commit** `feat(event-management): Wahrheitspruefung ueber den strukturierten Verlauf`

### Task 6: 80-%-Warnung des Tagesbudgets und Ausroll-Skript

**Files:**
- Modify (äußeres Repo `Vibemind_V1`, Commit dort direkt auf master): `scripts/claude_budget.py`, Test `tests/test_claude_budget.py` (bestehende Testdatei suchen; sonst neu)
- Modify (vibemind-os): `spaces/event_management/deploy/agent_budget.yaml`
- Create (vibemind-os): `spaces/event_management/deploy/ausrollen.sh`, `spaces/event_management/deploy/ausrollen.md`
- Test (vibemind-os): `spaces/event_management/tests/test_ausrollen.py`

**Interfaces:**
- **Budget:**
  - Optionaler Eintrag `warnen_ab: 0.8` je Agent in der Budget-YAML.
  - `pruefen()` schreibt beim ersten Überschreiten am Tag genau eine Zeile `{"art": "warnung", "agent", "heute", "grenze"}` ins Tagesprotokoll.
  - Der Aufruf `budget_warnung(agent, heute, grenze)` ist injizierbar. Standard: `scripts/notify_telegram.py` als Subprozess, ohne geerbte Pipes, Timeout 10 s, Fehler nur geloggt.
  - Ohne `warnen_ab` verhält sich alles wie bisher.
  - `agent_budget.yaml` bekommt `warnen_ab: 0.8`.
  - **Vor dem Bauen prüfen**, ob das Shim-Image `event-management/shim` `claude_budget.py` beim Build einbackt oder per Mount liest. Backt es ein, gehört der Shim-Neubau als eigene Phase in `ausrollen.sh`, mit eigenem OK.
- **`ausrollen.sh`:** `bash deploy/ausrollen.sh --ich-habe-ok <phase>` mit diesen Phasen:
  - `pruefen`: Vorbedingungen ohne Änderung. Gemessen werden PG-Version ≥ 15, ob die Images vorhanden sind, ob `werte.local.json` vollständig ist, ob `laufzeit/`-Dateien da sind und 0600 haben, ob der Claim gesetzt ist (Datei- oder Grep-Probe) und ob `.14` frei ist.
  - `sichern`: `pg_dump -n event` nach `laufzeit/sicherung/`, dazu eine Kopie von `openclaw.json` aus dem Volume.
  - `migrieren`: `db/anwenden.py` als `supabase_admin`.
  - `bauen`: genau ein Image je Aufruf (`bauen system` | `bauen web`), gedeckelt.
  - `dienste`: `docker compose --profile event-system up -d event-api event-mcp event-web` mit Proben `/gesund`.
  - `anbinden`: Hinweis plus Aufruf von `anbinden.sh`; die 7 Zeilen kommen vom Aufrufer über stdin.
  - `timer`: `bereitstellung` und `nachrichten` installieren und starten.
  - `tailnet`: `tailscale serve` für `event-web`.
  - `zurueck <phase>`: Rückweg je Phase.

  Regeln für das Skript:
  - Ohne Schalter: Exit 2, kein Aufruf.
  - Ohne erfolgreiche Vorphase (Stempeldatei `laufzeit/ausrollen/<phase>.ok`): Abbruch mit Name der fehlenden Phase.
  - `set -euo pipefail`. Keine Werte in der Ausgabe. Geheimnisse nur über Dateien.
- **`ausrollen.md`:**
  - Reihenfolge, OK-Punkte für Felix und Abbruchkriterien.
  - Den Rückweg je Phase. Zum Beispiel `dienste`: `docker compose --profile event-system stop …`. `anbinden`: Sicherung der `openclaw.json` zurückspielen.
  - Wer was misst.

- [ ] **Step 1: Tests schreiben**
  - Budget:
    - `test_warnung_einmal_pro_tag`.
    - `test_ohne_warnen_ab_unveraendert`.
    - `test_warnung_fehler_bricht_nichts`.
  - Ausrollen (Review Focus 5), bash mit protokollierenden Fake-Programmen wie in `test_messen.py`:
    - `test_ohne_schalter_nichts`.
    - `test_reihenfolge_erzwungen`: `dienste` ohne `migrieren.ok` bricht ab.
    - `test_bauen_einzeln_und_gedeckelt`: Der Aufruf enthält eine Speichergrenze, und es gibt nie zwei Builds in einem Aufruf.
    - `test_keine_werte_in_ausgabe`.
    - `test_zurueck_je_phase_definiert`.
- [ ] **Step 2–4:** rot, umsetzen, grün. Dazu die volle Space-Suite in Teilen und Web `npm test`/`typen`/`build`.
- [ ] **Step 5: Commits**
  - äußeres Repo: `feat(budget): Warnung beim Erreichen von warnen_ab`
  - vibemind-os: `feat(event-management): Ausroll-Skript mit Phasen, Schalter und Rueckweg`

**Nach Task 6:**
- Abschluss-Review des ganzen Zweigs (opus) und eine Fix-Welle.
- Integration auf master: Rebase, fast-forward-Push, Pin-Bump über `wt-board`.
- Erst danach beginnt Teil B.

---

## Teil B – Live (Controller selbst, je Phase Felix-OK)

**Für Teil B gilt:**
- Jede Phase beginnt mit der Frage an Felix: „Phase X jetzt? Rückweg: …“ und endet mit einem Eintrag im Ledger. Der Eintrag nennt nur Ergebnisse, keine IDs.
- Schlägt eine Phase fehl, geht es zuerst den Rückweg, dann folgt der Bericht. Nie improvisiert weiterbauen.

**Reihenfolge Teil B (Abschluss-Review F-3):** `pruefen` → `bauen system` → `bauen web` → Task 7 (messen, braucht `tags.env`/`EVENT_SYSTEM_TAG` und das System-Image) → `sichern` → `migrieren` → `shim` → `dienste` → Schlüssel → `tailnet` → erster Betreiber, Firma, Legacy-Eintrag → `anbinden` → `timer` → Brain-Umgebung. Task 7 läuft also zwischen den Schritten 2 und 3 von Task 8; maßgeblich ist `deploy/ausrollen.md`.

### Task 7: Live messen (M1–M6)

Vorher: Task 8, Schritte 1 und 2 (`pruefen`, `bauen system`, `bauen web`). Erst dann gibt es `laufzeit/ausrollen/tags.env` und das Image `event-management/system:<tag>`.

- [ ] **Step 1:** Claim im WORKBOARD und in `002_Koordination_Live.md` setzen und committen. Nur die eigene Zeile stagen.
- [ ] **Step 2:** Nach Felix-OK `deploy/messen.md` abarbeiten:
  - mit `messen.sh --ich-habe-ok vorbereiten`, dann Messpunkt für Messpunkt;
  - M1 mit dem Gegentest (Gruppe B behauptet die Chat-ID von A);
  - M5 mit zwei fremden Direktchats.

  Ein Testbot und Testgruppen sind nötig. Felix stellt sie bereit und nennt sie nur lokal.
- [ ] **Step 3:** Je Messpunkt das Ergebnis (bestanden / nicht bestanden / Alternative) in den Ledger schreiben.
  - Ist einer nicht bestanden: anhalten und Felix die Alternative aus `messen.md` vorlegen.
  - Teil B geht erst weiter, wenn alle bestanden sind oder eine Alternative entschieden und umgesetzt ist. Eine Umsetzung braucht einen eigenen kleinen Plan.
- [ ] **Step 4:** `messen.sh --ich-habe-ok aufraeumen` ausführen. Testbot und -gruppen löst Felix.

### Task 8: Ausrollen

Die Phasen laufen in dieser Reihenfolge, jede mit eigenem OK:

1. `ausrollen.sh --ich-habe-ok pruefen`. Alle Vorbedingungen müssen grün sein.
2. `bauen system`, dann `bauen web`, einzeln. Beide verlangen nur `pruefen`. Vor jedem Build Speicher messen (`/proc/meminfo` SwapFree/MemAvailable, Memory „brain-core-Deploy: VM-Speicher messen“). **Danach Task 7 (messen).**
3. `sichern`.
4. `migrieren`. Danach `select … from event.schema_migrationen` (oder wie `anwenden.py` es nennt) mit 001–009.
5. `shim` mit `EVENT_SHIM_BAUKONTEXT` (Rezept in `deploy/ausrollen.md`; ohne Baukontext bricht die Phase ab).
6. `dienste`. Die Proben `/gesund` für API und MCP sind grün, die Web-Startseite liefert 200 aus dem Container-Netz.
7. Schlüssel ausstellen (`schluessel ausstellen`) mit Admin-DSN auf der VM:
   - `melden`;
   - `betreiber_lesen` mit Name `VibeMind` für `bn-lesen`;
   - `betreiber` mit Name `VibeMind` für `bn-auftrag`;
   - `betreiber_lesen` mit Name `Pruefung` für den Observer.

   Die Klartexte gehen direkt in Dateien unter `laufzeit/` (0600) und nie in eine Ausgabe.
8. `tailnet`. Ab hier ist die Oberfläche im Tailnet sichtbar.
9. Erster Betreiber (`python3 -m spaces.event_management.system.betreiber anlegen`, Passwort per stdin), Felix meldet sich über das Tailnet an und legt die Firma des Legacy-Events in der Oberfläche an; dann `legacy eintragen` mit der lokalen Datei.
10. `anbinden`. `printf` mit 7 Zeilen aus Dateien geht an `anbinden.sh`. Danach prüfen:
    - Die Legacy-Gruppe antwortet weiter. Ein Test-Satz in der Gruppe kommt über `ev-legacy` und nutzt die alten Werkzeuge.
    - `main` antwortet in einer fremden Testgruppe nur auf @-Erwähnung.
11. `timer`. Die Units laufen, das Journal zeigt Läufe ohne Fehler. Ein erneutes `anbinden` danach stoppt `bereitstellung.timer` selbst; `timer` startet ihn wieder.
12. Brain-Umgebung: `EVENT_MCP_URL` und `EVENT_VERLAUF_TOKEN_FILE` über den dokumentierten Launcher- bzw. Secret-Weg setzen, kein `service update`. Den Netzweg (`deploy/ausrollen.md`, „Netzweg Brain → event-mcp“) wählen. Die Wahl kommt in den Ledger, ohne Werte.

### Task 9: Abnahme und Abschluss

- [ ] **Step 1: Abnahme live**, mit Testfirma und Testevent:
  - Gruppe verbinden mit Code und Prüfcode, der Agent entsteht, der Bot legt eine Aufgabe an.
  - Brain `event.do` auf das Testevent, die Prüfung `event_change` ist true.
  - Brain `event.ask` auf das Legacy-Festival antwortet über die alten Werkzeuge.
  - Archivieren, der Agent verschwindet.
  - Eine freigegebene Test-Mail an eine eigene Adresse kommt an.
  - 80-%-Warnung durch eine niedrige Testgrenze auslösen, dann zurücksetzen.
- [ ] **Step 2:** Testdaten entfernen: Testevent löschen, Testfirma bleibt archiviert oder wird gelöscht.
- [ ] **Step 3:**
  - Claims schließen.
  - Memory `project_space_event_management.md` aktualisieren.
  - Doku-Loop für `docs/spaces/` laufen lassen, nicht von Hand schreiben.
  - `/distill`, wenn Erkenntnisse von Dauer entstanden sind.

---

## Offene Entscheidungen für Felix (Vorschlag in Klammern)

1. **Mail-Versandweg:** Er bleibt der Betreiber-Mailer, so wie die Einladungen. sales-claw kennt keinen generischen Auftrags-Eingang. (Vorschlag: bleibt; ein Adapter erst bei Bedarf.)
2. **Warnung bei 80 %:** Bauen wie in Task 6, auch wenn dafür das Shim-Image neu gebaut werden muss. (Vorschlag: ja.)
3. **Adresse `.14` ohne reservierten Bereich:** Das Kollisionsrisiko hinnehmen. (Vorschlag: ja, eine Kollision zeigt sich als sichtbarer Startfehler.)
4. **Aufgeschobene Punkte aus Etappe 3** gehören nicht in diesen Plan, sondern in einen eigenen kleinen Plan nach dem Ausrollen:
   - Rate-Limit für Prüfcodes je Firma;
   - eigene DB-Rolle fürs Melden;
   - Re-Provisionierung nach dem Entarchivieren;
   - Schlüsselerneuerung;
   - Einladung widerrufen und Passwort-Reset.

   (Vorschlag: danach.)
