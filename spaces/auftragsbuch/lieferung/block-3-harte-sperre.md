# Lieferung Block 3: Harte Sperre für den Werkzeug-Server `auftragsbuch`

Fertiger Block zum Einbauen durch die Koordination (sie betreut OpenFang). Dieses Repo ändert an OpenFang nichts.
Die Zeilenangaben gelten nach dem Stand vom 22.11. und sind gegen `origin/master` (`948f436f`, geprüft am 23.11.)
nachgezogen: unverändert, denn das Unterprojekt `openfang` steht dort auf demselben Stand (`eada7ecf`) wie vorher. Bitte
beim Einbauen trotzdem noch einmal gegenlesen.

## Das Problem in einem Satz

Heute sieht jeder Agent mit leerem oder fehlendem `mcp_servers` (und ohne `no_mcp = true`) alle verbundenen
Werkzeug-Server. Hat er dazu eine leere Werkzeugliste, sieht er auch alle ihre Werkzeuge. Die Sperre je
Auftragsart gilt damit nur für `brain-sales`; offen sind zurzeit `capability-gap-filer` und
`learning-course-factory`.

## Was gebaut werden soll

### 1. Neues Feld am Server-Eintrag

Datei `openfang/crates/openfang-types/src/config.rs`, Struktur `McpServerConfigEntry` (Zeile 1518, Felder
bis 1541, nach `oauth`):

```rust
/// Nur diese Agenten (nach Name) sehen den Server und seine Werkzeuge. Leer heißt: wie bisher, keine Einschränkung.
#[serde(default)]
pub allowed_agents: Vec<String>,
```

Leer heißt wie bisher. Die Struktur hat 12 Stellen, an denen sie als Literal gebaut wird (`McpServerConfigEntry {`
unter `openfang/crates`); dort muss das Feld mit `allowed_agents: Vec::new()` ergänzt werden.

### 2. Auswertung bei der Werkzeugliste

Datei `openfang/crates/openfang-kernel/src/kernel.rs`, Funktion `available_tools_with_registry` (ab Zeile 7247),
Schritt 3 (Zeilen 7380 bis 7411): In der Schleife über `mcp_tools`, direkt **vor** dem Aufruf von
`crate::integrations::mcp_server_visible(...)` (Zeile 7400):

- Der Server, aus dem das Werkzeug stammt (`actual_server`), wird in der Konfiguration gesucht.
- Hat er eine nicht leere `allowed_agents` und steht der Name des Agenten nicht darin, wird das Werkzeug
  übersprungen (`continue`).

Weil LLM-Schleife, `/mcp` und die Laufzeitprüfung dieselbe Liste benutzen, wirkt die Sperre an allen drei Stellen.
Die bestehende Funktion `mcp_server_visible` (`integrations.rs`, Zeile 189 bis 192) bleibt unverändert; sie
prüft nur die Liste `mcp_servers` des Agenten.

### 3. Eintrag in der Konfiguration

Im Server-Eintrag `auftragsbuch` in `openfang/openfang.vibemind.toml` (Zeile 570 bis 576) und in der Vorlage
`spaces/auftragsbuch/deploy/openfang-eintrag.beispiel.toml`, direkt unter `timeout_secs`:

```toml
allowed_agents = ["brain-sales", "brain-rowboat"]
```

Hinweis: Im Repo gibt es unter `openfang/agents` zurzeit keinen Agenten `brain-rowboat` (nur `rowboat-chat`).
Gemeint ist offenbar ein Agent, der nur zur Laufzeit angelegt wird. Bitte den Namen so eintragen, wie der
Agent wirklich heißt.

### 4. Rust-Tests (Namen und Aussagen)

| Name | Aussage |
|---|---|
| `server_mit_liste_ist_fuer_fremden_agenten_unsichtbar` | Ein Agent mit leerem `mcp_servers` und leerer `tools`-Liste sieht kein `mcp_auftragsbuch_*`, wenn der Server eine Liste hat und der Agent nicht darin steht. |
| `server_mit_liste_ist_fuer_genannten_sichtbar` | Ein Agent, der in der Liste steht (und sonst Zugriff hat), sieht die Werkzeuge. |
| `server_ohne_liste_verhaelt_sich_wie_bisher` | Ein Server mit leerer `allowed_agents` verhält sich unverändert. |
| `mcp_endpunkt_lehnt_fremden_agenten_ab` | Ein `tools/call` eines fremden Agenten am Endpunkt `/mcp` wird mit dem JSON-RPC-Fehler `-32602` abgelehnt. |

## Sofort wirksam, ohne Bau (Änderungen an den Agenten-Manifesten)

Beide Handgriffe stehen am Anfang der Datei, **vor** dem ersten Abschnitt in eckigen Klammern (Felder der
obersten Ebene; unter `[capabilities]` oder `[model]` werden sie ignoriert).

1. `capability-gap-filer` (`openfang/agents/capability-gap-filer/agent.toml`): Die Zeile
   `mcp_servers = ["issue-detector"]` steht bei Zeile 76 unter `[capabilities]` und wirkt dort nicht. Löschen
   und stattdessen vor `[model]` (nach `tags = ...`, Zeile 7) setzen:
   ```toml
   mcp_servers = ["issue-detector"]
   ```
2. `learning-course-factory` (`openfang/agents/learning-course-factory/agent.toml`): Der Agent hat kein
   `mcp_servers` und ein leeres `tools = []` (Zeile 26), sieht also alle Server mit allen Werkzeugen. Er liefert
   nur ein JSON-Objekt je Anfrage und braucht (nach dem Manifest) keinen Werkzeug-Server. Vor `[model]`
   (nach `max_history_messages = 1` / `tags = ...`, Zeile 10 bis 11) eintragen:
   ```toml
   no_mcp = true
   ```
   (Braucht der Agent doch einen Server, stattdessen `mcp_servers = ["<dieser-Server>"]` ohne `auftragsbuch`.)
3. `abo-probe` (liegt nur im Laufzeit-Ordner, nicht im Repo): ebenfalls `no_mcp = true` am Anfang.

Erst diese Manifest-Änderungen schließen die beiden offenen Repo-Agenten. `allowed_agents` in der
Konfiguration ändert kein Manifest.

## Schnittstelle: ohne sie bleibt die Abnahme unerreichbar

Die Agenten-Detailabfrage von OpenFang (`GET /api/agents/<Kennung>`) liefert heute nur: `capabilities`,
`created_at`, `description`, `fallback_models`, `id`, `identity`, `mcp_servers`, `mcp_servers_mode`, `mode`,
`model`, `name`, `profile`, `session_id`, `skills`, `skills_mode`, `state`, `system_prompt`, `tags`.

Unser Werkzeug `werkzeuge-je-agent` berechnet „offener Zugang“ (Gruppe 2) allein aus diesen Feldern (leere oder
`*`-Werkzeuge, dazu sieht der Agent den Server). Es erfährt weder `no_mcp` noch `tool_allowlist`, und die Sperre
`allowed_agents` ist eine Eigenschaft des Servers, die die Detailabfrage nicht zeigt. Darum muss OpenFang
zusätzlich liefern:

- (a) `no_mcp` und `tool_allowlist` in der Agenten-Detailabfrage, **und** die Wirkung von `allowed_agents`:
  entweder `allowed_agents` je Werkzeug-Server in einer Abfrage (z. B. `GET /api/mcp/servers`) oder je Agent
  die Liste der Werkzeug-Server, die er tatsächlich sieht.
- (b) Danach ändern **wir** `werkzeuge-je-agent` (Folgeänderung im Repo, nicht Sache der Koordination): Agenten
  mit `no_mcp`, nicht leerer `tool_allowlist` oder ohne Sicht auf `auftragsbuch` zählen als geschlossen.

## Erwartetes Zwischenergebnis (bis (a) und (b) erledigt sind)

Nach dem Einbau von `allowed_agents` und den Manifest-Änderungen oben, aber ohne (a) und (b):

- Gruppe 2 („offener Zugang“) listet weiter alle Agenten mit leerer oder `*`-Werkzeugliste, die laut Detailfeldern
  den Server sehen. Das sind `abo-probe` und `learning-course-factory` (beide mit `no_mcp = true`, das die
  Detailabfrage nicht zeigt; sie sind in Wahrheit gesperrt) sowie jeder weitere Agent mit leerer Werkzeugliste im
  Laufzeit-Ordner. `capability-gap-filer` erscheint nicht mehr, weil sein `mcp_servers = ["issue-detector"]`
  in der Detailabfrage steht. (Wer `learning-course-factory` schon jetzt aus Gruppe 2 haben will, trägt statt
  `no_mcp = true` ein `mcp_servers` mit einem anderen, vorhandenen Server ein; das ist aber unnötig, wenn (a)
  und (b) kommen.)
- Der Exit-Code ist **1** (erwartet, kein Fehler), solange Gruppe 2 nicht leer ist. Das heißt nicht, dass die
  Sperre fehlt; das Werkzeug kann sie nur noch nicht sehen.
- Gruppe 1 zeigt genau `brain-sales` mit `mcp_auftragsbuch_auftrag_lesen` und `mcp_auftragsbuch_auftrag_probe`.
- Die Sperre selbst lässt sich schon jetzt prüfen: `gegenprobe <fremder-agent> ...` muss für einen Agenten
  außerhalb der Liste `-32602` liefern, und die Rust-Tests aus dem Abschnitt „Rust-Tests“ laufen grün.

## Abnahme im Betrieb (Ziel)

Nach (a), (b) und den Manifest-Änderungen:

```
werkzeuge-je-agent auftragsbuch
```

- Unter „offener Zugang“ steht niemand, der Exit-Code ist 0. Kein fremder Agent sieht den Server `auftragsbuch`.
- In Gruppe 1 steht genau `brain-sales` mit `mcp_auftragsbuch_auftrag_lesen` und `mcp_auftragsbuch_auftrag_probe`.

## Der Test im Repo (`xfail`)

`test_nur_erlaubte_traeger_sehen_alle_werkzeuge_des_auftragsbuchs`
(`spaces/auftragsbuch/tests/test_openfang_offene_mcp_zugaenge.py`) liest nur die Manifeste unter
`openfang/agents`. Er ist als `xfail` („Lieferung Block 3“, `strict`) gesetzt und schlägt heute mit
`capability-gap-filer` und `learning-course-factory` fehl. `allowed_agents` in der Konfiguration ändert daran
nichts. Er kippt erst, wenn **beide** Manifeste wie oben geändert sind (Abschnitt „Sofort wirksam“): Dann
meldet pytest XPASS, und wir entfernen den `xfail`-Marker. Gleichzeitig müssen die Tests angepasst werden,
die die heutige Menge offener Agenten festhalten (`OFFENER_SERVERZUGANG`, `SIEHT_ALLE_WERKZEUGE`).

Hinweis: Die Liste `allowed_agents` ist eine zusätzliche Sperre am Server. Sie ersetzt nicht die Freigabe je
Auftragsart in `brain-sales`.
