# Event-Management: Repo-Compose übernimmt den Live-Stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `deploy/ausrollen.sh` legt Shims, Dienste und Claw im selben Compose-Projekt an wie der laufende Festival-Bot (`event-management`), mit dessen Volumes, Netzen und Bot — statt still ein zweites Projekt `event_management` mit leeren Volumes zu starten.

**Architecture:** Die Repo-`docker-compose.yml` bekommt `name: event-management`; die Live-Netze sind dort identisch definiert, Compose übernimmt sie und die Volumes `event-management_event-management-claw-{state,keys}`. `ausrollen.sh` prüft vor jeder Docker-Änderung, dass vorhandene Container zu diesem Projekt gehören, und vor `dienste`, dass `laufzeit/event-telegram-token` derselbe Bot ist, den der Claw nutzt (Felix 16.11.: **(a) Festival-Bot bleibt**). Ein dokumentierter Umzugsschritt holt die fehlenden Laufzeit-Dateien aus `~/event-management`.

**Tech Stack:** bash (`deploy/ausrollen.sh`), Python 3.12 (`deploy/pg_hilfe.py`), Docker Compose v2, pytest mit Attrappen.

**Spec:** `docs/superpowers/specs/2026-11-12-event-management-system-design.md` (Etappe 4 Teil B); Befund und Entscheidung im Ledger `.superpowers/sdd/2026-11-14-event-system-etappe-4-brain-legacy-ausrollen/progress.md` (Eintrag 16.11. „BLOCKER vor Phase shim“).

## Global Constraints

- Projektname exakt `event-management` (Bindestrich) — so heißt das laufende Projekt auf offload-vm.
- Werte (Tokens, DSN) nur über 0600-Dateien oder stdin, nie als Argument, nie in einer Ausgabe; Ausgaben nennen nur Namen und Zustände (gleich/ungleich/fehlt).
- vibemind-os ist öffentlich: keine Chat-IDs, Gruppennamen, Tailnet-Namen, Tokens in Code, Tests oder Doku.
- Keine neue Phase und keine neuen Stempel; die Phasenreihenfolge bleibt.
- Tests: `pytest -p no:playwright --basetemp=<C:-Pfad>`, Git-Bash vorn im PATH; nie parallel.
- Commits direkt auf vibemind-os master (Worktree `wt-vos-em-brain`, Zweig = master), Conventional Commits, Abschluss `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; danach Pin im äußeren Repo über `wt-outer-pin`.

## Review Focus

1. Container existiert noch gar nicht (frische VM) → Projektprüfung meldet ok, nicht FEHLT (Test in Task 2).
2. Projektprüfung scheitert erst, nachdem `shim` schon gebaut/umgetaggt hat → Abbruch muss vor `docker build`/`docker tag` stehen (Test in Task 2: Protokoll ohne `build`/`tag`).
3. Token-Datei mit Zeilenumbruch/Leerzeichen am Ende → Bot-Vergleich trotzdem „gleich“ (Test in Task 3).
4. `openclaw.json` ohne `channels.telegram.botToken` oder kaputtes JSON → „fehlt“, Exit ≠ 0, kein Traceback mit Inhalt (Test in Task 3).
5. `bot-token` auf ein vorhandenes Ziel → verweigert, überschreibt nie (Test in Task 3).

---

### Task 1: Projektname und Netzname

**Files:**
- Modify: `spaces/event_management/docker-compose.yml` (oberste Ebene, vor `services:`)
- Modify: `spaces/event_management/deploy/ausrollen.sh:70-73`
- Test: `spaces/event_management/tests/test_anbinden.py`, `spaces/event_management/tests/system/test_ausrollen.py`

**Interfaces:**
- Produces: Shell-Variablen `PROJEKT=event-management` und `NETZ=${PROJEKT}_event-agent` in `ausrollen.sh` (Task 2 nutzt `PROJEKT`).

- [ ] **Step 1: Failing tests**

```python
# tests/test_anbinden.py
def test_compose_projekt_heisst_wie_der_live_stack() -> None:
    # Live laeuft als Projekt event-management (~/event-management); ohne name: hiesse es
    # nach dem Ordner event_management: zweite Netze im selben Adressraum, leere Volumes.
    assert _compose()["name"] == "event-management"
```

In `test_pruefen_adresse_14_belegt` zusätzlich: `assert "docker network inspect event-management_event-agent" in pj.protokoll()` (Methode der `Projekt`-Klasse, die das Attrappen-Protokoll liest; heißt sie anders, die vorhandene nehmen).

- [ ] **Step 2:** `pytest spaces/event_management/tests/test_anbinden.py::test_compose_projekt_heisst_wie_der_live_stack spaces/event_management/tests/system/test_ausrollen.py::test_pruefen_adresse_14_belegt` → beide FAIL.
- [ ] **Step 3:** `name: event-management` mit einem Kommentar (warum) in die Compose-Datei; in `ausrollen.sh` `PROJEKT=event-management` und `NETZ=${PROJEKT}_event-agent`.
- [ ] **Step 4:** Beide Tests PASS; `grep -n "docker compose" deploy/messen.sh` zeigt, dass `messen.sh` kein Compose nutzt (sonst Ruling im Ledger).
- [ ] **Step 5:** Commit `fix(event-management): Compose-Projekt event-management wie der Live-Stack`.

### Task 2: Projekt- und Laufzeitprüfung vor jeder Docker-Änderung

**Files:**
- Modify: `spaces/event_management/deploy/ausrollen.sh` (neue Funktion bei den Hilfen ab Zeile 125; Aufrufe in `phase_pruefen`, `phase_shim`, `phase_dienste`, `phase_anbinden`)
- Test: `spaces/event_management/tests/system/test_ausrollen.py` (Attrappe `DOCKER`, Fixture `Projekt`, neue Tests)

**Interfaces:**
- Consumes: `PROJEKT`, `CLAW`, `SHIM`, `SHIM_E` (Task 1 / vorhanden).
- Produces: `fremde_projekte()` — schreibt je Container aus `$CLAW $SHIM $SHIM_E`, der existiert und dessen Label `com.docker.compose.project` ≠ `$PROJEKT` ist, eine Zeile `<container> <projekt>` auf stdout; nichts, wenn alle passen oder fehlen. Gelesen mit `docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' <c>`; Exit ≠ 0 von inspect = Container fehlt.

- [ ] **Step 1: Attrappe erweitern:** im `DOCKER`-Skript vor dem allgemeinen `inspect`-Zweig: Argumente enthalten `compose.project` → bei `FAKE_PROJEKT=-` Exit 1, sonst `echo "${FAKE_PROJEKT:-event-management}"`. Fixture `Projekt` legt `laufzeit/claude-token` (0600, Inhalt `CLAUDE_CODE_OAUTH_TOKEN=x`) und Ordner `laufzeit/shim-budget/` an.

- [ ] **Step 2: Failing tests**

```python
def test_pruefen_fremdes_compose_projekt_meldet_fehlt(pj):
    r = pj.lauf("pruefen", umgebung={"FAKE_PROJEKT": "event_management"})
    assert r.returncode != 0 and "FEHLT" in r.stdout and "event_management" in r.stdout
    assert not (pj.st / "pruefen.ok").exists()

def test_pruefen_ohne_container_ist_projekt_ok(pj):
    assert pj.lauf("pruefen", umgebung={"FAKE_PROJEKT": "-"}).returncode == 0

@pytest.mark.parametrize("phase", ["shim", "dienste", "anbinden"])
def test_fremdes_projekt_bricht_vor_jeder_docker_aenderung_ab(phase, pj):
    # Vorphasen stempeln wie die vorhandenen Tests der Phase (Baukontext fuer shim, Vergleich fuer anbinden).
    r = pj.lauf(phase, umgebung={"FAKE_PROJEKT": "event_management"})
    assert r.returncode != 0 and "Compose-Projekt" in r.stderr
    p = pj.protokoll()
    assert not re.search(r"docker (build|tag|compose)", p)

def test_pruefen_und_shim_verlangen_claude_token(pj):
    (pj.lz / "claude-token").unlink()
    assert "claude-token" in pj.lauf("pruefen").stdout
```

(Namen `pj.st`, `pj.lz`, `pj.lauf`, `pj.protokoll` an die vorhandene `Projekt`-Klasse anpassen.)

- [ ] **Step 3: Implementieren.** `fremde_projekte` wie oben. `phase_pruefen`: je Zeile `fehlt "Container <c> gehoert zum Compose-Projekt <p>, erwartet $PROJEKT"`, sonst `ok "Compose-Projekt $PROJEKT"`; `$LZ/claude-token` in die 0600-Dateiliste, `$LZ/shim-budget` als Ordner verlangen. `phase_shim`, `phase_dienste`, `phase_anbinden`: als erste Arbeit nach `verlange_vorphasen` `[ -z "$(fremde_projekte)" ] || abbruch "Compose-Projekt: ... (ausrollen.md, Umzug)"`; `phase_shim` verlangt dort auch `claude-token` und `shim-budget`.
- [ ] **Step 4:** Neue Tests und die ganze `test_ausrollen.py` PASS.
- [ ] **Step 5:** Commit `fix(event-management): ausrollen prueft Compose-Projekt und Shim-Laufzeit vor Docker`.

### Task 3: Derselbe Bot für Claw und App

**Files:**
- Modify: `spaces/event_management/deploy/pg_hilfe.py` (zwei Unterbefehle)
- Modify: `spaces/event_management/deploy/ausrollen.sh` (`phase_pruefen`, `phase_dienste`)
- Test: `spaces/event_management/tests/system/test_ausrollen.py` (Muster `test_pg_hilfe_*`, Attrappe `PYTHON3`)

**Interfaces:**
- Produces:
  - `pg_hilfe.py bot-gleich <tokendatei> <openclaw.json>` → Exit 0 + `gleich`, Exit 1 + `ungleich`, Exit 2 + `fehlt` (Datei/Schlüssel `channels.telegram.botToken` fehlt, JSON kaputt). Verglichen wird die Bot-ID (Teil vor `:`), beide Werte `strip()`. Nie ein Wert in Ausgabe oder Fehlertext.
  - `pg_hilfe.py bot-token <openclaw.json> <ziel>` → schreibt den `botToken` mit Zeilenende nach `<ziel>` (0600, `os.open(..., O_CREAT|O_EXCL, 0o600)`), Ausgabe `geschrieben`; Ziel vorhanden → Exit 1 `ziel existiert`; kein Token → Exit 2 `fehlt`.

- [ ] **Step 1: Failing tests** (direkt mit `sys.executable deploy/pg_hilfe.py …`, `tmp_path`):
  - `test_pg_hilfe_bot_gleich` — Token `FAKE_TOKEN + "\n  "` gegen JSON mit demselben Token → 0/`gleich`; anderer Bot (`"654321:" + "B"*35`) → 1/`ungleich`; JSON ohne Schlüssel, kaputtes JSON, fehlende Datei → 2/`fehlt`; in keinem Fall `FAKE_TOKEN` oder `"B"*35` in stdout+stderr.
  - `test_pg_hilfe_bot_token_schreibt_0600_und_ueberschreibt_nie` — schreibt, Inhalt `FAKE_TOKEN + "\n"`, Modus 0600 (unter Windows nur Inhalt prüfen); zweiter Aufruf → 1, Inhalt unverändert; Token nicht in der Ausgabe.
  - `test_dienste_bricht_bei_anderem_bot_ab` — Attrappe `PYTHON3`: `deploy/pg_hilfe.py bot-gleich` → `exit ${FAKE_BOT:-0}`; mit `FAKE_BOT=1` bricht `dienste` ab, Protokoll ohne `docker compose`, kein `dienste.ok`; ohne Sicherung (`laufzeit/sicherung/openclaw-*` fehlt) ebenso.
  - `test_pruefen_meldet_anderen_bot` — `FAKE_BOT=1` → `FEHLT` mit „Bot“; ohne Sicherung → Zeile `offen  Bot-Vergleich (nach sichern)`, kein FEHLT.
- [ ] **Step 2:** Tests FAIL.
- [ ] **Step 3:** Implementieren; `ausrollen.sh` vergleicht mit `neueste_sicherung` (vorhanden, Zeile ~581) — `pruefen` meldet ok/FEHLT/offen, `dienste` bricht vor `docker compose` ab.
- [ ] **Step 4:** PASS, ganze `test_ausrollen.py` PASS.
- [ ] **Step 5:** Commit `feat(event-management): Bot von App und Claw muss gleich sein`.

### Task 4: Anleitung — Umzug aus `~/event-management`

**Files:**
- Modify: `spaces/event_management/deploy/ausrollen.md` (neuer Abschnitt „Umzug des Live-Stacks (vor `shim`)“ nach „Danach: messen“; Hinweise in Phase 6 und 7)
- Test: `spaces/event_management/tests/system/test_ausrollen.py` (Doku-Test)

- [ ] **Step 1: Failing test** `test_anleitung_umzug_des_live_stacks`: `ausrollen.md` enthält `name: event-management`, `claude-token`, `shim-budget`, `bot-token`, `bot-gleich`, `event-telegram-token.testbot`, und die Zeichenfolge `~/event-management` höchstens als Pfad ohne private Werte (Test `test_keine_geheimnisse_oder_echten_ziele` bleibt grün).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Abschnitt schreiben** (Handarbeit, eigenes OK, `umask 077`, im Space-Ordner auf der VM):
  1. `cp -p ~/event-management/laufzeit/claude-token laufzeit/` und `cp -rp ~/event-management/laufzeit/shim-budget laufzeit/` (Budgetzähler des Tages laufen weiter).
  2. Festival-Bot (Felix 16.11., Variante a): `mv laufzeit/event-telegram-token laufzeit/event-telegram-token.testbot`, dann `"$PY" deploy/pg_hilfe.py bot-token "$(ls -1 laufzeit/sicherung/openclaw-*.json | tail -1)" laufzeit/event-telegram-token`, dann `bot-gleich` → `gleich`.
  3. `gateway-token` schreibt `anbinden` selbst; dort Zeile „Telegram-Bot-Token“ = der Festival-Bot.
  4. Danach `~/event-management` nicht mehr per `docker compose` bedienen; ab `shim` legt das Repo die Container im selben Projekt neu an (Label `working_dir` wechselt).
  Phase 6/7: je ein Satz zu den neuen Prüfungen. Kein erneutes `pruefen` nötig (es würde die späteren Stempel löschen); die Prüfungen laufen in `shim`/`dienste`/`anbinden` selbst.
- [ ] **Step 4:** Doku-Test und ganze Suite des Space PASS (volle Suite ~20 min, im Vordergrund, nicht parallel).
- [ ] **Step 5:** Commit `docs(event-management): Umzug des Live-Stacks vor shim`; Push vibemind-os master; Pin im äußeren Repo über `wt-outer-pin` (`chore: pin vibemind-os …`), Push.

### Task 5: Umzug auf offload-vm (Betrieb, kein Code)

Nur nach Task 4 und mit Claim (WORKBOARD + Vault). Felix' OK „a“ deckt den Token-Tausch; die Phase `shim` braucht danach ihr eigenes OK.

- [ ] VM-Checkout `/home/debian/vibemind-os` auf den neuen Stand (detached, ohne Submodule), `git status` sauber.
- [ ] Umzug Schritte 1–2 aus der Anleitung; Ausgabe nur `geschrieben`/`gleich`.
- [ ] Gegenprobe ohne Stempelverlust: `docker compose config` im Space zeigt `name: event-management`; `docker compose ps` listet `event-management-claw` und `event-management-shim` als Teil des Projekts; `ls -l laufzeit/claude-token laufzeit/event-telegram-token` = 0600.
- [ ] Ledger-Eintrag, Gedächtnisnotiz, Claim auf „ruht“; Felix um OK für `shim` fragen.
