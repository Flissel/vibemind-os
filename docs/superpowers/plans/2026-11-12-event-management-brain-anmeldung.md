# Event-Management: Brain-Anmeldung (Teil 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Brain routet `event.*` an den Space `event-management`. Er übergibt eine Frage oder einen Auftrag an den Event-Bot (OpenClaw-Gateway) und prüft Schreibaufträge unabhängig bei der Event-App nach.

**Architecture:** Weg B (Entscheidung Felix 12.11.2026): Es gibt keinen OpenFang-Agenten, der Event-Bot bleibt der einzige Agent. Der Brain ruft per `direct:` ein Übergabe-Modul im Space auf. Das Modul spricht das OpenAI-kompatible HTTP-API des Gateways an, mit zwei eigenen Gateway-Agenten: `brain-lesen` für Fragen und `brain-auftrag` für Änderungen. Schreibaufträge prüft eine neue `world_observer`-Prüfung gegen `list_recent_changes` der App nach.

**Tech Stack:** Python 3.12, pytest, PyYAML, OpenClaw 2026.7.1 (`ghcr.io/openclaw/openclaw:2026.7.1-slim`), JSON-RPC über MCP Streamable HTTP.

**Spec:** Kein eigenes Dokument. Grundlage sind die Gesprächsentscheidungen vom 12.11.2026:
- „Zusätzlich im Brain anmelden“
- „Neutralisieren“: Das Repo ist öffentlich, private Angaben stehen nur als Platzhalter darin.
- „B: an den Event-Bot“

Den Ausgangsstand liefert Teil 1, vibemind-os `08c70203`:
- `spaces/event-management/`
- `spaces/_contract/event-management.contract.yaml`

Vorbilder:
- `config/space_agent_registry.yml` → `agentfarm`: `agent: null`, `generate_agent: false`, `runtime`-Block.
- `spaces/schedule/execution.py`: `direct:`-Ziele, die ein Dict annehmen.
- sales-claw `sales-mcp/auto.py`: Gateway-HTTP mit Sitzungsschlüssel `agent:<id>:` und `/v1/models` als fail-closed-Vorprüfung.

## Global Constraints

- vibemind-os ist ÖFFENTLICH. Keine Schlüssel, Telefonnummern, Gruppen-IDs, kein Ort und kein Datum des Festivals ins Repo, auch nicht in Tests oder Commit-Texte. Neue private Angaben werden Platzhalter plus Eintrag in `werte.beispiel.json`.
- Schlüssel kommen nur per stdin ins Laufzeit-Volume oder als `*_FILE`-Secret. Sie stehen nie auf einer Kommandozeile, nie in einer Ausgabe und nie in einem Log.
- Die Space-ID bleibt `event-management`, der Präfix bleibt `event.`.
- Es entsteht kein OpenFang-Agent: `generate_agent: false`. `python scripts/sync_openfang_agents.py --check` muss unverändert grün bleiben.
- Nie `docker service update` oder `stack deploy` auf den vibemind-Stack. Der Space läuft als eigenes Compose-Projekt.
- Ausrollen (Task 6) erst nach ausdrücklichem OK von Felix. Bis dahin bleibt der alte Container `openclaw-festival` gestoppt.
- Claude-Code-Sessions committen direkt auf `master` (Submodul und äußeres Repo), Conventional Commits, danach den Pin im äußeren Repo nachziehen.
- pytest immer mit `--basetemp` auf C:, siehe Memory „pytest --basetemp auf C:“.

## Review Focus

1. **Brain-Text versucht eine Shell- oder Dateiaktion** („führe rm aus“): Die Brain-Agenten haben keine Laufzeit- und keine Dateiwerkzeuge. Test in Task 2.
2. **Gateway aus oder Token falsch:** `frage` und `auftrag` geben `{"ok": False, ...}` mit lesbarer Ursache zurück, ohne Ausnahme und ohne Token im Text. Test in Task 3.
3. **Tippfehler im Agentennamen:** OpenClaw legt einen unbekannten Agenten still mit ALLEN Werkzeugen an. Deshalb wird vor jedem Aufruf `/v1/models` geprüft und es geht nichts raus, wenn der Agent fehlt. Test in Task 3.
4. **App-Token abgelaufen (401):** Die Wahrheitsprüfung meldet „unbekannt“ (`None`), nie „ok“. Test in Task 4.
5. **Derselbe Telegram-Bot-Token pollt doppelt** (alter Container wieder an): Vor dem Start wird geprüft, dass `openclaw-festival` steht. Schritt in Task 6.

---

### Task 0: Messen, bevor gebaut wird (kein Code)

**Files:**
- Create: `spaces/event-management/MESSUNG-2026-11.md`. Die Datei kommt nicht ins Repo, falls sie Hosts oder Pfade nennt, die privat sind. Dann wandert der Befund ins Ledger.

- [ ] **Step 1:** Feststellen, in welchem Prozess `direct:`-Ziele des Brain wirklich laufen: brain-core im Swarm auf offload-vm oder der Brain am PC. Gemessen wird über die laufende Instanz (`/introspection`-Route oder Log einer bekannten `direct:schedule`-Ausführung), nicht über die Konfiguration.
- [ ] **Step 2:** Den Netzweg von dort zum Gateway festlegen. Das Gateway bindet im Container nur Loopback. Zur Wahl stehen ein veröffentlichter Port nur auf der Tailscale-Adresse der VM oder ein angeschlossenes Overlay-Netz. Festgehalten werden die Wahl, die URL-Form (ohne echte Werte) und der Name der Umgebungsvariablen `EVENT_GATEWAY_URL`.
- [ ] **Step 3:** Prüfen, ob die Event-App noch antwortet. Auf der VM läuft ein JSON-RPC-Aufruf `tools/call` `get_festival_info` mit dem Schlüssel aus der alten Config. Der Schlüssel wird nur auf der VM gelesen und nie ausgegeben, berichtet werden nur der HTTP-Status und `ok`/`fehler`.
- [ ] **Step 4:** Stopp-Regel. Antwortet die App nicht (Status ≠ 200 oder 401), wird der Plan angehalten und Felix gefragt. Ohne App gibt es nichts anzumelden.

### Task 1: Space-Ordner importierbar machen

`direct:<modul>:<funktion>` braucht einen Python-Pfad. `spaces.event-management` ist keiner.

**Files:**
- Move: `spaces/event-management/` → `spaces/event_management/` (`git mv`)
- Create: `spaces/event_management/__init__.py` (leer, mit einer Docstring-Zeile)
- Modify: `spaces/event_management/tests/test_event_management.py` (nur Pfade); Kopfkommentar in `docker-compose.yml`
- Modify: `spaces/_contract/event-management.contract.yaml`, Kommentarpfade

**Interfaces:**
- Produces: das Paket `spaces.event_management`. Space-ID und Vertragsdateiname bleiben `event-management`, wie bei `agentfarm` mit `source_path: spaces/captain_cook`.

- [ ] **Step 1:** Test `test_paket_importierbar` ergänzen: `importlib.import_module("spaces.event_management")` wirft nicht.
- [ ] **Step 2:** Ausführen, Erwartung FAIL (`ModuleNotFoundError`).
- [ ] **Step 3:** `git mv`, `__init__.py`, Pfade anpassen.
- [ ] **Step 4:** `python -m pytest spaces/event_management/tests -q --basetemp=<C:-Pfad>`, Erwartung: alle grün (23 + 1).
- [ ] **Step 5:** Commit `refactor(event-management): Space-Ordner importierbar (event_management)`.

### Task 2: Gateway-Agenten für den Brain in der Saat

**Files:**
- Modify: `spaces/event_management/config/openclaw.json`
- Modify: `spaces/event_management/config/workspace/SOUL.md`, neuer Abschnitt `## Aufträge aus VibeMind`
- Test: `spaces/event_management/tests/test_event_management.py`

**Interfaces:**
- Produces: die Gateway-Agenten-IDs `brain-lesen` und `brain-auftrag`, sichtbar als `openclaw/brain-lesen` und `openclaw/brain-auftrag` in `GET /v1/models`. Dazu `gateway.http.endpoints.chatCompletions.enabled: true`.

- [ ] **Step 1: Tests schreiben:**
  - `test_brain_agenten_eingetragen`: `{a["id"] for a in agents.list} >= {"brain-lesen", "brain-auftrag"}`, und der Chat-Endpunkt ist an.
  - `test_brain_agenten_ohne_shell_und_dateien`: Für beide Agenten enthält `tools.deny` `"group:runtime"` und `"group:fs"`. Das ist Review Focus 1; der Foto-Skill bleibt dem Telegram-Agenten vorbehalten.
  - `test_brain_lesen_darf_nicht_schreiben`: `tools.deny` von `brain-lesen` enthält alle acht Schreibwerkzeuge aus der SOUL-Tabelle „Schreiben“. Für den Abgleich dient die vorhandene Hilfsfunktion `_werkzeuge_aus_soul()`.
  - `test_soul_regelt_actor_fuer_vibemind`: SOUL.md enthält im Abschnitt `## Aufträge aus VibeMind` den Satz ``Setze `actor` auf „VibeMind“``.
- [ ] **Step 2:** Ausführen, Erwartung FAIL.
- [ ] **Step 3:** Saat und SOUL.md ergänzen.
  - Der bestehende Telegram-Agent bleibt der Standard (`main`).
  - Der neue SOUL-Abschnitt sagt: Nachrichten mit dem Kopf `[VibeMind]` kommen nicht aus der Gruppe. Dann gilt actor = „VibeMind“, und in die Gruppe wird nicht geschrieben.
- [ ] **Step 4: Messen in einem Wegwerf-Gateway auf der VM** (Image wie Saat, Modell-Ersatz wie am 29.10. für sales, keine Kosten):
  - Ob `tools.deny` einzelne MCP-Werkzeuge per Namen wirklich entfernt. Belegt wird das mit der Werkzeugliste in der Anfrage an den Modell-Ersatz.
  - Ob beide Agenten in `/v1/models` stehen.
  - Ruling-Regel: Greift das Sperren per Namen nicht, wird `brain-lesen` NICHT ausgeliefert. `event.ask` entfällt, das wird im Ledger festgehalten und Felix informiert. Weiter geht es nur mit `event.do`.
- [ ] **Step 5:** Tests grün, Commit `feat(event-management): Gateway-Agenten brain-lesen und brain-auftrag`.

### Task 3: Übergabe-Modul

**Files:**
- Create: `spaces/event_management/uebergabe.py`
- Test: `spaces/event_management/tests/test_uebergabe.py` (Attrappen-Gateway als `ThreadingHTTPServer`, Muster `sales-mcp/tests/test_stt_anbinden.py`)

**Interfaces:**
- Consumes: Agenten-IDs aus Task 2.
- Produces:
  - `frage(value: dict) -> dict` und `auftrag(value: dict) -> dict`. Eingabe ist `{"text": str, ...}`, wie bei `DirectExecutor.call_with_arg` mit `arg_kwarg="text"`.
  - Rückgabe: `{"ok": bool, "antwort": str, "agent": str, "result_id": str, "fehler": str | None}`. `result_id` ist der Sitzungsschlüssel.
  - Umgebungsvariablen: `EVENT_GATEWAY_URL` sowie `EVENT_GATEWAY_TOKEN` oder `EVENT_GATEWAY_TOKEN_FILE`; `_FILE` hat Vorrang, beide werden mit `strip()` gelesen (Memory „Env-CRLF leakt Schlüssel“).

- [ ] **Step 1: Tests schreiben:**
  - `test_frage_geht_an_brain_lesen`: Die Attrappe sieht den Header `x-openclaw-session-key`, der mit `agent:brain-lesen:` beginnt, das Modell `openclaw/brain-lesen` und den Text mit dem Kopf `[VibeMind] `.
  - `test_auftrag_geht_an_brain_auftrag`: analog für den Auftrag.
  - `test_ohne_agent_in_models_geht_nichts_raus`: `/v1/models` ohne `openclaw/brain-auftrag` ergibt `ok is False`, „brain-auftrag“ in `fehler`, und die Attrappe zählt 0 POST.
  - `test_gateway_aus`: URL auf einen freien Port ergibt `ok is False` ohne Ausnahme.
  - `test_502_ist_endgueltig`: Antwortet die Attrappe mit 502, folgt genau 1 POST, kein Wiederholen.
  - `test_token_nie_in_rueckgabe`: Mit dem Token `"geheim-123"` und einer 401 kommt `"geheim-123"` nirgends in `str(rueckgabe)` vor.
  - `test_leerer_text_wird_abgelehnt`: `{"text": "  "}` ergibt `ok is False` und 0 Anfragen.
- [ ] **Step 2:** Ausführen, Erwartung FAIL.
- [ ] **Step 3:** Umsetzen.
  - Timeout 90 s pro Aufruf.
  - Sitzungsschlüssel `agent:<id>:vibemind-<uuid4>`.
  - Weder Text noch Antwort ins Log, nur Agent, Status und Dauer.
- [ ] **Step 4:** Tests grün.
- [ ] **Step 5:** Commit `feat(event-management): Uebergabe Brain -> Event-Bot`.

### Task 4: Wahrheitsprüfung `event_app_change`

**Files:**
- Modify: `brain/the_brain/core/world_observer.py`: neue Funktion `_check_event_app_change(spec)` plus Eintrag in `_CHECKS`
- Test: `brain/the_brain/tests/test_world_observer_event_app.py`

**Interfaces:**
- Produces: Prüfname `event_app_change`.
  - Spec: `{check: event_app_change, actor: "VibeMind", since_s: 600}`.
  - Umgebungsvariablen: `EVENT_APP_URL` sowie `EVENT_APP_TOKEN` oder `EVENT_APP_TOKEN_FILE`.
  - Rückgabe wie alle Prüfungen: `(ok | None, signal: dict, detail: str)`.

- [ ] **Step 1: Tests schreiben:**
  - `test_aenderung_von_vibemind_im_fenster_ist_ok`: Die Attrappe antwortet auf `tools/call list_recent_changes` mit einem Eintrag `actor="VibeMind"` von vor 30 s; Ergebnis `ok is True`.
  - `test_nur_fremde_oder_alte_aenderungen`: Ergebnis `ok is False`.
  - `test_401_ist_unbekannt`: Ergebnis `ok is None`, und der Token steht nicht in `detail`.
  - `test_sse_antwort_wird_gelesen`: Die Antwort kommt als `text/event-stream` mit einer `data:`-Zeile; Ergebnis `ok is True`.
  - `test_ohne_umgebung_unbekannt`: Ohne `EVENT_APP_URL` ist das Ergebnis `None`, und „EVENT_APP_URL“ steht in `detail`.
- [ ] **Step 2:** Ausführen, Erwartung FAIL.
- [ ] **Step 3:** Umsetzen.
  - Zuerst `initialize`, dann `tools/call`, mit `Accept: application/json, text/event-stream`, `Mcp-Session-Id` aus der ersten Antwort übernehmen.
  - Feldnamen der Antwort aus der echten App ablesen (Task 0, Schritt 3) und als Konstante festhalten.
- [ ] **Step 4:** Tests grün, außerdem `python -m pytest brain/the_brain/tests -q -k world_observer`.
- [ ] **Step 5:** Commit `feat(brain): Wahrheitspruefung event_app_change`.

### Task 5: Brain-Kataloge eintragen

**Files:**
- Modify:
  - `config/space_agent_registry.yml`: neuer Eintrag `event-management:` nach `agentfarm`
  - `spaces/_navigator/registry.py`: `SPACES["event-management"]`
  - `brain/the_brain/data/capabilities.yaml`: zwei Einträge
  - `spaces/_contract/event-management.contract.yaml`: auf zwei Capabilities umstellen
  - `spaces/event_management/tests/test_event_management.py`: `test_vertrag_bildet_jedes_werkzeug_mit_richtigem_writes_ab` wird ersetzt
- Test: `brain/the_brain/tests/test_space_contract.py` (bestehend), dazu der neue Test unten

**Interfaces:**
- Consumes: `spaces.event_management.uebergabe:frage|auftrag` (Task 3) und die Prüfung `event_app_change` (Task 4).
- Produces: die Events `event.ask` und `event.do` sowie die Capabilities `event_ask` (writes false) und `event_do` (writes true).

Ruling: Der Brain sieht grobe Events, der Bot hat die feinen Werkzeuge. Das entspricht dem Muster von agentfarm: „Coarse events outside, fine refs inside“, und laut CONTEXT.md ist eine Capability die Sicht des Brain. Die 17 Werkzeug-Capabilities aus Teil 1 entfallen deshalb im Vertrag. Die Werkzeugtabelle bleibt in SOUL.md.

- [ ] **Step 1: Test `test_event_space_im_brain_angemeldet`** in `spaces/event_management/tests/test_brain_anmeldung.py`:
  - `load_space_contract()` kennt `"event-management"`, und `event_space_map["event.ask"] == "event-management"`.
  - `registry_health(...)["issues"] == []` mit den echten Capabilities.
  - Beide Capabilities in `capabilities.yaml` zeigen auf `direct:spaces.event_management.uebergabe:frage` bzw. `:auftrag`, und `event_do.validator.kind == "truth:event_app_change"` mit `on_fail == "report"`.
  - Der Registry-Eintrag hat `agent is None` und `generate_agent is False`.
  - Vertrag: `{c["name"]: c["writes"]} == {"event_ask": False, "event_do": True}`.
- [ ] **Step 2:** Ausführen, Erwartung FAIL.
- [ ] **Step 3: Einträge schreiben.**
  - Registry:
    - `prefixes: [event.]`, `aliases: [festival, orga, veranstaltung]`, `keywords: ["festival|veranstaltung|orga.?gruppe|event.?orga"]`
    - `runtime: {status: active, source_path: spaces/event_management, transport: openclaw-http, token_env: EVENT_GATEWAY_TOKEN}`
    - `events: {event.ask: {tool: event_ask, required_params: [text]}, event.do: {tool: event_do, required_params: [text]}}`
  - Navigator: `label "Event-Orga"`, `event_prefix "event."`, `stream None`, `renderer_id None`, `capabilities ["event.ask", "event.do"]`.
  - capabilities.yaml:
    - `arg_kwarg: text`
    - `match_patterns` nur mit Event-Bezug, damit „Aufgabe“ allein nicht greift: `"(festival|veranstaltung|event).{0,40}(aufgabe|budget|gäste|gaeste|team|helfer)"` für ask; für do zusätzlich ein Verb davor (`anlegen|eintragen|erledigt|zuweisen|ändern`).
    - `event_do.validator` mit `postcondition {check: event_app_change, actor: "VibeMind", since_s: 600}`.
- [ ] **Step 4: Prüfen:**
  - `python -m pytest spaces/event_management/tests brain/the_brain/tests/test_space_contract.py brain/the_brain/tests/test_space_registry_path.py -q --basetemp=<C:-Pfad>`
  - `python scripts/sync_openfang_agents.py --check`: unverändert, kein neuer Agent.
  - `python -m pytest scripts/tests -q -k "openfang or registry"`
  - Erwartung: alles grün.
- [ ] **Step 5:** Kollisionsprobe für Match-Patterns: Die vorhandenen Routing-Tests laufen (`python -m pytest brain/the_brain/tests -q -k "routing or capabilit"`), und keine bisher grüne Absicht routet jetzt nach `event_*`.
- [ ] **Step 6:** Commit `feat(event-management): im Brain angemeldet (event.ask, event.do)`, danach den äußeren Pin ziehen.

### Task 6: Ausrollen (erst nach OK von Felix)

**Files:**
- Create: `spaces/event_management/deploy/anbinden.sh`, Muster sales-claw `deploy/stt-anbinden.sh`. Es nimmt Gateway-Token, Telegram-Bot-Token und App-Authorization per stdin, schreibt sie mit `node -e` ins Volume (0600) und setzt `config set` nur bei Abweichung.
- Test: `spaces/event_management/tests/test_anbinden.py`. Statische Prüfungen wie in `test_stt_anbinden.py`: nur stdin, kein `docker cp`, `umask 077`, keine `$…TOKEN` in `echo`.

- [ ] **Step 1:** Tests schreiben, FAIL, `anbinden.sh` schreiben, grün, Commit.
- [ ] **Step 2:** Auf der VM `werte.local.json` aus der alten Config erzeugen, nur auf der VM, ohne Ausgabe der Werte. Danach `python deploy/fuellen.py werte.local.json build/`.
- [ ] **Step 3:** Prüfen, dass `openclaw-festival` steht: `docker inspect -f '{{.State.Running}}'` liefert `false` (Review Focus 5).
- [ ] **Step 4:** `docker compose up -d` im Space-Ordner (eigenes Projekt), danach `bash deploy/anbinden.sh`.
- [ ] **Step 5: Smoke:**
  - `/healthz` 200.
  - `/v1/models` enthält beide Brain-Agenten.
  - `event.ask` „Was ist noch offen?“ Ende zu Ende über den Brain liefert `ok True`.
  - Ein `event.do` nur mit Felix' OK und an einer von ihm genannten Testaufgabe; danach meldet die Prüfung `event_app_change` `ok True`.
- [ ] **Step 6:** Brain-Umgebung setzen: `EVENT_GATEWAY_URL`, `EVENT_GATEWAY_TOKEN_FILE`, `EVENT_APP_URL`, `EVENT_APP_TOKEN_FILE`. Das geschieht über den dokumentierten Launcher- bzw. Secret-Weg, nicht per `service update`.
- [ ] **Step 7:** WORKBOARD-Zeile `cc-event-management` schließen, Memory `project_space_event_management.md` aktualisieren.
