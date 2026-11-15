# Event-Management: Empfangsbudget und freundliche Budget-Antwort – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fremde, nicht verbundene Telegram-Gruppen können das Tagesbudget der verbundenen Gruppen nicht mehr leeren, und ein leeres Budget erscheint als freundliche Antwort statt als „LLM request failed.“.

**Architecture:** Der Empfangs-Agent `main` bekommt einen eigenen Shim-Container (`event-management-shim-empfang`, Budget-Agent `event-management-empfang`, Tagesgrenze 50). In OpenClaw zeigt nur `main` auf diesen Provider, alle anderen Agenten bleiben beim bisherigen Shim (300). Die Budget-Hülle des Shims gibt bei erreichter Tagesgrenze optional (`VIBEMIND_BUDGET_ANTWORT`) einen festen Text als normale Antwort zurück. Ohne die Variable bleibt alles wie bisher, also auch beim Rowboat-Shim. Absender in Gruppen sind offen (`groupAllowFrom: ["*"]`), damit der Empfang unverbundene Gruppen überhaupt erreicht (M1).

**Tech Stack:** OpenClaw 2026.7.1 (`openclaw.json`), Docker Compose, Bash (`ausrollen.sh`, `anbinden.sh`, `messen.sh`), Python 3 (Shim-Hülle, pytest).

**Spec:** `spaces/event_management/deploy/messen.md` (M1, M4 mit Alternative „Eigenes Budget für den Empfang“) sowie die Felix-Entscheide vom 15.11. im Ledger `.superpowers/sdd/2026-11-14-event-system-etappe-4-brain-legacy-ausrollen/progress.md`: (a) Absender offen plus Empfangsbudget, freundliche Meldung, M5 weglassen, ev-legacy an die Live-Gruppe. Übergeordnet: `docs/superpowers/specs/2026-11-12-event-management-system-design.md`.

## Global Constraints

- vibemind-os ist ÖFFENTLICH: keine Chat-IDs, Tokens, Personen- oder Festivalnamen in Code, Tests, Doku oder Commits.
- Kein Deploy, kein `stack deploy`, kein `service update`. Live-Phasen nur mit Felix-OK je Phase (`ausrollen.sh --ich-habe-ok`).
- Werte (Tokens) nur über stdin oder 0600-Dateien, nie als Argument, nie in Ausgaben.
- Ohne `VIBEMIND_BUDGET_ANTWORT` verhält sich `rowboat_shim_huelle.py` exakt wie vorher. Der Rowboat-Shim nutzt dieselbe Datei.
- Feste Texte (wörtlich):
  - Gruppen-Shim: `Für heute ist mein Kontingent aufgebraucht. Ab morgen antworte ich wieder; Dringendes bitte direkt an die Orga.`
  - Empfangs-Shim: `Für heute ist mein Kontingent aufgebraucht. Ab morgen kann ich wieder Gruppen verbinden.`
- Namen: Container und Compose-Dienst `event-management-shim-empfang`, Budget-Agent `event-management-empfang`, Tagesgrenze `50`, `warnen_ab: 0.8`. Provider-Id `empfang`, Basis-URL `http://event-management-shim-empfang:8125/v1` (fest, wie der Compose-Dienstname).
- Tests: `-p no:playwright`, `--basetemp` auf C:, Git-Bash vorn im PATH. DB-Tests nie parallel.
- Commits direkt auf master (vibemind-os), Conventional Commits, Abschluss `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Danach Pin im äußeren Repo.

## Review Focus

1. **Rowboat-Shim ohne Variable:** Bei Tagesgrenze kommt weiterhin `ShimError` (bestehender Test `test_abgelehnt_ruft_claude_nicht_auf` muss unverändert grün bleiben).
2. **Leere oder nur aus Leerzeichen bestehende `VIBEMIND_BUDGET_ANTWORT`:** gilt als nicht gesetzt (Task 1, `test_antwort_leer_gilt_als_ungesetzt`).
3. **Andere Ablehnungsgründe** (`waechter_fehler`, `not_aus`, `unbekannter_agent`) bleiben Fehler, auch mit gesetzter Antwort. Ein falsch konfigurierter Shim soll laut scheitern, nicht freundlich schweigen (Task 1).
4. **Nur `main` nutzt den Empfangs-Shim:** `ev-legacy`, die Brain-Agenten und die vom Arbeiter angelegten `ev-…`-Agenten haben kein eigenes `model` und erben den Gruppen-Shim (Task 2, `test_nur_main_nutzt_den_empfang`).
5. **Alter Baukontext:** Liegt im `EVENT_SHIM_BAUKONTEXT` noch die Hülle ohne `VIBEMIND_BUDGET_ANTWORT`, bricht Phase `shim` ab, statt still ohne freundliche Antwort zu bauen (Task 3).

---

### Task 1: Freundliche Antwort bei Tagesgrenze in der Shim-Hülle

**Files:**
- Modify: `spaces/rowboat/deploy/headless/shim/rowboat_shim_huelle.py` (`umhuellen`, `main`, neue Funktion `antwort_aus_umgebung`; Kopf-Docstring um die Variable ergänzen)
- Test: `spaces/rowboat/tests/test_rowboat_shim_huelle.py`

**Interfaces:**
- Produces: `antwort_aus_umgebung(umgebung: Mapping[str, str]) -> str | None` (Variable `VIBEMIND_BUDGET_ANTWORT`, gestrippt, leer → `None`). `umhuellen(shim, budget, agent, antwort: str | None = None) -> None`.
- Bei `grund == "tagesgrenze"` und gesetzter `antwort` gibt der umhüllte `run_claude` `{"result": antwort, "is_error": False, "usage": {}, "stop_reason": "end_turn"}` zurück. Er ruft die CLI nicht auf und bucht nichts. Diese Felder liest der Shim aus dem Payload (`result`, `usage`, `stop_reason`).

- [ ] **Step 1: Failing tests** (Stil der vorhandenen Attrappen `FalscherShim`, `FalschesBudget`):
  - `test_tagesgrenze_mit_antwort_gibt_text_zurueck`: Budget lehnt mit `tagesgrenze` ab, `umhuellen(..., antwort="X")`. Erwartet: Rückgabe `result == "X"`, `is_error is False`, die CLI-Attrappe wurde nicht aufgerufen, `buchen` wurde nicht aufgerufen.
  - `test_andere_gruende_bleiben_fehler_trotz_antwort` (parametrisiert `waechter_fehler`, `not_aus`, `unbekannter_agent`): `pytest.raises(ShimError, match=grund)`.
  - `test_antwort_aus_umgebung` mit den Fällen `{}` → `None`; `{"VIBEMIND_BUDGET_ANTWORT": "  "}` → `None`; `" Hallo "` → `"Hallo"`.
  - `test_antwort_leer_gilt_als_ungesetzt`: `umhuellen(..., antwort=antwort_aus_umgebung({"VIBEMIND_BUDGET_ANTWORT": ""}))` mit Tagesgrenze erwartet `ShimError`.
- [ ] **Step 2:** `python -m pytest spaces/rowboat/tests/test_rowboat_shim_huelle.py -p no:playwright -q` → neue Tests FAIL.
- [ ] **Step 3:** Implementieren. `main()` liest `antwort_aus_umgebung(os.environ)` und reicht sie an `umhuellen` weiter. Die stderr-Zeile `budget-waechter aktiv …` nennt nur `antwort=ja|nein`, nie den Text.
- [ ] **Step 4:** Dieselbe Datei plus `spaces/rowboat/tests/test_headless_deploy.py` → PASS. Alle alten Tests bleiben unverändert grün.
- [ ] **Step 5:** Commit `feat(shim): feste Antwort bei Tagesgrenze per VIBEMIND_BUDGET_ANTWORT (opt-in)`.

### Task 2: Empfangs-Shim, Budget und Saat-Config

**Files:**
- Modify: `spaces/event_management/config/openclaw.json`, `spaces/event_management/docker-compose.yml`, `spaces/event_management/deploy/agent_budget.yaml`
- Test: `spaces/event_management/tests/test_event_management.py`, `spaces/event_management/tests/test_anbinden.py`

**Interfaces:**
- Consumes: `VIBEMIND_BUDGET_ANTWORT` aus Task 1.
- Produces:
  - Compose-Dienst `event-management-shim-empfang`: gleiches Image und gleiche Härtung, Volumes, Netze (`event-agent`, `event-shim-aus`), `env_file` und Healthcheck wie `event-management-shim`; `VIBEMIND_AGENT: event-management-empfang`; `VIBEMIND_BUDGET_ANTWORT` = Empfangstext.
  - `event-management-shim` bekommt `VIBEMIND_BUDGET_ANTWORT` = Gruppentext.
  - `event-management-claw.depends_on` wartet auf beide Shims mit `service_healthy`.
  - `openclaw.json`: `channels.telegram.groupAllowFrom: ["*"]`; `models.providers.empfang` als Kopie von `shim` mit der festen Empfangs-URL; `agents.list` Eintrag `main` bekommt `"model": {"primary": "empfang/claude-code-sonnet"}`.
  - `agent_budget.yaml`: zusätzlicher Agent `event-management-empfang` mit denselben Feldern wie der Claw, aber `tagesgrenze: 50`.

- [ ] **Step 1: Failing tests:**
  - `test_telegram_gruppen_und_werkzeugsperren` erweitern: `telegram["groupAllowFrom"] == ["*"]`. Der Kommentar nennt den Grund (M1: Wildcard-Gruppen lassen sonst keinen Absender durch).
  - `test_nur_main_nutzt_den_empfang`: `main.model == {"primary": "empfang/claude-code-sonnet"}`; jeder andere Eintrag in `agents.list` hat kein `model`; `defaults.model.primary == "shim/claude-code-sonnet"`; `providers.empfang.baseUrl == "http://event-management-shim-empfang:8125/v1"`, die restlichen Felder sind gleich `providers.shim` (ohne `baseUrl`).
  - `test_anbinden.py`: `test_shim_dienst_mit_eigenem_budget_agenten` über beide Dienste parametrisieren (Agent-Name je Dienst). Neu `test_shims_antworten_freundlich`: Die Texte stimmen wörtlich mit den Global Constraints überein. `test_claw_wartet_auf_den_shim…` verlangt beide `depends_on`. `test_netze_getrennt…` prüft den Empfangs-Shim wie den Shim. `test_budget_datei_kennt_genau_den_claw` heißt jetzt `…_den_claw_und_den_empfang`, mit `list(budget["agenten"]) == ["event-management-claw", "event-management-empfang"]` und dem Empfang mit `tagesgrenze: 50`.
- [ ] **Step 2:** `python -m pytest spaces/event_management/tests/test_event_management.py spaces/event_management/tests/test_anbinden.py -p no:playwright -q` → FAIL.
- [ ] **Step 3:** Die drei Dateien ändern (Werte aus den Global Constraints).
- [ ] **Step 4:** Dieselben Tests plus `tests/test_images.py` → PASS. `python deploy/fuellen.py werte.beispiel.json <tmp>` läuft ohne offenen Platzhalter.
- [ ] **Step 5:** Commit `feat(event-management): eigenes Empfangsbudget fuer main, Absender in Gruppen offen`.

### Task 3: Ausrollen und Anbinden kennen beide Shims

**Files:**
- Modify: `spaces/event_management/deploy/ausrollen.sh` (`shim_gesund`, `phase_shim`, `phase_zurueck` Fall `shim`), `spaces/event_management/deploy/anbinden.sh` (Neuanlage bei neuem Claude-Token, Shim-Probe), `spaces/event_management/deploy/ausrollen.md` (Phase 6)
- Test: `spaces/event_management/tests/system/test_ausrollen.py`, `spaces/event_management/tests/test_anbinden.py`

**Interfaces:**
- Consumes: Dienstname `event-management-shim-empfang` aus Task 2.
- Produces:
  - `shim_gesund <container>` wartet auf genau diesen Container.
  - Phase `shim` und `zurueck shim` legen beide Dienste in EINEM Aufruf neu an (`docker compose up -d --no-deps --force-recreate event-management-shim event-management-shim-empfang`) und warten auf beide.
  - Phase `shim` bricht ab, wenn `$k/rowboat_shim_huelle.py` kein `VIBEMIND_BUDGET_ANTWORT` enthält. Meldung: `rowboat_shim_huelle.py im Baukontext ist alt (keine Budget-Antwort); aus vibemind-os neu kopieren`.

- [ ] **Step 1: Failing tests:**
  - In `test_ausrollen.py` (Fake-Docker-Protokoll wie bisher): `test_shim_mit_baukontext_…` erwartet beide Dienstnamen im `compose up`-Aufruf und eine Gesundheitsabfrage je Container. `test_shim_baukontext_inhalt_geprueft` bekommt den Fall „Hülle ohne `VIBEMIND_BUDGET_ANTWORT`“ → Abbruch mit obiger Meldung, kein Build. `zurueck shim` legt beide neu an.
  - In `test_anbinden.py`: `test_shim_probe_ohne_logausgabe` gilt für beide Shims. Bei neuem Claude-Token werden beide Dienste neu angelegt.
  - Die Fixtures, die einen Baukontext anlegen, schreiben eine Hülle MIT `VIBEMIND_BUDGET_ANTWORT`, damit die alten Fälle grün bleiben.
- [ ] **Step 2:** `python -m pytest spaces/event_management/tests/system/test_ausrollen.py spaces/event_management/tests/test_anbinden.py -p no:playwright -q` → FAIL.
- [ ] **Step 3:** Skripte ändern. In `ausrollen.md`, Phase 6, ergänzen: Der Baukontext braucht die aktuelle Hülle (Rezept Schritt 1 neu kopieren), und die Phase legt beide Shims neu an.
- [ ] **Step 4:** Dieselben Tests plus `bash -n` beider Skripte → PASS.
- [ ] **Step 5:** Commit `feat(event-management): ausrollen/anbinden fuehren den Empfangs-Shim mit`.

### Task 4: Testpaar misst mit Empfangsbudget (messen.sh, messen.md)

**Files:**
- Modify: `spaces/event_management/deploy/messen.sh` (`vorbereiten`, `SAAT_PY`, `aufraeumen`, Kopf), `spaces/event_management/deploy/messen.md` (M1, M4)
- Test: `spaces/event_management/tests/system/test_messen.py`

**Interfaces:**
- Consumes: Texte und Budgetfelder aus Task 2, Hülle aus Task 1.
- Produces:
  - Zweiter Test-Shim `event-messung-shim-empfang`: `--network container:$SHIM`, gleiche Härtung wie der Test-Shim, Befehl `python3 /opt/shim/rowboat_shim_huelle.py --host 127.0.0.1 --port 8126`, `VIBEMIND_AGENT=event-messung-empfang`.
  - Beide Test-Shims hängen `$PWD/../rowboat/deploy/headless/shim/rowboat_shim_huelle.py` read-only über `/opt/shim/rowboat_shim_huelle.py`. So wird die Repo-Hülle ohne Neubau getestet.
  - Beide bekommen ihren `VIBEMIND_BUDGET_ANTWORT`-Text. `messen.sh` führt die Texte wörtlich, weil der Host-Python der VM kein `yaml` hat. Ein Test hält sie gleich mit `docker-compose.yml`.
  - Budget-Datei mit zwei Agenten: `$CLAW` mit `MESS_TAGESGRENZE` (Vorgabe 5) und `event-messung-empfang` mit `MESS_EMPFANGSGRENZE` (Vorgabe 3, Zahl bis 999 wie die andere).
  - `SAAT_PY` setzt `providers.empfang.baseUrl = "http://127.0.0.1:8126/v1"`; `groupAllowFrom` kommt aus der Saat.
  - `aufraeumen` entfernt auch den Empfangs-Shim.

- [ ] **Step 1: Failing tests:**
  - `test_nur_das_testpaar_wird_angefasst`: Die Namensliste enthält `event-messung-shim-empfang`.
  - `test_test_config_ohne_betrieb_und_ohne_platzhalter`: Die Test-Config hat `providers.empfang.baseUrl == "http://127.0.0.1:8126/v1"`, `main.model.primary == "empfang/claude-code-sonnet"` und `groupAllowFrom == ["*"]`.
  - Neu `test_empfangs_shim_im_testpaar`: Der Docker-Aufruf des Empfangs-Shims enthält `--network container:`, `--port 8126`, den Hüllen-Mount `:ro` und `VIBEMIND_AGENT=event-messung-empfang`. `MESS_EMPFANGSGRENZE=abc` bricht ab. Beide Texte in `messen.sh` sind gleich den `VIBEMIND_BUDGET_ANTWORT`-Werten der Compose-Dienste.
  - `test_anleitung_nennt_jeden_messpunkt…` bleibt grün. M4 beschreibt den neuen Ablauf.
- [ ] **Step 2:** `python -m pytest spaces/event_management/tests/system/test_messen.py -p no:playwright -q` → FAIL.
- [ ] **Step 3:** Implementieren. `messen.md`, M4 neu:
  1. Empfangsgrenze 3, Gruppe A verbinden.
  2. In „fremd“ viermal erwähnen. Erwartet: 3 Antworten, danach der Empfangstext.
  3. In Gruppe A schreiben. Erwartet: normale Antwort.
  4. Ohne Erwähnung in „fremd“ schreiben. Erwartet: keine Antwort, kein Verbrauch.
  5. Gruppengrenze ausschöpfen. Erwartet: Gruppentext in A, kein „LLM request failed.“.

  Neues Abbruchkriterium: A antwortet nach Schritt 2 nicht normal, oder irgendwo erscheint „LLM request failed.“. In M1 wird der Test-only-Schritt `groupAllowFrom` gestrichen (steht jetzt in der Saat), und M5 wird als „entfällt (Felix 15.11.)“ markiert.
- [ ] **Step 4:** Dieselben Tests plus `bash -n deploy/messen.sh` → PASS. Danach einmal die ganze Event-Suite (seriell, ~10 min).
- [ ] **Step 5:** Commit `feat(event-management): Testpaar misst mit Empfangsbudget`, dann push master und Pin im äußeren Repo (Worktree `wt-outer-pin`, wie gehabt).

### Task 5: M4 nachmessen (Controller, live auf dem Testpaar)

Nur das Testpaar `event-messung-*`. Die Felix-OKs für die Messung und das Testbudget gelten weiter.

- [ ] **Step 1:** VM-Checkout `/home/debian/vibemind-os` auf den neuen master (detached), ohne Submodule.
- [ ] **Step 2:** Altes Testpaar abbauen und neu aufbauen:
  - Testbot-Token und Claude-Token vorher aus den VM-Dateien in eine 0600-Datei außerhalb von `laufzeit/messung` retten. `aufraeumen` löscht den Ordner, `test-gruppen` liegt zusätzlich in `laufzeit/`.
  - `aufraeumen`, dann `vorbereiten`. Die drei stdin-Zeilen kommen aus diesen Dateien, das Gateway-Token neu per `openssl rand -hex 24`. Danach die Rettungsdatei löschen.
  - `testinstanz`, dann `anlegen` A.
  - `config_pruefen` muss `gueltig` melden. Das belegt, dass `agents.list[].model` von 2026.7.1 angenommen wird.
- [ ] **Step 3:** M4 nach dem neuen `messen.md` abarbeiten, über Telegram Web wie bisher. Zählen über `/tmp/em-m.sh budget` je Agent.
- [ ] **Step 4:** Ergebnis ohne IDs in den Ledger. Bei Bestehen: `aufraeumen` und den Baukontext auf der VM (`~/event-shim-kontext/rowboat_shim_huelle.py`) aus dem Checkout neu kopieren.
- [ ] **Step 5:** Felix berichten. Nächste Phase laut Etappe-4-Plan ist `sichern`, mit Felix-OK.
