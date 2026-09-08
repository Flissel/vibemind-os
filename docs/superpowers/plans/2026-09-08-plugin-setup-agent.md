# Plugin-Setup-Agent — Umsetzungsplan

> **Für agentische Arbeiter:** ERFORDERLICHE UNTER-FERTIGKEIT: Nutze
> superpowers:subagent-driven-development (empfohlen) oder
> superpowers:executing-plans, um diesen Plan Aufgabe für Aufgabe umzusetzen.
> Schritte benutzen Kästchen (`- [ ]`).

**Ziel:** Ein Plugin, das einen Schlüssel braucht, richtet sich über ein
sichtbares Fenster ein — der Wert landet in OpenFangs Vault, wird ohne
Daemon-Neustart ausgebbar, und das Plugin ist danach installiert und aufrufbar.

**Architektur:** OpenFang bekommt einen authentifizierten Store-Endpunkt und
eine zur Laufzeit wachsende Ausgabe-Allowlist; Rowboat lernt, den *richtigen*
Referenznamen zu nennen, und bekommt eine REST-Route fürs Werkzeug-Binden; ein
MCP-Sidecar stellt vier Werkzeuge bereit; ein openclaw-Agent treibt damit ein
sichtbares Chrome-Fenster. Supabase führt Buch — nie über Werte.

**Tech-Stack:** Rust (openfang-api/-extensions), TypeScript strict (Rowboat,
Next 15), Python (MCP-Sidecar, wie `spaces/marketing/claw`), openclaw natives
Gateway `:18793`, Supabase (Postgres).

**Spec:** `docs/superpowers/specs/2026-09-08-plugin-setup-agent.md` — Lesen ist
Pflicht; dort stehen die Entscheidungen D1–D7 und die fünf gemessenen Lücken.

## Global Constraints

- **Ein Wert verlässt nie seinen Pfad.** Kein Credential-Wert in Protokollen,
  Antworten, Fehlermeldungen, Supabase-Zeilen, Tests oder Beweisdateien. Tests
  benutzen offensichtlich erfundene Werte.
- **Nur der isolierte Daemon.** Entwicklung und Beweis gegen einen eigenen
  OpenFang auf `127.0.0.1:4273` mit eigenem `OPENFANG_HOME`. Der Daemon auf
  `:4200` und `~/.openfang/` werden nicht angefasst.
- **Fail closed.** Jede unklare Eingabe, jede nicht ableitbare Referenz, jede
  fehlende Berechtigung endet in einer Ablehnung, nie in einem Durchlassen.
- TypeScript `strict`, **kein `any`** (`unknown` + Narrowing). Rust ohne
  `unwrap()` auf Fremdeingaben.
- Kein `console.log` / `println!` mit Nutzdaten in committetem Code.
- TDD: Test zuerst, Rot gesehen, dann minimal grün.
- Conventional Commits, direkt auf `master` (dieses Repo arbeitet so), kein
  Push ohne grüne Prüfkette, kein Deploy, kein Gitlink-Bump.
- Der Agent erteilt **keine** Freigaben und startet **keinen** Daemon neu.

---

## Aufgabe 1: OpenFang nimmt Credentials entgegen und macht sie sofort ausgebbar

**Dateien:**
- Ändern: `crates/openfang-api/src/routes.rs` (neuer Handler neben
  `issue_credential`, ~Zeile 11544)
- Ändern: `crates/openfang-api/src/server.rs` (Route registrieren ~Zeile 738;
  `issuable_credentials` von `HashSet` auf einen zur Laufzeit erweiterbaren,
  persistenten Zustand ~Zeile 49)
- Ändern: `crates/openfang-extensions/src/credentials.rs` (Ablage im Vault)
- Test: `crates/openfang-api/tests/api_integration_test.rs`

**Schnittstellen:**
- Erzeugt: `POST /api/credentials/store`, Body `{"reference": "...", "value":
  "..."}`, Antwort `200 {"reference": "...", "issuable": true}` — **niemals**
  der Wert. Fehler: `400 {"error":"reference_invalid"}` (Name verletzt
  `^[A-Za-z_][A-Za-z0-9_]{0,127}$` oder Wert leer/mit NUL),
  `401` ohne gültigen Bearer, `409 {"error":"reference_exists"}` wenn die
  Referenz belegt ist und `overwrite` nicht gesetzt wurde,
  `503 {"error":"store_unavailable"}` wenn kein Vault verfügbar ist.
- Konsumiert: nichts aus späteren Aufgaben.

- [ ] **Schritt 1: Den Ablehnungs-Test zuerst.** Ein Store-Aufruf ohne Bearer
  bekommt 401; einer mit ungültigem Namen 400; einer auf eine belegte Referenz
  ohne `overwrite` 409. Der Wert taucht in keiner Antwort auf.

- [ ] **Schritt 2: Den Kern-Test zuerst.** Gespeicherte Referenz ist **sofort**
  über `/api/credentials/issue` ausgebbar — im selben Prozess, ohne Neustart —
  und liefert genau den gespeicherten Wert.

- [ ] **Schritt 3: ROT festhalten.** Beide Läufe zeigen, dass die Route fehlt.

- [ ] **Schritt 4: Store-Handler bauen.** Namen prüfen mit der vorhandenen
  `is_valid_credential_reference`; Wert ablegen über den Vault-Weg
  (`store_credential`); `write_secret_env`-Muster nur, wo kein Vault verfügbar
  ist. Antwort ohne Wert, `Cache-Control: no-store` wie bei der Ausgabe.

- [ ] **Schritt 5: Allowlist zur Laufzeit wachsen lassen.** `AppState`
  bekommt statt des starren `HashSet` einen geteilten, schreibbaren Zustand
  (`RwLock<HashSet<String>>`), beim Start aus `OPENFANG_ISSUABLE_CREDENTIALS`
  gesät. Der Store-Handler trägt die Referenz ein; die Eintragung wird
  persistiert, damit sie einen Neustart überlebt. Die Ausgabe liest weiterhin
  ausschließlich aus diesem Zustand.

- [ ] **Schritt 6: Neustart-Test.** Referenz speichern, Daemon neu starten,
  Ausgabe funktioniert weiterhin.

- [ ] **Schritt 7: GRÜN + Bau.** `cargo test -p openfang-api`, dann
  `cargo build --profile release-fast` mit `CARGO_TARGET_DIR` auf `E:` (nie
  `C:`; das Standard-Release-Profil mit fat LTO läuft hier über zwei Stunden).

- [ ] **Schritt 8: Commit.** `feat(credentials): store endpoint and a runtime
  allowlist`

---

## Aufgabe 2: Rowboat nennt den Namen, den OpenFang wirklich braucht

**Dateien:**
- Ändern: `apps/rowboat/src/application/use-cases/plugins/plugin-service.shared.ts`
  (`requiredCredentialNames`, Zeile 53–78)
- Test: `apps/rowboat/test/plugins/plugin-service-shared.test.ts`

**Schnittstellen:**
- Erzeugt: `requiredCredentialNames(entry)` liefert **die Namen, unter denen
  OpenFang die Werte kennt**, je Eintrag `{ name, kind: "bearer" | "oauth" |
  "connector", source }`.
- Konsumiert: die Ableitungsregeln aus
  `src/infrastructure/plugins/openfang-credential-resolver.ts` (oauth →
  `OAUTH_BEARER_<HOST_PFAD>`) und
  `packages/openai-plugin-runtime/src/providers/connector-bridge-provider.ts`
  (`CONNECTOR_<APP>`). **Die Regel wird geteilt, nicht kopiert** — eine zweite
  Kopie driftet.

- [ ] **Schritt 1: Test zuerst, mit den vier ausführbaren Plugins.**
  `github` → `GITHUB_PAT_TOKEN` (bearer); `linear` →
  `OAUTH_BEARER_MCP_LINEAR_APP_MCP` (oauth); `cloudflare` →
  `OAUTH_BEARER_MCP_CLOUDFLARE_COM_MCP` (oauth, obwohl der Katalog **nichts**
  deklariert); `canva` → `OPENAI_API_KEY` **und** `CONNECTOR_CANVA`
  (connector). Namen im Test aus den geteilten Ableitungsfunktionen bilden,
  nie als Literal.

- [ ] **Schritt 2: ROT festhalten.** Heute liefert die Funktion für linear die
  rohe URL, für cloudflare und canva gar nichts.

- [ ] **Schritt 3: Ableitung teilen.** Die Namensbildung an eine Stelle ziehen,
  von der Resolver, Connector-Provider und diese Funktion lesen.

- [ ] **Schritt 4: GRÜN.** `cd apps/rowboat && npx vitest run test/plugins` und
  `npx tsc --noEmit`.

- [ ] **Schritt 5: Commit.** `fix(plugins): tell the truth about which
  credentials a plugin needs`

---

## Aufgabe 3: Werkzeug-Binden über REST

**Dateien:**
- Erstellen: `apps/rowboat/app/api/v1/projects/[projectId]/plugins/[pluginName]/tools/route.ts`
- Ändern: `apps/rowboat/src/interface-adapters/http/plugins/plugin-routes.ts`
- Test: `apps/rowboat/test/plugins/plugin-routes.test.ts`

**Schnittstellen:**
- Erzeugt: `POST …/plugins/{pluginName}/tools`, Body
  `{"componentDigest": "<64-hex>"}` (`strictObject`), Antwort
  `{"toolName": "...", "added": true|false}`. Autorisierung und Fehlerform wie
  bei der bestehenden Installations-Route.
- Konsumiert: `AddPluginToolUseCase` — unverändert.

- [ ] **Schritt 1: Test zuerst.** Route bindet eine ausgewählte Komponente
  (`added: true`), ist beim zweiten Aufruf idempotent (`added: false`), lehnt
  eine nicht ausgewählte Komponente ab und weist einen fremden Aufrufer ab.

- [ ] **Schritt 2: ROT festhalten.**

- [ ] **Schritt 3: Route bauen**, streng geparst, Fehler ohne Innenleben.

- [ ] **Schritt 4: GRÜN + Typecheck.**

- [ ] **Schritt 5: Commit.** `feat(plugins): REST route for binding a plugin
  tool`

---

## Aufgabe 4: Supabase führt Buch (nie über Werte)

**Dateien:**
- Erstellen: `spaces/rowboat/setup/migrations/0001_plugin_setup.sql`
- Test: `spaces/rowboat/setup/tests/test_plugin_setup_state.py`

**Schnittstellen:**
- Erzeugt: Tabelle `plugin_setup.einrichtungen` mit `projekt_id`,
  `plugin`, `referenz_name`, `art` (bearer/oauth/connector), `status`
  (offen/eingerichtet/fehlgeschlagen), `eingerichtet_von`, `eingerichtet_am`,
  `zuletzt_erfolgreich_am`, `hinweis`. **Keine Spalte für einen Wert** — und
  ein Test, der genau das festhält.

- [ ] **Schritt 1: Test zuerst.** Das Schema hat keine Spalte, deren Name auf
  einen Geheimniswert hindeutet (`value`, `secret`, `token`, `key`), und ein
  Einfügeversuch mit einem Zusatzfeld scheitert.

- [ ] **Schritt 2: ROT festhalten.**

- [ ] **Schritt 3: Migration schreiben**, mit RLS-Regeln nach dem Muster der
  bestehenden `marketing.*`-Tabellen.

- [ ] **Schritt 4: GRÜN gegen die laufende Supabase**, Migration zweimal
  anwenden (idempotent).

- [ ] **Schritt 5: Commit.** `feat(rowboat): setup bookkeeping table, values
  deliberately absent`

---

## Aufgabe 5: Der MCP-Sidecar mit den vier Werkzeugen

**Dateien:**
- Erstellen: `spaces/rowboat/setup/werkzeuge.py`, `spaces/rowboat/setup/server.py`
- Test: `spaces/rowboat/setup/tests/test_werkzeuge.py`

**Schnittstellen:**
- Erzeugt (MCP, FastMCP auf `0.0.0.0:8131`, Muster
  `spaces/marketing/claw/server.py`):
  `plugin_bedarf(projekt, plugin)` → Liste `{name, art, quelle, vorhanden}`;
  `credential_speichern(referenz, wert)` → `{referenz, ausgebbar}`;
  `plugin_installieren(projekt, plugin, komponenten)` → Empfangsschein;
  `plugin_werkzeug_binden(projekt, plugin, komponente)` → `{toolName, added}`.
- Konsumiert: Aufgabe 1 (Store), 2 (Bedarf über Rowboats API), 3 (Bind-Route).

- [ ] **Schritt 1: Test zuerst — die Verbotsliste.** Kein Werkzeug gibt je
  einen Wert zurück; `credential_speichern` protokolliert nur den Namen;
  ein Aufruf ohne konfigurierte Ziele scheitert freundlich (fail-soft, wie
  `marketing/claw/werkzeuge.py`), statt zu stürzen.

- [ ] **Schritt 2: ROT festhalten.**

- [ ] **Schritt 3: Werkzeuge bauen**, jedes mit hartem Zeitlimit; Fehler tragen
  nie den Antwortkörper des Gegenübers.

- [ ] **Schritt 4: GRÜN**, `python -m pytest spaces/rowboat/setup/tests -q`.

- [ ] **Schritt 5: Commit.** `feat(rowboat): MCP sidecar for plugin setup`

---

## Aufgabe 6: Der openclaw-Setup-Agent und sein Fenster

**Dateien:**
- Erstellen: `spaces/rowboat/setup/agent/AGENTS.md` (Auftrag und Grenzen)
- Erstellen: `spaces/rowboat/setup/agent/anbinden.sh` (Werkzeuge anbinden,
  prüfen — Muster `marketing/claw/gateway/anbinden.sh`)
- Ändern: nichts am laufenden `:4200`

**Voraussetzungen (vor Schritt 1 prüfen, nicht annehmen):**
- Das native Gateway `:18793` läuft (Windows-Aufgabe „OpenClaw Gateway").
- `C:\Users\User\.openclaw\openclaw.json` zeigt auf die Subscription-Lane
  (Shim `:8114`), **nicht** auf `openai/gpt-4o-mini` (kein Guthaben). Datei ist
  UTF-8 **mit BOM** — BOM vor dem Parsen entfernen, beim Schreiben wieder
  setzen; vorher sichern.
- `browser.defaultProfile = "openclaw"` (verwaltetes sichtbares Chrome).

- [ ] **Schritt 1: Voraussetzungen messen** und das Ergebnis notieren, bevor
  irgendetwas gebaut wird. Fehlt eine, ist das ein Befund, keine stille
  Reparatur.

- [ ] **Schritt 2: Auftrag schreiben.** Der Agent fragt zuerst `plugin_bedarf`,
  nennt dem Menschen **den Referenznamen im Klartext**, öffnet dann das Fenster
  (OAuth-Form: Provisioner; Schlüssel-Form: Ausgabestelle des Anbieters),
  nimmt den Wert entgegen, speichert ihn, installiert, bindet, und meldet, was
  er getan hat — ohne den Wert.

- [ ] **Schritt 3: Werkzeuge anbinden** (`SHIM_EXTRA_MCP_CONFIG` in die CLI,
  `openclaw mcp reload` + `probe`), Abnahme wie in `anbinden.sh`: jedes
  Werkzeug einmal aufgerufen, Verbotsliste geprüft.

- [ ] **Schritt 4: Ein Durchgang von Hand**, mit einem erfundenen Wert:
  Fenster öffnet, Wert kommt an, Referenz ist ausgebbar, Plugin installiert.

- [ ] **Schritt 5: Commit.** `feat(rowboat): openclaw setup agent for plugins`

---

## Aufgabe 7: Der Beweis

**Dateien:**
- Erstellen: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/live-setup-agent-e2e.test.ts`
- Ändern: `spaces/rowboat/rowboat/E2E-PROOF.md` (Teil V)

- [ ] **Schritt 1: Der Testaufbau.** Isolierter Daemon `:4273` mit eigenem
  `OPENFANG_HOME` und **leerer** Allowlist, Mongo als Replica-Set, frisches
  Projekt. Opt-in über eigene Umgebungsvariable, wie Teile I–IV.

- [ ] **Schritt 2: Der Durchgang.** Referenz speichern (offensichtlich
  erfundener Wert) → **ohne Neustart** ausgebbar → Plugin komponenten-genau
  installieren → Werkzeug binden → Aufruf → Freigabe → Provider antwortet →
  Empfangsschein trägt die Freigabe-Nummer.

- [ ] **Schritt 3: Die Hygiene-Prüfung als Behauptung im Test.** Der erfundene
  Wert kommt in Empfangsscheinen, Protokollen und Supabase-Zeilen **null**
  Mal vor.

- [ ] **Schritt 4: Teil V schreiben** — was bewiesen ist und was nicht.

- [ ] **Schritt 5: Aufräumen.** Daemon beenden, Home und Schlüsseldatei
  löschen, `:4200` unberührt.

- [ ] **Schritt 6: Commit.** `test(plugins): live proof of the setup agent`

---

## Reihenfolge und Abhängigkeiten

1 → 2 → 3 können parallel begonnen werden (verschiedene Repos), 5 braucht 1–3,
6 braucht 5, 7 braucht alles. 4 ist unabhängig und kann jederzeit dazwischen.

## Was diesen Plan zum Scheitern brächte

- **Die Allowlist wird zur Laufzeit erweiterbar, aber nicht persistent.** Dann
  ist nach jedem Neustart alles weg und niemand merkt es, bis ein Aufruf
  scheitert. Deshalb Schritt 6 in Aufgabe 1.
- **Die Namensableitung wird kopiert statt geteilt.** Dann driften Resolver und
  Bedarfsauskunft auseinander, und der Agent fragt wieder nach dem Falschen —
  genau der Fehler, den dieser Plan behebt.
- **Der Agent bekommt Schreibrechte auf Freigaben.** Dann ist die
  Vier-Augen-Eigenschaft der ganzen Kette dahin.
