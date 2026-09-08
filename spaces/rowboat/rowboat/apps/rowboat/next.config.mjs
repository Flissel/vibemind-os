import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const nextConfig = {
    output: 'standalone',
    // The plugin kernel lives outside this app (packages/openai-plugin-runtime,
    // pulled in as a `file:` dependency), so tracing must start at the repo
    // root or the standalone output would not carry it. With this set, the
    // standalone tree keeps the workspace layout: apps/rowboat/server.js next
    // to the traced node_modules.
    outputFileTracingRoot: join(dirname(fileURLToPath(import.meta.url)), '..', '..'),
    // Next pulls `sharp` in only for its image optimizer, and in the
    // standalone output that optimizer's bundled loader cannot find the @img
    // platform binary -- every project page answered 500 with "Could not load
    // the sharp module using the linux-x64 runtime" while a plain require of
    // the very same module succeeded inside the image. This is an internal
    // agent-builder UI: unoptimized images cost nothing here and remove the
    // dependency on that loader entirely.
    images: { unoptimized: true },
    serverExternalPackages: [
        'awilix',
        // Bundling sharp breaks its own resolution of the @img platform
        // binary at runtime ("Could not load the sharp module using the
        // linux-x64 runtime") even when a plain require of it succeeds in the
        // same image. Left external, node resolves it normally.
        'sharp',
    ],
};

export default nextConfig;
