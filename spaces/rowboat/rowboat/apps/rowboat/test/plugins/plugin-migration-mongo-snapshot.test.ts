import { describe, expect, it } from "vitest";
import { createMongoMigrationSnapshotReaders } from "@/src/application/services/plugin-migration-mongo-snapshot";

const projectId = "11111111-1111-4111-8111-111111111111"; const top = "2026-08-26T10:00:00.000Z";
const workflow = { lastUpdatedAt: "2026-08-26T09:59:59.000Z", tools: [], agents: [], prompts: [], pipelines: [], startAgent: "main" };

describe("Mongo plugin migration snapshot readers", () => {
  it("uses server BSON sizing before one bounded full projection and forwards exact deadline controls", async () => {
    const calls: Readonly<{ kind: string; value: unknown; options: unknown }>[] = []; const signal = new AbortController().signal; const session = Object.freeze({ id: "snapshot-session" });
    const collection = {
      aggregate: (pipeline: readonly unknown[], options: unknown) => { calls.push({ kind: "aggregate", value: pipeline, options });
        return { toArray: async () => [{ _id: projectId, lastUpdatedAt: top, version: 1, projectBsonBytes: 4096 }] }; },
      findOne: async (filter: unknown, options: unknown) => { calls.push({ kind: "findOne", value: filter, options });
        return { _id: projectId, lastUpdatedAt: top, version: 1, draftWorkflow: workflow, liveWorkflow: workflow }; },
    };
    const readers = createMongoMigrationSnapshotReaders(collection, session);
    const page = await readers.readPage(undefined, 33, 10, signal); const full = await readers.readProject(projectId, 9, signal);
    expect(page).toHaveLength(1); expect(full).toMatchObject({ projectId }); expect(calls).toHaveLength(2);
    expect(JSON.stringify(calls[0]!.value)).toContain('"$bsonSize":"$$ROOT"'); expect(JSON.stringify(calls[0]!.value)).not.toMatch(/draftWorkflow|liveWorkflow/);
    expect(calls[0]!.options).toMatchObject({ session, maxTimeMS: 10, signal });
    expect(calls[1]!.value).toEqual({ _id: projectId }); expect(calls[1]!.options).toMatchObject({ session, maxTimeMS: 9, signal,
      projection: { _id: 1, createdAt: 1, lastUpdatedAt: 1, version: 1, draftWorkflow: 1, liveWorkflow: 1, pluginMigrationPointer: 1 } });
  });
});
