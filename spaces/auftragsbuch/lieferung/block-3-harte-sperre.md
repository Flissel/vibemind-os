# Lieferung Block 3: Harte Sperre für den Werkzeug-Server `auftragsbuch`

Fertiger Block zum Einbauen durch die Koordination (sie betreut OpenFang). Dieses Repo ändert an OpenFang nichts.
Die Zeilenangaben gelten nach dem Stand vom 22.11. und wurden am Quelltext geprüft; bitte beim Einbauen
trotzdem noch einmal gegenlesen.

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

## Sofort wirksam, ohne Bau

Zwei Handgriffe, die schon vorher helfen:

1. `capability-gap-filer` (`openfang/agents/capability-gap-filer/agent.toml`): Die Zeile
   `mcp_servers = ["issue-detector"]` steht dort unter `[capabilities]` (Zeile 76) und wird ignoriert,
   weil `mcp_servers` ein Feld der obersten Ebene ist. Die Zeile muss über den ersten Abschnitt
   (vor `[capabilities]` und vor allen anderen Abschnitten) gesetzt werden.
2. `abo-probe` (liegt nur im Laufzeit-Ordner, nicht im Repo): `no_mcp = true` eintragen.

Danach bleibt im Repo `learning-course-factory` als Agent mit offenem Zugang; ihn sperrt erst die Liste
`allowed_agents`.

## Wunsch an die Schnittstelle

Die Agenten-Detailabfrage von OpenFang (`GET /api/agents/<Kennung>`) liefert heute nur: `capabilities`,
`created_at`, `description`, `fallback_models`, `id`, `identity`, `mcp_servers`, `mcp_servers_mode`, `mode`,
`model`, `name`, `profile`, `session_id`, `skills`, `skills_mode`, `state`, `system_prompt`, `tags`.
Es fehlen `no_mcp` und `tool_allowlist`. Bitte beide Felder ergänzen. Dann kann unser Prüfwerkzeug
(`werkzeuge-je-agent`) Agenten mit `no_mcp = true` oder nicht leerer `tool_allowlist` später als geschlossen
zählen. Bis dahin zählt es sie als „offener Zugang“, auch wenn sie in Wahrheit gesperrt sind.

## Abnahme im Betrieb

```
werkzeuge-je-agent auftragsbuch
```

- Unter „offener Zugang“ steht niemand, und der Exit-Code ist 0.
- Steht dort jemand, ist der Exit-Code 1 (neu; bisher blieb er trotz offener Agenten bei 0).
- In Gruppe 1 steht genau `brain-sales` mit `mcp_auftragsbuch_auftrag_lesen` und `mcp_auftragsbuch_auftrag_probe`.

Im Repo ändert sich der Test `test_nur_erlaubte_traeger_sehen_alle_werkzeuge_des_auftragsbuchs`
(`spaces/auftragsbuch/tests/test_openfang_offene_mcp_zugaenge.py`): Er ist als `xfail` („Lieferung Block 3“)
gesetzt und wird grün, sobald die Agenten im Repo geschlossen sind. Dann `xfail` entfernen. Die Tests, die die
heutige Menge offener Agenten festhalten, müssen dabei mit angepasst werden.

Hinweis: Die Liste `allowed_agents` ist eine zusätzliche Sperre am Server. Sie ersetzt nicht die Freigabe je
Auftragsart in `brain-sales`.
