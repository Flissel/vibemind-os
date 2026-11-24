# Lieferung Block 5: Ergebnistext und Protokoll

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns). Dieses Repo ändert am
Gehirn nichts. Die Zeilenangaben sind gegen `origin/master` (`948f436f`, geprüft am 23.11.; vorher `ae9f2dfe`) nachgezogen.
Master hat in `plan_executor.py` Zeilen eingefügt, aber keine der Einhängestellen dieses Blocks verändert
(`kollisionen-948f436f.md`, Nr. 7). Bitte beim Einbauen trotzdem noch einmal gegenlesen, denn die Zeilen
verschieben sich mit jeder weiteren Änderung.

## Das Problem in einem Satz

Antworten des Auftragsbuchs enthalten Inhalte von Aufträgen (Titel, Namen, Texte). Das Gehirn schreibt heute
jedes Schritt-Ergebnis ungekürzt in Standzeilen und in sein dauerhaftes Protokoll; dort haben solche Inhalte nichts
zu suchen.

## Was mitgeliefert wird

Ordner `spaces/auftragsbuch/lieferung/ergebnistext/`, nur Standardbibliothek:

- `ergebnis_text.py` mit
  - `vorschau(ziel, ergebnis) -> str | None`: für Ziele der Form `mcp:<agent>:auftragsbuch:<werkzeug>` eine
    Zeile wie `Auftrag 4aef2c02 · rowboat/vorschlaege_anlegen · fertig · punkte: 3 · Beleg bestätigt`. Sie enthält
    nur die ersten 8 Zeichen der Kennung, `an/art`, den Stand, die Länge jeder Liste und jede ganze Zahl ab 0 auf oberster Ebene von
    `ergebnis` (`name: n`, nicht bei `bool`, Gleitkommazahlen, negativen oder verschachtelten Zahlen), bei Stand `fehler` den Grund
    (`Grund: …`, siehe unten) und den Beleg-Zustand als festes Wort (siehe unten; fehlt der Beleg, fehlt die Angabe). Ist kein Stand lesbar: `Auftragsbuch: Antwort ohne lesbaren Stand`.
    **Jedes andere Ziel gibt `None` zurück**, dann nimmt der Aufrufer den alten Weg.
  - `fuers_protokoll(ziel, ergebnis) -> object`: für dieselben Ziele ein kleines Wörterbuch
    (`auftrag_id`, `an`, `art`, `stand`, `grund` bei `fehler`, `listen` mit Anzahlen, `zahlen`, `beleg`), ohne jeden Inhalt; nicht lesbar:
    `{"stand": "unlesbar"}`. **Bei jedem anderen Ziel kommt die Eingabe unverändert zurück.**
- `test_ergebnis_text.py` (93 Tests, darunter die Fälle zu Fehlergrund und Zahlenfeldern). Aufruf von der Repo-Wurzel:
  `python -m pytest spaces/auftragsbuch/lieferung/ergebnistext/test_ergebnis_text.py -q -p no:cacheprovider`.

Beide Funktionen werfen nie. Werte aus der Antwort werden nur übernommen, wenn sie einem engen Muster
entsprechen (Kennung als UUID, Namen klein geschrieben mit Ziffern und `_`/`-`, Listennamen klein und kurz);
alles andere wird weggelassen. So landet auch ein Titel, der als Feldname oder Stand getarnt ist, nicht im Text.

## Welche Formen `ergebnis` hat

Das Werkzeug liefert als Text einen JSON-Block (`content[0].text`): bei `auftrag_lesen` mit `auftrag_id`, `an`,
`art`, `version`, `stand`, `ergebnis`, `fehler`, `plan_id`, `frist_bis`; bei `auftrag_probe` nur `auftrag_id`,
`stand`, `vertrag_hash`; beim Anlegen `auftrag_id`, `an`, `art`, `version`, `stand`
(`spaces/auftragsbuch/werkzeuge.py`, `_probe`, `_lesen`, `ausfuehren`). Beim Gehirn kommt das als MCP-Ergebnis
`{"content": [{"type": "text", "text": "<JSON>"}], "isError": false}` an (`McpExecutor._call` gibt `result`
unverändert zurück), bei der Brücke zusätzlich eingepackt unter `result`. `vorschau` und `fuers_protokoll`
nehmen alle diese Formen: den JSON-Text selbst, das fertig gelesene Wörterbuch, das MCP-Ergebnis und die
Umhüllung mit `result`.

Der Beleg steht fest als Textfeld `beleg` auf oberster Ebene der Antwort von `auftrag_lesen` (Vorgabe aus
Aufgabe 6) mit genau einem Wert aus `entfaellt`, `bestaetigt`, `nicht_bestaetigt`, `nicht_pruefbar`. Die Vorschau
macht daraus feste Worte: `Beleg entfällt`, `Beleg bestätigt`, `Beleg nicht bestätigt`, `Beleg nicht prüfbar`.
Jeder andere Wert wird nie wiedergegeben, die Angabe fehlt dann. Die alte Form `ergebnis["beleg"]` als `True`
oder `{"bestaetigt": true}` gilt nur ersatzweise (als bestätigt), wenn oben nichts Gültiges steht.

Hat ein fertiges Ergebnis die falsche Form, antwortet `auftrag_lesen` mit `stand: "fehler"`,
`fehler: "Ergebnis in falscher Form"` und `ergebnis: null`. Die Vorschau lautet dann zum Beispiel
`Auftrag 4aef2c02 · rowboat/vorschlaege_anlegen · fehler · Grund: Ergebnis in falscher Form`.

### Fehlergrund (Abgleich mit Sales)

`auftrag_lesen` liefert `fehler` als Feld eines erfolgreichen Aufrufs. Ist der Stand `fehler`, prüft der Ergebnistext
den Text gegen eine feste Positivliste (`GRUENDE` in `ergebnis_text.py`, mit Quellen kommentiert): Rowboat-Abholer
(`rowboat/abholer.py`, `rowboat/rpc.py`), Migration 101 (`Bearbeitung bei Sales/Rowboat abgebrochen`,
`Fehler ohne Angabe`), `Zwei Versuche ohne Ergebnis` (fester Text der Datenbank bei zwei erfolglosen Versuchen:
`096_auftragsbuch.sql:120`, `098_auftragsbuch_video.sql:97`, `101_auftragsbuch_empfang.sql:194` und `197`), die sieben `GRUND_*` aus `sales-mcp/auftrag_abholer.py` (Sales, Commit 5223472) und
`Ergebnis in falscher Form` aus `werkzeuge.py`. Verglichen wird ohne Rücksicht auf Groß-/Kleinschreibung: Zuerst muss der
ganze getrimmte Text (oder der Text vor einem Zusatz, der mit `:` beginnt) auf der Liste stehen; so bleiben Sätze mit
eigenem `:` (`Abholer: interner Fehler`, `Rowboat: Schlüssel abgelehnt`) heil. Gezeigt wird immer die Schreibweise der
Liste. Alles andere, ein leerer oder fehlender Grund eingeschlossen, ergibt `Grund nicht anzeigbar`; der Rohtext
erscheint nie, weder in der Zeile noch im Protokoll. Ein Zusatz nach dem `:` wird verworfen.
Bekannte Lücke: `Ausfuehrung bei Sales gescheitert (<Typ>)` trägt seinen Zusatz in Klammern und nicht nach `:`; er
ergibt deshalb `Grund nicht anzeigbar`. Das ist gewollt vorsichtig; Sales kann den Zusatz nach `:` setzen, wenn der
Grund sichtbar sein soll.

### Zahlenfelder (Abgleich mit Sales)

Ganze Zahlen von 0 bis 10^9 auf oberster Ebene von `ergebnis` kommen als `zahlen` ins Protokoll und als `name: n` in die Zeile,
etwa `Auftrag 4aef2c02 · sales/termine_heute · fertig · anzahl: 3 · anzahl_kunden: 1 · quellen_stumm: 0`. Es gilt dasselbe
Namensmuster wie bei Listen (klein, höchstens 24 Zeichen, Ziffern und `_`); `bool`, Gleitkommazahlen, negative und
verschachtelte Zahlen fehlen, ebenso Zahlen über 10^9 (so rutscht keine Telefonnummer oder PLZ durch). Das Format folgt dem der Listen (`name: n`) und ist das Einfachste, aus dem Sales die
Sätze der `ANTWORTSAETZE.md` (Zweig `cc/space-sales`) bauen kann. Die Sätze selbst gehören nicht zu diesem Block; sie
brauchen die Zahlen je Feldname, und die stehen jetzt in `zahlen`.

## Wo einhängen

Das Schritt-Ergebnis (`result_payload`) entsteht in `brain/the_brain/core/plan_executor.py` bei 1998 und wandert
in `HopResult.result` (Rückgabe bei 2234). Das Ziel steht in `hr.target` bzw. `h.execution_target`. Vier Stellen
geben den Inhalt heute weiter:

1. **Standzeile für die Oberfläche: `_hop_event`** (ab 2526). Bei 2530 steht
   `result_preview = repr(res)[:500]`. Davor einhängen:
   ```python
   text = vorschau(hr.target or h.execution_target, hr.result)
   result_preview = text if text is not None else repr(res)[:500]
   ```
2. **Kurztext für TriBE: `_tribe_text`** (2269 bis 2281, Kürzung auf 240 Zeichen bei 2279). Dort ebenso:
   `preview = vorschau(hop.execution_target, result_payload)`, und nur wenn das `None` ist,
   `repr(result_payload)` mit der alten Kürzung.
3. **Dauerhafter Snapshot (`recorder.record`)**: in `execute` bei 1267 steht
   `"executed": {sid: asdict(hr) for sid, hr in executed.items()}` (dieselbe Zeile bei 1127 für das Ereignis
   des abgebrochenen Plans). Das ist es, was dauerhaft liegt. Dort das Ergebnis ersetzen:
   ```python
   def _fuer_protokoll(hr):
       d = asdict(hr)
       d["result"] = fuers_protokoll(hr.target, hr.result)
       return d
   "executed": {sid: _fuer_protokoll(hr) for sid, hr in executed.items()},
   ```
   Andere Ziele laufen unverändert durch, weil `fuers_protokoll` sie zurückgibt.
4. **Zustand der Schritte: `_safe_state_snapshot`** (2512, Kürzung bei 1500 Zeichen) legt `repr` jedes
   Zustandswerts ab (`"state"` im Snapshot bei 1268). Stehen Schritt-Ergebnisse dort unter ihrer
   Schritt-Kennung, muss dieselbe Ersetzung auch dort gelten; die Zuordnung Schritt zu Ziel liegt in `executed`.

Außerdem schreibt `decision_recall.record(... hop_results=hop_results_list ...)` (1204) Zusammenfassungen; bitte
prüfen, ob dort `hr.result` im Klartext verwendet wird (`_summarize` in `decision_recall.py`, 47), und
gegebenenfalls dieselbe Ersetzung anwenden.

Beim Einbauen die Datei ins Gehirn kopieren (Vorschlag: `brain/the_brain/core/ergebnis_text.py`) und dort
`from .ergebnis_text import vorschau, fuers_protokoll` verwenden.

## Prüfen nach dem Einbau

- Ein Plan mit `auftrag_lesen` auf einen Auftrag mit Titel „Mail an Anna Beispiel“ darf den Titel weder im
  Ereignis `hop_completed` noch im Snapshot-Protokoll zeigen.
- Ein Plan mit einem anderen Ziel verhält sich byte-genau wie vorher.
