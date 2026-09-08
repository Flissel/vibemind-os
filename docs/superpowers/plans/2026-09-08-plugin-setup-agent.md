# Plugin-Setup-Agent — Umsetzungsplan

> **Für agentische Arbeiter:** ERFORDERLICHE UNTER-FERTIGKEIT: Nutze
> superpowers:subagent-driven-development (empfohlen) oder
> superpowers:executing-plans, um diesen Plan Aufgabe für Aufgabe umzusetzen.
> Schritte benutzen Kästchen (`- [ ]`).

**Ziel:** Ein eigenständiger Agent-Space richtet Plugins ein: Fenster auf,
Schlüssel entgegennehmen, in Supabase ablegen, beim Anbieter **verifizieren**,
von OpenFang übernehmen lassen (ohne Neustart, ohne Bruch), Supabase-Kopie
löschen, Plugin installieren und binden.

**Architektur:** `spaces/plugin-setup/` nach dem Muster von `sales-claw`
(eigenes Compose, eigene `openclaw.json`, eigener MCP-Server, eigene
Deploy-Skripte). OpenFang bekommt einen bewachten Store-Endpunkt und eine zur
Laufzeit wachsende, neustartfeste Ausgabeliste. Rowboat lernt, den richtigen
Referenznamen zu nennen, und bekommt eine REST-Route fürs Werkzeug-Binden.

**Tech-Stack:** Rust (openfang-api/-extensions), TypeScript strict (Rowboat,
Next 15), Python (MCP-Sidecar, FastMCP), openclaw natives Gateway `:18793`,
Supabase (Postgres + `supabase_vault`).

**Spec:** `docs/superpowers/specs/2026-09-08-plugin-setup-agent.md` — Lesen ist
Pflicht; dort stehen D1–D7, die fünf gemessenen Lücken und der Weg eines
Schlüssels als Diagramm.

## Global Constraints

- **Ein Wert verlässt nie seinen Pfad.** Kein Credential-Wert in Protokollen,
  Antworten, Fehlermeldungen, Zustandszeilen, Tests oder Beweisdateien. Tests
  benutzen offensichtlich erfundene Werte.
- **Nach der Übernahme gibt es genau eine Verwahrstelle.** Die Supabase-Kopie
  wird gelöscht; ein Test hält das fest.
- **Nur der isolierte Daemon.** Entwicklung und Beweis gegen einen eigenen
  OpenFang auf `127.0.0.1:4273` mit eigenem `OPENFANG_HOME`. `:4200` und
  `~/.openfang/` werden nicht angefasst.
- **Fail closed.** Jede unklare Eingabe, jede nicht ableitbare Referenz, jede
  fehlende Berechtigung endet in einer Ablehnung, nie in einem Teilzustand.
- TypeScript `strict`, **kein `any`**. Rust ohne `unwrap()` auf Fremdeingaben.
- Kein `console.log` / `println!` mit Nutzdaten in committetem Code.
- TDD: Test zuerst, Rot gesehen, dann minimal grün.
- Conventional Commits, direkt auf `master`, kein Deploy, kein Gitlink-Bump.
- Der Agent erteilt **keine** Freigaben und startet **keinen** Daemon neu.

---

## Aufgabe 1: OpenFang nimmt entgegen und sortiert ein, ohne kaputtzugehen

**Dateien:**

- Ändern: `crates/openfang-api/src/routes.rs` (Handler neben `issue_credential`)
- Ändern: `crates/openfang-api/src/server.rs` (Route ~738; `issuable_credentials` ~49)
- Ändern: `crates/openfang-extensions/src/credentials.rs` (Ablage im Vault)
- Test: `crates/openfang-api/tests/api_integration_test.rs`

**Schnittstellen:**

- Erzeugt: `POST /api/credentials/store`, Body
  `{"reference": "...", "value": "...", "overwrite": false}`, Antwort
  `200 {"reference": "...", "issuable": true}` — **nie** der Wert.
  `400 reference_invalid` · `401` ohne Bearer · `409 reference_exists` ·
  `503 store_unavailable`.

- [ ] **Schritt 1: Die vier Ablehnungen als Test.** Ohne Bearer → 401;
  ungültiger Name → 400; leerer Wert oder NUL → 400; belegte Referenz ohne
  `overwrite` → 409 **und der bestehende Wert ist danach unverändert**.

- [ ] **Schritt 2: Der Kern-Test.** Gespeicherte Referenz ist **sofort** über
  `/api/credentials/issue` ausgebbar — selber Prozess, kein Neustart — und
  liefert genau den gespeicherten Wert.

- [ ] **Schritt 3: Der Neustart-Test.** Speichern, Daemon neu starten, Ausgabe
  funktioniert weiter. (Ohne diesen Test ist die Ausgabeliste nach jedem
  Neustart leer und niemand merkt es, bis ein Aufruf scheitert.)

- [ ] **Schritt 4: ROT festhalten** für alle drei.

- [ ] **Schritt 5: Store-Handler bauen.** Name über die vorhandene
  `is_valid_credential_reference`; Wert über den Vault-Weg; Antwort ohne Wert,
  `Cache-Control: no-store` wie bei der Ausgabe.

- [ ] **Schritt 6: Ausgabeliste zur Laufzeit.** `AppState` bekommt statt des
  starren `HashSet` einen geteilten schreibbaren Zustand
  (`RwLock<HashSet<String>>`), beim Start aus `OPENFANG_ISSUABLE_CREDENTIALS`
  gesät; der Store-Handler trägt ein und persistiert. Die Ausgabe liest
  ausschließlich aus diesem Zustand.

- [ ] **Schritt 7: GRÜN + Bau.** `cargo test -p openfang-api`, dann
  `cargo build --profile release-fast` mit `CARGO_TARGET_DIR` auf `E:` (nie
  `C:`; fat LTO läuft hier über zwei Stunden).

- [ ] **Schritt 8: Commit.** `feat(credentials): store endpoint and a runtime allowlist`

---

## Aufgabe 2: Rowboat nennt den Namen, den OpenFang wirklich braucht

**Dateien:**

- Ändern: `apps/rowboat/src/application/use-cases/plugins/plugin-service.shared.ts` (`requiredCredentialNames`, 53–78)
- Test: `apps/rowboat/test/plugins/plugin-service-shared.test.ts`

**Schnittstellen:**

- Erzeugt: Einträge `{ name, kind: "bearer"|"oauth"|"connector", source }`.
- Konsumiert: die Ableitungen aus `openfang-credential-resolver.ts` und
  `connector-bridge-provider.ts` — **geteilt, nicht kopiert.**

- [ ] **Schritt 1: Test zuerst, mit den vier ausführbaren Plugins.** `github` →
  `GITHUB_PAT_TOKEN`; `linear` → `OAUTH_BEARER_MCP_LINEAR_APP_MCP`;
  `cloudflare` → `OAUTH_BEARER_MCP_CLOUDFLARE_COM_MCP` (obwohl der Katalog
  **nichts** deklariert); `canva` → `OPENAI_API_KEY` **und** `CONNECTOR_CANVA`.
  Namen im Test aus den geteilten Funktionen bilden, nie als Literal.

- [ ] **Schritt 2: ROT festhalten** (heute: rohe URL, bzw. gar nichts).

- [ ] **Schritt 3: Ableitung an eine Stelle ziehen**, von der Resolver,
  Connector-Provider und Bedarfsauskunft lesen.

- [ ] **Schritt 4: GRÜN.** `cd apps/rowboat && npx vitest run test/plugins`
  und `npx tsc --noEmit`.

- [ ] **Schritt 5: Commit.** `fix(plugins): tell the truth about which credentials a plugin needs`

---

## Aufgabe 3: Werkzeug-Binden über REST

**Dateien:**

- Erstellen: `apps/rowboat/app/api/v1/projects/[projectId]/plugins/[pluginName]/tools/route.ts`
- Ändern: `apps/rowboat/src/interface-adapters/http/plugins/plugin-routes.ts`
- Test: `apps/rowboat/test/plugins/plugin-routes.test.ts`

**Schnittstellen:**

- Erzeugt: `POST …/plugins/{pluginName}/tools`, Body
  `{"componentDigest": "<64-hex>"}` (`strictObject`) → `{"toolName", "added"}`.

- [ ] **Schritt 1: Test zuerst.** Bindet eine ausgewählte Komponente
  (`added: true`), ist beim zweiten Aufruf idempotent (`added: false`), lehnt
  eine nicht ausgewählte Komponente ab, weist einen fremden Aufrufer ab.

- [ ] **Schritt 2: ROT festhalten.**

- [ ] **Schritt 3: Route bauen**, streng geparst, Fehler ohne Innenleben.

- [ ] **Schritt 4: GRÜN + Typecheck.**

- [ ] **Schritt 5: Commit.** `feat(plugins): REST route for binding a plugin tool`

---

## Aufgabe 4: Der Eingang in Supabase — Wert verschlüsselt, Zustand daneben

**Dateien:**

- Erstellen: `spaces/plugin-setup/db/0001_plugin_setup.sql`
- Test: `spaces/plugin-setup/tests/test_eingang.py`

**Schnittstellen:**

- Erzeugt: Schema `plugin_setup` mit `einrichtungen` (`projekt_id`, `plugin`,
  `referenz_name`, `art`, `status` ∈ {entgegengenommen, verifiziert,
  uebernommen, fehlgeschlagen}, `vault_secret_id` (nullable),
  `eingerichtet_von`, `zeitpunkte`, `hinweis`). Der **Wert** liegt
  ausschließlich in `vault.secrets`, referenziert über `vault_secret_id`.
- Erzeugt: `plugin_setup.uebernommen(referenz)` — löscht das Vault-Secret,
  setzt `vault_secret_id = NULL`, Status `uebernommen`. **Der einzige Weg,
  wie ein Wert wieder verschwindet, und er ist unumkehrbar.**

- [ ] **Schritt 1: Test zuerst — die Verbotsliste.** `einrichtungen` hat keine
  Spalte, deren Name auf einen Geheimniswert deutet (`value`, `secret`,
  `token`, `key`); ein Insert mit Zusatzfeld scheitert; nach
  `uebernommen(referenz)` liefert eine Suche im Vault nichts mehr.

- [ ] **Schritt 2: ROT festhalten.**

- [ ] **Schritt 3: Migration schreiben**, RLS nach dem Muster der bestehenden
  `marketing.*`-Tabellen.

- [ ] **Schritt 4: GRÜN gegen die laufende Supabase**, Migration zweimal
  anwenden (idempotent).

- [ ] **Schritt 5: Commit.** `feat(plugin-setup): intake schema, values only in the vault`

---

## Aufgabe 5: Die Verifikation — bevor OpenFang je einen Wert sieht

**Dateien:**

- Erstellen: `spaces/plugin-setup/pruefung.py`
- Test: `spaces/plugin-setup/tests/test_pruefung.py`

**Schnittstellen:**

- Erzeugt: `pruefe(art, referenz, wert, ziel) -> {"gut": bool, "status": int}`.
  bearer → `GET https://api.github.com/user`; oauth → MCP `initialize` gegen
  die Ressource; connector → Responses-API mit `connector_id`.
- **Gibt nie den Antwortkörper zurück**, nur den Statuscode.

- [ ] **Schritt 1: Test zuerst, gegen eingespritztes `fetch`.** 200 → `gut`;
  401 → nicht gut; Zeitüberschreitung → nicht gut, kein Absturz; der Wert
  taucht in keinem Rückgabefeld und keinem Protokoll auf.

- [ ] **Schritt 2: ROT festhalten.**

- [ ] **Schritt 3: Bauen**, jede Prüfung mit hartem Zeitlimit, genau ein
  Versuch (kein Wiederholen gegen fremde Anbieter).

- [ ] **Schritt 4: GRÜN**, plus **ein** echter Lauf gegen einen Anbieter mit
  einem erfundenen Wert (erwartet 401) als Beleg, dass die Form stimmt.

- [ ] **Schritt 5: Commit.** `feat(plugin-setup): verify a credential against its provider`

---

## Aufgabe 6: Der eigenständige Space und sein MCP-Server

**Dateien:**

- Erstellen: `spaces/plugin-setup/{docker-compose.yml,server.py,werkzeuge.py,ablage.py}`
- Erstellen: `spaces/plugin-setup/config/openclaw.json` + `config/workspace/AGENTS.md`
- Erstellen: `spaces/plugin-setup/deploy/{bootstrap.sh,anbinden.sh,smoke.sh}`
- Test: `spaces/plugin-setup/tests/test_werkzeuge.py`

**Schnittstellen (MCP, FastMCP auf `0.0.0.0:8131`):**

- `plugin_bedarf(projekt, plugin)` → `[{name, art, quelle, vorhanden}]`
- `schluessel_entgegennehmen(referenz, art, wert)` → legt in Supabase ab
  (Status `entgegengenommen`), **verifiziert** (Aufgabe 5), übergibt bei Erfolg
  an OpenFang (Aufgabe 1), löscht die Kopie, Status `uebernommen`; bei
  Misserfolg Status `fehlgeschlagen` + Statuscode.
- `plugin_installieren(projekt, plugin, komponenten)` → Empfangsschein
- `plugin_werkzeug_binden(projekt, plugin, komponente)` → `{toolName, added}`

- [ ] **Schritt 1: Test zuerst — die Verbotsliste.** Kein Werkzeug gibt je
  einen Wert zurück; `schluessel_entgegennehmen` protokolliert nur den Namen;
  bei fehlgeschlagener Verifikation wird **nichts** an OpenFang übergeben und
  die Supabase-Kopie bleibt zur Fehlersuche stehen; ohne konfigurierte Ziele
  scheitert es freundlich (fail-soft wie `marketing/claw/werkzeuge.py`).

- [ ] **Schritt 2: ROT festhalten.**

- [ ] **Schritt 3: Space bauen** nach dem Muster von `sales-claw`: eigenes
  Compose, eigene `openclaw.json`, `bootstrap.sh` legt an, `anbinden.sh` bindet
  die Werkzeuge (`SHIM_EXTRA_MCP_CONFIG` in die CLI, `openclaw mcp reload` +
  `probe`), `smoke.sh` ruft jedes Werkzeug einmal und prüft die Verbotsliste.

- [ ] **Schritt 4: GRÜN**, `python -m pytest spaces/plugin-setup/tests -q`,
  danach `deploy/smoke.sh`.

- [ ] **Schritt 5: Commit.** `feat(plugin-setup): standalone space with its own MCP tools`

---

## Aufgabe 7: Der Agent und sein Fenster

**Dateien:**

- Ändern: `spaces/plugin-setup/config/workspace/AGENTS.md` (Auftrag, Grenzen)
- Ändern: `spaces/plugin-setup/config/openclaw.json` (sichtbarer Browser)

**Voraussetzungen — messen, nicht annehmen, und Befunde notieren:**

- Natives Gateway `:18793` läuft (Windows-Aufgabe „OpenClaw Gateway").
- `C:\Users\User\.openclaw\openclaw.json` zeigt auf die Subscription-Lane
  (Shim `:8114`), **nicht** auf `openai/gpt-4o-mini` (kein Guthaben). Datei ist
  UTF-8 **mit BOM** — BOM vor dem Parsen entfernen, beim Schreiben wieder
  setzen, vorher sichern.
- `browser.defaultProfile = "openclaw"` (verwaltetes sichtbares Chrome).

- [ ] **Schritt 1: Voraussetzungen messen.** Fehlt eine, ist das ein Befund,
  keine stille Reparatur.

- [ ] **Schritt 2: Auftrag schreiben.** Der Agent fragt `plugin_bedarf`, nennt
  dem Menschen **den Referenznamen im Klartext**, öffnet das Fenster (oauth:
  Provisioner; schlüssel: Ausgabestelle des Anbieters), nimmt den Wert
  entgegen, ruft `schluessel_entgegennehmen`, meldet Verifikationsergebnis und
  Übernahme — ohne den Wert. Grenzen aus D7 wörtlich.

- [ ] **Schritt 3: Ein Durchgang von Hand** mit einem erfundenen Wert: Fenster
  öffnet, Wert kommt an, Verifikation schlägt erwartungsgemäß fehl (401), es
  wird **nichts** an OpenFang übergeben — die Ablehnung ist der Beweis.

- [ ] **Schritt 4: Commit.** `feat(plugin-setup): the setup agent and its visible window`

---

## Aufgabe 8: Der Beweis

**Dateien:**

- Erstellen: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/live-setup-agent-e2e.test.ts`
- Ändern: `spaces/rowboat/rowboat/E2E-PROOF.md` (Teil V)

- [ ] **Schritt 1: Aufbau.** Isolierter Daemon `:4273`, eigenes
  `OPENFANG_HOME`, **leere** Ausgabeliste, Mongo als Replica-Set, frisches
  Projekt, Supabase-Schema aus Aufgabe 4. Opt-in über eigene Variable wie
  Teile I–IV.

- [ ] **Schritt 2: Der Durchgang.** Schlüssel entgegennehmen → in Supabase →
  verifizieren → OpenFang übernimmt → **ohne Neustart ausgebbar** →
  Supabase-Kopie ist weg → Plugin komponenten-genau installieren → Werkzeug
  binden → Aufruf → Freigabe → Provider → Empfangsschein mit Freigabe-Nummer.

- [ ] **Schritt 3: Hygiene als Behauptung im Test.** Der erfundene Wert kommt
  in Empfangsscheinen, Protokollen, Zustandszeilen und `vault.secrets`
  **null** Mal vor.

- [ ] **Schritt 4: Teil V schreiben** — was bewiesen ist und was nicht.

- [ ] **Schritt 5: Aufräumen.** Daemon beenden, Home und Schlüsseldatei
  löschen, `:4200` unberührt.

- [ ] **Schritt 6: Commit.** `test(plugin-setup): live proof of the setup chain`

---

## Reihenfolge und Abhängigkeiten

1, 2, 3 und 4 sind unabhängig (verschiedene Repos/Schichten) und können in
beliebiger Folge. 5 braucht 4. 6 braucht 1, 3, 4, 5. 7 braucht 6. 8 braucht
alles.

## Was diesen Plan zum Scheitern brächte

- **Die Ausgabeliste wächst zur Laufzeit, aber nicht persistent.** Nach jedem
  Neustart ist alles weg, und es fällt erst beim nächsten Aufruf auf. Deshalb
  Schritt 3 in Aufgabe 1.
- **Die Namensableitung wird kopiert statt geteilt.** Dann driften Resolver und
  Bedarfsauskunft auseinander, und der Agent fragt wieder nach dem Falschen.
- **Die Supabase-Kopie wird nicht gelöscht.** Dann gibt es zwei Verwahrstellen
  und zwei Widerrufsflächen — genau das, was D2 verhindert.
- **Die Verifikation wird übersprungen oder aufgeweicht.** Dann bekommt OpenFang
  Blindgänger, und „ohne kaputt zu gehen" ist nicht mehr wahr.
