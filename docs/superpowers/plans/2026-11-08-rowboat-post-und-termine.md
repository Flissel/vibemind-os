# Rowboat: offene Post und Kundentermine im Wissensbaum, Mail-Tab aus – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rowboat zeigt keinen Mail-Tab mehr; stattdessen stehen Felix' offene Mails (wie sales-ui `/posteingang`) und alle Kundentermine als Notizen im Wissensbaum, stündlich frisch.

**Architecture:** Teil 1 ist ein Schalter `EMAIL_ENABLED=false` im Renderer des Forks `Flissel/rowboat` (Branch `vibemind`). Teil 2/3 erweitern den nächtlichen Geschäftsdaten-Lauf (`spaces/rowboat/geschaeftsdaten/`) um Termin-Notizen (anlegen, archivieren, löschen) und bekommen einen zweiten, stündlichen Einstieg `stuendlich.py`, der nur anlegt/aktualisiert (Termine) und die Notiz „Zu beantworten“ schreibt. Gelesen wird ausschließlich über die Rolle `rowboat_leser` und `security_barrier`-Sichten; beide Läufe teilen sich eine `flock`-Sperre.

**Tech Stack:** Python 3.11 (VM), pytest, PostgreSQL-Sichten (Supabase-Container `debian-supabase-db-1`), systemd-Timer; Fork: TypeScript/React, vitest, pnpm.

**Spec:** `docs/superpowers/specs/2026-11-08-rowboat-post-und-termine-design.md`

## Global Constraints

- Notizen tragen `erzeugt_von: rowboat-geschaeftsdaten` und einen eigenen `schluessel`; fremde Notizen werden nie angefasst.
- Generierte Notizen beginnen mit `---`, enthalten nie `@rowboat`, `live`, `live_note`, `curated_at` (Vertragstest `test_geschaeftsdaten_vertrag.py`).
- Offen-Regel exakt wie sales-claw `offen_sql()`: `ziel = 'posteingang' and status = 'eingeordnet' and erledigt_am is null and abgeglichen_am is not null and geloescht_am is null`, dazu `besitzer = 'felix'` und `datum >= '<Stichtag>'`. Ohne Stichtag ist nichts offen.
- Stichtag: nur der Schlüssel `POST_AUFRAEUMEN_AB` aus `~/sales-claw/.env`, gelesen mit `laeden.env_werte`. Keine anderen Werte aus `.env` lesen, loggen oder ausgeben.
- Reihenfolge der Mails wie sales-ui: `order by datum asc, id`.
- Sales-ui-Links: Liste `https://vibemind-offload-1.tail6c7d61.ts.net/posteingang`, einzelne Mail `https://vibemind-offload-1.tail6c7d61.ts.net/post/mail/<id>`, Kalender `https://vibemind-offload-1.tail6c7d61.ts.net/kalender`.
- Sensible Mail: „vertrauliche Mail“, kein Absender, kein Betreff. Mail eines gesperrten Kontakts: „Mail eines gesperrten Kontakts“, nur Alter und Postfach. Beide zählen mit.
- Höchstens 50 Listenpunkte, danach „… und N weitere in sales-ui“.
- Liste statt Tabelle (keine Wiki-Links in Tabellen).
- Der stündliche Lauf löscht, archiviert und verschiebt nie. Löschen/Archivieren macht nur der nächtliche Lauf, Obergrenze 15 je Lauf (`Takt`), Massen-Wächter `MASSEN_ANTEIL = 0.20`, `MASSEN_MIN = 5`.
- Schreiben nur bei inhaltlicher Änderung; die Stand-Zeile allein ist keine Änderung.
- Beide Läufe schreiben nie gleichzeitig (gemeinsame `flock`-Sperre).
- Timer `rowboat-post-termine.timer`: stündlich zur Minute 5.
- `spaces/rowboat/schema` und `spaces/rowboat/apps/x` sind Sync-Kopien: nie von Hand ändern (Fork-Änderung kommt per `sync_desktop.py`).
- Notiztexte deutsch, sachlich, ohne Emojis. Code-Namen in vibemind-os deutsch wie im Bestand, im Fork englisch wie upstream.
- Commits: Conventional Commits, Trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Feature-Branches, nie direkt auf `master`/`main`, kein Force-Push.
- Maschinenweite Arbeit (VM, Datenbank, systemd) erst nach Claim in `C:\Users\User\Desktop\secondbrain\00_Meta\002_Koordination_Live.md`; Repo-Arbeit nach Claim in `C:\Users\User\Desktop\Vibemind_V1\WORKBOARD.md`. Claims sofort committen.

## Review Focus

1. **Stichtag in sales-ui und `.env` laufen auseinander** (sales-ui liest `POST_AUFRAEUMEN_AB` beim Container-Start, der Lauf bei jedem Lauf aus der Datei). Erwartung: Der Lauf vergleicht genau wie sales-ui in SQL; die Abnahme vergleicht die Zahlen. Ein kaputter Wert gilt wie fehlend, also ist nichts offen. → Test in Task 6 (`test_stichtag_kaputt_gilt_als_fehlend`).
2. **Absender passt zu einem gesperrten und einem freien Lead zugleich.** Erwartung: gesperrt gewinnt (fail closed), kein Name, kein Link. → SQL-Test in Task 2, Python-Test in Task 6 (`test_gesperrt_gewinnt_vor_steckbrief_link`).
3. **Steuerzeichen, `[[`, `]]`, `@rowboat`, Zeilenumbrüche in Betreff/Absender/Thema/Ort.** Erwartung: eine Zeile, keine Wiki-Links aus Fremdtext, Auslöser neutralisiert. → Tests in Task 4 (`test_termin_fremdtext_neutralisiert`) und Task 6 (`test_betreff_fremdtext_neutralisiert`).
4. **Kontakt wird gesperrt, nachdem seine Termin-Notiz existiert** (die Sicht liefert dann keine Ereignisse mehr). Erwartung: Der nächtliche Lauf löscht die Notiz über `kontakt_id` im Schlüssel, nicht über die Ereignisse. → Test in Task 5 (`test_termin_gesperrter_kontakt_wird_entfernt`).
5. **Stündlicher Lauf trifft eine laufende Nacht.** Erwartung: Er setzt aus (Exit 0, eine Logzeile) statt zu warten oder zu schreiben. → Test in Task 7 (`test_stuendlich_setzt_bei_sperre_aus`).

## Festlegungen des Plans (über die Spec hinaus)

- Termin-Schlüssel ist `termin:<lead_id>:<uid>` statt `termin:<uid>`. Wird ein Kontakt gesperrt, liefert die Sicht keine Ereignisse mehr. Nur über die Lead-ID im Schlüssel findet der nächtliche Lauf seine Termin-Notizen dann noch zum Löschen.
- `Post/_overview.md` bekommt den eigenen Schlüssel `post:uebersicht`.
- Die Termin-Übersicht entsteht nur bei Firmen mit Terminen oder einem Termin-Archiv. Sonst gäbe es leere Ordner bei den Läden Ivan und Sophie.
- Der Stichtag wird als Datumsliteral in die SQL-Abfrage des Laufs gesetzt, nicht in die Sicht. So bleibt die Sicht unabhängig von `.env`, und der Vergleich ist derselbe wie in sales-ui.
- Die Zuordnung „Absender ist ein gesperrter Kontakt“ ist fail closed. Gehört eine Adresse zu irgendeinem gesperrten Lead, gilt die Mail als gesperrt.

---

## Arbeitsumgebung

- vibemind-os: neuer Worktree `C:\Users\User\ClaudeWork\wt-post-termine`, Branch `feat/rowboat-post-termine`, abgezweigt von `origin/docs/rowboat-post-termine` (enthält Spec und Plan, basiert auf `master` 193262d2).
- Fork: Checkout `C:\Users\User\ClaudeWork\wt-rowboat-vibemind`, neuer Branch `feat/email-aus` von `origin/vibemind` (13e01004). `node_modules` ist vorhanden.
- Tests vibemind-os: `python -m pytest spaces/rowboat/tests -q` im Worktree-Root (Ausgangsstand: 802 passed, 4 skipped).
- Tests Fork: `pnpm -C apps/x/apps/renderer exec vitest run <datei>` und `pnpm -C apps/x/apps/renderer typecheck`.
- Shell-Hinweis: `python - <<EOF`-Heredocs tun in dieser Shell nichts. Dateien mit Edit/Write ändern, Hilfsskripte als Datei im Scratchpad.

---

### Task 1: Mail-Tab im Fork ausschalten

**Files:**
- Modify: `apps/x/apps/renderer/src/lib/feature-flags.ts`
- Create: `apps/x/apps/renderer/src/lib/email-gate.ts`, `apps/x/apps/renderer/src/lib/email-gate.test.ts`
- Modify: `apps/x/apps/renderer/src/App.tsx` (Stellen um Zeile 743, 5312, 5517, 5921, 5947–5956, 6690, 6755, 7725 auf `origin/vibemind`)
- Modify: `apps/x/apps/renderer/src/components/dock-sidebar.tsx` (737–760, 946, 1003–1006)
- Modify: `apps/x/apps/renderer/src/components/sidebar-content.tsx` (850, 871)
- Modify: `apps/x/apps/renderer/src/components/command-palette.tsx` (Scope-/Abschnittsfilter wie bei `SPACES_ENABLED`, 266 und 338)
- Modify: `apps/x/apps/renderer/src/components/product-tour.tsx` (Schritt `id: 'email'`)

**Interfaces:**
- Produces: `export const EMAIL_ENABLED: boolean = false` in `feature-flags.ts`; `export function gateEmailView<V extends { type: string }>(view: V, enabled?: boolean): V | { type: 'home' }` in `email-gate.ts` (Default `enabled = EMAIL_ENABLED`).

- [ ] **Step 1: Branch anlegen**

Run: `git -C C:/Users/User/ClaudeWork/wt-rowboat-vibemind switch -c feat/email-aus origin/vibemind`

- [ ] **Step 2: Failing test schreiben** (`email-gate.test.ts`)

```ts
import { describe, expect, it } from 'vitest'
import { EMAIL_ENABLED } from './feature-flags'
import { gateEmailView } from './email-gate'

describe('gateEmailView', () => {
  it('is off in the VibeMind build', () => { expect(EMAIL_ENABLED).toBe(false) })
  it('sends the email view home when disabled', () => {
    expect(gateEmailView({ type: 'email', threadId: 't1' }, false)).toEqual({ type: 'home' })
  })
  it('keeps every other view', () => {
    const v = { type: 'meetings' }
    expect(gateEmailView(v, false)).toBe(v)
  })
  it('keeps the email view when enabled', () => {
    const v = { type: 'email' }
    expect(gateEmailView(v, true)).toBe(v)
  })
})
```

- [ ] **Step 3: Test laufen lassen, er muss scheitern**

Run: `pnpm -C apps/x/apps/renderer exec vitest run src/lib/email-gate.test.ts`
Expected: FAIL (Modul `./email-gate` fehlt).

- [ ] **Step 4: `EMAIL_ENABLED` und `gateEmailView` anlegen**

`feature-flags.ts`: Konstante `false` mit Kommentar (VibeMind: Mail läuft über sales-claw; der Gmail-Tab soll nicht benutzbar sein). Kein Preload-Flag. `email-gate.ts`: reine Funktion ohne React.

- [ ] **Step 5: Test laufen lassen, er muss bestehen**

Run: `pnpm -C apps/x/apps/renderer exec vitest run src/lib/email-gate.test.ts`
Expected: PASS (4 Tests).

- [ ] **Step 6: Einstiegspunkte sperren**

- `App.tsx`: Am Anfang von `navigateToView` und im Ansicht-Anwenden-Switch (`case 'email'`, ~5312) gilt `gateEmailView`, damit Copilot (`navigateToNamedView`, `read-view`-Zweige 5947–5956), Palette (6690), Menübefehl (6755), Verlauf/gespeicherte Ansichten (743) und `openEmailView` auf der Startseite landen. Die `KeepAliveSection` für `email` (7725) wird nur gerendert, wenn `EMAIL_ENABLED` gilt.
- `dock-sidebar.tsx`: Der Nav-Eintrag `key: 'email'` fehlt bei `!EMAIL_ENABLED`. Der Effekt mit `gmail:getImportant` läuft dann nicht, es gibt also keinen IPC-Aufruf, keine Ungelesen-Zahl und keine Vorschau.
- `sidebar-content.tsx`: Mail-Vorschau/Tipp nur bei `EMAIL_ENABLED`, ebenso das dortige Laden.
- `command-palette.tsx`: Abschnitt/Scope `email` wie `spaces` filtern.
- `product-tour.tsx`: Schritt `email` bei `!EMAIL_ENABLED` aus der Schrittliste filtern.

- [ ] **Step 7: Typecheck und Renderer-Tests**

Run: `pnpm -C apps/x/apps/renderer typecheck` und danach `pnpm -C apps/x/apps/renderer test`
Expected: Typecheck ohne Fehler. Die Tests sind so grün wie vor der Änderung; falls vorher schon Tests rot waren, ihre Namen im Report nennen.

- [ ] **Step 8: Commit und Fork-PR**

```bash
git add apps/x/apps/renderer/src
git commit -m "feat(renderer): EMAIL_ENABLED-Schalter, Mail-Tab in VibeMind aus"
git push -u origin feat/email-aus
gh pr create --repo Flissel/rowboat --base vibemind --head feat/email-aus --title "feat(renderer): Mail-Tab in VibeMind aus" --body "<Zusammenfassung + 🤖-Zeile>"
```

Den PR erst mergen, wenn Felix zugestimmt hat. Danach den Merge-Commit von `vibemind` für Task 9 notieren.

---

### Task 2: Sichten für `rowboat_leser` erweitern

**Files:**
- Modify: `spaces/rowboat/geschaeftsdaten/db/rowboat_leser.sql`
- Modify: `spaces/rowboat/geschaeftsdaten/db/verify_rowboat_leser.sql`
- Modify: `spaces/rowboat/geschaeftsdaten/steckbrief.py` (Modul-Docstring „Betreiber-Entscheid 2“)
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_sql.py` (bestehende statische SQL-Tests)

**Interfaces:**
- Produces: `<schema>.rowboat_ereignisse` mit den neuen Spalten **am Ende** `termin_dauer text`, `termin_thema text`, `termin_ort text` (nur für `typ = 'termin'` gefüllt, sonst `null`). Neue Sicht `sales.rowboat_post_offen(id, datum, fach, bereich, sensibel, postfach, absender_klein, von_anzeige, betreff, absender_gesperrt)`.

- [ ] **Step 1: Failing Tests schreiben**

```python
def test_ereignisse_termin_details_am_ende():
    sql = _sql()  # vorhandener Lese-Helfer der Datei
    teil = sql[sql.index("rowboat_ereignisse with (security_barrier)"):]
    for spalte in ("termin_dauer", "termin_thema", "termin_ort"):
        assert f"case when a.type = 'termin' then a.payload->>" in teil
        assert spalte in teil
    # neue Spalten nach empfohlen_von_lead (create or replace view darf nur hinten anfuegen)
    assert teil.index("empfohlen_von_lead") < teil.index("termin_dauer")

def test_post_offen_regel_wie_sales_ui():
    sql = _sql()
    teil = sql[sql.index("rowboat_post_offen"):]
    for bedingung in ("m.ziel = 'posteingang'", "m.status = 'eingeordnet'", "m.erledigt_am is null",
                      "m.abgeglichen_am is not null", "m.geloescht_am is null", "m.besitzer = 'felix'"):
        assert bedingung in teil
    for verboten in ("m.text", "m.auszug", "m.anhaenge", "m.an "):
        assert verboten not in teil
    assert "security_barrier" in teil
    assert "grant select on sales.rowboat_post_offen to rowboat_leser" in sql

def test_post_offen_gesperrt_und_sensibel_leeren():
    teil = _sql()[_sql().index("rowboat_post_offen"):]
    assert "case when g.gesperrt or m.sensibel is not false then null else m.betreff end" in teil
    assert "case when g.gesperrt then null else" in teil
```

- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern**

Run: `python -m pytest spaces/rowboat/tests -q -k "termin_details or post_offen"`
Expected: FAIL.

- [ ] **Step 3: SQL ändern**

- `rowboat_ereignisse`: drei Spalten hinten anfügen (`a.payload->>'dauer_minuten'`, `'thema'`, `'ort'`, jeweils `case when a.type = 'termin' then … end`).
- Neue Sicht nach der Laden-Schleife, nur wenn `to_regclass('sales.post_mails')` und `to_regclass('sales.post_konten')` existieren:
  - Quelle `sales.post_mails m join sales.post_konten k on k.id = m.konto_id`.
  - Absender: `lower(coalesce(substring(m.von from '<([^>]+)>'), trim(m.von)))`, wörtlich `ABSENDER_SQL` aus sales-claw.
  - `cross join lateral (select exists(select 1 from sales.leads l where lower(btrim(l.email)) = <absender> and <Sperr-Prädikat>) as gesperrt) g`.
  - Das Sperr-Prädikat ist dasselbe wie für `p.gesperrt` in `rowboat_kontakte` (Datei-Kopf „Sperr-Regel“, Zeilen ~84–88): unklar zählt als gesperrt, `_loeschantrag` sperrt.
  - `postfach` = `coalesce(k.kennung, k.anbieter)`.
  - `absender_klein`/`von_anzeige` sind `null` bei `g.gesperrt`. `von_anzeige` ist der Anzeigename vor `<…>`, getrimmt und ohne Anführungszeichen, sonst `null`.
  - `betreff` ist `null` bei `g.gesperrt or m.sensibel is not false`.
  - `sensibel` = `m.sensibel is not false` (NULL gilt als sensibel).
- Grant `select` an `rowboat_leser`, Kommentar „Betreiber-Entscheid 2“ im Kopf anpassen: Termin-Thema und -Ort aufgehoben (Felix, 08.11.2026), sonst gilt die Regel weiter. Denselben Satz im Docstring von `steckbrief.py`.
- `verify_rowboat_leser.sql`: prüft, dass `rowboat_leser` die neue Sicht lesen kann, nicht aber `sales.post_mails` direkt. Außerdem prüft es, dass die Sicht keine Spalte `text`/`auszug`/`anhaenge`/`an` hat (über `information_schema.columns`).

- [ ] **Step 4: Tests laufen lassen, sie müssen bestehen**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS, alle bisherigen Tests weiter grün.

- [ ] **Step 5: Commit**

```bash
git add spaces/rowboat/geschaeftsdaten/db spaces/rowboat/geschaeftsdaten/steckbrief.py spaces/rowboat/tests
git commit -m "feat(rowboat): Leser-Sichten fuer offene Post und Termin-Details"
```

---

### Task 3: Quelle liest Termin-Details und offene Post

**Files:**
- Modify: `spaces/rowboat/geschaeftsdaten/quelle.py`
- Modify: `spaces/rowboat/tests/geschaeftsdaten_fake.py`
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_quelle.py` (bestehende Quelle-Tests)

**Interfaces:**
- Consumes: Sichten aus Task 2.
- Produces:
  - `Ereignis` bekommt `termin_dauer: str | None = None`, `termin_thema: str | None = None`, `termin_ort: str | None = None` (hinten, mit Default).
  - `@dataclass(frozen=True) class PostMail: id: str; datum: str; fach: str | None; bereich: str | None; sensibel: bool; postfach: str | None; absender_klein: str | None; von_anzeige: str | None; betreff: str | None; absender_gesperrt: bool`
  - `Quelle.post_offen(self, stichtag: date) -> list[PostMail]`, sortiert `datum asc, id`.
  - Fake: `FakeQuelle(..., post: list[PostMail] = ())`, filtert `datum[:10] >= stichtag.isoformat()` und sortiert. Helfer `post_mail(**ueberschreiben) -> PostMail` mit nicht sensiblen, nicht gesperrten Standardwerten.

- [ ] **Step 1: Failing Tests schreiben**

```python
def test_post_offen_sql_mit_stichtag_literal():
    q = _AufzeichnendeQuelle([])   # PsqlQuelle-Unterklasse mit ueberschriebenem _zeilen, wie in den vorhandenen Tests
    q.post_offen(date(2026, 11, 8))
    sql = q.letztes_sql
    assert "from sales.rowboat_post_offen" in sql
    assert "datum >= '2026-11-08'" in sql
    assert "order by datum asc, id" in sql

def test_ereignis_termin_details_gelesen():
    q = _AufzeichnendeQuelle([{"lead_id": "l1", "typ": "termin", "zeit": "2026-11-09T10:00:00+00:00",
                               "termin_uid": "u1", "termin_datum": "2026-11-12", "termin_uhrzeit": "14:30",
                               "termin_dauer": "45", "termin_thema": "Erstgespraech",
                               "termin_ort": "https://meet.jit.si/abc", "empfohlen_von_name": None,
                               "empfohlen_von_lead": None}])
    e = q.ereignisse("sales")[0]
    assert (e.termin_dauer, e.termin_thema, e.termin_ort) == ("45", "Erstgespraech", "https://meet.jit.si/abc")
```

- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern**

Run: `python -m pytest spaces/rowboat/tests -q -k "post_offen_sql or termin_details_gelesen"`
Expected: FAIL.

- [ ] **Step 3: `quelle.py` und Fake erweitern**

Das Stichtag-Literal kommt nur aus `stichtag.isoformat()` eines `date`-Objekts, nie aus einem String. Im `ereignisse`-SQL die drei Spalten mit auswählen.

- [ ] **Step 4: Tests laufen lassen, sie müssen bestehen**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spaces/rowboat/geschaeftsdaten/quelle.py spaces/rowboat/tests
git commit -m "feat(rowboat): Quelle liest offene Post und Termin-Details"
```

---

### Task 4: Termin-Notizen bauen (`termine.py`)

**Files:**
- Create: `spaces/rowboat/geschaeftsdaten/termine.py`
- Modify: `spaces/rowboat/geschaeftsdaten/steckbrief.py` (`_offene_termine` nutzt `termine_aus`)
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_termine.py`

**Interfaces:**
- Consumes: `Ereignis` (Task 3), `Soll` aus `ablage.py`, `dateistamm`/`eindeutig`/`relpfad` aus `namen.py`, `ERZEUGT_VON`, `_AUSLOESER`-Neutralisierung aus `steckbrief.py`.
- Produces:
  - `@dataclass(frozen=True) class Termin: lead_id: str; uid: str; datum: date; uhrzeit: str | None; dauer_minuten: int | None; thema: str | None; ort: str | None`
  - `termine_aus(ereignisse: list[Ereignis]) -> tuple[list[Termin], set[str]]` liefert (aktive Termine, abgesagte uids). Je uid gewinnt der spätere Eintrag; ohne gültiges `termin_datum` wird der Termin übersprungen.
  - `TERMIN_PRAEFIX = "termin:"`, `termin_schluessel(lead_id: str, uid: str) -> str` gibt `f"termin:{lead_id}:{uid}"` zurück.
  - `UEBERSICHT_TERMINE = "termine:uebersicht"`
  - `termine_soll(firma: str, termine: list[Termin], pfade: dict[str, str], namen: dict[str, str], *, heute: date, archiv_vorhanden: bool) -> list[Soll]`. `firma` ist der Ordnername, `pfade` geht von Lead-ID auf den Steckbrief-Pfad ab `knowledge/` (mit `.md`). `Soll.rel` ist relativ zum Firmenordner (`Termine/…`). Termine ohne Steckbrief-Pfad werden übersprungen. Die Übersicht entsteht nur, wenn es Termine gibt oder `archiv_vorhanden` gilt.
  - `gesperrte_termin_schluessel(ist_schluessel: Iterable[str], gesperrte_ids: set[str], abgesagt: set[str]) -> set[str]`

- [ ] **Step 1: Failing Tests schreiben**

```python
HEUTE = date(2026, 11, 8)
PFADE = {"l1": "companys/Vibemind/Kontakte/Kunden/Anna Muster.md"}
NAMEN = {"l1": "Anna Muster"}

def test_termine_aus_absage_und_spaeterer_eintrag():
    ev = [ereignis(lead_id="l1", typ="termin", termin_uid="u1", termin_datum="2026-11-10", termin_uhrzeit="09:00"),
          ereignis(lead_id="l1", typ="termin", termin_uid="u1", termin_datum="2026-11-12", termin_uhrzeit="14:30"),
          ereignis(lead_id="l1", typ="termin", termin_uid="u2", termin_datum="2026-11-13"),
          ereignis(lead_id="l1", typ="termin_abgesagt", termin_uid="u2")]
    aktiv, abgesagt = termine_aus(ev)
    assert [(t.uid, t.datum, t.uhrzeit) for t in aktiv] == [("u1", date(2026, 11, 12), "14:30")]
    assert abgesagt == {"u2"}

def test_termin_notiz_pfad_kopf_und_text():
    t = Termin("l1", "u1abcdef", date(2026, 11, 12), "14:30", 45, "Erstgespraech", "https://meet.jit.si/raum")
    soll = {s.schluessel: s for s in termine_soll("Vibemind", [t], PFADE, NAMEN, heute=HEUTE, archiv_vorhanden=False)}
    notiz = soll["termin:l1:u1abcdef"]
    assert notiz.rel == "Termine/2026-11-12 1430 Anna Muster.md"
    assert notiz.inhalt.startswith("---\n")
    assert 'parent: "[[companys/Vibemind/Termine/_overview]]"' in notiz.inhalt
    assert 'kontakt: "[[companys/Vibemind/Kontakte/Kunden/Anna Muster]]"' in notiz.inhalt
    assert "kontakt_id: l1" in notiz.inhalt and "termin_uid: u1abcdef" in notiz.inhalt
    assert "Erstgespraech" in notiz.inhalt and "(https://meet.jit.si/raum)" in notiz.inhalt
    assert "nicht bearbeiten" in notiz.inhalt
    ueb = soll["termine:uebersicht"]
    assert ueb.rel == "Termine/_overview.md"
    assert 'parent: "[[companys/Vibemind/Bereiche/_overview]]"' in ueb.inhalt
    assert "[[companys/Vibemind/Termine/2026-11-12 1430 Anna Muster]]" in ueb.inhalt
    assert "/kalender" in ueb.inhalt

def test_uebersicht_nur_kommende_14_tage_und_zahl_vergangener():
    ts = [Termin("l1", "a", date(2026, 11, 1), "10:00", None, None, None),
          Termin("l1", "b", date(2026, 11, 22), "10:00", None, None, None),
          Termin("l1", "c", date(2026, 11, 23), "10:00", None, None, None)]
    ueb = {s.schluessel: s for s in termine_soll("Vibemind", ts, PFADE, NAMEN, heute=HEUTE, archiv_vorhanden=False)}["termine:uebersicht"]
    assert "2026-11-22 1000" in ueb.inhalt and "2026-11-23 1000" not in ueb.inhalt
    assert "Vergangene Termine: 1" in ueb.inhalt

def test_gleicher_name_bekommt_uid_zusatz():
    ts = [Termin("l1", "aaaaaaaa1", date(2026, 11, 12), "14:30", None, None, None),
          Termin("l1", "bbbbbbbb2", date(2026, 11, 12), "14:30", None, None, None)]
    rels = sorted(s.rel for s in termine_soll("Vibemind", ts, PFADE, NAMEN, heute=HEUTE, archiv_vorhanden=False))
    assert "Termine/2026-11-12 1430 Anna Muster (bbbbbbbb).md" in rels

def test_ohne_steckbrief_kein_termin_und_ohne_termine_keine_uebersicht():
    t = Termin("fremd", "u9", date(2026, 11, 12), None, None, None, None)
    assert termine_soll("Vibemind", [t], PFADE, NAMEN, heute=HEUTE, archiv_vorhanden=False) == []

def test_termin_fremdtext_neutralisiert():
    t = Termin("l1", "u1", date(2026, 11, 12), None, None, "Thema\n[[Geheim]] @rowboat los", "javascript:alert(1)")
    inhalt = termine_soll("Vibemind", [t], PFADE, NAMEN, heute=HEUTE, archiv_vorhanden=False)[0].inhalt
    assert "[[Geheim]]" not in inhalt and "@rowboat" not in inhalt and "\nlos" not in inhalt
    assert "](javascript:" not in inhalt   # nur https-Orte werden Link

def test_gesperrte_termin_schluessel():
    ist = ["termin:l1:u1", "termin:l2:u2", "termin:l3:u3", "l1", "termine:uebersicht"]
    assert gesperrte_termin_schluessel(ist, {"l2"}, {"u3"}) == {"termin:l2:u2", "termin:l3:u3"}
```

(Uhrzeit fehlt → Stamm `2026-11-12 <Name>`; Name fehlt → `Kontakt <id8>`; Dateistamm über `dateistamm`, Eindeutigkeit über `eindeutig` mit `uid` als Schlüssel.)

- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern**

Run: `python -m pytest spaces/rowboat/tests/test_geschaeftsdaten_termine.py -q`
Expected: FAIL (Modul fehlt).

- [ ] **Step 3: `termine.py` umsetzen und `steckbrief._offene_termine` auf `termine_aus` umstellen**

Fremdtext (Thema, Ort, Name) wird einzeilig. Aus `[[`/`]]` wird `[`/`]`, Auslöser werden wie in `steckbrief.py` neutralisiert, das Thema hat höchstens 200 Zeichen. Der Ort wird nur zum Markdown-Link `[Jitsi-Raum](<url>)`, wenn er mit `https://` beginnt und kein Leerzeichen enthält, sonst steht er als neutralisierter Text da. Die Übersicht listet die Termine der nächsten 14 Tage (`heute` bis `heute + 13`), nach Datum und Uhrzeit sortiert, und nennt danach die Zahl der vergangenen Termine.

- [ ] **Step 4: Tests laufen lassen, sie müssen bestehen**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS, die Steckbrief-Tests bleiben unverändert grün.

- [ ] **Step 5: Commit**

```bash
git add spaces/rowboat/geschaeftsdaten/termine.py spaces/rowboat/geschaeftsdaten/steckbrief.py spaces/rowboat/tests
git commit -m "feat(rowboat): Termin-Notizen und Termin-Uebersicht je Firma"
```

---

### Task 5: Termine im nächtlichen Lauf (anlegen, archivieren, löschen)

**Files:**
- Modify: `spaces/rowboat/geschaeftsdaten/ablage.py` (`bestand`, `_ist_kontakt`, `_im_archiv`, `_archivziel`, `planen`-Docstring)
- Modify: `spaces/rowboat/geschaeftsdaten/lauf.py` (`_firma_soll`, `_archiv_text`, `_massenschutz`, `lauf`)
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_lauf.py`, `spaces/rowboat/tests/test_geschaeftsdaten_ablage.py`

**Interfaces:**
- Consumes: `termine_aus`, `termine_soll`, `gesperrte_termin_schluessel`, `TERMIN_PRAEFIX` (Task 4).
- Produces:
  - `ablage.bestand` scannt zusätzlich `("Termine", True)`.
  - `ablage.ARCHIV_ORDNER` bleibt `"Kontakte/_archiv"`, dazu kommt `TERMIN_ARCHIV = "Termine/_archiv"`. Archiviert werden Dateien unter `Kontakte/` und `Termine/` ohne Soll, außer `Termine/_overview.md`, jeweils in den `_archiv`-Ordner ihres eigenen Bereichs.
  - `lauf._steckbrief_pfade(firma: Path, gruppe: list[_Gelesen]) -> tuple[dict[str, str], dict[str, str], set[str], int]` liefert (pfade, namen, gesperrte_ids, uebersprungen). Die Funktion wird aus `_firma_soll` herausgezogen, das Verhalten bleibt gleich; `_firma_soll` und Task 7 nutzen sie.
  - `lauf._termine_fuer_firma(firma: Path, gruppe: list[_Gelesen], pfade: dict[str, str], namen: dict[str, str], *, heute: date) -> tuple[list[Soll], set[str]]` liefert (Soll, abgesagte uids). Task 7 nutzt sie ebenfalls.

- [ ] **Step 1: Failing Tests schreiben** (in `test_geschaeftsdaten_lauf.py`, mit den vorhandenen Fixtures `LAEDEN`, `HEUTE`, `FakeQuelle`)

```python
def test_termin_notiz_entsteht_am_steckbrief(tmp_path):
    q = FakeQuelle(kontakte={"sales": [kontakt(id="l1", name="Anna Muster")]},
                   ereignisse={"sales": [ereignis(lead_id="l1", typ="termin", termin_uid="u1",
                                                  termin_datum="2026-10-28", termin_uhrzeit="10:00",
                                                  termin_thema="Erstgespraech")]})
    _lauf(q, tmp_path)
    notiz = next((_firma(tmp_path) / "Termine").glob("2026-10-28 1000 *.md"))
    assert "kontakt: \"[[companys/" in notiz.read_text(encoding="utf-8")

def test_termin_abgesagt_wird_nachts_entfernt(tmp_path): ...       # erst anlegen, dann termin_abgesagt -> Datei weg
def test_termin_verschwunden_wird_archiviert(tmp_path): ...         # Ereignis fehlt, Kontakt da -> Termine/_archiv/<stamm>.md mit ARCHIV_VERMERK, parent bleibt Termine/_overview
def test_termin_gesperrter_kontakt_wird_entfernt(tmp_path): ...     # kontakt(gesperrt=True), keine Ereignisse -> Notiz entfernt, nicht archiviert
def test_termin_massenschutz_greift_getrennt(tmp_path): ...         # 10 Termine verschwinden bei 10 aktiven -> nichts archiviert, "Massenarchivierung <Firma>" in uebersprungen
def test_termin_verschieben_bei_neuem_pfad(tmp_path): ...           # Uhrzeit aendert sich -> nachts verschoben, nicht doppelt
```

Die Tests mit `...` sind ganz auszuschreiben. Muster sind die vorhandenen Steckbrief-Archiv-Tests derselben Datei, die Erwartung steht jeweils im Kommentar.

- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern**

Run: `python -m pytest spaces/rowboat/tests/test_geschaeftsdaten_lauf.py -q -k termin`
Expected: FAIL.

- [ ] **Step 3: Umsetzen**

- `_firma_soll`:
  - nutzt `_steckbrief_pfade`;
  - hängt `_termine_fuer_firma(...)`-Soll an;
  - liefert neu `(soll, gesperrt, uebersprungen, abgesagt)`. Das vierte Feld `abgesagt: set[str]` sind die abgesagten uids; der Aufruf in `lauf` wird mit angepasst.
- In `lauf` steht nach `ist = bestand(firma)`: `gesperrt |= gesperrte_termin_schluessel((i.schluessel for i in ist), gesperrt_ids, abgesagt)`. Die Kontakt-Schlüssel in `gesperrt` sind die Lead-IDs (heutiger Stand); nur sie gehen als `gesperrte_ids` hinein.
- `archiv_vorhanden` = `Termine/_archiv` enthält mindestens eine `.md`-Datei (kein Symlink).
- `_archiv_text`:
  - Für `Kontakte/` bleibt alles wie heute.
  - Für `Termine/` kommt nur `ARCHIV_VERMERK` dazu. Parent und Kopf bleiben unverändert.
- `_massenschutz`:
  - Kontakte und Termine werden getrennt gezählt, mit denselben Schwellen. Aktive Termine sind Dateien unter `Termine/` außerhalb von `_archiv` und ohne `_overview`.
  - `weg` zählt Archivierungen und nicht gesperrte Entfernungen mit `TERMIN_PRAEFIX`.
  - Ergebnis ist `True`, wenn einer der beiden Bereiche greift.

- [ ] **Step 4: Tests laufen lassen, sie müssen bestehen**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS, auch der Vertragstest für die neuen Notizen.

- [ ] **Step 5: Commit**

```bash
git add spaces/rowboat/geschaeftsdaten spaces/rowboat/tests
git commit -m "feat(rowboat): naechtlicher Lauf pflegt Termin-Notizen mit Archiv und Massen-Waechter"
```

---

### Task 6: Notiz „Zu beantworten“ (`post.py`)

**Files:**
- Create: `spaces/rowboat/geschaeftsdaten/post.py`
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_post.py`

**Interfaces:**
- Consumes: `PostMail`, `Quelle.post_offen` (Task 3), `env_werte` (`laeden.py`), `_eigen`/`_schreiben_atomar` (`ablage.py`), `ERZEUGT_VON`.
- Produces:
  - `SALES_UI = "https://vibemind-offload-1.tail6c7d61.ts.net"`, `MAX_PUNKTE = 50`, `STAND_PRAEFIX = "Stand: "`
  - `stichtag_lesen(env_datei: Path) -> date | None`: liest nur `POST_AUFRAEUMEN_AB`. Fehlt die Datei, der Schlüssel oder ist das Datum kaputt, gibt sie `None` zurück.
  - `post_notizen(mails: list[PostMail], steckbriefe: dict[str, str], *, jetzt: datetime, stichtag: date | None, company_da: bool) -> dict[str, tuple[str, str]]`. Das Ergebnis geht von `rel ab knowledge/` auf `(schluessel, inhalt)`, und zwar für `Post/_overview.md` (`post:uebersicht`) und `Post/Zu beantworten.md` (`post:zu-beantworten`). `steckbriefe` geht von `email_klein` auf den Steckbrief-Pfad ab `knowledge/` (nur nicht gesperrte Kontakte aus Laden `sales`).
  - `post_schreiben(wissen: Path, notizen: dict[str, tuple[str, str]], *, trocken: bool) -> dict[str, int]` liefert die Zähler `geschrieben`, `unveraendert`, `fremd`.

- [ ] **Step 1: Failing Tests schreiben**

```python
JETZT = datetime(2026, 11, 11, 9, 0, tzinfo=ZoneInfo("Europe/Berlin"))
ST = date(2026, 11, 8)

def _zu(mails, **kw):
    n = post_notizen(mails, kw.pop("steckbriefe", {}), jetzt=JETZT, stichtag=kw.pop("stichtag", ST), company_da=False)
    return n["Post/Zu beantworten.md"][1]

def test_punkt_mit_alter_postfach_absender_betreff_und_links():
    m = post_mail(id="m1", datum="2026-11-08T08:00:00+00:00", postfach="gmx", von_anzeige="Anna Muster",
                  absender_klein="anna@example.org", betreff="Angebot")
    text = _zu([m], steckbriefe={"anna@example.org": "companys/Vibemind/Kontakte/Kunden/Anna Muster.md"})
    assert "- seit 3 Tagen · gmx · Anna Muster · „Angebot“" in text
    assert "[[companys/Vibemind/Kontakte/Kunden/Anna Muster]]" in text
    assert "/post/mail/m1)" in text
    assert "Offen: 1" in text and "/posteingang" in text

def test_sensibel_ohne_absender_und_betreff():
    text = _zu([post_mail(sensibel=True, betreff=None, von_anzeige="Bank", absender_klein="noreply@bank.de")])
    assert "vertrauliche Mail" in text and "Bank" not in text and "bank.de" not in text

def test_gesperrter_absender_ohne_name_aber_mitgezaehlt():
    text = _zu([post_mail(absender_gesperrt=True, von_anzeige=None, absender_klein=None, betreff=None), post_mail(id="m2")])
    assert "Mail eines gesperrten Kontakts" in text and "Offen: 2" in text

def test_gesperrt_gewinnt_vor_steckbrief_link():
    # auch wenn die Sicht wider Erwarten eine Adresse liefert: gesperrt heisst kein Name, kein Link
    m = post_mail(absender_gesperrt=True, absender_klein="anna@example.org", von_anzeige="Anna")
    text = _zu([m], steckbriefe={"anna@example.org": "companys/Vibemind/Kontakte/Kunden/Anna Muster.md"})
    assert "[[companys" not in text and "anna@" not in text and "Anna" not in text

def test_ohne_stichtag_nichts_offen():
    assert "Nichts offen" in _zu([post_mail()], stichtag=None)

def test_stichtag_kaputt_gilt_als_fehlend(tmp_path):
    (tmp_path / ".env").write_text("ANDERES=geheim\nPOST_AUFRAEUMEN_AB=08.11.2026\n", encoding="utf-8")
    assert stichtag_lesen(tmp_path / ".env") is None

def test_obergrenze_50():
    mails = [post_mail(id=f"m{i:03}", datum=f"2026-11-09T{i % 24:02}:00:00+00:00") for i in range(53)]
    text = _zu(mails)
    assert text.count("\n- ") == 50 and "… und 3 weitere in sales-ui" in text

def test_betreff_fremdtext_neutralisiert():
    text = _zu([post_mail(betreff="Hallo\n[[Geheim]] @rowboat")])
    assert "[[Geheim]]" not in text and "@rowboat" not in text

def test_kein_schreiben_ohne_inhaltliche_aenderung(tmp_path):
    n1 = post_notizen([post_mail()], {}, jetzt=JETZT, stichtag=ST, company_da=False)
    n2 = post_notizen([post_mail()], {}, jetzt=JETZT + timedelta(hours=1), stichtag=ST, company_da=False)
    assert post_schreiben(tmp_path, n1, trocken=False)["geschrieben"] == 2
    assert post_schreiben(tmp_path, n2, trocken=False) == {"geschrieben": 0, "unveraendert": 2, "fremd": 0}

def test_fremde_datei_bleibt(tmp_path):
    (tmp_path / "Post").mkdir()
    (tmp_path / "Post" / "Zu beantworten.md").write_text("meine Notiz\n", encoding="utf-8")
    z = post_schreiben(tmp_path, post_notizen([], {}, jetzt=JETZT, stichtag=ST, company_da=False), trocken=False)
    assert z["fremd"] == 1 and (tmp_path / "Post" / "Zu beantworten.md").read_text(encoding="utf-8") == "meine Notiz\n"
```

- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern**

Run: `python -m pytest spaces/rowboat/tests/test_geschaeftsdaten_post.py -q`
Expected: FAIL (Modul fehlt).

- [ ] **Step 3: `post.py` umsetzen**

- **Alter:** Tage zwischen dem Datum der Mail und `jetzt`, beide in Europe/Berlin. Ausgabe „seit heute“, „seit 1 Tag“ oder „seit N Tagen“.
- **Absender:** `von_anzeige`, sonst `absender_klein`. Die Mailadresse bleibt hier stehen, anders als bei `_einzeilig`.
- **Betreff:** in „…“, höchstens 120 Zeichen. Neutralisiert wie in Task 4.
- **Kopf `Zu beantworten`:**
  - `parent: "[[Post/_overview]]"`
  - danach eine Zeile `Stand: JJJJ-MM-TT HH:MM` (Berlin), `Offen: N` und der Link „alle in sales-ui“.
- **`_overview`:**
  - `parent: "[[Company]]"` nur bei `company_da`
  - Link auf `[[Post/Zu beantworten]]`
- **Vergleich in `post_schreiben`:** Zeilen mit `STAND_PRAEFIX` werden vor dem Vergleich herausgenommen.
- **Eigentum:** über `ablage._eigen`. Ist die Datei da, aber nicht unsere, zählt sie als `fremd` und wird nicht geschrieben.
- **Neue Ordner:** mit `mkdir(parents=True, exist_ok=True)`.

- [ ] **Step 4: Tests laufen lassen, sie müssen bestehen**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spaces/rowboat/geschaeftsdaten/post.py spaces/rowboat/tests
git commit -m "feat(rowboat): Notiz 'Zu beantworten' aus offener Post"
```

---

### Task 7: Stündlicher Einstieg, gemeinsame Sperre, Timer

**Files:**
- Create: `spaces/rowboat/geschaeftsdaten/sperre.py`, `spaces/rowboat/geschaeftsdaten/stuendlich.py`
- Create: `spaces/rowboat/geschaeftsdaten/deploy/rowboat-post-termine.service`, `…/rowboat-post-termine.timer`
- Modify: `spaces/rowboat/geschaeftsdaten/lauf.py` (`main` nimmt die Sperre), `spaces/rowboat/geschaeftsdaten/README.md`
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_stuendlich.py`

**Interfaces:**
- Consumes: `_steckbrief_pfade`, `_termine_fuer_firma`, `_lesen`-Muster und `pruefen` aus `lauf.py`/`laeden.py`; `post_notizen`, `post_schreiben`, `stichtag_lesen` (Task 6); `planen`, `ausfuehren`, `bestand` (`ablage.py`).
- Produces:
  - `sperre.py`: `SPERRE_STANDARD = "/home/debian/rowboat-geschaeftsdaten/lauf.lock"`. Dazu `@contextmanager def sperre(pfad: Path, *, warten_s: float) -> Iterator[bool]`. Sie liefert `True`, wenn sie die Sperre innerhalb von `warten_s` bekommt (`fcntl.flock`, `LOCK_EX | LOCK_NB` im Abstand von 1 s); `warten_s=0` bedeutet einen einzigen Versuch. Ohne `fcntl` (Windows-Entwicklung) liefert sie `True`.
  - `stuendlich.stunde(quelle: Quelle, laeden: list[Laden], wissen: Path, *, jetzt: datetime, stichtag: date | None, trocken: bool, gestoert: frozenset[str]) -> Ergebnis`
  - `stuendlich.main(argv: list[str] | None = None) -> int`: Optionen `--trocken` und `--jetzt` (ISO, für Tests). Es gelten dieselben Umgebungsvariablen wie bei `lauf.main`, dazu `GESCHAEFTSDATEN_SPERRE`.

- [ ] **Step 1: Failing Tests schreiben**

```python
def test_stuendlich_legt_termin_an_loescht_aber_nie(tmp_path): ...
    # Bestand: eigene Termin-Notiz "termin:l1:alt" ohne Ereignis + abgesagter Termin "termin:l1:u2"
    # nach stunde(): beide Dateien noch da, neue Notiz fuer u1 geschrieben
def test_stuendlich_neuer_pfad_wird_neu_geschrieben_alte_bleibt(tmp_path): ...
def test_stuendlich_schreibt_post_notiz_nur_fuer_laden_sales(tmp_path): ...
    # Steckbrief-Link nur fuer Kontakt aus Laden "sales"; gleicher Absender als Kontakt in sales_ivan -> kein Link
def test_stuendlich_db_fehler_schreibt_nichts(tmp_path): ...      # FakeQuelle wirft DbFehler -> exit_code 2, keine Datei
def test_stuendlich_setzt_bei_sperre_aus(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    lock = tmp_path / "lauf.lock"
    with open(lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        monkeypatch.setenv("GESCHAEFTSDATEN_SPERRE", str(lock))
        monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(tmp_path))
        assert stuendlich.main(["--trocken"]) == 0   # setzt aus, ohne die Quelle anzufassen
def test_sperre_zweiter_versuch_scheitert(tmp_path):
    pytest.importorskip("fcntl")
    with sperre(tmp_path / "l", warten_s=0) as a, sperre(tmp_path / "l", warten_s=0) as b:
        assert a is True and b is False
```

Die Tests mit `...` sind ganz auszuschreiben, die Erwartung steht jeweils im Kommentar. Die Fixtures sind die aus `test_geschaeftsdaten_lauf.py`.

- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern**

Run: `python -m pytest spaces/rowboat/tests/test_geschaeftsdaten_stuendlich.py -q`
Expected: FAIL.

- [ ] **Step 3: Umsetzen**

- **`stunde`:**
  - Je Laden liest sie nur `kontakte`, `ereignisse` und einmal `post_offen(stichtag)`. Das geschieht nur, wenn `stichtag` gesetzt ist; sonst bleibt die Liste leer.
  - Alle Lesungen kommen vor dem ersten Schreiben. Bei `DbFehler` ist der Exit 2 und es wird nichts geschrieben.
  - Gestörte oder übersprungene Marken werden wie in `lauf` nicht angefasst.
- **Je Firma:** `planen(termine_soll, [i for i in bestand(firma) if i.rel.startswith("Termine/")], gesperrt, …)`. Ausgeführt wird nur `Plan(schreiben=plan.schreiben + [s for _, s in plan.verschieben], verschieben=[], archivieren=[], entfernen=[], unveraendert=plan.unveraendert)`.
- **Post:** `steckbriefe` stammt aus den `pfade` der Firma, deren Laden `sales` ist. Danach folgen `post_notizen` und `post_schreiben`.
- **`stuendlich.main`:** nimmt `sperre(..., warten_s=0)`. Ist die Sperre nicht frei, loggt es „Geschaeftsdaten-Lauf aktiv, Stunde ausgesetzt“ und gibt 0 zurück.
- **`lauf.main`:** nimmt dieselbe Sperre mit `warten_s=600`. Ist sie dann nicht frei, gibt es Exit 1 mit Log und schreibt `stand.json` mit `fehler="Sperre"`.
- **Units:** `rowboat-post-termine.service`, Kopie von `rowboat-geschaeftsdaten.service` mit `ExecStart=/usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/geschaeftsdaten/stuendlich.py` und `TimeoutStartSec=10min`. Der Timer bekommt `OnCalendar=*-*-* *:05:00` und `Persistent=false`.
- **README:** neuer Abschnitt „Stündlich: Post und Termine“. Er erklärt, was der Lauf tut und was nicht (nie löschen), die Sperre, die Befehle (`--trocken`, `systemctl start`, `journalctl`), das Einrichten und den Rückweg.

- [ ] **Step 4: Tests laufen lassen, sie müssen bestehen**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS. Die `fcntl`-Tests werden unter Windows übersprungen; die Zahl der Skips steigt um höchstens 2.

- [ ] **Step 5: Commit**

```bash
git add spaces/rowboat/geschaeftsdaten spaces/rowboat/tests
git commit -m "feat(rowboat): stuendlicher Lauf fuer Post und Termine mit gemeinsamer Sperre"
```

---

### Task 8: Wissens-Spiegel: Termine, Bereiche und Post gehören der VM

**Files:**
- Modify: `spaces/rowboat/scripts/wissens_spiegel.py:98` (`VM_FIRMENORDNER`)
- Test: `spaces/rowboat/tests/test_wissens_spiegel.py` (bei `test_besitzer`)

**Interfaces:**
- Produces: `VM_FIRMENORDNER = frozenset({"Kontakte", "Newsletter", "Wochenbild", "Bereiche", "Termine"})`. `Post/` gehört schon heute der VM (nicht in `PC_ORDNER`), der Test hält das fest.

- [ ] **Step 1: Failing Test schreiben**

```python
def test_besitzer_post_termine_bereiche_vm():
    assert besitzer("companys/Vibemind/Termine/2026-11-12 1430 Anna Muster.md") == "vm"
    assert besitzer("companys/Vibemind/Termine/_archiv/x.md") == "vm"
    assert besitzer("companys/Vibemind/Bereiche/_overview.md") == "vm"
    assert besitzer("Post/Zu beantworten.md") == "vm"
    assert besitzer("companys/Vibemind/Notizen/frei.md") == "pc"
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `python -m pytest spaces/rowboat/tests/test_wissens_spiegel.py -q -k post_termine`
Expected: FAIL (Termine und Bereiche gehören noch dem `pc`).

- [ ] **Step 3: `VM_FIRMENORDNER` ergänzen**

- [ ] **Step 4: Tests laufen lassen, sie müssen bestehen**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spaces/rowboat/scripts/wissens_spiegel.py spaces/rowboat/tests/test_wissens_spiegel.py
git commit -m "fix(rowboat): Wissens-Spiegel - Termine und Bereiche gehoeren der VM"
```

---

### Task 9: Fork-Stand übernehmen und PR in vibemind-os

**Voraussetzung:** Fork-PR aus Task 1 ist gemergt.

**Files:**
- Modify (nur per Skript): `spaces/rowboat/apps/x/**`, Pin in `apps/renderer/src/lib/rowboat-plugin-api.ts`

- [ ] **Step 1: Sync**

Run:
```bash
python spaces/rowboat/scripts/sync_desktop.py --fork C:/Users/User/ClaudeWork/wt-rowboat-vibemind --fork-ref <vibemind-Merge-Commit> --ehl <EHL-Clone> --ehl-ref <aktueller EHL-Pin>
```
Den EHL-Pin aus der Zeile `- EHL commit:` in `rowboat-plugin-api.ts` übernehmen, damit `schema/` unverändert bleibt.
Expected: Das Skript meldet nur Änderungen unter `apps/x` und am Pin. Bricht es ab, weil eine Kopie von Hand geändert wurde, wird nichts erzwungen; dann Felix fragen.

- [ ] **Step 2: Tests**

Run: `python -m pytest spaces/rowboat/tests -q`
Expected: PASS, auch der Catalog-Pin-Guard.

- [ ] **Step 3: Commit, Push, PR**

```bash
git add spaces/rowboat
git commit -m "chore(rowboat): Desktop-Kopie auf Fork <kurz-sha> (Mail-Tab aus)"
git push -u origin feat/rowboat-post-termine
gh pr create --repo Flissel/vibemind-os --base master --title "feat(rowboat): offene Post und Kundentermine im Wissensbaum, Mail-Tab aus" --body "<Zusammenfassung, Spec/Plan-Links, Testzahlen, 🤖-Zeile>"
```

Danach den PR mit `mcp__ccd_pr__get_status` / `bind_pr` binden und erst nach Felix' OK mergen.

---

### Task 10: Ausrollen auf der VM und Abnahme

Alle Schritte erst nach dem Merge von Task 9. Vorher kommt der Claim `cc-post-termine` in `002_Koordination_Live.md` (VM, Datenbank, systemd) und in `WORKBOARD.md`, jeweils sofort committet. Ist dort ein fremder Claim aktiv, wird nichts angefasst und Felix gefragt.

- [ ] **Step 1: Schema-Sicherung**, dann SQL einspielen

Run (VM):
```bash
docker exec debian-supabase-db-1 pg_dump -U supabase_admin -s -n sales > ~/backups/schema-sales-vor-post-termine-$(date +%F-%H%M).sql
cd ~/marketing-os && git pull --ff-only
docker exec -i debian-supabase-db-1 psql -v ON_ERROR_STOP=1 -U supabase_admin -d postgres < spaces/rowboat/geschaeftsdaten/db/rowboat_leser.sql
docker exec -i debian-supabase-db-1 psql -v ON_ERROR_STOP=1 -U supabase_admin -d postgres < spaces/rowboat/geschaeftsdaten/db/verify_rowboat_leser.sql
```
Expected: Beide Skripte laufen ohne Fehler; verify meldet alle Prüfungen ok.

- [ ] **Step 2: Trockenläufe**

Run: `python3 ~/marketing-os/spaces/rowboat/geschaeftsdaten/stuendlich.py --trocken` und `python3 ~/marketing-os/spaces/rowboat/geschaeftsdaten/lauf.py --trocken`
Expected: Exit 0 oder 1 nur mit bekannten Warnungen. Der stündliche Lauf nennt die Zahl offener Mails und je Firma geplante Termin-Notizen; nichts wird entfernt.

- [ ] **Step 3: Units einrichten und ersten echten Lauf starten**

```bash
sudo cp ~/marketing-os/spaces/rowboat/geschaeftsdaten/deploy/rowboat-post-termine.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rowboat-post-termine.timer
sudo systemctl start rowboat-post-termine.service && journalctl -u rowboat-post-termine -n 20 --no-pager
```

- [ ] **Step 4: Abnahme Daten**

- Die Zahl in `Post/Zu beantworten.md` (`Offen: N`) ist gleich der Zahl offener Mails in sales-ui `/posteingang`. Felix prüft das im Browser; ich nenne beide Zahlen.
- Termin-Notizen liegen unter `companys/<Firma>/Termine/` und tragen `kontakt: "[[…]]"`.
- Ein zweiter Start des Dienstes meldet `geschrieben=0`.
- `python3 ~/marketing-os/spaces/rowboat/wissens_pruefung/pruefung.py --trocken` zeigt keine neuen losen Notizen (Stand vorher: 4).

- [ ] **Step 5: PC nachziehen und Einbettung neu bauen**

```bash
git -C C:/Users/User/ClaudeWork/vibemind-betrieb fetch origin && git -C C:/Users/User/ClaudeWork/vibemind-betrieb checkout --detach origin/master
python C:/Users/User/ClaudeWork/vibemind-betrieb/spaces/rowboat/scripts/start_desktop.py --build-embed --env-file C:/Users/User/Desktop/Vibemind_V1/.env
```
Danach Rowboat über die geplante Aufgabe „VibeMind Rowboat (Einbettung)“ neu starten, der Wächter übernimmt.
Expected: In Rowboat fehlt der Eintrag „Email“. Ein Copilot-Auftrag „zeig mir meine Mails“ landet auf der Startseite. `Post/Zu beantworten` und die Termin-Notizen erscheinen im Baum und im Graph am Steckbrief.

- [ ] **Step 6: Claims schließen**, Ergebnis in `002_Koordination_Live.md` und `WORKBOARD.md` eintragen und committen.
