# Kollisionen der Lieferung mit `origin/master` (`948f436f`)

Für die Koordination. Die sechs Blöcke (`block-1-rechtetraeger.md` bis `block-6-eingabe.md`) nennen Einbaustellen
als Datei und Zeile gegen `ae9f2dfe`. Seitdem hat `origin/master` (`948f436f`) die Desktop-Schleife, die Herkunft
„getippt“ mit Zeichen und den Prüfer `truth:desktop_sicht` bekommen. Alle Zeilenangaben in den Blöcken sind auf
`948f436f` nachgezogen. Hier steht nur, wo master an Code **geändert** hat, an den ein Block anhängt oder den er
beschreibt, und was die Koordination deshalb anders machen muss. Gelesen wurde ausschließlich mit
`git show origin/master:<Pfad>` und `git diff ae9f2dfe origin/master -- <Pfad>`; nichts ausgecheckt oder gemergt.

Kurz: Nr. 1 ist entschieden und umgesetzt, Nr. 2 verlangt eine Entscheidung vor dem Einbau von Block 2. Nr. 3 ist eine Naht an derselben Stelle
wie Block 4. Nr. 4 bis Nr. 9 sind Hinweise ohne Eingriff in die Blöcke.

## Welche Dateien sich bewegt haben

| Datei | Wirkung auf die Zeilen der Blöcke |
| --- | --- |
| `brain/the_brain/core/plan_executor.py` | +5 nach Zeile 304, +1 nach 729, +4 nach 841, +30 nach 1830, +20 nach 1959, +5 nach 1983; Stellen ab 726 verschieben sich um 5 bis 65 |
| `brain/the_brain/core/capability_targets.py` | +5 nach Zeile 1088; alles bis 1088 unverändert, danach +5 |
| `brain/the_brain/web/routers/introspection.py` | +14 nach 2548, `pe.execute`-Aufruf neu gefasst (aus 1 Zeile werden 11), +9 danach |
| `brain/the_brain/data/capabilities.yaml` | +22 nach Zeile 1775; `rowboat_status` steht jetzt bei 2002 bis 2016 |
| `brain/the_brain/data/capability_effects.yaml` | +1 nach Zeile 48; Abschnitt `# --- external ---` steht bei 111 |
| `config/space_agent_registry.yml` | +25 im Space `desktop` (Zeile 207 bis 247); `rowboat` und `sales` unverändert, nur 25 Zeilen weiter unten |
| `capability_validator.py`, `world_observer.py` | neue Prüfarten, siehe Nr. 6 und Nr. 9; keine Zeile eines Blocks verschoben |

Nicht bewegt: `scripts/sync_openfang_agents.py`, `step_space.py`, `space_contract.py`, `nachfasser.py`,
`openfang_runtime_authority.py`, `deterministic_gateway.py`, die Verträge unter `spaces/_contract/` und das
Unterprojekt `openfang` (Block 3). Deren Zeilen gelten wie bisher.

## Nr. 1 Herkunft „getippt“ gegen Block 2 (`EINGAENGE_BETREIBER`)

**Was master geändert hat.** `PlanExecutor.execute(...)` hat den neuen Parameter `eingabe_herkunft`
(`plan_executor.py` Zeile 735), und `plan_ctx` trägt `"eingabe_herkunft"` (Zeile 850). Gesetzt wird er in
`multihop_execute` (`introspection.py` Zeile 2849 bis 2859) aus `core/desktop_tagesrest.py:herkunft_pruefen`. Der
Wert ist `"getippt"` oder `None`, und `"getippt"` gilt nur, wenn alles zugleich stimmt:

- kein Plan vom Aufrufer mitgebracht,
- `body["context"]["eingabe"] == "getippt"`,
- Kopfzeile `X-Vibemind-Eingabe` hat die Form `<Zeit>.<HMAC-SHA-256 über Zeit und Satz>` mit dem gemeinsamen
  Schlüssel aus der Umgebungsvariable `VIBEMIND_EINGABE_SCHLUESSEL` (mindestens 32 Zeichen),
- die Zeit liegt höchstens 60 Sekunden zurück.

Genutzt wird der Wert in `plan_executor.py` (Zeile 1841 ff., Prüfung bei 1847: ohne „getippt“ kein Start von `desktop_task_start`) und
in `desktop_schleife.nach_aufgabenstart` (zweite Prüfung, Zeile 365).

**Was Block 2 daneben setzt.** Ein Eingangsname (`app`, `telegram-betreiber`) aus den Kopfzeilen `X-Brain-Eingang` und
`X-Brain-Eingang-Schluessel` gegen einen festen Hash je Eingang (`BRAIN_EINGANG_<NAME>_SHA256`), als Parameter
`herkunft` und Eintrag `plan_ctx["herkunft"]`.

**Betroffen:** Block 2 (Eingang, Durchreichen, Tor, Laufzeitgrenze), Block 6 (hängt über `required_origin: betreiber`
an Block 2), Block 1 (nur über den Gesamtablauf).

Die Überschneidung im Einzelnen:

1. Zwei Mechanismen für dieselbe Frage am selben Endpunkt: zwei Kopfzeilen-Sätze, zwei Schlüssel, zwei Parameter, zwei
   Einträge in `plan_ctx`. Textlich kollidieren beide Änderungen an der Signatur von `execute` (Zeile 726 bis 736),
   an `plan_ctx` (Zeile 832 bis 852) und am Aufruf `pe.execute(...)` in `multihop_execute`.
2. Das Wort „getippt“ steht **nicht** in `EINGAENGE_BETREIBER` und kann dort auch nicht hinein: `eingang_pruefen`
   erzeugt nur Namen aus dieser Menge, master erzeugt `"getippt"` an anderer Stelle mit anderem Beleg.
3. Die Stärke ist verschieden. Das Zeichen von master gilt für genau einen Satz und 60 Sekunden; der Schlüssel in
   Block 2 reist unverändert in jeder Anfrage (steht dort unter „Grenzen“).
4. Block 2 sagt „die Herkunft kommt nie aus `context`“. master liest `context["eingabe"]` zwar, vertraut ihm aber nur
   zusammen mit dem Zeichen. Das widerspricht Block 2 nicht, sollte aber im Einbau-Text stehen, damit es niemand für
   eine Lücke hält.
5. master gilt nur für **getippte** Sätze. Gesprochene Sätze und der Betreiber-Chat über Telegram bekommen von master
   keine Herkunft. Soll `rowboat.vorschlaege_anlegen` auch gesprochen oder über Telegram laufen, braucht es weiter den
   Mechanismus aus Block 2.
6. Der Pin der Hülle (`voice`) steht jetzt auf einem Stand, dessen Commit-Text „Zeichen für getippte Sätze“ nennt. Die
   Hülle sendet also schon `X-Vibemind-Eingabe`. Den Inhalt der Hülle konnte ich hier nicht lesen.

**Entschieden, so umgesetzt** (Koordination, Block 2 ist umgeschrieben):

- Ein gültiges „getippt“ (Zeichen `X-Vibemind-Eingabe`, Prüfung weiter `herkunft_pruefen` von master, nicht
  nachgebaut) zählt als Betreiber-Herkunft für `required_origin: betreiber`. Es hat Vorrang: Ist getippt gültig, wird
  der Eingang nicht geprüft.
- Der Eingangsschlüssel (`X-Brain-Eingang …`) gilt nur für Eingänge ohne getippten Satz, zum Beispiel den Telegram-Chat
  des Betreibers. Über diese Eingänge gibt es keine Desktop-Aufgaben. `desktop.task` bleibt an „getippt“ gebunden und
  bekommt **kein** `required_origin`.
- Eine Funktion an einer Stelle: `execute()` bekommt **ein** Herkunfts-Objekt (`herkunft_bestimmen`). Daraus kommen
  `plan_ctx["herkunft"]` (das Objekt) und `plan_ctx["eingabe_herkunft"]` (`eingabe_herkunft(h)`, `"getippt"` oder
  `None`). Der Parameter `eingabe_herkunft` von `execute` **entfällt ohne Alias** (Begründung in Block 2, Abschnitt 2: sonst schaltet eine einzige Zeichenkette `desktop.task` frei). Umzustellen: `introspection.py:2854` (`herkunft=` statt `eingabe_herkunft=`); `test_desktop_schleife.py:1001` setzt `plan_ctx` direkt und bleibt; `test_desktop_schleife.py:863` prüft den Quelltext und bleibt wahr; `plan_executor.py:735` und `:850` sind die Einbaustellen. Aufrufer von `execute(..., eingabe_herkunft="getippt")` wurden auf master nicht gefunden.
- Plan-Rest und Nachfasser tragen `weitergeben(h)` weiter: `{art, weitergegeben: True}`; zählt als Betreiber, ergibt nie
  `"getippt"`. Der Nachfasser ruft `weitergeben(rest.get("herkunft"))` (der Rest ist gespeicherte Daten).
- Die Textstellen des Einbaus (`plan_executor.py` Zeile 726 bis 736, 832 bis 852, 1841 bis 1847,
  `desktop_schleife.nach_aufgabenstart` Zeile 366, `multihop_execute` Zeile 2849 bis 2859) stehen in
  `block-2-herkunft.md`; die Funktionen in `herkunft/herkunft.py` (28 Tests).

## Nr. 2 `desktop_task_start`: neuer Weg über das ausdrückliche Ziel (Block 2, Block 6)

**Was master geändert hat.** Neue Fähigkeit `desktop_task_start` (`capabilities.yaml` Zeile 1776 bis 1797) mit
`execution_target: mcp:brain-desktop:spaces-desktop:desktop_task_start`. Sie steht **nicht** in
`_CANONICAL_SPACE_EVENT_IDS` (`capability_targets.py` Zeile 1352). Ihr Hop behält damit die Kennung
`desktop_task_start`; `canonical_agent` und `deterministic_target` bleiben leer (`plan_executor.py` Zeile 1546 ff.),
der Hop läuft über das Ziel aus dem Katalog. Davor setzt master ab Zeile 1841 ein eigenes Tor (nur aus „getippt“,
Satz nur aus der Absicht, nie doppelt starten).

**Betroffen:** Block 2, Abschnitt 3 (Tor im Hop) und der dort verlangte Test; Block 6, Abschnitt 2.1.

- Block 2 beschreibt das Tor über `event_id` nach dem Desktop-Block. Für `desktop_task_start` ist `event_id` aber
  `desktop_task_start`, im Verzeichnis gibt es dazu kein Ereignis. Dieser Hop wird nur vom Tor für das **ausdrücklich
  genannte Ziel** (Zeile 1471 bis 1497 und 1919 bis 1924) erfasst, das das Ereignis über Agent, Server und Werkzeug
  findet. Der Test „`desktop_skill`- und `browser_automation`-Hop“ aus Block 2 deckt diesen Weg nicht ab.
- Block 6 bleibt wie geschrieben richtig: Der Eintrag in `_CANONICAL_SPACE_EVENT_IDS` ist für
  `rowboat_vorschlaege_anlegen` nötig; master zeigt nur, dass man es auch ohne kann, wenn man auf das Tor des
  ausdrücklichen Ziels setzt. Block 6 und Block 2 sind für `rowboat.vorschlaege_anlegen` auf die Kennung angewiesen.
- Die Ablehnungsmeldung von Block 2 („Nur aus einer Eingabe des Betreibers“) kennt `core/desktop_antwort.py` nicht;
  dort werden Fehlertexte über feste Teilstrings in Sätze übersetzt (`_FEHLER`). Wer Block 2 für Desktop-Ereignisse
  einschaltet, bekommt statt des festen Satzes die Rückfall-Meldung.

**Was die Koordination anders machen muss:** Beim Einbau von Block 2 je einen Test für den Hop `desktop_task_start`
ohne und mit Zeichen ergänzen (Reihenfolge der beiden Tore ist egal, die Meldung der ersten gewinnt). Block 6 wie
beschrieben einbauen, Eintrag in `_CANONICAL_SPACE_EVENT_IDS` nicht weglassen.

## Nr. 3 Neuer Fall in `McpExecutor._call` an der Stelle von Block 4

**Was master geändert hat.** Im Zweig `if result.get("isError") is True:` (`capability_targets.py` Zeile 1088) steht
jetzt zuerst ein Fall für `self.server == "spaces-desktop"` (Zeile 1089 bis 1093): nur der Fehlercode kommt durch
(`fehlercode_aus_ergebnis`), nie freier Text. Das generische `raise RuntimeError("OpenFang MCP tool returned
isError=true")` steht nun bei Zeile 1094 statt 1089. Der Fall für `spaces-ideas` davor (Zeile 1078 bis 1087) und der
JSON-RPC-Zweig (Zeile 1050 bis 1072) sind unverändert.

**Betroffen:** Block 4 (Fehlermeldung), genau der zweite Einbau („bei 1088/1089“).

**Was die Koordination anders machen muss:** Den Fall für `auftragsbuch` im selben `isError`-Zweig neben den neuen
Fall setzen, entweder vor `spaces-desktop` oder davor/danach als eigener `if`; nie zwischen `if` und `raise` des
Desktop-Falls. Das Muster ist dasselbe (Code statt Rohtext), es besteht kein inhaltlicher Konflikt, aber der
Einbau-Text von Block 4 passt zeilenweise nicht mehr. Im Block ist das vermerkt.

## Nr. 4 Verzeichnis: neue Desktop-Ereignisse, `rowboat` und `sales` unverändert (Block 1, Block 2)

**Was master geändert hat.** Im Space `desktop` neu: die Werkzeuge `desktop_elements`, `desktop_task_step`,
`desktop_blick` unter `mcp_tools`, die Ereignisse `desktop.blick`, `desktop.elements` und `desktop.task.step`
(Letzteres mit `requires_confirmation: true`). Die Blöcke `rowboat` (jetzt ab Zeile 350) und `sales` (ab 492) sind
inhaltlich unverändert; neue Einträge „neben“ ihnen gibt es nicht.

**Betroffen:** Block 1 (Wortlaut für das Verzeichnis, `validate_mcp_authority`, `validate_rights_carrier`), Block 2
(Abschnitt 5).

- Der Wortlaut aus Block 1 mischt sich unverändert in das Verzeichnis von master; `test_gleichstand.py` zeigt das
  (siehe unten). Auch `validate_mcp_authority`, `validate_rights_carrier` und `validate_mcp_tool_scopes` aus
  `scripts/sync_openfang_agents.py` melden gegen die Datei von master keinen Fehler.
- Block 2 nennt `desktop.task` (jetzt Zeile 276). Dort steht weiterhin kein `required_origin`, siehe Nr. 1.
- Das Feld `required_origin` kennt auf master noch nichts. Block 1 vermerkt als offene Annahme, dass der Lader es
  toleriert; die Desktop-Änderungen berühren den Lader nicht.

**Was die Koordination anders machen muss:** Nichts am Wortlaut. Beim Einmischen die Zeilen von master zugrunde
legen (Verzeichnis ist 25 Zeilen länger).

## Nr. 5 Neue Aufrufer von `find_registered_mcp_authority` und `RuntimeInvocationContext` (Block 1, Block 2)

**Was master geändert hat.** `core/desktop_schleife.py` (Zeile 222 bis 250) und `core/desktop_sicht.py` (Zeile 120
bis 140) bauen je festen Aufruf einen eigenen `RuntimeInvocationContext` und rufen dafür
`find_registered_mcp_authority("brain-desktop", "spaces-desktop", <werkzeug>)` auf; ausgeführt wird über die echte
`McpExecutor`-Klasse. Das sind neue Aufrufer neben dem Planausführer (Zeile 1918 bis 1927).

**Betroffen:** Block 1 (`_mcp_authority` und `find_registered_mcp_authority` ändern sich), Block 2, Abschnitt 4
(`RuntimeInvocationContext` bekommt `herkunft`).

- Block 1: Für `desktop` gibt es keinen Träger; die Auflösung muss für Spaces ohne `rights_carriers` genau das
  bisherige Ergebnis liefern, sonst bricht die Desktop-Schleife. Die Tests der Schleife
  (`test_desktop_schleife.py`) sind dafür die Gegenprobe und sollten nach dem Einbau von Block 1 laufen.
- Block 2: Beide neuen Kontexte werden mit Schlüsselwörtern gebaut, das neue Feld mit Vorgabewert `"unbekannt"` stört
  also nicht. Sie tragen aber **keine** Herkunft. Das ist heute folgenlos, weil keines ihrer Ereignisse
  `required_origin` hat. Wer später `required_origin` an `desktop.task.step`, `desktop.elements` oder `desktop.blick`
  setzt, sperrt damit die Schleife und die Nachprüfung `truth:desktop_sicht`.

**Was die Koordination anders machen muss:** Block 1 mit dem Lauf der Desktop-Tests abnehmen. Kein
`required_origin` an die drei Ereignisse der Schleife setzen, solange die Schleife ihre Kontexte ohne Herkunft baut.

## Nr. 6 Katalog und Wirkungsklassen: neue Fähigkeit und neue Prüfarten (Block 6)

**Was master geändert hat.**

- Neue Fähigkeit `desktop_task_start` mit dem Muster `^\s*desktop[-\s]?aufgabe\s*:\s*\S` (nur Satzanfang). Steht
  vor der Stelle, an der Block 6 einfügt (Zeile 1776 gegen 2018).
- `capability_effects.yaml`: Eintrag `desktop_task_start: write` (Zeile 49) im Abschnitt „write, mit truth“. Die
  `restliste` blieb bei 20 Einträgen; `RESTLISTE_OBERGRENZE` ist unberührt. Der Abschnitt `# --- external ---` ist
  eine Zeile nach unten gerückt (111).
- `capability_validator.py`: neue Prüfarten `truth:desktop_aufgabenbuch` und `truth:desktop_sicht`
  (`world_observer.py`: zwei neue Prüfungen dazu). Keine Fähigkeit des Katalogs nutzt `truth:desktop_sicht` bisher.

**Betroffen:** Block 6, Abschnitt 1.

- Muster: Kein Satz aus der Beispielliste von Block 6 trifft `desktop_task_start`, und kein Satz, der mit
  „Desktop-Aufgabe:“ beginnt, ist von Block 6 betroffen, **außer** er enthält zugleich „… auf die To-do-Liste“. Dann
  gewinnt `desktop_task_start`, weil es im Katalog früher steht und der Router den ersten Treffer nimmt. Das ist
  gewollt; es gibt aber keinen Test dafür.
- Das `external` für `rowboat_vorschlaege_anlegen` bleibt richtig: Kein Eintrag der Restliste kommt hinzu. `write`
  mit `truth:desktop_*` hat damit nichts zu tun.
- Block 6 braucht keine der neuen Prüfarten. Der Ausgang bleibt `rule:non_empty_result`.

**Was die Koordination anders machen muss:** Eintrag hinter `rowboat_status` (jetzt Zeile 2002 bis 2016) einfügen,
nicht hinter `desktop_task_start`, damit die Reihenfolge so bleibt. Optional einen Test für den Satz
„Desktop-Aufgabe: … auf die To-do-Liste“ anlegen.

## Nr. 7 Einschübe in `plan_executor.py` und `introspection.py` neben den Stellen von Block 5 und Block 6

**Was master geändert hat.**

- `plan_executor.py` Zeile 2000 bis 2019 (nach `result_payload = last.get("result")` bei 1998): Nach einem Hop auf
  `desktop_task_start` ersetzt `nach_aufgabenstart(...)` Ergebnis, Erfolg und Fehler und startet die Schleife im
  Hintergrund. Dazu Zeile 2044 ff.: `truth:desktop_sicht` bekommt die Plan-Angaben als `_laufzeit` am Prüfer.
- `introspection.py`: `_feste_antwort` hat einen Zweig für Fähigkeiten, die mit `desktop_` beginnen (Zeile 2549 ff.),
  und `multihop_execute` hängt `out["sicht"]` an die Antwort (Zeile 2870 ff.), nur für den Chat, nie in den
  Schnappschuss.

**Betroffen:** Block 5 (Standzeile, `_tribe_text`, Schnappschuss, `_safe_state_snapshot`), Block 6 (Einschub hinter den
festen Ideen-Aufrufen, Zeile 1626 bis 1650).

- Keine der vier Einhängestellen von Block 5 und nicht der Einschub von Block 6 wurde verändert oder verschoben,
  außer um die oben genannten Zeilenzahlen. Der Einschub für `desktop.task` liegt **hinter** der Stelle von Block 6
  (Zeile 1841 gegen 1650) und vor der Stelle von Block 2 für das ausdrückliche Ziel (Zeile 1919).
- Block 5: `vorschau` und `fuers_protokoll` lassen Desktop-Ziele (`mcp:brain-desktop:...`) unverändert durch. Das
  Ergebnis von `desktop_task_start` landet also weiter ungekürzt im Schnappschuss. master hält die Bildschirmtexte
  anders heraus (nur Wahrheitswerte, Sätze nur für den Chat). Für Auftragsbuch-Inhalte bleibt Block 5 nötig.
- `core/desktop_antwort.py` übersetzt Fehlertexte über feste Teilstrings in Sätze. Das ist dasselbe Muster wie
  Block 4 und kann für die Antwort bei `auftragsbuch` als Vorbild dienen; es ist hier kein Konflikt.

**Was die Koordination anders machen muss:** Nichts an den Blöcken. Beim Einbau von Block 5 prüfen, dass der
neue Zweig bei 2000 den Wert `result_payload` für Auftragsbuch-Ziele nicht berührt (er greift nur für
`desktop_task_start`).

## Nr. 8 Plan-Rest bewahrt `eingabe_herkunft` nicht (Block 2)

**Was master geändert hat.** Nichts am Plan-Rest: `_auftrag_plan_rest_speichern` (Zeile 2325, Wörterbuch `plan_rest`
Zeile 2388 bis 2393) speichert nur `antwortkanal`, nicht `eingabe_herkunft`. Ein fortgesetzter Plan (Nachfasser,
`nachfasser.py` Zeile 161) kennt „getippt“ also nicht mehr, und die Schleife startet dann nicht.

**Betroffen:** Block 2, Abschnitt 2 („Plan-Rest“): Block 2 bewahrt `herkunft`, master `eingabe_herkunft` bewusst nicht.

**Entschieden, so umgesetzt** (siehe Nr. 1): Der Plan-Rest bewahrt `herkunft` als `weitergeben(h)`, nicht
`eingabe_herkunft`. Ein fortgesetzter Plan zählt damit weiter als Betreiber (für `required_origin`), startet aber nie
die Desktop-Schleife. Test am Hop: fortgesetzter Plan-Rest ergibt `plan_ctx["eingabe_herkunft"] is None`.

## Nr. 9 Geprüft, keine Überschneidung

- **Block 3 (harte Sperre):** Das Unterprojekt `openfang` steht auf master auf demselben Stand (`eada7ecf`). Alle
  Zeilen und Aussagen gelten unverändert.
- **`capability_validator.py`:** Die Änderung betrifft nur die zwei neuen Prüfarten. Rechteträger, `mcp_authority`
  und Agentennamen kommen dort nicht vor; Block 1 und Block 2 sind nicht berührt.
- **`world_observer.py`:** Zwei neue Prüfungen in `_CHECKS`; kein Bezug zu den Blöcken.
- **`capabilities.yaml` allgemein:** Außer `desktop_task_start` gibt es keine neue Fähigkeit, die ein Muster aus
  Block 6 trifft.

## Tests gegen master

`test_gleichstand.py` und `test_katalog_eintrag.py` lesen die **echten** Dateien aus dem Arbeitsbaum
(`config/space_agent_registry.yml`, `capabilities.yaml`). Gegen die Stände von master geprüft, ohne den Arbeitsbaum zu
ändern: eine zeitweilige Kopie von `spaces/auftragsbuch` und `spaces/_contract` außerhalb des Repos, darin die drei
Dateien (`space_agent_registry.yml`, `capabilities.yaml`, `capability_effects.yaml`) aus `git show origin/master:...`.
Ergebnis: beide Dateien verhalten sich gegen master **gleich** wie im Arbeitsbaum.

| Datei | Arbeitsbaum | Kopie mit master-Dateien |
| --- | --- | --- |
| `spaces/auftragsbuch/tests/test_gleichstand.py` | 25 grün | 25 grün |
| `spaces/auftragsbuch/lieferung/eingabe/test_katalog_eintrag.py` | 9 grün | 9 grün |

Einschränkung: Verglichen wird nur, was die Tests lesen (Verzeichnis, Katalog, Wirkungsklassen). Die Brain-Tests von
master (`test_capability_effects.py`, `test_desktop_schleife.py`) wurden nicht gestartet, weil der Rechner knapp bei
Speicher ist und sie Teile von `brain/` einlesen.

## Zeilenangaben der Blöcke, die schon vor master nicht stimmten

Beim Nachziehen aufgefallen und mitkorrigiert (mit Wirkung auch gegen `ae9f2dfe`, nicht durch master verursacht):

- Block 6: `rowboat_status` stand schon gegen `ae9f2dfe` bei Zeile 1980 bis 1994, nicht 1983 bis 1996. Jetzt 2002 bis 2016.
- Block 1: `TRAEGER` in `scripts/tests/test_sync_openfang_agents.py` beginnt bei Zeile 412, nicht 405; die Prüfung
  `test_deterministic_mcp_agent_scope_drift_fails_before_executor` bei 933, nicht 932.
- Block 2: `deterministic_gateway.py` liegt unter `brain/the_brain/deterministic_gateway.py`, nicht unter `core/`.
- Block 5: Die Stelle `"state"` im Schnappschuss steht bei 1268, nicht bei 1259.
