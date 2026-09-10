/**
 * The one place a plugin credential *reference* becomes the *name* OpenFang
 * actually resolves it by. Every consumer that needs to say "this is the
 * credential you'll be asked for" -- the OpenFang credential resolver, the
 * connector-bridge provider, and Rowboat's own "what does this plugin need"
 * install-dialog copy -- reads these functions rather than re-deriving the
 * same shape, so the three can never quietly drift apart.
 */

const OPENFANG_REFERENCE = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
const CONNECTOR_NAME_INVALID = /[^A-Z0-9]+/g;

/** The literal environment-credential reference the connector bridge asks for
 *  alongside every per-app connector token: OpenAI's own API key. */
export const OPENAI_API_KEY_CREDENTIAL_REFERENCE = "OPENAI_API_KEY" as const;

/**
 * OpenFang resolves a credential BY NAME (vault -> dotenv -> env var) and its
 * issuance endpoint accepts only `^[A-Za-z_][A-Za-z0-9_]{0,127}$`, so an
 * oauth-shaped reference must become an env-shaped name before it means
 * anything to OpenFang. An oauth reference is a resource URL; it
 * deterministically becomes `OAUTH_BEARER_` + host and path, uppercased,
 * every non-alphanumeric run collapsed to one underscore --
 * `https://mcp.linear.app/mcp` -> `OAUTH_BEARER_MCP_LINEAR_APP_MCP` -- which
 * is the exact name the operator provisions in OpenFang's secrets and
 * allowlists in `OPENFANG_ISSUABLE_CREDENTIALS`. A resource that cannot be
 * derived (not a URL, or a name that would exceed OpenFang's bound) yields
 * `undefined` so every caller fails closed rather than guessing a name.
 */
export function deriveOAuthBearerReference(resourceUrl: string): string | undefined {
  let resource: URL;
  try {
    resource = new URL(resourceUrl);
  } catch {
    return undefined;
  }
  const stem = `${resource.host}${resource.pathname}`
    .toUpperCase()
    .replace(/[^A-Z0-9]+/gu, "_")
    .replace(/^_+|_+$/gu, "");
  const derived = `OAUTH_BEARER_${stem}`;
  return OPENFANG_REFERENCE.test(derived) ? derived : undefined;
}

/**
 * `CONNECTOR_` + the app name uppercased, with runs of non-alphanumeric
 * characters collapsed to a single underscore (e.g. "canva" ->
 * "CONNECTOR_CANVA", "monday-com" -> "CONNECTOR_MONDAY_COM"). This is the
 * environment-credential reference that carries the connector's per-app
 * OAuth token, resolved through the same `CredentialResolver` as the OpenAI
 * API key -- never a standing secret inside Rowboat itself.
 */
export function deriveConnectorReference(appName: string): string {
  return `CONNECTOR_${appName.toUpperCase().replace(CONNECTOR_NAME_INVALID, "_")}`;
}
