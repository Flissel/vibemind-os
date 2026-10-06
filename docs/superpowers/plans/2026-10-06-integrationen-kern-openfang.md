# Integrations-Kern in OpenFang (Teilprojekt 1) — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenFang verbindet offizielle Remote-MCP-Server der Anbieter als Integrationen. Den statischen Schlüssel setzt es dabei aus dem Tresor in den HTTP-Header, ohne ihn je in die Umgebung, die API oder Logs zu geben. Integrations-Werkzeuge brauchen eine Freigabe (Standard) und sind nur für ausdrücklich berechtigte Agenten sichtbar. Pilot ist GitHub über `api.githubcopilot.com/mcp/`.

**Architecture:**
- Die Vorlagen (`IntegrationTemplate`) bekommen `auth_headers` (nur Namen), `read_only_tools` und `[catalog]`.
- Zusätzlich zu den eingebauten Vorlagen lädt OpenFang Vorlagen aus `[extensions] template_dirs`.
- Ein neues, reines Kernel-Modul `integrations.rs` übernimmt drei Aufgaben:
  - Es baut die Laufzeit-Konfiguration samt aufgelöstem Header.
  - Es entscheidet über Freigabepflicht und Sichtbarkeit.
  - Es übersetzt Verbindungsfehler in Status.
- Die drei bisherigen, doppelten Konfig-Baustellen im Kernel (Boot, Reload, Reconnect) rufen nur noch dieses Modul auf.

**Tech Stack:** Rust (Workspace `openfang`, Crates `openfang-types`, `openfang-runtime`, `openfang-extensions`, `openfang-kernel`, `openfang-api`), `rmcp` Streamable HTTP, `zeroize`, `axum`/`tokio` für Tests, TOML.

**Spec:** `vibemind-os/docs/superpowers/specs/2026-10-06-integrationen-kern-openfang-design.md` (vibemind-os `48817e4a`).

## Global Constraints

- **Schlüsselwerte nie ausgeben:** nicht in Logs, `Debug`, Fehlertexten, API-Antworten, Commits oder im Chat. Prüfen nur über „gesetzt/ungesetzt", Namen oder Kanarienwerte in Tests.
- **Für `http`/`sse`-Vorlagen** kommt kein Schlüssel per `std::env::set_var` in die Daemon-Umgebung. `to_mcp_configs` legt bei Remote-Vorlagen eine leere `env`-Liste an.
- **Fail closed:**
  - Eine fehlende Referenz verhindert den Verbindungsversuch.
  - Ein ungültiger Header bricht die Verbindung ab, statt still verworfen zu werden.
  - Ein unklassifiziertes Integrations-Werkzeug braucht eine Freigabe.
- **Builds nur mit `cargo … -j 2`.** Vorher freien RAM messen. Bei weniger als 8 GB frei: User fragen.
- **Arbeitsort:** OpenFang-Code nur im Worktree `C:/Users/User/ClaudeWork/wt-openfang-credmerge`, Arbeitslinie `claude/openfang-fork-reconciliation-v1`, Remote `Flissel/openfang`. Nie im Haupt-Checkout `C:\Users\User\Desktop\Vibemind_V1\vibemind-os\openfang`.
- **Repo-Prüfung vor jedem Commit:** `git rev-parse --show-toplevel` muss den Worktree zeigen.
- **vibemind-os-Commits** nur im Worktree `C:/Users/User/ClaudeWork/wt-os-pins`, detached auf `origin/master`, Push mit `HEAD:master`.
- **Gitlink-Pin:** nur nach `rev-parse` + `cat-file -t` + `ls-remote`.
- **Daemon `:4200`:** Neustart nur mit WORKBOARD- und Koordinations-Claim und nur über den Watchdog, sonst fehlt der Issue-Key.
- **Unverändert lassen:**
  - `/api/credentials/*`, Issue-Key-Weg, `tailscale serve`
  - Rowboat-Plugin-Laufzeit, plugin-setup
  - die 25 eingebauten Vorlagen in ihrem Verhalten
- **Keine Schlüssel-Rotation.**

## Review Focus

1. **Ein Header-Wert mit CR/LF oder Nicht-ASCII** (kaputt kopierter Token): Die Verbindung muss abbrechen, mit Status `nicht_erreichbar` und `detail` „ungültiger Header". Sie darf nie still ohne Auth verbinden. Test in Task 2 und Task 4.
2. **Fehlertext des Anbieters oder von `rmcp` enthält den Token** (Echo im Fehlerkörper): Vor dem Speichern in Health oder Log wird geschwärzt. Test in Task 2 (`scrub_secrets`) und Task 4.
3. **Ein Werkzeug, das nach einem Reconnect neu auftaucht**, braucht sofort eine Freigabe. Es gibt also keinen Zwischenspeicher, der veralten kann. Test in Task 4.
4. **Eine Vorlage im Ordner hat dieselbe `id` wie eine eingebaute, ist aber regelwidrig:** Die eingebaute bleibt aktiv, die Ordner-Vorlage wird übersprungen und protokolliert. Test in Task 3.
5. **Ein Agent mit leerer `mcp_servers`-Liste** sieht nach dem Installieren einer Integration weiterhin alle bisherigen MCP-Server, aber keine Integration. Test in Task 4 (pure Funktion). Live-Gegenprobe über den Hub in Task 8 Step 5.4.

---

## Dateistruktur

| Datei | Verantwortung |
|---|---|
| `crates/openfang-types/src/config.rs` | `AuthHeaderRef`, Feld `McpServerConfigEntry.auth_headers`, geschwärztes `Debug`, `ExtensionsConfig.template_dirs`, `is_valid_credential_reference` (geteilt) |
| `crates/openfang-runtime/src/mcp.rs` | geschwärztes `Debug` für `McpServerConfig`, harter Header-Fehler, sensitive Header, `classify_connect_error`, `scrub_secrets` |
| `crates/openfang-extensions/src/lib.rs` | Vorlagenfelder `auth_headers`, `read_only_tools`, `catalog`, neue Status samt `zustand()`/`detail()` |
| `crates/openfang-extensions/src/registry.rs` | `validate_template`, `load_template_dirs`, `to_mcp_configs` (Remote: leere `env`, `auth_headers`) |
| `crates/openfang-extensions/src/health.rs` | `report_status`, `should_reconnect` nur bei vorübergehenden Fehlern |
| `crates/openfang-kernel/src/integrations.rs` (neu) | reine Funktionen: Konfig bauen, Freigabe, Sichtbarkeit, Fehler zu Status |
| `crates/openfang-kernel/src/kernel.rs` | nutzt `integrations.rs` an Boot/Reload/Reconnect, Approval, Sichtbarkeit, lädt Ordner |
| `crates/openfang-runtime/src/kernel_handle.rs`, `tool_runner.rs` | Belege: Integrations-Aufrufe mit Freigabe-Ausgang ins Audit-Log |
| `crates/openfang-api/src/routes.rs` | Admission-Prüfung in `add_integration`, `zustand`/`detail` in Listen und Health |
| `crates/openfang-api/tests/integrations_remote_test.rs` (neu) | End-to-End gegen lokalen Test-MCP-Server: 200, 401, fehlender Schlüssel, Kanarienwert |
| `vibemind-os/integrations/github.toml` (neu) | Pilot-Vorlage |
| `agents/integrations-hub/agent.toml` (neu, openfang-Repo) | Hub-Agent mit `mcp_servers = ["github"]` |
| `openfang.vibemind.toml` (openfang-Repo) | `[extensions] template_dirs = ["../integrations"]` |

Ein Pfad in `template_dirs` wird relativ zum **Arbeitsverzeichnis des Daemons** aufgelöst. Der Watchdog startet ihn in `vibemind-os/openfang`, also zeigt `../integrations` auf `vibemind-os/integrations`. Das ist eine Ruling-Abweichung zur Spec: Dort steht `[integrations] template_dirs`, hier wird die bestehende Sektion `[extensions]` genutzt. Inhaltlich ist es dasselbe, aber ohne neue Top-Level-Sektion.

---

### Task 1: Typen — `AuthHeaderRef`, geschwärztes `Debug`, `template_dirs`

**Files:**
- Modify: `crates/openfang-types/src/config.rs` (`McpServerConfigEntry` ~1458, `ExtensionsConfig` ~694)
- Test: `crates/openfang-types/src/config.rs` (Testmodul am Dateiende)

**Interfaces:**
- Produces:
  - `pub struct AuthHeaderRef { pub name: String, pub format: String, pub credential: String }` mit `pub fn validate(&self) -> Result<(), String>`
  - `pub fn is_valid_credential_reference(reference: &str) -> bool`
  - `McpServerConfigEntry.auth_headers: Vec<AuthHeaderRef>` (`#[serde(default)]`)
  - `ExtensionsConfig.template_dirs: Vec<std::path::PathBuf>` (Standard: leer)

- [ ] **Step 1: Failing tests schreiben** (im `#[cfg(test)] mod tests` von `config.rs`)

```rust
#[test]
fn auth_header_ref_requires_exactly_one_placeholder() {
    let ok = AuthHeaderRef { name: "Authorization".into(), format: "Bearer {credential}".into(), credential: "GITHUB_PAT_TOKEN".into() };
    assert!(ok.validate().is_ok());
    let none = AuthHeaderRef { format: "Bearer".into(), ..ok.clone() };
    assert!(none.validate().is_err());
    let twice = AuthHeaderRef { format: "{credential}{credential}".into(), ..ok.clone() };
    assert!(twice.validate().is_err());
    let bad_ref = AuthHeaderRef { credential: "1BAD-REF".into(), ..ok.clone() };
    assert!(bad_ref.validate().is_err());
    let bad_name = AuthHeaderRef { name: "Auth orization".into(), ..ok };
    assert!(bad_name.validate().is_err());
}

#[test]
fn mcp_server_entry_debug_redacts_header_values() {
    let e = McpServerConfigEntry {
        name: "x".into(),
        transport: McpTransportEntry::Http { url: "https://example.com/mcp".into() },
        timeout_secs: 30,
        env: vec![],
        headers: vec!["Authorization: Bearer KANARIE-123".into()],
        auth_headers: vec![],
    };
    let dbg = format!("{e:?}");
    assert!(!dbg.contains("KANARIE-123"), "Debug leaks header value: {dbg}");
    assert!(dbg.contains("Authorization"));
}

#[test]
fn mcp_server_entry_auth_headers_default_empty() {
    let toml_src = r#"
name = "x"
timeout_secs = 30
[transport]
type = "http"
url = "https://example.com/mcp"
"#;
    let e: McpServerConfigEntry = toml::from_str(toml_src).unwrap();
    assert!(e.auth_headers.is_empty());
}

#[test]
fn extensions_template_dirs_default_empty_and_parse() {
    assert!(ExtensionsConfig::default().template_dirs.is_empty());
    let c: ExtensionsConfig = toml::from_str(r#"template_dirs = ["../integrations"]"#).unwrap();
    assert_eq!(c.template_dirs, vec![std::path::PathBuf::from("../integrations")]);
}
```

- [ ] **Step 2: Tests laufen lassen, erwartet: Kompilierfehler**

Run: `cd crates/openfang-types && cargo test -j 2 --lib auth_header_ref mcp_server_entry extensions_template_dirs`
Expected: FAIL (`AuthHeaderRef` unbekannt, Feld `auth_headers` fehlt)

- [ ] **Step 3: Implementieren**

In `config.rs` oberhalb von `McpServerConfigEntry`:

```rust
/// `^[A-Za-z_][A-Za-z0-9_]{0,127}$` — gemeinsame Form einer Credential-Referenz.
pub fn is_valid_credential_reference(reference: &str) -> bool {
    if reference.is_empty() || reference.len() > 128 {
        return false;
    }
    let mut chars = reference.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Ein HTTP-Header, dessen Wert zur Verbindungszeit aus dem Tresor kommt.
/// Traegt NUR Namen: Header-Name, Format mit genau einem `{credential}`,
/// Tresor-Referenz. Der Wert existiert nie in dieser Struktur.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthHeaderRef {
    pub name: String,
    pub format: String,
    pub credential: String,
}

impl AuthHeaderRef {
    pub fn validate(&self) -> Result<(), String> {
        let name_ok = !self.name.is_empty()
            && self.name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
        if !name_ok {
            return Err(format!("auth_headers: ungueltiger Header-Name '{}'", self.name));
        }
        if self.format.matches("{credential}").count() != 1 {
            return Err("auth_headers: format braucht genau einen Platzhalter {credential}".into());
        }
        if !is_valid_credential_reference(&self.credential) {
            return Err(format!("auth_headers: ungueltige Referenz '{}'", self.credential));
        }
        Ok(())
    }
}
```

`McpServerConfigEntry`: `#[derive(Debug …)]` durch `#[derive(Clone, Serialize, Deserialize)]` ersetzen, das Feld ergänzen und `Debug` von Hand schreiben:

```rust
    /// Header, deren Werte der Kernel beim Verbinden aus dem Tresor einsetzt.
    #[serde(default)]
    pub auth_headers: Vec<AuthHeaderRef>,
}

impl std::fmt::Debug for McpServerConfigEntry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let headers: Vec<String> = self
            .headers
            .iter()
            .map(|h| match h.split_once(':') {
                Some((n, _)) => format!("{}: <redacted>", n.trim()),
                None => "<redacted>".to_string(),
            })
            .collect();
        f.debug_struct("McpServerConfigEntry")
            .field("name", &self.name)
            .field("transport", &self.transport)
            .field("timeout_secs", &self.timeout_secs)
            .field("env", &self.env)
            .field("headers", &headers)
            .field("auth_headers", &self.auth_headers)
            .finish()
    }
}
```

`ExtensionsConfig`: Feld `pub template_dirs: Vec<std::path::PathBuf>,` mit Doku-Kommentar ergänzen („Zusaetzliche Ordner mit Integrations-Vorlagen (*.toml); relativ zum Arbeitsverzeichnis des Daemons"), im `Default` `template_dirs: Vec::new(),`.

Danach alle Struct-Literale von `McpServerConfigEntry` im Workspace um `auth_headers: Vec::new()` ergänzen:
- `grep -rn "McpServerConfigEntry {" crates`, darunter `registry.rs:202`, `kernel.rs` und Tests.
- Die Stelle in `registry.rs` füllt Task 3 richtig. Hier reicht `Vec::new()`.

- [ ] **Step 4: Tests laufen lassen, erwartet: PASS; Workspace kompiliert**

Run: `cd crates/openfang-types && cargo test -j 2 --lib` dann im Workspace-Root `cargo check -j 2 --workspace --tests`
Expected: alle `openfang-types`-Tests PASS. `cargo check` ohne Fehler.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-types/src/config.rs crates/openfang-extensions/src/registry.rs crates/openfang-kernel/src/kernel.rs
git commit -m "feat(types): AuthHeaderRef und Vorlagen-Ordner fuer Integrationen, Header-Werte im Debug geschwaerzt"
```
(Weitere geänderte Dateien aus dem Struct-Literal-Nachzug mit `git add` aufnehmen. Jede ausdrücklich nennen, nie `git add -A`.)

---

### Task 2: Laufzeit — geschwärztes `Debug`, harte Header-Fehler, Fehlerklassen, Schwärzen

**Files:**
- Modify: `crates/openfang-runtime/src/mcp.rs` (`McpServerConfig` ~24, `connect_http` ~288)
- Test: `crates/openfang-runtime/src/mcp.rs` (Testmodul)

**Interfaces:**
- Consumes: nichts aus Task 1.
- Produces:
  - `pub enum ConnectErrorClass { KeyRejected, Unreachable, InvalidHeader }` (`Debug, Clone, Copy, PartialEq, Eq`)
  - `pub fn classify_connect_error(message: &str) -> ConnectErrorClass`
  - `pub fn scrub_secrets(text: &str, secrets: &[&str]) -> String`
  - Verhalten: `connect_http` gibt `Err("MCP header invalid: <Header-Name>")` zurück, wenn ein Header nicht parsebar ist. Header-Werte sind `set_sensitive(true)`.

- [ ] **Step 1: Failing tests schreiben**

```rust
#[test]
fn mcp_server_config_debug_redacts_header_values() {
    let c = McpServerConfig {
        name: "x".into(),
        transport: McpTransport::Http { url: "https://example.com/mcp".into() },
        timeout_secs: 30,
        env: vec![],
        headers: vec!["Authorization: Bearer KANARIE-456".into()],
    };
    let dbg = format!("{c:?}");
    assert!(!dbg.contains("KANARIE-456"), "{dbg}");
    assert!(dbg.contains("Authorization"));
}

#[test]
fn classify_connect_error_maps_auth_and_network() {
    assert_eq!(classify_connect_error("MCP HTTP connection failed: HTTP status 401 Unauthorized"), ConnectErrorClass::KeyRejected);
    assert_eq!(classify_connect_error("... 403 Forbidden ..."), ConnectErrorClass::KeyRejected);
    assert_eq!(classify_connect_error("Auth required"), ConnectErrorClass::KeyRejected);
    assert_eq!(classify_connect_error("MCP header invalid: Authorization"), ConnectErrorClass::InvalidHeader);
    assert_eq!(classify_connect_error("error trying to connect: dns error"), ConnectErrorClass::Unreachable);
    assert_eq!(classify_connect_error("HTTP status 503"), ConnectErrorClass::Unreachable);
}

#[test]
fn scrub_secrets_removes_every_occurrence() {
    let out = scrub_secrets("token ABC123 echoed ABC123", &["ABC123"]);
    assert_eq!(out, "token <redacted> echoed <redacted>");
    assert_eq!(scrub_secrets("nichts", &[""]), "nichts");
}

#[tokio::test]
async fn connect_http_rejects_header_with_newline_instead_of_dropping_it() {
    let cfg = McpServerConfig {
        name: "x".into(),
        transport: McpTransport::Http { url: "http://127.0.0.1:9/mcp".into() },
        timeout_secs: 2,
        env: vec![],
        headers: vec!["Authorization: Bearer a\r\nX-Evil: 1".into()],
    };
    let err = McpConnection::connect(cfg).await.err().expect("must fail");
    assert!(err.contains("MCP header invalid: Authorization"), "{err}");
    assert!(!err.contains("Bearer a"), "{err}");
}
```

(Falls `McpConnection::connect` keinen `String`-Fehler liefert, den Test an den tatsächlichen Fehlertyp anpassen, z. B. `.to_string()`. Die Prüfungen bleiben.)

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cd crates/openfang-runtime && cargo test -j 2 --lib mcp_server_config_debug classify_connect_error scrub_secrets connect_http_rejects`
Expected: FAIL (Funktionen fehlen. Der Debug-Test findet den Kanarienwert.)

- [ ] **Step 3: Implementieren**

`McpServerConfig`: `Debug` aus dem derive nehmen und von Hand schreiben. Header werden genauso geschwärzt wie in Task 1 (`"<Name>: <redacted>"`).

In `connect_http` die Header-Schleife ersetzen:

```rust
        let mut custom_headers: HashMap<HeaderName, HeaderValue> = HashMap::new();
        for header_str in headers {
            let Some((name, value)) = header_str.split_once(':') else {
                return Err("MCP header invalid: <unnamed>".to_string());
            };
            let name = name.trim();
            let value = value.trim();
            let hn = HeaderName::from_bytes(name.as_bytes())
                .map_err(|_| format!("MCP header invalid: {name}"))?;
            let mut hv = HeaderValue::from_str(value)
                .map_err(|_| format!("MCP header invalid: {name}"))?;
            hv.set_sensitive(true);
            custom_headers.insert(hn, hv);
        }
```

Bei den Namespacing-Helfern ergänzen:

```rust
/// Grobe Einordnung eines Verbindungsfehlers fuer den Integrations-Status.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectErrorClass {
    KeyRejected,
    Unreachable,
    InvalidHeader,
}

pub fn classify_connect_error(message: &str) -> ConnectErrorClass {
    let m = message.to_ascii_lowercase();
    if m.contains("mcp header invalid") {
        return ConnectErrorClass::InvalidHeader;
    }
    if m.contains("401") || m.contains("403") || m.contains("unauthorized")
        || m.contains("forbidden") || m.contains("auth required")
    {
        return ConnectErrorClass::KeyRejected;
    }
    ConnectErrorClass::Unreachable
}

/// Ersetzt jedes Vorkommen jedes (nicht leeren) Geheimnisses durch `<redacted>`.
pub fn scrub_secrets(text: &str, secrets: &[&str]) -> String {
    let mut out = text.to_string();
    for s in secrets.iter().filter(|s| !s.is_empty()) {
        out = out.replace(s, "<redacted>");
    }
    out
}
```

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run: `cd crates/openfang-runtime && cargo test -j 2 --lib` (alle Tests des Moduls, die bestehenden Header-Tests ~530 eingeschlossen)
Expected: PASS. Ein bestehender Test, der stilles Verwerfen erwartet, bekommt einen neuen Namen und prüft künftig den harten Fehler. Das gehört in den Bericht.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-runtime/src/mcp.rs
git commit -m "feat(runtime): MCP-Header fail-closed und sensitiv, Fehlerklassen und Schwaerzen fuer Integrationen"
```

---

### Task 3: Vorlagen — Felder, Prüfung, Ordner, Remote ohne `env`, Status

**Files:**
- Modify: `crates/openfang-extensions/src/lib.rs` (`IntegrationTemplate` ~151, `IntegrationStatus` ~185)
- Modify: `crates/openfang-extensions/src/registry.rs` (`load_bundled` ~37, `to_mcp_configs` ~178)
- Modify: `crates/openfang-extensions/src/health.rs` (~53–185)
- Test: Testmodule in `lib.rs`, `registry.rs`, `health.rs`

**Interfaces:**
- Consumes: `openfang_types::config::{AuthHeaderRef, McpServerConfigEntry, McpTransportEntry}` (Task 1).
- Produces:
  - `IntegrationTemplate.auth_headers: Vec<AuthHeaderRef>`, `.read_only_tools: Vec<String>`, `.catalog: Option<CatalogMeta>`
  - `pub struct CatalogMeta { pub replaces_openai_plugin: Option<String>, pub license: Option<String>, pub admission: Admission }`
  - `pub enum Admission { Admitted, ReviewRequired }` (serde `snake_case`, Standard `Admitted`)
  - `IntegrationTemplate::is_admitted(&self) -> bool`
  - `IntegrationStatus` neu: `KeyMissing(Vec<String>)`, `KeyRejected`, `Unreachable(String)`, `NotAdmitted`
  - `IntegrationStatus::zustand(&self) -> &'static str` und `::detail(&self) -> Option<String>`
  - `pub fn validate_template(t: &IntegrationTemplate) -> Result<(), String>` (in `registry.rs`)
  - `IntegrationRegistry::load_template_dirs(&mut self, dirs: &[std::path::PathBuf]) -> TemplateDirReport`
  - `pub struct TemplateDirReport { pub loaded: usize, pub skipped: Vec<(std::path::PathBuf, String)> }`
  - `HealthMonitor::report_status(&self, id: &str, status: IntegrationStatus)`

- [ ] **Step 1: Failing tests schreiben**

In `registry.rs` `mod tests`:

```rust
const REMOTE: &str = r#"
id = "github"
name = "GitHub"
description = "GitHub ueber den offiziellen Remote-MCP-Server"
category = "devtools"
read_only_tools = ["get_me"]
[transport]
type = "http"
url = "https://api.githubcopilot.com/mcp/"
[[auth_headers]]
name = "Authorization"
format = "Bearer {credential}"
credential = "GITHUB_PAT_TOKEN"
[[required_env]]
name = "GITHUB_PAT_TOKEN"
label = "GitHub PAT"
help = "fein granuliert"
[catalog]
replaces_openai_plugin = "github"
license = "MIT"
admission = "admitted"
"#;

fn write(dir: &std::path::Path, file: &str, body: &str) {
    std::fs::write(dir.join(file), body).unwrap();
}

#[test]
fn folder_template_overrides_bundled_and_is_remote_without_env() {
    let home = tempfile::tempdir().unwrap();
    let dir = tempfile::tempdir().unwrap();
    write(dir.path(), "github.toml", REMOTE);
    let mut reg = IntegrationRegistry::new(home.path());
    reg.load_bundled();
    let report = reg.load_template_dirs(&[dir.path().to_path_buf()]);
    assert_eq!(report.loaded, 1);
    assert!(report.skipped.is_empty());
    let t = reg.get_template("github").unwrap();
    assert!(matches!(t.transport, crate::McpTransportTemplate::Http { .. }));
    reg.install(crate::InstalledIntegration {
        id: "github".into(), installed_at: chrono::Utc::now(), enabled: true,
        oauth_provider: None, config: Default::default(),
    }).unwrap();
    let cfgs = reg.to_mcp_configs();
    let gh = cfgs.iter().find(|c| c.name == "github").unwrap();
    assert!(gh.env.is_empty(), "remote template must not export env: {:?}", gh.env);
    assert_eq!(gh.auth_headers.len(), 1);
    assert_eq!(gh.auth_headers[0].credential, "GITHUB_PAT_TOKEN");
    assert!(gh.headers.is_empty());
}

#[test]
fn invalid_folder_template_is_skipped_and_bundled_stays() {
    let home = tempfile::tempdir().unwrap();
    let dir = tempfile::tempdir().unwrap();
    write(dir.path(), "github.toml", &REMOTE.replace("Bearer {credential}", "Bearer"));
    write(dir.path(), "kaputt.toml", "das ist kein toml = = =");
    let mut reg = IntegrationRegistry::new(home.path());
    reg.load_bundled();
    let report = reg.load_template_dirs(&[dir.path().to_path_buf()]);
    assert_eq!(report.loaded, 0);
    assert_eq!(report.skipped.len(), 2);
    assert!(report.skipped.iter().all(|(_, why)| !why.contains("Bearer")));
    let t = reg.get_template("github").unwrap();
    assert!(matches!(t.transport, crate::McpTransportTemplate::Stdio { .. }), "bundled stays active");
}

#[test]
fn auth_headers_on_stdio_are_rejected() {
    let mut t: crate::IntegrationTemplate = toml::from_str(REMOTE).unwrap();
    t.transport = crate::McpTransportTemplate::Stdio { command: "npx".into(), args: vec![] };
    assert!(validate_template(&t).is_err());
}

#[test]
fn bundled_templates_unchanged_and_valid() {
    let home = tempfile::tempdir().unwrap();
    let mut reg = IntegrationRegistry::new(home.path());
    assert_eq!(reg.load_bundled(), 25);
    for t in reg.list_templates() {
        assert!(validate_template(t).is_ok(), "{}", t.id);
        assert!(t.is_admitted());
        assert!(t.auth_headers.is_empty());
    }
}

#[test]
fn review_required_template_is_not_admitted() {
    let t: crate::IntegrationTemplate =
        toml::from_str(&REMOTE.replace("admission = \"admitted\"", "admission = \"review_required\"")).unwrap();
    assert!(!t.is_admitted());
}
```

In `lib.rs` `mod tests`:

```rust
#[test]
fn zustand_names_match_spec() {
    assert_eq!(IntegrationStatus::Ready.zustand(), "verbunden");
    assert_eq!(IntegrationStatus::KeyMissing(vec!["A".into()]).zustand(), "fehlt_schluessel");
    assert_eq!(IntegrationStatus::Setup.zustand(), "fehlt_schluessel");
    assert_eq!(IntegrationStatus::KeyRejected.zustand(), "schluessel_abgelehnt");
    assert_eq!(IntegrationStatus::Unreachable("x".into()).zustand(), "nicht_erreichbar");
    assert_eq!(IntegrationStatus::Error("x".into()).zustand(), "nicht_erreichbar");
    assert_eq!(IntegrationStatus::NotAdmitted.zustand(), "nicht_zugelassen");
    assert_eq!(
        IntegrationStatus::KeyMissing(vec!["A".into(), "B".into()]).detail().as_deref(),
        Some("fehlende Schluessel: A, B")
    );
}
```

In `health.rs` `mod tests`:

```rust
#[test]
fn key_rejected_and_key_missing_are_not_auto_reconnected() {
    let m = HealthMonitor::new(HealthMonitorConfig::default());
    m.register("a");
    m.report_status("a", IntegrationStatus::KeyRejected);
    assert!(!m.should_reconnect("a"));
    m.report_status("a", IntegrationStatus::KeyMissing(vec!["X".into()]));
    assert!(!m.should_reconnect("a"));
    m.report_status("a", IntegrationStatus::Unreachable("dns".into()));
    assert!(m.should_reconnect("a"));
    assert_eq!(m.get_health("a").unwrap().last_error.as_deref(), Some("dns"));
}
```

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cd crates/openfang-extensions && cargo test -j 2 --lib`
Expected: FAIL (Felder, Funktionen und Varianten fehlen). Prüfen, ob `tempfile` in `[dev-dependencies]` steht. Falls nicht: `tempfile = { workspace = true }` ergänzen, so wie in `openfang-api`.

- [ ] **Step 3: Implementieren**

`lib.rs`: nach `HealthCheckConfig`:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Admission {
    #[default]
    Admitted,
    ReviewRequired,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CatalogMeta {
    #[serde(default)]
    pub replaces_openai_plugin: Option<String>,
    #[serde(default)]
    pub license: Option<String>,
    #[serde(default)]
    pub admission: Admission,
}
```

`IntegrationTemplate` am Ende ergänzen:

```rust
    /// Header mit Werten aus dem Tresor (nur fuer http/sse).
    #[serde(default)]
    pub auth_headers: Vec<openfang_types::config::AuthHeaderRef>,
    /// Originalnamen lesender Werkzeuge; alle anderen brauchen Freigabe.
    #[serde(default)]
    pub read_only_tools: Vec<String>,
    /// Katalog-Metadaten (fehlt = zugelassen).
    #[serde(default)]
    pub catalog: Option<CatalogMeta>,
}

impl IntegrationTemplate {
    pub fn is_admitted(&self) -> bool {
        self.catalog.as_ref().map(|c| c.admission == Admission::Admitted).unwrap_or(true)
    }
}
```

`IntegrationStatus` ergänzen: `KeyMissing(Vec<String>)`, `KeyRejected`, `Unreachable(String)`, `NotAdmitted`. Dazu die `Display`-Arme `"Key missing: {list}"`, `"Key rejected"`, `"Unreachable: {msg}"` und `"Not admitted"` sowie:

```rust
impl IntegrationStatus {
    pub fn zustand(&self) -> &'static str {
        match self {
            Self::Ready => "verbunden",
            Self::Setup | Self::KeyMissing(_) => "fehlt_schluessel",
            Self::KeyRejected => "schluessel_abgelehnt",
            Self::Unreachable(_) | Self::Error(_) => "nicht_erreichbar",
            Self::NotAdmitted => "nicht_zugelassen",
            Self::Available => "verfuegbar",
            Self::Disabled => "deaktiviert",
        }
    }
    pub fn detail(&self) -> Option<String> {
        match self {
            Self::KeyMissing(names) => Some(format!("fehlende Schluessel: {}", names.join(", "))),
            Self::Unreachable(msg) | Self::Error(msg) => Some(msg.clone()),
            _ => None,
        }
    }
}
```

Danach `cargo check -j 2 --workspace` laufen lassen. Jede nicht erschöpfende `match` über `IntegrationStatus` (z. B. in `openfang-cli`/TUI) um die neuen Arme ergänzen, mit derselben Anzeige wie `Error` bzw. `Setup`.

`registry.rs`:

```rust
pub fn validate_template(t: &crate::IntegrationTemplate) -> Result<(), String> {
    if !t.auth_headers.is_empty() {
        match t.transport {
            crate::McpTransportTemplate::Http { .. } | crate::McpTransportTemplate::Sse { .. } => {}
            crate::McpTransportTemplate::Stdio { .. } => {
                return Err("auth_headers nur bei http/sse erlaubt".into());
            }
        }
    }
    for h in &t.auth_headers {
        h.validate()?;
    }
    Ok(())
}

#[derive(Debug, Default)]
pub struct TemplateDirReport {
    pub loaded: usize,
    pub skipped: Vec<(std::path::PathBuf, String)>,
}
```

In `load_bundled` jede Vorlage nach dem Parsen mit `validate_template` prüfen. Ist sie ungültig, `warn!` mit id und Grund ausgeben und nicht einfügen.

```rust
    /// Vorlagen aus Ordnern laden; gleiche id ueberschreibt die eingebaute.
    /// Ungueltige Dateien werden uebersprungen (Grund ohne Dateiinhalt).
    pub fn load_template_dirs(&mut self, dirs: &[std::path::PathBuf]) -> TemplateDirReport {
        let mut report = TemplateDirReport::default();
        for dir in dirs {
            let entries = match std::fs::read_dir(dir) {
                Ok(e) => e,
                Err(e) => {
                    report.skipped.push((dir.clone(), format!("Ordner nicht lesbar: {}", e.kind())));
                    continue;
                }
            };
            let mut paths: Vec<_> = entries
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| p.extension().and_then(|x| x.to_str()) == Some("toml"))
                .collect();
            paths.sort();
            for path in paths {
                let parsed = std::fs::read_to_string(&path)
                    .map_err(|e| format!("nicht lesbar: {}", e.kind()))
                    .and_then(|s| {
                        toml::from_str::<crate::IntegrationTemplate>(&s)
                            .map_err(|_| "TOML ungueltig".to_string())
                    })
                    .and_then(|t| validate_template(&t).map(|_| t));
                match parsed {
                    Ok(t) => {
                        self.templates.insert(t.id.clone(), t);
                        report.loaded += 1;
                    }
                    Err(why) => {
                        warn!(file = %path.display(), reason = %why, "Integrations-Vorlage uebersprungen");
                        report.skipped.push((path, why));
                    }
                }
            }
        }
        report
    }
```

`to_mcp_configs`: `env` und `auth_headers` je nach Transport setzen:

```rust
                let is_remote = !matches!(template.transport, crate::McpTransportTemplate::Stdio { .. });
                let env: Vec<String> = if is_remote {
                    Vec::new()
                } else {
                    template.required_env.iter().map(|e| e.name.clone()).collect()
                };
                Some(McpServerConfigEntry {
                    name: inst.id.clone(),
                    transport,
                    timeout_secs: 30,
                    env,
                    headers: Vec::new(),
                    auth_headers: template.auth_headers.clone(),
                })
```

`health.rs`:

```rust
impl IntegrationHealth {
    pub fn mark_status(&mut self, status: IntegrationStatus) {
        self.last_error = status.detail();
        if !matches!(status, IntegrationStatus::Ready) {
            self.connected_since = None;
            self.consecutive_failures += 1;
        }
        self.status = status;
        self.reconnecting = false;
    }
}

impl HealthMonitor {
    pub fn report_status(&self, id: &str, status: IntegrationStatus) {
        if let Some(mut entry) = self.health.get_mut(id) {
            entry.mark_status(status);
        }
    }
}
```

In `should_reconnect` die `matches!`-Bedingung ersetzen durch `matches!(entry.status, IntegrationStatus::Error(_) | IntegrationStatus::Unreachable(_))`.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run: `cd crates/openfang-extensions && cargo test -j 2 --lib`, dann im Root `cargo check -j 2 --workspace --tests`
Expected: PASS, Workspace kompiliert.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-extensions/src/lib.rs crates/openfang-extensions/src/registry.rs crates/openfang-extensions/src/health.rs crates/openfang-extensions/Cargo.toml
git commit -m "feat(extensions): Remote-Vorlagen mit auth_headers, Vorlagen-Ordner, Katalogfelder und Integrations-Status"
```
(Geänderte CLI-/TUI-Dateien aus dem `match`-Nachzug ausdrücklich mit aufnehmen.)

---

### Task 4: Kernel-Modul `integrations.rs` (reine Funktionen)

**Files:**
- Create: `crates/openfang-kernel/src/integrations.rs`
- Modify: `crates/openfang-kernel/src/lib.rs` (`pub mod integrations;`)
- Test: im neuen Modul

**Interfaces:**
- Consumes: Task 1 (`McpServerConfigEntry.auth_headers`), Task 2 (`classify_connect_error`, `scrub_secrets`, `ConnectErrorClass`), Task 3 (`IntegrationStatus`).
- Produces:
  - `pub fn to_runtime_transport(t: &McpTransportEntry) -> McpTransport`
  - `pub enum BuildError { MissingCredentials(Vec<String>), InvalidCredentialValue(String) }`
  - `pub struct BuiltConfig { pub config: McpServerConfig, pub secrets: Vec<zeroize::Zeroizing<String>> }`
  - `pub fn build_runtime_config(entry: &McpServerConfigEntry, resolve: &dyn Fn(&str) -> Option<zeroize::Zeroizing<String>>) -> Result<BuiltConfig, BuildError>`
  - `pub fn status_from_connect_error(err: &str, secrets: &[zeroize::Zeroizing<String>]) -> IntegrationStatus`
  - `pub fn integration_tool_requires_approval(tool_name: &str, server: &str, read_only_tools: &[String]) -> bool`
  - `pub fn mcp_server_visible(server: &str, allowlist: &[String], is_integration: bool) -> bool`

- [ ] **Step 1: Failing tests schreiben** (`#[cfg(test)] mod tests` in `integrations.rs`)

```rust
use super::*;
use openfang_types::config::{AuthHeaderRef, McpServerConfigEntry, McpTransportEntry};
use zeroize::Zeroizing;

fn entry() -> McpServerConfigEntry {
    McpServerConfigEntry {
        name: "github".into(),
        transport: McpTransportEntry::Http { url: "https://api.githubcopilot.com/mcp/".into() },
        timeout_secs: 30,
        env: vec![],
        headers: vec![],
        auth_headers: vec![AuthHeaderRef {
            name: "Authorization".into(),
            format: "Bearer {credential}".into(),
            credential: "GITHUB_PAT_TOKEN".into(),
        }],
    }
}

#[test]
fn builds_header_only_into_runtime_config_never_into_env() {
    let resolve = |k: &str| (k == "GITHUB_PAT_TOKEN").then(|| Zeroizing::new("KANARIE-789".to_string()));
    let built = build_runtime_config(&entry(), &resolve).unwrap();
    assert_eq!(built.config.headers, vec!["Authorization: Bearer KANARIE-789".to_string()]);
    assert!(built.config.env.is_empty());
    assert!(std::env::var("GITHUB_PAT_TOKEN").is_err());
    assert!(!format!("{:?}", built.config).contains("KANARIE-789"));
    assert_eq!(built.secrets.len(), 1);
}

#[test]
fn missing_credential_is_reported_by_name_without_connecting() {
    let resolve = |_: &str| None;
    match build_runtime_config(&entry(), &resolve) {
        Err(BuildError::MissingCredentials(names)) => assert_eq!(names, vec!["GITHUB_PAT_TOKEN".to_string()]),
        other => panic!("expected MissingCredentials, got {:?}", other.map(|_| ())),
    }
}

#[test]
fn credential_value_with_newline_is_rejected() {
    let resolve = |_: &str| Some(Zeroizing::new("abc\r\nX-Evil: 1".to_string()));
    assert!(matches!(build_runtime_config(&entry(), &resolve), Err(BuildError::InvalidCredentialValue(_))));
}

#[test]
fn status_from_errors_scrubs_secret() {
    let s = vec![Zeroizing::new("KANARIE-789".to_string())];
    assert_eq!(status_from_connect_error("HTTP 401 for KANARIE-789", &s), IntegrationStatus::KeyRejected);
    match status_from_connect_error("dns error near KANARIE-789", &s) {
        IntegrationStatus::Unreachable(msg) => assert!(!msg.contains("KANARIE-789")),
        other => panic!("{other:?}"),
    }
    match status_from_connect_error("MCP header invalid: Authorization", &s) {
        IntegrationStatus::Unreachable(msg) => assert!(msg.contains("ungueltiger Header")),
        other => panic!("{other:?}"),
    }
}

#[test]
fn approval_default_is_on_except_read_only() {
    let ro = vec!["get_me".to_string()];
    assert!(!integration_tool_requires_approval("mcp_github_get_me", "github", &ro));
    assert!(integration_tool_requires_approval("mcp_github_create_issue", "github", &ro));
    assert!(integration_tool_requires_approval("mcp_github_brand_new_tool", "github", &ro));
    assert!(integration_tool_requires_approval("mcp_github_get_me", "github", &[]));
}

#[test]
fn integrations_are_opt_in_while_plain_servers_keep_empty_means_all() {
    assert!(mcp_server_visible("rowboat", &[], false));
    assert!(!mcp_server_visible("github", &[], true));
    assert!(mcp_server_visible("github", &["github".to_string()], true));
    assert!(!mcp_server_visible("github", &["rowboat".to_string()], true));
    assert!(!mcp_server_visible("rowboat", &["github".to_string()], false));
}
```

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cd crates/openfang-kernel && cargo test -j 2 --lib integrations::`
Expected: FAIL (Modul fehlt).

- [ ] **Step 3: Implementieren** (`integrations.rs`)

```rust
//! Integrations-Kern: reine Funktionen, die der Kernel an Boot/Reload/Reconnect,
//! bei der Freigabe-Pruefung und beim Werkzeug-Filter benutzt. Kein I/O hier.

use openfang_extensions::IntegrationStatus;
use openfang_runtime::mcp::{
    classify_connect_error, format_mcp_tool_name, scrub_secrets, ConnectErrorClass, McpServerConfig,
    McpTransport,
};
use openfang_types::config::{McpServerConfigEntry, McpTransportEntry};
use zeroize::Zeroizing;

pub fn to_runtime_transport(t: &McpTransportEntry) -> McpTransport {
    match t {
        McpTransportEntry::Stdio { command, args } => McpTransport::Stdio {
            command: command.clone(),
            args: args.clone(),
        },
        McpTransportEntry::Sse { url } => McpTransport::Sse { url: url.clone() },
        McpTransportEntry::Http { url } => McpTransport::Http { url: url.clone() },
    }
}

#[derive(Debug)]
pub enum BuildError {
    /// Referenz-Namen, die der Tresor nicht kennt.
    MissingCredentials(Vec<String>),
    /// Referenz-Name, dessen Wert nicht header-tauglich ist (CR/LF/Steuerzeichen).
    InvalidCredentialValue(String),
}

pub struct BuiltConfig {
    pub config: McpServerConfig,
    /// Aufgeloeste Werte, nur zum Schwaerzen von Fehlertexten.
    pub secrets: Vec<Zeroizing<String>>,
}

pub fn build_runtime_config(
    entry: &McpServerConfigEntry,
    resolve: &dyn Fn(&str) -> Option<Zeroizing<String>>,
) -> Result<BuiltConfig, BuildError> {
    let mut headers = entry.headers.clone();
    let mut secrets = Vec::new();
    let mut missing = Vec::new();
    for h in &entry.auth_headers {
        match resolve(&h.credential) {
            None => missing.push(h.credential.clone()),
            Some(value) => {
                if value.chars().any(|c| c.is_control()) {
                    return Err(BuildError::InvalidCredentialValue(h.credential.clone()));
                }
                headers.push(format!("{}: {}", h.name, h.format.replacen("{credential}", &value, 1)));
                secrets.push(value);
            }
        }
    }
    if !missing.is_empty() {
        return Err(BuildError::MissingCredentials(missing));
    }
    Ok(BuiltConfig {
        config: McpServerConfig {
            name: entry.name.clone(),
            transport: to_runtime_transport(&entry.transport),
            timeout_secs: entry.timeout_secs,
            env: entry.env.clone(),
            headers,
        },
        secrets,
    })
}

pub fn status_from_connect_error(err: &str, secrets: &[Zeroizing<String>]) -> IntegrationStatus {
    let refs: Vec<&str> = secrets.iter().map(|s| s.as_str()).collect();
    let clean = scrub_secrets(err, &refs);
    match classify_connect_error(&clean) {
        ConnectErrorClass::KeyRejected => IntegrationStatus::KeyRejected,
        ConnectErrorClass::InvalidHeader => IntegrationStatus::Unreachable("ungueltiger Header".into()),
        ConnectErrorClass::Unreachable => IntegrationStatus::Unreachable(clean),
    }
}

pub fn integration_tool_requires_approval(tool_name: &str, server: &str, read_only_tools: &[String]) -> bool {
    !read_only_tools.iter().any(|ro| format_mcp_tool_name(server, ro) == tool_name)
}

pub fn mcp_server_visible(server: &str, allowlist: &[String], is_integration: bool) -> bool {
    let listed = allowlist.iter().any(|a| a == server);
    if is_integration { listed } else { allowlist.is_empty() || listed }
}
```

In `crates/openfang-kernel/src/lib.rs`: `pub mod integrations;` ergänzen.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run: `cd crates/openfang-kernel && cargo test -j 2 --lib integrations::`
Expected: PASS (6 Tests).

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-kernel/src/integrations.rs crates/openfang-kernel/src/lib.rs
git commit -m "feat(kernel): reines Integrations-Modul fuer Header aus dem Tresor, Freigabe und Sichtbarkeit"
```

---

### Task 5: Kernel verdrahten — Boot/Reload/Reconnect, Freigabe, Sichtbarkeit, Ordner

**Files:**
- Modify: `crates/openfang-kernel/src/kernel.rs`. Betroffen sind:
  - Registry-Aufbau ~990
  - `connect_mcp_servers` ~5990
  - `reload_extension_mcps` ~6068
  - `reconnect_extension_mcp` ~6188
  - `available_tools_with_registry` ~6309/6457
  - `KernelHandle::requires_approval` ~7821
- Test: `crates/openfang-kernel/src/kernel.rs` (bestehendes Testmodul), eng gefasst

**Interfaces:**
- Consumes: alles aus Task 4. Dazu `IntegrationRegistry::load_template_dirs` und `HealthMonitor::report_status` (Task 3).
- Produces:
  - `OpenFangKernel::connect_one_mcp(self: &Arc<Self>, server_config: &McpServerConfigEntry) -> Result<usize, String>`. Einziger Verbindungsweg für Boot, Reload und Reconnect.
  - `OpenFangKernel::integration_requires_approval(&self, tool_name: &str) -> bool`

- [ ] **Step 1: Failing test schreiben**

Im Testmodul von `kernel.rs`, nach dem Muster der bestehenden Kernel-Tests (`boot_with_config` mit `tempfile`-Home):

```rust
#[tokio::test(flavor = "multi_thread")]
async fn integration_with_missing_key_is_marked_and_not_connected() {
    let tmp = tempfile::tempdir().unwrap();
    let dir = tmp.path().join("integrations");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("probe.toml"), r#"
id = "probe"
name = "Probe"
description = "Testvorlage"
category = "devtools"
[transport]
type = "http"
url = "http://127.0.0.1:9/mcp"
[[auth_headers]]
name = "Authorization"
format = "Bearer {credential}"
credential = "PROBE_KEY_NOT_SET_ANYWHERE"
"#).unwrap();
    std::fs::write(tmp.path().join("integrations.toml"),
        "[[installed]]\nid = \"probe\"\ninstalled_at = \"2026-10-06T00:00:00Z\"\nenabled = true\n").unwrap();
    let mut config = KernelConfig { home_dir: tmp.path().to_path_buf(), data_dir: tmp.path().join("data"), ..KernelConfig::default() };
    config.extensions.template_dirs = vec![dir];
    config.runtime.tool_only = true;
    let kernel = Arc::new(OpenFangKernel::boot_with_config(config).unwrap());
    kernel.set_self_handle();
    kernel.connect_mcp_servers().await;
    let h = kernel.extension_health.get_health("probe").expect("registered");
    assert_eq!(h.status.zustand(), "fehlt_schluessel");
    assert_eq!(h.last_error.as_deref(), Some("fehlende Schluessel: PROBE_KEY_NOT_SET_ANYWHERE"));
    assert!(std::env::var("PROBE_KEY_NOT_SET_ANYWHERE").is_err());
    kernel.shutdown();
}
```

(Ist `connect_mcp_servers` privat, läuft der Test im selben Modul und darf sie aufrufen. Heißt das Boot-Feld anders, z. B. `runtime.tool_only`, nach den vorhandenen Tests ~9127 ausrichten.)

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cd crates/openfang-kernel && cargo test -j 2 --lib integration_with_missing_key`
Expected: FAIL. Heute wird die Vorlage gar nicht geladen, oder es wird verbunden, und der Status ist `nicht_erreichbar`.

- [ ] **Step 3: Implementieren**

1. **Registry-Aufbau (~992):** Nach `load_bundled()` die Ordner laden. Beim Überspringen nur Dateiname und Grund loggen.
   ```rust
   let dir_report = extension_registry.load_template_dirs(&config.extensions.template_dirs);
   for (path, why) in &dir_report.skipped {
       warn!(file = %path.display(), reason = %why, "Integrations-Vorlage uebersprungen");
   }
   ```
2. **Einziger Verbindungsweg:** Neue Methode, die die drei Kopien ersetzt.
   ```rust
   async fn connect_one_mcp(
       self: &Arc<Self>,
       server_config: &openfang_types::config::McpServerConfigEntry,
   ) -> Result<usize, String> {
       use crate::integrations::{build_runtime_config, status_from_connect_error, BuildError};
       // Nur stdio behaelt den alten env-Weg (unveraendert fuer die 25 eingebauten Vorlagen).
       for var_name in &server_config.env {
           if std::env::var(var_name).is_err() {
               if let Some(val) = self.resolve_credential(var_name) {
                   std::env::set_var(var_name, &val);
               }
           }
       }
       let resolve = |k: &str| self.resolve_credential(k).map(zeroize::Zeroizing::new);
       let built = match build_runtime_config(server_config, &resolve) {
           Ok(b) => b,
           Err(BuildError::MissingCredentials(names)) => {
               self.extension_health.report_status(
                   &server_config.name,
                   openfang_extensions::IntegrationStatus::KeyMissing(names.clone()),
               );
               return Err(format!("fehlende Schluessel: {}", names.join(", ")));
           }
           Err(BuildError::InvalidCredentialValue(name)) => {
               self.extension_health.report_status(
                   &server_config.name,
                   openfang_extensions::IntegrationStatus::Unreachable(format!("ungueltiger Wert fuer {name}")),
               );
               return Err(format!("ungueltiger Wert fuer {name}"));
           }
       };
       match openfang_runtime::mcp::McpConnection::connect(built.config).await {
           Ok(conn) => {
               let tool_count = conn.tools().len();
               self.extension_health.report_ok(&server_config.name, tool_count);
               self.install_mcp_connection(conn).await;
               Ok(tool_count)
           }
           Err(e) => {
               let status = status_from_connect_error(&e.to_string(), &built.secrets);
               let shown = status.detail().unwrap_or_else(|| status.to_string());
               self.extension_health.report_status(&server_config.name, status);
               Err(shown)
           }
       }
   }
   ```
   (`resolve_credential` hat die Rückgabe `Option<String>`. Weicht die Signatur ab, wird nur die Closure angepasst.)
3. **Die drei Aufrufer umstellen:**
   - `connect_mcp_servers`, die Schleife in `reload_extension_mcps` und `reconnect_extension_mcp` rufen nur noch `self.connect_one_mcp(server_config).await`.
   - Ihre bisherigen `info!`/`warn!` behalten sie. Für den Fehler nehmen sie den von `connect_one_mcp` gelieferten, geschwärzten Text, nie `e` direkt.
   - In `reload_extension_mcps` wird vor Schritt 2 `registry.load_template_dirs(&self.config.extensions.template_dirs)` aufgerufen.
4. **Freigabe (~7821):**
   ```rust
   fn requires_approval(&self, tool_name: &str) -> bool {
       self.approval_manager.requires_approval(tool_name) || self.integration_requires_approval(tool_name)
   }
   ```
   Dazu in `impl OpenFangKernel`:
   ```rust
   pub fn integration_requires_approval(&self, tool_name: &str) -> bool {
       let registry = self.extension_registry.read().unwrap_or_else(|e| e.into_inner());
       let installed: Vec<String> = registry.list_all_info().into_iter()
           .filter(|i| i.installed.is_some()).map(|i| i.template.id).collect();
       let server = self.mcp_tool_origins.lock().ok()
           .and_then(|o| o.get(tool_name).and_then(|s| (s.len() == 1).then(|| s.iter().next().cloned()).flatten()))
           .or_else(|| openfang_runtime::mcp::extract_mcp_server_from_known(tool_name, &installed).map(str::to_string));
       let Some(server) = server else { return false };
       if !installed.iter().any(|i| i == &server) { return false; }
       let read_only = registry.get_template(&server).map(|t| t.read_only_tools.clone()).unwrap_or_default();
       crate::integrations::integration_tool_requires_approval(tool_name, &server, &read_only)
   }
   ```
   (Signatur von `extract_mcp_server_from_known` in `mcp.rs:~370` prüfen und die Argumentform daran anpassen.)
5. **Sichtbarkeit (`available_tools_with_registry`, ~6457):**
   - Vor den MCP-Locks die installierten Integrations-ids aus `self.extension_registry` holen, mit `let integration_ids: std::collections::HashSet<String> = …`.
   - Die Bedingung `if !mcp_allowlist.is_empty() && !mcp_allowlist.iter().any(...)` ersetzen durch:
   ```rust
   if !crate::integrations::mcp_server_visible(
       actual_server,
       &mcp_allowlist,
       integration_ids.contains(actual_server.as_str()),
   ) {
       continue;
   }
   ```
   (Typ von `mcp_allowlist` beachten. Ist es kein `Vec<String>`, mit `.as_slice()` bzw. `.iter().cloned().collect::<Vec<_>>()` angleichen.)

- [ ] **Step 4: Laufen lassen, erwartet: PASS; Kernel-Suite grün**

Run: `cd crates/openfang-kernel && cargo test -j 2 --lib`
Expected: Der neue Test ist PASS. Alle bisher grünen Kernel-Tests bleiben grün. Vorher/Nachher-Zahlen gehören in den Bericht.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-kernel/src/kernel.rs
git commit -m "feat(kernel): Integrationen verbinden mit Tresor-Header, Freigabe-Standard und Opt-in-Sichtbarkeit"
```

---

### Task 6: Belege — jeder Integrations-Aufruf im Audit-Log

**Files:**
- Modify: `crates/openfang-runtime/src/kernel_handle.rs` (Trait `KernelHandle`, ~182)
- Modify: `crates/openfang-runtime/src/tool_runner.rs` (Freigabe-Block ~170, MCP-Dispatch ~494)
- Modify: `crates/openfang-kernel/src/kernel.rs` (`impl KernelHandle for OpenFangKernel`, ~7821)
- Test: `crates/openfang-runtime/src/tool_runner.rs` (Testmodul)

**Interfaces:**
- Consumes: `OpenFangKernel::integration_requires_approval` (Task 5). Dazu `installed`-Ermittlung nach demselben Muster.
- Produces:
  - Trait-Methode `fn is_integration_tool(&self, tool_name: &str) -> bool { false }`, Standard-Implementierung
  - Trait-Methode `fn record_integration_call(&self, agent_id: &str, tool_name: &str, approval: &str, outcome: &str) {}`, Standard-Implementierung
  - Audit-Eintrag: `AuditAction::ToolInvoke`, detail `integration_tool=<name> approval=<read_only|freigabe_erteilt|freigabe_abgelehnt>`, outcome `ok|fehler|denied`

Ruling-Abweichung zur Spec §4.7: Statt der Freigabe-**ID** wird der Freigabe-**Ausgang** protokolliert. `request_approval` liefert heute nur `bool`. Die Zuordnung zur Freigabe erfolgt über Agent, Werkzeug und Zeitstempel im Freigabe-Log. Kostet, falls falsch: Die ID nachzurüsten heißt, die Rückgabe von `request_approval` zu ändern.

- [ ] **Step 1: Failing test schreiben** (`tool_runner.rs` Testmodul)

Muster für einen Fake: `FakeKernel` in `crates/openfang-runtime/src/runtime_execution.rs:345`. Die Pflichtmethoden des Traits werden genauso implementiert wie dort, und zwar in einem neuen `RecordingKernel` im Testmodul von `tool_runner.rs`. Dazu kommen die vier Methoden unten. Die 11 `None` hinter `caller_agent_id` sind in dieser Reihenfolge: skill_registry, mcp_connections, web_ctx, browser_ctx, allowed_env_vars, workspace_root, media_engine, exec_policy, tts_engine, docker_config und process_manager (Signatur `tool_runner.rs:112`).

```rust
struct RecordingKernel {
    calls: std::sync::Mutex<Vec<(String, String, String)>>,
}
// in impl KernelHandle for RecordingKernel:
//   fn requires_approval(&self, _t: &str) -> bool { true }
//   async fn request_approval(&self, _a: &str, _t: &str, _s: &str) -> Result<bool, String> { Ok(false) }
//   fn is_integration_tool(&self, t: &str) -> bool { t.starts_with("mcp_github_") }
//   fn record_integration_call(&self, _a: &str, t: &str, approval: &str, outcome: &str) {
//       self.calls.lock().unwrap().push((t.into(), approval.into(), outcome.into()));
//   }

#[tokio::test]
async fn denied_integration_call_is_audited_as_denied() {
    let rec = std::sync::Arc::new(RecordingKernel { calls: Default::default() });
    let k: std::sync::Arc<dyn KernelHandle> = rec.clone();
    let result = execute_tool(
        "test-id",
        "mcp_github_create_issue",
        &serde_json::json!({"title": "x"}),
        Some(&k),        // kernel
        None,            // allowed_tools
        Some("agent-1"), // caller_agent_id
        None, None, None, None, None, None, None, None, None, None, None,
    )
    .await;
    let k = rec;
    assert!(result.is_error);
    assert_eq!(
        k.calls.lock().unwrap().as_slice(),
        &[("mcp_github_create_issue".to_string(), "freigabe_abgelehnt".to_string(), "denied".to_string())]
    );
}
```

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cd crates/openfang-runtime && cargo test -j 2 --lib denied_integration_call_is_audited`
Expected: FAIL (Trait-Methoden fehlen).

- [ ] **Step 3: Implementieren**

- `kernel_handle.rs`: die beiden Default-Methoden oben ergänzen, mit Doku-Kommentar.
- `tool_runner.rs`:
  - Vor dem Freigabe-Block `let mut approval_mode = "read_only";` setzen.
  - Bei `Ok(true)` gilt `approval_mode = "freigabe_erteilt"`.
  - Bei `Ok(false)` vor dem `return` ergänzen:
    ```rust
    if kh.is_integration_tool(tool_name) {
        kh.record_integration_call(agent_id_str, tool_name, "freigabe_abgelehnt", "denied");
    }
    ```
  - Im MCP-Zweig (`Fallback 1`) das Ergebnis von `conn.call_tool` in `let r = …;` binden. Danach, sofern `kernel` gesetzt ist und `kh.is_integration_tool(other)` gilt:
    ```rust
    kh.record_integration_call(caller_agent_id.unwrap_or("unknown"), other, approval_mode, if r.is_ok() { "ok" } else { "fehler" });
    ```
    `approval_mode` und `kh` müssen dort erreichbar sein. Falls nötig, die Variablen eine Ebene höher ziehen. **Nie** Eingabe- oder Ausgabeinhalte mitprotokollieren.
- `kernel.rs`, in `impl KernelHandle for OpenFangKernel`:
  ```rust
  fn is_integration_tool(&self, tool_name: &str) -> bool {
      let registry = self.extension_registry.read().unwrap_or_else(|e| e.into_inner());
      let installed: Vec<String> = registry.list_all_info().into_iter()
          .filter(|i| i.installed.is_some()).map(|i| i.template.id).collect();
      let refs: Vec<&str> = installed.iter().map(|s| s.as_str()).collect();
      openfang_runtime::mcp::extract_mcp_server_from_known(tool_name, &refs).is_some()
  }

  fn record_integration_call(&self, agent_id: &str, tool_name: &str, approval: &str, outcome: &str) {
      self.audit_log.record(
          agent_id.to_string(),
          openfang_runtime::audit::AuditAction::ToolInvoke,
          format!("integration_tool={tool_name} approval={approval}"),
          outcome,
      );
  }
  ```

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run: `cd crates/openfang-runtime && cargo test -j 2 --lib`, dann `cargo check -j 2 --workspace --tests`
Expected: PASS, Workspace kompiliert.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-runtime/src/kernel_handle.rs crates/openfang-runtime/src/tool_runner.rs crates/openfang-kernel/src/kernel.rs
git commit -m "feat(audit): jeder Integrations-Aufruf mit Freigabe-Ausgang im Audit-Log, ohne Inhalte"
```

---

### Task 7: API — Zulassung, `zustand`/`detail`, End-to-End gegen Test-MCP-Server

**Files:**
- Modify: `crates/openfang-api/src/routes.rs` (`add_integration` ~9553, `list_integrations` ~9476, `integrations_health` ~9698)
- Create: `crates/openfang-api/tests/integrations_remote_test.rs`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `POST /api/integrations/add` antwortet bei `admission = review_required` mit `409 {"error":"integration_not_admitted"}`.
  - Antwort `201` enthält `"zustand"` und `"detail"`.
  - `GET /api/integrations` und `GET /api/integrations/health` enthalten je Eintrag `"zustand"`, `"detail"` und `"auth_headers": [{"name","credential"}]`. Das sind nur Namen.

- [ ] **Step 1: Failing tests schreiben** (`integrations_remote_test.rs`)

Der Test baut einen eigenen Kernel nach dem Muster von `start_tool_only_test_server` (siehe `api_integration_test.rs:48`). Dazu kommen ein Vorlagen-Ordner und ein Mini-MCP-Server auf `127.0.0.1:0`. Der Mini-Server antwortet bei `Authorization: Bearer GUT` auf `initialize`/`tools/list` und sonst mit `401`.

```rust
use axum::{http::{HeaderMap, StatusCode}, routing::post, Json, Router};
use openfang_api::routes::{self, AppState};
use openfang_kernel::OpenFangKernel;
use openfang_types::config::KernelConfig;
use std::{sync::Arc, time::Instant};

async fn mini_mcp(headers: HeaderMap, Json(req): Json<serde_json::Value>) -> (StatusCode, Json<serde_json::Value>) {
    if headers.get("authorization").and_then(|v| v.to_str().ok()) != Some("Bearer GUT") {
        return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error":"unauthorized"})));
    }
    let id = req.get("id").cloned().unwrap_or(serde_json::Value::Null);
    let result = match req["method"].as_str().unwrap_or("") {
        "initialize" => serde_json::json!({"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"mini","version":"1"}}),
        "tools/list" => serde_json::json!({"tools":[
            {"name":"get_me","description":"wer bin ich","inputSchema":{"type":"object"}},
            {"name":"create_issue","description":"schreibt","inputSchema":{"type":"object"}}]}),
        _ => serde_json::json!({}),
    };
    (StatusCode::OK, Json(serde_json::json!({"jsonrpc":"2.0","id":id,"result":result})))
}

async fn start_mini_mcp() -> String {
    let app = Router::new().route("/mcp", post(mini_mcp));
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    format!("http://{addr}/mcp")
}

fn template(url: &str, credential: &str, admission: &str) -> String {
    format!(r#"
id = "probe"
name = "Probe"
description = "Testvorlage"
category = "devtools"
read_only_tools = ["get_me"]
[transport]
type = "http"
url = "{url}"
[[auth_headers]]
name = "Authorization"
format = "Bearer {{credential}}"
credential = "{credential}"
[catalog]
admission = "{admission}"
"#)
}

struct Harness { base: String, state: Arc<AppState>, _tmp: tempfile::TempDir }

async fn harness(template_body: String) -> Harness {
    let tmp = tempfile::tempdir().unwrap();
    let dir = tmp.path().join("integrations");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("probe.toml"), template_body).unwrap();
    let mut config = KernelConfig { home_dir: tmp.path().to_path_buf(), data_dir: tmp.path().join("data"), ..KernelConfig::default() };
    config.extensions.template_dirs = vec![dir];
    config.runtime.tool_only = true;
    let kernel = Arc::new(OpenFangKernel::boot_with_config(config).unwrap());
    kernel.set_self_handle();
    // AppState exakt wie in api_integration_test.rs:start_tool_only_test_server aufbauen.
    let state = Arc::new(AppState {
        kernel,
        started_at: Instant::now(),
        peer_registry: None,
        bridge_manager: tokio::sync::Mutex::new(None),
        channels_config: tokio::sync::RwLock::new(Default::default()),
        shutdown_notify: Arc::new(tokio::sync::Notify::new()),
        clawhub_cache: dashmap::DashMap::new(),
        provider_probe_cache: openfang_runtime::provider_health::ProbeCache::new(),
        budget_config: Arc::new(tokio::sync::RwLock::new(Default::default())),
        issuable_credentials: Default::default(),
        store_credential_lock: tokio::sync::Mutex::new(()),
    });
    let app = Router::new()
        .route("/api/integrations", axum::routing::get(routes::list_integrations))
        .route("/api/integrations/add", post(routes::add_integration))
        .route("/api/integrations/health", axum::routing::get(routes::integrations_health))
        .route("/api/config", axum::routing::get(routes::get_config))
        .with_state(state.clone());
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    Harness { base: format!("http://{addr}"), state, _tmp: tmp }
}

async fn add(h: &Harness) -> (u16, serde_json::Value) {
    let r = reqwest::Client::new().post(format!("{}/api/integrations/add", h.base))
        .json(&serde_json::json!({"id":"probe"})).send().await.unwrap();
    (r.status().as_u16(), r.json().await.unwrap())
}

async fn body(h: &Harness, path: &str) -> String {
    reqwest::get(format!("{}{path}", h.base)).await.unwrap().text().await.unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn connected_with_vault_key_and_value_never_leaks() {
    // Eindeutiger Kanarienwert als Prozess-Umgebung: der CredentialResolver faellt auf env zurueck.
    std::env::set_var("PROBE_KEY_GUT", "GUT");
    let url = start_mini_mcp().await;
    let h = harness(template(&url, "PROBE_KEY_GUT", "admitted")).await;
    let (status, json) = add(&h).await;
    assert_eq!(status, 201, "{json}");
    assert_eq!(json["zustand"], "verbunden", "{json}");
    for p in ["/api/integrations", "/api/integrations/health", "/api/config"] {
        let b = body(&h, p).await;
        assert!(!b.contains("Bearer GUT"), "{p} leaks header: {b}");
    }
    let list = body(&h, "/api/integrations").await;
    assert!(list.contains("PROBE_KEY_GUT"), "reference name should be listed: {list}");
    // Freigabe-Standard ueber den echten Kernel:
    assert!(!h.state.kernel.integration_requires_approval("mcp_probe_get_me"));
    assert!(h.state.kernel.integration_requires_approval("mcp_probe_create_issue"));
    std::env::remove_var("PROBE_KEY_GUT");
}

#[tokio::test(flavor = "multi_thread")]
async fn wrong_key_is_key_rejected() {
    std::env::set_var("PROBE_KEY_FALSCH", "FALSCH");
    let url = start_mini_mcp().await;
    let h = harness(template(&url, "PROBE_KEY_FALSCH", "admitted")).await;
    let (_, json) = add(&h).await;
    assert_eq!(json["zustand"], "schluessel_abgelehnt", "{json}");
    assert!(!body(&h, "/api/integrations/health").await.contains("FALSCH\""));
    std::env::remove_var("PROBE_KEY_FALSCH");
}

#[tokio::test(flavor = "multi_thread")]
async fn missing_key_is_reported_by_name() {
    let url = start_mini_mcp().await;
    let h = harness(template(&url, "PROBE_KEY_FEHLT_UEBERALL", "admitted")).await;
    let (status, json) = add(&h).await;
    assert_eq!(status, 201);
    assert_eq!(json["zustand"], "fehlt_schluessel", "{json}");
    assert!(json["detail"].as_str().unwrap().contains("PROBE_KEY_FEHLT_UEBERALL"));
}

#[tokio::test(flavor = "multi_thread")]
async fn review_required_is_not_installable() {
    let url = start_mini_mcp().await;
    let h = harness(template(&url, "PROBE_KEY_EGAL", "review_required")).await;
    let (status, json) = add(&h).await;
    assert_eq!(status, 409);
    assert_eq!(json["error"], "integration_not_admitted");
}
```

Hinweise für die Implementierung des Tests:
- `get_config` und die Felder von `AppState` an die tatsächlichen Namen in `routes.rs` und `server.rs` anpassen. Die bestehenden Tests nutzen exakt diese Felder.
- Antwortet der Mini-Server nicht MCP-konform genug für `rmcp`, zum Beispiel weil `notifications/initialized` `202` erwartet, wird der Mini-Server nachgebessert. Die Prüfungen ändern sich dabei nicht.
- `reqwest` ist dev-dependency von `openfang-api`. Falls nicht: aus dem Workspace ergänzen.

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cd crates/openfang-api && cargo test -j 2 --test integrations_remote_test`
Expected: FAIL (`zustand` fehlt in der Antwort, 409 nicht implementiert).

- [ ] **Step 3: Implementieren** (`routes.rs`)

- **`add_integration`:** Nach dem Template-Check (vor `install`) die Zulassung prüfen:
  ```rust
  } else if registry.get_template(&id).map(|t| !t.is_admitted()).unwrap_or(false) {
      Some((StatusCode::CONFLICT, "integration_not_admitted".to_string()))
  ```
  Bei diesem Fall `{"error":"integration_not_admitted"}` liefern.
- **Antwort von `add_integration`:** Nach `reload_extension_mcps()` den Status aus der Health holen und mitgeben.
  ```rust
  let h = state.kernel.extension_health.get_health(&id);
  let st = h.as_ref().map(|h| h.status.clone()).unwrap_or(openfang_extensions::IntegrationStatus::Setup);
  ```
  Antwort: `"zustand": st.zustand()`, `"detail": st.detail()` und die bisherigen Felder.
- **`list_integrations`:** Je Eintrag ergänzen:
  - `"zustand"`: Health-Status → `zustand()`, oder `"deaktiviert"` bei `enabled = false`
  - `"detail"`
  - `"auth_headers": info.template.auth_headers.iter().map(|a| json!({"name": a.name, "credential": a.credential}))`
- **`integrations_health`:** Je Eintrag `"zustand": h.status.zustand()` und `"detail": h.status.detail()` ergänzen. `last_error` ist durch `report_status` schon geschwärzt.

- [ ] **Step 4: Laufen lassen, erwartet: PASS; API-Suite grün**

Run: `cd crates/openfang-api && cargo test -j 2 --test integrations_remote_test`, danach `cargo test -j 2 --test api_integration_test`
Expected: 4 neue Tests PASS. Die API-Integrationstests bleiben im bisherigen Stand. Vorher/Nachher-Zahlen gehören in den Bericht.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-api/src/routes.rs crates/openfang-api/tests/integrations_remote_test.rs crates/openfang-api/Cargo.toml
git commit -m "feat(api): Integrations-Zulassung und Zustand, End-to-End-Test gegen Test-MCP-Server"
```

---

### Task 8: Pilot GitHub — Vorlage, Hub-Agent, Konfig, Pin, Live-Nachweis (FREIGABE nötig)

**Files:**
- Create: `vibemind-os/integrations/github.toml` (Worktree `wt-os-pins`)
- Create: `agents/integrations-hub/agent.toml` (openfang-Repo)
- Modify: `openfang.vibemind.toml` (openfang-Repo)
- Modify: Gitlink `openfang` in vibemind-os

**Interfaces:**
- Consumes: der gebaute Daemon aus Tasks 1–7 und `GITHUB_PAT_TOKEN` im Tresor. Er liegt seit 06.10. dort und steht auf der Ausgabeliste.

- [ ] **Step 1: Vorlage anlegen** (`vibemind-os/integrations/github.toml`)

```toml
id = "github"
name = "GitHub"
description = "GitHub ueber den offiziellen Remote-MCP-Server (GitHub Copilot MCP)"
category = "devtools"
icon = "🐙"
tags = ["git", "code", "issues", "pull-requests"]
read_only_tools = ["get_me", "search_repositories", "get_file_contents", "list_issues", "get_issue", "list_pull_requests", "get_pull_request"]

[transport]
type = "http"
url = "https://api.githubcopilot.com/mcp/"

[[auth_headers]]
name = "Authorization"
format = "Bearer {credential}"
credential = "GITHUB_PAT_TOKEN"

[[required_env]]
name = "GITHUB_PAT_TOKEN"
label = "GitHub Personal Access Token (fein granuliert)"
help = "Fein granulierter Token; Rechte nach Bedarf, fuer get_me reichen keine"
is_secret = true
get_url = "https://github.com/settings/personal-access-tokens/new"

[catalog]
replaces_openai_plugin = "github"
license = "MIT"
admission = "admitted"
```

- [ ] **Step 2: Hub-Agent und Konfig im openfang-Repo**

`agents/integrations-hub/agent.toml`. Das Modell nach dem Muster der vorhandenen schlanken Agenten wählen, z. B. `agents/approval-handler/agent.toml`:

```toml
name = "integrations-hub"
version = "0.1.0"
description = "Identitaet fuer Hub-Abnehmer (Claude Code, Spaces, Rowboat): sieht ausdruecklich zugewiesene Integrationen."
author = "vibemind"
module = "builtin:chat"
tags = ["integrations", "hub"]
mcp_servers = ["github"]

[model]
provider = "ollama"
model = "qwen2.5-coder:7b"
max_tokens = 1024
temperature = 0.0
system_prompt = "Du bist die Hub-Identitaet fuer Integrationen. Du fuehrst nur Werkzeugaufrufe aus, die dir ueber /mcp angetragen werden."
```

In `openfang.vibemind.toml` die Sektion ergänzen bzw. erweitern:

```toml
[extensions]
template_dirs = ["../integrations"]
```

Bauen: vorher RAM messen, dann im Worktree `cargo build --release -j 2 -p openfang-cli` (bzw. das Ziel, das `target/release/openfang.exe` erzeugt). Danach mit `cargo test -j 2 --workspace` die Gesamtzahlen festhalten.

Commit im openfang-Worktree, Push auf `claude/openfang-fork-reconciliation-v1`.

- [ ] **Step 3: STOP — Freigabe des Users einholen**

Die folgenden Schritte ändern Laufendes: Daemon neu bauen und tauschen, `:4200` neu starten, Pin umstellen, `github` installieren. Erst nach ausdrücklichem „ok" weiter.

- [ ] **Step 4: Ausrollen**

1. Claims eintragen (WORKBOARD und Koordinationsdatei), jeweils sofort committen.
2. vibemind-os: `integrations/github.toml` committen.
   - Gitlink `openfang` auf den neuen Commit setzen. Vorher prüfen mit `git -C <openfang-worktree> rev-parse HEAD`, `cat-file -t` und `git ls-remote origin <sha>`. Dann `git update-index --cacheinfo 160000,<sha>,openfang`.
   - Danach `git ls-tree HEAD openfang` prüfen, Push `HEAD:master`.
3. Haupt-Checkout `vibemind-os/openfang` auf den neuen Pin ziehen. Ist der Arbeitsbaum dort nicht sauber: stoppen und User fragen.
4. Neue `openfang.exe` an ihren Platz bringen. Ist die Datei gesperrt, den Weg über `deps/openfang.exe` nehmen wie bisher. Daemon-Neustart: Watchdog-Task anhalten, `POST /api/shutdown`, warten, Watchdog-Task starten. Auf `/api/health` warten.
5. Den Daemon-Log auf die Meldung „Integrations-Vorlage uebersprungen" prüfen. Erwartet: keine.

- [ ] **Step 5: Live-Nachweis** (Spec §7)

1. `POST /api/integrations/add {"id":"github"}` mit Bearer. Erwartet: `zustand = "verbunden"`. `GET /api/integrations` zeigt `auth_headers` nur mit Namen.
2. `integrations-hub` ruft über `/mcp` (gebundene Caller-Identität wie in den bestehenden `/mcp`-Tests) `mcp_github_get_me` auf. Erwartet: keine Freigabe, das Ergebnis enthält den Login `Flissel`.
3. Ein schreibendes Werkzeug aufrufen, z. B. `mcp_github_create_issue` mit einem Ziel-Repo, das es nicht gibt. Erwartet: In `GET /api/approvals` erscheint eine Freigabe. Sie wird über `POST /api/approvals/{id}/reject` abgelehnt. Erwartet: Antwort „Execution denied", bei GitHub entsteht nichts.
4. Ein Agent ohne `mcp_servers`-Eintrag (beliebiger bestehender) sieht keine `mcp_github_*`-Werkzeuge. Geprüft wird über `tools/list` als dieser Agent.
5. `reconnect` mit einer absichtlich fehlenden Referenz. Den eigentlichen Tresor nicht anfassen, sondern eine Kopie der Vorlage mit Referenz `GITHUB_PAT_TOKEN_FEHLT` als `github-probe.toml`, installieren und deinstallieren. Erwartet: `fehlt_schluessel`.
6. Leck-Prüfung auf `github_pat_` und `ghp_` an diesen Stellen, erwartet jeweils 0 Treffer:
   - `logs/openfang/*`
   - Audit-Log (DB-Export der letzten Stunde)
   - `/api/config`, `/api/integrations*`
   - Umgebung von Kindprozessen: per `Get-CimInstance Win32_Process` die Prozesse unter `openfang.exe` auflisten und ihre Umgebung **nicht** ausgeben, nur prüfen, ob der Name `GITHUB_PAT_TOKEN` darin vorkommt. Wenn das unter Windows nicht ohne Weiteres geht, die Prüfung über einen Test-Agenten mit `shell_exec` `set` ersetzen. Geprüft wird nur der Name.

- [ ] **Step 6: Abschluss**

- Claims schließen.
- `E2E-PROOF.md` (vibemind-os) um „Teil VIII: Integrations-Kern, GitHub direkt über OpenFang" ergänzen.
- Projektnotiz aktualisieren.
- Den Ketten-Wächter vormerken: Prüfung `zustand` der Integration `github`. Das gehört in Teilprojekt 3, hier nur notieren.
