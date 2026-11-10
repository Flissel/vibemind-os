# Plugin-System Teil 4: Katalog füllen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Für jeden OpenAI-Katalog-Anbieter mit einrichtbarem offiziellen Remote-MCP-Server liegt eine als „ungeprüft" gekennzeichnete VibeMind-Vorlage bereit. Die Seite „Integrationen" bleibt mit 100+ Vorlagen benutzbar. Eine Abgleichsliste begründet jeden der 180 Einträge.

**Architecture:**
- **OpenFang (Rust):** bekommt das Feld `catalog.verified`, vier neue Kategorien und ein Prüfprogramm. Das Prüfprogramm benutzt dieselben Funktionen wie das echte Einrichten (`mcp_oauth::discover`, `UrlPolicy::strict`).
- **Erzeuger (Python, vibemind-os):** liest die Recherche samt Prüfergebnis und schreibt daraus die Vorlagen und die Abgleichsliste.
- **Rowboat:** reicht `verified` und die Stichworte durch und baut Suche, Gruppen und Kennzeichen.

**Tech Stack:** Rust (openfang-extensions, openfang-api), Python 3.11 (`tomllib`, pytest), TypeScript/React + zod + vitest (Rowboat-Fork).

**Spec:** `docs/superpowers/specs/2026-11-10-integrationen-katalog-fuellen-design.md`

## Global Constraints

- **Nur offizielle Server.** Erlaubt sind Server, die der Anbieter selbst betreibt oder auf seiner eigenen Domain als offiziell nennt. Kein Community-Server, kein Aggregator (Zapier, Composio, Pipedream …).
- **Domain-Prüfung vor jeder Vorlage.** Die registrierbare Domain der Server-Adresse muss gleich der registrierbaren Domain der Quelle sein oder in der Ausnahmeliste stehen. Sonst gibt es keine Vorlage, sondern Ergebnis `domain_pruefen`.
- **Prüfprogramm ohne Spuren.** Es ruft nie `register_client` auf und sendet nie Zugangsdaten. Anbieter-Antwortinhalte erscheinen nie im Ergebnis, nur Klassen.
- **Feste Felder jeder neuen Vorlage:**
  - `read_only_tools = []`
  - `[catalog] admission = "admitted"`, `license = "proprietary-service"`, `verified = false`, `replaces_openai_plugin = "<name>"`
- **Tresor-Name für Schlüssel:** `INTEGRATION_<ID>_KEY`. `<ID>` = id in Großbuchstaben, jedes Nicht-Alphanumerische wird zu `_`.
- **id-Format:** `^[a-z0-9][a-z0-9-]{0,62}$` (Regel aus Rowboat `INTEGRATION_ID`).
- **Nie überschreiben:**
  - `integrations/github.toml` und `integrations/vercel.toml`;
  - jede Vorlage mit `verified = true` oder ohne `verified`-Feld.
- **OAuth-Scopes:** `["offline_access"]`, wenn `scopes_supported` es enthält, sonst `[]`.
- **OAuth vor Schlüssel**, wenn beides geht.
- **`verified` fehlt ⇒ true.** So bleiben GitHub und Vercel unverändert geprüft.
- **Neue Kategorien:**

  | Wert | Display (Rust) | Anzeige deutsch |
  |---|---|---|
  | `sales` | `Sales` | Vertrieb |
  | `marketing` | `Marketing` | Marketing |
  | `finance` | `Finance` | Finanzen |
  | `design` | `Design & Media` | Design & Medien |

  Nicht Passendes kommt nach `productivity`.
- **Keine Geheimnisse und keine Kundendaten in Logs, Tests oder Commits.**
- **Ausrollen nur mit Felix' OK:** OpenFang-Neubau/-Neustart, Rowboat-Fassung, Nachziehen des Betriebscheckouts `Vibemind_V1/vibemind-os`. Dieser Plan endet mit gemergtem, gepushtem Code. Nicht ausgerollt wird.
- **cargo immer mit `-j 3`.** Nie `cargo build --release` im Betriebs-Checkout `vibemind-os/openfang` (daraus läuft der Live-Daemon).
- **Kein `git stash`, nichts Fremdes verwerfen.** Der Rowboat-Checkout `C:/Users/User/ClaudeWork/wt-rowboat-vibemind` hat fremde offene Änderungen; dort nicht arbeiten.
- **Commits:** Conventional Commits, mit Abschlusszeile `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings (Planer, nicht Felix)

- **Neues Ergebnis `adresse_je_kunde`** für Recherche-Zeilen, deren `server_url` einen Platzhalter `{…}` enthält (Glean, Alation, Outlook …). Daraus entsteht keine Vorlage.
- **Anmeldeart unbekannt:** Bei `anmeldung = "unbekannt"` und 401 ohne OAuth-Erkennung lautet das Ergebnis `unklar` mit Klasse `anmeldeart_unbekannt`. Daraus entsteht keine Vorlage, denn der Header ist nicht bekannt.
- **Kategorien in der Oberfläche:** Gruppiert wird nach dem Display-String aus der API (`category`). Die deutsche Anzeige übersetzt ein Wörterbuch in Rowboat. Ein unbekannter String wird unverändert gezeigt.

## Arbeitsorte

| Repo | Worktree | Branch | Basis | Integration |
|---|---|---|---|---|
| OpenFang-Fork (`Flissel/openfang`) | `C:/Users/User/ClaudeWork/wt-of-katalog` | `claude/katalog-teil4` | `origin/main` | ff auf `main` **und** `claude/openfang-fork-reconciliation-v1` |
| vibemind-os | `C:/Users/User/ClaudeWork/wt-vos-katalog` | `claude/katalog-teil4` | `origin/master` | ff auf `master` |
| Rowboat-Fork (`Flissel/rowboat`) | `C:/Users/User/ClaudeWork/wt-rowboat-katalog` | `claude/katalog-teil4` | `origin/vibemind` | Merge auf `vibemind` |

Für cargo im OpenFang-Worktree `CARGO_TARGET_DIR=C:/Users/User/Desktop/Vibemind_V1/vibemind-os/openfang/target` setzen. Das vermeidet einen Neubau von Grund auf. Es entstehen nur Debug-/Test-Artefakte, `target/release/openfang.exe` bleibt unberührt.

Vor Beginn einen WORKBOARD-Claim `cc-katalog-teil4` eintragen und committen. Nur die eigene Zeile stagen.

## Review Focus

1. **Sonderzeichen im Anzeigenamen** (`Moody's`, `S&P Global`, Anführungszeichen) müssen gültiges TOML ergeben. → Test in Task 4.
2. **Zweiter Lauf des Erzeugers** darf nichts ändern und keine von Hand auf geprüft gesetzte Vorlage überschreiben. → Test in Task 4.
3. **Ein hängender oder kaputter Anbieter** darf den Prüflauf nicht abbrechen. Er bekommt Ergebnis `nicht_erreichbar`, der Lauf läuft weiter. → Test in Task 2 (Einordnung) und Schritt in Task 3.
4. **Suche:** „hub" findet HubSpot. Ein Stichwort wie „crm" findet die Vertriebsdienste. Leere Suche zeigt alles. → Test in Task 6.
5. **Kaputte Vorlagen-Datei** im Ordner darf die anderen nicht verhindern. Sie wird gemeldet. → Test in Task 1 (Laden des echten Ordners).

---

### Task 1: Vorlagenformat — `verified`, neue Kategorien, API-Feld

**Files:**
- Modify: `crates/openfang-extensions/src/lib.rs`
  - `IntegrationCategory` + `Display`
  - `CatalogMeta`
  - `impl IntegrationTemplate`
- Modify: `crates/openfang-api/src/routes.rs`
  - `list_available_integrations`: Feld `verified`
  - Liste installierter Integrationen (`"installed"`-Einträge): Feld `verified`
- Test: `crates/openfang-extensions/src/lib.rs` (`mod tests`)
- Test: `crates/openfang-extensions/tests/vibemind_vorlagen.rs` (neu)

**Interfaces:**
- Produces:
  - `IntegrationTemplate::is_verified(&self) -> bool`: `catalog` fehlt ⇒ true, `verified` fehlt ⇒ true.
  - `CatalogMeta.verified: bool` mit `#[serde(default = "...true")]`.
  - JSON-Feld `"verified": bool` in `GET /api/integrations/available` und in jedem Eintrag von `GET /api/integrations`.
  - Kategorien `sales`, `marketing`, `finance`, `design`.

- [ ] **Step 1: Failing tests** in `lib.rs` `mod tests`:
  - `vorlage_ohne_verified_gilt_als_geprueft`: Vorlage mit `[catalog] admission = "admitted"`, ohne `verified` ⇒ `is_verified() == true`. Ohne `[catalog]` ⇒ ebenfalls true.
  - `vorlage_mit_verified_false_ist_ungeprueft`: `verified = false` ⇒ `is_verified() == false` und `is_admitted() == true`.
  - `neue_kategorien_parsen_und_anzeigen`: Je eine Vorlage mit `category = "sales" | "marketing" | "finance" | "design"` parst. `to_string()` ergibt `"Sales"`, `"Marketing"`, `"Finance"`, `"Design & Media"`.

  Neuer Integrationstest `tests/vibemind_vorlagen.rs`, Name `alle_vibemind_vorlagen_laden`:
  - Ordner = Umgebungsvariable `VIBEMIND_INTEGRATIONS_DIR`, sonst `env!("CARGO_MANIFEST_DIR")/../../../integrations`. Fehlt der Ordner (Fork ohne vibemind-os), `return` mit `eprintln!`.
  - `IntegrationRegistry::load_template_dirs(&[dir])` aufrufen. Der Bericht darf keinen Fehler enthalten.
  - Jede geladene Vorlage mit `catalog` hat ein nicht leeres `replaces_openai_plugin`.

- [ ] **Step 2: Rot prüfen**
  `cargo test -p openfang-extensions -j 3 --lib vorlage_ neue_kategorien` ⇒ Kompilierfehler (`is_verified` fehlt, Kategorien fehlen).

- [ ] **Step 3: Implementieren**
  - Felder und Methoden wie unter Interfaces.
  - Die Kategorien als neue Enum-Varianten `Sales`, `Marketing`, `Finance`, `Design` (serde lowercase).
  - In `routes.rs` beide JSON-Antworten um `"verified": t.is_verified()` bzw. `info.template.is_verified()` ergänzen.

- [ ] **Step 4: Grün prüfen**
  - `cargo test -p openfang-extensions -j 3`
  - `cargo test -p openfang-api -j 3 --test api_integration_test`
  - `cargo check -p openfang-api -j 3`

  Alles grün.

- [ ] **Step 5: Commit**
  `feat(integrations): Vorlagen-Feld verified und Kategorien sales/marketing/finance/design`

### Task 2: Prüfprogramm für Katalog-Server

**Files:**
- Modify: `crates/openfang-extensions/src/mcp_oauth.rs`
  - Metadaten-Struct des Autorisierungsservers: `scopes_supported: Vec<String>` (`#[serde(default)]`).
  - `OAuthRecord`: `#[serde(default)] pub scopes_supported: Vec<String>`, befüllt in `discover`.
- Create: `crates/openfang-extensions/src/katalog_pruefung.rs` (reine Einordnung, keine Netzaufrufe), `pub mod` in `lib.rs`.
- Create: `crates/openfang-extensions/examples/katalog_pruefen.rs` (Netzlauf).

**Interfaces:**
- Produces:
  ```rust
  pub enum Anmeldung { Oauth, Schluessel, Unbekannt }
  pub enum Antwort { Status(u16), Umleitung, Netz }
  // Ergebnis serialisiert snake_case:
  //   einrichtbar_oauth, einrichtbar_schluessel, einrichtbar_ohne_anmeldung,
  //   braucht_registrierung, nicht_erreichbar, unklar, adresse_je_kunde
  pub struct Einordnung { pub ergebnis: Ergebnis, pub klasse: String }
  pub fn einordnen(
      url: &str,
      anmeldung: Anmeldung,
      entdeckt: Option<Result<bool /* registration_endpoint vorhanden */, String /* OAuthError::class */>>,
      initialize: Option<Antwort>,
  ) -> Einordnung
  ```
- Programm: `cargo run -p openfang-extensions --example katalog_pruefen -- <recherche.json>`.
  - Schreibt die Datei zurück. Jede Zeile bekommt `"pruefung": {"ergebnis", "klasse", "scopes_supported": [...], "datum": "YYYY-MM-DD"}`.
  - Zeilen ohne `server_url` bleiben ohne `pruefung`.

- [ ] **Step 1: Failing tests** in `katalog_pruefung.rs`, Tabelle über `einordnen`:
  - URL mit `{tenant}` ⇒ `adresse_je_kunde`. Kein Netzaufruf nötig: `entdeckt = None`, `initialize = None`.
  - `entdeckt = Some(Ok(true))` ⇒ `einrichtbar_oauth`.
  - `entdeckt = Some(Ok(false))` ⇒ `braucht_registrierung`.
  - `entdeckt = Some(Err("weiterleitung"))` und `initialize = Some(Umleitung)` ⇒ `nicht_erreichbar`, Klasse `umleitung`.
  - `entdeckt = Some(Err(_))`, `Status(200)` ⇒ `einrichtbar_ohne_anmeldung`.
  - `entdeckt = Some(Err(_))`, `Status(401)`, `Schluessel` ⇒ `einrichtbar_schluessel`. Dasselbe mit `Status(403)`.
  - `entdeckt = Some(Err(_))`, `Status(401)`, `Unbekannt` ⇒ `unklar`, Klasse `anmeldeart_unbekannt`.
  - `initialize = Some(Netz)` ⇒ `nicht_erreichbar`, Klasse `netz`.
  - `Status(404)` ⇒ `nicht_erreichbar`, Klasse `status_404`.
  - `Status(500)` ⇒ `unklar`, Klasse `status_500`.

  Dazu ein Test `discover_liest_scopes_supported` im bestehenden Testteil von `mcp_oauth.rs`, nach dem Muster der vorhandenen Discover-Tests mit lokalem Server: Die AS-Metadaten nennen `scopes_supported: ["offline_access","read"]` ⇒ `record.scopes_supported` enthält beide.

- [ ] **Step 2: Rot prüfen**
  `cargo test -p openfang-extensions -j 3 --lib katalog_pruefung discover_liest_scopes` ⇒ Kompilierfehler.

- [ ] **Step 3: Implementieren**
  - `einordnen` gemäß Tabelle.
  - Das Beispielprogramm baut:
    - den Client über `mcp_oauth::http_client(&UrlPolicy::strict())`;
    - für jede Zeile `discover(...)`;
    - nur wenn `discover` fehlschlägt, ein eigenes unauthentifiziertes `initialize` (POST, gleicher Body und `Accept`-Header wie in `discover`, Umleitung ⇒ `Antwort::Umleitung`, Sendefehler ⇒ `Netz`).
  - Ruft nie `register_client` auf.
  - Pro Zeile Fehler einfangen, der Lauf geht weiter.
  - Die Konsolenausgabe je Zeile ist nur `name ergebnis klasse`.

- [ ] **Step 4: Grün prüfen**
  `cargo test -p openfang-extensions -j 3` ⇒ alle grün, auch die bestehenden OAuth-Tests (Tresor-Format kompatibel durch `serde(default)`).

- [ ] **Step 5: Commit**
  `feat(integrations): Pruefprogramm fuer Katalog-Server (gleicher Weg wie das Einrichten)`

### Task 3: OpenFang integrieren, Prüflauf über die Recherche

**Files:**
- Create (vibemind-os Worktree): `integrations/abgleich/recherche.json`
  - Kopie von `C:/Users/User/AppData/Local/Temp/claude/ad0a820d6f43fefb/b229cb60-b946-4e65-b3ce-0d6dd07c5239/scratchpad/recherche/recherche.json`, 154 Zeilen.

- [ ] **Step 1:** OpenFang-Branch per ff integrieren:
  - `git push origin claude/katalog-teil4:main claude/katalog-teil4:claude/openfang-fork-reconciliation-v1`
  - vorher `git fetch` und prüfen, dass beide Ziele Vorfahren sind; sonst stoppen.
- [ ] **Step 2:** Recherche-Datei kopieren, dann Prüflauf:
  `cargo run -p openfang-extensions -j 3 --example katalog_pruefen -- C:/Users/User/ClaudeWork/wt-vos-katalog/integrations/abgleich/recherche.json`

  Erwartet:
  - Alle Zeilen mit `server_url` haben `pruefung`.
  - Die Konsole zeigt eine Zusammenfassung je Ergebnis.
  - Kein Abbruch.
- [ ] **Step 3:** Stichprobe von Hand:
  - Vercel-ähnliche Bekannte, z. B. `notion`, `linear`, `stripe`, sind `einrichtbar_oauth`.
  - `glean` ist `adresse_je_kunde`.

  Weicht etwas ab, Ursache klären und nicht die Datei von Hand „gerade biegen".
- [ ] **Step 4: Commit** (vibemind-os)
  `chore(integrations): Recherche und Pruefergebnis fuer den OpenAI-Katalog`
  Dazu den Submodul-Pin `openfang` auf den neuen Fork-Stand setzen.

### Task 4: Erzeuger — Vorlagen und Abgleichsliste

**Files:**
- Create: `scripts/katalog_vorlagen.py`
- Test: `scripts/tests/test_katalog_vorlagen.py`
- Create (generiert): `integrations/<name>.toml` (je Ergebnis `einrichtbar_*`), `integrations/abgleich/README.md`

**Interfaces:**
- Consumes:
  - `integrations/abgleich/recherche.json` mit `pruefung` (Task 3);
  - gepinnter Katalog `spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json`;
  - Ordner der mitgelieferten Vorlagen `openfang/crates/openfang-extensions/integrations/`.
- Produces:
  - CLI `python scripts/katalog_vorlagen.py --recherche <json> --katalog <lock.json> --mitgeliefert <dir> --ziel <integrations-dir>`;
  - Funktionen:
    - `registrierbare_domain(host: str) -> str` — letzte zwei Labels, drei bei zweistufigen Endungen `co.uk`, `com.au`, `co.jp`, `com.br`, `co.in`;
    - `domain_ok(zeile: dict) -> bool` — Server-Domain gleich Quell-Domain oder `ANBIETER_DOMAINS[name]` enthält beide;
    - `vorlage_toml(zeile: dict) -> str`;
    - `abgleich(recherche, katalog, mitgeliefert) -> list[dict]`;
  - Konstanten `KATEGORIE: dict[str, str]` (jeder Recherche-Name ⇒ Kategorie-Wert) und `ICON: dict[str, str]` (Kategorie ⇒ Emoji).

- [ ] **Step 1: Failing tests** (`pytest`, `tmp_path`):
  - `test_oauth_vorlage`:
    - Zeile mit `einrichtbar_oauth` und `scopes_supported=["offline_access"]` ⇒ TOML parst mit `tomllib`.
    - `auth.type == "oauth"`, `auth.scopes == ["offline_access"]`.
    - `catalog == {"replaces_openai_plugin": name, "license": "proprietary-service", "admission": "admitted", "verified": False}`.
    - `read_only_tools == []`, `transport.url` gleich `server_url`.
  - `test_oauth_ohne_offline_access`: `scopes_supported=["read"]` ⇒ `auth.scopes == []`.
  - `test_schluessel_vorlage`:
    - `einrichtbar_schluessel`, id `moody-s` ⇒ `auth_headers[0].credential == "INTEGRATION_MOODY_S_KEY"`.
    - `required_env[0].name` gleich, `is_secret` true, `get_url` = `schluessel_link`.
    - Kein `[auth]`.
  - `test_ohne_anmeldung_vorlage`: weder `auth` noch `auth_headers`.
  - `test_sonderzeichen_im_namen`: `anzeigename = "Moody's \"Analytics\" & S&P"` ⇒ TOML parst, `name` exakt gleich.
  - `test_domain_pruefung`:
    - `mcp.hubspot.com` + Quelle `developers.hubspot.com` ⇒ ok.
    - `mcp.example-aggregator.io` + Quelle `hubspot.com` ⇒ nicht ok, Ergebnis `domain_pruefen`, keine Datei.
    - `foo.co.uk` vs. `bar.foo.co.uk` ⇒ ok.
  - `test_nie_ueberschreiben`:
    - Zielordner mit `github.toml` (beliebiger Inhalt) und `notion.toml` mit `verified = true` ⇒ nach dem Lauf beide Dateien byte-gleich.
    - `notion` steht im Abgleich als `vorlage_bestand`.
  - `test_zweiter_lauf_aendert_nichts`: zweimal laufen ⇒ alle Dateien byte-gleich (kein Zeitstempel außer `datum` aus `pruefung`).
  - `test_mitgeliefert`:
    - Zeile `gmail` mit `braucht_registrierung` und `gmail.toml` im Mitgeliefert-Ordner ⇒ keine Datei, Abgleich `vorlage_mitgeliefert`.
    - Mit `einrichtbar_oauth` ⇒ Datei entsteht.
  - `test_abgleich_vollstaendig`:
    - Kleiner Katalog mit je einem App-, MCP- und Skills-Eintrag sowie github ⇒ jede Katalog-Zeile genau einmal.
    - Skills-Eintrag ⇒ `skills_teil_5`, github ⇒ `vorlage_bestand`.
    - Zeile ohne `server_url` ⇒ `kein_offizieller_server`.
    - Platzhalter ⇒ `adresse_je_kunde`.
  - `test_id_format`: Name `Foo_Bar` ⇒ Lauf meldet Fehler und schreibt keine Datei.
  - `test_kategorie_vollstaendig`: lädt die echte `integrations/abgleich/recherche.json` ⇒ jeder Name mit `einrichtbar_*` steht in `KATEGORIE`, jeder Wert ist einer der Kategorie-Werte.

- [ ] **Step 2: Rot prüfen**
  `python -m pytest scripts/tests/test_katalog_vorlagen.py -q` ⇒ ImportError.

- [ ] **Step 3: Implementieren**
  - Inhalt der Vorlage laut Spec Abschnitt 3.
  - `description` = `"<Anzeigename> ueber den offiziellen Remote-MCP-Server"`. ASCII-Umschrift wie in `vercel.toml`.
  - Kopfkommentar: `# Erzeugt von scripts/katalog_vorlagen.py - Quelle: <quelle> - geprueft: <datum>`.
  - TOML-Strings selbst escapen: `\`, `"`, Steuerzeichen.
  - `KATEGORIE` von Hand für alle Recherche-Namen befüllen. Das ist die Kategorie-Entscheidung aus dem Spec, Rest `productivity`.
  - `ANBIETER_DOMAINS` startet leer und wird nur für echte Fälle aus dem Lauf befüllt, mit Begründung als Kommentar.
  - Abgleichsliste als Markdown-Tabelle:
    - Spalten: Name, Sorte, Ergebnis, Grund/Klasse, Quelle, Prüfdatum;
    - darüber eine Zusammenfassung mit Zahlen je Ergebnis;
    - sortiert nach Name.

- [ ] **Step 4: Grün prüfen**
  `python -m pytest scripts/tests/test_katalog_vorlagen.py -q` ⇒ alle grün.

- [ ] **Step 5: Echter Lauf**
  Mit den Pfaden aus Interfaces. Danach:
  - Aus dem OpenFang-Worktree mit `VIBEMIND_INTEGRATIONS_DIR=C:/Users/User/ClaudeWork/wt-vos-katalog/integrations`: `cargo test -p openfang-extensions -j 3 --test vibemind_vorlagen`.
  - Ergebnis: grün.
  - `domain_pruefen`-Fälle einzeln ansehen: echter Anbieter-Domainwechsel ⇒ in `ANBIETER_DOMAINS` mit Kommentar, sonst so lassen.
- [ ] **Step 6: Commit**
  `feat(integrations): Vorlagen fuer den OpenAI-Katalog und Abgleichsliste`

### Task 5: Rowboat — Daten durchreichen

**Files (Rowboat-Worktree, unter `apps/x`):**
- Modify: `packages/shared/src/integrations.ts`
  - `VerfuegbareVorlageSchema` + `verifiziert: z.boolean()`, `stichworte: z.array(z.string())`
  - `InstallierteIntegrationSchema` + `verifiziert: z.boolean()`
- Modify: `packages/core/src/integrations/openfang-integrations.ts`
  - Interfaces `VerfuegbareVorlage` / `InstallierteIntegration` und die Abbildung (Zeile ~200–220)
- Test: `packages/core/src/integrations/openfang-integrations.test.ts`

**Interfaces:**
- Produces:
  - `VerfuegbareVorlage.verifiziert: boolean` — aus API `verified`, fehlt ⇒ `true`.
  - `VerfuegbareVorlage.stichworte: string[]` — aus `tags`, fehlt ⇒ `[]`.
  - `InstallierteIntegration.verifiziert: boolean` — gleiche Regel.

- [ ] **Step 1: Failing tests** in `openfang-integrations.test.ts`, nach dem Muster der vorhandenen Status-Tests mit gefälschten API-Antworten:
  - `verified: false` und `tags: ["crm"]` ⇒ `verfuegbar[0].verifiziert === false`, `stichworte` gleich `["crm"]`.
  - Antwort ohne `verified`/`tags` ⇒ `true` / `[]`.
  - Installierte mit `verified: false` ⇒ `installiert[0].verifiziert === false`.
- [ ] **Step 2: Rot prüfen**
  `cd apps/x/packages/core && npx vitest run src/integrations` ⇒ FAIL.
- [ ] **Step 3: Implementieren**
  Schema und Abbildung. Die Schemas bleiben `.strict()`.
- [ ] **Step 4: Grün prüfen**
  - `npx vitest run` in `packages/core` und `packages/shared`.
  - `cd apps/x/packages/core && npx tsc --noEmit -p .` (Memory: nie `npx --prefix tsc`).
- [ ] **Step 5: Commit**
  `feat(integrations): verified und Stichworte von OpenFang durchreichen`

### Task 6: Rowboat — Suche, Gruppen, Kennzeichen „ungeprüft"

**Files (unter `apps/x/apps/renderer/src/components/integrations/`):**
- Create: `verfuegbar-liste.tsx`
  - `VerfuegbarListe(props: { vorlagen: VerfuegbareVorlage[]; laufend: Set<string>; ausgegraut: boolean; onHinzufuegen: (id: string) => void })`
- Create: `verfuegbar-liste.test.tsx`
- Modify: `integrations-page.tsx`: Abschnitt „Verfügbar" durch `VerfuegbarListe` ersetzen.
- Modify: `integration-card.tsx`: Prop `verifiziert: boolean`, Kennzeichen.
- Modify: `zustand.ts` nur falls nötig, um `verifiziert` an die Karte zu geben.

**Interfaces:**
- Consumes: `VerfuegbareVorlage.verifiziert`, `.stichworte`, `.kategorie`, `InstallierteIntegration.verifiziert` (Task 5).
- Produces:
  - `KATEGORIE_ANZEIGE: Record<string, string>`:
    - `Dev Tools` ⇒ Entwicklung
    - `Productivity` ⇒ Produktivität
    - `Communication` ⇒ Kommunikation
    - `Data` ⇒ Daten
    - `Cloud` ⇒ Cloud
    - `AI & Search` ⇒ KI & Suche
    - `Sales` ⇒ Vertrieb
    - `Marketing` ⇒ Marketing
    - `Finance` ⇒ Finanzen
    - `Design & Media` ⇒ Design & Medien
  - Unbekannte Kategorie ⇒ der String selbst.

- [ ] **Step 1: Failing tests** (`verfuegbar-liste.test.tsx`, Testing Library):
  - Mit Vorlagen „HubSpot" (Sales, tags `["crm"]`, ungeprüft), „Notion" (Productivity, geprüft), „Stripe" (Finance, ungeprüft):
    - ohne Suche sind drei Gruppenüberschriften „Vertrieb", „Produktivität", „Finanzen" sichtbar, Gruppen alphabetisch nach deutscher Anzeige, Einträge alphabetisch nach Name;
    - Suche `hub` ⇒ nur HubSpot sichtbar;
    - Suche `CRM` ⇒ HubSpot sichtbar (Stichwort, Groß/klein egal);
    - Suche `xyz` ⇒ Text „Keine Vorlage passt zur Suche.";
    - HubSpot zeigt das Kennzeichen „ungeprüft", Notion nicht;
    - das Kennzeichen hat als `title` den Hinweistext aus dem Spec: „Noch von niemandem eingerichtet. Funktioniert vermutlich, alle Werkzeuge brauchen eine Freigabe.";
    - Klick auf „HubSpot hinzufügen" ruft `onHinzufuegen("hubspot")`.
  - In der vorhandenen Testdatei der Karte bzw. Seite: Eine installierte Integration mit `verifiziert: false` zeigt „ungeprüft".
- [ ] **Step 2: Rot prüfen**
  `cd apps/x/apps/renderer && npx vitest run src/components/integrations` ⇒ FAIL.
- [ ] **Step 3: Implementieren**
  - Stil der bestehenden Seite übernehmen: Tailwind-Klassen, Knöpfe wie bisher.
  - Das Suchfeld bekommt `aria-label="Vorlagen durchsuchen"`.
  - Das Kennzeichen ist eine kleine graue Pille.
- [ ] **Step 4: Grün prüfen**
  - `npx vitest run` in `apps/renderer` (ganze Suite).
  - `cd apps/x/apps/renderer && npx tsc --noEmit -p .`
- [ ] **Step 5: Commit**
  `feat(integrations): Verfuegbar-Liste mit Suche, Kategorien und Kennzeichen ungeprueft`

### Task 7: Zusammenführen, Pins, Übergabe

- [ ] **Step 1: Rowboat zusammenführen.**
  - `claude/katalog-teil4` in `vibemind` mergen; Tests aus Task 5 und 6 auf dem Merge-Ergebnis grün.
  - `git push origin vibemind`.
- [ ] **Step 2: vibemind-os zusammenführen.**
  - `claude/katalog-teil4` per ff auf `master` pushen; vorher `git fetch` und Vorfahren prüfen.
  - Spaces-Pin `spaces/rowboat` nachziehen, falls vibemind-os den Rowboat-Fork pinnt (`git ls-tree HEAD spaces/rowboat`). Pin per `rev-parse` + `cat-file -t` + `ls-remote` prüfen, nie per `--cacheinfo` blind.
- [ ] **Step 3: Äußeres Repo.** In `Vibemind_V1`:
  - Pin `vibemind-os` auf den neuen master setzen und auf `codex/vibemind-ops-baseline` committen;
  - `C:/Users/User/ClaudeWork/wt-board` per ff nachziehen;
  - beide Branches pushen;
  - `git rev-list --left-right --count master...codex/vibemind-ops-baseline` ⇒ `0 0`.
- [ ] **Step 4: Aufräumen.** Eigene Worktrees und gemergte Branches entfernen.
- [ ] **Step 5: Abschließen.**
  - WORKBOARD-Claim schließen, mit Ergebniszahlen aus der Abgleichsliste.
  - Memory `project_openfang_integrationen_kern.md` nachführen.
- [ ] **Step 6: Melden.** Felix bekommt:
  - die Zahlen je Ergebnis;
  - die `domain_pruefen`-Fälle;
  - die Bitte um OK für das Ausrollen. Das umfasst OpenFang-Neubau und -Neustart (zusammen mit dem Telegram-Token-Fix), die Rowboat-Fassung und das Nachziehen des Betriebscheckouts.
