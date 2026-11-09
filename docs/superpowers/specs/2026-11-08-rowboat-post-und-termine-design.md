# Rowboat: offene Post und Kundentermine im Wissensbaum, Mail-Tab aus

Stand 08.11.2026. Felix-Entscheidungen aus dem Gespräch vom 08.11. sind als **(Felix)** markiert, meine Festlegungen als **(meine Wahl)**.

## Warum

Felix will morgens in Rowboat sehen, was auf ihn wartet. Am wichtigsten sind dabei die **Mails, die er beantworten muss**, und die **Termine**. Die Daten dazu gibt es schon, aber nicht in Rowboat:

- **Mails:** sales-claw liest seit 28.10. alle drei Betreiber-Postfächer (Namecheap, GMX, Gmail) nur lesend, ordnet jede Mail ein und führt einen Erledigt-Zustand (`sales.post_mails`). sales-ui zeigt unter `/posteingang` die offenen Mails.
- **Kundentermine:** Sie stehen als `activities` vom Typ `termin` am Kontakt. Jeder Termin hat einen eigenen Jitsi-Raum.
- **Rowboats eigener E-Mail-Tab** ist ein Gmail-Programm. Auf dem Mini-PC (Fern-Modus) ist er unverbunden, und er kann nur Gmail.

Den Tab zu verbinden würde Probleme schaffen:
- Zwei Systeme würden dasselbe Gmail sortieren und verschieben.
- Rowboat würde an sales-claw vorbei senden. sales-claw ist aber der einzige Versandweg, mit Freigabe.

## Ziele

1. **Mail-Tab aus (Felix).** Rowboat zeigt keinen E-Mail-Bereich mehr, damit niemand ihn benutzt.
2. **„Zu beantworten“ im Baum (Felix).**
   - Eine Notiz zeigt dieselben offenen Mails wie sales-ui `/posteingang` für Felix.
   - Je Mail stehen dort Postfach, Absender, Betreff und Alter, dazu ein Link nach sales-ui.
   - Ist der Absender als Kontakt bekannt, verweist die Mail auf dessen Steckbrief.
   - Absender und Betreff dürfen in Rowboat stehen. Das ist eine bewusste Ausnahme von der Regel „Dashboards nur Zahlen“ **(Felix)**.
3. **Kundentermine im Baum.**
   - Je Termin entsteht eine Notiz, verknüpft mit dem Kontakt-Steckbrief, mit Thema und Jitsi-Link.
   - Dazu kommt eine Übersicht der kommenden Termine.
   - An die Termin-Notiz hängt später die Mitschrift (siehe „Danach“).
   - Thema und Ort (Jitsi-Link) dürfen in Rowboat stehen. Damit ist der frühere „Betreiber-Entscheid 2“ für Termine aufgehoben (**Felix, 08.11.**). Bisher stand dort „keine Termin-Details außer Datum und Uhrzeit“ (Kopf von `rowboat_leser.sql`, `steckbrief.py`). Beide Stellen werden angepasst. Für alle anderen Daten gilt die Regel weiter.

## Nicht-Ziele

- Antworten, Freigeben oder Erledigt-Setzen aus Rowboat heraus. Das bleibt in sales-ui.
- Mailtexte, Anhänge und Auszüge im Baum. Es kommen nur Kopfdaten hinein **(meine Wahl)**.
- Eigene, Kollegen- und Handy-Termine. Sie kommen über geheime ICS-Adressen und sind ein späterer Schritt.
- Mails anderer Besitzer als Felix sowie Mails der Läden Ivan und Sophie.
- Aufnahme und Mitschrift von Terminen: eigenes Vorhaben, siehe unten.
- Bewerbungen und Finanzen: eigene Vorhaben.

## Darf nicht passieren

- Rowboat zeigt **andere** offene Mails als sales-ui, also mehr, weniger oder bereits erledigte. Dann vertraut Felix keiner der beiden Listen mehr.
- Daten **gesperrter Kontakte** (privat oder mit Löschantrag) erscheinen im Baum. Die Sicht `rowboat_kontakte` sperrt sie heute schon, und das muss so bleiben. Das gilt auch für Mails von gesperrten Kontakten. Sie zählen mit, damit die Zahl zu sales-ui passt, erscheinen aber ohne Namen, Adresse und Betreff (siehe unten).
- **Sensible Mails** (`post_mails.sensibel`, z. B. Login-Codes) zeigen Betreff oder Inhalt.
- Der Lauf schreibt in sales-claw, verändert Postfächer oder berührt fremde Notizen.

## Teil 1: Mail-Tab ausschalten (Fork Flissel/rowboat, Branch `vibemind`)

- In `apps/x/apps/renderer/src/lib/feature-flags.ts` kommt neben `SPACES_ENABLED` ein Schalter `EMAIL_ENABLED`. In der VibeMind-Fassung steht er auf `false`.
- Bei `false` gilt:
  - Kein Eintrag „Email“ in der Seitenleiste, kein Tastenkürzel, keine Mail-Vorschau oder Ungelesen-Zahl auf der Startseite.
  - Kein Sprung in die Mail-Ansicht, weder aus dem Copilot (`read-view`/`email`) noch über gespeicherte Ansichten. Ein Aufruf der Ansicht `email` führt zur Startseite.
  - Der Hauptprozess bleibt unverändert. Gmail bleibt unverbunden, also wird nichts abgeholt und nichts gesendet.
- Die Änderung bleibt klein und an wenigen Stellen gebündelt, damit Upstream-Merges leicht bleiben.
- Ausliefern geht wie bei früheren Fork-Änderungen:
  1. Fork-Commit.
  2. `spaces/rowboat/scripts/sync_desktop.py` in vibemind-os, dann PR.
  3. Betriebs-Checkout `C:\Users\User\ClaudeWork\vibemind-betrieb` nachziehen. Er steht heute 39 Commits hinter master.
  4. `start_desktop.py --build-embed`, danach Neustart über den Wächter.

## Teil 2 und 3: Lauf „Post und Termine“

### Wo

- Der Code liegt im Geschäftsdaten-Ordner `spaces/rowboat/geschaeftsdaten/` und nutzt dessen Bausteine **(meine Wahl)**:
  - Leserolle `rowboat_leser`
  - Ablage mit `erzeugt_von` und `schluessel`, Lösch-Obergrenze
  - Steckbrief-Pfade je Kontakt
- Er hat einen eigenen Einstieg und einen eigenen Timer, weil er stündlich läuft (Geschäftsdaten laufen nachts):
  - `rowboat-post-termine.timer`, stündlich zur Minute 5 **(meine Wahl)**.
  - Läuft der Geschäftsdaten-Lauf gerade, wartet der Post-Lauf oder setzt aus. Beide dürfen nie gleichzeitig schreiben.
- Notizen tragen `erzeugt_von: rowboat-geschaeftsdaten` und eigene Schlüssel, damit Ablage und Wissens-Prüfung sie kennen:
  - `post:zu-beantworten`
  - `termin:<uid>`
  - `termine:uebersicht`

### Datenzugang (neu in `geschaeftsdaten/db/rowboat_leser.sql`)

**Sicht `sales.rowboat_post_offen`** (`security_barrier`, nur Schema `sales`):
- **Bedingung:** Die Zeilen müssen genau denen entsprechen, die sales-ui als offen zeigt (sales-claw `sales-mcp/post_erledigt.py`, `offen_sql()`):
  - `ziel = 'posteingang'`
  - `status = 'eingeordnet'`
  - `erledigt_am is null`
  - `abgeglichen_am is not null`
  - `geloescht_am is null`
  - `besitzer = 'felix'`
  - `datum >= Stichtag`
- **Stichtag:**
  - Heute steht er nur in der Umgebung von sales-ui (`POST_AUFRAEUMEN_AB`, aus `~/sales-claw/.env`, aktuell `2026-11-08`). Ohne Stichtag ist nichts offen.
  - Der Lauf liest aus dieser Datei **nur diesen einen Schlüssel** (Vorbild: `start_desktop.py` liest aus `.env` nur `OPENFANG_API_KEY`) und filtert selbst nach `datum`.
  - Die Sicht liefert dafür `datum` mit.
- **Spalten:**
  - `id`, `datum`, `fach`, `bereich`, `sensibel`
  - Postfach-Kennung: `post_konten.kennung` bzw. `anbieter`
  - `absender_klein`: Absenderadresse kleingeschrieben, wie `ABSENDER_SQL` in `post_erledigt.py`
  - `von_anzeige`: Anzeigename
  - `betreff`, aber `null`, wenn `sensibel`
  - `absender_gesperrt` **(meine Wahl)**:
    - wahr, wenn die Absenderadresse zu einem gesperrten Lead in `sales.leads` gehört. Gesperrt heißt dasselbe wie in `rowboat_kontakte`.
    - Dann sind `absender_klein`, `von_anzeige` und `betreff` `null`.
    - Gesucht wird nur im Schema `sales`. Genauso ordnet sales-ui Absender Kontakten zu (`ABSENDER_SQL`).
- Kein `text`, kein `auszug`, keine `anhaenge`, kein `an`.
- **Zeitzone beim Stichtag** **(meine Wahl)**: derselbe SQL-Vergleich wie in sales-ui (`m.datum >= '<Stichtag>'` als Datumsliteral), in der Standard-Zeitzone der Datenbank-Sitzung. Ob die Zahlen übereinstimmen, zeigt die Abnahme.
- Grant `select` an `rowboat_leser`.

**Kundentermine:** Die Sicht `rowboat_ereignisse` hat heute schon `termin_uid`, `termin_datum` und `termin_uhrzeit` je nicht gesperrtem Kontakt. Neu kommen dazu, nur für `typ = 'termin'`:
- `termin_dauer` (`payload->>'dauer_minuten'`)
- `termin_thema` (`payload->>'thema'`)
- `termin_ort` (`payload->>'ort'`, dort steht der Jitsi-Link)

Abgesagte Termine erkennt der Lauf wie heute über `termin_abgesagt` mit gleicher `uid`.

- **Einspielen:** wie am 08.11. als `supabase_admin`, idempotent, mit Schema-Sicherung vorher und `verify_rowboat_leser.sql` danach (um die neuen Sichten erweitert).

### Notiz „Zu beantworten“

- **Pfad:** `Post/Zu beantworten.md` **(meine Wahl)**, mit `parent: "[[Post/_overview]]"`. Die Übersicht `Post/_overview.md` verweist mit `parent` auf `Company.md`, falls vorhanden, sonst ohne parent. Die Ordnungs-Karte platziert sie dann.
- **Kopf:** Stand (Uhrzeit), Zahl offener Mails, Link „alle in sales-ui“: `https://vibemind-offload-1.tail6c7d61.ts.net/posteingang`.
- **Liste:**
  - Je Mail ein Listenpunkt, keine Tabelle, damit Wiki-Links erlaubt sind (Zentrale-Regel: keine Links in Tabellen).
  - Reihenfolge wie sales-ui: am längsten wartende zuerst.
  - Inhalt je Punkt: Alter (z. B. „seit 3 Tagen“), Postfach, Absender (Name, sonst Adresse), Betreff. Ist der Absender als nicht gesperrter Kontakt bekannt, folgt ein Link `[[<Steckbrief-Pfad>]]`.
  - Gibt es einen Direktlink auf die einzelne Mail in sales-ui, wird er genutzt, sonst der Link auf `/posteingang`. Der Bauende prüft das in `sales-mcp/ui.py`.
- **Sensibel:** „vertrauliche Mail“ statt Betreff, kein Absendername.
- **Gesperrter Absender:** „Mail eines gesperrten Kontakts“, nur Alter und Postfach.
- Absender werden nur mit Kontakten aus dem Laden `sales` verknüpft, genau wie in sales-ui.
- **Grenze:** höchstens 50 Punkte, darüber der Satz „… und N weitere in sales-ui“ **(meine Wahl)**.
- **Keine offenen Mails:** Die Notiz bleibt stehen und sagt „Nichts offen“, mit Stand.
- **Schreiben:** nur bei inhaltlicher Änderung. Die Uhrzeit allein ist keine Änderung, sonst würde jede Stunde ohne Grund der Rowboat-Watcher und der Wissens-Spiegel ausgelöst.

### Termin-Notizen

- **Welche Läden** **(meine Wahl)**: alle Läden, je Firma abgelegt wie die Steckbriefe. Die bestehende Regel bleibt: Ist ein Laden falsch eingerichtet oder nicht lesbar, bleibt seine Firma unberührt.
- **Pfad:** `companys/<Firma>/Termine/<JJJJ-MM-TT HHMM> <Kontaktname>.md` **(meine Wahl)**. Bei gleichem Namen entscheidet `uid[:6]` als Zusatz, wie bei Steckbriefen.
- **Kopf:**
  - `parent: "[[companys/<Firma>/Termine/_overview]]"`
  - `kontakt: "[[<Steckbrief-Pfad>]]"`, eine Relation, die eine Linie zum Kontakt zieht
  - `datum`, `uhrzeit`, `dauer_minuten`, `termin_uid`, `kontakt_id` (Lead-ID; damit unterscheidet der Lauf „gesperrt“ von „verschwunden“)
- **Text:** Thema und Jitsi-Link (`termin_ort`). Der Lauf besitzt die ganze Notiz und schreibt sie bei jeder Datenänderung neu, wie bei den Steckbriefen. Ein Hinweis „automatisch erzeugt, nicht bearbeiten“ steht im Text **(meine Wahl)**.
- **Mitschrift:** Das spätere Mitschrift-Vorhaben schreibt eine **eigene Notiz** neben den Termin, mit `parent` auf die Termin-Notiz. Die Termin-Notiz selbst fasst es nicht an. So braucht es keine Zusammenführung von Texten.
- **Welche Termine:** alle nicht abgesagten Kundentermine. Vergangene bleiben als Verlauf stehen.
- **Was beim Löschen und Archivieren gilt** **(meine Wahl)**:
  - Abgesagter Termin, oder Kontakt jetzt gesperrt (`rowboat_kontakte.gesperrt` zur `kontakt_id`): Die Notiz wird gelöscht. Datenschutz-Löschungen kommen zuerst.
  - Termin anders verschwunden (Aktivität oder Kontakt gelöscht): Die Notiz wird nach `companys/<Firma>/Termine/_archiv/` verschoben, unter dem bestehenden Massen-Wächter. Ist der ganze Laden nicht lesbar, bleibt die Firma unberührt (bestehende Regel).
  - **Löschen, Archivieren und Umbenennen macht nur der nächtliche Geschäftsdaten-Lauf**, mit der bestehenden Obergrenze von 15 je Lauf. Der stündliche Lauf legt nur an und aktualisiert, er löscht nie. Hat sich der Pfad eines Termins geändert, steht die alte Notiz deshalb bis zur Nacht neben der neuen. Dafür bleibt das Lösch-Limit des Wissens-Spiegels sicher, auch wenn der PC tagsüber aus ist.
- **Übersicht `companys/<Firma>/Termine/_overview.md`:**
  - Kommende 14 Tage als Liste mit Links auf die Termin-Notizen, dazu die Zahl vergangener Termine.
  - Verweis nach sales-ui `/kalender`.
  - `parent: "[[companys/<Firma>/Bereiche/_overview]]"`.

### Wissens-Spiegel

- `Post/` und `companys/<Firma>/Termine/` werden nur auf der VM geschrieben.
- Der Bauende prüft in `spaces/rowboat/scripts/wissens_spiegel.py`, dass beide der VM gehören (Besitzer `vm`). So werden Löschungen der VM auf den PC übertragen, und Änderungen am PC gewinnen nicht.
- `Termine` kommt zu `VM_FIRMENORDNER`. `Post` wird bei Bedarf ergänzt.
- Dabei gleich mit erledigen: `Bereiche` gehört in `VM_FIRMENORDNER` (offener Rest vom 08.11.).
- Der Betriebs-Checkout `vibemind-betrieb` wird danach nachgezogen.

## Was dem Bauenden überlassen bleibt

- Aufteilung in Module, Namen von Funktionen und Tests.
- Ob Termine und Post ein gemeinsamer Lauf sind oder zwei Einstiege mit einem gemeinsamen Timer.
- Wie das Warten auf den Geschäftsdaten-Lauf umgesetzt wird (Sperrdatei oder `flock`).
- Die genaue Wortwahl der Notizen. Sie sind auf Deutsch, sachlich und ohne Emojis.

## Prüfen

- **Tests** mit Stub-Quelle wie bei den Geschäftsdaten:
  - gleiche Offen-Regel wie sales-ui, einschließlich Stichtag fehlt = nichts offen
  - sensibel ohne Betreff
  - gesperrter Kontakt ohne Link und ohne Termin-Notiz
  - Mail eines gesperrten Kontakts ohne Name und Betreff, aber mitgezählt
  - abgesagter Termin wird gelöscht, verschwundener archiviert, beides nur im nächtlichen Lauf
  - der stündliche Lauf löscht nie
  - keine Änderung = kein Schreiben
  - Obergrenze 50
- **Abnahme auf der VM:**
  - Zahl offener Mails in der Notiz = Zahl in sales-ui `/posteingang` (Felix).
  - Termin-Notizen hängen im Graph am Steckbrief.
  - Die Wissens-Gesundheit zeigt keine neuen losen Notizen.
- **Fork:** Renderer-Tests grün. In der gebauten Einbettung fehlt der Mail-Eintrag, und ein Copilot-Sprung „zeig Mails“ landet auf der Startseite.

## Danach (eigene Vorhaben, hier nur als Anschluss)

**4. Aufnahme mit Mitschrift**
- Standardmäßig an, mit Möglichkeit zum Nein **(Felix)**.
- Hinweis in der Einladung: Aufnahme, lokale Mitschrift und Zusammenfassung an beide Seiten, Aufnahme wird danach gelöscht, Nein per Antwort oder zu Beginn.
- Ein Termin-Rekorder am PC nimmt ohne Klick auf, sales-stt schreibt lokal ab.
- Die Mitschrift-Mail geht über die Freigabe in sales-ui, das Ergebnis kommt in die Termin-Notiz.

**5. Bewerbungen**

**6. Finanzen**
