# Lieferung Block 5: Ergebnistext und Protokoll

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns). Dieses Repo ändert am
Gehirn nichts. Die Zeilenangaben gelten nach dem Stand vom 23.11. und wurden am Quelltext geprüft; bitte beim
Einbauen noch einmal gegenlesen, denn `plan_executor.py` wird gerade von anderer Seite bearbeitet und die Zeilen
verschieben sich.

## Das Problem in einem Satz

Antworten des Auftragsbuchs enthalten Inhalte von Aufträgen (Titel, Namen, Texte). Das Gehirn schreibt heute
jedes Schritt-Ergebnis ungekürzt in Standzeilen und in sein dauerhaftes Protokoll; dort haben solche Inhalte nichts
zu suchen.

## Was mitgeliefert wird

Ordner `spaces/auftragsbuch/lieferung/ergebnistext/`, nur Standardbibliothek:

- `ergebnis_text.py` mit
  - `vorschau(ziel, ergebnis) -> str | None`: für Ziele der Form `mcp:<agent>:auftragsbuch:<werkzeug>` eine
    Zeile wie `Auftrag 4aef2c02 · rowboat/vorschlaege_anlegen · fertig · punkte: 3 · Beleg bestätigt`. Sie enthält
    nur die ersten 8 Zeichen der Kennung, `an/art`, den Stand, die Länge jeder Liste auf oberster Ebene von
    `ergebnis` und den Beleg-Zustand als festes Wort (`Beleg bestätigt` oder `Beleg offen`; fehlt der Beleg,
    fehlt die Angabe). Ist kein Stand lesbar: `Auftragsbuch: Antwort ohne lesbaren Stand`.
    **Jedes andere Ziel gibt `None` zurück**, dann nimmt der Aufrufer den alten Weg.
  - `fuers_protokoll(ziel, ergebnis) -> object`: für dieselben Ziele ein kleines Wörterbuch
    (`auftrag_id`, `an`, `art`, `stand`, `listen` mit Anzahlen, `beleg`), ohne jeden Inhalt; nicht lesbar:
    `{"stand": "unlesbar"}`. **Bei jedem anderen Ziel kommt die Eingabe unverändert zurück.**
- `test_ergebnis_text.py` (10 Tests). Aufruf von der Repo-Wurzel:
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

Der Beleg steht in der Antwort nicht an fester Stelle; gelesen wird `ergebnis["beleg"]`, sonst `beleg` auf
oberster Ebene. Als bestätigt gilt `True` oder `{"bestaetigt": true}`, jeder andere Wert als offen. Bitte mit
dem tatsächlichen Beleg-Feld der Aufträge abgleichen.

## Wo einhängen

Das Schritt-Ergebnis (`result_payload`) entsteht in `brain/the_brain/core/plan_executor.py` bei 1958 und wandert
in `HopResult.result` (Rückgabe bei 2169). Das Ziel steht in `hr.target` bzw. `h.execution_target`. Vier Stellen
geben den Inhalt heute weiter:

1. **Standzeile für die Oberfläche: `_hop_event`** (ab 2461). Bei 2465 steht
   `result_preview = repr(res)[:500]`. Davor einhängen:
   ```python
   text = vorschau(hr.target or h.execution_target, hr.result)
   result_preview = text if text is not None else repr(res)[:500]
   ```
2. **Kurztext für TriBE: `_tribe_text`** (2204 bis 2216, Kürzung auf 240 Zeichen bei 2214). Dort ebenso:
   `preview = vorschau(hop.execution_target, result_payload)`, und nur wenn das `None` ist,
   `repr(result_payload)` mit der alten Kürzung.
3. **Dauerhafter Snapshot (`recorder.record`)**: in `execute` bei 1257 steht
   `"executed": {sid: asdict(hr) for sid, hr in executed.items()}` (dieselbe Zeile bei 1117 für das Ereignis
   des abgebrochenen Plans). Das ist es, was dauerhaft liegt. Dort das Ergebnis ersetzen:
   ```python
   def _fuer_protokoll(hr):
       d = asdict(hr)
       d["result"] = fuers_protokoll(hr.target, hr.result)
       return d
   "executed": {sid: _fuer_protokoll(hr) for sid, hr in executed.items()},
   ```
   Andere Ziele laufen unverändert durch, weil `fuers_protokoll` sie zurückgibt.
4. **Zustand der Schritte: `_safe_state_snapshot`** (2447, Kürzung bei 1500 Zeichen) legt `repr` jedes
   Zustandswerts ab (`"state"` im Snapshot bei 1259). Stehen Schritt-Ergebnisse dort unter ihrer
   Schritt-Kennung, muss dieselbe Ersetzung auch dort gelten; die Zuordnung Schritt zu Ziel liegt in `executed`.

Außerdem schreibt `decision_recall.record(... hop_results=hop_results_list ...)` (1194) Zusammenfassungen; bitte
prüfen, ob dort `hr.result` im Klartext verwendet wird (`_summarize` in `decision_recall.py`, 47), und
gegebenenfalls dieselbe Ersetzung anwenden.

Beim Einbauen die Datei ins Gehirn kopieren (Vorschlag: `brain/the_brain/core/ergebnis_text.py`) und dort
`from .ergebnis_text import vorschau, fuers_protokoll` verwenden.

## Prüfen nach dem Einbau

- Ein Plan mit `auftrag_lesen` auf einen Auftrag mit Titel „Mail an Anna Beispiel“ darf den Titel weder im
  Ereignis `hop_completed` noch im Snapshot-Protokoll zeigen.
- Ein Plan mit einem anderen Ziel verhält sich byte-genau wie vorher.
