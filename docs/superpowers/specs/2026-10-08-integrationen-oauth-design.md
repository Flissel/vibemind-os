# Eigenes Plugin-System, Teilprojekt 1b: OAuth-Anbieter und nicht ausgebbare Integrations-Schlüssel

Stand: 2026-10-08 · Status: Entwurf zur Durchsicht · Ansatz A (vom User gewählt) · Pilot: Vercel

## 1. Ziel und Einordnung

Teilprojekt 1 (Spec `2026-10-06-integrationen-kern-openfang-design.md`, live seit 06.10.) verbindet
Remote-MCP-Server mit **statischen** Schlüsseln. Die meisten offiziellen Anbieter-Server melden sich aber
per **OAuth** an, zum Beispiel Vercel, Notion, Linear, monday, Atlassian, Asana, Sentry, Canva und Box.

1b ergänzt zwei Dinge:
1. **MCP-OAuth in OpenFang.** OpenFang ermittelt den Anmeldeserver selbst, registriert sich selbst als
   Client (Dynamic Client Registration), lässt den User einmal im Browser anmelden, legt die Token im Tresor
   ab und erneuert sie selbständig. Alles läuft im Daemon, der Wert verlässt ihn nie.
2. **Nicht ausgebbare Integrations-Schlüssel.** Schlüssel für Integrationen werden über eigene Wege
   gespeichert. `/api/credentials/issue` gibt sie nie heraus. Damit wird auch die offene Token-Entscheidung
   aus Teilprojekt 1 gelöst: GitHub bekommt eine eigene Referenz `INTEGRATION_GITHUB_PAT` statt des
   ausgebbaren `GITHUB_PAT_TOKEN`.

Gemessener Pilot-Befund vom 08.10.: Vercels MCP-Server folgt dem MCP-Anmeldestandard vollständig.
- `POST https://mcp.vercel.com/` ohne Token liefert `401` mit
  `WWW-Authenticate: Bearer … resource_metadata="https://mcp.vercel.com/.well-known/oauth-protected-resource"`.
- Die Schutzbeschreibung nennt `authorization_servers: ["https://vercel.com"]`.
- Die Beschreibung des Anmeldeservers enthält:
  - `registration_endpoint`
  - `code_challenge_methods_supported: ["S256"]`
  - `token_endpoint_auth_methods_supported: ["none"]` (öffentlicher Client)
  - `grant_types_supported` mit `refresh_token`
  - `offline_access`
  - `revocation_endpoint`

## 2. Ausgangslage in OpenFang (openfang `39514ed`)

- **`openfang-extensions/src/oauth.rs`:** PKCE-Ablauf mit eigenem kurzlebigem localhost-Server, nur für vier
  fest eingetragene Anbieter mit festen Client-IDs. Selbstregistrierung und Erneuerung fehlen. Den Weg nutzen
  weder Kernel noch API.
- **`CredentialResolver::store_in_vault` / `remove_from_vault`** existieren. Der Kernel hält den Resolver.
- **`/api/credentials/store`** legt einen Wert ab **und** setzt die Referenz auf die Ausgabeliste
  (`issuable_credentials.list`). `/api/credentials/issue` gibt nur gelistete Referenzen heraus.
- **Teilprojekt 1** liefert die Bausteine, auf denen 1b aufsetzt:
  - `IntegrationTemplate` mit `auth_headers`, `read_only_tools` und `catalog`
  - den einzigen Verbindungsweg `connect_one_mcp` mit `build_runtime_config` (Wert nur im Header)
  - Zustände und Health, Audit sowie `remove_integration`, das zuerst die Verbindung trennt

## 3. Umfang

**Drin:**
- `[auth] type = "oauth"` in Vorlagen
- Ermittlung des Anmeldeservers
- Selbstregistrierung
- Anmeldung über eine Callback-Route am Daemon
- Token im Tresor
- Erneuerung mit Sperre
- Zustand `anmeldung_noetig`
- Abmelden und Widerruf
- nicht ausgebbare `INTEGRATION_`-Referenzen mit Sperre in `/issue` und `/store`
- Einmal-Link-Seite für statische Integrations-Schlüssel
- Umstellung von GitHub auf `INTEGRATION_GITHUB_PAT`
- Pilot Vercel

**Nicht drin:**
- Dashboard-Oberfläche (Teilprojekt 2)
- Browser-Steuerung und Telegram (3)
- weitere Anbieter (4)
- Anbieter ohne Selbstregistrierung, die eine vorher angelegte App brauchen (HubSpot, Google, Microsoft); dafür kommt ein eigenes Teilprojekt
- Änderungen am Rowboat-Plugin-Weg und an `GITHUB_PAT_TOKEN`; Letzteres bleibt für Rowboat ausgebbar

## 4. Design

### 4.1 Vorlagenformat

```toml
id = "vercel"
name = "Vercel"
description = "Vercel ueber den offiziellen Remote-MCP-Server"
category = "devtools"
read_only_tools = [ … lesende Werkzeuge aus dem Live-tools/list … ]

[transport]
type = "http"
url = "https://mcp.vercel.com/"

[auth]
type = "oauth"
scopes = ["offline_access"]     # optional; leer = keine scope-Angabe

[catalog]
replaces_openai_plugin = "vercel"
license = "proprietary-service"
admission = "admitted"
```

Prüfregeln (`validate_template`):
- `[auth] type = "oauth"` ist nur bei `http`/`sse` erlaubt und schließt `auth_headers` aus.
- Für `auth_headers` gilt zusätzlich: Ihre Referenzen müssen mit `INTEGRATION_` beginnen. Ausnahme ist die
  bestehende eingebaute Welt ohne `[catalog]`, also die `stdio`-Vorlagen.

### 4.2 Nicht ausgebbare Integrations-Referenzen

- **Namensraum:** Alle Schlüssel von Integrationen heißen `INTEGRATION_<…>`. Ein OAuth-Eintrag heißt
  `INTEGRATION_OAUTH_<ID>`, wobei die ID in Großbuchstaben steht und `-` zu `_` wird.
- **Doppelte Sperre:**
  - `/api/credentials/issue` lehnt jede Referenz mit Vorsilbe `INTEGRATION_` ab, auch wenn sie auf der
    Ausgabeliste steht. Die Antwort ist dieselbe wie bei einer unbekannten Referenz: `404 credential_unavailable`.
  - `/api/credentials/store` lehnt diese Vorsilbe ab.
- **Schreiben** darf nur der Kernel, über den OAuth-Ablauf und die Einmal-Link-Seite.
- **OAuth-Eintrag:** ein Tresor-Wert als JSON. Er wird nur als Ganzes geschrieben, also atomar.
  ```json
  {"v":1,"resource":"https://mcp.vercel.com/","issuer":"https://vercel.com",
   "token_endpoint":"…","revocation_endpoint":"…","client_id":"…",
   "access_token":"…","refresh_token":"…","expires_at":"RFC3339","scopes":["…"]}
  ```
- **`Debug`** des geparsten Typs schwärzt `access_token`, `refresh_token` und `client_id`.

### 4.3 Ermittlung (Discovery)

1. Ein MCP-Aufruf ohne Token an `transport.url` muss mit `401` und einem `WWW-Authenticate` mit
   `resource_metadata` antworten. Fehlt dieser Hinweis, liest OpenFang `{origin}/.well-known/oauth-protected-resource`.
2. Aus der Schutzbeschreibung kommt `authorization_servers[0]`. Danach liest OpenFang
   `{issuer}/.well-known/oauth-authorization-server`, ersatzweise `openid-configuration`.
3. Pflichtregeln, sonst ist die Ermittlung gescheitert:
   - Alle URLs sind `https`.
   - `issuer` der Beschreibung ist genau gleich dem Eintrag in `authorization_servers`.
   - Autorisierungs-, Token-, Registrierungs- und Widerrufs-Endpunkt haben denselben Host wie `issuer`.
   - `S256` ist unterstützt.
4. Ergebnis wird im OAuth-Eintrag gemerkt und nur beim ersten Mal oder nach `abmelden` neu ermittelt.

### 4.4 Selbstregistrierung (DCR, RFC 7591)

- Einmal pro Integration, sobald noch keine `client_id` vorliegt.
- Die Anfrage enthält:
  - `client_name: "OpenFang (VibeMind)"`
  - `redirect_uris: ["http://127.0.0.1:<api_port>/api/integrations/<id>/oauth/callback"]`
  - `grant_types: ["authorization_code","refresh_token"]`
  - `token_endpoint_auth_method: "none"`
  - `response_types: ["code"]`
- Lehnt der Anbieter ab (4xx), wird der Zustand `nicht_erreichbar` mit
  `detail = "registrierung abgelehnt (http NNN)"`. Den Antworttext übernimmt OpenFang nicht.

### 4.5 Anmeldung

- **`POST /api/integrations/{id}/oauth/start`** verlangt den Bearer.
  - Voraussetzungen: Die Integration ist installiert und zugelassen und hat `auth.type = "oauth"`.
  - OpenFang führt Ermittlung und Registrierung bei Bedarf aus und erzeugt dann:
    - `state`: 32 Zufallsbytes, base64url
    - PKCE-`code_verifier`: 32 Zufallsbytes, dazu `S256`-Challenge
  - Beides liegt nur im Speicher, mit der Integrations-ID verknüpft, 10 Minuten gültig und einmal nutzbar.
  - Die Antwort ist `{ "anmelde_url": "…" }`. Es wird kein Token erzeugt.
- **`GET /api/integrations/{id}/oauth/callback?code&state`** braucht keinen Bearer.
  - Sie ist nur von Loopback-Adressen erreichbar und wird dafür in die Middleware-Ausnahme aufgenommen.
  - Sie nimmt `state` atomar heraus (danach ist er verbraucht) und prüft Integration und Ablauf.
  - Dann tauscht sie den Code mit `code_verifier`, `redirect_uri` und `client_id` gegen Token.
  - Die Token schreibt sie als OAuth-Eintrag in den Tresor und verbindet danach sofort (`connect_one_mcp`).
  - Antwort ist eine schlichte HTML-Seite: „Anmeldung erfolgreich" oder „fehlgeschlagen: &lt;Klasse&gt;".
    Kein Token, keine Weiterleitung.
- **Fehlerklassen:**
  - `state ungueltig`
  - `anmeldung abgebrochen` (wenn `error=` zurückkommt)
  - `token-tausch fehlgeschlagen (http NNN)`

### 4.6 Verbindung und Erneuerung

- **`build_runtime_config` bei OAuth-Vorlagen:** Liegt kein OAuth-Eintrag vor, ist der Zustand
  `anmeldung_noetig` und es gibt keinen Verbindungsversuch. Sonst wird der Header
  `Authorization: Bearer <access_token>` gesetzt, genauso wie die statischen Header aus Teilprojekt 1.
- **Vor jedem Verbindungsaufbau** wird erneuert, wenn `expires_at - jetzt < 120 s`.
- **Bei 401 während Verbindung oder Aufruf** wird genau **einmal** erneuert, neu verbunden und der Aufruf
  wiederholt. Der Aufruf läuft danach weiter über den gewohnten Freigabe- und Audit-Weg.
- **Sperre:** pro Integration ein `tokio::sync::Mutex`, damit jeweils nur eine Erneuerung läuft. Wer auf die
  Sperre wartet, liest danach den frischen Eintrag und erneuert nicht noch einmal.
- **Gescheiterte Erneuerung** (`invalid_grant` o. ä.): Der Zustand wird `anmeldung_noetig`. Es gibt keinen
  automatischen Neuversuch, auch der Health-Loop versucht es nicht erneut.
- **Gibt der Anbieter bei der Erneuerung ein neues Refresh-Token aus,** ersetzt es das alte im selben atomaren
  Schreibvorgang.

### 4.7 Abmelden und Deinstallieren

- **`POST /api/integrations/{id}/oauth/abmelden`** verlangt den Bearer.
  - Erst trennt OpenFang die Verbindung, dann widerruft es das Token beim Anbieter, falls er einen
    `revocation_endpoint` hat. Ein Fehler beim Widerruf wird geloggt, blockiert aber nichts.
  - Danach wird der OAuth-Eintrag aus dem Tresor gelöscht. Der Zustand ist `anmeldung_noetig`.
- **`remove_integration`** führt bei OAuth-Integrationen dieselben Schritte aus, nach dem Trennen.

### 4.8 Einmal-Link-Seite für statische Integrations-Schlüssel

- **`POST /api/integrations/{id}/schluessel/link`** verlangt den Bearer.
  - Die Integration muss `auth_headers` haben.
  - OpenFang erzeugt ein Einmal-Token (32 Bytes, 10 Minuten gültig, nur im Speicher, an Integration und
    Referenz gebunden).
  - Antwort: `{ "url": "http://127.0.0.1:<port>/api/integrations/{id}/schluessel/<token>" }`
- **`GET …/schluessel/<token>`** ist nur über Loopback erreichbar und braucht keinen Bearer. Die Seite zeigt
  ein Passwortfeld für die Referenz. Ein `GET` verbraucht das Token nicht.
- **`POST …/schluessel/<token>`** (Formular, nur Loopback) verbraucht das Token atomar.
  - Der Wert wird geprüft: nicht leer, keine Steuerzeichen, höchstens 4096 Zeichen.
  - Er wird mit `store_in_vault` unter der `INTEGRATION_`-Referenz gespeichert, danach folgt `reconnect`.
  - Die Antwortseite enthält nur „gespeichert" oder eine Fehlerklasse, nie den Wert.
- Die Seite setzt diese Header:
  - `Cache-Control: no-store`
  - `Referrer-Policy: no-referrer`
  - eine `Content-Security-Policy` ohne externe Quellen

### 4.9 Zustände (Erweiterung von Teilprojekt 1)

Neu ist `anmeldung_noetig`. Der Zustand gilt in drei Fällen: Es liegt noch kein OAuth-Eintrag vor, die
Erneuerung ist gescheitert, oder der User hat sich abgemeldet. `detail` nennt dann `"nie angemeldet"`,
`"erneuerung fehlgeschlagen"` bzw. `"abgemeldet"`.

### 4.10 Belege

Audit-Zeilen (`AuditAction::ConfigChange`) gibt es für vier Ereignisse. Sie enthalten keine Werte, keine
Client-ID und keine URLs mit Query:
- `integration_oauth=<id> ereignis=anmeldung ergebnis=<ok|klasse>`
- `ereignis=erneuerung`
- `ereignis=abmeldung`
- `ereignis=schluessel_gespeichert referenz=<name>`

## 5. Fehlerbehandlung (Grundsätze)

- **Fail closed:**
  - Eine unbrauchbare Ermittlung ergibt keine Registrierung.
  - Ein ungültiger `state` ergibt keinen Token-Tausch.
  - Eine gescheiterte Erneuerung ergibt keinen Verbindungsversuch mit altem Token.
  - Bei einem unbekannten Fehler ist der Zustand `nicht_erreichbar` mit fester Klasse.
- **Kein Wert verlässt den Daemon.** Das gilt für Access-Token, Refresh-Token, Code, `code_verifier`, `state`
  und Einmal-Tokens. Keiner davon steht in Logs, `detail`, Audit, API-Antworten oder HTML-Seiten. Einzige
  Ausnahme ist die Anmelde-URL: Sie enthält die PKCE-Challenge und `state`, aber kein Geheimnis.
- **Keine Antworttexte von Anbietern** in Zuständen oder Logs, nur HTTP-Status und feste Klassen. Das ist die
  Regel aus Teilprojekt 1.

## 6. Tests

Alle Tests werden zuerst geschrieben. Gebaut wird nur mit `-j 2`.

1. **Ermittlung:** Ein gültiger Ablauf funktioniert. Abgelehnt werden: `http`, eine `issuer`-Abweichung, ein
   Endpunkt auf fremdem Host und eine Beschreibung ohne `S256`.
2. **DCR:** Die Anfrageform wird geprüft, ebenso die Behandlung von Ablehnungen. Die `client_id` wird nur im
   Tresor-Eintrag gespeichert.
3. **`state` und PKCE:** Abgedeckt werden Ablauf, einmalige Nutzung, falsche Integration und die korrekte
   S256-Challenge.
4. **Erneuerung:**
   - Unter 120 s Restlaufzeit wird erneuert.
   - Zwei gleichzeitige 401 führen zu genau einer Erneuerung.
   - Ein neues Refresh-Token wird übernommen.
   - Bei `invalid_grant` wird der Zustand `anmeldung_noetig`, und es gibt keinen Neuversuch.
5. **Sperre `INTEGRATION_`:** `/issue` liefert für eine gelistete `INTEGRATION_`-Referenz trotzdem `404`, und
   `/store` lehnt die Vorsilbe ab.
6. **Einmal-Link:**
   - Abgedeckt werden Ablauf und einmalige Nutzung.
   - Ein Zugriff von außerhalb Loopback wird abgelehnt.
   - Der Wert taucht in keiner Antwort und keinem Log auf.
   - Die Header sind gesetzt.
7. **Ende-zu-Ende** gegen einen Test-Anmeldeserver und einen Test-MCP-Server am PC: anmelden, verbunden,
   Aufruf, Ablauf, Erneuerung, Aufruf, abmelden mit Widerruf. Kanarienwerte für alle Token dürfen auf keinem
   Endpunkt auftauchen.
8. **Ausnahme nur im Test:** Http auf Loopback ist für die Ermittlung **nur** in Tests erlaubt
   (`cfg(test)` bzw. ein Test-Feature). Im Release-Build gibt es diese Ausnahme nicht. Ein Test beweist, dass
   der Standard-Build `http` ablehnt.
9. **Regression:** Die Tests aus Teilprojekt 1 bleiben grün, ebenso `api_integration_test`.
10. **Kein stiller Rückfall:** Eine Ordner-Vorlage `github` mit ungültiger Referenz und dazu eine
    installierte Integration `github` ergibt `nicht_zugelassen` mit `"vorlage ungueltig"`. Es gibt keine
    stdio-Verbindung.

## 7. Live-Nachweis (Abnahme)

1. `vibemind-os/integrations/vercel.toml` anlegen. Die `read_only_tools` kommen aus dem Live-`tools/list`
   nach der Anmeldung und werden dann eingetragen. Danach `reload` und `add`. Erwartet: `anmeldung_noetig`.
2. `oauth/start` liefert die Anmelde-URL. **Der User** meldet sich im Browser bei Vercel an. Erwartet: die
   Seite „Anmeldung erfolgreich" und der Zustand `verbunden`.
3. `integrations-hub` mit `mcp_servers = ["github","vercel"]`: Ein lesender Aufruf (z. B. Projekte auflisten)
   läuft ohne Freigabe. Ein schreibendes Werkzeug erzeugt eine Freigabe, die abgelehnt wird.
4. Erzwungene Erneuerung: `expires_at` im Eintrag in die Vergangenheit setzen (Test-Endpunkt nur für Loopback
   oder Kernel-Hilfe). Der nächste Aufruf funktioniert, und im Audit steht `ereignis=erneuerung ergebnis=ok`.
5. GitHub umstellen:
   - **Reihenfolge ist Pflicht.** Die Vorlage `integrations/github.toml` wird **im selben Rollout und vor dem
     Daemon-Neustart** auf `INTEGRATION_GITHUB_PAT` umgestellt. Sonst würde die alte Vorlage mit
     `GITHUB_PAT_TOKEN` an der neuen Regel aus 4.1 scheitern, und die id `github` fiele still auf die
     eingebaute stdio-Vorlage zurück.
   - Nach dem Neustart ist `github` vorübergehend `fehlt_schluessel`. Davon ist nur der Hub-Agent betroffen.
   - `schluessel/link` erzeugt den Link. **Der User** trägt den fein granulierten PAT ein. Danach ist der
     Zustand `verbunden`, und `get_me` liefert `Flissel`.
   - `/api/credentials/issue` mit Bearer **und** Issue-Key liefert für `INTEGRATION_GITHUB_PAT` `404`.
   - **Zusätzliche Absicherung im Code:** Fällt eine installierte Integration wegen einer ungültigen
     Ordner-Vorlage auf eine eingebaute Vorlage mit **anderem Transport** zurück, wird sie nicht verbunden.
     Der Zustand ist dann `nicht_zugelassen`, mit `detail = "vorlage ungueltig"`. Ein stiller Wechsel auf
     stdio ist damit ausgeschlossen. Das deckt Finding I4 aus Teilprojekt 1 endgültig ab.
6. Leck-Prüfung auf `github_pat_`, `ghp_`, Vercel-Token-Muster und die Werte selbst. Geprüft werden Logs,
   `/api/config`, `/api/integrations*`, `/api/audit/recent`, `/api/approvals` und die Seite nach dem Callback.
   Erwartet: 0 Treffer.
7. `oauth/abmelden` für Vercel. Erwartet:
   - Widerruf beim Anbieter: `ok` oder geloggt.
   - Tresor-Eintrag weg.
   - Zustand `anmeldung_noetig`.
   - Ein nachfolgender Aufruf ist nicht möglich.
   - Danach optional erneut anmelden.

## 8. Rahmenbedingungen

Es gelten dieselben Regeln wie in Teilprojekt 1, Abschnitt 8:
- **Ort:** Code im openfang-Worktree, detached, Push auf `claude/openfang-fork-reconciliation-v1`.
- **Pin:** Pin-Prüfung vor jedem Pin-Wechsel.
- **Neustart:** Neustart von `:4200` nur über den Watchdog und mit Claim.
- **Keine Rotation:** keine Schlüssel-Rotation.
- **`GITHUB_PAT_TOKEN`:** bleibt unverändert ausgebbar, für den Rowboat-Weg.

## 9. Offene Punkte für spätere Teilprojekte

- **Ausweich für Anbieter ohne Selbstregistrierung:** feste Client-ID pro Vorlage. Betrifft HubSpot, Google
  und Microsoft.
- **Dashboard-Knopf** „Anmelden" bzw. „Schlüssel eintragen" (Teilprojekt 2).
- **Browser-Steuerung und Telegram-Erinnerung** bei `anmeldung_noetig` (Teilprojekt 3).
- **Verschlüsselung des Tresors:** Wie sicher die Token im Tresor liegen, hängt weiter vom Windows-Keyring ab
  (Entscheid 24.09.).
