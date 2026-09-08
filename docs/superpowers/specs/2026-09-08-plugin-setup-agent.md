# Plugin-Setup-Agent — Entwurf

**Ziel in einem Satz.** Ein Plugin, das einen Schlüssel braucht, soll sich
einrichten lassen, ohne dass jemand weiß, wo Secrets liegen: Fenster auf,
anmelden oder Schlüssel eintragen — und wenn der Schlüssel nachweislich
funktioniert, sortiert OpenFang ihn selbsttätig ein und das Plugin ist
aufrufbar.

**Stand, auf dem das aufsetzt.** Die Kette Katalog → Installation → Freigabe →
Credential pro Aufruf → Provider ist gebaut und bewiesen (W1–W3, `E2E-PROOF.md`
Teile I–IV); seit `605c6f0d` liefert ein gebautes Image die Plugin-Seite mit dem
gepinnten Katalog. Was fehlt, ist der Weg **hinein**.

---

## Was heute fehlt (gemessen am 08.09.2026)

| # | Lücke | Beleg |
| --- | --- | --- |
| 1 | OpenFang nimmt Credentials nicht über HTTP entgegen | nur `POST /api/credentials/issue` (lesen), `server.rs:738` |
| 2 | Die Ausgabe-Allowlist ist beim Start eingefroren | `OPENFANG_ISSUABLE_CREDENTIALS` → `HashSet`, `server.rs:49`, Kommentar „Read once" |
| 3 | Rowboat nennt die falschen Credential-Namen | `requiredCredentialNames` liefert die rohe OAuth-URL statt `OAUTH_BEARER_…`; für cloudflare/canva „nichts" |
| 4 | „Als Werkzeug binden" hat keine REST-Route | Routen unter `app/api/v1/**/plugins`: nur GET/POST/PATCH |
| 5 | Kein Ort, an dem ein Fenster den Wert entgegennimmt | `scripts/provision-oauth-token.py` holt Tokens, schreibt in eine Datei |

Vorhanden und wiederverwendbar: OpenFangs **verschlüsselter Vault**
(`credentials.rs`: Vault → dotenv → Env), der Schreibpfad `write_secret_env`
(setzt zusätzlich sofort `std::env::set_var`), der OAuth-Provisioner, die
Installations-Route mit `componentDigests`, die Shim-Werkzeugbrücke
`SHIM_EXTRA_MCP_CONFIG`, und `supabase_vault` (installiert, geprüft).

---

## Der Weg eines Schlüssels

```text
  Mensch                Setup-Agent            Supabase            OpenFang
    |                        |                    |                   |
    |  Fenster (Chrome)      |                    |                   |
    |<---------------------- |                    |                   |
    |  anmelden / eintragen  |                    |                   |
    |----------------------->|                    |                   |
    |                        | 1. ablegen         |                   |
    |                        |------------------->| vault + Zeile     |
    |                        |                    |  "entgegengenommen"
    |                        | 2. VERIFIZIEREN (echter Aufruf beim Anbieter)
    |                        |------------------------------------->  X
    |                        |    200 = gut, 401 = zurück an den Menschen
    |                        | 3. uebergeben      |                   |
    |                        |----------------------------------->|   |
    |                        |                    |   Vault + Allowlist,
    |                        |                    |   ohne Neustart   |
    |                        | 4. Kopie loeschen  |                   |
    |                        |------------------->| Zeile "uebernommen"
    |                        |                    |  (nie ein Wert)   |
    |                        | 5. installieren + Werkzeug binden      |
```

---

## Entscheidungen

### D1 — Ein eigenständiger Space, wie sales-claw

`spaces/plugin-setup/` mit eigenem `docker-compose.yml`, eigener
`config/openclaw.json` samt Arbeitsbereich, eigenem MCP-Server, eigenen
Deploy-Skripten (`bootstrap.sh`, `smoke.sh`, `update.sh`, `wache.sh`,
`sicherung.sh`) und eigener Dokumentation. Er kümmert sich um nichts anderes
als das Einrichten von Plugins. Kein Anhängsel an marketing-claw, keine
Mitbenutzung eines fremden Gateways.

### D2 — Supabase nimmt entgegen, OpenFang übernimmt

Der Schlüssel landet **zuerst** in Supabase: Wert im `supabase_vault`
(verschlüsselt), daneben eine Zustandszeile. Das ist der Eingang — dort kann
ein Mensch nachsehen, was angefangen und was liegengeblieben ist.

**Nach bestandener Verifikation übernimmt OpenFang den Wert in seinen eigenen
Vault, und die Kopie in Supabase wird gelöscht.** Danach steht in Supabase nur
noch die Tatsache, dass es passiert ist — nie der Wert.

Das ist der Ausgleich zwischen zwei berechtigten Ansprüchen. Der Eingang gehört
dorthin, wo man ihn sieht und verwalten kann. Die **Verwahrung** gehört zur
Freigabe-Instanz: OpenFang genehmigt den Schreibzugriff und gibt das Credential
für genau diesen Aufruf aus — ein Widerruf wirkt beim nächsten Aufruf, und das
gilt nur, solange es *eine* Verwahrstelle gibt. Zwei dauerhafte Kopien wären
zwei Widerrufsflächen; genau davor schützt der Löschschritt.

### D3 — Verifiziert heißt: hat beim Anbieter funktioniert

„Verifiziert" ist kein Häkchen, sondern ein echter Aufruf gegen den Anbieter,
bevor OpenFang den Wert je sieht:

| Form | Prüfung | gut | schlecht |
| --- | --- | --- | --- |
| bearer (`GITHUB_PAT_TOKEN`) | `GET https://api.github.com/user` | 200 | 401 |
| oauth (`OAUTH_BEARER_*`) | MCP `initialize` gegen die Ressource | 200 | 401 |
| connector (`CONNECTOR_<APP>`) | Responses-API mit `connector_id` | 200 | 401 |

Scheitert sie, geht der Vorgang zurück an den Menschen — mit dem
Statuscode, nie mit dem Antwortkörper. **Damit bekommt OpenFang nie einen
Blindgänger**, und das ist die halbe Antwort auf „ohne dabei kaputt zu gehen".

### D4 — Wie OpenFang „einsortiert", ohne kaputtzugehen

Die Übernahme läuft über einen **bewachten Endpunkt** des Daemons
(`POST /api/credentials/store`), den der Agent nach bestandener Verifikation
ruft. Der Daemon entscheidet selbst, wohin der Wert gehört (Vault), trägt die
Referenz in seine Ausgabeliste ein und macht sie **ohne Neustart** ausgebbar;
die Eintragung überlebt einen Neustart.

Bewusst **kein** Abfrage-Kreisel im Daemon, der Supabase pollt: Das wäre eine
neue Netzabhängigkeit und eine neue Fehlerquelle *innerhalb* des Dienstes, der
gerade nicht kaputtgehen soll — und bei ausgefallener Datenbank hätte er keinen
sinnvollen Zustand. Aus Sicht des Menschen ist es trotzdem automatisch:
Niemand trägt etwas in OpenFang ein.

Vier Eigenschaften, die die Übernahme nicht verletzen darf:

1. **Additiv.** Eine bestehende Referenz wird nie stillschweigend überschrieben
   (`409`, Überschreiben nur ausdrücklich).
2. **Fail closed.** Ungültiger Name, leerer Wert, fehlende Berechtigung, kein
   Vault → Ablehnung, kein Teilzustand.
3. **Neustartfest.** Was ausgebbar war, ist es nach einem Neustart wieder.
4. **Stumm.** Der Wert erscheint in keiner Antwort, keinem Protokoll, keiner
   Fehlermeldung.

### D5 — Das Fenster ist ein sichtbarer Browser, den openclaw treibt

Über das **native openclaw-Gateway `:18793`** (`browser.defaultProfile =
"openclaw"`, verwaltetes sichtbares Chrome; Windows-Aufgabe „OpenClaw
Gateway") — 2026-05-19 Ende-zu-Ende bewiesen. Für oauth-Referenzen fährt der
Agent den bestehenden Provisioner; für Schlüssel-Referenzen führt er zur
Ausgabestelle des Anbieters und nimmt den Wert im selben Fenster entgegen.

### D6 — Rowboat sagt die Wahrheit über den Bedarf

`requiredCredentialNames` liefert künftig die Namen, unter denen OpenFang die
Werte kennt, nach denselben Regeln wie der Resolver: bearer wörtlich; oauth →
`OAUTH_BEARER_<HOST_PFAD>`; HTTP-MCP ohne jede Deklaration → eigene URL nach
derselben Regel; Connector-App → `OPENAI_API_KEY` **und** `CONNECTOR_<APP>`.
**Die Regel wird geteilt, nicht kopiert.**

### D7 — Was der Agent nicht darf

Keine Freigaben erteilen (das bleibt der Mensch in OpenFang), keinen Daemon
neu starten, keine bestehende Referenz ohne ausdrückliche Bestätigung
überschreiben, und **niemals** einen Wert in Protokolle, Antworten oder eine
Supabase-Zustandszeile schreiben.

---

## Abnahme

Ein heute nicht aufrufbares Plugin wird in einem Durchgang nutzbar: Der Agent
nennt den korrekten Referenznamen, das Fenster öffnet sich, der Wert landet in
Supabase, die Verifikation gegen den Anbieter besteht, OpenFang übernimmt und
gibt **ohne Neustart** aus, die Supabase-Kopie ist gelöscht, das Plugin ist
komponenten-genau installiert und gebunden, ein Aufruf läuft über die Freigabe
bis zum Provider, und die Zustandszeile erzählt den Vorgang — ohne den Wert.
Bewiesen gegen einen **isolierten Daemon**, nicht gegen `:4200`.

## Ausdrücklich nicht in diesem Entwurf

- **Rowboats Agentenlaufzeit durch openclaw ersetzen.** ~3.160 Zeilen mit zwei
  Einstiegspunkten, an denen `pluginBinding` zur Ausführung wird; jeder Beweis
  aus W1–W3 hängt daran.
- **Die Umstellung des laufenden `:4200`-Daemons.** Er läuft ohne `api_key`,
  weshalb die Ausgabe dort per Design verweigert — maschinenweite Entscheidung,
  eigener freigegebener Schritt.
- **Dauerhafte Secret-Kopien in Supabase.** Der Eingang ja, die Verwahrung
  nein (D2).

## Voraussetzungen, die heute nicht erfüllt sind

- **OpenFang `:4200` läuft nicht** (gemessen 08.09.). Für die Entwicklung
  irrelevant (isolierter Daemon), für die Nutzung nicht.
- **Das native openclaw-Gateway `:18793` läuft nicht**, und seine
  Konfiguration zeigt laut Notiz auf `openai/gpt-4o-mini` — ein Modell ohne
  Guthaben; es muss auf die Subscription-Lane (Shim `:8114`) gezogen werden.
  Datei ist UTF-8 **mit BOM**.
