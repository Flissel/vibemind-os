# Research-Ergebnisse sichtbar und synchron halten — Design

**Datum:** 2026-09-16
**Status:** Entwurf, zur Freigabe
**Vorgänger:** `2026-09-16-research-bubble-kopplung-design.md` (gebaut, Tasks 1–6 abgeschlossen)
**Betrifft:** `spaces/research/mcp_server.py`, `voice/python/publishing/bubble_sync/`,
`voice/electron-app/renderer/universe_canvas.js`, Betriebsregistrierung

## Ziel

Ein fertiger Research-Report ist dort sichtbar und lesbar, wo gearbeitet wird: als
aufklappbare Karte in der Bubble und als Datei im Rowboat-Vault. Und er bleibt es —
ohne dass jemand daran denken muss.

## Ausgangslage — gemessen am 2026-09-16

Der erste echte Lauf (Bubble `4bdaa482`, Artefakt `artifact_v1_01M2N6MBXXET694BXAD410GPE0`,
41 Quellen) hat ein vollständiges Ergebnis erzeugt. Sichtbar ist davon fast nichts.

### Der Sync-Arbeiter läuft nicht

Der Trigger arbeitet korrekt. Für unseren Knoten liegt ein Eintrag in der Outbox:

```
node_id aae8ea79…  operation INSERT  origin db  emitted_at 2026-09-16T13:31:21Z  applied_at NULL
```

`applied_at` ist leer. Zuletzt wurde überhaupt etwas angewendet am **2026-06-09**; seither
liegen drei unbearbeitete Einträge. Kein `worker_db_to_fs` und kein `worker_fs_to_db` läuft.
Passend dazu: `~/.rowboat/knowledge/.bubble_sync_hashes.json` trägt den Stand vom
**2026-06-19**, und eine Sheerlay-Datei existiert nirgends.

Das ist kein Code-Fehler, sondern dasselbe Betriebsmuster, das die Marketing-Dienste schon
einmal drei Tage lang stillgelegt hat: als Kindprozess gestartete Dienste überleben keinen
Neustart, und es fällt nicht auf, weil nichts scheitert — es passiert nur nichts mehr.

### Der Knoten trägt nur einen Stummel

`canvas_nodes` enthält für den Lauf drei Zeilen: Überschrift, `artifact_ref`, Quellenzahl.
Der 57-KB-Report liegt in `research_report_artifacts`, das bewusst an **keinem** Sync hängt
(offene Entscheidung D3 der Migration `20260817`).

### Die Oberfläche kennt den Typ nicht

`universe_canvas.js` behandelt als Sondertyp nur `bubble`; `research` fällt in den
Standardzweig. Der `artifact_ref` im Knoteninhalt ist eine Zeichenkette, die nichts auflöst.

### Ein Sicherheitsbefund, der das Design bindet

`universe_canvas.js:563` setzt Knoteninhalt per `innerHTML`, mit dem Kommentar im Dateikopf:
„innerHTML usage is safe — all content originates from the Python backend". Für einen
Research-Report gilt das nicht. Sein Text stammt aus einem Modell, das fremde Webseiten
gelesen hat — der am wenigsten vertrauenswürdige Inhalt im System. Ein aufgeschnapptes
`<img onerror=…>` würde ausgeführt.

### Der Ablauf ist noch gar nicht benutzbar

`research_start` erzeugt in der Vorschau Job A mit Ausgabepfad A. Beim Bestätigen erzeugt es
einen **neuen** Job B — der übergebene `final_brief` trägt aber weiterhin Pfad A. Der Agent
schreibt nach A, `research_status` wartet auf B. Wer dem dokumentierten Ablauf folgt, läuft
garantiert hinein; beim Validierungslauf wurde das nur durch eine Dateikopie von Hand
umgangen.

## Entscheidungen des Betreibers

- **Der Knoten trägt den Volltext**, nicht bloß einen Verweis. Bewusst gegen die Empfehlung
  der Migration (D1) gewählt, weil es kein neues Schema, keine zweite Outbox und keinen
  neuen Worker-Zweig braucht.
- **Ein neuer Knoten je Lauf.** Das ist der Grund, warum die erste Entscheidung tragfähig
  ist: ein Knoten wird nach dem Anlegen nie wieder angefasst, also faktisch write-once.
  Damit verfallen beide Einwände der Migration — nichts pumpt den Volltext erneut durch die
  Outbox, und es gibt kein last-write-wins-Rennen.
- **Die Karte klappt auf**, statt im Vault gelesen zu werden.

## Nicht-Ziele

- Keine eigene Sync-Spur für `research_report_artifacts`. D3 bleibt offen; die Tabelle
  bleibt reine Metadaten- und Beweisspur.
- Kein Lesefenster neben dem Canvas.
- Keine Reparatur des 300-Sekunden-Limits im `claude-code`-Treiber. Das bleibt die separate
  Architekturfrage aus dem Vorgängervorhaben.
- Keine Änderung an der bidirektionalen Bubble-Sync-Mechanik selbst.

## Architektur

### Teil 0 — Ausgabepfad aus dem bestätigten Brief übernehmen

Voraussetzung für alles andere. Bestätigt der Aufrufer mit einem `final_brief`, muss der
Lauf gegen **den Pfad laufen, der in diesem Brief steht**, statt gegen einen frisch
erzeugten.

Konkret: der Ausgabepfad im Brief hat die Form `…/research_<job_id>.md`. Aus ihm wird die
`job_id` gelesen, und dieser Job wird geführt — Job-Datei, Anfragedatei, Protokoll und
spätere Statusabfrage hängen alle daran. Es wird in diesem Fall **keine** neue `job_id`
erzeugt. Ohne `final_brief` bleibt alles wie bisher: frische `job_id`, frischer Pfad.

Fail-closed bleibt maßgeblich: enthält ein `final_brief` keinen erkennbaren Ausgabepfad, ist
das ein Fehler vor dem Start — kein stiller Rückfall auf einen neuen Pfad, denn genau der
stille Rückfall ist der heutige Fehler.

### Teil 1 — Der Sync-Arbeiter überlebt einen Neustart

Eine registrierte Aufgabe nach dem Vorbild von `VibeMind-Marketing-Dienste` (vorhanden,
Zustand `Ready`) startet beide Arbeiter bei der Anmeldung. Anforderungen:

- idempotent — mehrfaches Registrieren erzeugt keine Dubletten
- ein Prüfmodus, der nur berichtet und nichts verändert
- Protokolle in Dateien, nicht in eine Pipe (Pipe-Pumper sterben mit der startenden Sitzung)

Die drei unbearbeiteten Outbox-Einträge werden einmalig abgearbeitet, damit der bestehende
Knoten tatsächlich im Vault ankommt. Das ist zugleich der Beleg, dass der Weg trägt.

### Teil 2 — Volltext im Knoten, ein Knoten je Lauf

`_persist_result` schreibt den vollständigen Reporttext in `canvas_nodes.content`. Der Titel
trägt das Datum, damit mehrere Läufe unterscheidbar bleiben.

Die Idempotenz aus dem Vorgängervorhaben bleibt unverändert: sie verhindert Dubletten
**desselben** Laufs bei einem Wiederholungsversuch. Ein **anderer** Lauf erzeugt bewusst
einen eigenen Knoten — das ist der Unterschied, auf dem die Write-once-Eigenschaft beruht.

Der Vault braucht dafür keine Änderung: `render_canvas_note` schreibt ohnehin eine Datei je
Knoten und kennt einen User-Fence (`user_content_below_fence`). Der Report steht oberhalb,
Annotationen darunter bleiben über Syncs hinweg erhalten.

### Teil 3 — Aufklappbare Research-Karte

Drei Berührungspunkte in `universe_canvas.js`:

1. `getTypeIcon` — ein Symbol für `research`
2. `getNodeContent` — ein Zweig, der zusammengeklappt Titel, Datum, Quellenzahl und die
   ersten Zeilen der Zusammenfassung zeigt, mit Umschalter
3. CSS für den aufgeklappten Zustand mit eigenem Scrollbereich

Der Report wird **formatiert** dargestellt, nicht als rohes Markdown — 436 Zeilen mit
sichtbaren `##` und Pipe-Tabellen wären der Zweck des Aufklappens verfehlt.

**Bindend, und konkret:** Der Reportinhalt darf niemals über `innerHTML` in das Dokument
gelangen — auch nicht gefiltert. Stattdessen baut ein kleiner Renderer die Formatierung
direkt als DOM-Knoten: `createElement` für die Struktur, `textContent` für jeden Text.

Der Unterschied ist nicht kosmetisch. Wer HTML erzeugt und anschließend säubert, verlässt
sich darauf, dass der Filter vollständig ist. Wer nie HTML erzeugt, hat keinen Parse-Schritt,
der eingebettetes Markup interpretieren könnte — ein `<img onerror=…>` im Report wird dann
zu sichtbarem Text, weil es gar keinen anderen Weg gibt. Deshalb auch kein `marked`, kein
`DOMPurify`: keine neue Abhängigkeit, kein Filter, dem man vertrauen muss.

Umfang des Renderers: genau die Elemente, die diese Reports verwenden — Überschriften,
Absätze, Fettung, Listen, Links, Tabellen, Codeblöcke, Zitate. Nicht mehr. Markdown, das
er nicht kennt, erscheint als Text; das ist der richtige Ausfallmodus.

Rahmen, Titel und Umschalter dürfen weiterhin als Markup gebaut werden — sie stammen aus
unserem Code, nicht aus dem Report.

## Datenfluss

```
research_status(job_id) == done
  -> research_report_artifacts   (Metadaten, citation_count, Offenlegung)
  -> canvas_nodes                (VOLLTEXT, neuer Knoten je Lauf)
       |
       |  Trigger -> canvas_sync_outbox
       v
     worker_db_to_fs  (laeuft jetzt dauerhaft)
       |
       v
  ~/.rowboat/knowledge/<Bubble>/<Knoten>.md
       oberhalb Fence: Report        (maschinenbesessen)
       unterhalb Fence: Annotationen (bleiben erhalten)

Canvas: Karte zusammengeklappt -> aufklappen -> Scrollbereich, Inhalt escaped
```

## Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| `final_brief` ohne erkennbaren Ausgabepfad | Fehler vor dem Start, kein stiller Rückfall |
| Arbeiter bereits registriert | Registrierung ist idempotent, meldet den Fund |
| Outbox-Eintrag lässt sich nicht anwenden | bleibt unangewendet, Protokoll nennt den Grund — kein Überspringen |
| Report enthält HTML | wird als Text dargestellt, nie ausgeführt |

## Tests

- Teil 0: ein `final_brief` mit Pfad P führt zu einem Lauf, dessen Status gegen P prüft;
  ein `final_brief` ohne Pfad wird abgelehnt (auf die Fehlerart prüfen, nicht nur darauf,
  dass ein Fehler kam)
- Teil 2: der geschriebene Knoteninhalt enthält den vollen Reporttext; zwei verschiedene
  Läufe an derselben Bubble erzeugen zwei Knoten; ein Wiederholungsversuch desselben Laufs
  erzeugt keinen zweiten
- Teil 3: ein Report mit `<script>` oder `<img onerror=…>` erscheint als sichtbarer Text und
  erzeugt kein Element — dieser Test ist nicht verhandelbar
- Teil 1 wird nicht im Unittest belegt, sondern betrieblich: Aufgabe registrieren, Prozesse
  töten, Prüfmodus zeigt den Verlust, nach erneuter Anmeldung laufen sie wieder

## Validierung

Am bestehenden Lauf, nicht an einem neuen — er ist bezahlt und liegt vor:

1. Arbeiter registrieren und starten, die drei offenen Outbox-Einträge abarbeiten
2. Prüfen, dass `~/.rowboat/knowledge/` eine Datei für den Sheerlay-Knoten trägt
3. `_persist_result` auf Volltext umstellen, den bestehenden Artefakt-Datensatz erneut
   verbuchen und prüfen, dass der Knoten den Report trägt
4. Die Bubble in der Oberfläche öffnen, Karte auf- und zuklappen
5. Einen Report mit eingebettetem HTML gegen die Karte halten und belegen, dass nichts
   ausgeführt wird

## Risiken

- **Der Vault bekommt große Dateien.** 57 KB je Report, mehrere Läufe je Bubble. Für einen
  Markdown-Vault unproblematisch, aber es summiert sich; wenn es stört, ist die eigene
  Artefakt-Spur (D3) der Ausweg.
- **Der eigene Renderer deckt nicht jeden Markdown-Sonderfall ab.** Das ist der bewusst
  gewählte Preis dafür, keinem Filter vertrauen zu müssen. Unbekannte Syntax erscheint als
  Text — ein harmloser Ausfallmodus. Wenn die Reports später Konstrukte nutzen, die der
  Renderer nicht kennt, wird er erweitert, nicht durch eine Bibliothek ersetzt.
- **Die Registrierung wirkt erst nach der nächsten Anmeldung.** Bis dahin müssen die
  Arbeiter einmal von Hand gestartet werden.
- **Das 300-Sekunden-Limit bleibt bestehen.** Ein längerer Rechercheauftrag scheitert
  weiterhin, unabhängig von allem hier Beschriebenen.
