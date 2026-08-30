import { describe, expect, it } from "vitest";
import { classifyPluginOperation } from "@/src/application/services/plugin-operation-classifier";

const component = { id: "mcp:.mcp.json#github", name: "github", kind: "mcp", status: "available", metadata: { digest: "a".repeat(64), bindingDigest: "b".repeat(64) } };

describe("plugin operation classification", () => {
  it("classifies an operation the component declares read-only as read", () => {
    const readOnly = { ...component, metadata: { ...component.metadata, readOnlyOperations: ["list_issues", "search"] } };
    expect(classifyPluginOperation({ pluginName: "github", component: readOnly, operationName: "list_issues" })).toBe("read");
    expect(classifyPluginOperation({ pluginName: "github", component: readOnly, operationName: "create_issue" })).toBe("write");
  });

  it("classifies everything else as write", () => {
    expect(classifyPluginOperation({ pluginName: "github", component, operationName: "list_issues" })).toBe("write");
    expect(classifyPluginOperation({ pluginName: "github", component, operationName: "anything" })).toBe("write");
  });

  it("ignores an unreadable declaration rather than trusting it", () => {
    for (const declared of ["list_issues", { list_issues: true }, [1, 2], null]) {
      const broken = { ...component, metadata: { ...component.metadata, readOnlyOperations: declared } };
      expect(classifyPluginOperation({ pluginName: "github", component: broken, operationName: "list_issues" })).toBe("write");
    }
  });
});
