# W2 — Connector-Bridge: Spezifikation

Datum 2026-09-03. Autorität für den Plan
`docs/superpowers/plans/2026-09-03-rowboat-plugins-w2-connector-bridge.md`.
Kontext: Master-Plan `2026-08-30-rowboat-plugin-full-integration-master.md`
(Workstream W2), Ops-Doc `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md`.

## Ziel

App-Komponenten des gepinnten Katalogs, deren `.app.json` eine
`connector_…`-ID trägt, werden über die OpenAI Responses API ausführbar —
unter denselben Regeln wie MCP-Komponenten: Admission, komponenten-genaue
Installation, Klassifizierer, OpenFang-Release pro Write, Credentials pro
Aufruf über den `CredentialResolver`, Receipt mit Approval-Id.

## Verifizierte Faktenlage (2026-09-03, nachgezählt)

- 156 App-Komponenten im Lock; `.app.json` trägt NUR eine opake ID
  (`^(?:connector|asdk_app|templated_apps)_[a-f0-9]+$`, Schema
  `component-schemas.ts:7-15`). Split am gepinnten Quell-Commit
  `openai/plugins@11c74d6b`: **125 `asdk_app_`, 30 `connector_`, 1
  `templated_apps_`**.
- **connector_ ∧ admitted = 15**: alpaca, amplitude, biorender, canva,
  daloopa, egnyte, fireflies, help-scout, intercom, jam, monday-com,
  pipedrive, semrush, sendgrid, teamwork-com. Weitere 14 connector_ in
  `review_required` (u. a. gmail, google-drive/-calendar, outlook-*, teams,
  sharepoint, github, vercel, figma).
- Responses API (aktuelle OpenAI-Doku, geprüft): `tools:[{type:"mcp",
  server_label, connector_id, authorization:"<oauth access token>",
  require_approval:"never", allowed_tools:[...]}]`; Output enthält
  `mcp_call`-Items. `allowed_tools` beschränkt die importierten Tools.
- Der Importer pinnt die Connector-ID heute NICHT ins Lock: App-Metadata =
  `{path, digest}` (`component-discovery.ts:331-339`), während die
  mcp-Branch ihre Deklaration bewusst inline pinnt („constructed from the
  pinned record alone", `:345-368`).
- Registry verweigert `openai-connector-bridge` unbedingt
  (`provider-registry.ts:135-137`); Runtime lässt `kind:"app"` bereits durch
  (`plugin-tool-runtime.ts:602-604`), Receipts akzeptieren `"app"`, Mongo
  akzeptiert den providerKind. Die Verweigerung sitzt allein in Registry +
  `provider-resolution.ts:63-64`.
- Alle 3 Prozess-MCP-Komponenten des Lock sind `rejected`.

## Entscheidungen

**D1 — Die Connector-ID wandert ins Lock (Katalog-Re-Sync, neuer Digest).**
Die App-Branch des Importers folgt demselben Prinzip wie die mcp-Branch:
`appDeclaration: definedFields(declaration.data)` in die Komponenten-Metadata,
damit der Provider allein aus dem gepinnten Record konstruierbar ist. Das
ändert die kanonischen Bytes des Lock → `PINNED_PLUGIN_CATALOG_DIGEST` wird
neu gepinnt (Quell-Commit `11c74d6b` bleibt DERSELBE). Verworfen:
Content-Store-Lesen zur Laufzeit (Web-Runtime mountet ihn nicht, kein
Einzeldatei-API, bricht das dokumentierte Prinzip) und eine separate
Mapping-Datei (Drift). Reproduzierbarkeit ist Gate: zwei Sync-Läufe müssen
byte-identisch sein.

**D2 — `ConnectorBridgeProvider` (Kernel).** Neuer `PluginProvider` für
Bindings `providerKind:"openai-connector-bridge"` mit `connector_…`-ID.
`invoke()` macht GENAU EINEN `POST {baseUrl}/v1/responses`-Call:
`tools:[{type:"mcp", server_label:<app.name>, connector_id,
authorization:<Connector-Token>, require_approval:"never",
allowed_tools:[<operationName>]}]`, `tool_choice:"required"`, `store:false`,
Input = deterministische Ein-Satz-Instruktion mit Operationsname +
JSON-Argumenten. Ergebnis = das `mcp_call`-Output-Item (`name ===
operationName`); fehlend oder Fehler → `{status:"failed",
reason:"provider_failed"}`. Policy-/Lizenz-Gate identisch zu
`HttpMcpProvider` (`evaluateComponentAdmission` mit `mcp_http` + `write` —
ein Connector-Call IST ein ferngesteuerter MCP-Call; keine neue
Policy-Lane).

**D3 — Zwei Credentials pro Aufruf, beide über den `CredentialResolver`.**
`{kind:"environment", reference:"OPENAI_API_KEY"}` für den API-Key und
`{kind:"environment", reference: deriveConnectorReference(app.name)}` mit
`CONNECTOR_` + Name uppercased, nicht-alphanumerische Läufe → `_` (z. B.
`CONNECTOR_CANVA`, `CONNECTOR_MONDAY_COM`) für das Connector-OAuth-Token.
Kein stehendes Secret in Rowboat; OpenFang-Allowlist + Ausgabe pro Aufruf,
exakt wie bei `GITHUB_PAT_TOKEN`. Auflösungsfehler → `credential_missing`
VOR jedem Netzwerk-I/O.

**D4 — `asdk_app_`/`templated_apps_` werden ehrlich verweigert.** Die
Resolution gibt für Nicht-`connector_`-IDs UNAVAILABLE zurück (kein
Konstruktionsversuch); der Provider-Konstruktor wirft zusätzlich
fail-closed. Es gibt für Apps-SDK-Apps keinen öffentlichen Aufrufpfad
außerhalb ChatGPTs — das wird dokumentiert, nicht approximiert.

**D5 — Prozess-MCP ist aus W2 GESTRICHEN.** Alle 3 Prozess-Komponenten sind
`rejected`; „ausführbar machen" hieße Admission überstimmen, was diese
Runtime nirgends tut. Der Content-Store-Mount gehört, wenn je ein
admitted Prozess-MCP existiert, zu W5 (Deployment). Master-Plan-Akzeptanz
wird entsprechend verengt (dokumentierte Kürzung wie der W3-Credential-Slot).

**D6 — E2E bis zu OpenAIs 401.** Workboard-Betriebseinschränkung: kein
OpenAI-API-Budget. Der Live-Beweis führt einen canva-Call durch Install →
Tool → Release → OpenFang-Approval → Ausgabe FAKE-generierter Werte für
beide Referenzen → realer POST an `https://api.openai.com/v1/responses` →
401 → `provider_failed`-Receipt mit Approval-Id. Ein echter Call (Cents)
ist eine spätere User-Entscheidung; der Testaufbau ist darauf vorbereitet.

**D7 — Konfiguration.** `OPENAI_RESPONSES_MODEL` (Default `"gpt-5.6"`, der in
der aktuellen Doku durchgängig verwendete Name) und `OPENAI_BASE_URL`
(Default `https://api.openai.com`) werden in der Composition gelesen;
`fetch` ist injizierbar (Tests). Timeout-Regime wie `HttpMcpProvider`
(`timeoutMilliseconds`, Default dort).

## Nicht-Ziele

Kein echter akzeptierter OpenAI-Call (D6-Gate). Kein Prozess-MCP (D5). Keine
UI-Änderung (App-Komponenten sind über die bestehende komponenten-genaue
Install-UI aus W3 bereits wähl- und bindbar). Kein Refresh-Design für
Connector-Tokens. Keine Änderung an OpenFang.

## Akzeptanz

1. Kernel- und App-Suiten grün, `tsc --noEmit` 0, Lock-Re-Sync
   byte-reproduzierbar, `plugins:catalog-load -- --verify-only` grün gegen
   den neuen Digest.
2. Unit-bewiesen: exakte Request-Form (gepinnt), mcp_call-Parsing, beide
   Credential-Auflösungen, Policy-Gate, asdk-Verweigerung, kein
   Secret-Leak über `JSON.stringify`.
3. Live-bewiesen (Part IV in `E2E-PROOF.md`): canva App-Komponente
   komponenten-genau installiert, Tool gebunden, Release + Approval, beide
   Referenzen ausgegeben, realer 401 von api.openai.com, Receipt
   `status=failed reason=provider_failed` mit Approval-Id und
   `componentKind:"app"`.
4. Ops-Doc: Bridge-Sektion (Referenznamen-Tabelle inkl. der 15
   CONNECTOR_*-Namen), asdk-Absatz, Prozess-MCP-Kürzung, neuer Digest
   überall; Master-Plan-W2-Zeile aktualisiert.
