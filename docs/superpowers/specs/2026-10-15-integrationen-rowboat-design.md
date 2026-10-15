# Eigenes Plugin-System, Teilprojekt 2: Integrationen in Rowboat Desktop

Datum: 2026-10-15. Baut auf Teilprojekt 1 und 1b auf: openfang `68f4c03`, E2E-PROOF Teil VIII und IX.
Ziel-App: das neue Rowboat Desktop, eingeführt mit vibemind-os PR #151 (Merge `987d1cac`). Sein Quellcode liegt im Fork
`Flissel/rowboat`, Branch `vibemind` (Stand `ef2b538f`). Lokal wird darin im Worktree
`C:/Users/User/ClaudeWork/wt-rowboat-vibemind` gearbeitet.

## 1. Ziel

Der User richtet Integrationen komplett in Rowboat Desktop ein, ohne Terminal und ohne Claude. Rowboat ist dafür eine
reine Oberfläche, die Quelle der Wahrheit bleibt OpenFang auf `127.0.0.1:4200`.

**Erfolg heißt**, ein neuer Seitenleisten-Eintrag „Integrationen“ zeigt Folgendes:
- je installierter Integration den Zustand, die Werkzeugzahl und die offenen Freigaben,
- Knöpfe zum Anmelden, Abmelden, Schlüssel eintragen, Neu verbinden und Entfernen,
- eine Liste der verfügbaren Remote-Vorlagen zum Hinzufügen.

Anbieter-Login und Schlüssel-Seite öffnen sich im Standardbrowser des PCs.

**Entscheidungen des Users:**
- Die Oberfläche lebt in Rowboat, nicht im OpenFang-Dashboard.
- Gemeint ist die neue Desktop-Variante.
- Integrationen bekommen einen eigenen Eintrag in der Seitenleiste.
- Umfang: installierte Integrationen verwalten und neue aus dem Katalog hinzufügen.
- Welcher Agent welche Integration sieht, bleibt in den Agent-Manifesten.

## 2. Nicht-Ziele

- Werkzeugaufrufe in Rowboat freigeben. Rowboat zeigt nur die Zahl offener Freigaben und verlinkt auf das OpenFang-Dashboard.
- Agent-Zugriff (`mcp_servers`) aus Rowboat ändern.
- Den Setup-Agenten bauen (Teilprojekt 3).
- Den Plugins-Tab in den Einstellungen und die Rowboat-Web-App ersetzen (Teilprojekt 4).
- OpenFang ändern. Es wird nur die vorhandene API aus 1 und 1b genutzt.
- Die eingebauten stdio-Vorlagen von OpenFang anbieten (aws, notion, slack …). Sie erwarten Schlüssel aus der Umgebung und
  passen nicht zum `INTEGRATION_`-Modell.

## 3. Aufbau

```
Renderer (React)                  Main-Prozess (Electron)                 OpenFang 127.0.0.1:4200
Seite "Integrationen"  ──IPC──▶   OpenFangIntegrationsClient  ──HTTP──▶   /api/integrations …
                                  hält OPENFANG_API_KEY (Bearer)          /api/approvals
                                  prüft Ziel-URLs ──shell.openExternal──▶ Standardbrowser
```

### 3.1 Main-Prozess: OpenFang-Client

Neues Modul im Main-Prozess, dazu IPC-Handler in `apps/main/src/ipc.ts`.

- **Basis-URL:** fest `http://127.0.0.1:4200`. Sie ist weder konfigurierbar noch aus dem Renderer setzbar.
- **Schlüssel:** aus `process.env.OPENFANG_API_KEY`. Er wird nie geloggt, nie an den Renderer gegeben und nie in
  `<WorkDir>/config` gespeichert.
- **HTTP:** Timeout 10 s, keine Redirects (Redirect gilt als Fehler), keine Proxies.
- **Integrations-IDs:** Jede ID, die vom Renderer kommt, wird gegen `^[a-z0-9][a-z0-9-]{0,62}$` geprüft. Das ist dieselbe
  Regel wie in OpenFang. Erst danach wird sie in einen Pfad eingesetzt.

**IPC-Kanäle** (zod-Schemas in `packages/shared/src/ipc.ts`):

| Kanal | Request | Aufruf an OpenFang | Response an den Renderer |
|---|---|---|---|
| `integrations:status` | – | `GET /api/integrations`, `GET /api/integrations/available`, `GET /api/approvals` | `{ verbindung, installiert[], verfuegbar[] }` (s. u.) |
| `integrations:add` | `{id}` | `POST /api/integrations/add {"id"}`; danach je nach Zustand `oauth/start` oder `schluessel/link` und Browser öffnen | `{ok, zustand, meldung?}` |
| `integrations:anmelden` | `{id}` | `POST /api/integrations/{id}/oauth/start` → `anmelde_url` prüfen → `openExternal` | `{ok, meldung?}` |
| `integrations:abmelden` | `{id}` | `POST /api/integrations/{id}/oauth/abmelden` | `{ok, meldung?}` |
| `integrations:schluessel` | `{id}` | `POST /api/integrations/{id}/schluessel/link` → URL prüfen → `openExternal` | `{ok, meldung?}` |
| `integrations:neuVerbinden` | `{id}` | `POST /api/integrations/{id}/reconnect` | `{ok, meldung?}` |
| `integrations:entfernen` | `{id}` | `DELETE /api/integrations/{id}` | `{ok, meldung?}` |
| `integrations:freigabenOeffnen` | – | – (öffnet `http://127.0.0.1:4200/#approvals` im Browser) | `{ok}` |

**Antwort von `integrations:status`.** Der Main-Prozess baut aus jeder OpenFang-Antwort ein neues Objekt mit genau diesen
Feldern, eine Positivliste. Alles andere, etwa `auth_headers` oder Referenznamen, verwirft er.

- **`verbindung`:** `"ok" | "openfang_aus" | "schluessel_fehlt" | "schluessel_falsch"`.
- **`installiert[]`:** `{ id, name, icon, zustand, detail, werkzeuge, statischerSchluessel: boolean, offeneFreigaben }`.
  - `werkzeuge` kommt aus `tool_count`, aber nur wenn `zustand == "verbunden"`, sonst 0.
  - `statischerSchluessel` ist `auth_headers.length > 0`. Der Referenzname wird nicht weitergegeben.
  - `offeneFreigaben` zählt die offenen Freigaben, deren Werkzeugname mit `mcp_<id>_` beginnt. Bei IDs mit Bindestrich
    ersetzt OpenFang diesen im Werkzeugnamen durch `_`.
- **`verfuegbar[]`:** `{ id, name, icon, beschreibung, kategorie }`. Nur Vorlagen mit `transport` `http` oder `sse`, die
  `installed == false` sind und `admission == "admitted"`.

**Fehler werden zu festen deutschen Meldungen:**
- **Verbindung abgelehnt oder Timeout:** `verbindung = "openfang_aus"`.
- **`OPENFANG_API_KEY` leer:** `verbindung = "schluessel_fehlt"`. OpenFang wird dann gar nicht aufgerufen.
- **HTTP 401:** `verbindung = "schluessel_falsch"`.
- **HTTP 409 mit einem `error`-Text aus OpenFang:** Der Text wird nur weitergegeben, wenn er auf der Positivliste fester
  Klassen steht, nämlich `tresor nicht verfuegbar`, `ermittlung fehlgeschlagen: …` und die Klassen aus `OAuthError::class()`.
  Sonst lautet die Meldung „Aktion fehlgeschlagen (HTTP 409)“.
- **Alle anderen Statuscodes:** „Aktion fehlgeschlagen (HTTP <code>)“. Den Antworttext gibt der Main-Prozess nie weiter.

**URL-Prüfung vor `openExternal`.** Bei einem Verstoß kommt die Meldung „Ungültiger Link von OpenFang“, und es wird
nichts geöffnet.
- **Anmelde-URL:** Schema exakt `https:`, ohne Nutzerdaten (`user:pass@`), mit einem Host, der kein IP-Literal ist.
- **Schlüssel-Link:** Schema `http:`, Host exakt `127.0.0.1`, Port `4200`, Pfad beginnt mit
  `/api/integrations/<id>/schluessel/`, wobei `<id>` die angefragte ID ist. Keine Query, kein Fragment.
- **Freigaben:** fest `http://127.0.0.1:4200/#approvals`.

**Logs.** Rowboats Logs enthalten nur Kanal, ID, HTTP-Status und Dauer. Nie URLs mit `state`, nie Links, nie Bodys.

### 3.2 Renderer: Seite „Integrationen“

- **Seitenleiste:** Neuer Eintrag „Integrationen“ in `components/dock-sidebar.tsx`, direkt unter „Apps“, mit einem
  lucide-Icon (z. B. `Plug`). Dazu kommt ein neuer `ViewState` `integrations` in `App.tsx`, nach dem Muster von `apps`
  (Section-Liste, `switch`-Zweige, `KeepAliveSection`, `activeNav`).
- **Komponente:** `components/integrations/integrations-page.tsx` mit Unterkomponenten `IntegrationCard` und `AvailableList`.
  - Stil: shadcn/Tailwind wie auf der Apps-Seite.
  - Texte auf Deutsch.
- **Aktualisierung:** `integrations:status` wird abgefragt, solange die Seite sichtbar ist. Das Intervall beträgt 3 s nach
  einer Aktion, die den Browser geöffnet hat, sonst 10 s. Ist die Seite unsichtbar, ruht die Abfrage.
- **Hinweise oben auf der Seite** statt der Karten:
  - bei `openfang_aus`: „OpenFang läuft nicht (127.0.0.1:4200)“, die Karten werden ausgegraut.
  - bei `schluessel_fehlt` oder `schluessel_falsch`: „Rowboat hat keinen gültigen OpenFang-Schlüssel – Rowboat über
    start_desktop.py neu starten“.

**Zustände und Knöpfe:**

| `zustand` | Anzeige | Knöpfe |
|---|---|---|
| `verbunden` | grün, „verbunden · N Werkzeuge“ | Abmelden (nur wenn `!statischerSchluessel`), Neu verbinden, Entfernen |
| `anmeldung_noetig` | gelb, „Anmeldung nötig“ + `detail` | **Anmelden**, Entfernen |
| `fehlt_schluessel` | gelb, „Schlüssel fehlt“ | **Schlüssel eintragen**, Entfernen |
| `schluessel_abgelehnt` | rot, „Schlüssel abgelehnt“ | **Schlüssel eintragen**, Neu verbinden, Entfernen |
| `nicht_erreichbar` | rot, „nicht erreichbar“ + `detail` | Neu verbinden, Schlüssel eintragen (nur wenn `statischerSchluessel`), Entfernen |
| `vorlage_ungueltig`, `nicht_zugelassen` | grau, Erklärtext | Entfernen |
| `verfuegbar` oder unbekannt | grau, „wird geprüft …“ | keine |

**Weitere Regeln:**
- **Rückfragen:** Vor „Entfernen“ und „Abmelden“ kommt ein Bestätigungsdialog.
- **Hinzufügen:** Ruft `integrations:add` auf. Der Main-Prozess öffnet je nach dem Zustand, den `add` zurückgibt, die
  Anmeldung (`anmeldung_noetig`) oder die Schlüssel-Seite (`fehlt_schluessel`). `has_oauth` aus `/available` wird
  ignoriert, weil es die OAuth-Art aus 1b nicht kennt.
- **Freigaben:** Ist `offeneFreigaben > 0`, zeigt die Karte ein Abzeichen „N Freigaben offen“. Ein Klick darauf ruft
  `integrations:freigabenOeffnen` auf.
- **Meldungen:** Aktionsfehler erscheinen als Toast mit dem `meldung`-Text.

### 3.3 Weitergabe des Schlüssels: `start_desktop.py` (vibemind-os)

`spaces/rowboat/scripts/start_desktop.py` liest beim normalen Start, also ohne `--install`, aus
`<Vibemind_V1>/.env` genau den Wert `OPENFANG_API_KEY` und setzt ihn nur in der Umgebung des Prozesses `npm run dev`
(apps/x).
- Andere Werte aus `.env` gibt es nicht weiter.
- Der Wert erscheint in keiner Ausgabe.
- Fehlt er, warnt das Skript mit „OPENFANG_API_KEY nicht gefunden – Integrationen-Seite zeigt Hinweis“ und startet
  trotzdem.
- Der `.env`-Pfad kann mit `--env-file` überschrieben werden. Standard ist das Repo-Root von Vibemind_V1, ermittelt
  relativ zum Skript.

## 4. Sicherheit

- **Schlüssel und Tokens:** Der OpenFang-Schlüssel bleibt im Main-Prozess. Renderer, Logs, Dateien und Crash-Berichte
  bekommen ihn nicht.
- **Links nur aus dem Main-Prozess:** Anmelde-URL (mit `state` und PKCE-Challenge) und Einmal-Link durchlaufen nur den
  Main-Prozess und gehen direkt an `openExternal`. Der Renderer sieht sie nie.
- **Kein offener Proxy:** Der Renderer kann nur die festen Kanäle aus 3.1 auslösen, mit geprüfter ID. Es gibt keinen
  generischen Proxy und keine frei wählbaren Pfade oder URLs.
- **Tresor-Grenzen aus 1b bleiben:** Die PC-only-Seiten aus 1b (Callback, Schlüssel-Seite) bleiben PC-only. Rowboat läuft
  auf demselben PC und öffnet sie dort im Browser.
- **Keine Werte durchreichen:** Rowboat zeigt nur feste Zustände und Klassen, nie Werte.

## 5. Tests

**Main-Prozess (vitest):**
- **Client:**
  - nur `127.0.0.1:4200`, mit Bearer
  - leerer Schlüssel ergibt `schluessel_fehlt` ohne Netzaufruf
  - 401 ergibt `schluessel_falsch`, abgelehnte Verbindung ergibt `openfang_aus`
  - 409 mit Klasse auf der Positivliste gibt die Klasse weiter, mit einem anderen Text die generische Meldung
  - ein Redirect wird zum Fehler
- **Positivliste:** Eine OpenFang-Antwort mit zusätzlichen Feldern (`auth_headers`, `anmelde_url`, `token`, `credential`)
  liefert im Ergebnis keines davon.
- **Zählen und Filtern:** Freigaben werden je Integration gezählt, auch für IDs mit Bindestrich. `verfuegbar` filtert
  stdio, installierte und nicht zugelassene Vorlagen heraus.
- **URL-Prüfung:**
  - Anmelde-URL: `https` ok; abgelehnt werden `http`, `javascript:`, Nutzerdaten in der URL und IP-Literale.
  - Schlüssel-Link: exakt passend ok; abgelehnt werden ein anderer Host, ein anderer Port, eine fremde ID im Pfad, eine
    Query und `https`.
  - `openExternal` wird nur bei gültigen URLs aufgerufen (Mock).
- **`add`:** Je nach zurückgegebenem Zustand wird der richtige Folgeaufruf gemacht.
- **ID-Prüfung:** Ungültige IDs werden vor jedem Netzaufruf abgelehnt.

**Renderer (vitest + jsdom):**
- Die Tabelle aus 3.2 wird Zeile für Zeile geprüft: Zustand → Anzeige → Knöpfe.
- Die Hinweise für `openfang_aus`, `schluessel_fehlt` und `schluessel_falsch`.
- Bestätigungsdialog vor Entfernen und Abmelden.
- Das Abzeichen für Freigaben.
- Die Abfrage startet und stoppt mit der Sichtbarkeit (Fake-Timer).

**IPC-Vertrag:** Die zod-Schemas lehnen ungültige IDs ab.

**`start_desktop.py` (pytest, `spaces/rowboat/tests`):**
- nur `OPENFANG_API_KEY` wird übernommen
- ein fehlender Wert ergibt eine Warnung ohne Abbruch
- der Wert erscheint in keiner Ausgabe (capsys)

**Bestehende Suites bleiben grün:**
- Renderer
- `npm run typecheck`
- `spaces/rowboat/tests`
- der Sync-Wächter, d. h. nach dem Sync ist die Kopie identisch mit dem neuen Fork-Stand

## 6. Auslieferung

1. **Code im Fork.** Im Fork-Worktree `wt-rowboat-vibemind` (Branch `vibemind`) entsteht der Code in kleinen Commits, die
   App-Änderungen nach dem Muster der vorhandenen VibeMind-Patches. Danach Review und Push nach `Flissel/rowboat` `vibemind`.
2. **Übernahme in vibemind-os.**
   - `sync_desktop.py --fork <wt-rowboat-vibemind> --fork-ref vibemind …` übernimmt `apps/x` und aktualisiert
     `UPSTREAM.md`. Den Patch trägt das Skript in die Liste ein.
   - `start_desktop.py` bekommt die Schlüssel-Weitergabe.
   - Beides wird auf `master` committet.
3. **Neustart** von Rowboat Desktop über `start_desktop.py` (vorher `--install`, falls Abhängigkeiten sich ändern), nur
   nach User-OK. OpenFang wird nicht neu gestartet.
4. **Live-Nachweis** mit dem User:
   - Die Seite zeigt GitHub und Vercel als `verbunden` mit Werkzeugzahl.
   - Vercel per Klick abmelden: Rückfrage, dann `anmeldung_noetig`.
   - Per Klick anmelden: Der Browser öffnet sich, nach dem Login springt die Karte auf `verbunden`.
   - Bei GitHub „Schlüssel eintragen“: Die Einmal-Seite öffnet sich im Browser (dort nichts eintragen).
   - Unter „Verfügbar“ steht nichts, solange GitHub und Vercel installiert sind. Ein kurzes Entfernen und Wieder-Hinzufügen
     von Vercel zeigt den Katalog-Fluss. Das geschieht nur mit User-OK, weil er sich danach neu anmelden muss.
   - Mit dem User-OK zum kurzen Stopp von OpenFang wird der Hinweis „OpenFang läuft nicht“ geprüft, danach wieder starten.
   - Suche nach Token-Spuren in den Logs von Rowboat Desktop und in `<WorkDir>`: kein `github_pat_`, `ghp_`, `vca_`,
     `vcr_`, kein `OPENFANG_API_KEY`-Wert, kein `state=`.
5. **Abschluss:** Nachweis als E2E-PROOF Teil X, Claims schließen, Notizen aktualisieren.

## 7. Offene Punkte für später

- **Teilprojekt 3:** Der Setup-Agent kann den User auf diese Seite schicken (Deep-Link) oder dieselben IPC-Kanäle nutzen.
- **Teilprojekt 4:** Der alte Plugins-Tab in den Einstellungen verweist auf diese Seite. Weitere Remote-Vorlagen kommen
  in den Katalog.
- **Freigeben direkt in Rowboat:** Das wäre ein eigenes, kleines Teilprojekt.
