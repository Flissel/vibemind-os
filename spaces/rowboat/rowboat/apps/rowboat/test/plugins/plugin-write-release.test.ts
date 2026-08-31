import { describe, expect, it } from "vitest";
import { DeniedWriteReleasePolicy, captureWriteReleaseRequest } from "@/src/application/policies/plugin-write-release.policy";

const request = Object.freeze({
  projectId: "11111111-1111-4111-8111-111111111111",
  pluginName: "github",
  toolName: "plugin_github_github",
  componentDigest: "a".repeat(64),
  argumentsDigest: "b".repeat(64),
});

describe("plugin write release", () => {
  it("denies when nothing is configured to release a write", async () => {
    const decision = await new DeniedWriteReleasePolicy().release(request, new AbortController().signal);
    expect(decision).toEqual({ status: "unavailable" });
  });

  it("captures only the fields a release decision may see", () => {
    expect(captureWriteReleaseRequest({ ...request, secret: "sk-live" } as never)).toEqual(request);
    expect(() => captureWriteReleaseRequest({ ...request, projectId: "nope" })).toThrow("write_release_request_invalid");
    expect(() => captureWriteReleaseRequest({ ...request, argumentsDigest: "short" })).toThrow("write_release_request_invalid");
  });
});
