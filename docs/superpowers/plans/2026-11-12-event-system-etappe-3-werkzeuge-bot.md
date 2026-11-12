# Event-Management-System, Etappe 3: Werkzeug-Server und Bot – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `event-mcp` mit Schlüsselarten, Event-Wahl per Marke und zweistufigen riskanten Aktionen; die 14 Zusatz-Bausteine und `nachrichten`; Gruppe/Direktchat verbinden per Einmal-Code; ein Bereitstellungs-Arbeiter, der Agenten, Server-Einträge und Bindings im `event-management-claw` erzeugt und wieder entfernt.

**Architecture:** `event-mcp` ist ein MCP-Server (Python `mcp` 1.27, Low-Level-`Server` + `StreamableHTTPSessionManager`, stateless, JSON-Antworten) in `spaces/event_management/system/mcp_server.py`. Er nutzt **dieselbe Fachschicht** wie `event-api` (Etappe 1). Der Aufrufkontext entsteht bei **jedem** `tools/list` und `tools/call` neu aus dem Bearer-Schlüssel der HTTP-Anfrage (Hash-Lookup in `event.schluessel`), nie aus Argumenten. Werkzeuge werden aus `REGISTRY` und dem wirksamen Schema erzeugt. Alles, was Telegram oder OpenClaw berührt, liegt hinter injizierbaren Schnittstellen und wird mit Fakes getestet; kein Task dieser Etappe ändert den laufenden Betrieb.

**Tech Stack:** Python 3.12, psycopg 3, FastAPI (bestehende `event-api`), `mcp` 1.27, httpx (Tests), Next.js (Erweiterung der Etappe-2-Oberfläche), PG16-Wegwerfcluster.

**Spec:** `docs/superpowers/specs/2026-11-12-event-management-system-design.md` — Abschnitte 1 (Zusatz-Bausteine, `nachrichten`), 3 (`event-mcp`), 4 (Bot). Etappe 1 (Fundament) und Etappe 2 (Oberfläche) sind Voraussetzung.

**Nicht in dieser Etappe:** Ausrollen auf `offload-vm`, jeder Eingriff in den laufenden `event-management-claw` oder die Live-Telegram-Gruppe (Etappe 4, nur mit Felix-OK); Brain-Umstellung, Legacy-Umbenennung `main → ev-legacy` (Etappe 4). Die drei Messpunkte der Spec werden hier **vorbereitet** (Messskript + Anleitung), aber nicht live ausgeführt.

## Global Constraints

- vibemind-os ist **ÖFFENTLICH**: keine echten Firmen, Personen, Mails, Gruppen-/Chat-IDs, URLs, Bot-Tokens, Schlüssel – auch nicht in Tests oder Commit-Texten. Beispiel-IDs wie `-1001234567890`.
- **Schlüssel:** 32 Byte `secrets.token_urlsafe`, Präfix je Art (`evk_`), gespeichert nur als sha256-Hex, Vergleich über Hash-Lookup + `hmac.compare_digest`. Klartext wird genau einmal zurückgegeben (Rückgabewert der Ausstell-Funktion) und nie geloggt. Tokens in Logs/Fehlern werden maskiert.
- **Kontext nur aus dem Schlüssel.** Kein Werkzeug nimmt `firma_id`, `event_id` oder `person_id` als Argument. Event-Bindung über `Kontext.event_id` (Art `event`) oder eine gültige Marke (andere Arten).
- **Fachschicht zuerst:** Rechteprüfung, Event-Bindung, Legacy-Ablehnung und Bestätigungscodes liegen in Python-Modulen unter `system/`, nicht im MCP-Adapter. Der MCP-Adapter übersetzt nur.
- `Kontext.quelle`: `"bot"` für `event-mcp`; `actor` = `"bot:<schluessel-art>"`, `actor_person_id` = Person des Schlüssels (falls vorhanden).
- Fehlerantworten von Werkzeugen enthalten keine Nutzerdaten außer dem Event-Namen; `NichtGefunden` → „nicht gefunden“, `KeinRecht` → „keine Berechtigung“, `ValueError` → Text des Fehlers, alles andere → „interner Fehler“ (geloggt nur mit Typname).
- Jede Antwort eines schreibenden Werkzeugs nennt das Event: `{"event": {"name": …, "firma": …}, …}`.
- Neue SQL nur als neue nummerierte Migrationen (`003_…`, `004_…`); 001/002 nie ändern. RLS für jede neue Tabelle; Grants an `event_app` minimal.
- Tests: pytest aus dem Worktree-Root mit `-q -p no:playwright --basetemp=<C:-Pfad>` (Parent vorher anlegen); jede neue Tabelle in die Mandantenmatrix; Fachschicht-Tests über `db_admin` **und** RLS-Tests über `db_app`.
- Kein `print`/`logging` von Schlüsseln, Tokens, Chat-IDs oder Kundentexten.
- Conventional Commits, Commit-Texte enden mit `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Zwei Gespräche gleichzeitig mit verschiedenen Schlüsseln** (Gruppen-Agent A und Leitungs-Agent B rufen parallel): Jeder sieht nur seine Werkzeuge und Daten; kein Kontext sickert über Task-/Thread-Grenzen. → Test `test_parallel_zwei_schluessel_getrennt` in Task 4.
2. **Marke aus einem anderen Schlüssel / abgelaufene Marke / Marke für ein inzwischen archiviertes oder Legacy-Event:** klare Ablehnung, nichts geschrieben. → Tests in Task 3 und 5.
3. **Bestätigungscode zweimal benutzt, oder mit anderen Argumenten als im ersten Aufruf:** der zweite Gebrauch schlägt fehl; ein Code bestätigt genau die Aktion+Ziel, für die er ausgestellt wurde. → Tests in Task 3 und 5.
4. **Einmal-Code zum Verbinden von einem Fremden geraten oder doppelt gemeldet:** Codes sind ≥ 8 Zeichen aus 32er-Alphabet, 15 Minuten gültig, einmalig; nach 5 Fehlmeldungen je Chat 15 Minuten Sperre; ohne Bestätigung in der Oberfläche entsteht kein Auftrag. → Tests in Task 7.
5. **Bereitstellung läuft halb durch** (z. B. Agent gesetzt, Binding scheitert): Auftrag endet `fehler`, der Arbeiter versucht beim nächsten Lauf nicht blind erneut, sondern idempotent (jeder Schritt prüft den Ist-Zustand), und ein Abbau entfernt auch Teilstände. → Tests in Task 8.

---

## Dateistruktur

```text
spaces/event_management/
  db/003_zusatz_bausteine.sql      # 14 Zusatz-Tabellen + nachricht (Task 1)
  db/004_bot.sql                   # schluessel, marke, bestaetigung, verbindung, bereitstellung; person-Spalten einengen (Task 2)
  system/bausteine.py              # REGISTRY + Zusätze (Task 1)
  system/schluessel.py             # ausstellen, pruefen, widerrufen, Kontext aus Schlüssel (Task 2)
  system/marke.py                  # Marke + Bestätigungscode (Task 3)
  system/werkzeuge.py              # Werkzeugkatalog je Schlüssel + Ausführung (Task 4/5), MCP-frei
  system/mcp_server.py             # MCP-Adapter, HTTP-App, CLI (Task 4)
  system/nachrichten.py            # Gruppe senden, Mail-Entwürfe, Freigabe, Versand (Task 6)
  system/telegram.py               # Telegram-Bot-API-Client (Protokoll + urllib) (Task 6)
  system/verbinden.py              # Einmal-Codes, melden, getChat-Prüfung, bestätigen (Task 7)
  system/bereitstellung.py         # Aufträge → OpenClaw-Schritte, Ausführer-Protokoll (Task 8)
  system/soul.py                   # SOUL-Vorlage je Gruppen-/Leitungs-Agent (Task 8)
  system/api.py                    # neue Routen Nachrichten/Verbinden/Schlüssel (Task 6/7)
  web/...                          # Reiter Nachrichten, Gruppe/Chat verbinden (Task 6/7)
  config/openclaw.json             # main = Empfang mit melden-Server, requireMention (Task 8)
  deploy/agent_budget.yaml         # Tagesgrenze 300 (Task 8)
  deploy/bereitstellung.service/.timer, deploy/messen.md, deploy/messen.sh (Task 8/9)
  tests/system/test_*.py           # je Task
```

---

### Task 1: Zusatz-Bausteine und Tabelle `nachricht`

**Files:**
- Create: `db/003_zusatz_bausteine.sql`
- Modify: `system/bausteine.py` (REGISTRY), ggf. `system/kern.py` (neue Typen `url`, `kontakt`, `ref:*` falls Etappe 2 Task 1 sie noch nicht vollständig in `_wert` abbildet)
- Test: `tests/system/test_zusatz_bausteine.py`; Mandantenmatrix in `tests/system/test_rls.py` um alle neuen Tabellen erweitern

**Interfaces:**
- Consumes: Typen-Vokabular aus Etappe 2 Task 1 (`text`, `ganzzahl`, `datumzeit`, `auswahl:…`, `url`, `kontakt`, `ref:<eintrag>`); `kern.liste/anlegen/aendern/loeschen`.
- Produces: REGISTRY-Einträge (Eintrag → Tabelle, `*` = Pflicht; alle Tabellen haben `id uuid pk`, `event_id`, `firma_id`, `angelegt`, zusammengesetzte FKs wie in 001; `kontakt` → FK `(spalte, firma_id)` auf `kontakt`, `ref:<e>` → FK `(spalte, event_id)` auf die Tabelle von `<e>` mit `unique (id, event_id)` dort):

| Eintrag | Tabelle | Felder | Sortierung |
|---|---|---|---|
| `wettbewerb_teams` | `wettbewerb_team` | name*, thema text, ansprech_id kontakt | name, id |
| `abgaben` | `abgabe` | team_id* ref:wettbewerb_teams, titel*, link url, eingereicht datumzeit | angelegt, id |
| `jury` | `jury` | kontakt_id* kontakt, rolle text | angelegt, id |
| `aussteller` | `aussteller` | name*, ansprech_id kontakt, status auswahl:angefragt,zugesagt,abgesagt | name, id |
| `staende` | `stand` | bezeichnung*, aussteller_id ref:aussteller, groesse text | bezeichnung, id |
| `sprecher` | `sprecher` | kontakt_id* kontakt, thema text, status auswahl:angefragt,zugesagt,abgesagt | angelegt, id |
| `online_raum` | `online_raum` | titel*, link* url, zugang text | angelegt, id |
| `aufzeichnung` | `aufzeichnung` | titel*, link url, status auswahl:geplant,aufgenommen,veroeffentlicht | angelegt, id |
| `protokoll` | `protokoll_punkt` | punkt*, notiz text, verantwortlich_id kontakt | angelegt, id |
| `beschluesse` | `beschluss` | text*, gefasst datumzeit, verantwortlich_id kontakt | angelegt, id |
| `verpflegung` | `verpflegung` | titel*, menge ganzzahl, anbieter text, betrag_cent ganzzahl | angelegt, id |
| `programm` | `programm_punkt` | titel*, beginn datumzeit, ende datumzeit, beschreibung text | beginn, id |
| `helferschichten` | `helferschicht` | titel*, beginn* datumzeit, ende* datumzeit, kontakt_id kontakt, bedarf ganzzahl | beginn, id |
| `buehnen` | `buehne` | name*, ort text | name, id |

  Dazu Tabelle `event.nachricht` (kein REGISTRY-Eintrag, eigenes Modul in Task 6): `id, event_id, firma_id, kanal check in ('gruppe','mail'), betreff text, text text not null, empfaenger_filter jsonb not null default '{}'` (z. B. `{"zusage": ["zugesagt"]}`), `status check in ('entwurf','freigegeben','versendet','fehler','verworfen')`, `angelegt, erstellt_von text, freigegeben_von uuid references person, freigegeben timestamptz, versendet timestamptz, fehler text`, zusammengesetzter FK auf `event`. Checks: `helferschicht.ende >= beginn`, `programm_punkt.ende >= beginn` (wenn beide gesetzt), Zahlen `>= 0`, `link ~ '^https?://'`.
  RLS wie die Kern-Bausteine (Mandant + `event_ok`), Grants select/insert/update/delete an `event_app` (bei `nachricht` ohne delete).

- [ ] **Step 1: Tests** – `test_registry_hat_alle_zusaetze` (`set(ZUSAETZE) <= {t.baustein or t.name for t in REGISTRY.values()}`), `test_zusatz_crud_ueber_kern` (parametrisiert über alle 14: anlegen mit Pflichtfeldern → liste → aendern → loeschen, nur in einem Event, dessen Kategorie den Baustein hat; sonst `ValueError`), `test_ref_nur_im_selben_event` (Abgabe mit `team_id` eines anderen Events → `ValueError`, Admin-Nachlesen: nichts geschrieben), `test_kontakt_fremder_firma_abgelehnt`, `test_url_nur_http`, Mandantenmatrix: alle 15 neuen Tabellen in RLS-Test (Firma B sieht/schreibt nichts von A), `test_migration_003_idempotent_ueber_anwenden`.
- [ ] **Step 2: rot. Step 3: implementieren. Step 4: grün** + ganzer Ordner `spaces/event_management/tests`.
- [ ] **Step 5: Commit** `feat(event-management): Zusatz-Bausteine und Nachrichten-Tabelle`

---

### Task 2: Schlüssel (Tabellen + Fachschicht) und Personen-Spalten einengen

**Files:**
- Create: `db/004_bot.sql`, `system/schluessel.py`
- Test: `tests/system/test_schluessel.py`, Ergänzung `tests/system/test_rls.py`

**Interfaces:**
- Produces (Migration 004):
  - `event.schluessel (id uuid pk, hash text unique not null, art text check in ('event','leitung','betreiber','betreiber_lesen','melden'), event_id uuid null, firma_id uuid null, person_id uuid null references person, bezeichnung text not null, angelegt, ablauf timestamptz not null, widerrufen timestamptz null)`; Checks: `art='event'` ⇔ `event_id` und `firma_id` gesetzt (FK `(event_id, firma_id)`); `art='leitung'` ⇒ `person_id` gesetzt; sonst `event_id is null`.
  - `event.marke`, `event.bestaetigung`, `event.verbindung`, `event.bereitstellung` (Spalten in Task 3/7/8 festgelegt, aber **alle in 004** angelegt, damit es eine Bot-Migration gibt): 
    - `marke (wert_hash text pk, schluessel_id uuid not null references schluessel, event_id uuid not null references event on delete cascade, ablauf timestamptz not null)`
    - `bestaetigung (code_hash text pk, schluessel_id uuid not null references schluessel, aktion text not null, ziel jsonb not null, ablauf timestamptz not null, benutzt timestamptz null)`
    - `verbindung (id uuid pk, art check in ('gruppe','direkt'), code_hash text unique not null, event_id uuid null, firma_id uuid null, person_id uuid not null references person, ablauf timestamptz not null, chat_id bigint null, chat_titel text null, gemeldet timestamptz null, bestaetigt timestamptz null, status check in ('offen','gemeldet','bestaetigt','verworfen') default 'offen')`; `art='gruppe'` ⇒ event_id+firma_id gesetzt (FK)
    - `bereitstellung (id uuid pk, art check in ('anlegen','abbauen'), ziel check in ('gruppe','leitung','betreiber'), event_id uuid null, person_id uuid null, chat_id bigint not null, agent_id text not null, status check in ('offen','laeuft','erfolg','fehler') default 'offen', schritt text null, fehler text null, angelegt, gestartet timestamptz null, beendet timestamptz null)`
  - Diese Bot-Tabellen sind **nicht** für `event_app` per RLS-Mandant lesbar, sondern nur über `security definer`-Funktionen bzw. eine eigene Rolle: Ruling-frei festgelegt: `event_app` bekommt **keine** Grants auf `schluessel`, `marke`, `bestaetigung`; Zugriff nur über `security definer`-Funktionen `event.schluessel_pruefen(p_hash text)` (liefert id, art, event_id, firma_id, person_id, nur wenn nicht widerrufen und nicht abgelaufen), `event.marke_pruefen(p_hash, p_schluessel)`, `event.bestaetigung_einloesen(p_hash, p_schluessel, p_aktion, p_ziel)` (setzt `benutzt` atomar, liefert true/false). Ausstellen/Widerrufen von Schlüsseln läuft als Admin-Werkzeug (CLI) oder über `security definer`-Funktionen mit Rechteprüfung in SQL (`event.ist_betreiber()` oder Leitung der Firma).
  - `verbindung` und `bereitstellung`: RLS über `firma_id`/`person_id` (eigene Person) bzw. Betreiber; der Arbeiter läuft als Admin-DSN.
  - **Personen-Spalten einengen** (offen aus Etappe 1): `revoke select on event.person from event_app; grant select (id, mail, name, betreiber, aktiv, sitzungs_version, angelegt) on event.person to event_app;` – `passwort_hash` nur noch über eine `security definer`-Funktion `event.anmeldung_holen(p_mail)` lesbar; `zugang.anmelden` wird darauf umgestellt.
- Produces (`system/schluessel.py`):
  - `ART = ("event","leitung","betreiber","betreiber_lesen","melden")`
  - `ausstellen(conn_admin, *, art: str, bezeichnung: str, gueltig_tage: int = 365, event_id: UUID | None = None, person_id: UUID | None = None) -> tuple[UUID, str]` – (id, Klartext `evk_<token>`); prüft Kombinationen, Ablauf ≤ 730 Tage.
  - `widerrufen(conn_admin, schluessel_id: UUID) -> None`
  - `@dataclass(frozen=True) SchluesselInfo(id, art, event_id, firma_id, person_id)`
  - `pruefen(conn, klartext: str) -> SchluesselInfo` – `NichtGefunden` bei unbekannt/abgelaufen/widerrufen/falschem Format (einheitlich, keine Unterscheidung nach außen).
  - `kontext_fuer(conn, info: SchluesselInfo) -> Kontext`: `event` → `Kontext(person_id=None, betreiber=False, rollen={firma: "mitglied"}, event_id=…, actor="bot:event", quelle="bot")`; `leitung` → Rollen nur der Firmen, in denen die Person **aktuell** Leitung ist (Mitglied-Firmen nicht); `betreiber`/`betreiber_lesen` → `betreiber=True`; `melden` → `Kontext` ohne Firmen. Wenn `Kontext.person_id` heute nicht `None` sein darf, erweitert dieser Task `Kontext` minimal (Default bleibt kompatibel).
  - CLI `python -m spaces.event_management.system.schluessel ausstellen --art … --bezeichnung … [--event-id] [--person-id]` / `widerrufen <id>`: Admin-DSN aus `EVENT_ADMIN_DSN_FILE`, gibt den Klartext genau einmal auf stdout aus.

- [ ] **Step 1: Tests** – `test_ausstellen_speichert_nur_hash` (Admin-Select: kein Klartext in irgendeiner Spalte), `test_pruefen_ok_und_einheitlich_abgelehnt` (unbekannt / abgelaufen / widerrufen / `""` / ohne Präfix → alle `NichtGefunden` mit gleichem Text), `test_kombinationen` (event ohne event_id → ValueError; leitung ohne person → ValueError), `test_kontext_leitung_nur_leitungsfirmen` (Leitung in A, Mitglied in B → `rollen == {A: "leitung"}`; nach `rolle_aendern` auf mitglied → keine Firma mehr), `test_event_app_sieht_schluesseltabellen_nicht` (`db_app`: select auf `schluessel`, `marke`, `bestaetigung` → InsufficientPrivilege), `test_passwort_hash_fuer_event_app_unlesbar` + bestehende Anmelde-Tests grün, Mandantenmatrix für `verbindung`/`bereitstellung`.
- [ ] **Step 2–4:** rot → implementieren → grün + ganzer Ordner.
- [ ] **Step 5: Commit** `feat(event-management): Bot-Schluessel mit Hash, Ablauf und Widerruf; passwort_hash nicht mehr lesbar`

---

### Task 3: Marke und Bestätigungscode

**Files:**
- Create: `system/marke.py`
- Test: `tests/system/test_marke.py`

**Interfaces:**
- Consumes: `SchluesselInfo`, `kontext_fuer`, `events.event_laden`.
- Produces:
  - `MARKE_MINUTEN = 30`, `BESTAETIGUNG_MINUTEN = 5`
  - `marke_ausstellen(conn, info: SchluesselInfo, event_id: UUID) -> str` – prüft vorher mit `kontext_fuer(info)` per Fachschicht, dass das Event sichtbar ist (`NichtGefunden` sonst), und lehnt Legacy (`ValueError("Legacy-Event: bitte die alte App nutzen")`) sowie `archiviert` ab; Klartext `mk_<12 Zeichen>`.
  - `marke_aufloesen(conn, info: SchluesselInfo, marke: str) -> UUID` – event_id; `NichtGefunden("Marke ungültig oder abgelaufen – bitte Event neu wählen")` bei fremdem Schlüssel, abgelaufen, unbekannt.
  - `bestaetigung_ausstellen(conn, info, aktion: str, ziel: Mapping[str, str]) -> str` – Code `bs_<8 Zeichen>`; `ziel` enthält die kanonischen IDs der Aktion (z. B. `{"event_id": …}` oder `{"firma_id": …, "kontakt_id": …}`).
  - `bestaetigung_einloesen(conn, info, code: str, aktion: str, ziel: Mapping[str, str]) -> None` – atomar einmalig; `ValueError("Bestätigungscode ungültig, abgelaufen oder für eine andere Aktion")` sonst.
  - Kontext-Hilfe: `kontext_mit_event(conn, info, marke: str | None) -> Kontext` – Art `event`: Marke wird ignoriert (Event fest); andere Arten: Marke Pflicht → `Kontext` mit `event_id` gesetzt (damit greift `event_bindung_pruefen` der Fachschicht).

- [ ] **Step 1: Tests** – `test_marke_gilt_nur_fuer_ausstellenden_schluessel`, `test_marke_laeuft_nach_30_min_ab` (Zeit über injizierbares `jetzt` oder direktes Admin-Update von `ablauf`), `test_marke_fuer_fremde_firma_nicht_ausstellbar` (Leitung A, Event B → NichtGefunden), `test_marke_legacy_und_archiviert_abgelehnt`, `test_bestaetigung_einmalig`, `test_bestaetigung_falsche_aktion_oder_ziel` (Review Focus 3), `test_bestaetigung_abgelaufen`, `test_kontext_mit_event_setzt_event_bindung` (mit Marke von Event X ist ein Baustein-Zugriff auf Event Y → NichtGefunden).
- [ ] **Step 2–4:** rot → implementieren → grün.
- [ ] **Step 5: Commit** `feat(event-management): Event-Wahl per Marke und zweistufige Bestaetigung`

---

### Task 4: Werkzeugkatalog und MCP-Server (Baustein-Werkzeuge)

**Files:**
- Create: `system/werkzeuge.py`, `system/mcp_server.py`
- Test: `tests/system/test_werkzeuge.py`, `tests/system/test_mcp_server.py`

**Interfaces:**
- Consumes: Task 1–3, `kern`, `events`, `kategorien.wirksames_schema`.
- Produces (`werkzeuge.py`, MCP-frei):
  - `@dataclass(frozen=True) Werkzeug(name: str, beschreibung: str, eingabe: dict  # JSON-Schema, schreibend: bool)`
  - `katalog(conn, info: SchluesselInfo) -> list[Werkzeug]`:
    - `event`: für jeden REGISTRY-Eintrag, dessen Baustein im Schema des gebundenen Events ist: `<eintrag>_liste`, `<eintrag>_anlegen`, `<eintrag>_aendern`, `<eintrag>_loeschen`; plus `event_info` (Eckdaten + Schema-Begriffe) und `verlauf_lesen`; **ohne** `marke`-Argument. Beschreibungen nutzen `begriffe` des Events (z. B. `ablauf_liste` beim Festival: „Timetable des Events anzeigen“).
    - `leitung`/`betreiber`: dieselben Baustein-Werkzeuge über die Vereinigung aller Bausteine (nicht schema-gefiltert, da das Event erst per Marke feststeht), jedes mit Pflichtargument `marke`; plus `events_suchen`, `event_waehlen` und die Verwaltungswerkzeuge aus Task 5.
    - `betreiber_lesen`: nur `*_liste`, `event_info`, `verlauf_lesen`, `events_suchen`, `event_waehlen`.
    - `melden`: nur `gruppe_melden`, `chat_melden` (Task 7).
  - JSON-Schema-Abbildung der Feldtypen: `text`→string (maxLength 2000), `ganzzahl`→integer (minimum 0), `datumzeit`→string `format: date-time` (naive = Berlin), `auswahl:a,b`→`enum`, `url`→string `pattern ^https?://`, `kontakt`/`ref:*`→string `format: uuid`. `_anlegen`: Pflichtfelder `required`; `_aendern`: `zeile` Pflicht + Felder optional; `_loeschen`: `zeile`. `additionalProperties: false` überall.
  - `ausfuehren(conn, info: SchluesselInfo, name: str, argumente: Mapping[str, object]) -> dict` – prüft, dass `name` im `katalog(info)` ist (sonst `NichtGefunden("unbekanntes Werkzeug")`), validiert Argumente gegen das Schema (unbekannte Schlüssel → ValueError), baut den Kontext über `kontext_mit_event`, ruft die Fachschicht. Baustein, den das Event nicht hat → `ValueError("Dieses Event hat keinen Bereich <Begriff>")`. Legacy → `ValueError("Legacy-Event: bitte die alte App nutzen")`. Schreibende Ergebnisse: `{"event": {"name", "firma"}, "zeile": …}`.
- Produces (`mcp_server.py`):
  - `erstelle_mcp_app(dsn: str, *, telegram=None, …) -> Starlette` – Low-Level-`mcp.server.lowlevel.Server` mit `list_tools`/`call_tool`, die **bei jedem Aufruf** den Bearer-Schlüssel aus `server.request_context.request.headers["authorization"]` lesen und `schluessel.pruefen` aufrufen (kein Kontext in globalen/contextvar-Zuständen, die über Anfragen leben). `StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)`, gemountet auf `/mcp`. Davor eine ASGI-Prüfung: fehlender/ungültiger Bearer → HTTP 401 ohne MCP-Verarbeitung. Route `GET /gesund` → `{"ok": true}` ohne Schlüssel. `TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[…, "host.docker.internal:*", "127.0.0.1:*", "localhost:*", "event-mcp:*"])`.
  - DB-Arbeit über `anyio.to_thread.run_sync` mit einem `psycopg_pool.ConnectionPool` (wie `event-api`).
  - CLI `python -m spaces.event_management.system.mcp_server` – DSN aus `EVENT_APP_DSN_FILE`/`EVENT_APP_DSN`, Port `EVENT_MCP_PORT` (Default 8171), Host `EVENT_MCP_HOST` (Default 127.0.0.1).

- [ ] **Step 1: Tests (`test_werkzeuge.py`, Fachschicht direkt)** – `test_event_schluessel_sieht_nur_eigene_bausteine` (team_meeting-Event: kein `budget_*`, aber `protokoll_*`; kein `marke`-Argument), `test_begriffe_in_beschreibung` (festival: „Timetable“), `test_leitung_braucht_marke` (ohne Marke → ValueError; mit Marke → Zeile im richtigen Event), `test_betreiber_lesen_nur_lesend` (kein `_anlegen` im Katalog; Aufruf von `aufgaben_anlegen` → NichtGefunden), `test_unbekannte_argumente_abgelehnt`, `test_event_id_argument_gibt_es_nicht` (Schema enthält nirgends `event_id`/`firma_id`), `test_legacy_abgelehnt`, `test_schreibantwort_nennt_event`, `test_verlauf_quelle_bot` (Verlaufseintrag hat `quelle="bot"`, `actor="bot:event"`).
- [ ] **Step 2: Tests (`test_mcp_server.py`, echter HTTP-Weg)** – mit `httpx`/Starlette-`TestClient` gegen `erstelle_mcp_app`: `test_ohne_schluessel_401`, `test_falscher_schluessel_401`, `test_tools_list_je_schluessel` (über MCP-Client `mcp.client.streamable_http.streamablehttp_client` mit Header, oder rohe JSON-RPC-POSTs `initialize`/`tools/list`/`tools/call`), `test_tool_call_schreibt_und_nennt_event`, `test_parallel_zwei_schluessel_getrennt` (Review Focus 1: zwei Threads/Tasks, je 20 abwechselnde `tools/list`+`tools/call`; jede Antwort passt zu ihrem Schlüssel), `test_gesund_ohne_schluessel`.
- [ ] **Step 3: implementieren. Step 4: grün** + ganzer Ordner.
- [ ] **Step 5: Commit** `feat(event-management): event-mcp mit Werkzeugen aus Bausteinen und Schluesselkontext`

---

### Task 5: Event-Wahl und Verwaltungswerkzeuge

**Files:**
- Modify: `system/werkzeuge.py`
- Test: `tests/system/test_werkzeuge_verwaltung.py`

**Interfaces:**
- Produces (nur für `leitung`/`betreiber`, Lesen auch `betreiber_lesen`):
  - `events_suchen(suche?: string, kategorie?: string)` → Liste `{name, firma, kategorie, beginn, ende, phase, status}` (max 20) – ohne IDs.
  - `event_waehlen(suche: string)` → genau ein Treffer: `{"marke", "event": {name, firma, beginn}}`; mehrere: `{"treffer": [{name, firma, beginn}], "hinweis": "Bitte genauer"}` ohne Marke; keiner: NichtGefunden.
  - `event_anlegen(firma: string /* Kürzel */, kategorie, name, beginn, ende, ort_adresse?, ort_link?, beschreibung?)` → legt an, liefert Marke des neuen Events.
  - `einladen(firma: string, mail, name, rolle)` – nutzt `zugang.einladen` mit dem Mailer der App.
  - Zweistufig (Review Focus 3; erster Aufruf ohne `bestaetigung` → `{"zusammenfassung": "… <Event-Name> (<Firma>) …", "bestaetigung": "bs_…", "gueltig_minuten": 5}` und ändert nichts; zweiter Aufruf mit `bestaetigung` → führt aus): `event_absagen(marke)`, `event_archivieren(marke)`, `event_loeschen(marke)`, `kontakt_entfernen(marke, kontakt)` (Kontakt per Name-Suche innerhalb der Firma des Events, eindeutig sonst Trefferliste), `rolle_aendern(firma, mail, rolle)`.
  - `firma_anlegen(name, kurz)` – nur `betreiber`.
  - (`gruppe_verbinden` kommt in Task 7, zusammen mit `verbinden.py`.)
  - Rechte: alle Verwaltungsaktionen laufen über `rechte.pruefe` mit dem Kontext aus `kontext_fuer`/`kontext_mit_event`; eine Leitung von A kann nichts in B (NichtGefunden).
- [ ] **Step 1: Tests** – `test_waehlen_eindeutig_mehrdeutig_keiner`, `test_waehlen_nur_eigene_firmen`, `test_events_suchen_ohne_ids`, `test_anlegen_liefert_marke`, `test_zweistufig_aendert_im_ersten_schritt_nichts` (parametrisiert über alle 5 riskanten Aktionen; Admin-Nachlesen), `test_bestaetigung_fuer_anderes_event_abgelehnt`, `test_bestaetigung_nur_einmal`, `test_firma_anlegen_nur_betreiber`, `test_betreiber_lesen_hat_keine_verwaltung`, `test_marke_archiviertes_event_nach_wahl` (Event nach Wahl archiviert → Baustein-Schreiben abgelehnt).
- [ ] **Step 2–4:** rot → implementieren → grün.
- [ ] **Step 5: Commit** `feat(event-management): Event-Wahl und zweistufige Verwaltung im event-mcp`

---

### Task 6: Nachrichten – Gruppe per Telegram, Mail-Entwürfe mit Freigabe

**Files:**
- Create: `system/telegram.py`, `system/nachrichten.py`
- Modify: `system/werkzeuge.py`, `system/api.py`, `web/` (Reiter `nachrichten` ersetzt den Platzhalter aus Etappe 2 Task 7)
- Test: `tests/system/test_nachrichten.py`, `tests/system/test_telegram.py`, `web/tests/nachrichten.test.tsx`

**Interfaces:**
- Produces (`telegram.py`): `class Telegram(Protocol): senden(chat_id: int, text: str) -> None; chat_info(chat_id: int) -> dict  # {titel, art, bot_ist_mitglied: bool}`; `class TelegramHttp` (urllib, Token aus Datei `TELEGRAM_BOT_TOKEN_FILE`, Timeout 10 s, Fehler ohne Token im Text – maskiert `bot\d+:[\w-]+`); `class SpeicherTelegram` (Tests).
- Produces (`nachrichten.py`):
  - `an_gruppe(conn, k, event_id, text, *, telegram: Telegram) -> dict` – Recht `baustein_schreiben`; Event muss eine bestätigte Gruppe haben (aus `verbindung` mit `status='bestaetigt'`, `art='gruppe'`), sonst `ValueError("Keine Telegram-Gruppe verbunden")`; speichert `nachricht(kanal='gruppe', status='versendet'|'fehler')`.
  - `mail_entwerfen(conn, k, event_id, betreff, text, zusage: list[str]) -> dict` – speichert `status='entwurf'`, liefert Anzahl Empfänger (Teilnehmende mit Mail und passender Zusage). Der Bot verschickt nie selbst.
  - `freigeben(conn, k, nachricht_id) -> dict` – nur Leitung (`rechte`-Aktion `event_aendern` reicht nicht; neue Aktion `nachricht_freigeben` = Leitung), nur aus `entwurf`, setzt `freigegeben`.
  - `verwerfen(conn, k, nachricht_id)`.
  - `versenden(conn_admin, versand: Versandweg) -> int` – verarbeitet freigegebene Mails (je Empfänger `versand.senden(an, betreff, text)`), Status `versendet`/`fehler`; aufrufbar per CLI `python -m spaces.event_management.system.nachrichten versenden`.
  - `Versandweg` = `mailer.Mailer`-Protokoll. **Ruling dieser Etappe:** Standard-Versandweg ist der bestehende Betreiber-Mailer (`SmtpMailer.aus_umgebung`, derselbe Weg wie die Einladungen); die Anbindung an den sales-claw-Versandweg ist ein austauschbarer Adapter und wird in Etappe 4 mit Felix entschieden (sales-claw kennt heute keinen generischen Auftrags-Eingang für diesen Space).
  - Werkzeuge: `nachricht_an_gruppe(text[, marke])` (Art `event`: an die eigene Gruppe), `mail_entwerfen(betreff, text, zusage[, marke])`, `nachrichten_liste([marke])`.
  - API: `GET /api/events/{id}/nachrichten`, `POST /api/events/{id}/nachrichten/gruppe {text}`, `POST /api/events/{id}/nachrichten/mail {betreff, text, zusage}`, `POST /api/nachrichten/{id}/freigeben`, `POST /api/nachrichten/{id}/verwerfen` (CSRF; Mandantenmatrix).
  - Web: Reiter „Nachrichten“: Liste (Kanal, Betreff/Anfang, Status, Empfängerzahl), „An Gruppe schreiben“, „Mail entwerfen“, bei Entwürfen „Freigeben“ (nur Leitung, mit `Bestaetigen`, zeigt Empfängerzahl) und „Verwerfen“.
- [ ] **Step 1: Tests** – Python: `test_an_gruppe_ohne_verbindung_abgelehnt`, `test_an_gruppe_sendet_an_bestaetigte_gruppe` (SpeicherTelegram), `test_entwurf_versendet_nichts`, `test_freigeben_nur_leitung`, `test_versenden_nur_freigegebene_und_nur_einmal`, `test_empfaenger_nach_zusage_und_mail`, `test_telegram_fehlertext_maskiert_token`, Mandantenmatrix der neuen Routen. Web: Liste, Freigeben nur bei `darfVerwalten`, Entwurf-Formular sendet `zusage`.
- [ ] **Step 2–4:** rot → implementieren → grün (Python + `npm test` + `npm run typen` + `npm run build`).
- [ ] **Step 5: Commit** `feat(event-management): Nachrichten an Gruppe und Mail-Entwuerfe mit Freigabe`

---

### Task 7: Gruppe und Direktchat verbinden

**Files:**
- Create: `system/verbinden.py`
- Modify: `system/werkzeuge.py` (`gruppe_melden`, `chat_melden`, `gruppe_verbinden`), `system/api.py`, `web/` (Event-Übersicht: Abschnitt „Telegram-Gruppe“; Kopfleiste/Menü: „Meinen Telegram-Chat verbinden“; Anlegen Schritt 3 verweist darauf)
- Test: `tests/system/test_verbinden.py`, `web/tests/verbinden.test.tsx`

**Interfaces:**
- Produces (`verbinden.py`):
  - `code_erzeugen(conn, k, *, art: Literal["gruppe","direkt"], event_id: UUID | None) -> str` – `gruppe`: neue Rechte-Aktion `gruppe_verbinden` (nur Leitung); `direkt`: nur wenn die Person Leitung in mindestens einer Firma oder Betreiber ist. Code: 8 Zeichen aus `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, 15 min gültig, Hash gespeichert; alte offene Codes derselben (art, event/person) werden verworfen.
  - `melden(conn_admin, *, code: str, chat_id: int, art: str, telegram: Telegram) -> str` – sucht den offenen Code; prüft `art` passt (`gruppe` nur mit negativer Chat-ID, `direkt` nur positiv); `telegram.chat_info(chat_id)` → bei Gruppe muss `bot_ist_mitglied` wahr sein; setzt `chat_id`, `chat_titel`, `status='gemeldet'`. Antwort an den Bot nur „Danke, bitte in der Oberfläche bestätigen“ bzw. einheitlich „Code ungültig oder abgelaufen“. Sperre: 5 Fehlmeldungen je `chat_id` in 15 min → weitere Meldungen dieses Chats 15 min abgelehnt (Review Focus 4).
  - `offene(conn, k, …)` → gemeldete Verbindungen für Event bzw. Person mit `chat_titel` (für die Bestätigung in der Oberfläche).
  - `bestaetigen(conn, k, verbindung_id) -> UUID` – setzt `bestaetigt`, legt **einen** `bereitstellung`-Auftrag an (`gruppe` → agent `ev-<erste 8 hex der event_id>`; `direkt` → `ld-<erste 8 hex der person_id>` bzw. `bo-<…>` für Betreiber), widerruft eine evtl. alte bestätigte Verbindung desselben Ziels (Auftrag `abbauen` für die alte).
  - `verwerfen(conn, k, verbindung_id)`.
  - Werkzeuge (Art `melden`): `gruppe_melden(code, chat_id)`, `chat_melden(code, chat_id)` – Die Chat-ID kommt hier als Argument vom Empfangs-Agenten (das ist der Messpunkt der Spec, Task 9); die Sicherheit hängt nicht an ihr, sondern an Code + `getChat` + Bestätigung in der Oberfläche.
  - Werkzeug (Leitung): `gruppe_verbinden(marke)` → Code + Anleitung.
  - API: `POST /api/events/{id}/verbinden` → `{code, gueltig_bis}`; `GET /api/events/{id}/verbinden` → offene/gemeldete/bestätigte; `POST /api/ich/verbinden` (Direktchat) / `GET /api/ich/verbinden`; `POST /api/verbindungen/{id}/bestaetigen`, `POST /api/verbindungen/{id}/verwerfen`.
  - Web: Event-Übersicht „Telegram-Gruppe“ (Status, „Code erzeugen“ zeigt Code + 3 Schritte, gemeldete Gruppe mit Titel → „Bestätigen“/„Verwerfen“, nur `darfVerwalten`); Seite `/ich/telegram` „Meinen Telegram-Chat verbinden“ (nur für Leitung/Betreiber sichtbar). Polling alle 5 s, solange ein Code offen ist.
- [ ] **Step 1: Tests** – `test_code_einmalig_und_15_min`, `test_code_nur_leitung`, `test_melden_falsche_art_oder_falsches_vorzeichen`, `test_melden_bot_nicht_in_gruppe_abgelehnt`, `test_sperre_nach_5_fehlmeldungen`, `test_ohne_bestaetigung_kein_auftrag`, `test_bestaetigen_legt_genau_einen_auftrag_an`, `test_fremde_firma_kann_nicht_bestaetigen`, `test_neue_gruppe_baut_alte_ab`, `test_melden_werkzeug_nur_mit_melden_schluessel`; Web: Code anzeigen, Bestätigen-Knopf nur für Leitung, Polling stoppt nach Bestätigung (Fake-Timer).
- [ ] **Step 2–4:** rot → implementieren → grün (Python + Web).
- [ ] **Step 5: Commit** `feat(event-management): Telegram-Gruppe und Direktchat per Einmal-Code verbinden`

---

### Task 8: Bereitstellungs-Arbeiter, SOUL-Vorlagen, Empfangs-Agent-Konfiguration

**Files:**
- Create: `system/bereitstellung.py`, `system/soul.py`, `deploy/bereitstellung.service`, `deploy/bereitstellung.timer`
- Modify: `config/openclaw.json`, `deploy/agent_budget.yaml`, `config/workspace/SOUL.md` (Empfang), `deploy/anbinden.sh` falls der melden-Schlüssel für `main` dort eingetragen wird
- Test: `tests/system/test_bereitstellung.py`, `tests/system/test_soul.py`, `tests/test_config.py` (bestehende Config-Tests anpassen)

**Interfaces:**
- Produces (`bereitstellung.py`):
  - `class Ausfuehrer(Protocol)`: `config_lesen() -> dict` (ganze openclaw.json), `config_schreiben(pfad: str, wert: object) -> None` (Wert über stdin/Datei, **nie** als argv – Schlüssel!), `config_entfernen(pfad: str) -> None`, `datei_schreiben(pfad_im_container: str, inhalt: str) -> None`.
  - `class DockerAusfuehrer(container: str = "event-management-claw")` – `docker exec -i <container> node -e <skript>` mit JSON über stdin (wie `anbinden.sh`), kein `openclaw config set` mit Werten in argv; `subprocess.run` ohne geerbte Pipes (stdout/stderr in Datei oder `DEVNULL`, Timeout 60 s).
  - `class SpeicherAusfuehrer` – In-Memory-Config für Tests, mit Fehlerinjektion je Schritt.
  - `schritte_anlegen(auftrag, *, schluessel_klartext, mcp_url, schema, event) -> list[Schritt]` – für `gruppe`: (1) Arbeitsbereich `/home/node/.openclaw/workspace-<agent>` mit `SOUL.md` aus `soul.gruppe(...)`, (2) `mcp.servers.<agent>` = `{url: mcp_url, transport: streamable-http, headers: {Authorization: "Bearer <schlüssel>"}}`, (3) Agent in `agents.list` `{id, workspace, tools: {allow: ["<agent>__*"]}}`, (4) `bindings`-Eintrag `{agentId, match: {channel: "telegram", peer: {kind: "group", id: "<chat_id>"}}}` + `channels.telegram.groups.<chat_id>.requireMention = false`. Für `leitung`/`betreiber`: dasselbe mit `peer.kind: "direct"`, `session.dmScope: "per-peer"`, SOUL `soul.leitung(...)`.
  - Jeder Schritt ist **idempotent** (Ist-Zustand lesen, nur setzen, wenn abweichend) und hat einen Namen; der Auftrag speichert den zuletzt erreichten `schritt`.
  - `abbauen`: entfernt Binding, Agent, Server-Eintrag, Gruppen-Eintrag, widerruft den Schlüssel (Arbeitsbereich bleibt, wird umbenannt `…-abgebaut-<datum>` – nichts löschen).
  - `lauf(conn_admin, ausfuehrer, *, mcp_url, jetzt=None) -> list[UUID]` – holt offene Aufträge (`for update skip locked`), setzt `laeuft`; hängende `laeuft` > 10 min → `fehler`; stellt beim Anlegen einen Schlüssel aus (`event` bzw. `leitung`/`betreiber`), bricht bei Fehler mit `fehler` + Schrittname ab und widerruft den gerade ausgestellten Schlüssel; Erfolg → `erfolg`.
  - Archivieren/Löschen eines Events (Fachschicht `events.event_status(... 'archiviert')` / `event_loeschen`) legt automatisch einen `abbauen`-Auftrag an, wenn es eine bestätigte Gruppe gibt (Spec §4 „Beim Archivieren … wieder entfernt“).
  - CLI `python -m spaces.event_management.system.bereitstellung lauf` (Admin-DSN, `EVENT_MCP_URL`, `EVENT_CLAW_CONTAINER`); `deploy/bereitstellung.service`/`.timer` (alle 20 s, wie sales `admin_auftraege`) – **nur Dateien**, nicht installiert.
- Produces (`soul.py`): `gruppe(event: Mapping, schema: WirksamesSchema, firma: str) -> str` und `leitung(name: str, betreiber: bool) -> str` – Deutsch, kurz; nennt Event, Datum (Berlin), Ort, Begriffe, verfügbare Bereiche; Regeln: nur die eigenen Werkzeuge, riskante Aktionen nur mit Rückfrage + Bestätigungscode, Mails nur als Entwurf, keine Daten anderer Events. Keine Nutzereingaben ungefiltert in Markdown-Überschriften (Zeilenumbrüche aus Namen entfernen).
- Config: `config/openclaw.json` → `main` bekommt `tools.allow: ["melden__gruppe_melden", "melden__chat_melden"]`, Server `mcp.servers.melden` (`{{EVENT_MCP_URL}}/mcp`, Header-Platzhalter, Wert setzt `anbinden.sh` über stdin); `channels.telegram.groupPolicy` bleibt `allowlist` mit `groups."*".requireMention: true` **nur wenn** der OpenClaw-Schema-Check (`tests/test_config.py` validiert gegen die bestehende Prüfung) das zulässt, sonst Ruling im Report; Tagesgrenze in `agent_budget.yaml` `300`. Warnung bei 80 %: der Umsetzer prüft im Shim-Code (`vibemind-os`, Konsument von `VIBEMIND_BUDGET_CONFIG`), ob eine Warnschwelle konfigurierbar ist; wenn ja, `0.8` setzen; wenn nein, **nicht** selbst bauen, sondern im Report als `DONE_WITH_CONCERNS` melden.
- [ ] **Step 1: Tests** – `test_anlegen_gruppe_erzeugt_vier_teile` (SpeicherAusfuehrer: Agent, Server mit Bearer, Binding group, requireMention false; `tools.allow == ["ev-xxxxxxxx__*"]`), `test_leitung_direktchat_dmscope`, `test_idempotent_zweiter_lauf_aendert_nichts`, `test_fehler_mitten_drin_endet_fehler_und_widerruft_schluessel` (Review Focus 5), `test_abbauen_entfernt_auch_teilstaende`, `test_haengender_auftrag_wird_fehler`, `test_archivieren_legt_abbau_an`, `test_schluessel_nie_in_argv` (DockerAusfuehrer mit gepatchtem `subprocess.run`: kein argv-Element enthält `evk_`), `test_soul_nennt_event_und_begriffe`, `test_soul_entfernt_zeilenumbrueche_aus_namen`, Config-Test: `main` hat genau die zwei melden-Werkzeuge, Budget 300, keine Klartext-Schlüssel in `config/`.
- [ ] **Step 2–4:** rot → implementieren → grün + ganzer Ordner.
- [ ] **Step 5: Commit** `feat(event-management): Bereitstellungs-Arbeiter, SOUL-Vorlagen und Empfangs-Agent`

---

### Task 9: Ende-zu-Ende-Probe und Messpunkte vorbereiten

**Files:**
- Create: `tests/system/test_bot_e2e.py`, `deploy/messen.md`, `deploy/messen.sh`
- Modify: `docker-compose.yml` (Dienste `event-mcp` und – falls noch nicht da – `event-api`/`event-web` als Definition, festes Subnetz, **nicht** gestartet), `README`/Space-Doku nur, wenn dort die Dienste aufgezählt sind

**Interfaces:**
- `test_bot_e2e.py` (läuft in der normalen Suite, Wegwerf-PG, `SpeicherTelegram`, `SpeicherAusfuehrer`, MCP über HTTP-`TestClient`): Leitung legt Event an (API) → erzeugt Gruppen-Code (API) → Empfangs-Agent ruft `gruppe_melden` mit `melden`-Schlüssel (MCP) → Leitung bestätigt (API) → `bereitstellung.lauf` → aus der SpeicherAusfuehrer-Config den Bearer des neuen `ev-…`-Servers lesen → mit diesem Schlüssel `tools/list` (nur Bausteine des Events, keine Verwaltung) → `aufgaben_anlegen` → Verlauf zeigt `quelle=bot` → Event archivieren → `lauf` → Agent/Server/Binding weg, alter Schlüssel → 401. Zweiter Test: Leitungs-Direktchat → `ld-…`-Schlüssel → `event_waehlen` → zweistufiges `event_absagen`.
- `deploy/messen.md` + `deploy/messen.sh`: Anleitung und Skript für die drei Messpunkte der Spec (Chat-ID im Kontext des Empfangs-Agenten; `tools.allow` mit `<server>__*` filtert zur Laufzeit; `bindings`/`mcp.servers` laden ohne Neustart) gegen einen **Test-Container** auf `offload-vm`, mit Abbruchkriterien und dem Hinweis, dass Etappe 4 bei einem Fehlschlag Alternativen vorlegt. Das Skript hat einen Pflicht-Schalter `--ich-habe-ok` und tut ohne ihn nichts. **Nicht ausführen.**
- `docker-compose.yml`: `event-mcp` (Python-Image des Space, Kommando CLI aus Task 4, Port nur intern + `127.0.0.1`, Netz `event-agent` mit fester Adresse im bestehenden `/28`), Healthcheck `GET /gesund`. Compose-Validierung im bestehenden Config-Test (`docker compose config` wird **nicht** gegen die VM ausgeführt; nur YAML-Struktur-Test).
- [ ] **Step 1: Tests schreiben. Step 2: ausführen**, gefundene Fehler mit eigenen `fix:`-Commits beheben. **Step 3: grün** + ganzer Python-Ordner + `npm test`.
- [ ] **Step 4: Commit** `test(event-management): Ende-zu-Ende-Probe Bot-Kette und Messanleitung`

---

## Nach der Etappe

Fertig, wenn die Python-Suite (inkl. E2E), `npm test`, `npm run typen`, `npm run build` grün sind. Live-Messung, Ausrollen, Legacy-Umbenennung und Brain-Umstellung sind Etappe 4 und brauchen Felix' OK.
