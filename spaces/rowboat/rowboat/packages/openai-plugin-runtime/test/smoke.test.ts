import { describe, expect, it } from "vitest";
import {
  PluginManifestValidationError,
  RUNTIME_SCHEMA_VERSION,
} from "../src/index.js";

describe("openai-plugin-runtime", () => {
  it("publishes a versioned schema boundary", () => {
    expect(RUNTIME_SCHEMA_VERSION).toBe("rowboat-openai-plugin-runtime-v1");
  });

  it("publishes structured manifest validation errors", () => {
    expect(PluginManifestValidationError).toBeTypeOf("function");
  });
});
