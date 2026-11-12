# Event-Management-System, Etappe 1: Fundament – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kategorien mit Vererbung, Datenbankschema `event` mit doppelter Mandantentrennung (Anwendung und RLS), Fachschicht für Firmen, Personen, Events und die Kern-Bausteine sowie eine `event-api` mit Login per Einladung. Alles getestet gegen eine echte, wegwerfbare Postgres.

**Architecture:**
- **Kategorien** sind YAML-Dateien. Ein Loader führt die Elternkette zum wirksamen Schema zusammen und prüft sie.
- **Datenbankzugriffe** laufen über eine Transaktion mit Aufrufkontext. Der Kontext setzt `event.firmen`, `event.betreiber`, `event.person` und `event.event` per `set_config`. RLS-Policies erlauben nur passende Zeilen, und die Fachschicht prüft Rollen zusätzlich.
- **Die FastAPI-App** ist nur eine dünne Hülle über der Fachschicht.

**Tech Stack:** Python 3.11, FastAPI, psycopg 3 (sync, `dict_row`), PyYAML, pydantic 2, PostgreSQL 16, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-event-management-system-design.md` (Abschnitte 1–3 sind Gegenstand dieser Etappe). Nicht hier: Oberfläche (Etappe 2), `event-mcp`, Zusatz-Bausteine, Bot, Baustein `nachrichten` (Etappe 3), Brain, Legacy und Ausrollen (Etappe 4).

## Global Constraints

- vibemind-os ist ÖFFENTLICH: keine echten Firmen, Personen, Mailadressen, Telegram-IDs, Adressen, Schlüssel oder Passwörter. Das gilt auch für Tests, YAML und Commit-Texte. Beispiele sind erfunden (Firma „Beispiel GmbH“, Mail `@example.org`).
- Code liegt unter `spaces/event_management/`. Imports laufen als `from spaces.event_management.system import …`, mit dem Repo-Root auf `PYTHONPATH`.
- Python 3.11-kompatibel. Neue Laufzeit-Abhängigkeiten sind nur `fastapi`, `psycopg[binary]>=3.2`, `pyyaml`, `pydantic>=2`. Passwörter werden mit `hashlib.scrypt` aus der Standardbibliothek gehasht, keine weitere Krypto-Bibliothek.
- Datenbank: Schema `event`, Laufzeitrolle `event_app` (NOLOGIN, ohne DDL). Migrationen sind nummerierte SQL-Dateien `spaces/event_management/db/NNN_<name>.sql` und werden nur als Admin angewendet.
- Mandantentrennung zweifach, beide Wege getestet: in der Fachschicht und per RLS. Fehlt der Kontext, gibt es KEINE Zeilen (fail-closed).
- Geheimnisse, Einladungs-Tokens, Passwörter und Sitzungs-Cookies stehen nie in Logs, Fehlermeldungen oder Exceptions. Gespeichert wird nur der sha256-Hash eines Tokens.
- Logging nur über `logging.getLogger(__name__)`, kein `print`.
- pytest immer mit `--basetemp` unter `C:\Users\User\AppData\Local\Temp\…\scratchpad\…` (Parent anlegen), PostgreSQL-Tests nur gegen den Wegwerf-Cluster aus Task 2, nie gegen Supabase.
- Conventional Commits, Commit-Texte enden mit `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Zeiten: Events werden in Europe/Berlin eingegeben und als `timestamptz` gespeichert. Ein Event, das heute 23:30 Berliner Zeit endet, ist um 23:00 noch „läuft“ und nicht „vergangen“. Test in Task 5: `test_phase_beachtet_berliner_zeit`.
2. Kontext fehlt oder ist leer: Eine Abfrage als `event_app` ohne `set_config` liefert null Zeilen und schreibt nichts. Test in Task 3: `test_ohne_kontext_keine_zeilen`.
3. Kaputte Kategorie-YAML (Zyklus, unbekannter Elternteil, Typwechsel eines geerbten Feldes, unbekannter Baustein, `verlauf` abgewählt) bricht den Start mit einer klaren Meldung ab. Tests in Task 1.
4. Mailadresse in anderer Groß-/Kleinschreibung oder ein zweites Einladen derselben Person ergibt dieselbe Person mit einer weiteren Mitgliedschaft, kein Duplikat. Test in Task 7: `test_einladung_gleiche_mail_andere_schreibweise`.
5. Ein Kontakt aus Firma A wird an ein Event von Firma B gehängt: Die Datenbank lehnt das ab, auch für den Betreiber. Test in Task 3: `test_kontakt_fremder_firma_nicht_verknuepfbar`.

---

## Dateistruktur

```text
spaces/event_management/
  kategorien/                 # YAML, eine Datei je Kategorie (Task 1)
    basis.yaml veranstaltung.yaml fest.yaml festival.yaml weihnachtsfeier.yaml
    messe.yaml online_event.yaml webinar.yaml hackathon.yaml team_meeting.yaml
  db/
    __init__.py
    anwenden.py               # Migrationslauf (Task 2)
    001_schema.sql            # Schema, Rolle, Tabellen (Task 2)
    002_rls.sql               # Funktionen + Policies + Grants (Task 3)
  system/
    __init__.py
    kategorien.py             # Loader + wirksames Schema (Task 1)
    bausteine.py              # Namen aller Bausteine, Kern-Registry (Task 1, Task 6)
    kontext.py                # Kontext + transaktion() (Task 4)
    rechte.py                 # Rollenprüfung (Task 4)
    firmen.py                 # Firmen, Personen, Mitgliedschaften (Task 4)
    events.py                 # Events, Phase, Kennzahlen, Metadaten-Prüfung (Task 5)
    verlauf.py                # Änderungsprotokoll (Task 5)
    kern.py                   # Kern-Bausteine CRUD (Task 6)
    zugang.py                 # Einladung, Passwort, Login, Cookie, CSRF (Task 7)
    mailer.py                 # Mailer-Protokoll + SMTP + Testattrappe (Task 7)
    api.py                    # FastAPI-App (Task 8)
  tests/system/
    conftest.py               # Wegwerf-Postgres (Task 2)
    test_kategorien.py test_migration.py test_rls.py test_firmen.py
    test_events.py test_kern.py test_zugang.py test_api.py
```

---

### Task 1: Kategorien mit Vererbung

**Files:**
- Create: `spaces/event_management/system/__init__.py` (leer), `system/bausteine.py`, `system/kategorien.py`, `kategorien/*.yaml` (10 Dateien)
- Test: `spaces/event_management/tests/system/__init__.py` (leer), `tests/system/test_kategorien.py`

**Interfaces:**
- Produces:
  - `bausteine.KERN: tuple[str, ...]` = `("aufgaben", "orga_team", "teilnehmende", "ablauf", "budget", "nachrichten", "verlauf")`
  - `bausteine.ZUSAETZE: tuple[str, ...]` = `("wettbewerb_teams", "abgaben", "jury", "aussteller", "staende", "sprecher", "online_raum", "aufzeichnung", "protokoll", "beschluesse", "verpflegung", "programm", "helferschichten", "buehnen")`
  - `bausteine.PFLICHT = frozenset({"verlauf"})`
  - `FELDTYPEN = frozenset({"text", "langtext", "zahl", "ja_nein", "datum", "datumzeit", "auswahl", "url"})`
  - `@dataclass(frozen=True) Feld(id: str, typ: str, beschriftung: str, pflicht: bool = False, vorgabe: object = None, optionen: tuple[str, ...] = ())`
  - `@dataclass(frozen=True) Kategorie(id, name, eltern: str | None, abstrakt: bool, bausteine: tuple[str, ...], ohne: tuple[str, ...], felder: tuple[Feld, ...], begriffe: Mapping[str, str], eckdaten_pflicht: tuple[str, ...])`
  - `@dataclass(frozen=True) WirksamesSchema(kategorie: str, name: str, kette: tuple[str, ...], bausteine: tuple[str, ...], felder: tuple[Feld, ...], begriffe: Mapping[str, str], eckdaten_pflicht: frozenset[str])`
  - `class KategorieFehler(ValueError)`
  - `lade_kategorien(ordner: Path | None = None) -> dict[str, Kategorie]`. Die Vorgabe ist `spaces/event_management/kategorien`. Der Loader prüft alle Kategorien sofort und baut für jede das wirksame Schema einmal.
  - `wirksames_schema(kategorien: Mapping[str, Kategorie], kategorie_id: str) -> WirksamesSchema`
  - `waehlbare(kategorien) -> list[Kategorie]` liefert alle nicht abstrakten Kategorien, nach `name` sortiert.

**YAML-Format** (alle Schlüssel außer `id`, `name` und `eltern` sind optional):

```yaml
id: hackathon          # muss dem Dateinamen entsprechen
name: Hackathon
eltern: basis          # nur basis hat null
abstrakt: false
bausteine: [wettbewerb_teams, abgaben, jury]   # zusätzlich zu den geerbten
ohne: []               # geerbte Bausteine abwählen
felder:
  - {id: thema, typ: text, beschriftung: Thema}
begriffe: {ablauf: Ablauf}
eckdaten_pflicht: []   # Teilmenge von {ort_adresse, ort_link, ende, beschreibung}
```

**Zusammenführen** (von `basis` zur Kategorie):
- Bausteine:
  - Geerbte Bausteine plus eigene, abzüglich `ohne`. Die Reihenfolge bleibt die der ersten Nennung.
  - `ohne` darf nur geerbte Bausteine nennen, `verlauf` nie.
- Felder:
  - Felder werden nach `id` zusammengeführt. Ein Kind darf `beschriftung`, `pflicht`, `vorgabe` und `optionen` überschreiben.
  - `typ` darf das Kind nicht ändern, sonst `KategorieFehler`.
  - `auswahl` braucht mindestens eine Option.
- Begriffe: werden überlagert (das Kind gewinnt).
- `eckdaten_pflicht`: Vereinigung über die Kette.
- Fehler (`KategorieFehler` mit Datei- und Kategorienamen):
  - unbekannter Elternteil
  - Zyklus
  - `id` passt nicht zum Dateinamen
  - unbekannter Baustein oder Feldtyp
  - doppelte Feld-`id` in einer Datei
  - `basis` mit Elternteil

**Inhalt der YAML-Dateien.** Exakte Bausteine; Felder und Begriffe sind ein Mindestbestand, der Umsetzer darf sinnvoll ergänzen:
- `basis`:
  - `abstrakt: true`, `bausteine: KERN`.
  - `begriffe`:
    - `aufgaben: Aufgaben`
    - `orga_team: Orga-Team`
    - `teilnehmende: Teilnehmende`
    - `ablauf: Ablauf`
    - `budget: Budget`
    - `nachrichten: Nachrichten`
    - `verlauf: Verlauf`
- `veranstaltung`: Elternteil `basis`, `+ programm, verpflegung`, Feld `kapazitaet` (zahl, „Kapazität“).
- `fest`: Elternteil `veranstaltung`, `+ helferschichten`.
- `festival`: Elternteil `fest`, `+ buehnen`, Begriff `ablauf: Timetable`.
- `weihnachtsfeier`: Elternteil `fest`, Felder `motto` (text) und `wichteln` (ja_nein, Vorgabe false).
- `messe`: Elternteil `veranstaltung`, `+ aussteller, staende`, Feld `halle` (text).
- `online_event`: Elternteil `basis`, `+ online_raum, sprecher, aufzeichnung`, `eckdaten_pflicht: [ort_link]`, Feld `plattform` (auswahl: Zoom, Teams, Google Meet, Jitsi, Andere).
- `webinar`: Elternteil `online_event`, Feld `anmeldung_noetig` (ja_nein, Vorgabe true).
- `hackathon`: Elternteil `basis`, `+ wettbewerb_teams, abgaben, jury`, Feld `thema` (text).
- `team_meeting`: Elternteil `basis`, `+ protokoll, beschluesse`, `ohne: [budget]`, Begriff `ablauf: Tagesordnung`.

- [ ] **Step 1: Tests schreiben** (`test_kategorien.py`). Mit dem echten Ordner:
  - `test_alle_kategorien_laden_und_sind_gueltig`: `set(lade_kategorien()) == {basis, veranstaltung, fest, festival, weihnachtsfeier, messe, online_event, webinar, hackathon, team_meeting}`.
  - `test_basis_ist_abstrakt_und_nicht_waehlbar`: `"basis" not in [k.id for k in waehlbare(k)]` und `len(waehlbare(k)) == 9`.
  - `test_festival_erbt_ueber_fest_und_veranstaltung`:
    - `s.kette == ("basis", "veranstaltung", "fest", "festival")`;
    - `{"programm", "verpflegung", "helferschichten", "buehnen"} <= set(s.bausteine)`;
    - `set(KERN) <= set(s.bausteine)`;
    - `s.begriffe["ablauf"] == "Timetable"`;
    - `"kapazitaet" in {f.id for f in s.felder}`.
  - `test_team_meeting_ohne_budget_mit_tagesordnung`: `"budget" not in s.bausteine`, `s.begriffe["ablauf"] == "Tagesordnung"`.
  - `test_webinar_verlangt_online_link`: `"ort_link" in s.eckdaten_pflicht`.
  - `test_jeder_baustein_ist_bekannt`: Jeder Baustein jedes wirksamen Schemas liegt in `KERN + ZUSAETZE`.

  Mit `tmp_path` und kaputten Dateien (Review Focus 3); jeweils `pytest.raises(KategorieFehler, match=…)`, und der Kategoriename steht in der Meldung:
  - `test_zyklus_wird_erkannt`
  - `test_unbekannter_elternteil`
  - `test_feldtyp_darf_nicht_wechseln`
  - `test_unbekannter_baustein`
  - `test_verlauf_nicht_abwaehlbar`
  - `test_ohne_nur_fuer_geerbte`
  - `test_id_muss_dateiname_sein`
- [ ] **Step 2: Ausführen, rot:** `python -m pytest spaces/event_management/tests/system/test_kategorien.py -q --basetemp=<scratch>/t1`. Erwartet ist ein ImportError.
- [ ] **Step 3: `bausteine.py`, `kategorien.py` und die 10 YAML-Dateien schreiben.** Laden mit `yaml.safe_load`. Felder, die in der YAML fehlen, bekommen die Vorgaben aus dem Format oben.
- [ ] **Step 4: Ausführen, grün**, einmal mit `-W error`.
- [ ] **Step 5: Commit** `feat(event-management): Kategorien mit Vererbung (alle Event-Arten)`

---

### Task 2: Wegwerf-Postgres, Migrationslauf, Tabellen

**Files:**
- Create: `db/__init__.py`, `db/anwenden.py`, `db/001_schema.sql`, `tests/system/conftest.py`
- Test: `tests/system/test_migration.py`

**Interfaces:**
- Produces:
  - `anwenden.migrationen(ordner: Path | None = None) -> list[tuple[int, str, Path]]` liefert die Dateien sortiert nach Nummer. Doppelte Nummern führen zu `ValueError`.
  - `anwenden.anwenden(conn: psycopg.Connection) -> list[str]` wendet fehlende Migrationen an, jede in einer eigenen Transaktion, und trägt sie in `event.migration(nr int primary key, name text, angewendet timestamptz default now())` ein. Rückgabe sind die Namen der neu angewendeten Dateien. Ist schon alles angewendet, ist die Liste leer.
  - CLI `python -m spaces.event_management.db.anwenden`: DSN aus `EVENT_ADMIN_DSN_FILE` oder `EVENT_ADMIN_DSN` (nur `strip()`). Ausgabe sind nur Dateinamen, nie der DSN.
  - Fixtures in `conftest.py`:
    - `pg_cluster` (Session-Scope): Wegwerf-Cluster, Rückgabe `dict(port=…, admin_dsn=…)`.
    - `db_admin` (Funktion): Verbindung als Superuser auf eine frische DB `event_test_<n>` mit angewendeten Migrationen.
    - `db_app(**kontext)`: Factory für Verbindungen als Login-Rolle `event_app_test`, Mitglied von `event_app`.

**Wegwerf-Cluster.** `initdb` und `pg_ctl` liegen in `EVENT_PG_BIN`, Vorgabe `C:\Program Files\PostgreSQL\16\bin`.
- Ablauf: `initdb -A trust -U postgres -E UTF8` nach `tmp_path_factory`, dann `pg_ctl start` auf einem freien Port mit `-o "-c listen_addresses=127.0.0.1 -p <port>"`. Am Session-Ende `pg_ctl stop -m fast`.
- Fehlen die Binaries, endet der Test mit `pytest.skip("PostgreSQL-Binaries fehlen: EVENT_PG_BIN")`. Er wird niemals stillschweigend grün.
- Der Superuser legt die Rolle `event_app_test LOGIN` an und gibt ihr `event_app` (`GRANT event_app TO event_app_test`), nachdem 001 die Rolle erzeugt hat.

**`001_schema.sql`** (idempotent, wo es geht):
- Rolle und Erweiterungen:
  - `do $$ … create role event_app nologin … $$` nur, wenn die Rolle fehlt;
  - `create schema if not exists event`;
  - `create extension if not exists pgcrypto` für `gen_random_uuid()`.
- Tabellen. Alle `id uuid primary key default gen_random_uuid()`, alle `timestamptz`, `angelegt timestamptz not null default now()`:
  - `firma(id, name text not null, kurz text not null unique check (kurz ~ '^[a-z0-9-]{2,32}$'), angelegt)`
  - `person(id, mail text not null, name text not null, passwort_hash text, betreiber boolean not null default false, aktiv boolean not null default true, sitzungs_version int not null default 1, angelegt)` plus `unique index person_mail_klein on event.person (lower(mail))`
  - `mitgliedschaft(person_id → person, firma_id → firma, rolle text check in ('leitung','mitglied'), primary key (person_id, firma_id))`
  - `event(id, firma_id → firma, kategorie text not null, name text not null, beginn timestamptz not null, ende timestamptz not null, ort_adresse text, ort_link text, beschreibung text, status text not null default 'geplant' check in ('entwurf','geplant','laeuft','abgeschlossen','abgesagt','archiviert'), quelle text not null default 'neu' check in ('neu','legacy'), legacy_url text, metadaten jsonb not null default '{}', angelegt, geaendert timestamptz not null default now(), check (ende >= beginn), unique (id, firma_id))`
  - `kontakt(id, firma_id → firma, name text not null, mail text, telegram text, person_id → person null, angelegt, unique (id, firma_id))`
  - Baustein-Tabellen. Alle haben `event_id uuid not null`, `firma_id uuid not null`, `foreign key (event_id, firma_id) references event.event(id, firma_id) on delete cascade`. Spalten mit Kontakt nutzen `foreign key (kontakt_id, firma_id) references event.kontakt(id, firma_id)`. Das ist Review Focus 5.
    - `orga_team(…, kontakt_id not null, rolle text)`
    - `teilnehmende(…, kontakt_id not null, zusage text not null default 'offen' check in ('offen','zugesagt','vielleicht','abgesagt'), unique (event_id, kontakt_id))`
    - `aufgabe_kategorie(…, name text not null, unique (event_id, name))`
    - `aufgabe(…, titel text not null, beschreibung text, status text not null default 'offen' check in ('offen','in_arbeit','erledigt'), kategorie_id uuid references aufgabe_kategorie(id) on delete set null, verantwortlich_id uuid, faellig timestamptz, foreign key (verantwortlich_id, firma_id) references kontakt(id, firma_id))`
    - `ablauf(…, beginn timestamptz not null, ende timestamptz, titel text not null, ort text, verantwortlich_id uuid, gleicher Kontakt-FK, check (ende is null or ende >= beginn))`
    - `budget_posten(…, titel text not null, art text not null check in ('kosten','einnahme'), betrag_cent bigint not null check (betrag_cent >= 0), status text not null default 'geplant' check in ('geplant','bestaetigt','bezahlt'))`
    - `verlauf(id bigserial primary key, event_id, firma_id, zeitpunkt timestamptz not null default now(), actor text not null, quelle text not null, baustein text not null, aktion text not null, ziel_id uuid)`, mit demselben Event-FK
  - `einladung(id, person_id → person, firma_id → firma, rolle text check in ('leitung','mitglied'), token_hash text not null unique, gueltig_bis timestamptz not null, eingeloest timestamptz)`
- Alle Tabellen gehören dem Admin, nicht `event_app`.

- [ ] **Step 1: Tests** (`test_migration.py`):
  - `test_migrationen_sortiert_und_eindeutig`
  - `test_anwenden_ist_wiederholbar`: Ein zweiter Lauf liefert `[]`.
  - `test_ende_vor_beginn_abgelehnt`: `psycopg.errors.CheckViolation`.
  - `test_firmenkuerzel_format`: `"Beispiel GmbH"` wird als `kurz` abgelehnt.
  - `test_mail_eindeutig_ohne_gross_klein`: `A@example.org` und `a@example.org` ergeben `UniqueViolation`.
- [ ] **Step 2: rot.**
- [ ] **Step 3: `conftest.py`, `anwenden.py`, `001_schema.sql` schreiben.**
- [ ] **Step 4: grün**, einmal mit `-W error`. Der Cluster startet einmal pro Sitzung. Die Laufzeit des Tests steht im Report.
- [ ] **Step 5: Commit** `feat(event-management): Schema event, Migrationslauf, Wegwerf-Postgres fuer Tests`

---

### Task 3: Row Level Security als zweites Tor

**Files:**
- Create: `db/002_rls.sql`
- Test: `tests/system/test_rls.py`

**Interfaces:**
- Produces, alle SQL-Funktionen `stable` und `security invoker` im Schema `event`:
  - `event.firmen() returns uuid[]` liest `string_to_array(nullif(current_setting('event.firmen', true), ''), ',')::uuid[]` und liefert ein leeres Array, wenn die Einstellung fehlt.
  - `event.ist_betreiber() returns boolean` gibt `current_setting('event.betreiber', true) = 'an'` zurück.
  - `event.person_id() returns uuid` und `event.aktuelles_event() returns uuid` geben NULL zurück, wenn die Einstellung leer ist.
  - `event.mandant_ok(f uuid) returns boolean` gibt `event.ist_betreiber() or f = any(event.firmen())` zurück.
  - `event.event_ok(e uuid) returns boolean` gibt `event.aktuelles_event() is null or e = event.aktuelles_event()` zurück.
  - Einzige Ausnahme für den Weg ohne Login: `event.einladung_holen(p_hash text) returns table(id uuid, person_id uuid, firma_id uuid, firma_name text, rolle text, gueltig_bis timestamptz, eingeloest timestamptz)`.
    - `security definer`, `set search_path = event, pg_temp`.
    - Liefert höchstens die eine Zeile mit genau diesem Hash.
    - `revoke all … from public`, `grant execute … to event_app`.
- Policies (Task 4 setzt die Einstellungen per `set_config(…, true)`):
  - `enable row level security` auf allen Tabellen außer `migration` und `person`.
  - Die Tabellen gehören dem Admin; `event_app` ist nicht Eigentümer, daher greift RLS ohne `force`.
  - `firma`: `using (event.mandant_ok(id))`; Insert nur `with check (event.ist_betreiber())`.
  - `mitgliedschaft`: `using (event.mandant_ok(firma_id) or person_id = event.person_id())`; schreiben nur `mandant_ok`.
  - `event`: `using (event.mandant_ok(firma_id) and event.event_ok(id))`, ebenso `with check`.
  - `kontakt` und `einladung`: `using (event.mandant_ok(firma_id))`.
  - Alle Baustein-Tabellen und `verlauf`: `using (event.mandant_ok(firma_id) and event.event_ok(event_id))`, ebenso `with check`.
- Rechte für `event_app`:
  - `usage` auf Schema `event`.
  - `select, insert, update, delete` auf alle Tabellen außer `migration` und `verlauf`.
  - `verlauf` nur `select, insert`, das Protokoll ist append-only.
  - `select, update(name, passwort_hash, sitzungs_version, aktiv)` und `insert` auf `person`.
  - `usage` auf die Sequenzen.

- [ ] **Step 1: Tests** (`test_rls.py`). Als Admin werden die Firmen A und B angelegt, je ein Event, ein Kontakt und eine Aufgabe. Danach arbeiten die Tests per `db_app(...)`, das als `event_app_test` die Einstellungen setzt:
  - `test_ohne_kontext_keine_zeilen` (Review Focus 2): Ohne `set_config` liefern `select count(*)` auf `event`, `aufgabe` und `kontakt` je 0. Ein `insert into event.aufgabe` scheitert mit `InsufficientPrivilege` oder einer RLS-Verletzung.
  - `test_firma_a_sieht_b_nicht`: Mit `firmen=A` liefert eine Abfrage auf die Aufgabe von B 0 Zeilen; ein `update` und ein `delete` darauf betreffen 0 Zeilen.
  - `test_firma_a_kann_nicht_in_b_schreiben`: Ein `insert` einer Aufgabe mit `firma_id=B` scheitert an der RLS-Verletzung.
  - `test_event_bindung`: Mit `firmen=A` und `event=E1` ist eine Aufgabe von E2 (auch Firma A) unsichtbar.
  - `test_betreiber_sieht_alles`
  - `test_kontakt_fremder_firma_nicht_verknuepfbar` (Review Focus 5): Als Betreiber wird `orga_team(event von A, kontakt von B)` eingefügt. Das scheitert an `ForeignKeyViolation`.
  - `test_verlauf_unveraenderlich`: Ein `update` oder `delete` auf `verlauf` als `event_app` scheitert an `InsufficientPrivilege`.
  - `test_einladung_holen_nur_mit_hash`: Ohne Kontext liefert `select * from event.einladung` 0 Zeilen. `event.einladung_holen(<richtiger Hash>)` liefert genau 1 Zeile, ein falscher Hash 0 Zeilen.
- [ ] **Step 2: rot.** **Step 3: `002_rls.sql`.** **Step 4: grün** (`-W error`).
- [ ] **Step 5: Commit** `feat(event-management): RLS als zweites Mandanten-Tor`

---

### Task 4: Kontext, Rechte, Firmen und Personen

**Files:**
- Create: `system/kontext.py`, `system/rechte.py`, `system/firmen.py`
- Test: `tests/system/test_firmen.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Kontext(person_id: UUID | None, betreiber: bool, rollen: Mapping[UUID, str], event_id: UUID | None = None, actor: str = "", quelle: str = "oberflaeche")`
    - Die Firmen sind `frozenset(rollen)`.
    - `Kontext.system()` liefert nur für Migration und Login-Lookup einen Kontext ohne Firmen.
  - `transaktion(conn, kontext) -> ContextManager[psycopg.Cursor]`
    - Öffnet `conn.transaction()` und setzt per `select set_config(...)` mit `is_local=true` die Werte `event.firmen` (kommagetrennt), `event.betreiber` ('an' oder ''), `event.person` und `event.event`.
    - Der Cursor nutzt `row_factory=dict_row`.
  - `class KeinRecht(PermissionError)`. Die Meldung nennt die Aktion, nie Daten.
  - `rechte.pruefe(kontext, aktion: str, firma_id: UUID) -> None`:
    - Betreiber: alles.
    - `leitung`: alles innerhalb ihrer Firmen, `firma_anlegen` aber nicht.
    - `mitglied`: nur `lesen`, `baustein_schreiben`, `event_aendern`.
  - Aktionen: `firma_anlegen`, `lesen`, `event_anlegen`, `event_aendern`, `event_loeschen`, `event_absagen`, `baustein_schreiben`, `einladen`, `rolle_aendern`, `mitglied_entfernen`.
  - `firmen.firma_anlegen(conn, k, name: str, kurz: str) -> dict` (nur Betreiber)
  - `firmen.firmen(conn, k) -> list[dict]`
  - `firmen.person_finden_oder_anlegen(conn, mail: str, name: str) -> dict`
    - Normalisiert die Mail mit `strip().lower()`.
    - Läuft im Systemkontext, weil `person` keine RLS hat.
  - `firmen.mitgliedschaft_setzen(conn, k, person_id, firma_id, rolle) -> None`: Upsert, braucht das Recht `einladen` bzw. `rolle_aendern`.
  - `firmen.mitglieder(conn, k, firma_id) -> list[dict]` mit `person_id, name, mail, rolle`.
  - `firmen.mitglied_entfernen(conn, k, person_id, firma_id) -> None`. Die letzte Leitung einer Firma lässt sich nicht entfernen (`ValueError`).
  - `firmen.kontext_fuer_person(conn, person_id) -> Kontext` liest Betreiber-Flag und Mitgliedschaften.

- [ ] **Step 1: Tests:**
  - `test_mitglied_darf_nicht_einladen`
  - `test_leitung_darf_keine_firma_anlegen`
  - `test_leitung_fremder_firma_ohne_recht`
  - `test_letzte_leitung_nicht_entfernbar`
  - `test_kontext_fuer_person_mit_zwei_firmen`: Die Rollen umfassen beide Firmen.
  - `test_transaktion_setzt_nur_lokal`: Nach Ende der Transaktion ist `current_setting('event.firmen', true)` leer.
  - Doppelte Wache: Die Fachschicht wird mit absichtlich zu weitem Kontext und falschem Recht aufgerufen. `KeinRecht` muss kommen, bevor SQL läuft.
- [ ] **Step 2: rot.** **Step 3: implementieren.** **Step 4: grün.**
- [ ] **Step 5: Commit** `feat(event-management): Kontext, Rechte, Firmen und Mitgliedschaften`

---

### Task 5: Events, Metadaten, Phase, Verlauf

**Files:**
- Create: `system/events.py`, `system/verlauf.py`
- Test: `tests/system/test_events.py`

**Interfaces:**
- Consumes: `wirksames_schema`, `Kontext`, `transaktion`, `pruefe`.
- Produces:
  - `verlauf.schreiben(cur, k, event_id, firma_id, baustein: str, aktion: str, ziel_id: UUID | None) -> None`
    - `actor` ist `k.actor` oder der Name der Person, `quelle` ist `k.quelle`.
    - Läuft im selben Cursor wie die Änderung, also in derselben Transaktion.
  - `verlauf.lesen(conn, k, event_id, seit: datetime | None = None, actor: str | None = None, limit: int = 100) -> list[dict]`
  - `ZEITZONE = ZoneInfo("Europe/Berlin")`
  - `events.event_anlegen(conn, k, firma_id, kategorie: str, name: str, beginn: datetime, ende: datetime, ort_adresse=None, ort_link=None, beschreibung=None, metadaten: dict | None = None) -> dict`
    - Naive Zeiten gelten als Europe/Berlin.
    - Prüft `eckdaten_pflicht` und die Metadaten gegen `felder`:
      - Unbekannte Felder, falsche Typen und eine fehlende Pflicht führen zu `ValueError` mit dem Feldnamen.
      - Vorgaben werden eingesetzt.
    - Eine abstrakte Kategorie führt zu `ValueError`.
    - Schreibt einen Verlaufseintrag mit `baustein="event"` und `aktion="angelegt"`.
  - `events.event_aendern(conn, k, event_id, **felder) -> dict`: gleiche Prüfungen, Verlaufseintrag `aktion="geaendert"`.
  - `events.event_status(conn, k, event_id, status) -> dict`: `abgesagt` und `archiviert` brauchen `event_absagen`.
  - `events.event_loeschen(conn, k, event_id) -> None` (`event_loeschen`)
  - `events.katalog(conn, k, firma_id=None, kategorie=None, suche: str | None = None, jetzt: datetime | None = None) -> list[dict]`
    - Jede Zeile enthält die Event-Felder sowie:
      - `kategorie_name`
      - `phase` (`laeuft`, `kommend` oder `vergangen`, aus beginn/ende relativ zu `jetzt`; `abgesagt` und `archiviert` gelten immer als `vergangen`)
      - `kennzahlen = {aufgaben_offen, aufgaben_gesamt, teilnehmende_zugesagt}`
    - Sortierung: `laeuft` zuerst, dann `kommend` aufsteigend nach Beginn, dann `vergangen` absteigend nach Ende.
    - `suche` sucht ohne Beachtung der Groß-/Kleinschreibung in Name, Ort und Firmenname.
  - `events.event(conn, k, event_id) -> dict` mit dem `schema` (wirksames Schema als dict: bausteine, felder, begriffe) dazu.

- [ ] **Step 1: Tests:**
  - `test_anlegen_mit_vererbten_feldern`: Bei einer Weihnachtsfeier ohne `wichteln` wird es zu `False`.
  - `test_unbekanntes_metadatenfeld_abgelehnt`
  - `test_falscher_feldtyp_abgelehnt`: `kapazitaet="viele"`.
  - `test_webinar_ohne_link_abgelehnt`
  - `test_abstrakte_kategorie_abgelehnt`
  - `test_phase_beachtet_berliner_zeit` (Review Focus 1): Ende heute 23:30 Berlin; mit `jetzt` = heute 23:00 Berlin ist die Phase `laeuft`, mit 23:45 `vergangen`.
  - `test_katalog_sortierung_und_kennzahlen`
  - `test_suche_findet_firmenname`
  - `test_katalog_nur_eigene_firmen`
  - `test_verlauf_bei_anlegen_und_aendern`
  - `test_mitglied_darf_nicht_loeschen`
- [ ] **Step 2–4:** rot, dann implementieren, dann grün (`-W error`).
- [ ] **Step 5: Commit** `feat(event-management): Events mit Schema-Pruefung, Katalog und Verlauf`

---

### Task 6: Kern-Bausteine

**Files:**
- Create: `system/kern.py`
- Modify: `system/bausteine.py` (Registry)
- Test: `tests/system/test_kern.py`

**Interfaces:**
- Produces:
  - `bausteine.REGISTRY: dict[str, BausteinTabelle]` für die in dieser Etappe umgesetzten Kern-Bausteine `aufgaben`, `orga_team`, `teilnehmende`, `ablauf`, `budget`.
    - `nachrichten` und alle Zusätze fehlen bewusst (Etappe 3).
    - `aufgaben` umfasst zusätzlich `aufgabe_kategorie` als Unterliste `kategorien`.
  - `@dataclass(frozen=True) BausteinTabelle(name: str, tabelle: str, felder: Mapping[str, str], pflicht: frozenset[str], sortierung: str)`
    - `felder` bildet erlaubte Spaltennamen auf Feldtypen ab.
    - Nur diese Spalten sind schreibbar. Spaltennamen kommen ausschließlich aus der Registry und werden mit `psycopg.sql.Identifier` gebaut, nie aus Eingaben.
  - `kern.liste(conn, k, event_id, baustein) -> list[dict]`
  - `kern.anlegen(conn, k, event_id, baustein, werte: dict) -> dict`
  - `kern.aendern(conn, k, event_id, baustein, zeile_id, werte: dict) -> dict`
  - `kern.loeschen(conn, k, event_id, baustein, zeile_id) -> None`
  - `kern.kontakt_anlegen(conn, k, firma_id, name, mail=None, telegram=None) -> dict`
  - `kern.kontakte(conn, k, firma_id, suche=None) -> list[dict]`
  - Regeln für alle Schreibzugriffe:
    - Sie prüfen `baustein_schreiben`.
    - Sie prüfen, dass das wirksame Schema des Events den Baustein enthält; sonst `ValueError("Baustein <name> gehört nicht zu Kategorie <kategorie>")`.
    - Sie schreiben `verlauf` (`baustein`, `aktion` = angelegt/geaendert/geloescht, `ziel_id`).
    - Legacy-Events (`quelle='legacy'`) lehnen sie mit `ValueError` ab.
    - Unbekannte Felder führen zu `ValueError`.

- [ ] **Step 1: Tests:**
  - `test_aufgabe_anlegen_aendern_loeschen_mit_verlauf`: genau 3 Verlaufseinträge, `ziel_id` gesetzt.
  - `test_budget_bei_team_meeting_abgelehnt`
  - `test_unbekanntes_feld_abgelehnt`: z. B. `{"firma_id": …}` oder `{"titel; drop": 1}`; dabei wird keine SQL ausgeführt.
  - `test_legacy_event_abgelehnt`
  - `test_teilnehmende_zusage_werte`
  - `test_mandantenmatrix_kern`: parametriert über alle Bausteine in `REGISTRY` und alle vier Operationen. Ein Kontext von Firma A auf ein Event von Firma B liefert `KeinRecht` oder „nicht gefunden“ und schreibt nichts. Nachgeprüft wird als Admin.
- [ ] **Step 2–4:** rot, dann implementieren, dann grün (`-W error`).
- [ ] **Step 5: Commit** `feat(event-management): Kern-Bausteine (Aufgaben, Orga-Team, Teilnehmende, Ablauf, Budget)`

---

### Task 7: Zugang: Einladung, Passwort, Login, Sitzung

**Files:**
- Create: `system/zugang.py`, `system/mailer.py`
- Test: `tests/system/test_zugang.py`

**Interfaces:**
- Produces:
  - `mailer.Mailer` (Protocol): `senden(an: str, betreff: str, text: str) -> None`.
  - `mailer.SmtpMailer.aus_umgebung()` liest `EVENT_SMTP_HOST`, `EVENT_SMTP_PORT`, `EVENT_SMTP_USER`, `EVENT_SMTP_PASSWORT_FILE` und `EVENT_SMTP_ABSENDER` und verbindet per STARTTLS.
  - `mailer.SpeicherMailer` ist die Testattrappe und speichert gesendete Mails in einer Liste.
  - `zugang.einladen(conn, k, mailer, mail, name, firma_id, rolle, basis_url) -> None`:
    - Prüft `einladen` und legt die Person per `person_finden_oder_anlegen` an.
    - Erzeugt `secrets.token_urlsafe(32)` und speichert nur `sha256` mit einer Gültigkeit von 7 Tagen.
    - Die Mail enthält den Link `<basis_url>/einladung/<token>`. Der Token steht nirgends sonst, auch nicht im Log.
  - `zugang.einladung_einloesen(conn, token, passwort: str, name: str | None) -> UUID`:
    - Holt die Einladung über `event.einladung_holen(sha256(token))` (Task 3). Prüft, ob sie abgelaufen oder schon eingelöst ist.
    - Danach in einer Transaktion mit `Kontext(person_id, betreiber=False, rollen={firma_id: rolle})`: Passwort und Mitgliedschaft setzen, Einladung als eingelöst markieren, ohne `rechte.pruefe`. Der gültige Token ist das Recht.
    - Ein Passwort mit weniger als 12 Zeichen führt zu `ValueError`.
    - Ist das Passwort schon gesetzt (zweite Firma), bleibt es unverändert; dann ist `passwort` leer erlaubt.
  - `zugang.passwort_hash(pw) -> str` und `zugang.passwort_ok(pw, h) -> bool`: `hashlib.scrypt` mit n=2**14, r=8, p=1 und 16 Byte Salt. Das Format ist `scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>`.
  - `zugang.anmelden(conn, mail, passwort) -> UUID | None`:
    - Bei unbekannter Mail wird trotzdem ein Hash berechnet (gleiche Laufzeit).
    - Eine inaktive Person führt zu `None`.
    - Bei 5 Fehlversuchen pro Mail innerhalb von 15 Minuten (im Speicher) folgt `None`, bis das Fenster abläuft.
  - `zugang.cookie_ausstellen(person_id, sitzungs_version, geheimnis: bytes, jetzt=None) -> str` erzeugt `<person_id>.<version>.<ablauf_epoch>.<hmac_sha256_b64>`, gültig 12 Stunden.
  - `zugang.cookie_pruefen(wert, geheimnis, conn, jetzt=None) -> UUID | None`: Signatur, Ablauf und `sitzungs_version == person.sitzungs_version` müssen stimmen.
  - `zugang.abmelden_ueberall(conn, person_id)` erhöht `sitzungs_version`.
  - `zugang.csrf_token(cookie_wert, geheimnis) -> str` und `zugang.csrf_ok(cookie_wert, token, geheimnis) -> bool`.

- [ ] **Step 1: Tests:**
  - `test_einladung_gleiche_mail_andere_schreibweise` (Review Focus 4): Zweimal einladen, einmal mit Großbuchstaben, ergibt eine Person mit zwei Mitgliedschaften.
  - `test_token_nur_als_hash_gespeichert`: Der Klartext-Token aus der `SpeicherMailer`-Mail kommt in keiner Spalte von `event.einladung` vor.
  - `test_token_einmalig_und_ablauf`
  - `test_kurzes_passwort_abgelehnt`
  - `test_anmelden_falsches_passwort_und_sperre`
  - `test_cookie_manipuliert_oder_abgelaufen`
  - `test_abmelden_ueberall_entwertet_cookie`
  - `test_csrf`
  - `test_kein_token_im_log` (`caplog`): Weder der Token noch das Passwort erscheinen im Log.
- [ ] **Step 2–4:** rot, dann implementieren, dann grün (`-W error`).
- [ ] **Step 5: Commit** `feat(event-management): Einladung, Login und Sitzung`

---

### Task 8: `event-api` (FastAPI) und Mandantenmatrix über HTTP

**Files:**
- Create: `system/api.py`
- Test: `tests/system/test_api.py`

**Interfaces:**
- Consumes: alles aus den Tasks 1–7.
- Produces:
  - `api.erstelle_app(dsn: str, geheimnis: bytes, mailer: Mailer, basis_url: str, kategorien_ordner: Path | None = None) -> FastAPI`
    - Lädt die Kategorien beim Start; bei `KategorieFehler` startet die App nicht.
    - Eine Verbindung je Anfrage über `psycopg_pool.ConnectionPool` ist erlaubt. Ist der Pool nicht installiert, ist das eine einfache Verbindung je Anfrage.
  - Cookie und CSRF:
    - Der Cookie heißt `event_sitzung` (HttpOnly, SameSite=Lax, Secure abschaltbar per `EVENT_COOKIE_UNSICHER=1` nur für Tests).
    - Ändernde Anfragen brauchen den Header `X-CSRF` (aus `GET /api/ich`).
  - Routen (JSON):
    - **Zugang:**
      - `POST /api/anmelden` mit `{mail, passwort}`
      - `POST /api/abmelden`
      - `GET /api/ich` liefert `{person, betreiber, firmen:[{id,name,kurz,rolle}], csrf}`
      - `GET /api/einladung/{token}` liefert `{gueltig, firma_name, braucht_passwort}`
      - `POST /api/einladung/{token}` mit `{passwort?, name?}`
    - **Firmen und Mitglieder:**
      - `GET /api/firmen`, `POST /api/firmen` (Betreiber)
      - `GET /api/firmen/{id}/mitglieder`
      - `POST /api/firmen/{id}/einladungen` mit `{mail, name, rolle}`
      - `PATCH /api/firmen/{id}/mitglieder/{person_id}` mit `{rolle}`
      - `DELETE /api/firmen/{id}/mitglieder/{person_id}`
    - **Kategorien:**
      - `GET /api/kategorien` liefert die wählbaren Kategorien `{id, name, eltern}`
      - `GET /api/kategorien/{id}/schema` liefert das wirksame Schema
    - **Events:**
      - `GET /api/events?firma=&kategorie=&suche=` (Katalog)
      - `POST /api/events`
      - `GET /api/events/{id}`
      - `PATCH /api/events/{id}`
      - `POST /api/events/{id}/status`
      - `DELETE /api/events/{id}`
    - **Bausteine und Verlauf:**
      - `GET|POST /api/events/{id}/b/{baustein}`
      - `PATCH|DELETE /api/events/{id}/b/{baustein}/{zeile}`
      - `GET /api/events/{id}/verlauf`
    - **Kontakte:** `GET|POST /api/firmen/{id}/kontakte`
  - Fehlerabbildung:
    - `KeinRecht` wird zu 403.
    - Nicht gefunden, auch fremde Firmen, wird zu 404 (bewusst nicht 403, damit nichts über fremde IDs verraten wird).
    - `ValueError` wird zu 422 mit dem Text der Meldung.
    - Ohne gültigen Cookie: 401.
    - Unerwartete Fehler werden zu 500 mit dem generischen Text „interner Fehler“, ohne Stacktrace im Body.
  - Start auf der Kommandozeile: `python -m spaces.event_management.system.api` liest `EVENT_APP_DSN_FILE` bzw. `EVENT_APP_DSN`, `EVENT_SITZUNG_GEHEIMNIS_FILE`, `EVENT_BASIS_URL` und die SMTP-Werte und startet uvicorn auf `0.0.0.0:${EVENT_API_PORT:-8170}`.

- [ ] **Step 1: Tests** (FastAPI `TestClient` gegen den Wegwerf-Cluster, `SpeicherMailer`):
  - `test_ablauf_einladen_einloesen_anmelden`: Der Betreiber legt eine Firma an und lädt eine Leitung ein. Diese löst die Einladung ein, meldet sich an und sieht nur ihre Firma.
  - `test_ohne_cookie_401`
  - `test_ohne_csrf_403`
  - `test_mandantenmatrix_http`: parametriert über alle schreibenden Routen. Ein Mitglied von Firma A gegen Ressourcen von Firma B bekommt 404, und es ändert sich nichts.
  - `test_kategorie_schema_route`: Für `team_meeting` gilt `"budget" not in bausteine`.
  - `test_katalog_route_gruppierbar`: Jede Zeile hat `phase` und `kennzahlen`.
  - `test_500_ohne_details`
- [ ] **Step 2–4:** rot, dann implementieren, dann grün (`-W error`). Danach läuft einmal der gesamte Ordner `spaces/event_management/tests` (alle bisherigen Space-Tests bleiben grün).
- [ ] **Step 5: Commit** `feat(event-management): event-api (Zugang, Firmen, Kategorien, Events, Bausteine)`

---

## Nach der Etappe

Die Etappe ist erst fertig, wenn:
- der gesamte Space-Testordner grün ist,
- die Mandantenmatrix aus Task 6 und Task 8 grün ist,
- keine echten Daten im Diff stehen.

Nicht in dieser Etappe:
- Ausrollen
- Supabase-Migration auf der VM
- Dockerfile

Diese Punkte gehören zu Etappe 4. Etappe 2 (Oberfläche) und Etappe 3 (Werkzeug-Server und Bot) bauen auf den Interfaces aus den Tasks 4–8 auf.
