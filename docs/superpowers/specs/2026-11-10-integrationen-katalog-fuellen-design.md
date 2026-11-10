# Plugin-System Teil 4: Katalog füllen

Stand: 2026-11-10 · Teil 4 der Reihe aus
`docs/superpowers/specs/2026-10-06-integrationen-kern-openfang-design.md` (Teil 1 = Kern,
Teil 1b = OAuth, Teil 2 = Seite „Integrationen" in Rowboat, beide live).

## Worum es geht und warum

Der gepinnte OpenAI-Plugin-Katalog
(`spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json`, 180 Einträge) soll
schrittweise durch eigene VibeMind-Vorlagen abgelöst werden. Ersetzt sind bisher nur GitHub
und Vercel (`integrations/github.toml`, `integrations/vercel.toml`).

Felix will **nicht** nur die Dienste nachbauen, die er selbst nutzt. Es soll für möglichst
alle Dienste eine Vorlage bereitliegen, damit sie da ist, wenn jemand sie braucht. Felix
testet die Vorlagen **nicht** einzeln durch. Geprüft wird bei Bedarf: Wer eine Vorlage zum
ersten Mal einrichtet, ist ihr erster echter Test. Bis dahin ist sie als „ungeprüft"
gekennzeichnet und kann trotzdem eingerichtet werden.

Eine Vorlage ist klein: Adresse des offiziellen Remote-MCP-Servers des Anbieters,
Anmeldeart, Katalogfelder. Darum ist das Füllen weitgehend Schema F. Die Arbeit steckt in
der Recherche je Anbieter, weil der OpenAI-Katalog zu den Apps nur eine interne OpenAI-Nummer
enthält (`appDeclaration.id`, `providerKind = "openai-connector-bridge"`), aber keine
Adresse und keine Anmeldeart.

## Bestand im OpenAI-Katalog

| Sorte | Anzahl | Behandlung in Teil 4 |
|---|---|---|
| App (`kind = "app"`, läuft bei OpenAI über deren Connector-Brücke) | 148 | Recherche, wo möglich Vorlage |
| Eigener MCP-Server (`kind = "mcp"`) | 8 | Recherche, wo möglich Vorlage (github schon ersetzt) |
| Nur Skills/Befehle/Agenten | 24 | nur Abgleichsliste, Umsetzung in Teil 5 |

## Ziele

1. **Recherche je Anbieter** für alle 156 Einträge mit App oder MCP: Gibt es einen
   **offiziellen** Remote-MCP-Server des Anbieters? Wenn ja: Adresse, Transport (http/sse),
   Anmeldeart, Quelle (Link auf die Anbieter-Doku, die die Adresse nennt).
2. **Maschinelle Prüfung ohne Konto** für jeden gefundenen Server (siehe unten).
3. **Vorlage erzeugen** für jeden Anbieter, dessen Server die Prüfung als „einrichtbar"
   besteht.
4. **Kennzeichen „ungeprüft"** im Vorlagenformat und auf der Seite „Integrationen".
5. **Seite „Integrationen" bleibt bei 100+ Vorlagen benutzbar**: Suchfeld und Gruppierung
   nach Kategorie im Bereich „Verfügbar".
6. **Abgleichsliste** über alle 180 Einträge mit Ergebnis und Grund.

## Nicht-Ziele

- Teil 3 (Einrichtungs-Agent) und Teil 5 (Skills als OpenFang-Skills).
- Listen lesender Werkzeuge (`read_only_tools`) für neue Vorlagen. Neue Vorlagen haben eine
  leere Liste, also braucht jedes Werkzeug eine Freigabe. Die Liste entsteht später bei
  Bedarf, wenn jemand die Vorlage nutzt.
- Eigene Connector-Server für Anbieter ohne offiziellen MCP-Server.
- App-Registrierungen bei Google, Microsoft, Slack oder anderen Anbietern, die keine
  Selbstregistrierung (RFC 7591) anbieten.
- Die Entscheidung über den alten Rowboat-Plugin-Weg (`packages/openai-plugin-runtime`,
  Connector-Brücke). Sie steht in der Teil-1-Spec als Teil-4-Punkt, wird aber hier nicht
  getroffen; dieser Teil fasst den alten Weg nicht an.
- Automatisches Umschalten von „ungeprüft" auf „geprüft". Das bleibt eine Änderung an der
  Vorlagen-Datei per Commit.

## Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- **Fremde MCP-Server.** Nur Server, die der Anbieter selbst betreibt oder in seiner
  eigenen Doku als offiziellen Server nennt. Community-Server, npm-Pakete Dritter oder
  Aggregatoren (Zapier, Composio, Pipedream und ähnliche) sind ausgeschlossen. Ein fremder
  Server sähe alle Daten und Zugänge des Nutzers.
- **Vorlagen, die man nicht einrichten kann.** Was die Prüfung nicht als „einrichtbar"
  besteht, wird keine Vorlage, sondern steht nur in der Abgleichsliste.
- **Werkzeuge ohne Freigabe** aus einer ungeprüften Vorlage.
- **Geheimnisse im Repo.** Vorlagen enthalten nur Namen von Tresor-Einträgen, nie Werte.
- **Bruch der bestehenden Vorlagen** GitHub und Vercel oder ihrer Freigabelisten.

## Rahmen, der schon steht (Teil 1, 1b, 2)

- Vorlagenformat: `IntegrationTemplate` in
  `openfang/crates/openfang-extensions/src/lib.rs`. Vorlagen-Ordner `integrations/`
  (vibemind-os), eingebunden über `template_dirs = ["../integrations"]` in
  `openfang/openfang.vibemind.toml`; geladen von `load_template_dirs` in `registry.rs`
  (eine kaputte Datei wird je Datei gemeldet).
- Anmeldearten heute:
  - **OAuth mit Selbstregistrierung** (`[auth] type = "oauth"`): Erkennung nach RFC 9728 und
    RFC 8414, Selbstregistrierung nach RFC 7591, PKCE, Refresh, Widerruf
    (`openfang-extensions/src/mcp_oauth.rs`; Beispiel `integrations/vercel.toml`).
  - **Schlüssel im Header** (`[[auth_headers]]` + `[[required_env]]`, Wert aus dem Tresor;
    Beispiel `integrations/github.toml`).
- `catalog.admission = "review_required"` **sperrt** die Installation (`NotAdmitted`).
  Das ist nicht dasselbe wie „ungeprüft" und wird für neue Vorlagen nicht benutzt.
- Seite „Integrationen" im Rowboat-Fork (`Flissel/rowboat`, Zweig `vibemind`,
  `apps/x/apps/renderer/src/components/integrations/`), Daten über IPC
  `integrations:status` (`apps/x/packages/shared/src/ipc.ts`,
  `apps/x/apps/main/src/integrations-ipc.ts`).

## Wie es gebaut wird

### 1. Recherche

- Je Anbieter eine Zeile in einer maschinenlesbaren Datei
  `integrations/abgleich/recherche.json`. Felder: `name` (OpenAI-Name), `anzeigename`,
  `server_url` oder leer, `transport` (`http`/`sse`), `anmeldung`
  (`oauth` / `schluessel` / `unbekannt`), bei Schlüssel: Header-Name, Format, Link zur
  Schlüsselseite; `quelle` (Link auf Anbieter-Doku); `offiziell` (true nur, wenn die Quelle
  auf der Domain des Anbieters liegt oder der Anbieter sie selbst verlinkt); `notiz`.
- Durchgeführt von Recherche-Agenten in Paketen zu je etwa 15–20 Anbietern. Jede
  Server-Adresse braucht einen Quellenlink; ohne Quelle gilt „kein offizieller Server
  gefunden".

### 2. Maschinelle Prüfung ohne Konto

Ein Prüfwerkzeug läuft über jede gefundene Adresse und schreibt das Ergebnis in die
Recherche-Datei zurück. **Es muss denselben Weg nehmen wie OpenFang beim Einrichten**
(also die Funktionen aus `mcp_oauth.rs` und dieselben URL-Regeln aus `UrlPolicy::strict`),
sonst ist ein grünes Ergebnis wertlos. Darum wird es in Rust geschrieben, als Beispiel- oder
Hilfsprogramm im Crate `openfang-extensions`.

Ergebnisse:

| Ergebnis | Bedingung |
|---|---|
| `einrichtbar_oauth` | Erkennung (RFC 9728 → RFC 8414) gelingt **und** der Autorisierungsserver nennt einen `registration_endpoint` |
| `einrichtbar_schluessel` | Anmeldung per Schlüssel laut Recherche **und** der Server antwortet auf `initialize` ohne Schlüssel mit 401 oder 403 |
| `einrichtbar_ohne_anmeldung` | `initialize` ohne jede Anmeldung liefert 200 mit gültiger MCP-Antwort |
| `braucht_registrierung` | Erkennung gelingt, aber ohne `registration_endpoint` |
| `nicht_erreichbar` | keine Antwort, TLS-Fehler, 404, Umleitung oder von `UrlPolicy` abgelehnt |
| `unklar` | alles andere; Grund als Klasse, nie Antwortinhalte |

Das Prüfwerkzeug legt **keine** Registrierung an (ruft `register_client` nicht auf) und
schickt keine Zugangsdaten. Antwortinhalte der Anbieter landen nie im Ergebnis, nur Klassen.

Entscheidungen zur Prüfung (vom Planer getroffen, nicht von Felix):

- Ein vorhandener `registration_endpoint` genügt für `einrichtbar_oauth`. Anbieter, die die
  Selbstregistrierung trotzdem einschränken (Freigabelisten für Rückleitungsadressen),
  fallen erst beim ersten echten Einrichten auf. Genau dafür steht „ungeprüft".
- Umleitungen werden nicht verfolgt und nicht automatisch korrigiert (gleiche Regel wie
  OpenFang). Solche Server landen in `nicht_erreichbar` mit Klasse `umleitung` und stehen
  so in der Abgleichsliste, damit man die Adresse von Hand nachziehen kann. Dasselbe gilt
  für reine SSE-Server, wenn die Erkennung an ihnen scheitert.
- Bietet ein Anbieter OAuth **und** Schlüssel an, gewinnt OAuth. Dann muss niemand einen
  Schlüssel abtippen, und er landet nicht im Tresor.

### 2b. Domain-Prüfung gegen fremde Server

Bevor eine Vorlage erzeugt wird, prüft der Erzeuger automatisch: Die registrierbare Domain
der Server-Adresse (zum Beispiel `hubspot.com` aus `mcp.hubspot.com`) muss gleich der
registrierbaren Domain der Quelle sein oder in einer kleinen, im Skript gepflegten Liste
bekannter Anbieter-Domains stehen (etwa `atlassian.com` und `atlassian.net`). Passt das
nicht, entsteht **keine** Vorlage. Der Eintrag landet mit Ergebnis `domain_pruefen` in der
Abgleichsliste und wird von Hand entschieden. Das ist die Absicherung gegen eine falsche
„offiziell"-Angabe aus der Recherche.

### 3. Vorlagen erzeugen

Ein Erzeuger-Skript schreibt aus der Recherche-Datei je Anbieter mit Ergebnis
`einrichtbar_*` eine Datei `integrations/<name>.toml`. Bestehende Dateien in
`vibemind-os/integrations/` (github, vercel) überschreibt es nie.

**Mitgelieferte Vorlagen.** OpenFang bringt 25 alte stdio-Vorlagen mit
(`openfang/crates/openfang-extensions/integrations/`, zum Beispiel gmail, google-calendar,
google-drive, linear, notion, slack). Eine gleichnamige Datei in `vibemind-os/integrations/`
ersetzt die mitgelieferte (`load_template_dirs` überschreibt per id). So ist es bei GitHub
schon gemacht. Installiert sind am 10.11. nur github und vercel. Regel:

- Hat der Anbieter einen einrichtbaren offiziellen Remote-Server, **ersetzt** die neue
  Vorlage die mitgelieferte mit derselben id.
- Sonst entsteht keine Datei, die mitgelieferte bleibt. Die Abgleichsliste nennt sie dann
  als `vorlage_mitgeliefert`.
- Weicht die id der mitgelieferten Vorlage vom OpenAI-Namen ab (z. B. `teams-mcp` gegenüber
  `teams`), gilt der OpenAI-Name. Die mitgelieferte bleibt unter ihrer id daneben stehen.

Felder:

- `id` = OpenAI-Name, `name` = Anzeigename, `description` = ein deutscher Satz
  („<Dienst> über den offiziellen Remote-MCP-Server").
- `category` aus einer festen Zuordnungstabelle im Skript.
- `icon` = ein Emoji je Kategorie.
- `tags` = 1–3 Stichworte.
- `[transport]` mit `type` und `url`.
- Anmeldung: `[auth] type = "oauth"` **oder** `[[auth_headers]]` + `[[required_env]]` mit
  Tresor-Namen `INTEGRATION_<ID>_KEY` und `get_url` aus der Recherche **oder**, bei
  `einrichtbar_ohne_anmeldung`, keins von beiden.
- OAuth-`scopes`: `["offline_access"]`, wenn der Autorisierungsserver das in
  `scopes_supported` nennt (für Refresh-Token), sonst leer. Das Prüfwerkzeug schreibt
  `scopes_supported` dafür mit in die Recherche-Datei.
- `read_only_tools = []`.
- `[catalog]`: `replaces_openai_plugin = "<name>"`, `license = "proprietary-service"`,
  `admission = "admitted"`, `verified = false`.
- Ein Kommentar oben mit Quelle und Prüfdatum.

### 4. Formatänderungen in OpenFang (Fork `Flissel/openfang`)

- `CatalogMeta` bekommt `verified: bool`, Vorgabe **true**, damit GitHub und Vercel
  unverändert als geprüft gelten.
- `IntegrationCategory` bekommt `sales`, `marketing`, `finance` und `design` dazu (Anzeige
  „Vertrieb", „Marketing", „Finanzen", „Design & Medien"). Die bestehenden Werte bleiben.
  Was in keine Kategorie passt, kommt nach `productivity`. Weitere Kategorien nur, wenn
  sonst mehr als 10 Anbieter falsch einsortiert wären; das ist dann eine Entscheidung des
  Planers und wird im Plan vermerkt.
- Die API, die Rowboat liest, reicht `verified` und `category` durch.
- Test: Jede Datei in `integrations/` lädt fehlerfrei, und keine hat einen leeren
  `replaces_openai_plugin`.

### 5. Seite „Integrationen" (Rowboat-Fork)

- Bereich „Verfügbar": Suchfeld (Name und Stichworte, ohne Groß-/Kleinschreibung) und
  Gruppen je Kategorie, alphabetisch innerhalb der Gruppe.
- Vorlagen mit `verified = false` tragen ein Kennzeichen „ungeprüft". Ein Hinweistext sagt:
  „Noch von niemandem eingerichtet. Funktioniert vermutlich, alle Werkzeuge brauchen eine
  Freigabe."
- Installierte Integrationen behalten ihre Karte; dort ist das Kennzeichen ebenfalls
  sichtbar, bis die Vorlage per Commit auf geprüft gesetzt ist.

### 6. Abgleichsliste

`integrations/abgleich/README.md`, erzeugt aus der Recherche-Datei und dem gepinnten
Katalog. Eine Zeile je Katalogeintrag (alle 180): Name, Sorte, Ergebnis, Grund, Quelle,
Prüfdatum. Ergebnisse: `vorlage`, `vorlage_bestand` (github, vercel), `vorlage_mitgeliefert`,
`braucht_registrierung`, `domain_pruefen`, `kein_offizieller_server`, `nicht_erreichbar`,
`unklar`, `skills_teil_5`. Oben eine Zusammenfassung mit Zahlen je Ergebnis.

## Stand der Recherche

Die Anbieter-Recherche (Schritt 1) läuft schon am 10.11. parallel zur Abnahme dieses
Entwurfs. Ihre Ergebnisse liegen zunächst außerhalb des Repos und kommen mit dem ersten
Plan-Schritt nach `integrations/abgleich/recherche.json`.

## Ausrollen

Code und Vorlagen werden gemergt und gepusht. **Wirksam** werden sie erst mit
OpenFang-Neubau und -Neustart sowie mit einer neuen Rowboat-Fassung. Beides braucht Felix'
OK (Deploy-Regel). Der OpenFang-Neustart lässt sich mit dem anstehenden Neubau für den
Telegram-Token-Fix (`d33f713`) zusammenlegen.

## Dem Bauenden überlassen

- Name und Form des Prüfwerkzeugs und des Erzeuger-Skripts.
- Kategorie-Zuordnung je Anbieter und Emoji je Kategorie.
- Paketgröße und Reihenfolge der Recherche.
- Gestaltung von Suchfeld, Gruppen und Kennzeichen im Stil der bestehenden Seite.

## Offene Punkte

- Entscheidung über den alten Rowboat-Plugin-Weg (siehe Nicht-Ziele).
- Anbieter mit `braucht_registrierung`: je Anbieter einzeln, wenn jemand ihn braucht.
