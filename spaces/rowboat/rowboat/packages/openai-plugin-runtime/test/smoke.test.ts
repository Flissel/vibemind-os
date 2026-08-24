import { describe, expect, it } from "vitest";
import { RUNTIME_SCHEMA_VERSION } from "../src/index.js";

describe("openai-plugin-runtime", () => {
  it("publishes a versioned schema boundary", () => {
    expect(RUNTIME_SCHEMA_VERSION).toBe("rowboat-openai-plugin-runtime-v1");
  });
});
