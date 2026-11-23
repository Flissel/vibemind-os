# Lieferung Block 1: Rechteträger je Ereignis (`rights_carriers`)

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns, den Agenten-Generator und das
Verzeichnis). Dieses Repo ändert an diesen Stellen nichts; geliefert sind der Wortlaut des Verzeichnis-Eintrags, die
Regel und die geprüften Einbaustellen. Die Zeilenangaben gelten nach dem Stand vom 23.11. (Basis `2a4df2e7`) und
wurden am Quelltext geprüft; bitte beim Einbauen noch einmal gegenlesen, denn `plan_executor.py` und
`introspection.py` werden gerade von anderer Seite bearbeitet und die Zeilen verschieben sich.

## Das Problem in einem Satz

Heute trägt ein ganzer Space die Auftragsbuch-Rechte (`rights_carrier: true`, so bei `sales` mit `brain-sales`); der
Space `rowboat` hat aber einen echten Chat-Agenten (`rowboat-chat`), der Nachrichten bearbeitet und deshalb nie
Rechte am Auftragsbuch halten darf, sodass `rowboat.vorschlaege_anlegen` im Namen eines eigenen, nachrichtenlosen
Trägers (`brain-rowboat`) laufen muss.

## Was mitgeliefert wird

- Diese Beschreibung samt Verzeichnis-Wortlaut (unten, Abschnitt 2).
- Der Abgleich `spaces/auftragsbuch/gleichstand.py` kennt das neue Feld schon und ist die Mustervorlage der Regel.
  Seine Tests (`spaces/auftragsbuch/tests/test_gleichstand.py`) lesen den YAML-Block unten aus diesem Dokument,
  mischen ihn in das echte Verzeichnis und verlangen Gleichstand. Der Block hier ist also geprüft, nicht nur
  abgeschrieben.
  `python -m pytest spaces/auftragsbuch/tests/test_gleichstand.py -q -p no:cacheprovider`

Es gibt keinen Code zum Kopieren: Die Regel gehört in vier bestehende Dateien (Abschnitt 3).

## 1. Die Regel

Neues, freiwilliges Feld am Space: `rights_carriers`, eine Liste von Einträgen
`{agent, mcp_servers, mcp_tools}`. `mcp_tools` hat dieselbe Form wie am Space (Server auf Werkzeugliste).

1. Ein Ereignis mit `execution: {kind: mcp, server: S}` läuft im Namen des Trägers, dessen `mcp_servers` `S`
   enthält. Gibt es keinen solchen Träger, gilt wie bisher `agent` des Space.
   Das Ziel heißt dann `mcp:<Träger>:<S>:<Werkzeug>`, also für Rowboat
   `mcp:brain-rowboat:auftragsbuch:rowboat_vorschlaege_anlegen`.
2. Das Werkzeug muss in den `mcp_tools` genau dieses Trägers stehen, nicht in denen des Space. Der Server steht
   nicht unter `mcp_servers` des Space und seine Werkzeuge nicht unter `mcp_tools` des Space: Der Chat-Agent
   `rowboat-chat` bekommt die Auftragsbuch-Rechte nie.
3. Je Space und Server höchstens ein Träger (sonst wäre die Zuordnung in Punkt 1 nicht eindeutig).
4. Der Träger ist kein Agent: kein Modell-Override, kein `system_prompt_hint`, nur MCP-Werkzeuge. Sein Name ist nie
   Ziel `openfang:<Träger>` einer Fähigkeit (Gleichstand-Regel 3).
5. `rights_carrier: true` (ganzer Space, wie `sales`) bleibt gültig und unverändert. Beides zugleich an einem Space
   ist ein Fehler.
6. Ereignisse am Auftragsbuch-Server dürfen nur `kind: mcp` sein.

## 2. Wortlaut für das Verzeichnis

Zum Einmischen in `config/space_agent_registry.yml` (Zuordnungen ergänzen, Listen ersetzen). Alle bisherigen
Einträge von `rowboat` und `sales` bleiben, wie sie sind; neu sind nur die hier genannten Felder und Ereignisse. Die
Datei selbst ist hier **nicht** geändert.

```yaml
spaces:
  rowboat:
    # Rechtetraeger je Server: rowboat-chat bearbeitet Nachrichten und haelt die Auftragsbuch-Rechte nie.
    rights_carriers:
      - agent: brain-rowboat
        mcp_servers: [auftragsbuch]
        mcp_tools: { auftragsbuch: [rowboat_vorschlaege_anlegen, auftrag_lesen] }
    events:
      rowboat.vorschlaege_anlegen:
        tool: rowboat_vorschlaege_anlegen
        required_params: [eingabe]
        required_origin: betreiber
        required_provenance: [approval_ref, cost_ref]
        execution: { kind: mcp, server: auftragsbuch }
      rowboat.auftrag_lesen:
        tool: auftrag_lesen
        required_params: [id]
        required_provenance: [approval_ref, cost_ref]
        execution: { kind: mcp, server: auftragsbuch }
  sales:
    mcp_tools: { auftragsbuch: [auftrag_probe, auftrag_lesen, sales_kontakt_finden] }
    events:
      sales.kontakt_finden:
        tool: sales_kontakt_finden
        required_params: [eingabe]
        required_origin: betreiber
        required_provenance: [approval_ref, cost_ref]
        execution: { kind: mcp, server: auftragsbuch }
```

Zu lesen:

- `required_origin: betreiber` ist das Feld aus Block 2 (Herkunft). Die zwei Vorprüfungen und Nachlesen
  (`auftrag_lesen`) bleiben ohne.
- Das Werkzeug zu `kontakt_finden` heißt im Dienst `sales_kontakt_finden` (Name aus
  `spaces/auftragsbuch/vertrag.py:werkzeugname`, Raum plus Art); der Gleichstand verlangt genau diesen Namen
  unter `mcp_tools`. Das Ereignis heißt `sales.kontakt_finden`. Die Form `required_params: [eingabe]` ist wie bei
  Rowboat angenommen; die Eingabe von `kontakt_finden` ist `{"suche": ...}`.
- `sales.post_pruefen` kommt **nicht** mit in diese Lieferung (nicht Teil von Stufe 2).
- Der Space `rowboat` hat heute `mcp_servers: [vibemind-db, fetch, spaces-rowboat]`; `auftragsbuch` kommt dort
  absichtlich **nicht** dazu.

## 3. Die Stellen, die die Regel auswerten müssen

### 3.1 `scripts/sync_openfang_agents.py` (Agenten-Generator)

- **Zweites Manifest.** `_render_rights_carrier_toml(space, spec)` (Zeile 163) schreibt heute das Manifest des
  ganzen Space aus `spec["agent"]`, `spec["mcp_servers"]` und `spec["mcp_tools"]`. Für `rights_carriers` braucht es
  dieselbe Schablone (`RIGHTS_CARRIER_TOML_TEMPLATE`, Zeile 136) mit `name`, `mcp_servers` und `mcp_tools` aus dem
  Träger-Eintrag; Modell-Block wie bisher aus den Voreinstellungen (`resolve_model_block`, Zeile 117), denn ein
  Träger darf keinen Override haben. Am einfachsten: die Funktion auf `(space, träger_eintrag, space_spec)`
  verallgemeinern; der Aufruf `_render_rights_carrier_toml(space, spec)` aus `_render_agent_toml` (Zeile 185 bis
  186) bleibt für den ganzen Space.
- **Wo es aufgerufen wird.** In `sync` (Zeile 501) läuft die Schleife `for space, spec in spaces.items()` (ab
  Zeile 521) und überspringt über `_skip_reason` (Zeile 208) jeden Space, dessen Agent geschützt ist. Der Agent
  `rowboat-chat` steht in dieser Schutzliste (Zeile 217): **Der ganze Space `rowboat` wird übersprungen**
  (`continue` nach Zeile 522), also würde ein Träger-Manifest, das im selben Schleifenrumpf erzeugt wird, nie
  geschrieben. Die Träger-Manifeste gehören deshalb in eine eigene Schleife über alle Spaces, vor oder nach dieser,
  die `rights_carriers` abarbeitet und `_skip_reason` nur auf `spec.get("enabled", True)` beschränkt. Zielordner
  `AGENTS_DIR / <Träger>/agent.toml` (wie Zeile 529), Vergleich und Ausgabe wie dort (`ok`, `DRIFT`, `would`,
  `create`).
- **`validate_rights_carrier`** (Zeile 464) prüft bisher nur `rights_carrier: true`. Ergänzen, je Eintrag in
  `rights_carriers`: ist eine Liste von Zuordnungen; `agent` nicht leer; `mcp_servers` nicht leer; `mcp_tools` nicht
  leer und jeder Server darin steht in `mcp_servers` des Trägers; kein `model`, kein `system_prompt_hint` im Eintrag;
  Name des Trägers kommt in der ganzen Datei nur einmal vor und ist nicht der `agent` irgendeines Space; je Space
  und Server nur ein Träger; kein Server des Trägers steht unter `mcp_servers` des Space; nicht zugleich
  `rights_carrier: true`; jedes Ereignis des Space mit `execution.server` eines Trägers ist `kind: mcp`.
- **`validate_mcp_authority`** (Zeile 395): Der Agent steht in Zeile 434
  (`"agent": space.get("agent")`); die erlaubten Server (Zeile 417 bis 423) und Werkzeuge
  (`allowed_tools_by_server`, Zeile 424 bis 426) kommen heute immer vom Space. Mit der Regel: Steht der Server des
  Ereignisses bei einem Träger, dann sind **dessen** `agent`, `mcp_servers` und `mcp_tools` maßgeblich; sonst wie
  bisher. Die geschlossene Herkunftsangabe (`required_provenance`, Zeile 456) gilt unverändert.
- `validate_mcp_tool_scopes` (Zeile 290) prüft die `mcp_tools` des Space. Die der Träger prüft
  `validate_rights_carrier` (oben); eine zweite Prüfung dort wäre doppelt.
- Das erzeugte Manifest liegt im Unterprojekt `openfang/agents/brain-rowboat/agent.toml` (wie `brain-sales`); es
  danach wie `brain-sales` auf dem Mini-PC bekannt machen ist Betrieb, nicht Teil dieser Lieferung.
  `spaces/auftragsbuch/deploy/openfang_aufruf.py` hat den Namen `brain-sales` fest (`ERWARTET_AGENT`, Zeile 49);
  für `brain-rowboat` braucht das Betriebshilfsmittel eine eigene Prüfung.

### 3.2 `brain/the_brain/core/capability_targets.py` (Ziel und Rechte beim Aufruf)

- **`_mcp_authority`** (Zeile 1215). Das ist die einzige Stelle, die aus Space und Ereignis den Dreiklang
  Agent/Server/Werkzeug bildet. Heute `values["agent"] = space.get("agent")` (Zeile 1222) und die Erlaubnislisten
  aus `space.get("mcp_servers")` (Zeile 1240) und `space.get("mcp_tools")` (Zeile 1249). Mit der Regel: Träger zum
  Server des Ereignisses suchen; gefunden, dann dessen `agent`, `mcp_servers` und `mcp_tools` benutzen. Die
  Fehlermeldungen (`... is not in {space_id}.mcp_servers`) dann auf den Träger umstellen.
- **`find_registered_mcp_authority`** (Zeile 1269) geht alle Ereignisse durch und vergleicht
  `(agent, server, tool)` mit dem Aufruf. Bei `_mcp_authority` richtig gelöst, braucht diese Funktion keine eigene
  Änderung; zu prüfen ist, dass `("brain-rowboat", "auftragsbuch", "rowboat_vorschlaege_anlegen")` gefunden wird
  und `("rowboat-chat", "auftragsbuch", ...)` **nicht**.
- **`McpExecutor._call`** (Zeile 980): `authority.agent != runtime_context.agent_name` (Zeile 996) bleibt wahr, weil
  der Planausführer den Kontext aus derselben Berechtigung baut (`plan_executor.py` Zeile 1878 bis 1884, dort
  `find_registered_mcp_authority(*target.split(":", 3)[1:])`). Keine Änderung nötig, nur im Test belegen.
- **`resolve_registry_execution_target`** (Zeile 1319, Rückgabe über `_mcp_authority(...).target` in Zeile 1343)
  liefert danach von selbst `mcp:brain-rowboat:auftragsbuch:rowboat_vorschlaege_anlegen` für
  `rowboat.vorschlaege_anlegen`; das ist genau das Ziel des Katalog-Eintrags aus Block 6.

### 3.2a `brain/the_brain/core/plan_executor.py` (Prüfung „Agent-Bereich" im Hop)

Diese Stelle fällt leicht durch, weil sie nicht nach „Rechten" aussieht, aber sie lässt den Schritt sonst scheitern.

- **`_canonical_space_event_agent`** (Zeile 62 bis 84) liefert heute `space["agent"]`, also `rowboat-chat`.
  `_exec_hop_kern` übernimmt das als `canonical_agent` (Zeile 1536) und verlangt danach
  (Zeile 1560 bis 1573), dass das Ziel aus dem Verzeichnis mit `mcp:<canonical_agent>:` beginnt, sonst bricht der
  Hop mit „agent scope drift" ab. Das Ziel von `rowboat.vorschlaege_anlegen` beginnt aber mit
  `mcp:brain-rowboat:` und würde so abgelehnt.
- Einbau: `_canonical_space_event_agent` gibt für ein Ereignis mit `execution.kind: mcp` den Träger zurück, wenn
  dessen Server bei einem Träger des Space steht (dieselbe Auflösung wie in `_mcp_authority`, am besten eine
  gemeinsame kleine Funktion in `capability_targets.py`, die beide benutzen); sonst unverändert `space["agent"]`.
  Der Wert gilt danach auch als `assigned_agent` (Zeile 1685), wirkt dort aber nur bei Zielen, die nicht schon
  `mcp:` heißen (`authoritative_target`, Zeile 1694 bis 1718); unser Ziel heißt `mcp:`.
- Der vorhandene Test `test_deterministic_mcp_agent_scope_drift_fails_before_executor`
  (`test_plan_executor_registered_openfang.py` Zeile 932) ersetzt `_canonical_space_event_agent` und bleibt wie er
  ist; er belegt nur, dass die Prüfung selbst steht.

### 3.3 `brain/the_brain/core/step_space.py` (Space eines Schritts)

- **`_space_aus_mcp_ziel`** (Zeile 65 bis 87). Bei einem Schritt, der nur ein Ziel nennt
  (`mcp:brain-rowboat:auftragsbuch:rowboat_vorschlaege_anlegen`), sucht die Funktion den Space über
  `meta.get("agent") != agent` (Zeile 78). Der Träger ist aber kein `agent` eines Space, also fände sie nichts
  (`None`, allgemeine Aufgabe). Ergänzen: Der Agent des Ziels zählt auch, wenn er unter `rights_carriers` des Space
  steht. Die Ereignis-Suche in Zeile 81 bis 85 (`tool` und `server` stimmen) bleibt dieselbe.
- Für Schritte mit Fähigkeitsnamen (`rowboat_vorschlaege_anlegen` wird über `space_for_step` Zeile 90 ff. zu
  `rowboat.vorschlaege_anlegen` und über die Ereigniszuordnung zu `rowboat`) ist nichts zu tun.

## 4. Tests, die dann einen Fall brauchen

Aus dem Quelltext erhoben (Suche nach `rights_carrier`, `validate_rights_carrier`, `validate_mcp_authority`,
`_mcp_authority`, `find_registered_mcp_authority` unter `scripts/`, `brain/the_brain/tests/` und `spaces/`; am
23.11.):

| Datei (Zeile) | Heute | Neuer Fall |
| --- | --- | --- |
| `scripts/tests/test_sync_openfang_agents.py` (Zeile 405, Festwert `TRAEGER`) | Eintrag mit `rights_carrier: true` | Zweiter Festwert mit `rowboat`-Eintrag (Agent `rowboat-chat`, `rights_carriers` mit `brain-rowboat`) |
| dieselbe Datei, `test_rights_carrier_manifest_has_no_agent_rights` (Zeile 428) | Manifest von `brain-sales` hat nur die zwei Werkzeuge und kein Gedächtnis | Gleiches für `brain-rowboat`: Server `auftragsbuch`, Werkzeuge `mcp_auftragsbuch_rowboat_vorschlaege_anlegen` und `mcp_auftragsbuch_auftrag_lesen`, `rowboat-chat` wird nicht neu geschrieben (geschützt) |
| dieselbe Datei, `test_rights_carrier_must_be_boolean`, `..._requires_mcp_tools`, `..._rejects_model_override_and_prompt_hint`, `..._events_must_be_mcp` (Zeilen 458, 466, 473, 482) | prüfen nur `rights_carrier: true` | je ein Gegenstück für einen Eintrag unter `rights_carriers` (Wahrheitswert entfällt; dafür: Eintrag ohne `agent`, doppelter Träger, Server des Trägers steht am Space, beides zugleich) |
| dieselbe Datei, `test_registry_mcp_authority_requires_allowed_server_and_provenance_contract` (Zeile 28) | `validate_mcp_authority` meldet falschen Server, Werkzeug, Herkunft | Ereignis am Auftragsbuch mit Träger: kein Fehler; Werkzeug nur am Space statt am Träger: Fehler |
| dieselbe Datei, `test_space_without_rights_carrier_renders_as_before` (Zeile 489) | echter Eintrag `ideas` rendert byte-gleich | bleibt; ein Space ohne `rights_carriers` ändert nichts |
| `spaces/auftragsbuch/tests/test_brain_anmeldung.py` (Zeilen 38 und 43: `test_ziele_sind_registriert`, `test_keine_auftragsart_ist_freigeschaltet`) | `find_registered_mcp_authority("brain-sales", "auftragsbuch", ...)` | `("brain-rowboat", "auftragsbuch", "rowboat_vorschlaege_anlegen")` und `auftrag_lesen` gefunden, Space `rowboat`; `("rowboat-chat", "auftragsbuch", ...)` ist `None`; `resolve_registry_execution_target("rowboat.vorschlaege_anlegen")` ist das Ziel aus Block 6 |
| `brain/the_brain/tests/test_plan_executor_registered_openfang.py` (Zeile 932, Muster `_write_idea_mcp_registry`) | Hop mit Ziel vom Ideen-Agenten, Drift wird abgelehnt | Hop für `rowboat.vorschlaege_anlegen` mit Träger: kommt durch die Prüfung und baut den Ausführer für `mcp:brain-rowboat:auftragsbuch:...`; Hop mit Ziel `mcp:rowboat-chat:auftragsbuch:...` wird abgelehnt |
| `brain/the_brain/tests/test_step_space.py` (Zeile 104 bis 117, `test_mcp_ziel_wird_ueber_das_verzeichnis_aufgeloest`) | Ziele mit `brain-sales` | `("mcp:brain-rowboat:auftragsbuch:rowboat_vorschlaege_anlegen", "rowboat")` und ein Ziel mit `brain-rowboat` auf fremdem Werkzeug als Gegenprobe |
| `brain/the_brain/tests/test_rowboat_space_contract.py` (Zeile 38 und 81) | Agent `rowboat-chat`, Ereignis `rowboat.status` | bleibt grün (Agent bleibt `rowboat-chat`); dazu ein Fall, dass `rowboat.vorschlaege_anlegen` im Verzeichnis steht und `required_origin: betreiber` trägt |
| `spaces/auftragsbuch/tests/test_gleichstand.py` | (hier, fertig) | `test_traeger_im_space_erlaubt`, `test_traeger_mit_openfang_ziel_faellt_auf`, `test_auftragsbuch_ohne_traeger_faellt_auf` und weitere; `test_vorschlag_der_lieferung_haelt_den_gleichstand` liest den Block aus Abschnitt 2 |

Tests, die `find_registered_mcp_authority` nur austauschen (`test_openfang_mcp_executor.py` Zeilen 34, 57, 374;
`test_openfang_runtime_authority.py` Zeilen 109, 151; `test_aenderung_zurueck_faehigkeit.py` Zeile 42;
`test_ideen_vertrag_bis_werkzeug.py` Zeilen 171, 246) brauchen keinen neuen Fall.

## 5. Reihenfolge und Grenzen

1. Zuerst die Regel im Generator und im Gehirn einbauen (Abschnitt 3), mit den Tests aus Abschnitt 4 rot-grün.
2. Dann den Wortlaut aus Abschnitt 2 ins Verzeichnis. Vorher würde der Gleichstand grün bleiben, aber das Gehirn
   `rights_carriers` nicht auswerten; nachher ist `rowboat.vorschlaege_anlegen` ohne die Einbauten **nicht**
   ausführbar (`_mcp_authority` würde `rowboat-chat` nehmen und am Server scheitern). Das ist der sichere Ausfall.
3. Dann `sync_openfang_agents.py` laufen lassen, damit `brain-rowboat` entsteht; den Agenten auf dem Mini-PC
   bekannt machen ist Betrieb.
4. Block 6 (Katalog-Eintrag) erst, wenn Punkt 1 und 2 stehen, denn sein Ziel wird aus dem Verzeichnis gelesen.
5. **Gesamtordnung:** Der Verzeichnis-Block mit `required_origin: betreiber` kommt erst NACH dem Herkunft-Tor (Block 2); sonst wird `required_origin` still übergangen. Keine Zeile in `auftrag.brain_arten` außer `brain/probe/1`, bevor harte Sperre (Block 3), Herkunft (Block 2) und Ergebnistext (Block 5) im Betrieb stehen.

Offene Annahmen, bitte bestätigen:

- Ob der Lader des Verzeichnisses (`brain/the_brain/core/space_contract.py:load_space_contract`) das neue Feld
  `rights_carriers` toleriert, wurde geprüft: Er nimmt die Zuordnung eines Space unverändert (`spaces=spaces`) und
  liest nur `events`; ein zusätzliches Feld stört dort nicht. Andere Leser des Verzeichnisses wurden nicht alle
  durchgesehen.
- Die Form von `mcp_tools` im Träger ist die des Space (Server auf Liste). Ein kürzeres `mcp_tools: [..]` wäre
  möglich, würde aber zwei Formen im selben Verzeichnis erzeugen.
