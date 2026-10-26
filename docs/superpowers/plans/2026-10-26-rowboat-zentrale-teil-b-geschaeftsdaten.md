# Rowboat-Zentrale Teil B – Geschäftsdaten in Rowboat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein nächtlicher Lauf auf dem Mini-PC liest sales-claw und Marketing nur lesend und legt je Firma Steckbriefe (`Kontakte/`), ein Newsletter-Archiv (`Newsletter/`) und ein Wochenbild (`Wochenbild/`) als Markdown in Rowboats Wissen unter `knowledge/companys/<Firma>/` ab.

**Architecture:** Ein reines Python-Paket ohne Fremdabhängigkeiten (`spaces/rowboat/geschaeftsdaten/`, nur stdlib plus die stdlib-reinen Textrenderer aus `spaces.marketing.claw`) läuft als systemd-Timer (`User=debian`, uid 1000 = `node` im Rowboat-Container) aus dem schlanken vibemind-os-Checkout `~/marketing-os` auf `offload-vm`. Es fragt die Datenbank über `docker exec debian-supabase-db-1 psql -h 127.0.0.1 -U rowboat_leser` ab. Die neue Rolle `rowboat_leser` hat kein Passwort, ist nur über die `trust`-Regel für 127.0.0.1 im Container erreichbar, ist read-only und sieht nur eigens gebaute Sichten. Diese Sichten geben für private Kontakte und Kontakte mit Löschantrag nur `id` und Sperrflag heraus und enthalten weder Nachrichtentexte noch Notizen noch Telefon. Der Lauf rechnet erst alles im Speicher. Danach schreibt er nur die Dateien atomar, deren Inhalt sich geändert hat. Verschiebungen laufen in Paketen mit Pausen, damit die Löschgrenze des Wissens-Spiegels (20 je Lauf) nie greift.

**Tech Stack:** Python 3.11 (VM: `/usr/bin/python3` 3.11.2, stdlib, `zoneinfo`), PostgreSQL 17 (Supabase-Container), systemd-Timer, pytest am PC.

**Spec:** `docs/superpowers/specs/2026-10-24-rowboat-geschaeftsdaten-zentrale-design.md` (Ziel 4, Anti-Ziele, Abschnitt 7, Abschnitt 8 „Format/Inhalt der Steckbriefe“). Vorgänger: `docs/superpowers/plans/2026-10-24-rowboat-zentrale-teil-a-mini-pc.md` (Wissens-Spiegel, Besitzer-Tabelle). Nachfolger, der diese Dateien indexiert: `docs/superpowers/plans/2026-10-26-rowboat-zentrale-teil-c-bedeutungssuche.md`.

## Erkundung (26.10.2026, nur lesend, nur Struktur und Zahlen)

| Befund | Quelle |
|---|---|
| Laden-Schemata live: `sales` (577 Kontakte), `sales_ivan` (1), `sales_test` (nie). 17 `_privat`, 0 `_loeschantrag`, 8 archiviert, 3 mit `source='system'` | `pg_namespace`, `sales.leads` |
| Erster Bereich: `bav` 477, `it` 1, keiner 99 → Ordner `bAV`, `IT-Lösung`, `_ohne` | `enrichment->'bereiche'->>0` |
| 4 Namen kommen (ohne Groß-/Kleinschreibung) doppelt vor, längster Name 86 Zeichen | `sales.leads` |
| Laden → Marke steht in den Env-Dateien, nicht in der DB: `~/sales-claw/.env` hat `MARKETING_MANDANT=vibemind` und kein `SALES_DB_SCHEMA` (also `sales`). `deploy/laeden/ivan.env` hat `SALES_DB_SCHEMA=sales_ivan` und `LADEN_MARKE=fin2gether`. **`beispiel.env` trägt ebenfalls `sales_ivan`** und ist eine Vorlage. `marketing.mandanten.verteiler` von fin2gether ist leer, taugt also nicht als Zuordnung | VM-Env (nur Schlüsselnamen gelesen) |
| Mandant → Ordnername: `marketing.mandanten.name` (`VibeMind`, `fin2gether`). Die Ordner `companys/VibeMind` und `companys/fin2gether` gibt es schon (Besitzer `debian`) | DB, `~/rowboat-data/knowledge` |
| Sammelkontakte: IDs in `INBOX_UNBEKANNT_LEAD_ID`, `RECHERCHE_LEAD_ID`, `LINKEDIN_POST_LEAD_ID` je Laden-Env (sales: alle drei gesetzt, ivan: nur der erste) | `server._sammelkontakt` |
| Privat: `server._privat` (Objekt und `privat` nicht `false`). Archiv: `server._archiv_sql`. Löschantrag: `_loeschantrag` (jedes Objekt) | `sales-mcp/server.py:1078-1163` |
| Termine sind Aktivitäten `termin` (`uid`, `datum`, `uhrzeit`, `thema`, `ort`) und `termin_abgesagt` (`uid`). Empfehlungen sind Aktivitäten `empfehlung` (`liste_id`, `empfohlen_von_name`) und `empfehlungslisten.empfohlen_von_lead` | `activities`, `empfehlungen.py:235` |
| Versendete Newsletter: `sales.drafts.newsletter` = `marketing.inhalte.id`, `status='sent'` (heute 1 Newsletter, 2 Mails). Inhalt in `marketing.inhalt_fassungen` (`format` `bloecke`/`seite`/`felder`, `felder.betreff`). Der Klartext kommt aus `bloecke_mjml.text_fassung` bzw. `seite_mail.text_fassung`, beide nur stdlib. **Öffnungen werden für Newsletter an Kontakte nicht erfasst** (`email_opens` hängt an `campaigns`) | DB, `spaces/marketing/claw/` |
| Reels: `marketing.video_auftraege` (`fertig` 12). Podcasts: `marketing.ton_auftraege` | DB |
| Lese-Rolle: `supabase_read_only_user` ist bypassrls und liest alles. Für diesen Zweck ist sie zu breit, also wird `rowboat_leser` neu angelegt. `pg_hba`: `host all all 127.0.0.1 trust`, von außen `scram` | `pg_roles`, `pg_hba_file_rules` |
| Rowboat-Hintergrunddienste: NoteTagging scannt nur `People/Organizations/Projects/Topics/Meetings` und überspringt Dateien, die mit `---` beginnen. Kuration fasst nur `People/Organizations/Projects/Topics` an. InlineTasks greifen nur bei `live_note: true`, Live-Notes nur bei einem `live:`-Block. Agent-Notes lesen `gmail_sync/` und `runs/`, GraphBuilder liest Quellen außerhalb von `companys/` | `spaces/rowboat/rowboat/apps/x/packages/core/src/knowledge/` |
| Wissens-Spiegel: `companys/*/{Kontakte,Newsletter,Wochenbild}/…` gehört der VM. Er bricht ab, wenn ein Lauf mehr als 20 Dateien löscht (oder mehr als 10 %). Pfade über 240 Zeichen gleicht er nicht ab. Dateien auf `.tmp` ignoriert er | `scripts/wissens_spiegel.py` |
| `~/marketing-os` ist ein Sparse-Checkout (`spaces/marketing`) von vibemind-os `master`. `sales-claw/deploy/marketing-aktualisieren.sh` zieht ihn per ff-only nach. `spaces/rowboat/__init__.py` lässt sich nicht importieren (es fehlt `.config`), Module werden deshalb wie `wissens_spiegel` per `sys.path` geladen | VM, Repo |
| VM-Zeitzone UTC. Supabase-Sicherung läuft 03:19/04:15 UTC, sales-Sicherung 04:35 UTC | `timedatectl`, Timer |
| Freie Marketing-Migrationsnummer: **094** (WORKBOARD, `cc-youtube-post`) | `WORKBOARD.md` |

## Entscheidungen dieses Plans

| Frage | Entscheid |
|---|---|
| Wo der Lauf lebt | Code in `vibemind-os/spaces/rowboat/geschaeftsdaten/` (flache Module, Start als Datei `lauf.py`). Auf der VM liegt er im vorhandenen Checkout `~/marketing-os`, ergänzt per `git sparse-checkout add spaces/rowboat/geschaeftsdaten spaces/rowboat/scripts`. `update.sh` zieht ihn mit. systemd-Timer `rowboat-geschaeftsdaten.timer`, kein Container: kein Image und keine Abhängigkeiten, und Rowboat sieht dieselbe uid |
| DB-Zugang | `docker exec -i debian-supabase-db-1 psql -h 127.0.0.1 -U rowboat_leser -d postgres -v ON_ERROR_STOP=1 -At -f -`. Die SQL kommt über stdin, die Antwort ist eine JSON-Zeile (`coalesce(json_agg(t),'[]')`). Kein Passwort und kein Geheimnis im Spiel |
| Rolle und Rechte | Idempotentes Skript `spaces/rowboat/geschaeftsdaten/db/rowboat_leser.sql` (Muster `provision.sql`: Schleife über `^sales(_[a-z][a-z0-9_]{0,30})?$` ohne `sales_test`). Es baut je Laden drei Sichten (`rowboat_kontakte`, `rowboat_ereignisse`, `rowboat_newsletter`) und gibt Spalten-Rechte auf fünf Marketing-Tabellen. Rolle mit `default_transaction_read_only=on` und `statement_timeout=60s`. Ein neuer Laden braucht einen erneuten Lauf des Skripts. Fehlt eine Sicht, überspringt der Job diesen Laden und meldet es. Bewusst **ohne Nummer**, weil es Laden-Schemata betrifft, die kommen und gehen (siehe Betreiber-Entscheid 3) |
| Laden → Firma | Aus `~/sales-claw/.env` plus `~/sales-claw/deploy/laeden/*.env`, ohne `beispiel.env`. Gelesen werden nur die Schlüssel `SALES_DB_SCHEMA` (Vorgabe `sales`), `LADEN_MARKE`, sonst `MARKETING_MANDANT`, und die drei Sammel-IDs. CR wird entfernt (Memory „Env-CRLF leakt Schlüssel“). Ohne Marke wird der Laden übersprungen (fail-closed). Der Ordner ist ein vorhandenes `companys/<x>`, gefunden ohne Groß-/Kleinschreibung, sonst `companys/<mandanten.name>`. Ein Symlink wird nie benutzt |
| Wer einen Steckbrief bekommt | Jeder Kontakt, der nicht gesperrt ist (privat oder Löschantrag) und kein Sammelkontakt (Env-IDs ∪ `source='system'`). Archivierte Kontakte liegen unter `Kontakte/_archiv/`, ebenso Kontakte, die aus der Quelle verschwinden |
| Gesperrt nach früherem Export | Die erzeugte Datei wird **entfernt**, auch aus `_archiv/`. Das ist die einzige Löschung, und sie betrifft nur die eigene Kopie. Die Quelle bleibt in sales-claw (Betreiber-Entscheid 1) |
| Kategorie-Ordner | Erster bekannter Bereich in fester Reihenfolge (`server.BEREICHE`), sonst `_ohne`. Ordnernamen: `bAV`, `Mitarbeiter`, `Private Vorsorge und Versicherung`, `Immobilien`, `IT-Lösung`, `Homelab`. Unbekannte Bereichswerte zählen nicht (wie `_einordnung`) |
| Inhalt des Steckbriefs | Name, Art, Firma, Rolle (`title`), Bereiche, Pipeline-Stufe, Herkunft, letzter Kontakt (nur Datum und Richtung), offene Termine (nur Datum und Uhrzeit, höchstens 5), Newsletter-Einwilligung (ja/nein), Empfohlen von (Link oder Name), Archiv-Hinweis, „Siehe auch“-Link auf eine People-Notiz. **Nicht** enthalten: E-Mail, Telefon, `notes`, `profil`, `bedarf`, `vertraege`, `firma`-Volltext, Kontaktprofil, Nachrichtentexte, Termin-Thema und -Ort, Alter (Betreiber-Entscheid 2) |
| Frontmatter | `erzeugt_von: rowboat-geschaeftsdaten`, `quelle: sales-claw`, `schluessel: <lead-uuid \| nl:<uuid> \| kw:JJJJ-Wnn>`, `laden`, `art`, `schlagwoerter: [<art>, <weitere Bereiche>]` (Schlüsselname wie im Teil-C-Test), `stand: JJJJ-MM-TT`. Nie `live`, `live_note`, `curated_at`. Die Datei beginnt immer mit `---` |
| Kopfzeile | `> Automatisch erzeugt, Quelle sales-claw, Stand TT.MM.JJJJ. Nicht hier bearbeiten – der nächtliche Lauf überschreibt; eigene Anmerkungen in eine eigene Notiz.` |
| „Stand“ | Aus den **Daten**, nicht aus der Laufzeit: das jüngste Datum aus `updated_at` und den Ereignissen des Kontakts (Europe/Berlin). Sonst wäre jede Datei jede Nacht anders, und Spiegel wie Teil-C-Index würden nachts alle 560 Dateien bewegen |
| Änderungserkennung | sha256 des fertigen Inhalts gegen die Datei auf der Platte. Kein Zustandsfile: die Platte ist die Wahrheit, ein zweiter Lauf schreibt 0 Dateien |
| Eigene Dateien erkennen | Nur `.md` mit `erzeugt_von: rowboat-geschaeftsdaten` im Frontmatter. Alles andere in diesen Ordnern bleibt unberührt |
| Atomar | Schreiben nach `.<name>.gd.tmp` (der Spiegel ignoriert `.tmp`), dann `os.replace`. Eine Verschiebung schreibt erst das Ziel und entfernt dann die Quelle, und zwar nur, wenn ihr sha noch dem geplanten entspricht |
| Löschgrenze des Spiegels | Verschieben, Archivieren und Entfernen laufen in Paketen zu je 15. Zwischen zwei Paketen wartet der Lauf 360 s, das ist länger als der Spiegeltakt von 5 min |
| Namen | NFC. `<>:"/\|?*`, Steuerzeichen → Leerzeichen, Leerraum zusammengefasst, `. ` am Rand weg, reservierte Namen (`CON`, `COM1` …) bekommen `_`. Leer → `Kontakt <id8>`. Der Stamm wird so gekürzt, dass der ganze Pfad ab `knowledge/` die Prüfung `wissens_spiegel._windows_tauglich` besteht (≤ 240). Doppelte Namen (casefold) **je Firma über den ganzen `Kontakte/`-Baum**: der älteste Kontakt (`created_at`, `id`) behält den Namen, alle weiteren heißen `<Name> (<id8>)`. Newsletter mit gleichem Datum und Betreff bekommen ebenfalls `(<id8>)` |
| Newsletter | Je freigegebenem `inhalte.art='newsletter'` mit mindestens einem `sent`-Entwurf in einem Laden derselben Marke: `Newsletter/<JJJJ-MM-TT> <Betreff>.md`. Das Datum ist der erste Versand (Berlin), der Betreff `felder.betreff`, sonst `titel`. Inhalt: Betreff, Vorschautext, Empfängerzahl (Summe über die Läden), „Öffnungen: werden nicht erfasst“ und der Klartext der freigegebenen Fassung. `{vorname}` wird zu `<Vorname>`, `{abmeldelink}` entfällt |
| Wochenbild | Für die letzte **abgeschlossene** ISO-Woche (Berlin), nur wenn die Datei fehlt (`--wochenbild-neu` erzwingt). Der Montagslauf legt sie also an, und ein ausgefallener Montag wird nachgeholt. Inhalt: Pipeline je Stufe (Stand bei Erstellung), neue Kontakte, Stufenwechsel, Termine der Woche, Newsletter der Woche mit Link und Empfängerzahl, fertige Reels und Podcasts (Titel, Dauer). Nur Zahlen und Titel, keine Namen von Kontakten |
| Zeitplan | `OnCalendar=*-*-* 02:30:00 Europe/Berlin`, `Persistent=true`. Das liegt vor beiden Sicherungen, und montags um 02:30 ist die Vorwoche abgeschlossen |
| People-Link | Erst gleiche E-Mail (`email:` im Frontmatter von `People/*.md`, nur oberste Ebene, casefold), dann gleicher Dateistamm (casefold, NFC). Mehrdeutig heißt: kein Link. Die E-Mail dient nur dem Abgleich und wird nie geschrieben |
| Protokoll | journald. Nur Zählungen je Firma und Laden-Schema, Fehlertyp und `id8`. Nie Namen, Pfade unterhalb der Firma, E-Mails oder SQL-Antworten. Zusätzlich `~/rowboat-geschaeftsdaten/stand.json` mit Zeit, Ergebnis, Zählungen und übersprungenen Läden (für brain-devops) |
| Exit-Codes | 0 ok. 1 lokaler oder Konfig-Fehler bzw. Teilerfolg (ein Laden übersprungen). 2 DB-Fehler vor jedem Schreiben (dann ist nichts geschrieben) |
| Einzelauftrag | systemd `oneshot` verhindert einen Doppelstart. Von Hand nur `systemctl start` bzw. `--trocken`. Kein eigenes Lock (YAGNI) |

## Betreiber-Entscheide (vor Task 11 einholen)

1. **Später privat markierte Kontakte bzw. Kontakte mit Löschantrag:** Ihre bereits erzeugte Datei wird entfernt, auch aus `_archiv/`. Das ist die Empfehlung. Die Alternative wäre, sie ins `_archiv/` zu verschieben, aber dann blieben private Daten in Rowboat.
2. **Steckbrief-Umfang:** E-Mail, Telefon, Kontaktprofil (KI-Verdichtung der Gespräche), Vertrags- und Bedarfsdaten sowie Termin-Thema und -Ort bleiben draußen. Soll „Gib mir das Profil von X“ trotzdem das Kontaktprofil oder die E-Mail zeigen?
3. **Rollen-Skript ohne Nummer** unter `spaces/rowboat/geschaeftsdaten/db/`. Es ist idempotent und wird für jeden neuen Laden erneut eingespielt. Die Alternative: den Marketing-Teil als Migration **094** (vorher auf dem WORKBOARD reservieren) und den Laden-Teil in sales-claw `db/provision.sql`.
4. **Code auf der VM über `~/marketing-os`** (Sparse-Ergänzung, nachgezogen durch `update.sh`). Die Alternative ist ein eigener Sparse-Checkout `~/rowboat-geschaeftsdaten-os`.
5. **Ausrollen** braucht ein Betreiber-OK: SQL auf der VM (DB-Änderung, additiv) und systemd-Einheiten (sudo).

## Global Constraints

- Ablage: `knowledge/companys/<Firma>/Kontakte/<Kategorie>/<Name>.md`, `Newsletter/<YYYY-MM-DD> <Betreff>.md`, `Wochenbild/<YYYY>-KW<nn>.md`, Archiv `Kontakte/_archiv/<Name>.md`, ohne Bereich `_ohne`.
- Kopfzeile wörtlich: „automatisch erzeugt, Quelle sales-claw, Stand …“ (Groß-/Kleinschreibung wie unter Entscheidungen).
- Keine Mail-Inhalte, keine Sozialdaten, keine Geheimnisse. Kein Modell auf dem Mini-PC; der Lauf ruft kein Modell auf.
- Nur lesend: Die Rolle hat nur `SELECT` auf Sichten bzw. Spalten, die Sitzung ist read-only. Der Job sendet ausschließlich `select`.
- Nie `sales_test`. Läden nur aus Env-Dateien mit Marke.
- Nur geänderte Dateien schreiben. Nichts hart löschen außer gesperrten Kopien (Betreiber-Entscheid 1).
- Rowboats Hintergrund-Agenten dürfen diese Ordner nicht bearbeiten (Frontmatter wie oben, Vertragstest in Task 10).
- Dateien gehören uid 1000. Pfade sind Windows-tauglich nach `wissens_spiegel._windows_tauglich` (≤ 240).
- Code: Python 3.11, nur stdlib plus `spaces.marketing.claw.{bloecke_mjml,seite_mail}`. Module flach und per `sys.path` geladen (wie `scripts/wissens_spiegel.py`).
- Repo-Regeln: Claude-Code-Sessions committen direkt auf `master` (vibemind-os im Worktree `.worktrees/setup-agent`). Vorher den WORKBOARD-Claim `cc-rowboat-zentrale-b` im äußeren Repo anlegen und sofort committen, nur die eigene Zeile stagen. Conventional Commits. pytest mit `--basetemp C:/Users/User/ClaudeWork/pytmp-rowboat`.

## Review Focus

1. **Lauf ohne Datenänderung:** Ein zweiter Lauf direkt danach schreibt 0 Dateien. „Stand“ aus den Daten statt aus der Uhr (Test in Task 8: `test_zweiter_lauf_schreibt_nichts`).
2. **Massenverschiebung** (z. B. 100 Kontakte neu eingeordnet): höchstens 15 Löschungen je Spiegeltakt, dazwischen 360 s Pause. Sonst blockiert der Spiegel mit Exit 3 (Test in Task 8: `test_verschiebungen_in_paketen_mit_pause`).
3. **Kontakt wird nach dem Export privat:** Die Datei verschwindet aus `Kontakte/` und `_archiv/`, und kein Feld des gesperrten Kontakts verlässt die DB (Tests in Task 1 `test_sicht_nullt_gesperrte` und Task 8 `test_gesperrt_entfernt_auch_aus_archiv`).
4. **Namen, die nur in Groß-/Kleinschreibung oder NFC/NFD abweichen**, verbotene Zeichen, `CON`, sehr lang: Das Ergebnis ist eindeutig, Windows-tauglich und ≤ 240 (Tests in Task 2).
5. **Fremde Dateien** (vom Betreiber oder vom Rowboat-Agenten) in `Kontakte/`, `Newsletter/` oder `Wochenbild/` werden nie überschrieben, verschoben oder archiviert (Test in Task 8: `test_fremde_dateien_unberuehrt`).

---

## File Structure

```
spaces/rowboat/geschaeftsdaten/
  db/rowboat_leser.sql          Rolle, Sichten je Laden, Marketing-Spaltenrechte (idempotent)
  db/verify_rowboat_leser.sql   Prüfungen in einer Transaktion mit ROLLBACK
  namen.py                      Dateinamen, Eindeutigkeit, Pfadprüfung (nutzt wissens_spiegel)
  laeden.py                     Laden-Env lesen, Laden→Marke, Firmenordner
  quelle.py                     Datentypen, Quelle-Protokoll, PsqlQuelle (docker exec psql, JSON)
  steckbrief.py                 Kategorie, People-Index, Steckbrief-Markdown (rein)
  newsletter.py                 Klartext und Newsletter-Notiz (rein)
  wochenbild.py                 KW-Rechnung und Wochenbild-Markdown (rein)
  ablage.py                     Bestand eigener Dateien, Plan, atomares Ausführen in Paketen
  lauf.py                       CLI: sammeln → rendern → planen → ausführen, Protokoll, stand.json
  deploy/rowboat-geschaeftsdaten.service
  deploy/rowboat-geschaeftsdaten.timer
spaces/rowboat/tests/
  geschaeftsdaten_fake.py       FakeQuelle + Fabriken (kein Test, Hilfsmodul)
  test_geschaeftsdaten_{sql,namen,laeden,quelle,steckbrief,newsletter,wochenbild,ablage,lauf,vertrag}.py
```

Tests laden die Module über `sys.path.insert(0, <repo>/spaces/rowboat/geschaeftsdaten)`, `<repo>/spaces/rowboat/scripts` und `<repo>` (für `spaces.marketing.claw`). `lauf.py` setzt dieselben drei Pfade selbst, relativ zu `__file__`.

---

### Task 1: Lese-Rolle und Sichten

**Files:**
- Create: `spaces/rowboat/geschaeftsdaten/db/rowboat_leser.sql`, `spaces/rowboat/geschaeftsdaten/db/verify_rowboat_leser.sql`
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_sql.py`

**Interfaces:**
- Produces: Rolle `rowboat_leser` (login, kein Passwort, noinherit, read-only, `statement_timeout 60s`). Je Laden-Schema `S` (ohne `sales_test`):
  - `S.rowboat_kontakte(id, gesperrt bool, archiviert bool, name, company, title, status, consent_status, source, email_klein, art, bereiche jsonb, created_at, updated_at)`. Ist `gesperrt`, sind alle Spalten außer `id`, `gesperrt`, `archiviert`, `created_at` NULL.
  - `S.rowboat_ereignisse(lead_id, typ, zeit, termin_uid, termin_datum, termin_uhrzeit, empfohlen_von_name, empfohlen_von_lead)`, nur Typen `nachricht_ausgehend, kundenantwort, nachricht, versand, termin, termin_abgesagt, stufenwechsel, empfehlung`, nur nicht gesperrte Kontakte.
  - `S.rowboat_newsletter(newsletter uuid, erster_versand timestamptz, empfaenger int)` aus `drafts` mit `status='sent'`.
  - Marketing: `select` nur auf `mandanten(id,name)`, `inhalte(id,mandant,art,titel,status,freigegebene_fassung)`, `inhalt_fassungen(inhalt,fassung,format,felder,bloecke,seite)`, `video_auftraege(id,mandant,art,titel,status,dauer_s,geaendert_am,archiviert)`, `ton_auftraege(id,mandant,titel,status,dauer_s,geaendert_am)`.

- [ ] **Step 1: WORKBOARD-Claim** `cc-rowboat-zentrale-b` im äußeren Repo eintragen (Scope: `spaces/rowboat/geschaeftsdaten`, VM-Timer, Rolle `rowboat_leser`), nur diese Zeile stagen, `docs: workboard claim cc-rowboat-zentrale-b` committen.

- [ ] **Step 2: Failing tests (Texttests gegen die SQL-Datei)**

```python
def test_kein_zugriff_auf_inhaltsspalten():
    sql = lesen("rowboat_leser.sql").lower()
    for verboten in ("body", "body_html", "notes", "phone", "payload->>'text'", "payload->>'inhalt'",
                     "begruendung", "briefing", "skript", "'thema'", "'ort'"):
        assert verboten not in sql
    assert "grant select on all tables" not in sql and "bypassrls" not in sql

def test_sicht_nullt_gesperrte():
    sql = lesen("rowboat_leser.sql").lower()
    assert "enrichment ? '_privat'" in sql and "enrichment ? '_loeschantrag'" in sql
    assert sql.count("case when p.gesperrt then null") >= 10   # alle Datenspalten

def test_schleife_ohne_sales_test_und_rolle_read_only():
    sql = lesen("rowboat_leser.sql").lower()
    assert "^sales(_[a-z][a-z0-9_]{0,30})?$" in sql and "<> 'sales_test'" in sql
    assert "default_transaction_read_only = on" in sql and "password null" in sql

def test_verify_endet_mit_rollback():
    assert lesen("verify_rowboat_leser.sql").strip().lower().endswith("rollback;")
```

- [ ] **Step 3: Run** `python -m pytest spaces/rowboat/tests/test_geschaeftsdaten_sql.py -q --basetemp C:/Users/User/ClaudeWork/pytmp-rowboat` → FAIL (Datei fehlt).

- [ ] **Step 4: SQL schreiben.** Kopf `\set ON_ERROR_STOP on`. Die Rolle wird in `do $$ … if not exists` angelegt, danach `alter role … password null`, `set default_transaction_read_only = on`, `set statement_timeout = '60s'`. Die Laden-Schleife wie in `provision.sql:73-81`, zusätzlich `and schema_name <> 'sales_test'`. Darin: `grant usage on schema %I`, `create or replace view` (Eigentümer supabase_admin), `grant select` auf die drei Sichten. Die Sperr-Regel ist fail-closed in Schutzrichtung, also strenger als `server._privat`:

```sql
cross join lateral (select
  ((l.enrichment ? '_privat') and not (jsonb_typeof(l.enrichment->'_privat') = 'object'
       and l.enrichment->'_privat'->>'privat' = 'false'))
  or (l.enrichment ? '_loeschantrag') as gesperrt,
  coalesce(jsonb_typeof(l.enrichment->'archiviert'->'archiviert') = 'boolean'
       and (l.enrichment->'archiviert'->>'archiviert') = 'true', false) as archiviert) p
```

`rowboat_ereignisse` hängt an `rowboat_kontakte` mit `not gesperrt` und joint `empfehlungslisten` über `e.id::text = a.payload->>'liste_id'` für `empfohlen_von_lead`. Der Payload wird nur per `->>` für die Schlüssel `uid`, `datum`, `uhrzeit`, `empfohlen_von_name`, `liste_id` gelesen. `verify_rowboat_leser.sql` steht in `begin … rollback;`, ruft `set local role rowboat_leser` und prüft per `do`-Block mit `raise exception`: Die Sichten lassen sich lesen. `select … from sales.leads` scheitert mit 42501. Ein `insert` scheitert mit 25006. Kein gesperrter Kontakt hat einen `name`. Die Spalte `body` von `sales.drafts` ist nicht lesbar.

- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `feat(rowboat): Lese-Rolle rowboat_leser mit Sichten je Laden`.

---

### Task 2: Namen und Pfade

**Files:** Create `spaces/rowboat/geschaeftsdaten/namen.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_namen.py`

**Interfaces:**
- Consumes: `wissens_spiegel._windows_tauglich(pfad: str) -> bool`, `wissens_spiegel.MAX_PFAD`.
- Produces:
  - `dateistamm(roh: str | None, ersatz: str, max_len: int) -> str`
  - `eindeutig(eintraege: list[tuple[str, str, datetime]]) -> dict[str, str]`: `(schluessel, stamm, created_at)` → Endstamm
  - `relpfad(ordner: str, stamm: str, endung: str = ".md") -> str`: kürzt den Stamm, bis `_windows_tauglich` stimmt, und wirft `ValueError`, wenn selbst 8 Zeichen nicht passen

- [ ] **Step 1: Failing tests**

```python
def test_verbotene_zeichen_und_rand():  assert dateistamm(' A<b>:c/d. ', "x", 100) == "A b c d"
def test_reserviert():                  assert dateistamm("con", "x", 100) == "con_"
def test_leer_wird_ersatz():            assert dateistamm(" \t", "Kontakt 1234abcd", 100) == "Kontakt 1234abcd"
def test_nfc():                         assert dateistamm("Mu\u0308ller", "x", 100) == "M\u00fcller"
def test_doppelt_casefold_aeltester_behaelt():
    r = eindeutig([("b"*36, "Max Müller", T2), ("a"*36, "max müller", T1)])
    assert r["a"*36] == "max müller" and r["b"*36] == "Max Müller (bbbbbbbb)"
def test_doppelt_nfd_gegen_nfc():       # gilt erst nach dateistamm als gleich
    r = eindeutig([("a"*36, dateistamm("Mu\u0308ller","x",100), T1), ("b"*36, "Müller", T2)])
    assert len({v.casefold() for v in r.values()}) == 2
def test_langer_pfad_passt():
    p = relpfad("companys/VibeMind/Kontakte/Private Vorsorge und Versicherung", "x" * 300)
    assert len(p) <= 240 and _windows_tauglich(p) and p.endswith(".md")
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (`unicodedata.normalize("NFC")`, Regex für die Zeichenklasse aus `wissens_spiegel._WIN_VERBOTEN`, Kürzen vor dem Suffix ` (<id8>)`). **Step 4: Run** → PASS. **Step 5: Commit** `feat(rowboat): Dateinamen fuer Geschaeftsnotizen`.

---

### Task 3: Läden und Firmenordner

**Files:** Create `spaces/rowboat/geschaeftsdaten/laeden.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_laeden.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Laden(schema: str, marke: str, sammel_ids: frozenset[str])`
  - `env_werte(pfad: Path, schluessel: frozenset[str]) -> dict[str, str]`
  - `laeden_finden(sales_claw: Path) -> tuple[list[Laden], list[str]]`: Läden und Warnungen (nur Dateiname und Grund, nie Werte)
  - `firmenordner(wissen: Path, marke_name: str) -> Path`: wirft `ValueError` bei Symlink oder ungültigem Namen

- [ ] **Step 1: Failing tests** (tmp-Ordner mit `.env` und `deploy/laeden/`):
  - `test_basis_laden_vorgabe_sales`: `.env` mit `MARKETING_MANDANT=vibemind\r\n` und drei Sammel-IDs ergibt `Laden("sales", "vibemind", {3 IDs})`. Das CR ist entfernt.
  - `test_laden_marke_vor_mandant`: `ivan.env` mit `SALES_DB_SCHEMA=sales_ivan`, `LADEN_MARKE=fin2gether`, `MARKETING_MANDANT=vibemind` ergibt Marke `fin2gether`.
  - `test_beispiel_und_sicherungen_ignoriert`: `beispiel.env` und `ivan.env.vor-aussehen` zählen nicht.
  - `test_ohne_marke_uebersprungen`, `test_sales_test_nie`, `test_doppeltes_schema_beide_weg` (2 Warnungen).
  - `test_nur_erlaubte_schluessel`: `GEHEIM_KEY=xyz` taucht weder im Ergebnis noch in den Warnungen auf.
  - `test_firmenordner_vorhanden_ohne_gross_klein`: Ein vorhandenes `companys/Vibemind` wird für `VibeMind` gefunden. Ein Symlink-Ordner führt zu `ValueError` (ohne Symlink-Recht `pytest.skip`).
- [ ] **Step 2–4:** Run → FAIL, Implement, Run → PASS. Schema-Regex wie `provision.sql`, Marke `^[a-z][a-z0-9_]{1,30}$`. Werte werden an Anführungszeichen und Leerraum gestutzt. Leere Sammel-IDs zählen nicht.
- [ ] **Step 5: Commit** `feat(rowboat): Laeden und Firmenordner fuer Geschaeftsdaten`.

---

### Task 4: Datenquelle

**Files:** Create `spaces/rowboat/geschaeftsdaten/quelle.py`, `spaces/rowboat/tests/geschaeftsdaten_fake.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_quelle.py`

**Interfaces:**
- Produces (alle `@dataclass(frozen=True)`; Zeiten tz-aware):
  - `Kontakt(id: str, gesperrt: bool, archiviert: bool, name: str|None, company: str|None, title: str|None, status: str|None, consent_status: str|None, source: str|None, email_klein: str|None, art: str|None, bereiche: tuple[str, ...], created_at: datetime, updated_at: datetime|None)`
  - `Ereignis(lead_id: str, typ: str, zeit: datetime, termin_uid: str|None, termin_datum: str|None, termin_uhrzeit: str|None, empfohlen_von_name: str|None, empfohlen_von_lead: str|None)`
  - `Versand(newsletter: str, erster_versand: datetime, empfaenger: int)`
  - `NewsletterInhalt(id: str, mandant: str, titel: str, format: str, felder: dict, bloecke: dict|None, seite: dict|None)`
  - `Medium(art: Literal["reel", "podcast"], mandant: str, titel: str, dauer_s: float|None, fertig_am: datetime)`
  - `class Quelle(Protocol)`: `marken() -> dict[str, str]`, `hat_sichten(schema) -> bool`, `kontakte(schema) -> list[Kontakt]`, `ereignisse(schema) -> list[Ereignis]`, `versand(schema) -> list[Versand]`, `newsletter_inhalte(ids: list[str]) -> list[NewsletterInhalt]` (nur `freigegeben`, freigegebene Fassung), `medien(von: datetime, bis: datetime) -> list[Medium]` (`status='fertig'`, Video nicht archiviert)
  - `class DbFehler(Exception)`
  - `PsqlQuelle(container: str = "debian-supabase-db-1", laeufer=subprocess.run)`
  - `geschaeftsdaten_fake.FakeQuelle(**listen)` und die Fabriken `kontakt(**ueberschreiben)`, `ereignis(...)`, `inhalt(...)`

- [ ] **Step 1: Failing tests** (mit einem Fake-`laeufer`):
  - `test_argv_ohne_geheimnis`: argv ist genau `["docker","exec","-i",C,"psql","-h","127.0.0.1","-U","rowboat_leser","-d","postgres","-v","ON_ERROR_STOP=1","-At","-f","-"]`. Die SQL kommt über `input=`.
  - `test_json_zu_kontakten`: Eine JSON-Zeile wird zu `Kontakt` mit tz-aware Zeiten. `bereiche` als `null` wird zu `()`.
  - `test_ungueltiges_schema_wirft_vor_aufruf`: `kontakte("sales; drop")` ergibt `ValueError`, der `laeufer` wird nie gerufen.
  - `test_psql_fehler_wird_dbfehler_ohne_stderr_text`: Bei rc 1 und stderr mit Kontaktnamen enthält `str(DbFehler)` den stderr **nicht**.
  - `test_nur_select`: Jede erzeugte SQL beginnt (ohne Leerraum) mit `select` oder `with`.
- [ ] **Step 2–4:** Run → FAIL, Implement, Run → PASS. Schema wird vor jeder Interpolation gegen die Regex aus Task 3 geprüft, IDs gegen eine UUID-Regex. Zeitstempel per `datetime.fromisoformat`. `hat_sichten` fragt `to_regclass('<S>.rowboat_kontakte') is not null`.
- [ ] **Step 5: Commit** `feat(rowboat): Datenquelle ueber rowboat_leser`.

---

### Task 5: Steckbrief

**Files:** Create `spaces/rowboat/geschaeftsdaten/steckbrief.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_steckbrief.py`

**Interfaces:**
- Consumes: `Kontakt`, `Ereignis` (Task 4).
- Produces:
  - `KATEGORIEN: dict[str, str]` (Schlüssel → Ordner wie unter Entscheidungen), `OHNE = "_ohne"`, `ARCHIV = "_archiv"`
  - `kategorie(k: Kontakt) -> str`, `stand(k: Kontakt, ereignisse: list[Ereignis]) -> date`
  - `people_index(wissen: Path) -> PeopleIndex` mit `link(name: str|None, email: str|None) -> str | None` (liefert `People/<stamm>`)
  - `steckbrief(k: Kontakt, ereignisse: list[Ereignis], *, laden: str, heute: date, people: str|None, empfohlen_von: str|None) -> str`

- [ ] **Step 1: Failing tests** (Fabriken aus `geschaeftsdaten_fake`):
  - `test_kategorie_erster_bekannter_bereich`: `("unbekannt","it","bav")` ergibt `bAV` (feste Reihenfolge). `()` ergibt `_ohne`.
  - `test_frontmatter_und_kopf`: Der Text beginnt mit `---\n`. Er enthält `erzeugt_von: rowboat-geschaeftsdaten`, `schluessel: <id>`, `schlagwoerter: [person, it]` für die Bereiche `(bav, it)`, die Art `person` und `Automatisch erzeugt, Quelle sales-claw, Stand 20.10.2026`.
  - `test_keine_verbotenen_schluessel`: Der Frontmatter enthält kein `live:`, `live_note:`, `curated_at:`.
  - `test_stand_aus_daten_nicht_heute`: `heute=2026-10-26`, `updated_at=2026-10-20`, jüngstes Ereignis 2026-10-22 23:30 UTC ergibt `Stand 23.10.2026` (Berlin).
  - `test_offene_termine_ohne_abgesagte_und_vergangene`: drei Termine (gestern, morgen, morgen und abgesagt per `uid`) ergeben genau einen. Ein kaputtes `datum` wird übersprungen.
  - `test_letzter_kontakt_nur_datum_und_richtung`: `kundenantwort` ergibt „eingehend“, `nachricht_ausgehend` „ausgehend“.
  - `test_einwilligung`: `opt_in` ergibt „ja“, alles andere „nein“.
  - `test_people_link_mail_vor_name_und_mehrdeutig_keiner`: People-Dateien im tmp-Ordner. `People/Veraltet/` wird ignoriert.
  - `test_keine_mail_im_text`: `email_klein="a@b.de"` taucht im Steckbrief nicht auf.
- [ ] **Step 2–4:** Run → FAIL, Implement, Run → PASS. Stufen-Bezeichnungen: `new` Neu, `researched` Recherchiert, `qualified` Qualifiziert, `contacted` Kontaktiert, `replied` Geantwortet, `meeting` Termin, `won` Gewonnen, `lost` Verloren, Unbekanntes wie es ist. Die Bereichs-Bezeichnungen kommen aus `server.BEREICHE`.
- [ ] **Step 5: Commit** `feat(rowboat): Steckbrief je Kontakt`.

---

### Task 6: Newsletter-Notiz

**Files:** Create `spaces/rowboat/geschaeftsdaten/newsletter.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_newsletter.py`

**Interfaces:**
- Consumes: `NewsletterInhalt`, `Versand` (Task 4), `spaces.marketing.claw.bloecke_mjml.text_fassung(dok, pflichtteil)`, `spaces.marketing.claw.seite_mail.text_fassung(seite, pflichtteil)`.
- Produces: `klartext(inh: NewsletterInhalt) -> str`, `stamm(inh: NewsletterInhalt, erster_versand: datetime) -> str` (`JJJJ-MM-TT <Betreff>`, roh, wird mit Task 2 bereinigt), `newsletter_notiz(inh: NewsletterInhalt, versand: Versand) -> str`.

- [ ] **Step 1: Failing tests:**
  - `test_bloecke_text`: Ein kleines `bloecke`-Dokument (`root.data.childrenIds` → Heading „Hallo {vorname}“, Text „Abmelden: {abmeldelink}“) ergibt einen Text mit „Hallo <Vorname>“, ohne `{abmeldelink}` und ohne die Fußzeile `--`.
  - `test_felder_format`: `felder={"betreff": "B", "abschnitte": [{"titel": "T", "text": "X"}]}` ergibt einen Text mit `T` und `X`.
  - `test_stamm_datum_berlin`: `erster_versand=2026-10-24T22:30Z` ergibt `2026-10-25 …`. Ohne `felder.betreff` gilt `titel`.
  - `test_notiz`: Frontmatter-`schluessel: nl:<id>`, „Empfänger: 2“, „Öffnungen: werden nicht erfasst“, Kopfzeile mit `Stand` = Datum des ersten Versands.
- [ ] **Step 2–4:** Run → FAIL, Implement, Run → PASS (`seite_mail` wird erst in `klartext` importiert. Wirft ein Renderer `RenderFehler`, steht dort „Text nicht darstellbar“, und der Lauf bricht nicht ab.)
- [ ] **Step 5: Commit** `feat(rowboat): Newsletter-Archiv als Notiz`.

---

### Task 7: Wochenbild

**Files:** Create `spaces/rowboat/geschaeftsdaten/wochenbild.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_wochenbild.py`

**Interfaces:**
- Consumes: `Kontakt`, `Ereignis`, `Medium` (Task 4).
- Produces: `letzte_kw(heute: date) -> tuple[int, int]`, `zeitraum(jahr: int, kw: int) -> tuple[datetime, datetime]` (Berlin, halboffen), `stamm(jahr, kw) -> str` (`2026-KW43`), `wochenbild(firma: str, jahr: int, kw: int, kontakte: list[Kontakt], ereignisse: list[Ereignis], newsletter: list[tuple[str, int]], medien: list[Medium]) -> str`. `newsletter` ist eine Liste aus (Link-Ziel ohne `.md`, Empfänger).

- [ ] **Step 1: Failing tests:**
  - `test_letzte_kw_montag`: `heute=2026-10-26` (Montag) ergibt `(2026, 43)`. `heute=2027-01-04` ergibt `(2026, 53)`.
  - `test_zeitraum_sommerzeitwechsel`: KW 43/2026 beginnt am 19.10. um 00:00 Berlin und endet am 26.10. um 00:00 Berlin. Die Grenze liegt also bei 23:00 UTC am 25.10. (nach dem Zeitumstellungs-Sonntag).
  - `test_zahlen_ohne_gesperrte_und_archivierte`: Gesperrte zählen nirgends, archivierte nicht in der Pipeline.
  - `test_keine_kontaktnamen`: Kein `Kontakt.name` taucht im Text auf.
  - `test_medien_der_woche`: Ein Reel am 25.10. 22:59 UTC zählt, eines am 25.10. 23:00 UTC nicht. Die Dauer wird als `m:ss` gezeigt.
  - `test_frontmatter`: `schluessel: kw:2026-W43`, `stand: 2026-10-25`.
- [ ] **Step 2–4:** Run → FAIL, Implement, Run → PASS. `date.isocalendar`, `zoneinfo.ZoneInfo("Europe/Berlin")`.
- [ ] **Step 5: Commit** `feat(rowboat): Wochenbild je Firma`.

---

### Task 8: Ablage (Plan und Ausführen)

**Files:** Create `spaces/rowboat/geschaeftsdaten/ablage.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_ablage.py`

**Interfaces:**
- Consumes: `namen.eindeutig/relpfad/dateistamm` (Task 2), `steckbrief.ARCHIV` (Task 5).
- Produces:
  - `@dataclass(frozen=True) Soll(rel: str, schluessel: str, inhalt: str)`, `Ist(rel: str, schluessel: str, sha: str)`
  - `bestand(firma: Path) -> list[Ist]`: liest nur `Kontakte/**`, `Newsletter/*`, `Wochenbild/*` und davon nur Dateien mit `erzeugt_von: rowboat-geschaeftsdaten` im Frontmatter. `rel` ist relativ zum Firmenordner
  - `@dataclass Plan(schreiben: list[Soll], verschieben: list[tuple[Ist, Soll]], archivieren: list[tuple[Ist, str]], entfernen: list[Ist], unveraendert: int)`
  - `planen(soll: list[Soll], ist: list[Ist], gesperrt: set[str], archiv_inhalt: Callable[[Ist], str]) -> Plan`: Ein Kontakt ohne Soll und ohne Sperre geht nach `Kontakte/_archiv/<stamm>.md` (eindeutig gemacht). Ein Newsletter oder Wochenbild ohne Soll bleibt stehen. Eigene Doppel desselben Schlüssels landen in `entfernen`
  - `ausfuehren(plan: Plan, firma: Path, *, paket: int = 15, pause_s: float = 360.0, schlafen=time.sleep, trocken: bool = False) -> dict[str, int]` (Zähler: `geschrieben, verschoben, archiviert, entfernt, unveraendert, abweichung`)

- [ ] **Step 1: Failing tests** (tmp-Firmenordner):
  - `test_zweiter_lauf_schreibt_nichts` (**Review Focus 1**): einmal ausführen, dann neu planen. Ergebnis: Der Plan ist leer und `unveraendert` entspricht der Zahl der Dateien.
  - `test_geaenderter_inhalt_wird_atomar_ersetzt`: Danach liegt keine `*.gd.tmp` mehr.
  - `test_kategorie_wechsel_verschiebt`: Die alte Datei ist weg, die neue hat den neuen Inhalt.
  - `test_verschwundener_kontakt_ins_archiv` und `test_archivname_kollidiert_bekommt_id8`.
  - `test_gesperrt_entfernt_auch_aus_archiv` (**Review Focus 3**): je eine Datei unter `Kontakte/bAV/` und `Kontakte/_archiv/` mit gesperrtem Schlüssel. Ergebnis: beide weg.
  - `test_fremde_dateien_unberuehrt` (**Review Focus 5**): `Kontakte/bAV/Notiz.md` ohne Marker, `Newsletter/eigen.md` mit anderem `erzeugt_von` und `Kontakte/bAV/bild.png` bleiben byte-gleich und tauchen in keinem Plan auf.
  - `test_verschiebungen_in_paketen_mit_pause` (**Review Focus 2**): 40 Verschiebungen mit Fake-`schlafen`. Ergebnis: `schlafen` wird genau 2-mal mit 360 gerufen, und jede Lücke zwischen zwei Pausen hat höchstens 15 Löschungen.
  - `test_abweichung_quelle_geaendert_bleibt_liegen`: Nach dem Planen ändert sich die Quelldatei. Ergebnis: Das Ziel wird geschrieben, die Quelle bleibt, `abweichung == 1`.
  - `test_trocken_schreibt_nichts`.
  - `test_newsletter_ohne_soll_bleibt`.
- [ ] **Step 2–4:** Run → FAIL, Implement, Run → PASS. Frontmatter wird nur bis zur zweiten `---`-Zeile gelesen, höchstens 4 KB. Ordner bekommen `mkdir(parents=True, exist_ok=True)`, Dateien `0o644`. Kein Symlink wird verfolgt (`is_symlink` → überspringen).
- [ ] **Step 5: Commit** `feat(rowboat): Ablage mit Aenderungserkennung und Paketen`.

---

### Task 9: Lauf (CLI)

**Files:** Create `spaces/rowboat/geschaeftsdaten/lauf.py`; Test `spaces/rowboat/tests/test_geschaeftsdaten_lauf.py`

**Interfaces:**
- Consumes: alles aus den Tasks 2–8.
- Produces:
  - `lauf(quelle: Quelle, laeden: list[Laden], wissen: Path, *, heute: date, trocken: bool, wochenbild_neu: bool, ablage_optionen: dict) -> Ergebnis`
  - `@dataclass Ergebnis(je_firma: dict[str, dict[str, int]], uebersprungen: list[str], exit_code: int)`
  - `main(argv: list[str] | None = None) -> int`. Argumente: `--trocken`, `--wochenbild-neu`, `--heute JJJJ-MM-TT`. Env (mit Vorgaben): `GESCHAEFTSDATEN_WISSEN=/home/debian/rowboat-data/knowledge`, `GESCHAEFTSDATEN_SALES_CLAW=/home/debian/sales-claw`, `GESCHAEFTSDATEN_DB_CONTAINER=debian-supabase-db-1`, `GESCHAEFTSDATEN_STAND=/home/debian/rowboat-geschaeftsdaten/stand.json`

Ablauf: Läden finden. Je Laden `hat_sichten`, sonst landet er in `uebersprungen`. **Alle Lesungen vor jedem Schreiben**, bei `DbFehler` Exit 2. Läden nach Marke gruppieren, Marke → `marken()`-Name → `firmenordner`. Sammelkontakte und `source='system'` raus. Namen werden je Firma eindeutig gemacht (Task 2), danach Soll-Listen bilden, planen und ausführen. Wochenbild nur, wenn `Wochenbild/<stamm>.md` fehlt oder `--wochenbild-neu` gesetzt ist. Der Link „Empfohlen von“ zeigt auf den Steckbrief des `empfohlen_von_lead`, wenn er in derselben Firma liegt, sonst steht nur der Name da.

- [ ] **Step 1: Failing tests** (`FakeQuelle`, tmp-Wissen mit `companys/VibeMind/`):
  - `test_ende_zu_ende_zwei_firmen`: `sales`→vibemind (3 Kontakte, davon 1 Sammel, 1 gesperrt), `sales_ivan`→fin2gether (1). Ergebnis: `companys/VibeMind/Kontakte/bAV/<A>.md` und `companys/fin2gether/Kontakte/_ohne/<B>.md`. Sammel und gesperrt fehlen.
  - `test_zweimal_hintereinander_null_schreibungen`.
  - `test_laden_ohne_sichten_uebersprungen_exit_1`. Die übrigen Läden laufen trotzdem.
  - `test_dbfehler_vor_schreiben_exit_2_nichts_geschrieben`.
  - `test_wochenbild_nur_wenn_fehlt`: Am 2026-10-26 wird `2026-KW43.md` angelegt. Ein zweiter Lauf mit geänderten Zahlen lässt die Datei unverändert. Mit `--wochenbild-neu` wird sie überschrieben.
  - `test_protokoll_ohne_personendaten` (caplog, DEBUG): Kein Kontaktname, keine E-Mail und kein Pfad unter `Kontakte/` taucht in einer Logzeile auf.
  - `test_stand_json`: Die Datei enthält `ergebnis`, `je_firma.VibeMind.geschrieben` und `uebersprungen` und wird atomar geschrieben.
  - `test_newsletter_nur_eigene_marke`: Ein Versand im Laden `sales` für einen Inhalt mit `mandant=fin2gether` erscheint nicht unter VibeMind.
- [ ] **Step 2–4:** Run → FAIL, Implement, Run → PASS. Logging über `logging.basicConfig(format="%(levelname)s %(message)s")` (journald setzt die Zeit).
- [ ] **Step 5: Run die ganze Suite** `python -m pytest spaces/rowboat/tests -q --basetemp C:/Users/User/ClaudeWork/pytmp-rowboat`. Erwartet: neu grün, die Alttests unverändert.
- [ ] **Step 6: Commit** `feat(rowboat): naechtlicher Geschaeftsdaten-Lauf`.

---

### Task 10: Hintergrund-Vertrag und systemd-Einheiten

**Files:**
- Create: `spaces/rowboat/geschaeftsdaten/deploy/rowboat-geschaeftsdaten.service`, `…/deploy/rowboat-geschaeftsdaten.timer`
- Test: `spaces/rowboat/tests/test_geschaeftsdaten_vertrag.py`

- [ ] **Step 1: Failing tests**
  - `test_rowboat_hintergrund_fasst_companys_nicht_an`: liest aus `spaces/rowboat/rowboat/apps/x/packages/core/src/knowledge/`, ohne Checkout `pytest.skip`:
    - Die `folder:`-Werte in `note_system.ts` sind eine Teilmenge von `{People, Organizations, Projects, Topics, Meetings}`.
    - `CURATION_ENTITY_FOLDERS` in `build_graph.ts` ist gleich `['People', 'Organizations', 'Projects', 'Topics']`.
    - `inline_tasks.ts` enthält `fields['live_note'] !== 'true'`.
    - `tag_notes.ts` enthält `content.startsWith('---')`.
    - `live-note/fileops.ts` liefert `null` ohne `getLiveBlock`.

    Ändert upstream das, wird der Test rot und Teil B muss neu geprüft werden.
  - `test_erzeugte_notizen_triggern_keinen_agenten`: Steckbrief, Newsletter und Wochenbild aus den Fabriken beginnen mit `---`, ihr Frontmatter hat kein `live`, `live_note` oder `curated_at`, und der Text enthält kein `@rowboat`.
  - `test_systemd_einheiten`:
    - `.timer`: `OnCalendar=*-*-* 02:30:00 Europe/Berlin` und `Persistent=true`.
    - `.service`: `Type=oneshot`, `User=debian`, `ExecStart=/usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/geschaeftsdaten/lauf.py`, `Nice=10`, `TimeoutStartSec=3h`, `NoNewPrivileges=yes`.
    - Es gibt kein `EnvironmentFile`, und kein Wert passt auf `(key|token|secret|passw)`.
- [ ] **Step 2–4:** Run → FAIL, Einheiten schreiben, Run → PASS.
- [ ] **Step 5: Commit** `feat(rowboat): systemd-Timer und Hintergrund-Vertrag fuer Geschaeftsdaten`. Danach `git push origin master` (vibemind-os). Vorher `git status` prüfen und nur eigene Dateien committen.

---

### Task 11: Ausrollen auf offload-vm (nur mit Betreiber-OK, Entscheide 1–5)

**Files:** keine Repo-Änderung außer WORKBOARD, Memory und Vault.

- [ ] **Step 1: Betreiber-OK** zu den Entscheiden 1–5 einholen. Ohne OK hier stoppen.
- [ ] **Step 2: Rolle einspielen**: `ssh offload-vm "docker exec -i debian-supabase-db-1 psql -U supabase_admin -d postgres" < spaces/rowboat/geschaeftsdaten/db/rowboat_leser.sql`, danach dasselbe mit `verify_rowboat_leser.sql`. Erwartet: keine `ERROR`-Zeile, am Ende `ROLLBACK`.
- [ ] **Step 3: Code auf die VM**: `ssh offload-vm 'cd ~/marketing-os && git status --porcelain --untracked-files=no && git sparse-checkout add spaces/rowboat/geschaeftsdaten spaces/rowboat/scripts && git fetch origin master && git merge --ff-only origin/master'`. Erwartet: Status leer, `spaces/rowboat/geschaeftsdaten/lauf.py` liegt vor. `mkdir -m 700 ~/rowboat-geschaeftsdaten`.
- [ ] **Step 4: Trockenlauf**: `ssh offload-vm '/usr/bin/python3 ~/marketing-os/spaces/rowboat/geschaeftsdaten/lauf.py --trocken'`. Erwartet: Exit 0, `VibeMind geschrieben≈557` (577 minus 17 gesperrt minus 3 Sammel), `fin2gether geschrieben=1`, Newsletter 1, Wochenbild 1 je Firma, keine Namen in der Ausgabe.
- [ ] **Step 5: Einheiten**: `scp` beider Dateien, dann `sudo install -m 644 … /etc/systemd/system/`, `sudo systemctl daemon-reload`, `sudo systemctl enable --now rowboat-geschaeftsdaten.timer`, `sudo systemctl start rowboat-geschaeftsdaten.service`. Erwartet: `journalctl -u rowboat-geschaeftsdaten -n 50` ohne Fehler, `stand.json` mit `ergebnis: ok`, `systemctl list-timers` mit dem nächsten Lauf um 02:30 Berlin. Dateien prüfen: `find ~/rowboat-data/knowledge/companys -path '*Kontakte*' -name '*.md' ! -user debian | wc -l` ergibt 0.
- [ ] **Step 6: Idempotenz und Hintergrund-Agenten**: Nach ≥ 30 min erneut `--trocken`. Erwartet: `geschrieben=0 verschoben=0`, kein Rowboat-Dienst hat eine Datei verändert. Gegenprobe: `docker logs --since 1h rowboat-headless-rowboat-server-1 2>&1 | grep -E 'NoteTagging|InlineTasks|LiveNote|Curation' | grep -c companys` ergibt 0.
- [ ] **Step 7: Spiegel**: Der nächste Lauf der PC-Aufgabe „Wissens-Spiegel“ endet mit Exit 0 (Log unter `vibemind-betrieb`). `~/.rowboat/knowledge/companys/VibeMind/Kontakte/bAV/` am PC enthält die Steckbriefe, und kein Pfad landet in `untauglich`.
- [ ] **Step 8: Abnahme per Telegram** an „VibeMind Rowboat“:
  - „Gib mir das Profil von <ein bAV-Kunde>“: Die Antwort kommt aus dem Steckbrief und enthält Stufe und Einordnung.
  - „Welche Newsletter haben wir verschickt?“: Die Antwort nennt den Newsletter.
  - „Wie war die letzte Woche?“: Die Antwort zitiert das Wochenbild.
  - Zum Datenschutz zählt `grep -rl` mit den IDs der 17 gesperrten Kontakte (am PC per Abfrage ermittelt, nicht ausgeben) unter `companys/` 0 Treffer.
- [ ] **Step 9: Abschluss**: WORKBOARD-Zeile nach *Erledigt* verschieben (Commit-Refs, „LIVE“), nur die eigene Zeile stagen. Memory `project_rowboat_geschaeftsdaten.md` anlegen und im Index verlinken: Timer, Rolle, rerun-Pflicht für neue Läden, Paket-Regel. `/distill` anbieten.

---

## Selbstprüfung (erledigt)

- **Spec-Abdeckung:**
  - Steckbrief je Kontakt außer privat und Sammel: Tasks 1, 4, 9.
  - Kategorie-Ordner und Schlagwörter: Task 5.
  - Laden → Marke, nie `sales_test`: Tasks 1, 3.
  - Dateiablage und Kopfzeile: Tasks 2, 5–7.
  - Nächtlich, Wochenbild montags: Tasks 7, 9, 10.
  - Nur geänderte Dateien: Task 8.
  - Archiv statt Löschen: Task 8.
  - People-Links: Task 5.
  - Hintergrund-Agenten: Task 10, Task 11 Step 6.
  - Nur lesend: Task 1.
  - Kein Modell, keine Mail-Inhalte, keine Sozialdaten: Tasks 1, 5.
  - Budget „nur geänderte Dateien“: Review Focus 1.
  - Ziel 5 (Bedeutungssuche) ist Teil C. Der Schlüssel `schlagwoerter` passt zu dessen Test.
- **Typen:** `Kontakt`, `Ereignis`, `Versand`, `NewsletterInhalt`, `Medium`, `Laden`, `Soll`, `Ist`, `Plan` und `Ergebnis` werden in den Tasks 3, 4, 8 und 9 definiert und später nur verwendet.
- **Offen beim Betreiber:** die Entscheide 1–5 oben.
