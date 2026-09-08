# Plugin-Setup-Agent — Entwurf

**Ziel in einem Satz.** Ein Plugin, das einen Schlüssel braucht, soll sich
einrichten lassen, ohne dass jemand weiß, wo Secrets liegen: Fenster auf,
anmelden oder Schlüssel eintragen, fertig — das Plugin ist installiert und
aufrufbar.

**Stand, auf dem das aufsetzt.** Die Kette Katalog → Installation → Freigabe →
Credential pro Aufruf → Provider ist gebaut und bewiesen (W1–W3, `E2E-PROOF.md`
Teile I–IV), und seit `605c6f0d` liefert ein gebautes Image die Plugin-Seite mit
dem gepinnten Katalog. Was fehlt, ist der Weg **hinein**: ein Credential
bekommt man heute nur, indem ein Mensch eine Datei bearbeitet und den Daemon
neu startet.

---

## Was heute fehlt (gemessen am 08.09.2026)

| # | Lücke | Beleg |
| --- | --- | --- |
| 1 | OpenFang nimmt Credentials nicht über HTTP entgegen | nur `POST /api/credentials/issue` (lesen), `server.rs:738` |
| 2 | Die Ausgabe-Allowlist ist beim Start eingefroren | `OPENFANG_ISSUABLE_CREDENTIALS` → `HashSet` in `server.rs:49`, Kommentar „Read once" |
| 3 | Rowboat nennt die falschen Credential-Namen | `requiredCredentialNames` liefert die rohe OAuth-URL statt `OAUTH_BEARER_…`; für cloudflare/canva „nichts" |
| 4 | „Als Werkzeug binden" hat keine REST-Route | Routen unter `app/api/v1/**/plugins`: nur GET/POST/PATCH, kein add-tool |
| 5 | Kein Ort, an dem ein Fenster den Wert entgegennimmt | `scripts/provision-oauth-token.py` holt Tokens, schreibt aber in eine Datei |

Vorhanden und wiederverwendbar: OpenFangs **verschlüsselter Vault**
(`credentials.rs`: Vault → dotenv → Env), der Schreibpfad `write_secret_env`
(setzt zusätzlich sofort `std::env::set_var`, heute aber nur für Kanal-Felder),
der OAuth-Provisioner, die Installations-Route mit `componentDigests`, und die
Shim-Werkzeugbrücke `SHIM_EXTRA_MCP_CONFIG` (openclaw-Agenten können MCP-
Werkzeuge rufen — die frühere Sperre ist weg).

---

## Entscheidungen

### D1 — Werte in OpenFangs Vault, Zustand in Supabase

Die Credential-Werte bleiben in OpenFang. Nicht aus Gewohnheit, sondern weil
OpenFang die **Freigabe-Instanz** ist: Es genehmigt den Schreibzugriff *und*
gibt das Credential für genau diesen Aufruf aus, und ein Widerruf wirkt beim
nächsten Aufruf. Zwei Speicher hieße zwei Widerrufsflächen — eine widerrufene
Freigabe bei weiterlaufendem Credential ist genau das, was diese Kette
verhindern soll. Dazu: Supabase ist in diesem Stack absichtlich von vielen
Diensten erreichbar; Plugin-Credentials sollen die kleinste erreichbare Fläche
haben (dafür existiert die Allowlist). Und OpenFang hat keinen Postgres-Client
— jede Ausgabe liefe über PostgREST und hinge an Supabase.

`supabase_vault` ist installiert und wäre technisch geeignet — die Entscheidung
fällt nicht an der Fähigkeit, sondern an der Zuständigkeit.

**Supabase bekommt den Setup-Zustand**, niemals einen Wert: welches Plugin, in
welchem Projekt, unter welchem Referenznamen, von wem, wann, mit welchem
Status, wann zuletzt erfolgreich benutzt. Das ist abfragbar, mit dem Rest von
VibeMind joinbar und füllt genau die Lücke, die die Brücken-Inventur vom
03.09. gefunden hat: null Brücken bewegen heute Zustand.

### D2 — Das Fenster ist ein sichtbarer Browser, den openclaw treibt

Der Setup-Agent öffnet ein echtes Chrome-Fenster über das **native
openclaw-Gateway auf `:18793`** (`browser.defaultProfile = "openclaw"`,
verwaltetes sichtbares Chrome; Windows-Aufgabe „OpenClaw Gateway"). Das ist
2026-05-19 Ende-zu-Ende bewiesen worden und trägt auch Provider mit
eigenwilligen Anmeldungen, bei denen ein reiner Weiterleitungs-Flow nicht
reicht.

**Zwei Wege, ein Ziel:** Für Referenzen OAuth-Form fährt der Agent den
bestehenden Provisioner (Discovery → dynamische Registrierung → PKCE →
Zustimmung im sichtbaren Fenster → Token-Tausch). Für Referenzen
Schlüssel-Form (`GITHUB_PAT_TOKEN`, `OPENAI_API_KEY`, `CONNECTOR_<APP>`) führt
er zur Ausgabestelle des Anbieters und nimmt den Wert im selben Fenster
entgegen.

### D3 — Ein Store-Endpunkt, der wiederverwendet, was schon da ist

`POST /api/credentials/store` in OpenFang: authentifiziert wie die
Ausgabe-Route, nimmt `{reference, value}`, legt den Wert im Vault ab und macht
die Referenz ausgebbar. Der Name muss OpenFangs Regel erfüllen
(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`) — dieselbe, gegen die Rowboats Resolver
schon ableitet. Der Wert wird nie zurückgegeben, nie geloggt, nie in einer
Fehlermeldung wiederholt.

### D4 — Die Allowlist wächst zur Laufzeit

Heute ist sie ein Startschnappschuss; ein frisch gespeichertes Secret wäre erst
nach einem Daemon-Neustart ausgebbar — und einen Neustart darf ein Setup-Agent
nicht auslösen. Künftig: Die Umgebungsvariable bleibt die **Saat beim Start**,
und jede über D3 gespeicherte Referenz wird zusätzlich ausgebbar, persistent
über Neustarts. Die Sicherheitseigenschaft, die die Allowlist trägt — ein
API-Token darf nicht jedes Secret prägen — bleibt erhalten: Sie wächst
ausschließlich über den authentifizierten Store-Weg, nie durch Raten eines
Namens.

### D5 — Rowboat sagt die Wahrheit über den Bedarf

`requiredCredentialNames` wird durch eine Ableitung ersetzt, die dieselben
Regeln benutzt wie der Resolver zur Laufzeit: Bearer-Referenz wörtlich;
oauth-Referenz → `OAUTH_BEARER_<HOST_PFAD>`; HTTP-MCP-Server ohne jede
Deklaration → seine eigene URL nach derselben Regel; Connector-App →
`OPENAI_API_KEY` **und** `CONNECTOR_<APP>`. Die Regeln existieren bereits
(`openfang-credential-resolver.ts`, `connector-bridge-provider.ts`); sie sind
nur nicht mit der Bedarfsauskunft verbunden. Ohne diesen Punkt fragt der Agent
nach dem Falschen.

### D6 — Werkzeuge des Agenten

Vier, über einen MCP-Sidecar nach dem Muster von `marketing/claw` (Host-Prozess,
weil Container das LAN nicht erreichen):

1. `plugin_bedarf(projekt, plugin)` → welche Referenznamen fehlen, je mit Form
   (oauth/schlüssel) und Bezugsquelle.
2. `credential_speichern(referenz, wert)` → an OpenFang; der Agent sieht den
   Wert nie im Klartext zurück.
3. `plugin_installieren(projekt, plugin, komponenten)` → bestehende REST-Route.
4. `plugin_werkzeug_binden(projekt, plugin, komponente)` → neue REST-Route
   (Lücke 4).

### D7 — Was der Agent nicht darf

Keine Freigaben erteilen (das bleibt der Mensch in OpenFang), keinen Daemon
neu starten, keine bestehende Referenz überschreiben ohne ausdrückliche
Bestätigung, und **niemals** einen Wert in Protokolle, Antworten oder Supabase
schreiben.

---

## Abnahme

Ein Plugin, das heute nicht aufrufbar ist, wird in einem Durchgang nutzbar:
Agent nennt den korrekten Referenznamen, Fenster öffnet sich, nach der
Anmeldung ist die Referenz in OpenFang ausgebbar **ohne Neustart**, das Plugin
ist komponenten-genau installiert und als Werkzeug gebunden, ein Aufruf läuft
über die Freigabe bis zum Provider, und in Supabase steht der Vorgang — ohne
den Wert. Bewiesen wird gegen einen **isolierten Daemon**, nicht gegen `:4200`.

## Ausdrücklich nicht in diesem Entwurf

- **Rowboats Agentenlaufzeit durch openclaw ersetzen.** ~3.160 Zeilen mit zwei
  Einstiegspunkten, an denen `pluginBinding` zur Ausführung wird — jeder Beweis
  aus W1–W3 hängt daran. Additiv neben Rowboat ja, als Austausch nein, solange
  nicht benannt ist, was dadurch besser würde.
- **Die Umstellung des laufenden `:4200`-Daemons.** Der läuft ohne `api_key`,
  weshalb die Ausgabe dort per Design verweigert; das ist eine maschinenweite
  Entscheidung und bleibt ein eigener, freigegebener Schritt.
- **Secrets nach Supabase spiegeln.** Siehe D1.

## Voraussetzungen, die heute nicht erfüllt sind

- **OpenFang `:4200` läuft nicht** (gemessen 08.09., keine Antwort). Für die
  Entwicklung irrelevant (isolierter Daemon), für die spätere Nutzung nicht.
- **Das native openclaw-Gateway `:18793` läuft nicht** und seine Konfiguration
  zeigt laut Notiz auf `openai/gpt-4o-mini` — ein Modell ohne Guthaben. Es muss
  auf die Subscription-Lane (Shim `:8114`) gezogen werden, bevor der Agent
  denken kann. Die Datei ist UTF-8 **mit BOM**.
