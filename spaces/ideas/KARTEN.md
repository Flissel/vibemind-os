# Ideen-Space: Was eine Karte zeigt

Stand 23.11.2026. Der Bestand ist nur lesend gemessen, direkt an den Tabellen `canvas_nodes`, `ideas` und
`canvas_edges`. Die Darstellung baut die Hülle (voice). Hier stehen der Inhalt, die Regeln und die Schnittstelle.

Dieses Repo ist öffentlich: Hier stehen nur Gesamtzahlen und erfundene Beispiele, keine Namen, Titel, Inhalte
oder Kennungen aus dem Bestand.

## 1. Bestand in Zahlen

| Was | Zahl |
|---|---|
| Karten (`canvas_nodes`) | 241 |
| … davon in einer Bubble | 99 |
| … ohne Bubble (`linked_idea_id` leer, in keiner Bubble sichtbar) | 142 |
| Bubbles (`ideas`, alle ohne Eltern) | 15, davon 13 ohne eine einzige Karte |
| Verbindungen (`canvas_edges`) | 115: 70 verwandt, 33 Ober-/Unterpunkt, 12 weitergedacht |
| Karten mit Bewertung | 0 (bewertet ist nur eine Bubble) |

**Formate.** Das Format steht in `content_json.type`, nicht in `format_schema.type`. Bei den alten Karten ist
`format_schema.type` immer „object“, der Typ des gespeicherten JSON-Schemas.

| Format | Karten | ohne lesbaren Inhalt |
|---|---|---|
| Notiz (kein strukturierter Inhalt) | 151 | 120 |
| Anforderungen (`technical_specs`) | 55 | 34 |
| SWOT-Analyse | 11 | 0 |
| Pro und Contra | 8 | 0 |
| Nutzergeschichten | 8 | 0 |
| Ablauf | 3 | 0 |
| Mindmap | 3 | 0 |
| Gliederung | 1 | 0 |
| Recherche | 1 | 0 |
| Tabelle, Aufgabenliste, Kanban, Vergleich | 0 | – |

**Textlänge des Felds `content`:**

- Median 20 Zeichen, Mittel 313, Maximum gut 56 000 (die Recherche-Karte).
- 2 Karten sind ganz leer, 119 haben höchstens 20 Zeichen, 19 mehr als 200, 5 mehr als 1000.
- Bei 201 Karten wiederholt der Text nur den Titel.
- Bei 120 davon gibt es auch keinen strukturierten Inhalt: Die Karte hat nichts außer ihrem Titel.

**Warum die Notizen nicht nachzulesen sind:**

1. **Inhalt im strukturierten Feld.** Bei 91 Karten steht der Inhalt nur in `content_json`. Der Text daneben ist
   der Titel. Eine Vorschau aus `content` zeigt also z. B. „Buehnen_Plan“ unter „Buehnen_Plan“.
2. **Vorlagentext statt Inhalt.**
   - 25 der 55 Anforderungs-Karten haben keine Anforderung.
   - 36 Anforderungen lauten wörtlich „Konkrete Anforderung“.
   - 3 SWOT-Karten haben den Gegenstand „Was analysiert wird“.
3. **Änderungsdatum ohne Aussage.** Alle 241 Karten haben dasselbe `updated_at`, offenbar aus einem
   Sammel-Update. „Zuletzt geändert“ taugt deshalb nicht als Anzeige.
4. **Falsches Format im Chat-Weg.** `idea_list` meldete bei 91 Karten das Format „object“. Ist behoben (Abschnitt 3).

**Maschinell erzeugt:**

| Herkunft | Karten |
|---|---|
| Inhalt von Formatier-Agenten (`content_json.metadata.formatted_by`, z. B. SpecsAgent, SwotAgent) | 91 |
| Vorschläge aus einer anderen Karte (`metadata.ai_generated`, mit `source_title`) | 15 |
| Recherche (`node_type: research`) | 1 |
| Testmuster ohne Bubble (Titel wie „Idea 3: example idea 3“, „Alpha“/„Beta“, „Test Node“) | 101 |
| Nachweis-Karte eines Testlaufs in einer Bubble | 1 |

### Die Recherche-Karte

- **Weg.** `spaces/research/mcp_server.py`, Funktion `_persist_result`. Sobald ein Recherche-Auftrag für eine
  Bubble fertig ist (`research_status` meldet `done`), entstehen zwei Zeilen:
  - eine in `research_report_artifacts`, mit Zitatzahl und Offenlegung;
  - eine Karte mit `node_type: "research"`, dem Titel `Research: <Bubble> <Datum Uhrzeit>` und dem **vollen
    Bericht** als Markdown in `content`, an der Stelle (0, 0).

  Die Karte wird danach nie mehr angefasst; laut Kommentar im Code bringt sie den Bericht in den Vault.
- **Die Karte im Bestand** ist ein deutscher Bericht von gut 56 000 Zeichen mit rund 30 Überschriften und rund
  40 Quellen.
- **Lesbar ist sie, aber nicht auf der Karte.**
  - Der Inhalt ist ein sauberer Bericht mit Zusammenfassung, Profilen, Tabellen und Quellen.
  - Auf der Karte stehen dagegen ein englischer Vorsatz mit Uhrzeit im Titel und als Vorschau rohes Markdown:
    „# Überschrift …“ und „**Auftraggeber:** …“.
  - Den Bericht muss man aufklappen und nach Abschnitten lesen können.

## 2. Was eine Karte zeigt

**Für alle Karten gilt:**

- Nichts Technisches: keine Kennungen, kein JSON, keine Feldnamen, keine Unterstriche im Titel.
- Deutsche Beschriftungen.
- Höchstens 3 Zeilen zu je 120 Zeichen; längere Zeilen enden mit „…“.

**Zugeklappt** besteht eine Karte aus:

- dem Titel;
- einem kleinen Etikett mit der Art;
- bis zu 3 Zeilen;
- einer Fußzeile mit dem Umfang, der Herkunft und der Bewertung (falls vorhanden), z. B.
  „3 Pro · 3 Contra · Vorschlag aus ‚Familien Programm‘ · 80“;
- der Zahl der Verbindungen, falls es welche gibt.

**Ohne lesbaren Inhalt** (`leer`) zeigt die Karte statt der Zeilen „Noch ohne Inhalt“, blass. Sie behauptet dann
nichts.

**Aufgeklappt** (zum Nachlesen) zeigt die Karte:

- den ganzen Inhalt in Abschnitten, in der gespeicherten Reihenfolge;
- bei Notizen und Recherchen den Text als Markdown gerendert und eine Gliederung zum Springen;
- darunter die Verbindungen mit Titel und Art (Oberpunkt, Unterpunkt, Ausgangspunkt, Weiterentwicklung, verwandt),
  anklickbar.

Die Beispiele sind erfunden (Bubble „Stadtfest“); ihre Form entspricht dem, was der Server für Karten aus dem
Bestand liefert.

| Format (Etikett) | Zeilen zugeklappt | Umfang | Aufgeklappt | Beispiel |
|---|---|---|---|---|
| **Notiz** | erste Zeilen des Texts, ohne Aufzählungszeichen; Text gleich Titel zeigt nichts | Abschnitte, wenn mehr als einer | Markdown gerendert | „Strom Anschluss“: „Strom fuer die Buehne klaeren.“ / „Anschluesse:“ / … |
| **Pro und Contra** | „Pro: …“, „Contra: …“ | „2 Pro · 1 Contra“ | zwei Spalten Pro und Contra | „Festplatz App“: „Pro: Lageplan immer dabei“ / „Pro: Programm ohne Papier“ / „Contra: Ältere Gäste brauchen trotzdem Aushänge“ |
| **SWOT-Analyse** | „Stärken: …“, „Schwächen: …“, „Chancen: …“, „Risiken: …“ | „1 Stärke · 1 Schwäche“ | vier Felder | „Sponsoren Paket“: „Stärken: Starke Marktposition“ / „Schwächen: Mangelnde Ressourcen“ |
| **Anforderungen** | die Anforderungen selbst | „2 Anforderungen“ | Liste | „Sponsoren Paket Spec“: „Logo nur nach schriftlicher Freigabe“ / „Banner bis Freitag hängen“ |
| **Nutzergeschichten** | „Als … möchte ich …“ | „1 Geschichte“ | Liste mit Nutzen und Abnahme | „Als Kunde möchte ich teilen“ |
| **Ablauf** | die Schritte ohne Start und Ende | „1 Schritt“ | Schrittliste; das Diagramm kann die Hülle weiter zeichnen | „Antrag prüfen“ |
| **Mindmap** | Mittelpunkt, dann die Zweige | „1 Zweig“ | Zweige als Abschnitte | „Messe“ / „Licht“ |
| **Gliederung** | die Unterpunkte | „1 Punkt“ | Punkte als Abschnitte | „Wer?“ |
| **Recherche** | die ersten Sätze der Zusammenfassung; Titel „Recherche: Laternenlauf“ | „4 Abschnitte“; Herkunft „Recherche vom 15.01.2026 · 7 Quellen“ | Gliederung plus der ganze Bericht | „Der Laternenlauf trifft auf zwei Feste am selben Abend.“ |
| **Tabelle** | Zeilen als „Zelle · Zelle“ | „2 Zeilen“ | echte Tabelle mit Kopf | „Stand · 900“ / „Druck · 120“ |
| **Aufgabenliste** | Aufgaben, Zusatz „(erledigt)“, „(in Arbeit)“ oder „(blockiert)“ | „1 von 2 erledigt“ | Abhakliste | „Angebot schicken (erledigt)“ / „Anrufen“ |
| **Kanban** | „Spalte: Karte“ | „1 Karte in 2 Spalten“ | Spalten nebeneinander | „Offen: Logo“ |

Die Fälle stehen als Prüfliste in `spaces/ideas/tests/test_kartenansicht.py`.

Bei der Recherche sucht der Server zuerst den Abschnitt „Zusammenfassung“ (oder „Summary“, „Kurzfassung“, „Fazit“).
So stehen auf der Karte nicht die Kopfangaben wie „Auftraggeber: …“.

**Zwei Grenzen:**

- **Fester Formatieraufruf strukturiert nicht.** Der feste Aufruf `idea_format` (ohne Modell) setzt nur das
  Etikett. Die Karte heißt dann „Tabelle“, zeigt aber ihren Text weiter als Zeilen. Eine echte Tabelle mit Spalten
  braucht das Modell.
- **Englische Inhalte bleiben englisch.** Ein Teil der Inhalte ist englisch. Der Server übersetzt nicht.

## 3. Schnittstelle für die Hülle

**Gebaut** im Zweig `cc/ideen-karten`, Ideen-Server Version 1.2.0. Tests: `spaces/ideas/tests/test_kartenansicht.py`.

**`idea_list` (kurz, Vorgabe).** Die bisherigen Felder bleiben:

- `id`, `title`, `format`, `score`, `node_type`, `x`, `y`, `linked_idea_id`;
- `preview`: die ersten 200 Zeichen von `content`;
- `content_length`.

Neu oder geändert:

- **`format` (korrigiert):** kommt jetzt aus `content_json.type`. `pros_cons_table` heißt `pros_cons`.
  Eine Recherche heißt `research`.
- **`karte` (neu):**
  `{titel, art, zeilen[≤3], umfang, quelle, leer}`. Das ist die zugeklappte Karte aus Abschnitt 2, fertig zum
  Anzeigen.
- **Abfrage:** Der Server liest jetzt `content_json` statt `format_schema`. Die großen Schemata bleiben in der
  Datenbank.
- **Form (seit dem Folgeauftrag):** Die kurze Liste ist ein Objekt `{karten, gezeigt, gesamt, gekuerzt}`, sortiert
  oben links zuerst (`y`, `x`, Kennung).
  - `gesamt` ist die wahre Zahl der Karten. Ist die Liste voll (`limit`, Vorgabe 50), zählt der Server sie mit
    einer zweiten Abfrage, die nur die Kennungen liest.
  - Kann er nicht zählen, steht dort `null` und `gekuerzt: true`.
  - Der Deckel bleibt: Der Chat nennt ohnehin nur drei Titel, und der Agent soll nicht hundert Karten auf einmal
    lesen.
  - `full: true` bleibt eine rohe Liste.
- **Satz im Chat:** „75 Notizen in ‚Stadtfest‘: ‚…‘, ‚…‘, ‚…‘ (und 72 weitere).“ Ohne Zählung heißt es
  „Mindestens 50 Notizen …“. Den Satz bildet das Gehirn in `core/ideen_abkuerzung.py`,
  `antwort_fuer_schritt`.

**`idea_read(id)` (neu, nur lesend).** Liefert:

- `id` (nicht anzeigen);
- `titel`, `art`, `umfang`, `quelle`, `bewertung`;
- `abschnitte[{ueberschrift, zeilen[]}]`: der ganze Inhalt, ungekürzt;
- `text`: das Markdown einer Notiz oder Recherche, sonst `null`;
- `verbindungen[{id, titel, art}]`: höchstens 50; Kanten zu gelöschten Karten fallen weg;
- `leer`.

Es braucht drei Lesezugriffe: die Karte, ihre Kanten und die Titel der Nachbarn.

**`idea_get`** bleibt die Rohzeile für Werkzeuge.

**Was die Hülle wissen muss:**

1. **Die Hülle liest heute an den neuen Feldern vorbei.** Sie holt die Zeilen über Supabase Realtime selbst
   (`supabase-realtime.js`) und rendert `content_json` mit `format-renderer.js`; `idea_list` ruft sie nicht auf.
   Die Felder `karte` und `idea_read` erreichen sie also nicht von selbst. Dafür gibt es zwei Wege:
   - **(a)** Die Regeln aus Abschnitt 2 in der Hülle nachbauen. Die Fälle in `test_kartenansicht.py` sind die
     Prüfliste dafür, und es kommt kein Umweg hinzu.
   - **(b)** `idea_read` beim Aufklappen über das Gehirn aufrufen. Am festen Weg dauert das rund 1,5 bis 3 s.

   Ich empfehle (a) für die zugeklappte Karte und (b) oder ein direktes Lesen für die aufgeklappte.
2. **Werkzeug noch nicht freigegeben.** `idea_read` steht noch nicht in der Werkzeugliste von `brain-ideas`
   (`config/space_agent_registry.yml`). Das freizugeben ist Sache der Koordination.
3. **Neustart nötig.** Damit OpenFang den neuen Server benutzt, muss der Werkzeug-Dienst neu starten.
4. **Für jede Karte im Bestand vorhanden:**
   - Titel, Format, Lage;
   - Größe (`metadata.width`/`height`, meist 200 × 120);
   - Herkunft;
   - Verbindungen.

   **Fehlen:**
   - eine eigene Kurzfassung (`summary` ist bei allen 241 Karten leer);
   - ein verlässliches Änderungsdatum (alle haben dasselbe).

## 4. Aufräum-Vorschlag (nichts gelöscht; entscheidet Felix)

Die Liste mit Namen liegt nicht in diesem Repo, sondern im privaten Repo der Koordination.

- **Bubbles:** 11 offensichtliche Testreste (alle ohne Karte; Titel oder Quelle sagt „Test“, „Smoke“, „Proof“
  oder „Probe“) und 2 leere Bubbles zum Nachfragen.
- **Karten ohne Bubble: 142.** Keine Bubble zeigt sie, und alle haben dasselbe Datum.
  - 101 offensichtliche Testreste nach Titelmuster („Idea N: …“, „Alpha“/„Beta“, „Test Node“,
    „Example idea {{loop.index}}“); darunter 2 ganz leere Karten und 54 Titel-Dubletten.
  - 41 Entwicklernotizen und Vorschläge aus Probeläufen, zum Nachfragen. Sie sind inhaltlich Projektnotizen zum
    Router, keine Ideen eines Nutzers.
- **Karten in Bubbles ohne lesbaren Inhalt** (nur Titel oder Vorlagentext) sind keine Testreste, sondern
  Stichworte. Vorschlag: nicht löschen, sondern als „Noch ohne Inhalt“ zeigen; ausfüllen bei Bedarf.

**Ein Löschlauf bräuchte eine Kennungsliste.** Die stelle ich auf Zuruf zusammen. Gelöscht wird erst nach Felix'
Ja, und auch nur über das Änderungsprotokoll, damit „zurück“ greift.
