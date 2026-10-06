# Eigenes Plugin-System, Teilprojekt 1: Integrations-Kern in OpenFang (statische Schlüssel)

Stand: 2026-10-06 · Status: Entwurf zur Durchsicht · Ansatz A (vom User gewählt)

## 1. Ziel und Einordnung

VibeMind bekommt ein eigenes Plugin-System. Es löst den gepinnten OpenAI-Plugin-Katalog
(180 Einträge, Digest `5c9ea069…`) schrittweise ab, MCP für MCP. An die Stelle der
OpenAI-Kennungen treten die **offiziellen Remote-MCP-Server der Anbieter**. Die Integrationen
hängen **zentral an OpenFang**. Damit stehen sie jedem berechtigten OpenFang-Agenten zur
Verfügung, und über den Hub `:4200/mcp` auch Claude Code, den Spaces und Rowboat.

Gesamtvorhaben, jedes Teilprojekt mit eigener Spezifikation, eigenem Plan und eigener Umsetzung:

| # | Teilprojekt | Inhalt |
|---|---|---|
| **1** | **Integrations-Kern (dieses Dokument)** | HTTP-Vorlagen mit Schlüssel-Header aus dem Tresor, Vorlagen-Ordner, Katalogfelder, Freigabe-Standard, Sichtbarkeit, Pilot GitHub |
| 1b | OAuth-Anbieter | Selbstregistrierung (DCR), Refresh-Token, Ablauf, z. B. Notion, Linear, monday |
| 2 | Dashboard-Seite „Integrationen" | Katalog, Status, Einrichten/Testen, Hauptkanal für den User |
| 3 | Setup-Agent | Browser-Steuerung zur Anbieterseite, Eingabe-Anfragen, Telegram-Hinweise, Test nach Einrichtung |
| 4 | Katalog füllen | Vorlage je ersetzbarem OpenAI-Plugin, Abgleichsliste, Entscheidung über den Rowboat-Plugin-Weg |
| 5 | Skills | OpenAI-Plugin-Skills als OpenFang-Skills, mit Lizenz-/Inhaltsprüfung |

Hintergrund-Entscheidungen, die weiter gelten:
- Der Tresor bleibt lokal auf dem PC (Entscheid 24.09.).
- Schlüsselwerte kommen nie in den Agentenkontext.
- Keine Modelle auf der VM.

## 2. Ausgangslage (gemessen 2026-10-06, openfang `04b4dc2`)

- **`openfang-extensions` gibt es schon.** Es enthält eine Integrations-Registry mit 25 eingebauten
  Vorlagen. Sie sind per `include_str!` ins Programm kompiliert und verwenden fast alle `stdio` mit `npx …`.
  Dazu kommen Tresor, OAuth-PKCE für Google/GitHub/Microsoft/Slack, Gesundheitsmonitor und
  Installer. Die API umfasst `/api/integrations`, `/available`, `/add`, `/{id}`, `/{id}/reconnect`,
  `/health` und `/reload`. Eine Dashboard-Seite gibt es nicht.
- **`McpTransportTemplate::Http { url }` gibt es schon.** `IntegrationRegistry::to_mcp_configs`
  (`registry.rs:178`) setzt aber immer `headers: Vec::new()`.
- **Schlüssel für Integrationen setzt der Kernel heute so ein:** `std::env::set_var` in die Umgebung
  des **ganzen Daemons** (`kernel.rs` ~6013). Von dort erben sie alle Kindprozesse. Dieser
  Weg wird für die neuen Header **nicht** benutzt.
- **Freigaben:** `ApprovalPolicy.require_approval` vergleicht exakte Werkzeugnamen
  (`approval.rs:47`). Standard ist `["shell_exec"]`.
- **Sichtbarkeit:** Ein Agent mit leerer `mcp_servers`-Liste sieht **alle** verbundenen
  MCP-Server (`agent.rs:459`).
- **Hub `/mcp`:** `tools/list` und `tools/call` verlangen eine registrierte Agenten-Identität
  (`resolve_mcp_caller`). Ausgeführt wird mit deren Werkzeugmenge über denselben `tool_runner`
  (`routes.rs:7443`).
- **MCP-Werkzeugnamen:** `mcp_{server}_{tool}` (`mcp.rs:342`).

## 3. Umfang von Teilprojekt 1

**Drin:**
- Anbieter mit **statischen Schlüsseln** (PAT, API-Key, Agent-Key), die als HTTP-Header gesendet werden.
- Vorlagen-Ordner zur Laufzeit.
- Katalogfelder.
- Status je Integration.
- Freigabe-Standard.
- Opt-in-Sichtbarkeit.
- Pilot GitHub.

**Nicht drin:**
- OAuth mit Ablauf/Refresh (1b)
- Dashboard (2)
- Setup-Agent und Telegram (3)
- weitere Anbieter (4)
- Skills (5)
- Änderungen am Rowboat-Plugin-Weg. Er bleibt unverändert und funktionsfähig.

## 4. Design

### 4.1 Vorlagenformat (Erweiterung von `IntegrationTemplate`)

```toml
id = "github"
name = "GitHub"
description = "GitHub über den offiziellen Remote-MCP-Server"
category = "devtools"

[transport]
type = "http"
url = "https://api.githubcopilot.com/mcp/"

[[auth_headers]]
name = "Authorization"          # Header-Name
format = "Bearer {credential}"  # genau ein Platzhalter {credential}
credential = "GITHUB_PAT_TOKEN" # Tresor-Referenz (nur der NAME)

[[required_env]]                # wie bisher: beschreibt den Schlüssel für Mensch/Setup-Agent
name = "GITHUB_PAT_TOKEN"
label = "GitHub Personal Access Token (fein granuliert)"
is_secret = true
get_url = "https://github.com/settings/personal-access-tokens/new"

read_only_tools = ["get_me", "search_repositories", "get_file_contents"]

[catalog]
replaces_openai_plugin = "github"  # Name im OpenAI-Katalog, oder leer
license = "MIT"
admission = "admitted"             # "admitted" | "review_required"
```

Regeln:
- **`auth_headers.credential`** muss dem Referenzmuster von OpenFang genügen, also dem Muster, das
  `/api/credentials/*` schon prüft.
- **`format`** enthält genau einmal `{credential}`, sonst wird die Vorlage abgelehnt.
- **`auth_headers`** ist nur bei `http` und `sse` erlaubt. Bei `stdio` wird die Vorlage abgelehnt.
- **`admission = "review_required"`:** Die Vorlage ist sichtbar, `add` lehnt sie aber mit
  `integration_not_admitted` ab.
- **Fehlt `[catalog]`:** Es gelten die Standards (`admission = "admitted"` für die eingebauten
  Vorlagen), damit die 25 bestehenden unverändert bleiben.

### 4.2 Vorlagen-Ordner

- Neuer Konfigschlüssel `[integrations] template_dirs = ["…/vibemind-os/integrations"]`.
  Er ist auf oberster Ebene erlaubt, wie die übrigen Subsystem-Schlüssel.
- Beim Boot und bei `POST /api/integrations/reload` lädt OpenFang zuerst die eingebauten
  Vorlagen, dann jede `*.toml` aus den Ordnern. **Bei gleicher `id` gewinnt die Ordner-Vorlage.**
- Eine nicht parsebare oder regelwidrige Datei wird übersprungen. Das Log zeigt Dateiname und
  Grund, aber keinen Inhalt. Die übrigen Vorlagen laden trotzdem.

### 4.3 Schlüssel in den Header, nie in die Umgebung

- `McpServerConfigEntry` bekommt `auth_headers: Vec<AuthHeaderRef>`, mit Name, Format und
  Referenz, **ohne Wert**. `to_mcp_configs` überträgt sie aus der Vorlage.
- **Für `http`/`sse`-Vorlagen übernimmt `to_mcp_configs` `required_env` NICHT in `env`.**
  Heute landet jedes `required_env` dort, und der Kernel schreibt `env` per `set_var` in die
  Umgebung des Daemons. Bei Remote-Vorlagen beschreibt `required_env` nur noch den Schlüssel
  für Mensch und Setup-Agent. Für `stdio` bleibt alles wie bisher.
- Beim Verbinden löst der Kernel jede Referenz über `resolve_credential` auf. Daraus baut er
  die Header-Zeile und gibt sie **nur** in das `McpServerConfig.headers` dieser einen
  Verbindung. Kein `std::env::set_var` und keine Speicherung in Konfigstrukturen, die über die
  API lesbar sind.
- `McpServerConfig` und `McpServerConfigEntry` schwärzen in `Debug` alle Header-Werte
  (`<redacted>`).
- `/api/config`, `/api/integrations` und `/api/integrations/health` liefern nur Header-Namen
  und Referenz-Namen.
- **Rest-Risiko:** Der Wert liegt während der Verbindung im Speicher des Daemons. Das ist
  derselbe Prozess, der den Tresor ohnehin entschlüsselt hält.

### 4.4 Status je Integration

`IntegrationStatus` wird um diese Werte erweitert. Das Feld `detail` enthält nie Werte oder
Antwortkörper.

| Status | Bedeutung | Auslöser |
|---|---|---|
| `fehlt_schluessel` | Eine `auth_headers`-Referenz ist nicht im Tresor | Prüfung vor dem Verbinden. Es gibt keinen Verbindungsversuch. `detail` nennt die fehlenden Referenz-Namen. |
| `verbunden` | `initialize` und `tools/list` waren erfolgreich | Verbindung |
| `schluessel_abgelehnt` | Der Anbieter antwortet mit 401 oder 403 | Verbindung oder Aufruf. **Kein** automatischer Neuversuch. |
| `nicht_erreichbar` | DNS-, TLS- oder Netzwerkfehler, Zeitüberschreitung, 5xx | Der Gesundheitsmonitor versucht es mit Backoff erneut, wie bisher. |
| `nicht_zugelassen` | `admission = "review_required"` | Laden |

- **`POST /api/integrations/add`:** installiert auch bei fehlendem Schlüssel und meldet dann
  `fehlt_schluessel`. Andernfalls verbindet die Integration sofort, ohne Neustart.
- **`POST /api/integrations/{id}/reconnect`:** liest den Schlüssel neu aus dem Tresor, zum
  Beispiel nach einer neuen Eingabe.

### 4.5 Freigabe-Standard für Integrations-Werkzeuge

- Für jedes Werkzeug einer Integration gilt: **Es braucht eine Freigabe, außer sein
  Originalname steht in `read_only_tools` der Vorlage.**
- Neue oder unbekannte Werkzeuge eines Anbieters brauchen damit automatisch eine Freigabe.
- Umsetzung: `requires_approval(tool_name)` prüft zusätzlich eine Menge der freigabepflichtigen
  Integrations-Werkzeuge. Diese Menge baut der Kernel bei jedem (Re-)Connect aus `tools/list`
  minus `read_only_tools` auf. Die bestehende exakte Liste `require_approval` bleibt
  unverändert gültig.
- Der Freigabe-Zeitrahmen ist die bestehende `[approval] timeout_secs`, bei VibeMind 300 s.

### 4.6 Sichtbarkeit: nur auf ausdrücklichen Wunsch

- MCP-Server, die aus einer **Integration** stammen, gehören **nie** zur Regel „leere
  `mcp_servers`-Liste = alle Server".
- Ein Agent bekommt eine Integration nur, wenn sein Manifest sie in `mcp_servers` nennt,
  z. B. `mcp_servers = ["github"]`.
- Bestehende, nicht aus Integrationen stammende MCP-Server behalten ihr heutiges Verhalten.
- Hub-Abnehmer (Claude Code, Spaces, Rowboat) rufen als registrierter Agent auf. Für sie wird
  ein eigener Agent `integrations-hub` mit ausdrücklicher Liste angelegt. Das Manifest gehört
  zum Pilot.

### 4.7 Belege

Jeder Aufruf eines Integrations-Werkzeugs schreibt in das bestehende Audit-Log:
- Integration
- Werkzeug
- aufrufender Agent
- Freigabe-ID oder `read_only`
- Ergebnis: ok, Fehlerklasse oder Status

Argumentwerte und Antwortinhalte werden nicht protokolliert, Schlüsselwerte nie.

## 5. Fehlerbehandlung (Grundsätze)

- **Fail closed:**
  - Ein unbekannter Status gilt als nicht verbunden.
  - Eine nicht auflösbare Referenz verhindert den Verbindungsversuch.
  - Ein Werkzeug ohne Klassifizierung braucht eine Freigabe.
- **Keine Werte in Fehlern:** Fehlertexte enthalten Referenz-Namen, HTTP-Status und
  Fehlerklasse, aber nie Header-Werte oder Antwortkörper des Anbieters.
- **Eine defekte Vorlage oder Integration** beeinträchtigt keine andere und nicht den Boot des
  Daemons.

## 6. Tests

Alle Tests werden zuerst geschrieben. Gebaut wird mit `cargo … -j 2`, vorher wird der RAM gemessen.

1. **Laden:**
   - Vorlage mit `auth_headers` und `[catalog]` parsen.
   - `format` ohne oder mit doppeltem Platzhalter ablehnen.
   - `auth_headers` bei `stdio` ablehnen.
   - Eine Ordner-Vorlage überschreibt die eingebaute mit gleicher `id`.
   - Eine kaputte Datei wird übersprungen, die übrigen werden geladen.
2. **Header:**
   - Der aufgelöste Wert steht nur im `McpServerConfig.headers` der Verbindung.
   - `std::env` enthält die Referenz danach nicht.
   - `to_mcp_configs` legt für eine `http`-Vorlage mit `required_env` eine leere `env`-Liste an.
   - `Debug` zeigt `<redacted>`.
   - `/api/config` und `/api/integrations` enthalten keinen Wert. Geprüft wird mit einem
     Kanarienwert.
3. **Status:**
   - Fehlender Schlüssel ergibt `fehlt_schluessel` ohne Netzwerkzugriff.
   - Ein Test-Server mit 401 ergibt `schluessel_abgelehnt` ohne Neuversuch.
   - Ein Test-Server mit 200 ergibt `verbunden`.
   - Bei `review_required` antwortet `add` mit `integration_not_admitted`.
4. **Freigabe:**
   - Ein Werkzeug aus `read_only_tools` braucht keine Freigabe.
   - Jedes andere Werkzeug, auch eines, das erst nach dem Reconnect auftaucht, braucht eine.
5. **Sichtbarkeit:**
   - Ein Agent mit leerer Liste sieht keine Integration, aber weiterhin bestehende MCP-Server.
   - Ein Agent mit `mcp_servers = ["github"]` sieht die Integration.
   - Für den Hub gilt dasselbe über `resolve_mcp_caller`.
6. **Regression:** Die 25 eingebauten Vorlagen laden und verhalten sich unverändert. Die
   bestehenden Tests zu `api_integration_test` und Credentials bleiben grün.

## 7. Live-Nachweis (Abnahme)

1. Die Vorlage `vibemind-os/integrations/github.toml` (Copilot-MCP, `GITHUB_PAT_TOKEN`) wird über
   `template_dirs` geladen. `add github` ergibt `verbunden`.
2. Ein Test-Agent mit `mcp_servers = ["github"]` ruft `mcp_github_get_me` ohne Freigabe auf.
   Das Ergebnis enthält den Login.
3. Ein schreibendes Werkzeug erzeugt eine Freigabe. Sie wird abgelehnt, und beim Anbieter
   entsteht nachweislich nichts.
4. Ein Agent ohne Zuweisung sieht keine `mcp_github_*`-Werkzeuge. Dasselbe gilt über den
   Hub als dieser Agent.
5. Der Tresoreintrag wird entfernt und `reconnect` ausgelöst. Ergebnis: `fehlt_schluessel`.
   Danach wird der Eintrag wiederhergestellt.
6. Leck-Prüfung auf `github_pat_`/`ghp_`: OpenFang-Logs, Audit-Log, `/api/config`,
   `/api/integrations*` und die Umgebung von Kindprozessen (`claude.exe`-Wrapper). Ergebnis
   jeweils 0 Treffer.

## 8. Rahmenbedingungen

- **Repos:**
  - OpenFang-Code im Fork `Flissel/openfang`, gearbeitet im Worktree und nie im Haupt-Checkout.
  - Pin in `vibemind-os` über `rev-parse`, `cat-file -t` und `ls-remote` prüfen.
  - Vorlagen in `vibemind-os/integrations/`.
- **Neustart von `:4200`:** nur mit WORKBOARD- und Koordinations-Claim. Gestartet wird
  ausschließlich über den Watchdog, damit der Issue-Key erhalten bleibt.
- **Bestehende Wege bleiben unberührt:**
  - Issue-Key-Weg (`/api/credentials/issue`) und `tailscale serve`
  - Rowboat-Plugin-Laufzeit
  - plugin-setup
- **Keine Schlüssel-Rotation**, Entscheid des Users.

## 9. Offene Punkte für spätere Teilprojekte

- **1b:** Speicherort und Refresh für OAuth-Token (DCR-Client, Refresh-Token, Ablaufzeit).
  Klären, wie der Gesundheitsmonitor einen Refresh auslöst.
- **2/3:** Ob der Status zusätzlich per Server-Sent Events an das Dashboard geht.
- **4:**
  - Abbau oder Umbau des Rowboat-Plugin-Wegs: Rowboat bindet den Hub als MCP-Server ein.
  - Damit entfällt auch der Issue-Key-Weg für Plugins.
  - Abgleichsliste gegen den OpenAI-Katalog.
