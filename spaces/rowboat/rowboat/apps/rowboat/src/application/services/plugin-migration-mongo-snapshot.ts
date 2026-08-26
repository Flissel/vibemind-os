import { captureMigrationProjectManifestCandidate, captureMigrationProjectSizeCandidate } from "./plugin-migration-project-state";

interface SnapshotQueryOptions { readonly session: unknown; readonly maxTimeMS: number; readonly signal: AbortSignal }
interface SnapshotFindOptions extends SnapshotQueryOptions { readonly projection: Readonly<Record<string, 1>> }
export interface MongoMigrationSnapshotCollection {
  readonly aggregate: (pipeline: readonly Readonly<Record<string, unknown>>[], options: SnapshotQueryOptions) => Readonly<{ toArray: () => Promise<readonly unknown[]> }>;
  readonly findOne: (filter: Readonly<Record<string, unknown>>, options: SnapshotFindOptions) => Promise<unknown>;
}

const FULL_PROJECTION = Object.freeze({ _id: 1, createdAt: 1, lastUpdatedAt: 1, version: 1, draftWorkflow: 1, liveWorkflow: 1, pluginMigrationPointer: 1 } as const);

export function createMongoMigrationSnapshotReaders(collection: MongoMigrationSnapshotCollection, session: unknown) {
  return Object.freeze({
    readPage: async (afterProjectId: string | undefined, limit: number, remainingMs: number, signal: AbortSignal) => {
      const match = afterProjectId === undefined ? {} : { _id: { $gt: afterProjectId } };
      const pipeline = Object.freeze([
        Object.freeze({ $match: match }), Object.freeze({ $sort: { _id: 1 } }), Object.freeze({ $limit: limit }),
        Object.freeze({ $project: { _id: 1, createdAt: 1, lastUpdatedAt: 1, version: 1, pluginMigrationPointer: 1, projectBsonBytes: { $bsonSize: "$$ROOT" } } }),
      ]);
      const documents = await collection.aggregate(pipeline, Object.freeze({ session, maxTimeMS: remainingMs, signal })).toArray();
      return Object.freeze(documents.map(captureMigrationProjectSizeCandidate));
    },
    readProject: async (projectId: string, remainingMs: number, signal: AbortSignal) => {
      const document = await collection.findOne(Object.freeze({ _id: projectId }), Object.freeze({ projection: FULL_PROJECTION, session, maxTimeMS: remainingMs, signal }));
      if (document === null) throw new Error("project_not_found");
      return captureMigrationProjectManifestCandidate(document);
    },
  });
}
