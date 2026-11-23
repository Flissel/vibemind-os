# Lieferung Block 6: Katalog-Eintrag und Eingabe für `rowboat.vorschlaege_anlegen`

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns). Dieses Repo ändert am
Gehirn nichts. Die Zeilenangaben gelten nach dem Stand vom 23.11. (Basis `2a4df2e7`) und wurden am Quelltext
geprüft; bitte beim Einbauen noch einmal gegenlesen, denn `plan_executor.py` und `introspection.py` werden gerade
von anderer Seite bearbeitet und die Zeilen verschieben sich.

## Das Problem in einem Satz

Ein Satz wie „Leg mir Milch kaufen und Steuer machen auf die To-do-Liste" muss im Gehirn zu genau dem
Argument werden, das der Vertrag `rowboat/vorschlaege_anlegen` (Version 2) verlangt, mit Kennung je Punkt, und
darf bei Unklarem nichts anlegen, sondern muss zurückfragen.

## Was mitgeliefert wird

Ordner `spaces/auftragsbuch/lieferung/eingabe/`:

- `vorschlaege_eingabe.py`, nur Standardbibliothek, mit
  `argumente(roh: str | list | dict, plan_id: str) -> tuple[dict | None, str | None]`.
  - Nimmt eine JSON-Liste von Titeln (als Text oder als Liste), ein Objekt `{"titel": [...]}` oder Text mit
    Zeilen, führender Nummerierung (`1.`, `2)`), Spiegelstrichen (`-`, `*`) oder `;` als Trenner.
  - Liefert `({"eingabe": {"punkte": [...]}, "plan_id": plan_id}, None)` oder `(None, <Rückfrage>)`. Nie eine
    Ausnahme.
  - Jeder Punkt hat genau `punkt_id`, `titel`, `frist` (immer `null`) und `link` (immer `""`).
  - `punkt_id` sind die ersten 16 Zeichen von `sha256("vorschlag:" + Titel)`, wobei der Titel vorher
    zusammengefasst (Leerraum zu einem Leerzeichen, Rand abgeschnitten) und kleingeschrieben wird. Das passt zu
    `#([a-f0-9]{16})$`, womit der Abholer die Kennung aus dem Vorschlagstext liest (`rowboat/text.py`).
  - Der Titel im Ergebnis ist zusammengefasst und abgeschnitten, aber in der Schreibweise des ersten Vorkommens.
    Gleiche Titel (nach der Kleinschreibung) fallen zu einem Punkt zusammen.
  - Rückfrage, wenn nichts übrig bleibt, wenn es nach dem Zusammenfallen mehr als 50 Punkte sind, wenn ein Titel
    länger als 200 Zeichen ist (Grenze des Vertrags; es wird nie still gekürzt), bei Nicht-Text-Einträgen, bei
    einem Objekt mit anderen Schlüsseln als `titel` oder bei kaputtem JSON (Text, der mit `[` oder `{` beginnt).
- `test_vorschlaege_eingabe.py` (7 Tests, darunter `test_ergebnis_passt_zum_vertrag` gegen `pruefe_eingabe` und
  den echten Vertrag) und `test_katalog_eintrag.py` (4 Tests; liest den Eintrag unten aus diesem Dokument und
  prüft ihn gegen den echten Katalog). Aufruf von der Repo-Wurzel, je Datei einzeln:
  `python -m pytest spaces/auftragsbuch/lieferung/eingabe/test_vorschlaege_eingabe.py -q -p no:cacheprovider`
  `python -m pytest spaces/auftragsbuch/lieferung/eingabe/test_katalog_eintrag.py -q -p no:cacheprovider`

Beim Einbauen `vorschlaege_eingabe.py` ins Gehirn kopieren (Vorschlag: `brain/the_brain/core/vorschlaege_eingabe.py`).

Die Rückgabe folgt den festen Aufrufen des Ideen-Space (`brain/the_brain/core/ideen_fest_contract.py`,
`feste_argumente(event_id, wert)`): Argumente oder eine Rückfrage der Form `clarification required: ...`, nichts
wird geraten oder umbenannt. Ein Unterschied nach der Vorgabe der Aufgabe: Der Erfolgsfall gibt als zweites Glied
`None` zurück, `feste_argumente` gibt dort `""`. Der Einbau unten prüft nur `is None` beim ersten Glied, wie der
Ideen-Aufruf.

## 1. Katalog-Eintrag

Zum Einfügen in `brain/the_brain/data/capabilities.yaml`, direkt hinter den Eintrag `rowboat_status` (Zeile 1983
bis 1996). Muster sind Pflicht (ohne sie verwirft der Katalog den Eintrag, siehe die Anmerkung bei
`aenderung_zurueck`, Zeile 689 ff.). Die Muster sind eng gefasst und wurden gegen alle Muster des Katalogs
geprüft: Die Beispielsätze treffen nur diesen Eintrag, „Setz einen Timer auf 5 Minuten" und „Rowboat Status" treffen
ihn nicht. Die Datei selbst ist hier **nicht** geändert.

```yaml
- capability: rowboat_vorschlaege_anlegen
  description: "put a list of to-do items on the operator's Rowboat to-do list as suggestions, handed over through the order book"
  anchor_phrases:
    - "Leg mir ... auf die To-do-Liste"
    - "Setz ... auf meine Liste"
    - "Schreib ... auf die Aufgabenliste"
  match_patterns:
    - "\\b(?:leg|lege|setz|setze|schreib|schreibe)\\b.{1,300}\\b(?:auf|in)\\s+(?:die|meine|unsere)\\s+(?:to-?do|aufgaben)[-\\s]?liste\\b"
    - "\\b(?:setz|setze)\\b.{1,300}\\bauf\\s+(?:meine|die)\\s+liste\\s*[.!]?\\s*$"
    - "\\b(?:put|add)\\b.{1,300}\\bto\\s+my\\s+(?:to-?do|task)\\s+list\\b"
  execution_target: "mcp:brain-rowboat:auftragsbuch:rowboat_vorschlaege_anlegen"
  validator:
    # Das Auftragsbuch antwortet mit der Auftragskennung; ob Rowboat den Vorschlag wirklich
    # angelegt hat, prueft dessen eigene Nachpruefung (nachpruefung: beleg) und `auftrag_lesen`.
    kind: "rule:non_empty_result"
    on_fail: block
  agents:
    primary: [brain-rowboat]
    supporting: []
```

Dazu in `brain/the_brain/data/capability_effects.yaml` eine Zeile bei den `read`/`external`-Einträgen (Zeile 98
ff., neben `rowboat_status: read`), sonst fällt `test_jede_capability_hat_eine_klasse` (die Klasse ist Pflicht für
jede Fähigkeit):

```yaml
  rowboat_vorschlaege_anlegen: external   # legt im Auftragsbuch an; die Spur am Ziel prueft dessen Beleg
```

**[Wahl]** `external` statt `write`: Eine `write`-Fähigkeit ohne `truth:`-Prüfung müsste auf die Restliste, und
die steht in `test_capability_effects.py` mit Obergrenze 20 genau bei 20 (`RESTLISTE_OBERGRENZE`, nur nach unten
änderbar). `event_do` (ebenfalls `external`) ist das Vorbild. Will die Koordination eine `truth:`-Prüfung gegen das
Auftragsbuch bauen, wird daraus `write`.

Zu `agents.primary: [brain-rowboat]`: Der Katalog prüft Agentennamen beim Start gegen OpenFang; der Agent muss also
vorher bekannt sein (Block 1, Reihenfolge Punkt 3), sonst warnt der Router.

## 2. Einbau in das Gehirn

### 2.1 Ereignis-Name für die Fähigkeit

`brain/the_brain/core/capability_targets.py`, `_CANONICAL_SPACE_EVENT_IDS` (Zeile 1347): einen Eintrag ergänzen

```python
    "rowboat_vorschlaege_anlegen": "rowboat.vorschlaege_anlegen",
```

Ohne ihn bleibt `event_id` im Hop `rowboat_vorschlaege_anlegen`, das Verzeichnis kennt dann kein Ereignis dazu
(Ereignisse heißen dort `rowboat.…`), und weder das Tor aus Block 2 noch der Aufruf unten sehen es. So geht es auch
heute `rowboat_status`: Dort ist das egal, hier nicht.

### 2.2 Argument aus dem Satz bauen

`brain/the_brain/core/plan_executor.py`, in `_exec_hop_kern`, **direkt hinter** dem Block der festen Ideen-Aufrufe
(`if ist_fester_aufruf(event_id, deterministic_target):` ab Zeile 1616, der Block endet vor
`if event_id == "bubble.create" and not deterministic_target:` bei Zeile 1640):

```python
            if event_id == "rowboat.vorschlaege_anlegen" and deterministic_target:
                from .vorschlaege_eingabe import argumente as _vorschlaege_argumente
                strict_mcp_arguments, clarification = _vorschlaege_argumente(
                    rendered_arg, plan_ctx.get("plan_id") or "")
                if strict_mcp_arguments is None:
                    return HopResult(
                        step_id=hop.step_id, ok=False, error=clarification,
                        capability=hop.capability, target=target,
                        rendered_arg=rendered_arg, kg_hits=kg_hits,
                        elapsed_s=time.time() - t0,
                        contract_pass=False, reward=-1.0,
                    )
                rendered_arg = strict_mcp_arguments
                hop.arg_kwarg = None
                # Eine Wiederholung nach einer Antwort-Panne waere ein zweiter Auftrag.
                single_plan_attempt = True
```

Das ist dieselbe Gestalt wie bei den festen Ideen-Aufrufen (Zeile 1617 bis 1639): Das Wörterbuch ersetzt das
Argument, `arg_kwarg` entfällt, ein Fehler bricht den Schritt vor dem Ausführer ab. Der Ausführer bekommt
`{"eingabe": {...}, "plan_id": ...}`; das ist genau die Form des Werkzeugs (`werkzeuge.py`: erlaubt sind nur
`eingabe` und `plan_id`, Pflicht ist `eingabe`).

Die Herkunft (Block 2) prüft vorher oder in derselben Gegend; die Reihenfolge ist egal, solange die Eingabe vor dem
Ausführer gebaut wird.

## 3. Was man wissen muss

- **Letztes Wort als Argument.** Der Kommentar bei `aenderung_zurueck` (`capabilities.yaml` ab Zeile 689) hält fest:
  Die Abkürzung über Muster setzt das letzte Wort des Satzes als Argument ein. Für diese Fähigkeit hieße das, dass
  aus „…auf die To-do-Liste" ein einziger Punkt „To-do-Liste" werden könnte. `argumente` kann das nicht
  erkennen (es sieht nur das Argument, nicht den Satz). Wer die Abkürzung zulässt, muss sie hier wie bei
  `aenderung_zurueck` abfangen (`gruppe_aus_plan_argument` nimmt nur Eingaben, die wörtlich in der Anfrage stehen).
  Das ist eine eigene Entscheidung und nicht Teil dieser Lieferung. Empfohlen: nach dem Einbauen einen Test mit
  „Setz Oma anrufen auf meine Liste" über den ganzen Weg.
- Nur der Betreiber darf das auslösen (`required_origin: betreiber`, Block 1 und 2). Ohne die Kopfzeilen aus
  Block 2 läuft der Schritt nicht.
- `plan_id` kommt unverändert durch; das Werkzeug verlangt `^[A-Za-z0-9_-]{1,64}$`. Eine `plan_id` außerhalb davon
  lehnt erst das Werkzeug ab (mit seiner Formfehler-Meldung); `argumente` prüft sie nicht.
- Reihenfolge der Muster im Katalog: Der Eintrag muss nach dem Einfügen noch von den Mustern getroffen werden,
  bevor ein allgemeiner Eintrag einen Satz wie „Schreib … auf die Aufgabenliste" schluckt. Gegen die heutigen
  Muster stimmt das (kein anderer Eintrag trifft die Beispielsätze); die Probe steht in
  `test_katalog_eintrag.py`.
