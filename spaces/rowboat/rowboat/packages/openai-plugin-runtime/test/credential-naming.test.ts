import { describe, expect, it } from "vitest";
import {
  deriveConnectorReference,
  deriveOAuthBearerReference,
  resolveHttpMcpOauthResource,
} from "../src/index.js";

// `resolveHttpMcpOauthResource` is the one place that decides which resource
// an HTTP MCP server's oauth credential requirement resolves to. Both
// `normalizeMcpServer` (this package) and Rowboat's `requiredCredentialNames`
// (apps/rowboat) read it, so a change to the fallback rule can never land in
// only one of the two.

describe("resolveHttpMcpOauthResource", () => {
  it("prefers an explicit oauth_resource over the server's own url", () => {
    expect(resolveHttpMcpOauthResource({
      url: "https://example.invalid/mcp",
      oauthResource: "https://example.invalid/oauth",
    })).toBe("https://example.invalid/oauth");
  });

  it("yields no oauth resource at all when a bearer token reference is declared instead", () => {
    expect(resolveHttpMcpOauthResource({
      url: "https://example.invalid/mcp",
      bearerTokenEnvVar: "API_TOKEN",
    })).toBeUndefined();
  });

  it("falls back to the server's own url when neither is declared - 'declares nothing' must never mean 'needs nothing'", () => {
    expect(resolveHttpMcpOauthResource({
      url: "https://mcp.cloudflare.com/mcp",
    })).toBe("https://mcp.cloudflare.com/mcp");
  });

  it("still prefers the explicit oauth_resource even when a bearer reference is also present", () => {
    expect(resolveHttpMcpOauthResource({
      url: "https://example.invalid/mcp",
      oauthResource: "https://example.invalid/oauth",
      bearerTokenEnvVar: "API_TOKEN",
    })).toBe("https://example.invalid/oauth");
  });
});

describe("deriveOAuthBearerReference", () => {
  it("still derives the exact name OpenFang provisions, for a resolved resource", () => {
    const resource = resolveHttpMcpOauthResource({ url: "https://mcp.linear.app/mcp" });
    expect(resource).toBeDefined();
    expect(deriveOAuthBearerReference(resource as string)).toBe("OAUTH_BEARER_MCP_LINEAR_APP_MCP");
  });
});

describe("deriveConnectorReference", () => {
  it("still derives CONNECTOR_<APP>", () => {
    expect(deriveConnectorReference("canva")).toBe("CONNECTOR_CANVA");
  });
});
