# OAuth-Anbieter und nicht ausgebbare Integrations-Schlüssel (Teilprojekt 1b) — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenFang meldet sich bei Remote-MCP-Servern nach dem MCP-Anmeldestandard per OAuth an. Dazu gehören Ermittlung, Selbstregistrierung, Browser-Anmeldung über einen Callback am Daemon, Token im Tresor, Erneuerung mit Sperre sowie Abmelden mit Widerruf. Integrations-Schlüssel (Vorsilbe `INTEGRATION_`) gibt der Daemon nie heraus. Statische Schlüssel trägt der User über eine Einmal-Link-Seite ein. Pilot ist Vercel; GitHub wird auf `INTEGRATION_GITHUB_PAT` umgestellt.

**Architecture:** Die Arbeit verteilt sich auf vier Crates:
- **`openfang-extensions`:** Ein neues Modul `mcp_oauth.rs` enthält das reine Protokoll (HTTP über `reqwest`, URL-Regeln über `UrlPolicy`) und den Tresor-Eintragstyp `OAuthRecord`. Vorlagen erhalten `[auth] type = "oauth"`.
- **`openfang-kernel`:** Ein neues Modul `integration_oauth.rs` steuert Anmeldung, Erneuerung (`tokio::Mutex` je Integration), Abmelden und Einmal-Links. `connect_one_mcp` holt bei OAuth-Integrationen vorher ein frisches Access-Token.
- **`openfang-runtime`:** `tool_runner` erneuert bei einer 401 einmal und wiederholt den Aufruf.
- **`openfang-api`:** Neue Routen und die `INTEGRATION_`-Sperre.

**Tech Stack:** Rust (Workspace `openfang`), `reqwest` 0.12 (rustls), `rand` 0.8, `sha2` 0.10, `base64` 0.22, `url` 2, `axum`/`tokio` (Routen und Test-Server), `zeroize`.

**Spec:** `vibemind-os/docs/superpowers/specs/2026-10-08-integrationen-oauth-design.md` (vibemind-os `69bb2698`). Baut auf Teilprojekt 1 auf (Spec `2026-10-06-integrationen-kern-openfang-design.md`, openfang `39514ed`).

## Global Constraints

- **Geheimwerte nie ausgeben.** Access-Token, Refresh-Token, Code, `code_verifier`, `state`, Einmal-Tokens, Client-ID und die Werte statischer Schlüssel erscheinen in keinem Log, `Debug`, `detail`, Audit, keiner API-Antwort und keiner HTML-Seite. Einzige Ausnahme ist die Anmelde-URL: Sie enthält die PKCE-Challenge und `state`.
- **Keine Antworttexte von Anbietern** in Zuständen oder Logs. Erlaubt sind nur HTTP-Status und feste Klassen.
- **Ermittlung nur über `https`.** Der `issuer` muss gleich dem Eintrag in `authorization_servers` sein. Alle Endpunkte liegen auf dem Host des `issuer`, und `S256` ist Pflicht. `http` auf Loopback ist **nur** mit dem Cargo-Feature `test-insecure-oauth` erlaubt **und** wenn die Laufzeit-Erlaubnis gesetzt ist. Release-Builds haben weder das eine noch das andere.
- **`INTEGRATION_`-Referenzen:** `/api/credentials/issue` und `/api/credentials/store` lehnen sie immer ab. Schreiben darf nur der Kernel.
- **Fail closed:**
  - Ohne gültigen `state` findet kein Token-Tausch statt.
  - Ohne frisches Token findet kein Verbindungsversuch statt.
  - Eine gescheiterte Erneuerung führt zu `anmeldung_noetig` und keinem automatischen Neuversuch.
- **Builds nur mit `cargo … -j 2`.** Vorher freien RAM messen: Bei weniger als 6 GB anhalten.
- **Worktree:** Code entsteht nur in `C:/Users/User/ClaudeWork/wt-openfang-credmerge`, mit detached HEAD und ausdrücklichem Staging. Der Commit-Trailer lautet `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Push nur durch den Controller.
- **Unverändert bleiben:**
  - `GITHUB_PAT_TOKEN` und der Rowboat-Weg
  - `/api/credentials/*` für alle Nicht-`INTEGRATION_`-Referenzen
  - die 25 eingebauten Vorlagen
  - alle Garantien aus Teilprojekt 1
- **Keine Schlüssel-Rotation.**

## Review Focus

1. **Der Anbieter rotiert das Refresh-Token, und zwei Aufrufe laufen gleichzeitig in eine 401.** Erwartet: genau eine Erneuerung. Der zweite Aufruf nutzt das frische Token, und das alte Refresh-Token wird nie ein zweites Mal verwendet. → Test in Task 4.
2. **Der User öffnet den Anmelde-Link zweimal, oder der Link ist älter als 10 Minuten.** Erwartet: Der zweite bzw. abgelaufene Callback endet mit „state ungueltig". Es gibt keinen Token-Tausch, und der erste Erfolg bleibt bestehen. → Test in Task 4 und Task 6.
3. **Der Tresor ist gesperrt oder fehlt.** Erwartet: Die Anmeldung schlägt mit der Klasse „tresor nicht verfuegbar" fehl. Der Token wird nicht im Speicher gehalten und kein Erfolg gemeldet. → Test in Task 4.
4. **Eine Callback- oder Schlüssel-URL wird von einer Nicht-Loopback-Adresse aufgerufen, etwa über `tailscale serve` oder das LAN.** Erwartet: 404 bzw. 401 und nie die Formularseite. → Test in Task 6.
5. **Nach dem Umschalten der Vorlage auf `INTEGRATION_GITHUB_PAT` fehlt der Schlüssel noch.** Erwartet: `fehlt_schluessel` mit dem Referenznamen. Es gibt keinen Rückfall auf die stdio-Vorlage und keine Ausgabe über `/issue`. → Test in Task 2 und Task 6.

---

## Dateistruktur

| Datei | Verantwortung |
|---|---|
| `crates/openfang-types/src/config.rs` | `McpServerConfigEntry.oauth: bool` |
| `crates/openfang-extensions/src/lib.rs` | `AuthTemplate`, `IntegrationTemplate.auth`, `IntegrationStatus::{LoginRequired(String), TemplateInvalid}`, `oauth_reference(id)` |
| `crates/openfang-extensions/src/registry.rs` | Prüfregeln (OAuth/`INTEGRATION_`), `to_mcp_configs` für OAuth, `invalid_overrides` ohne stillen Rückfall |
| `crates/openfang-extensions/src/health.rs` | `should_reconnect` schließt `LoginRequired` und `TemplateInvalid` aus |
| `crates/openfang-extensions/src/mcp_oauth.rs` (neu) | `UrlPolicy`, `OAuthRecord`, `discover`, `register_client`, `authorize_url`, `pkce_pair`, `exchange_code`, `refresh`, `revoke` |
| `crates/openfang-kernel/src/integration_oauth.rs` (neu) | `PendingLogins`, `OneTimeLinks`, `RefreshLocks`, Ablaufsteuerung |
| `crates/openfang-kernel/src/kernel.rs` | OAuth-Zweig in `connect_one_mcp`, `remove_integration`, Boot und Reload mit `TemplateInvalid`, KernelHandle-Methoden |
| `crates/openfang-runtime/src/kernel_handle.rs`, `tool_runner.rs` | `refresh_integration_after_rejection` und einmaliges Wiederholen |
| `crates/openfang-api/src/routes.rs`, `middleware.rs`, `server.rs` | Routen, Loopback-Ausnahmen, `INTEGRATION_`-Sperre |
| `crates/openfang-api/tests/integrations_oauth_test.rs` (neu) | Ende-zu-Ende gegen Test-Anmeldeserver und Test-MCP-Server |
| `vibemind-os/integrations/vercel.toml` (neu), `github.toml` | Pilot und Umstellung |

---

### Task 1: Vorlagen, Typen und Zustände — OAuth-Form, `INTEGRATION_`-Regel, kein stiller Rückfall

**Files:**
- Modify: `crates/openfang-types/src/config.rs` (`McpServerConfigEntry`)
- Modify: `crates/openfang-extensions/src/lib.rs` (`IntegrationTemplate`, `IntegrationStatus`)
- Modify: `crates/openfang-extensions/src/registry.rs` (`validate_template`, `load_template_dirs`, `to_mcp_configs`)
- Modify: `crates/openfang-extensions/src/health.rs` (`should_reconnect`)
- Test: die Testmodule dieser Dateien

**Interfaces:**
- Produces:
  - `McpServerConfigEntry.oauth: bool` (`#[serde(default)]`)
  - `pub struct AuthTemplate { #[serde(rename = "type")] pub kind: AuthKind, #[serde(default)] pub scopes: Vec<String> }`
  - `pub enum AuthKind { Oauth }` (serde `snake_case`)
  - `IntegrationTemplate.auth: Option<AuthTemplate>`
  - `IntegrationStatus::LoginRequired(String)` mit `zustand()` = `"anmeldung_noetig"`
  - `IntegrationStatus::TemplateInvalid` mit `zustand()` = `"nicht_zugelassen"` und `detail()` = `Some("vorlage ungueltig")`
  - `pub fn oauth_reference(id: &str) -> String`, das `INTEGRATION_OAUTH_<ID>` liefert (ID in Großbuchstaben, alles Nicht-Alphanumerische wird zu `_`)
  - `pub const INTEGRATION_PREFIX: &str = "INTEGRATION_"`
  - `IntegrationRegistry::invalid_overrides(&self) -> &HashSet<String>`: ids, deren Ordner-Vorlage ungültig war
  - `to_mcp_configs` überspringt installierte ids aus `invalid_overrides`

- [ ] **Step 1: Failing tests schreiben**

In `registry.rs` `mod tests`:

```rust
const VERCEL: &str = r#"
id = "vercel"
name = "Vercel"
description = "Vercel ueber den offiziellen Remote-MCP-Server"
category = "devtools"
read_only_tools = ["list_projects"]
[transport]
type = "http"
url = "https://mcp.vercel.com/"
[auth]
type = "oauth"
scopes = ["offline_access"]
[catalog]
replaces_openai_plugin = "vercel"
license = "proprietary-service"
admission = "admitted"
"#;

#[test]
fn oauth_template_parses_and_maps_to_oauth_entry_with_bearer_reference() {
    let t: crate::IntegrationTemplate = toml::from_str(VERCEL).unwrap();
    assert!(validate_template(&t).is_ok());
    let home = tempfile::tempdir().unwrap();
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("vercel.toml"), VERCEL).unwrap();
    let mut reg = IntegrationRegistry::new(home.path());
    reg.load_bundled();
    reg.load_template_dirs(&[dir.path().to_path_buf()]);
    reg.install(crate::InstalledIntegration { id: "vercel".into(), installed_at: chrono::Utc::now(),
        enabled: true, oauth_provider: None, config: Default::default() }).unwrap();
    let e = reg.to_mcp_configs().into_iter().find(|c| c.name == "vercel").unwrap();
    assert!(e.oauth);
    assert!(e.env.is_empty());
    assert_eq!(e.auth_headers.len(), 1);
    assert_eq!(e.auth_headers[0].name, "Authorization");
    assert_eq!(e.auth_headers[0].format, "Bearer {credential}");
    assert_eq!(e.auth_headers[0].credential, crate::oauth_reference("vercel"));
}

#[test]
fn oauth_reference_shape() {
    assert_eq!(crate::oauth_reference("vercel"), "INTEGRATION_OAUTH_VERCEL");
    assert_eq!(crate::oauth_reference("github-probe"), "INTEGRATION_OAUTH_GITHUB_PROBE");
}

#[test]
fn oauth_rejected_on_stdio_and_together_with_auth_headers() {
    let mut t: crate::IntegrationTemplate = toml::from_str(VERCEL).unwrap();
    t.transport = crate::McpTransportTemplate::Stdio { command: "npx".into(), args: vec![] };
    assert!(validate_template(&t).is_err());
    let mut t: crate::IntegrationTemplate = toml::from_str(VERCEL).unwrap();
    t.auth_headers.push(openfang_types::config::AuthHeaderRef {
        name: "Authorization".into(), format: "Bearer {credential}".into(), credential: "INTEGRATION_X".into() });
    assert!(validate_template(&t).is_err());
}

#[test]
fn catalog_templates_require_integration_prefix_for_static_keys() {
    let bad = REMOTE.replace("credential = \"GITHUB_PAT_TOKEN\"", "credential = \"GITHUB_PAT_TOKEN\"");
    let t: crate::IntegrationTemplate = toml::from_str(&bad).unwrap();
    assert!(validate_template(&t).is_err(), "catalog template with non-INTEGRATION_ reference must be rejected");
    let good = REMOTE.replace("credential = \"GITHUB_PAT_TOKEN\"", "credential = \"INTEGRATION_GITHUB_PAT\"");
    let t: crate::IntegrationTemplate = toml::from_str(&good).unwrap();
    assert!(validate_template(&t).is_ok());
}

#[test]
fn invalid_folder_override_blocks_installed_id_instead_of_falling_back_to_stdio() {
    let home = tempfile::tempdir().unwrap();
    let dir = tempfile::tempdir().unwrap();
    // REMOTE uses GITHUB_PAT_TOKEN -> now invalid for a catalog template
    std::fs::write(dir.path().join("github.toml"), REMOTE).unwrap();
    let mut reg = IntegrationRegistry::new(home.path());
    reg.load_bundled();
    let report = reg.load_template_dirs(&[dir.path().to_path_buf()]);
    assert_eq!(report.loaded, 0);
    assert!(reg.invalid_overrides().contains("github"));
    reg.install(crate::InstalledIntegration { id: "github".into(), installed_at: chrono::Utc::now(),
        enabled: true, oauth_provider: None, config: Default::default() }).unwrap();
    assert!(reg.to_mcp_configs().iter().all(|c| c.name != "github"), "no silent stdio fallback");
}
```

(`REMOTE` ist die bestehende Test-Konstante aus Teilprojekt 1 mit `credential = "GITHUB_PAT_TOKEN"`. Sie bleibt unverändert. Der zweite Test nutzt `.replace(...)`, das in Fall 1 absichtlich nichts ändert.)

In `lib.rs` `mod tests`:

```rust
#[test]
fn login_required_and_template_invalid_states() {
    assert_eq!(IntegrationStatus::LoginRequired("nie angemeldet".into()).zustand(), "anmeldung_noetig");
    assert_eq!(IntegrationStatus::LoginRequired("nie angemeldet".into()).detail().as_deref(), Some("nie angemeldet"));
    assert_eq!(IntegrationStatus::TemplateInvalid.zustand(), "nicht_zugelassen");
    assert_eq!(IntegrationStatus::TemplateInvalid.detail().as_deref(), Some("vorlage ungueltig"));
}
```

In `health.rs` `mod tests`:

```rust
#[test]
fn login_required_and_template_invalid_never_auto_reconnect() {
    let m = HealthMonitor::new(HealthMonitorConfig::default());
    m.register("a");
    m.report_status("a", IntegrationStatus::LoginRequired("erneuerung fehlgeschlagen".into()));
    assert!(!m.should_reconnect("a"));
    m.report_status("a", IntegrationStatus::TemplateInvalid);
    assert!(!m.should_reconnect("a"));
}
```

In `config.rs` `mod tests`:

```rust
#[test]
fn mcp_server_entry_oauth_defaults_false() {
    let e: McpServerConfigEntry = toml::from_str("name = \"x\"\ntimeout_secs = 30\n[transport]\ntype = \"http\"\nurl = \"https://e/mcp\"\n").unwrap();
    assert!(!e.oauth);
}
```

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cargo test -j 2 -p openfang-extensions --lib`, dann `cargo test -j 2 -p openfang-types --lib config`
Expected: Kompilierfehler (fehlende Typen und Felder).

- [ ] **Step 3: Implementieren**

1. `config.rs`: `#[serde(default)] pub oauth: bool,` an `McpServerConfigEntry` anhängen. Dazu `.field("oauth", &self.oauth)` im handgeschriebenen `Debug` und `oauth: false` in allen Struct-Literalen. Suchen mit `grep -rn "McpServerConfigEntry {" crates`.
2. `lib.rs`:
   ```rust
   pub const INTEGRATION_PREFIX: &str = "INTEGRATION_";

   pub fn oauth_reference(id: &str) -> String {
       let up: String = id.chars().map(|c| if c.is_ascii_alphanumeric() { c.to_ascii_uppercase() } else { '_' }).collect();
       format!("{INTEGRATION_PREFIX}OAUTH_{up}")
   }

   #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
   #[serde(rename_all = "snake_case")]
   pub enum AuthKind { Oauth }

   #[derive(Debug, Clone, Serialize, Deserialize)]
   pub struct AuthTemplate {
       #[serde(rename = "type")]
       pub kind: AuthKind,
       #[serde(default)]
       pub scopes: Vec<String>,
   }
   ```
   - `IntegrationTemplate`: Feld `#[serde(default)] pub auth: Option<AuthTemplate>,`.
   - `IntegrationStatus`: Varianten `LoginRequired(String)` und `TemplateInvalid` ergänzen.
   - `zustand()`: `Self::LoginRequired(_) => "anmeldung_noetig"` und `Self::TemplateInvalid => "nicht_zugelassen"`.
   - `detail()`: `Self::LoginRequired(r) => Some(r.clone())` und `Self::TemplateInvalid => Some("vorlage ungueltig".into())`.
   - `Display`: `"Login required: {r}"` und `"Template invalid"`.
   - Alle erschöpfenden `match` im Workspace nachziehen, etwa in `openfang-cli`. Suchen mit `cargo check -j 2 --workspace --tests`.
3. `registry.rs`, in `validate_template` zusätzlich:
   ```rust
   if let Some(auth) = &t.auth {
       let _ = auth;
       match t.transport {
           crate::McpTransportTemplate::Http { .. } | crate::McpTransportTemplate::Sse { .. } => {}
           crate::McpTransportTemplate::Stdio { .. } => return Err("oauth nur bei http/sse erlaubt".into()),
       }
       if !t.auth_headers.is_empty() {
           return Err("oauth schliesst auth_headers aus".into());
       }
   }
   if t.catalog.is_some() && t.auth_headers.iter().any(|h| !h.credential.starts_with(crate::INTEGRATION_PREFIX)) {
       return Err("auth_headers: Referenz muss mit INTEGRATION_ beginnen".into());
   }
   ```
   - `IntegrationRegistry` bekommt das Feld `invalid_overrides: std::collections::HashSet<String>`, initialisiert in `new`, mit Getter `invalid_overrides()`.
   - In `load_template_dirs`: Eine Datei, die zwar als TOML parst, aber an `validate_template` scheitert, trägt ihre `t.id` in `invalid_overrides` ein. Eine gültig geladene Vorlage mit derselben id entfernt sie dort wieder. Die id wird dabei nur gesammelt, nie ins Log geschrieben.
   - In `to_mcp_configs`: ids aus `invalid_overrides` überspringen. Für Vorlagen mit `auth: Some(_)` gilt `oauth: true` und `auth_headers: vec![AuthHeaderRef { name: "Authorization".into(), format: "Bearer {credential}".into(), credential: crate::oauth_reference(&inst.id) }]`, `env` bleibt leer.
4. `health.rs`: `should_reconnect` bleibt bei `matches!(…, Error(_) | Unreachable(_))`. Die neuen Varianten fallen damit schon heraus. Der Test sichert das ab.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run: `cargo test -j 2 -p openfang-extensions --lib`, `cargo test -j 2 -p openfang-types --lib config`, `cargo check -j 2 --workspace --tests`
Expected: alles grün.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-types/src/config.rs crates/openfang-extensions/src/lib.rs crates/openfang-extensions/src/registry.rs crates/openfang-extensions/src/health.rs
git commit -m "feat(extensions): OAuth-Vorlagen, INTEGRATION_-Referenzen, Zustand anmeldung_noetig, kein stiller Rueckfall"
```
(Weitere Dateien aus dem `match`- und Literal-Nachzug ausdrücklich mit `git add`.)

---

### Task 2: Protokoll-Modul `mcp_oauth.rs` — Ermittlung, Registrierung, PKCE, Tausch, Erneuerung, Widerruf

**Files:**
- Create: `crates/openfang-extensions/src/mcp_oauth.rs`
- Modify: `crates/openfang-extensions/src/lib.rs` (`pub mod mcp_oauth;`)
- Modify: `crates/openfang-extensions/Cargo.toml`:
  - Feature `test-insecure-oauth = []`
  - unter `[dev-dependencies]`: `axum`, `tokio` (features `macros`, `rt-multi-thread`) und `tempfile`, jeweils `{ workspace = true }`, falls sie fehlen
- Test: im Modul

**Interfaces:**
- Produces:
  ```rust
  pub struct UrlPolicy { allow_loopback_http: bool }
  impl UrlPolicy { pub fn strict() -> Self; #[cfg(feature = "test-insecure-oauth")] pub fn allow_loopback_http() -> Self; pub fn check(&self, url: &str) -> Result<url::Url, OAuthError>; }
  pub enum OAuthError { Discovery(&'static str), Registration(u16), TokenExchange(u16), Refresh(RefreshFailure), Revoke(u16), Network(&'static str), Vault }
  pub enum RefreshFailure { InvalidGrant, Http(u16) }
  impl OAuthError { pub fn class(&self) -> String }   // feste Klassen fuer `detail`, nie Antworttexte
  #[derive(Clone, Serialize, Deserialize)] pub struct OAuthRecord { pub v: u8, pub resource: String, pub issuer: String,
      pub authorization_endpoint: String, pub token_endpoint: String, pub registration_endpoint: Option<String>,
      pub revocation_endpoint: Option<String>, pub client_id: Option<String>, pub access_token: Option<String>,
      pub refresh_token: Option<String>, pub expires_at: Option<chrono::DateTime<chrono::Utc>>, pub scopes: Vec<String> }
  impl std::fmt::Debug for OAuthRecord   // schwaerzt client_id, access_token, refresh_token
  impl OAuthRecord { pub fn needs_refresh(&self, now: chrono::DateTime<chrono::Utc>) -> bool; pub fn has_tokens(&self) -> bool }
  pub async fn discover(http: &reqwest::Client, policy: &UrlPolicy, resource_url: &str) -> Result<OAuthRecord, OAuthError>;
  pub async fn register_client(http: &reqwest::Client, policy: &UrlPolicy, rec: &mut OAuthRecord, redirect_uri: &str) -> Result<(), OAuthError>;
  pub struct Pkce { pub verifier: zeroize::Zeroizing<String>, pub challenge: String }
  pub fn pkce_pair() -> Pkce;
  pub fn random_token() -> zeroize::Zeroizing<String>;   // 32 Bytes, base64url ohne Padding
  pub fn authorize_url(rec: &OAuthRecord, redirect_uri: &str, state: &str, challenge: &str) -> Result<String, OAuthError>;
  pub async fn exchange_code(http: &reqwest::Client, policy: &UrlPolicy, rec: &mut OAuthRecord, code: &str, verifier: &str, redirect_uri: &str) -> Result<(), OAuthError>;
  pub async fn refresh(http: &reqwest::Client, policy: &UrlPolicy, rec: &mut OAuthRecord) -> Result<(), OAuthError>;
  pub async fn revoke(http: &reqwest::Client, policy: &UrlPolicy, rec: &OAuthRecord) -> Result<(), OAuthError>;
  ```

- [ ] **Step 1: Failing tests schreiben** (`#[cfg(test)] mod tests`). Die Tests laufen mit dem Feature: `cargo test -p openfang-extensions --features test-insecure-oauth`.

```rust
use super::*;
use axum::{extract::Form, http::{HeaderMap, StatusCode}, routing::{get, post}, Json, Router};
use std::sync::{Arc, Mutex};

#[derive(Default)]
struct Seen { register: u32, token_bodies: Vec<String>, revoke: u32 }

/// Test-Anmeldeserver + Schutzbeschreibung auf 127.0.0.1:<port>; Antwortverhalten per Flags.
async fn mock_as(rotate_refresh: bool, register_status: u16) -> (String, Arc<Mutex<Seen>>) {
    let seen = Arc::new(Mutex::new(Seen::default()));
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", l.local_addr().unwrap());
    let b = base.clone();
    let s1 = seen.clone(); let s2 = seen.clone(); let s3 = seen.clone();
    let app = Router::new()
        .route("/mcp", post(move || { let b = b.clone(); async move {
            let mut h = HeaderMap::new();
            h.insert("www-authenticate", format!("Bearer error=\"invalid_token\", resource_metadata=\"{b}/.well-known/oauth-protected-resource\"").parse().unwrap());
            (StatusCode::UNAUTHORIZED, h) } }))
        .route("/.well-known/oauth-protected-resource", get({ let b = base.clone(); move || async move {
            Json(serde_json::json!({"resource": format!("{b}/mcp"), "authorization_servers": [b]})) } }))
        .route("/.well-known/oauth-authorization-server", get({ let b = base.clone(); move || async move {
            Json(serde_json::json!({"issuer": b, "authorization_endpoint": format!("{b}/authorize"),
              "token_endpoint": format!("{b}/token"), "registration_endpoint": format!("{b}/register"),
              "revocation_endpoint": format!("{b}/revoke"), "code_challenge_methods_supported": ["S256"],
              "grant_types_supported": ["authorization_code","refresh_token"],
              "token_endpoint_auth_methods_supported": ["none"]})) } }))
        .route("/register", post(move |Json(_b): Json<serde_json::Value>| { let s = s1.clone(); async move {
            s.lock().unwrap().register += 1;
            if register_status != 201 { return (StatusCode::from_u16(register_status).unwrap(), Json(serde_json::json!({"error":"KANARIE-REG-BODY"}))); }
            (StatusCode::CREATED, Json(serde_json::json!({"client_id": "KANARIE-CLIENT"}))) } }))
        .route("/token", post(move |Form(f): Form<std::collections::HashMap<String, String>>| { let s = s2.clone(); async move {
            let mut g = s.lock().unwrap();
            g.token_bodies.push(format!("{:?}", f.get("grant_type")));
            match f.get("grant_type").map(String::as_str) {
                Some("authorization_code") if f.get("code").map(String::as_str) == Some("CODE-OK") && f.contains_key("code_verifier") =>
                    (StatusCode::OK, Json(serde_json::json!({"access_token":"KANARIE-AT-1","refresh_token":"KANARIE-RT-1","expires_in":3600,"token_type":"Bearer"}))),
                Some("refresh_token") if f.get("refresh_token").map(String::as_str) == Some("KANARIE-RT-1") =>
                    (StatusCode::OK, Json(if rotate_refresh {
                        serde_json::json!({"access_token":"KANARIE-AT-2","refresh_token":"KANARIE-RT-2","expires_in":3600})
                    } else { serde_json::json!({"access_token":"KANARIE-AT-2","expires_in":3600}) })),
                Some("refresh_token") => (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"invalid_grant","error_description":"KANARIE-ERR-BODY"}))),
                _ => (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"invalid_request"}))),
            } } }))
        .route("/revoke", post(move || { let s = s3.clone(); async move { s.lock().unwrap().revoke += 1; StatusCode::OK } }));
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    (base, seen)
}

fn http() -> reqwest::Client { reqwest::Client::new() }

#[test]
fn strict_policy_rejects_http_even_on_loopback() {
    assert!(UrlPolicy::strict().check("http://127.0.0.1:9/mcp").is_err());
    assert!(UrlPolicy::strict().check("https://mcp.vercel.com/").is_ok());
}

#[tokio::test]
async fn discover_register_authorize_exchange_refresh_revoke_full_cycle() {
    let (base, seen) = mock_as(true, 201).await;
    let p = UrlPolicy::allow_loopback_http();
    let mut rec = discover(&http(), &p, &format!("{base}/mcp")).await.unwrap();
    assert_eq!(rec.issuer, base);
    register_client(&http(), &p, &mut rec, "http://127.0.0.1:4200/api/integrations/x/oauth/callback").await.unwrap();
    assert_eq!(rec.client_id.as_deref(), Some("KANARIE-CLIENT"));
    let pk = pkce_pair();
    let st = random_token();
    let url = authorize_url(&rec, "http://127.0.0.1:4200/api/integrations/x/oauth/callback", &st, &pk.challenge).unwrap();
    assert!(url.contains("code_challenge_method=S256") && url.contains(&pk.challenge) && url.contains("response_type=code"));
    assert!(!url.contains(pk.verifier.as_str()));
    exchange_code(&http(), &p, &mut rec, "CODE-OK", &pk.verifier, "http://127.0.0.1:4200/api/integrations/x/oauth/callback").await.unwrap();
    assert_eq!(rec.access_token.as_deref(), Some("KANARIE-AT-1"));
    refresh(&http(), &p, &mut rec).await.unwrap();
    assert_eq!(rec.access_token.as_deref(), Some("KANARIE-AT-2"));
    assert_eq!(rec.refresh_token.as_deref(), Some("KANARIE-RT-2"), "rotated refresh token adopted");
    revoke(&http(), &p, &rec).await.unwrap();
    assert_eq!(seen.lock().unwrap().revoke, 1);
    let dbg = format!("{rec:?}");
    for c in ["KANARIE-AT-2", "KANARIE-RT-2", "KANARIE-CLIENT"] { assert!(!dbg.contains(c), "Debug leaks {c}"); }
}

#[tokio::test]
async fn refresh_keeps_old_refresh_token_when_not_rotated_and_invalid_grant_is_classified() {
    let (base, _) = mock_as(false, 201).await;
    let p = UrlPolicy::allow_loopback_http();
    let mut rec = discover(&http(), &p, &format!("{base}/mcp")).await.unwrap();
    register_client(&http(), &p, &mut rec, "http://127.0.0.1:1/cb").await.unwrap();
    let pk = pkce_pair();
    exchange_code(&http(), &p, &mut rec, "CODE-OK", &pk.verifier, "http://127.0.0.1:1/cb").await.unwrap();
    refresh(&http(), &p, &mut rec).await.unwrap();
    assert_eq!(rec.refresh_token.as_deref(), Some("KANARIE-RT-1"));
    rec.refresh_token = Some("VERBRAUCHT".into());
    let err = refresh(&http(), &p, &mut rec).await.unwrap_err();
    assert!(matches!(err, OAuthError::Refresh(RefreshFailure::InvalidGrant)));
    assert!(!err.class().contains("KANARIE-ERR-BODY"));
}

#[tokio::test]
async fn registration_rejection_is_a_fixed_class_without_body() {
    let (base, _) = mock_as(true, 403).await;
    let p = UrlPolicy::allow_loopback_http();
    let mut rec = discover(&http(), &p, &format!("{base}/mcp")).await.unwrap();
    let err = register_client(&http(), &p, &mut rec, "http://127.0.0.1:1/cb").await.unwrap_err();
    assert_eq!(err.class(), "registrierung abgelehnt (http 403)");
}

#[tokio::test]
async fn discovery_rejects_issuer_mismatch_and_foreign_host_endpoints() {
    // Anmeldeserver, dessen issuer nicht zur Schutzbeschreibung passt
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let b = format!("http://{}", l.local_addr().unwrap());
    let b2 = b.clone(); let b3 = b.clone();
    let app = Router::new()
        .route("/.well-known/oauth-protected-resource", get(move || { let b = b2.clone(); async move { Json(serde_json::json!({"authorization_servers":[b]})) } }))
        .route("/.well-known/oauth-authorization-server", get(move || { let _b = b3.clone(); async move { Json(serde_json::json!({
            "issuer":"http://evil.example","authorization_endpoint":"http://evil.example/a","token_endpoint":"http://evil.example/t",
            "code_challenge_methods_supported":["S256"]})) } }));
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    let err = discover(&http(), &UrlPolicy::allow_loopback_http(), &format!("{b}/mcp")).await.unwrap_err();
    assert!(err.class().starts_with("ermittlung fehlgeschlagen"));
}

#[test]
fn pkce_challenge_is_s256_of_verifier() {
    use sha2::{Digest, Sha256};
    let pk = pkce_pair();
    let expect = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(Sha256::digest(pk.verifier.as_bytes()));
    use base64::Engine;
    assert_eq!(pk.challenge, expect);
    assert!(pk.verifier.len() >= 43);
}

#[test]
fn needs_refresh_under_120_seconds() {
    let now = chrono::Utc::now();
    let mut r = OAuthRecord::empty_for_tests();
    r.access_token = Some("x".into());
    r.expires_at = Some(now + chrono::Duration::seconds(119));
    assert!(r.needs_refresh(now));
    r.expires_at = Some(now + chrono::Duration::seconds(600));
    assert!(!r.needs_refresh(now));
}
```

(`OAuthRecord::empty_for_tests()` ist ein `#[cfg(test)]`-Konstruktor mit leeren Feldern.)

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cargo test -j 2 -p openfang-extensions --features test-insecure-oauth --lib mcp_oauth`
Expected: Kompilierfehler, weil das Modul fehlt.

- [ ] **Step 3: Implementieren** (`mcp_oauth.rs`). Kernregeln, die der Code einhalten **muss**:

- **`UrlPolicy::check`:** URL parsen. `https` ist immer erlaubt. `http` nur, wenn `allow_loopback_http` gesetzt ist **und** der Host `127.0.0.1`, `::1` oder `localhost` lautet. Alles andere ergibt `OAuthError::Discovery("unsicheres schema")`. Der Konstruktor `allow_loopback_http()` existiert nur mit `#[cfg(feature = "test-insecure-oauth")]`.
- **`discover`:**
  1. `POST resource_url` mit leerem JSON-RPC-`initialize`, mit `Accept: application/json, text/event-stream`. Aus einer 401-Antwort `WWW-Authenticate` lesen. `resource_metadata="…"` mit einem einfachen Parser herauslösen, der nach `resource_metadata="` sucht und bis zum nächsten `"` liest.
  2. Fehlt der Header, gilt `{origin}/.well-known/oauth-protected-resource`.
  3. Die Schutzbeschreibung lesen und `authorization_servers[0]` als `as_url` nehmen. Danach `{as_url}/.well-known/oauth-authorization-server` lesen, ersatzweise `{as_url}/.well-known/openid-configuration`.
  4. Prüfungen in dieser Reihenfolge, jede führt bei Verstoß zu `Discovery("…")`:
     - Jede URL besteht `policy.check`.
     - `issuer == as_url`, ohne abschließenden `/`-Unterschied: beide Seiten mit `trim_end_matches('/')` vergleichen.
     - Der Host von authorization, token, registration (falls vorhanden) und revocation (falls vorhanden) ist gleich dem Host des `issuer`.
     - `code_challenge_methods_supported` enthält `"S256"`.
  5. Ergebnis ist ein `OAuthRecord` ohne Tokens. `resource` ist die `resource_url`.
- **Alle Requests:** `reqwest` mit `timeout(Duration::from_secs(20))`. Antwortkörper werden **nur** als JSON in erwartete Felder gelesen und nie als Text weitergegeben. Netzwerkfehler werden zu `Network("verbindung")`, `Network("timeout")` oder `Network("tls")`, ermittelt über `e.is_timeout()`, `e.is_connect()` usw.
- **`register_client`:**
  - `POST registration_endpoint` mit JSON `{"client_name":"OpenFang (VibeMind)","redirect_uris":[redirect_uri],"grant_types":["authorization_code","refresh_token"],"response_types":["code"],"token_endpoint_auth_method":"none"}` und, falls nicht leer, `"scope": scopes.join(" ")`.
  - 200 oder 201 mit `client_id` setzt `rec.client_id`. Jeder andere Status ergibt `Registration(status)`.
  - Fehlt `registration_endpoint`, ergibt das `Discovery("keine selbstregistrierung")`.
- **`pkce_pair`:** 32 Zufallsbytes über `rand::rngs::OsRng`, als base64url ohne Padding der `verifier`. Die `challenge` ist base64url ohne Padding von `sha256(verifier)`.
- **`random_token`:** 32 Zufallsbytes über `OsRng`, base64url ohne Padding.
- **`authorize_url`:** `authorization_endpoint` mit diesen Query-Parametern, gesetzt über `url::Url::query_pairs_mut`:
  - `response_type=code`
  - `client_id`
  - `redirect_uri`
  - `state`
  - `code_challenge`
  - `code_challenge_method=S256`
  - `resource=<rec.resource>`
  - `scope`, falls `scopes` nicht leer ist
  Fehlt `client_id`, ergibt das `Discovery("keine client_id")`.
- **`exchange_code`:**
  - `POST token_endpoint` als `application/x-www-form-urlencoded` mit `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `code_verifier` und `resource`.
  - Bei 200 gilt `access_token` (Pflicht), dazu `refresh_token` (optional). `expires_at` ist jetzt plus `expires_in`; fehlt `expires_in`, sind es 3600.
  - Sonst ergibt das `TokenExchange(status)`.
- **`refresh`:**
  - `grant_type=refresh_token`, `refresh_token`, `client_id`, `resource`.
  - Bei 200 wird `access_token` ersetzt, ebenso `refresh_token`, **falls** die Antwort eines liefert, und `expires_at`.
  - 400 oder 401 mit JSON-`error` gleich `"invalid_grant"` ergibt `Refresh(InvalidGrant)`. Sonst ergibt es `Refresh(Http(status))`.
  - Fehlt `refresh_token`, ergibt das `Refresh(InvalidGrant)`.
- **`revoke`:**
  - Ohne `revocation_endpoint` sofort `Ok(())`.
  - Sonst `POST` als Formular mit `token=<refresh_token oder access_token>` und `client_id`. 200 heißt `Ok`, sonst `Revoke(status)`.
- **`OAuthError::class()`** liefert diese festen Texte:
  - `Discovery(r)`: `"ermittlung fehlgeschlagen: {r}"`
  - `Registration(s)`: `"registrierung abgelehnt (http {s})"`
  - `TokenExchange(s)`: `"token-tausch fehlgeschlagen (http {s})"`
  - `Refresh(InvalidGrant)`: `"erneuerung fehlgeschlagen"`
  - `Refresh(Http(s))`: `"erneuerung fehlgeschlagen (http {s})"`
  - `Revoke(s)`: `"widerruf fehlgeschlagen (http {s})"`
  - `Network(c)`: `"nicht erreichbar: {c}"`
  - `Vault`: `"tresor nicht verfuegbar"`
- **`OAuthRecord` `Debug`:** `client_id`, `access_token` und `refresh_token` werden als `<redacted>` bzw. `None` ausgegeben.
- **`needs_refresh(now)`:** `access_token.is_none()` oder `expires_at.map_or(true, |e| e - now < 120s)`.
- **`has_tokens()`:** `access_token.is_some()`.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run:
- `cargo test -j 2 -p openfang-extensions --features test-insecure-oauth --lib`
- **zusätzlich ohne Feature**: `cargo test -j 2 -p openfang-extensions --lib strict_policy` (der Strikt-Test muss auch ohne Feature kompilieren und laufen)
- `cargo check -j 2 --workspace --tests`

Expected: PASS. Tests, die `allow_loopback_http()` benutzen, sind mit `#[cfg(feature = "test-insecure-oauth")]` markiert.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-extensions/src/mcp_oauth.rs crates/openfang-extensions/src/lib.rs crates/openfang-extensions/Cargo.toml
git commit -m "feat(extensions): MCP-OAuth-Protokoll (Ermittlung, Selbstregistrierung, PKCE, Tausch, Erneuerung, Widerruf)"
```

---

### Task 3: Kernel-Bausteine `integration_oauth.rs` — `state`, Einmal-Links, Erneuerungssperren

**Files:**
- Create: `crates/openfang-kernel/src/integration_oauth.rs`
- Modify: `crates/openfang-kernel/src/lib.rs` (`pub mod integration_oauth;`)
- Test: im Modul

**Interfaces:**
- Produces:
  ```rust
  pub struct PendingLogin { pub integration: String, pub verifier: zeroize::Zeroizing<String>, pub redirect_uri: String, pub created: std::time::Instant }
  pub struct PendingLogins { /* Mutex<HashMap<String(state), PendingLogin>> */ }
  impl PendingLogins { pub fn new() -> Self; pub fn insert(&self, state: &str, login: PendingLogin);
      pub fn take(&self, state: &str, integration: &str, ttl: std::time::Duration) -> Option<PendingLogin>; }  // atomar, einmalig, Ablauf + Integrations-Bindung
  pub struct OneTimeLink { pub integration: String, pub reference: String, pub created: std::time::Instant }
  pub struct OneTimeLinks { /* Mutex<HashMap<String(token), OneTimeLink>> */ }
  impl OneTimeLinks { pub fn new() -> Self; pub fn insert(&self, token: &str, link: OneTimeLink);
      pub fn peek(&self, token: &str, integration: &str, ttl: Duration) -> Option<String /*reference*/>;   // GET verbraucht nicht
      pub fn take(&self, token: &str, integration: &str, ttl: Duration) -> Option<String /*reference*/>; }  // POST verbraucht
  pub struct RefreshLocks { /* Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>> */ }
  impl RefreshLocks { pub fn new() -> Self; pub fn for_integration(&self, id: &str) -> Arc<tokio::sync::Mutex<()>>; }
  pub const LOGIN_TTL: std::time::Duration = std::time::Duration::from_secs(600);
  pub fn callback_url(api_listen: &str, id: &str) -> String;   // http://127.0.0.1:<port>/api/integrations/<id>/oauth/callback (0.0.0.0 -> 127.0.0.1)
  pub fn validate_static_key(value: &str) -> Result<(), &'static str>;  // nicht leer, keine Steuerzeichen, <= 4096
  ```

- [ ] **Step 1: Failing tests schreiben**

```rust
use super::*;
use std::time::{Duration, Instant};

fn login(i: &str) -> PendingLogin {
    PendingLogin { integration: i.into(), verifier: zeroize::Zeroizing::new("V".into()), redirect_uri: "r".into(), created: Instant::now() }
}

#[test]
fn state_is_single_use_and_bound_to_integration() {
    let p = PendingLogins::new();
    p.insert("S1", login("vercel"));
    assert!(p.take("S1", "github", LOGIN_TTL).is_none(), "falsche Integration");
    assert!(p.take("S1", "vercel", LOGIN_TTL).is_none(), "nach Fehlversuch verbraucht (fail closed)");
    p.insert("S2", login("vercel"));
    assert!(p.take("S2", "vercel", LOGIN_TTL).is_some());
    assert!(p.take("S2", "vercel", LOGIN_TTL).is_none(), "einmalig");
}

#[test]
fn state_expires() {
    let p = PendingLogins::new();
    let mut l = login("vercel");
    l.created = Instant::now() - Duration::from_secs(601);
    p.insert("S", l);
    assert!(p.take("S", "vercel", LOGIN_TTL).is_none());
}

#[test]
fn one_time_link_peek_does_not_consume_take_does() {
    let l = OneTimeLinks::new();
    l.insert("T", OneTimeLink { integration: "github".into(), reference: "INTEGRATION_GITHUB_PAT".into(), created: Instant::now() });
    assert_eq!(l.peek("T", "github", LOGIN_TTL).as_deref(), Some("INTEGRATION_GITHUB_PAT"));
    assert_eq!(l.take("T", "github", LOGIN_TTL).as_deref(), Some("INTEGRATION_GITHUB_PAT"));
    assert!(l.take("T", "github", LOGIN_TTL).is_none());
    assert!(l.peek("T", "github", LOGIN_TTL).is_none());
}

#[tokio::test]
async fn refresh_lock_is_shared_per_integration() {
    let r = RefreshLocks::new();
    let a = r.for_integration("vercel");
    let b = r.for_integration("vercel");
    let c = r.for_integration("linear");
    assert!(Arc::ptr_eq(&a, &b));
    assert!(!Arc::ptr_eq(&a, &c));
    let _g = a.lock().await;
    assert!(b.try_lock().is_err());
}

#[test]
fn callback_url_uses_loopback_and_port() {
    assert_eq!(callback_url("127.0.0.1:4200", "vercel"), "http://127.0.0.1:4200/api/integrations/vercel/oauth/callback");
    assert_eq!(callback_url("0.0.0.0:4200", "vercel"), "http://127.0.0.1:4200/api/integrations/vercel/oauth/callback");
}

#[test]
fn static_key_validation() {
    assert!(validate_static_key("ghp_abc").is_ok());
    assert!(validate_static_key("").is_err());
    assert!(validate_static_key("a\r\nb").is_err());
    assert!(validate_static_key(&"x".repeat(4097)).is_err());
}
```

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cargo test -j 2 -p openfang-kernel --lib integration_oauth`
Expected: Kompilierfehler, weil das Modul fehlt.

- [ ] **Step 3: Implementieren**
  - Alle Maps liegen unter `std::sync::Mutex` mit `unwrap_or_else(|e| e.into_inner())`.
  - `take` entfernt den Eintrag **immer**, auch wenn danach die Integrations- oder Ablaufprüfung scheitert. Das ist fail closed.
  - `peek` liefert nur einen gültigen Eintrag, lässt ihn aber stehen.
  - `callback_url` liest den Port aus `api_listen` (`rsplit_once(':')`). Ein Host `0.0.0.0` oder leer wird zu `127.0.0.1`. Das Ergebnis ist `format!("http://{host}:{port}/api/integrations/{id}/oauth/callback")`.
  - Kein Wert aus `PendingLogin` oder `OneTimeLink` darf in einem `Debug` erscheinen. Deshalb kein `derive(Debug)` für `PendingLogin`.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run: `cargo test -j 2 -p openfang-kernel --lib integration_oauth`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-kernel/src/integration_oauth.rs crates/openfang-kernel/src/lib.rs
git commit -m "feat(kernel): Bausteine fuer OAuth-Anmeldung, Einmal-Links und Erneuerungssperren"
```

---

### Task 4: Kernel-Ablauf — Anmeldung, Callback, frisches Token, Erneuerung, Abmelden, Einmal-Link, Boot

**Files:**
- Modify: `crates/openfang-kernel/src/kernel.rs`. Betroffen sind:
  - die Felder des Kernel-Structs
  - `connect_one_mcp` (~6028)
  - `remove_integration` (~6341)
  - Boot und Reload für `TemplateInvalid`
  - die Methoden in `impl OpenFangKernel`
- Test: das Testmodul in `kernel.rs`

**Interfaces:**
- Consumes: Task 1 (`oauth_reference`, `oauth`-Flag, `LoginRequired`, `TemplateInvalid`, `invalid_overrides`), Task 2 (`mcp_oauth::*`), Task 3 (`PendingLogins`, `OneTimeLinks`, `RefreshLocks`, `callback_url`, `validate_static_key`).
- Produces (alle `pub` an `OpenFangKernel`):
  ```rust
  pub fn oauth_policy(&self) -> openfang_extensions::mcp_oauth::UrlPolicy;   // strict(); mit Feature test-insecure-oauth UND config.extensions.oauth_allow_loopback_http -> allow_loopback_http()
  pub async fn oauth_start(self: &Arc<Self>, id: &str) -> Result<String /*anmelde_url*/, String /*klasse*/>;
  pub async fn oauth_callback(self: &Arc<Self>, id: &str, code: Option<&str>, state: &str, error: Option<&str>) -> Result<(), String /*klasse*/>;
  pub async fn oauth_logout(self: &Arc<Self>, id: &str) -> Result<(), String>;
  pub async fn oauth_fresh_access_token(self: &Arc<Self>, id: &str, rejected: Option<&str>) -> Result<zeroize::Zeroizing<String>, openfang_extensions::IntegrationStatus>;   // rejected = abgelehntes Access-Token -> erneuern nur, wenn der Tresor noch genau dieses haelt
  pub fn schluessel_link(&self, id: &str) -> Result<String /*token*/, String>;
  pub fn schluessel_peek(&self, id: &str, token: &str) -> Option<String /*reference*/>;
  pub async fn schluessel_store(self: &Arc<Self>, id: &str, token: &str, value: &str) -> Result<(), String /*klasse*/>;
  ```
  Neue Kernel-Felder:
  - `oauth_pending: integration_oauth::PendingLogins`
  - `oauth_links: integration_oauth::OneTimeLinks`
  - `oauth_locks: integration_oauth::RefreshLocks`
  - `oauth_http: reqwest::Client` mit Timeout 20 s

  Neues Konfigfeld `ExtensionsConfig.oauth_allow_loopback_http: bool` (Standard `false`, `#[serde(default)]`). Es wirkt **nur** mit dem Feature `test-insecure-oauth`. Das Feature reicht `openfang-kernel` an `openfang-extensions/test-insecure-oauth` durch.

- [ ] **Step 1: Failing tests schreiben** (im Testmodul von `kernel.rs`, Feature-gesteuert)

```rust
#[cfg(feature = "test-insecure-oauth")]
mod oauth_flow_tests {
    use super::*;
    // Hilfsfunktion: Test-Anmeldeserver + Test-MCP-Server, der nur "Bearer KANARIE-AT-<n>" akzeptiert.
    // Nach Muster von openfang-extensions mcp_oauth::tests::mock_as; MCP-Teil wie
    // openfang-api/tests/integrations_remote_test.rs (initialize/tools/list, 202 auf notifications).
    // Liefert (base_url, Zaehler{token_refresh_calls}).

    #[tokio::test(flavor = "multi_thread")]
    async fn login_connect_refresh_logout_cycle() {
        let (base, counters) = start_mock_oauth_and_mcp().await;
        let kernel = boot_with_oauth_template(&base).await;      // Vorlage "probe" mit [auth] type="oauth", installiert
        assert_eq!(kernel.extension_health.get_health("probe").unwrap().status.zustand(), "anmeldung_noetig");
        let url = kernel.oauth_start("probe").await.unwrap();
        let state = url_param(&url, "state");
        // Anbieter wuerde nach Login mit code zurueckleiten:
        kernel.oauth_callback("probe", Some("CODE-OK"), &state, None).await.unwrap();
        assert_eq!(kernel.extension_health.get_health("probe").unwrap().status.zustand(), "verbunden");
        // zweiter Callback mit demselben state: abgelehnt, Erfolg bleibt
        assert_eq!(kernel.oauth_callback("probe", Some("CODE-OK"), &state, None).await.unwrap_err(), "state ungueltig");
        assert_eq!(kernel.extension_health.get_health("probe").unwrap().status.zustand(), "verbunden");
        // erzwungene Erneuerung
        kernel.oauth_fresh_access_token("probe", Some("KANARIE-AT-1")).await.unwrap();
        assert_eq!(counters.refresh_calls(), 1);
        // Abmelden: Tresor-Eintrag weg, Zustand anmeldung_noetig
        kernel.oauth_logout("probe").await.unwrap();
        assert!(kernel.resolve_credential(&openfang_extensions::oauth_reference("probe")).is_none());
        assert_eq!(kernel.extension_health.get_health("probe").unwrap().status.zustand(), "anmeldung_noetig");
        kernel.shutdown();
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn two_concurrent_forced_refreshes_hit_the_provider_once() {
        let (base, counters) = start_mock_oauth_and_mcp().await;   // Anbieter rotiert Refresh-Token
        let kernel = boot_with_oauth_template(&base).await;
        let url = kernel.oauth_start("probe").await.unwrap();
        kernel.oauth_callback("probe", Some("CODE-OK"), &url_param(&url, "state"), None).await.unwrap();
        let k1 = kernel.clone(); let k2 = kernel.clone();
        let (a, b) = tokio::join!(k1.oauth_fresh_access_token("probe", Some("KANARIE-AT-1")), k2.oauth_fresh_access_token("probe", Some("KANARIE-AT-1")));
        assert!(a.is_ok() && b.is_ok());
        assert_eq!(counters.refresh_calls(), 1, "the second waiter must reuse the fresh token");
        kernel.shutdown();
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn failed_refresh_sets_login_required_without_retry() {
        let (base, counters) = start_mock_oauth_and_mcp_with_dead_refresh().await;   // refresh -> invalid_grant
        let kernel = boot_with_oauth_template(&base).await;
        let url = kernel.oauth_start("probe").await.unwrap();
        kernel.oauth_callback("probe", Some("CODE-OK"), &url_param(&url, "state"), None).await.unwrap();
        let err = kernel.oauth_fresh_access_token("probe", Some("KANARIE-AT-1")).await.unwrap_err();
        assert_eq!(err.zustand(), "anmeldung_noetig");
        assert_eq!(err.detail().as_deref(), Some("erneuerung fehlgeschlagen"));
        assert_eq!(counters.refresh_calls(), 1);
        kernel.shutdown();
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn vault_unavailable_fails_login_without_success() {
        let (base, _c) = start_mock_oauth_and_mcp().await;
        let kernel = boot_with_oauth_template_without_vault(&base).await;   // kein vault.enc -> store_in_vault scheitert
        let url = kernel.oauth_start("probe").await.unwrap();
        let err = kernel.oauth_callback("probe", Some("CODE-OK"), &url_param(&url, "state"), None).await.unwrap_err();
        assert_eq!(err, "tresor nicht verfuegbar");
        assert_ne!(kernel.extension_health.get_health("probe").unwrap().status.zustand(), "verbunden");
        kernel.shutdown();
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn installed_integration_with_invalid_folder_template_is_template_invalid() {
    // Ordner-Vorlage github mit GITHUB_PAT_TOKEN (catalog) -> ungueltig; integrations.toml installiert github
    let kernel = boot_with_invalid_github_override().await;
    let h = kernel.extension_health.get_health("github").expect("registered");
    assert_eq!(h.status.zustand(), "nicht_zugelassen");
    assert_eq!(h.last_error.as_deref(), Some("vorlage ungueltig"));
    assert!(kernel.mcp_connections.lock().await.iter().all(|c| c.name() != "github"));
    kernel.shutdown();
}
```

Die Hilfsfunktionen `start_mock_oauth_and_mcp`, `start_mock_oauth_and_mcp_with_dead_refresh`, `boot_with_oauth_template`, `boot_with_oauth_template_without_vault`, `boot_with_invalid_github_override` und `url_param` schreibt der Implementierer im Testmodul selbst:
- Der Mock-Anmeldeserver folgt `mcp_oauth::tests::mock_as` aus Task 2. Ein Zähler für Refresh-Aufrufe und eine Rotation des Refresh-Tokens kommen dazu.
- Der Mock-MCP-Server akzeptiert jedes `Bearer KANARIE-AT-*` und antwortet ohne Token mit `401` und `WWW-Authenticate`.
- Der Kernel bootet mit `tempfile`-Home, `template_dirs`, `integrations.toml` und `config.extensions.oauth_allow_loopback_http = true`.
- Für Tests **mit** Tresor wird `vault.enc` im Home vorher mit einem Testschlüssel initialisiert. Muster dafür sind die bestehenden Vault-Tests in `openfang-extensions/src/vault.rs`. Wenn der Tresor im Test-Home nicht ohne Keyring initialisierbar ist, liefert `CredentialVault` einen Test-Konstruktor, und das wird im Bericht dokumentiert.

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cargo test -j 2 -p openfang-kernel --features test-insecure-oauth --lib oauth_flow_tests`, außerdem `cargo test -j 2 -p openfang-kernel --lib installed_integration_with_invalid_folder_template`
Expected: Kompilierfehler, weil Methoden und Felder fehlen.

- [ ] **Step 3: Implementieren**

1. **Vault strikt.** Neue private Hilfsmethoden. `store_credential` (best-effort) wird für `INTEGRATION_` **nicht** benutzt.
   ```rust
   fn vault_put(&self, key: &str, value: zeroize::Zeroizing<String>) -> Result<(), ()> {
       self.credential_resolver.lock().unwrap_or_else(|e| e.into_inner()).store_in_vault(key, value).map_err(|_| ())
   }
   fn vault_del(&self, key: &str) { let _ = self.credential_resolver.lock().unwrap_or_else(|e| e.into_inner()).remove_from_vault(key); }
   fn oauth_record(&self, id: &str) -> Option<openfang_extensions::mcp_oauth::OAuthRecord> {
       self.resolve_credential(&openfang_extensions::oauth_reference(id)).and_then(|s| serde_json::from_str(&s).ok())
   }
   fn oauth_record_put(&self, id: &str, rec: &openfang_extensions::mcp_oauth::OAuthRecord) -> Result<(), ()> {
       let json = serde_json::to_string(rec).map_err(|_| ())?;
       self.vault_put(&openfang_extensions::oauth_reference(id), zeroize::Zeroizing::new(json))
   }
   ```
   `oauth_record` liest nur aus dem Tresor. `resolve_credential` fällt zwar auf env und `.env` zurück, aber ein `INTEGRATION_OAUTH_…` in der Umgebung wäre ein Fehler des Betreibers und wird ebenfalls geparst. Das ist akzeptiert und kommt in den Bericht.
2. **`oauth_start(id)`:**
   - Prüfen, dass die Integration installiert und zugelassen ist und ihre Vorlage `auth: Some(_)` hat. Sonst `Err("keine oauth-integration")`.
   - `rec` ist `oauth_record(id)`, oder neu über `discover(&self.oauth_http, &self.oauth_policy(), url)` aus `transport.url`.
   - `redirect = callback_url(&self.config.api_listen, id)`. Fehlt `rec.client_id`, folgt `register_client(…, &redirect)`.
   - `rec` mit `oauth_record_put` speichern. Bei Fehlern **ohne** Tokens gibt es `Err("tresor nicht verfuegbar")`.
   - `pk = pkce_pair()`, `st = random_token()`, dann `oauth_pending.insert(&st, PendingLogin{…, redirect_uri: redirect.clone()})`.
   - `Ok(authorize_url(&rec, &redirect, &st, &pk.challenge))`. Alle Fehler gehen über `e.class()` nach `Err`.
   - Audit `integration_oauth=<id> ereignis=anmeldung_start ergebnis=<ok|klasse>`.
3. **`oauth_callback(id, code, state, error)`:**
   - `login = oauth_pending.take(state, id, LOGIN_TTL)`. Ist das `None`, folgt `Err("state ungueltig")`.
   - Ist `error.is_some()`, folgt `Err("anmeldung abgebrochen")`. Fehlt `code`, ebenfalls `Err("anmeldung abgebrochen")`.
   - `rec = oauth_record(id)`. Fehlt er, folgt `Err("state ungueltig")`.
   - `exchange_code(…, code, &login.verifier, &login.redirect_uri)`. Danach `oauth_record_put`; bei Fehler `Err("tresor nicht verfuegbar")`.
   - Dann `reconnect_extension_mcp(id).await`. Wenn die Integration noch nicht in `effective_mcp_servers` steht, stattdessen `reload_extension_mcps`.
   - Audit `ereignis=anmeldung ergebnis=<ok|klasse>`.
4. **`oauth_fresh_access_token(id, force)`:**
   ```rust
   let lock = self.oauth_locks.for_integration(id);
   let _g = lock.lock().await;
   let Some(mut rec) = self.oauth_record(id) else { return Err(IntegrationStatus::LoginRequired("nie angemeldet".into())) };
   if !rec.has_tokens() { return Err(IntegrationStatus::LoginRequired("nie angemeldet".into())); }
   let before = rec.access_token.clone();
   // force: nur erneuern, wenn niemand anders es waehrend des Wartens getan hat
   let fresh_by_other = force && self.oauth_refreshed_since(id, &before);
   if !fresh_by_other && (force || rec.needs_refresh(chrono::Utc::now())) {
       match openfang_extensions::mcp_oauth::refresh(&self.oauth_http, &self.oauth_policy(), &mut rec).await {
           Ok(()) => { if self.oauth_record_put(id, &rec).is_err() { return Err(IntegrationStatus::Unreachable("tresor nicht verfuegbar".into())) }
                       self.audit_oauth(id, "erneuerung", "ok"); }
           Err(e) => { self.audit_oauth(id, "erneuerung", &e.class());
                       let st = match e { OAuthError::Refresh(RefreshFailure::InvalidGrant) => IntegrationStatus::LoginRequired("erneuerung fehlgeschlagen".into()),
                                          other => IntegrationStatus::Unreachable(other.class()) };
                       self.extension_health.report_status(id, st.clone()); return Err(st); }
       }
   }
   rec.access_token.map(zeroize::Zeroizing::new).ok_or(IntegrationStatus::LoginRequired("nie angemeldet".into()))
   ```
   **Deduplizierung:** Der Aufrufer gibt bei `force = true` das Token mit, das abgelehnt wurde. Steht im Tresor schon ein **anderes** Token, wird nicht erneut erneuert. Umsetzung: Die Signatur wird erweitert zu `oauth_fresh_access_token(id, force: Option<&str /*abgelehntes Token*/>)`. Bei `Some(t)` wird nur erneuert, wenn `rec.access_token.as_deref() == Some(t)`. Im Test oben übergeben beide parallelen Aufrufe das gleiche Token. Der erste erneuert, der zweite sieht danach ein neues Token und erneuert nicht. Der Test wird entsprechend formuliert (`force` ist das Token aus dem Callback). Der Hilfsaufruf `oauth_refreshed_since` entfällt damit. **Die Signatur mit `Option<&str>` ist verbindlich**, die Bool-Variante oben dient nur der Lesbarkeit.
5. **`connect_one_mcp`**, vor `build_runtime_config`: Ist `server_config.oauth`, dann gilt:
   ```rust
   let token = match self.oauth_fresh_access_token(&server_config.name, None).await {
       Ok(t) => t,
       Err(status) => { let shown = status.detail().unwrap_or_default(); self.extension_health.report_status(&server_config.name, status); return Err(shown); }
   };
   let oauth_ref = openfang_extensions::oauth_reference(&server_config.name);
   let resolve = |k: &str| if k == oauth_ref { Some(token.clone()) } else { self.resolve_credential(k).map(zeroize::Zeroizing::new) };
   ```
   Für Nicht-OAuth-Einträge bleibt `resolve` unverändert. **Wichtig:** Der rohe JSON-Eintrag `INTEGRATION_OAUTH_…` darf nie als Header-Wert landen. Bei `oauth = true` liefert der Resolver für `oauth_ref` daher **ausschließlich** das Access-Token.
6. **`oauth_logout(id)`:**
   - Unter `mcp_lifecycle` die Verbindung trennen, über `remove_mcp_connections_and_rebuild_cache(&[id])` und `integration_servers` entfernen.
   - Ist `rec = oauth_record(id)` vorhanden, folgt `revoke(...)`. Ein Fehler beim Widerruf landet nur im Audit, nicht in `Err`.
   - Danach `vault_del(oauth_reference(id))` und `report_status(id, LoginRequired("abgemeldet"))`.
   - Audit `ereignis=abmeldung ergebnis=…`.
7. **`remove_integration(id)`:** Ist die Vorlage eine OAuth-Vorlage, werden nach dem Trennen und vor dem Deinstallieren `revoke` und `vault_del` ausgeführt, genau wie in Schritt 6, ohne neuen Lock.
8. **Einmal-Link:**
   - `schluessel_link(id)`: Die Integration muss eine Vorlage mit genau einer oder mehreren `auth_headers` haben. Die Referenz ist die erste `auth_headers[0].credential` und muss mit `INTEGRATION_` beginnen, sonst `Err`. Danach `token = random_token()`, `oauth_links.insert`, und das Token zurückgeben.
   - `schluessel_peek`: entspricht `oauth_links.peek(token, id, LOGIN_TTL)`.
   - `schluessel_store`:
     - `reference = oauth_links.take(...)`, sonst `Err("link ungueltig")`.
     - `validate_static_key(value)`, sonst `Err("wert ungueltig")`.
     - `vault_put(reference, value)`, sonst `Err("tresor nicht verfuegbar")`.
     - Dann `reconnect_extension_mcp(id)`, bzw. `reload_extension_mcps`, falls die Integration nicht verbunden ist.
     - Audit `ereignis=schluessel_gespeichert referenz=<reference>`.
9. **Boot und Reload, `TemplateInvalid`:** An denselben Stellen, an denen Teilprojekt 1 `NotAdmitted` meldet, für jede installierte id in `registry.invalid_overrides()` zusätzlich `extension_health.register(id)` und `report_status(id, IntegrationStatus::TemplateInvalid)` aufrufen.
10. **Audit-Hilfe:**
    ```rust
    fn audit_oauth(&self, id: &str, ereignis: &str, ergebnis: &str) {
        self.audit_log.record("system".to_string(), openfang_runtime::audit::AuditAction::ConfigChange,
            format!("integration_oauth={id} ereignis={ereignis} ergebnis={ergebnis}"), "ok");
    }
    ```
    `ergebnis` ist immer `"ok"` oder eine `class()`. Es enthält nie einen Wert.
11. **Status nach dem Installieren:** Eine OAuth-Integration ohne Eintrag zeigt nach `add` den Zustand `anmeldung_noetig`. Das ergibt sich aus Schritt 5, weil `connect_one_mcp` mit `LoginRequired` zurückkommt.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run:
- `cargo test -j 2 -p openfang-kernel --features test-insecure-oauth --lib`
- `cargo test -j 2 -p openfang-kernel --lib`, damit auch die Tests ohne Feature laufen
- `cargo check -j 2 --workspace --tests`

Expected: Alle neuen Tests und alle bestehenden Kernel-Tests sind grün.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-kernel/src/kernel.rs crates/openfang-kernel/Cargo.toml crates/openfang-types/src/config.rs
git commit -m "feat(kernel): OAuth-Anmeldung, frisches Token mit Sperre, Abmelden mit Widerruf, Einmal-Links, keine stille Rueckfall-Vorlage"
```

---

### Task 5: Erneuerung bei 401 im Werkzeugaufruf — einmal erneuern, einmal wiederholen

**Files:**
- Modify: `crates/openfang-runtime/src/kernel_handle.rs`, `crates/openfang-runtime/src/tool_runner.rs` (MCP-Zweig, ~`Fallback 1`)
- Modify: `crates/openfang-kernel/src/kernel.rs` (`impl KernelHandle`)
- Test: `tool_runner.rs`-Testmodul (`RecordingKernel` aus Teilprojekt 1 wiederverwenden)

**Interfaces:**
- Produces:
  - Trait-Methode `async fn refresh_integration_after_rejection(&self, tool_name: &str) -> bool { false }` (Standard). Der Kernel liefert `true`, wenn die Integration eine OAuth-Integration ist (`Owner::Single(id)`), das Token erneuert wurde und die Verbindung neu aufgebaut ist.

- [ ] **Step 1: Failing tests schreiben**

```rust
#[tokio::test]
async fn key_rejected_integration_call_is_retried_once_after_successful_refresh() {
    // RecordingKernel erweitert: refresh_integration_after_rejection zaehlt Aufrufe und liefert true.
    // mcp_connections: Some(leer) -> Aufruf scheitert mit "Invalid MCP tool name" (kein 401) -> KEIN Refresh.
    let rec = std::sync::Arc::new(RecordingKernel::with_refresh(true));
    let k: std::sync::Arc<dyn KernelHandle> = rec.clone();
    let conns = tokio::sync::Mutex::new(Vec::new());
    let _ = execute_tool("t", "mcp_github_get_me", &serde_json::json!({}), Some(&k), None, Some("a"),
        None, Some(&conns), None, None, None, None, None, None, None, None, None).await;
    assert_eq!(rec.refresh_calls(), 0, "non-auth errors never trigger a refresh");
}

#[test]
fn should_refresh_only_on_key_rejected_class() {
    assert!(should_refresh_after_error("MCP tool call failed: HTTP 401 Unauthorized: x"));
    assert!(!should_refresh_after_error("MCP tool call failed: dns error"));
    assert!(!should_refresh_after_error("Invalid MCP tool name: mcp_x"));
}
```

Ein echter 401-Wiederholungsfall braucht eine lebende Verbindung und wird in Task 6 Ende-zu-Ende geprüft.

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cargo test -j 2 -p openfang-runtime --lib tool_runner`
Expected: Kompilierfehler.

- [ ] **Step 3: Implementieren**

- Neue freie Funktion `fn should_refresh_after_error(err: &str) -> bool { mcp::classify_connect_error(err) == mcp::ConnectErrorClass::KeyRejected }`.
- Den Block im MCP-Zweig so umbauen, dass der Aufruf als eigene async-Hilfsfunktion `dispatch_mcp(mcp_connections, other, input) -> Result<String,String>` läuft. Diese Funktion nimmt den Lock und gibt ihn danach wieder frei.
  ```rust
  let mut mcp_result = dispatch_mcp(mcp_connections, other, input).await;
  if let (Some(kh), Err(e)) = (kernel, &mcp_result) {
      if kh.is_integration_tool(other) && should_refresh_after_error(e) && kh.refresh_integration_after_rejection(other).await {
          mcp_result = dispatch_mcp(mcp_connections, other, input).await;   // genau eine Wiederholung
      }
  }
  // danach unveraendert: report_integration_call_error (nur wenn immer noch Err) + genau eine Audit-Zeile
  ```
  Während `refresh_integration_after_rejection` läuft, darf **kein** Lock auf `mcp_connections` gehalten werden, denn der Kernel verbindet neu.
- Kernel-Implementierung:
  ```rust
  async fn refresh_integration_after_rejection(&self, tool_name: &str) -> bool {
      let crate::integrations::Owner::Single(id) = self.integration_owner(tool_name) else { return false };
      let is_oauth = self.effective_mcp_servers.read().unwrap_or_else(|e| e.into_inner()).iter().any(|s| s.name == id && s.oauth);
      if !is_oauth { return false; }
      let Some(me) = self.self_arc() else { return false };   // vorhandenen Self-Handle nutzen (set_self_handle)
      let rejected = me.oauth_record(&id).and_then(|r| r.access_token);
      if me.oauth_fresh_access_token(&id, rejected.as_deref()).await.is_err() { return false; }
      me.reconnect_extension_mcp(&id).await.is_ok()
  }
  ```
  `self_arc()` meint den Mechanismus, den `set_self_handle` heute schon bereitstellt. Der Implementierer verwendet den tatsächlichen Namen und dokumentiert ihn.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run: `cargo test -j 2 -p openfang-runtime --lib`, danach `cargo check -j 2 --workspace --tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-runtime/src/kernel_handle.rs crates/openfang-runtime/src/tool_runner.rs crates/openfang-kernel/src/kernel.rs
git commit -m "feat(runtime): bei abgelehntem Schluessel einmal erneuern und einmal wiederholen"
```

---

### Task 6: API — OAuth-Routen, Einmal-Link-Seite, Loopback-Ausnahmen, `INTEGRATION_`-Sperre, Ende-zu-Ende

**Files:**
- Modify: `crates/openfang-api/src/routes.rs`, `crates/openfang-api/src/server.rs` (Routen), `crates/openfang-api/src/middleware.rs`
- Modify: `crates/openfang-api/Cargo.toml`. Feature `test-insecure-oauth = ["openfang-kernel/test-insecure-oauth"]`; die Tests laufen mit `--features test-insecure-oauth`.
- Create: `crates/openfang-api/tests/integrations_oauth_test.rs`

**Interfaces:**
- Produces:
  - `POST /api/integrations/{id}/oauth/start` (Bearer) gibt `200 {"anmelde_url": "…"}` zurück, sonst `409 {"error": "<klasse>"}`.
  - `GET /api/integrations/{id}/oauth/callback?code&state&error`: nur über Loopback, ohne Bearer. Antwort ist `200 text/html` mit „Anmeldung erfolgreich" oder `400 text/html` mit „Anmeldung fehlgeschlagen: <klasse>".
  - `POST /api/integrations/{id}/oauth/abmelden` (Bearer) gibt `200 {"status":"abgemeldet"}` zurück.
  - `POST /api/integrations/{id}/schluessel/link` (Bearer) gibt `200 {"url": "http://127.0.0.1:<port>/api/integrations/{id}/schluessel/<token>"}` zurück.
  - `GET /api/integrations/{id}/schluessel/{token}`: nur über Loopback. Bei gültigem Token ist das Ergebnis `200 text/html` mit dem Formular, sonst `404`.
  - `POST /api/integrations/{id}/schluessel/{token}`: nur über Loopback, `application/x-www-form-urlencoded` mit Feld `wert`. Antwort ist `200 text/html` mit „gespeichert" oder `400 text/html` mit der Klasse.
  - Alle HTML-Antworten tragen die Header `Cache-Control: no-store`, `Referrer-Policy: no-referrer` und `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'`.
  - `/api/credentials/issue` mit `INTEGRATION_*` liefert `404 {"error":"credential_unavailable"}`. Das passiert direkt nach der Formprüfung, vor jedem Log mit der Referenz und vor dem Blick auf die Ausgabeliste.
  - `/api/credentials/store` mit `INTEGRATION_*` liefert `400 {"error":"reference_invalid"}`.

- [ ] **Step 1: Failing tests schreiben** (`integrations_oauth_test.rs`, `#![cfg(feature = "test-insecure-oauth")]`)

```rust
// Aufbau wie integrations_remote_test.rs (Harness mit Kernel + echtem Router aus server.rs, damit Middleware greift),
// plus Mock-Anmeldeserver und Mock-MCP nach Muster aus Task 4.
// Der Kernel bekommt config.api_listen = Adresse des Test-Routers (vor dem Boot gebunden) und
// extensions.oauth_allow_loopback_http = true.

#[tokio::test(flavor = "multi_thread")]
async fn oauth_full_cycle_over_http_and_no_token_leaks() {
    let h = harness_with_oauth_probe().await;
    let (s, j) = post(&h, "/api/integrations/add", json!({"id":"probe"})).await;
    assert_eq!(s, 201); assert_eq!(j["zustand"], "anmeldung_noetig");
    let (s, j) = post(&h, "/api/integrations/probe/oauth/start", json!({})).await;
    assert_eq!(s, 200);
    let url = j["anmelde_url"].as_str().unwrap().to_string();
    // "Browser": der Mock-Anmeldeserver leitet sofort mit code=CODE-OK an redirect_uri weiter
    let page = follow_login_redirect(&url).await;           // GET authorize -> 302 -> GET callback (Loopback)
    assert!(page.contains("Anmeldung erfolgreich"));
    assert_eq!(get_json(&h, "/api/integrations").await["installed"].as_array().unwrap()
        .iter().find(|e| e["id"]=="probe").unwrap()["zustand"], "verbunden");
    // zweiter Callback mit gleichem state
    let again = get_text(&h, &callback_path_from(&url, "CODE-OK")).await;
    assert!(again.0 == 400 && again.1.contains("state ungueltig"));
    for p in ["/api/integrations", "/api/integrations/health", "/api/config", "/api/audit/recent?n=200"] {
        let b = get_text(&h, p).await.1;
        for c in ["KANARIE-AT-1", "KANARIE-AT-2", "KANARIE-RT-1", "KANARIE-CLIENT", "CODE-OK"] {
            assert!(!b.contains(c), "{p} leaks {c}");
        }
    }
    let (s, _) = post(&h, "/api/integrations/probe/oauth/abmelden", json!({})).await;
    assert_eq!(s, 200);
}

#[tokio::test(flavor = "multi_thread")]
async fn integration_prefix_is_never_issuable_or_storable() {
    let h = harness_with_issue_key().await;     // Bearer + OPENFANG_ISSUE_KEY wie in api_integration_test (start_credential_test_server_with_keys)
    std::fs::write(h.home().join("issuable_credentials.list"), "INTEGRATION_GITHUB_PAT\n").unwrap();
    let (s, j) = issue(&h, "INTEGRATION_GITHUB_PAT").await;
    assert_eq!(s, 404); assert_eq!(j["error"], "credential_unavailable");
    let (s, j) = store(&h, "INTEGRATION_GITHUB_PAT", "KANARIE-STORE").await;
    assert_eq!(s, 400); assert_eq!(j["error"], "reference_invalid");
}

#[tokio::test(flavor = "multi_thread")]
async fn one_time_key_page_stores_once_and_never_echoes() {
    let h = harness_with_static_probe("INTEGRATION_PROBE_KEY").await;   // Vorlage mit auth_headers -> INTEGRATION_PROBE_KEY, installiert
    let (_, j) = post(&h, "/api/integrations/probe/schluessel/link", json!({})).await;
    let url = j["url"].as_str().unwrap().to_string();
    let (s, form, headers) = get_page(&url).await;
    assert_eq!(s, 200); assert!(form.contains("type=\"password\""));
    assert_eq!(headers["cache-control"], "no-store");
    let (s, body) = post_form(&url, &[("wert", "KANARIE-STATIC-9")]).await;
    assert_eq!(s, 200); assert!(body.contains("gespeichert")); assert!(!body.contains("KANARIE-STATIC-9"));
    let (s, _) = post_form(&url, &[("wert", "noch einmal")]).await;
    assert_eq!(s, 400, "single use");
    for p in ["/api/integrations", "/api/integrations/health", "/api/config", "/api/audit/recent?n=200"] {
        assert!(!get_text(&h, p).await.1.contains("KANARIE-STATIC-9"));
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn browser_routes_refuse_non_loopback_peers() {
    // Router mit ConnectInfo einer Nicht-Loopback-Adresse aufrufen (tower::ServiceExt::oneshot mit
    // Request-Extension ConnectInfo(SocketAddr 100.67.177.45:5555)) — Muster: middleware.rs Tests (req_from).
    let app = router_for_tests().await;
    for path in ["/api/integrations/probe/oauth/callback?state=x&code=y", "/api/integrations/probe/schluessel/abc"] {
        let resp = oneshot_from(&app, "100.67.177.45:5555", "GET", path).await;
        assert!(resp.status() == 401 || resp.status() == 404, "{path}: {}", resp.status());
        assert!(!body_text(resp).await.contains("password"));
    }
}
```

Die Hilfsfunktionen wie `harness_*`, `post`, `get_text`, `follow_login_redirect` und `oneshot_from` schreibt der Implementierer nach den Mustern in `integrations_remote_test.rs`, `api_integration_test.rs` und `middleware.rs` (`req_from`). Mit `reqwest` wird `follow_login_redirect` über `redirect::Policy::limited(2)` umgesetzt: Der Mock-`/authorize` antwortet mit `302 Location: {redirect_uri}?code=CODE-OK&state={state}`.

- [ ] **Step 2: Laufen lassen, erwartet: FAIL**

Run: `cargo test -j 2 -p openfang-api --features test-insecure-oauth --test integrations_oauth_test`
Expected: FAIL, weil Routen und Sperre fehlen.

- [ ] **Step 3: Implementieren**

1. **`middleware.rs`.** Vor der `is_public`-Berechnung ergänzen:
   ```rust
   let is_browser_integration_path = path.starts_with("/api/integrations/")
       && (path.contains("/oauth/callback") || path.contains("/schluessel/"))
       && !path.ends_with("/schluessel/link");
   if is_browser_integration_path {
       if is_loopback { return next.run(request).await; }
       return (StatusCode::NOT_FOUND, "").into_response();
   }
   ```
   Den Stil und die Rückgabeform übernimmt der Implementierer von der bestehenden Middleware.
2. **Handler.** Jeder Handler prüft die Loopback-Bedingung zusätzlich selbst über `ConnectInfo<SocketAddr>` (Defence in Depth). HTML wird mit festen Texten gebaut, nur die Fehlerklasse wird eingesetzt. Diese besteht immer aus Kleinbuchstaben, Ziffern, Leerzeichen, `()` und `:`. Trotzdem wird HTML-escaped.
3. **`issue_credential`:** Direkt nach `is_valid_credential_reference` folgt `if reference.starts_with(openfang_extensions::INTEGRATION_PREFIX) { return credential_response(StatusCode::NOT_FOUND, json!({"error":"credential_unavailable"})); }`.
4. **`store_credential`:** Direkt nach der Referenzprüfung folgt die Antwort `reference_invalid` für die Vorsilbe.
5. **`server.rs`:** Die neuen Routen eintragen.

- [ ] **Step 4: Laufen lassen, erwartet: PASS**

Run:
- `cargo test -j 2 -p openfang-api --features test-insecure-oauth --test integrations_oauth_test`
- `cargo test -j 2 -p openfang-api --test integrations_remote_test`
- `cargo test -j 2 -p openfang-api --test api_integration_test`
- `cargo check -j 2 --workspace --tests`

Expected: Die neuen Tests sind grün, und die bestehenden bleiben im bisherigen Stand.

- [ ] **Step 5: Commit**

```bash
git add crates/openfang-api/src/routes.rs crates/openfang-api/src/server.rs crates/openfang-api/src/middleware.rs crates/openfang-api/Cargo.toml crates/openfang-api/tests/integrations_oauth_test.rs
git commit -m "feat(api): OAuth-Anmeldung, Einmal-Schluesselseite nur ueber Loopback, INTEGRATION_ nie ausgebbar"
```

---

### Task 7: Pilot Vercel und GitHub-Umstellung — Vorlagen, Hub-Agent, Build, Rollout, Live-Nachweis (FREIGABE nötig)

**Files:**
- Create: `vibemind-os/integrations/vercel.toml` (Worktree `wt-os-pins`)
- Modify: `vibemind-os/integrations/github.toml`, das `credential = "INTEGRATION_GITHUB_PAT"` erhält
- Modify: `agents/integrations-hub/agent.toml` (openfang-Repo), das `mcp_servers = ["github", "vercel"]` erhält

**Interfaces:**
- Consumes: der gebaute Daemon aus Tasks 1–6.

- [ ] **Step 1: Vorlagen anlegen und umstellen** (Worktree `wt-os-pins`)

`integrations/vercel.toml`:

```toml
id = "vercel"
name = "Vercel"
description = "Vercel ueber den offiziellen Remote-MCP-Server"
category = "devtools"
icon = "▲"
tags = ["deploy", "hosting", "projects"]
read_only_tools = []

[transport]
type = "http"
url = "https://mcp.vercel.com/"

[auth]
type = "oauth"
scopes = ["offline_access"]

[catalog]
replaces_openai_plugin = "vercel"
license = "proprietary-service"
admission = "admitted"
```

`read_only_tools` ist absichtlich leer, alles braucht also eine Freigabe. Gefüllt wird die Liste in Step 5.3, sobald das Live-`tools/list` vorliegt.

In `integrations/github.toml` wird `credential = "GITHUB_PAT_TOKEN"` ersetzt durch `credential = "INTEGRATION_GITHUB_PAT"`. Den Kommentar und das `required_env`-`name` auf dieselbe Referenz anpassen.

Commit: `feat(integrations): Vercel-Vorlage (OAuth) und GitHub auf INTEGRATION_GITHUB_PAT`. Noch nicht pushen.

- [ ] **Step 2: Hub-Agent und Build** (openfang-Worktree)

- `agents/integrations-hub/agent.toml` bekommt `mcp_servers = ["github", "vercel"]`.
- RAM messen, dann `cargo build --release -j 2 -p openfang-cli`, **ohne** das Feature `test-insecure-oauth`.
- `cargo test -j 2 --workspace` einmal laufen lassen und die Summen festhalten.
- Commit `feat(pilot): integrations-hub bekommt vercel`, nicht pushen.

- [ ] **Step 3: STOP — Freigabe des Users für das Ausrollen einholen**

Dieser Schritt wechselt Binary und Pin und startet `:4200` neu. GitHub ist danach vorübergehend `fehlt_schluessel`.

- [ ] **Step 4: Ausrollen.** Die Reihenfolge ist Pflicht.

1. Claims eintragen: WORKBOARD und Koordination.
2. openfang pushen mit `HEAD:claude/openfang-fork-reconciliation-v1`. Vorher Fast-Forward und `ls-remote` prüfen.
3. vibemind-os: die Vorlagen-Commits plus einen Pin-Bump auf den neuen openfang-SHA, mit `update-index --cacheinfo` nach `cat-file -t` im openfang-Repo, dann pushen.
4. Haupt-Checkout:
   - Das openfang-Submodul auf den SHA setzen.
   - `vibemind-os/integrations/github.toml` **und** `vercel.toml` aus dem neuen master-Stand kopieren, **vor** dem Neustart.
   - `agents/integrations-hub/agent.toml` nach `~/.openfang/agents/integrations-hub/` kopieren.
5. Neustart:
   - Watchdog-Task anhalten, `/api/shutdown`, warten.
   - `openfang.exe` sichern als `openfang.exe.vor-oauth` und das neue Binary einsetzen.
   - Watchdog-Task starten und auf `/api/health` warten.
6. Boot-Log prüfen:
   - `"2 Vorlagen aus Ordnern geladen"`, keine übersprungene Datei, kein unlesbarer Ordner.
   - `github` steht auf `fehlt_schluessel` mit `INTEGRATION_GITHUB_PAT`, **nicht** auf stdio.

- [ ] **Step 5: Live-Nachweis** (Spec §7)

1. `POST /api/integrations/add {"id":"vercel"}` muss `zustand = "anmeldung_noetig"` liefern.
2. `POST /api/integrations/vercel/oauth/start` liefert die `anmelde_url`. **Der User** öffnet sie und meldet sich an. Erwartet: die Seite „Anmeldung erfolgreich" und der Zustand `verbunden`.
   - Lehnt Vercel die Selbstregistrierung ab (`registrierung abgelehnt (http 4xx)`), wird das als Befund festgehalten. Der Pilot wechselt dann auf Notion oder monday, mit eigener Freigabe.
3. `tools/list` als `integrations-hub` abrufen.
   - Die lesenden Werkzeuge nach Namen und Beschreibung bestimmen (z. B. `list_*`, `get_*`, `search_*`) und in `vercel.toml` als `read_only_tools` eintragen. Commit, `reload`.
   - Ein lesender Aufruf läuft ohne Freigabe.
   - Ein schreibendes Werkzeug, etwa ein Deploy, erzeugt eine Freigabe, die abgelehnt wird.
4. Erzwungene Erneuerung über `oauth_fresh_access_token("vercel", Some(<aktuelles Token>))`. Dafür gibt es einen kleinen Test-Hilfsaufruf **nur über Loopback und mit Bearer**, oder direkt im Daemon über das Audit beim nächsten 401. Fehlt ein Live-Hebel, wird der Schritt als „nicht live erzwungen, im Ende-zu-Ende-Test bewiesen" festgehalten. Danach funktioniert der lesende Aufruf weiter.
5. GitHub umstellen:
   - `POST /api/integrations/github/schluessel/link` liefert die URL. **Der User** trägt den fein granulierten PAT ein.
   - Danach `verbunden`, und `get_me` liefert `Flissel`.
   - `/api/credentials/issue` mit Bearer und Issue-Key für `INTEGRATION_GITHUB_PAT` liefert `404`.
6. Leck-Prüfung auf `github_pat_`, `ghp_`, `vca_` und `vcr_` (Vercel-Token-Präfixe) plus die Werte. Geprüft werden:
   - `logs/openfang/*`
   - `/api/config`
   - `/api/integrations*`
   - `/api/audit/recent`
   - `/api/approvals`

   Erwartet: 0 Treffer.
7. `POST /api/integrations/vercel/oauth/abmelden`. Erwartet:
   - Das Audit zeigt `ereignis=abmeldung`.
   - Der Tresor-Eintrag ist weg.
   - Der Zustand ist `anmeldung_noetig`.

   Danach erneut anmelden, damit Vercel verbunden bleibt. Das geht nur, wenn der User zustimmt.

- [ ] **Step 6: Abschluss**

- Claims schließen.
- `E2E-PROOF.md` um „Teil IX: OAuth-Integrationen (Vercel) und nicht ausgebbare Schlüssel" ergänzen.
- Projektnotiz `project_openfang_integrationen_kern.md` aktualisieren.
