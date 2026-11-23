# Lieferung Block 6: Katalog-Eintrag und Eingabe für `rowboat.vorschlaege_anlegen`

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns). Dieses Repo ändert am
Gehirn nichts. Die Zeilenangaben sind gegen `origin/master` (`948f436f`, geprüft am 23.11.; vorher `ae9f2dfe`) nachgezogen.
Was master inzwischen an denselben Stellen geändert hat, steht in `kollisionen-948f436f.md`. Bitte beim Einbauen
trotzdem noch einmal gegenlesen, denn die Zeilen verschieben sich mit jeder weiteren Änderung.

## Das Problem in einem Satz

Ein Satz wie „Leg mir Milch kaufen; Steuer machen auf die To-do-Liste" muss im Gehirn zu genau dem
Argument werden, das der Vertrag `rowboat/vorschlaege_anlegen` (Version 2) verlangt, mit Kennung je Punkt, und
darf bei Unklarem nichts anlegen, sondern muss zurückfragen. Trenner zwischen den Punkten sind Zeilen,
Nummern, Spiegelstriche und `;`; ein „und" trennt **nicht** (ein Titel darf „und" enthalten).

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
  - Ein Titel, der nur der Name der Liste ist („To-do-Liste", „Liste.", „Aufgabenliste", „meine Liste"), ist nie
    ein Punkt, auch nicht neben anderen: Rückfrage. Das fängt das letzte Wort des Satzes ab, das der Muster-Kurzweg
    als Argument weiterreicht.
  - Rückfrage, wenn nichts übrig bleibt, wenn es nach dem Zusammenfallen mehr als 50 Punkte sind, wenn ein Titel
    länger als 200 Zeichen ist (Grenze des Vertrags; es wird nie still gekürzt), bei Nicht-Text-Einträgen, bei
    einem Objekt mit anderen Schlüsseln als `titel` oder bei kaputtem JSON (Text, der mit `[` oder `{` beginnt).
- Dritte Funktion `waehle_roh(hop_argument, plan_intent)`: gibt das Hop-Argument unverändert zurück, außer es sieht
  wie das Kurzweg-Argument aus (ein einzelnes Wort gleich dem letzten Wort der Anfrage, ohne Satzzeichen und
  Groß-/Kleinschreibung, oder ein Listenname); dann den Inhalt aus `aus_satz(plan_intent)`, und `None`, wenn der Satz
  zu keinem Anker passt (Rückfrage `RUECKFRAGE_KEIN_INHALT`). Eine Feldübergabe (Liste, Objekt, mehrere Titel) wird nie
  überschrieben. Wirft nie.
- Zweite Funktion `aus_satz(satz: str) -> str | None`: schneidet den Inhalt zwischen Ankerverb (`leg`, `lege`,
  `setz`, `setze`, `schreib`, `schreibe`, mit „mir" und „bitte") und „auf/in die|meine|unsere (To-do-|Aufgaben-)Liste"
  aus der Anfrage; Satzzeichen und nachgestelltes „, danke", „bitte" am Ende werden ignoriert. Passt der Satz nicht, `None`. Wirft nie.
- `test_vorschlaege_eingabe.py` ( darunter `test_ergebnis_passt_zum_vertrag` gegen `pruefe_eingabe` und
  den echten Vertrag; jetzt insgesamt 15 Tests) und `test_katalog_eintrag.py` (10 Tests; liest den Eintrag unten aus diesem Dokument und
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

Zum Einfügen in `brain/the_brain/data/capabilities.yaml`, direkt hinter den Eintrag `rowboat_status` (Zeile 2002
bis 2016). Muster sind Pflicht (ohne sie verwirft der Katalog den Eintrag, siehe die Anmerkung bei
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
    - "\\b(?:leg|lege|setz|setze|schreib|schreibe)\\b.{1,300}\\b(?:auf|in)\\s+(?:die|meine|unsere)\\s+(?:to-?do|aufgaben)[-\\s]?liste(?:\\s*,?\\s*(?:danke|bitte))?\\s*[.!?]*\\s*$"
    - "\\b(?:setz|setze)\\b.{1,300}\\bauf\\s+(?:meine|die)\\s+liste(?:\\s*,?\\s*(?:danke|bitte))?\\s*[.!?]*\\s*$"
    - "\\b(?:put|add)\\b.{1,300}\\bto\\s+my\\s+(?:to-?do|task)\\s+list(?:\\s*,?\\s*(?:please|thanks))?\\s*[.!?]*\\s*$"
  execution_target: "mcp:brain-rowboat:auftragsbuch:rowboat_vorschlaege_anlegen"
  validator:
    # Das Auftragsbuch antwortet mit der Auftragskennung; ob Rowboat den Vorschlag wirklich
    # angelegt hat, prueft dessen eigene Nachpruefung (nachpruefung: beleg) und `auftrag_lesen`.
    kind: "rule:non_empty_result"
    on_fail: block
```

Dazu in `brain/the_brain/data/capability_effects.yaml` eine Zeile unter `# --- external ---` (Zeile 111, neben
`desktop_skill: external`; **nicht** bei `rowboat_status: read`), sonst fällt `test_jede_capability_hat_eine_klasse` (die Klasse ist Pflicht für
jede Fähigkeit):

```yaml
  rowboat_vorschlaege_anlegen: external   # legt im Auftragsbuch an; die Spur am Ziel prueft dessen Beleg
```

**[Wahl]** `external` statt `write`: Eine `write`-Fähigkeit ohne `truth:`-Prüfung müsste auf die Restliste, und
die steht in `test_capability_effects.py` mit Obergrenze 20 genau bei 20 (`RESTLISTE_OBERGRENZE`, nur nach unten
änderbar). `event_do` (ebenfalls `external`) ist das Vorbild. Will die Koordination eine `truth:`-Prüfung gegen das
Auftragsbuch bauen, wird daraus `write`.

**Kein `agents`-Block, mit Absicht.** `brain-rowboat` ist ein Rechteträger ohne Modellrolle (Block 1, Regel 4): Er
bearbeitet keine Nachrichten. `agents.primary` ist die Quelle der Modell-Übergabe eines Schritts; stünde der Träger
dort, könnte der Router ihm eine Modell-Nachricht schicken. Es genügt `execution_target`. Das ist ohne Folgen: Der
Router liest `agents` freiwillig (`capability_router.py`: `entry.get("agents") or {}`), und ein Ziel `mcp:` springt
vor der Agentenwahl der Diskussion ab (`has_execution_target`). Im Katalog von `948f436f` haben 92 von 127 Einträgen
keinen `agents`-Block, darunter die Einträge `aenderung_zurueck` und `idea_connect` mit Ziel `mcp:`. Die Wahl des
Agenten steckt im Ziel selbst (`mcp:brain-rowboat:...`); damit entfällt auch die Abhängigkeit von Block 1, Punkt 3
(Agent bei OpenFang bekannt) für den Katalog. Prüfen tut das `test_eintrag_hat_keinen_agents_block`.

## 2. Einbau in das Gehirn

### 2.1 Ereignis-Name für die Fähigkeit

`brain/the_brain/core/capability_targets.py`, `_CANONICAL_SPACE_EVENT_IDS` (Zeile 1352): einen Eintrag ergänzen

```python
    "rowboat_vorschlaege_anlegen": "rowboat.vorschlaege_anlegen",
```

Ohne ihn bleibt `event_id` im Hop `rowboat_vorschlaege_anlegen`, das Verzeichnis kennt dann kein Ereignis dazu
(Ereignisse heißen dort `rowboat.…`), und weder das Tor aus Block 2 noch der Aufruf unten sehen es. So geht es auch
heute `rowboat_status`: Dort ist das egal, hier nicht. Master hat `desktop_task_start` ohne solchen Eintrag eingeführt;
dessen Hop behält die Kennung `desktop_task_start` und läuft über das ausdrückliche Ziel aus dem Katalog. Für
`rowboat_vorschlaege_anlegen` reicht das nicht, denn Block 2 und die Eingabe unten hängen an der Kennung
`rowboat.vorschlaege_anlegen` (siehe `kollisionen-948f436f.md`, Nr. 2).

### 2.2 Argument aus dem Satz bauen

`brain/the_brain/core/plan_executor.py`, in `_exec_hop_kern`, **direkt hinter** dem Block der festen Ideen-Aufrufe
(`if ist_fester_aufruf(event_id, deterministic_target):` ab Zeile 1626, der Block endet vor
`if event_id == "bubble.create" and not deterministic_target:` bei Zeile 1650):

```python
            if event_id == "rowboat.vorschlaege_anlegen" and deterministic_target:
                from .vorschlaege_eingabe import (
                    RUECKFRAGE_KEIN_INHALT, argumente as _vorschlaege_argumente,
                    waehle_roh as _vorschlaege_waehle_roh,
                )
                # Der Satz des Betreibers (plan_ctx["plan_intent"], wie bei idea.aenderung_zurueck, Zeile 1669)
                # ersetzt das Hop-Argument nur, wenn dieses wie das Kurzweg-Argument aussieht (ein einzelnes
                # Wort gleich dem letzten Wort des Satzes, oder ein Listenname). Eine Feldubergabe bleibt.
                roh = _vorschlaege_waehle_roh(rendered_arg, plan_ctx.get("plan_intent", ""))
                if roh is None:
                    strict_mcp_arguments, clarification = None, RUECKFRAGE_KEIN_INHALT
                else:
                    strict_mcp_arguments, clarification = _vorschlaege_argumente(
                        roh, plan_ctx.get("plan_id") or "")
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

Das ist dieselbe Gestalt wie bei den festen Ideen-Aufrufen (Zeile 1627 bis 1649): Das Wörterbuch ersetzt das
Argument, `arg_kwarg` entfällt, ein Fehler bricht den Schritt vor dem Ausführer ab. Der Ausführer bekommt
`{"eingabe": {...}, "plan_id": ...}`; das ist genau die Form des Werkzeugs (`werkzeuge.py`: erlaubt sind nur
`eingabe` und `plan_id`, Pflicht ist `eingabe`).

Die Herkunft (Block 2) prüft vorher oder in derselben Gegend; die Reihenfolge ist egal, solange die Eingabe vor dem
Ausführer gebaut wird.

## 3. Was man wissen muss

- **Letztes Wort als Argument (behoben).** Der Muster-Kurzweg (Kommentar bei `aenderung_zurueck`,
  `capabilities.yaml` ab Zeile 689) setzt das letzte Wort des Satzes als Argument ein, hier also „To-do-Liste".
  Drei Netze: Die Muster sind am Ende verankert (nur Leerraum, `.!?` und „danke"/„bitte" folgen der Liste; ein
  Satz mit weiterem Text trifft nicht), `waehle_roh` holt den Inhalt aus dem Satz (`plan_intent`, siehe 2.2) und nur
  dann, wenn das Hop-Argument wie der Kurzweg aussieht, und `argumente` lehnt einen Titel ab, der nur der Listenname
  ist. „Leg mir Milch auf die To-do-Liste, danke" ergibt „Milch", nie „danke". Bleibt die Anfrage ohne Anker (Planer hat den Satz umformuliert) und das
  Argument ist eine echte Liste von Titeln, gilt diese Liste. Empfohlen: nach dem Einbauen einen Test mit „Setz Oma
  anrufen auf meine Liste" über den ganzen Weg.
- Nur der Betreiber darf das auslösen (`required_origin: betreiber`, Block 1 und 2). Ohne die Kopfzeilen aus
  Block 2 läuft der Schritt nicht.
- `plan_id` kommt unverändert durch; das Werkzeug verlangt `^[A-Za-z0-9_-]{1,64}$`. Eine `plan_id` außerhalb davon
  lehnt erst das Werkzeug ab (mit seiner Formfehler-Meldung); `argumente` prüft sie nicht.
- Reihenfolge der Muster im Katalog: Der Eintrag muss nach dem Einfügen noch von den Mustern getroffen werden,
  bevor ein allgemeiner Eintrag einen Satz wie „Schreib … auf die Aufgabenliste" schluckt. Gegen die heutigen
  Muster stimmt das (kein anderer Eintrag trifft die Beispielsätze); die Probe steht in
  `test_katalog_eintrag.py`. Ist der Eintrag eingebaut, vergleicht der Test gegen den eingebauten Eintrag.
- **Gesamtordnung (gilt wörtlich auch in Block 1, Abschnitt 5, Punkt 5):** Der Verzeichnis-Block mit `required_origin: betreiber` kommt erst NACH dem Herkunft-Tor (Block 2); sonst wird `required_origin` still übergangen. Keine Zeile in `auftrag.brain_arten` außer `brain/probe/1`, bevor harte Sperre (Block 3), Herkunft (Block 2) und Ergebnistext (Block 5) im Betrieb stehen.
