import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths()],
  test: {
    environment: "node",
    // These suites spawn real CLI processes, hash real catalog content, and
    // import the agents runtime module graph. The 5s default made them fail
    // under parallel load with nothing actually wrong.
    testTimeout: 60_000,
  },
});
