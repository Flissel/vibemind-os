import { describe, expect, it } from "vitest";
import { assertMigrationProjectStateUnchanged, captureMigrationProjectManifestCandidate, captureMigrationProjectManifestEntry, captureMigrationProjectSizeCandidate, captureMigrationProjectState, migrationProjectManifestEntryFromState, migrationProjectPointerCasFilter, parseMigrationPointerRecord } from "@/src/application/services/plugin-migration-project-state";
import customerSupport from "@/app/lib/prebuilt-cards/customer-support.json";

const projectId = "11111111-1111-4111-8111-111111111111";
const top = "2026-08-26T10:00:00.000Z";
const workflow = (updatedAt: string, marker: string) => ({ lastUpdatedAt: updatedAt, tools: [], agents: [], prompts: [], pipelines: [], startAgent: marker });
const project = () => ({ _id: projectId, lastUpdatedAt: top, version: 9, draftWorkflow: workflow("2026-08-26T09:59:58.000Z", "draft"), liveWorkflow: workflow("2026-08-26T09:59:59.000Z", "live") });

describe("authoritative plugin migration project state", () => {
  it("uses top-level lastUpdatedAt epoch milliseconds as source revision, independently of pointer provenance", () => {
    const state = captureMigrationProjectState(project());
    expect(state.sourceProjectRevision).toBe(Date.parse(top)); expect(state.pointer).toBeNull();
    const pointed = captureMigrationProjectState({ ...project(), pluginMigrationPointer: { migrationRecordId: "22222222-2222-4222-8222-222222222222", catalogDigest: "a".repeat(64), sourceProjectRevision: 7, sourceStateDigest: "b".repeat(64) } });
    expect(pointed.sourceProjectRevision).toBe(Date.parse(top)); expect(pointed.pointer?.sourceProjectRevision).toBe(7);
  });

  it("uses createdAt as the documented source revision only when top-level lastUpdatedAt is absent", () => {
    const fresh = project(); delete (fresh as Partial<typeof fresh>).lastUpdatedAt; Object.assign(fresh, { createdAt: "2026-08-26T08:00:00.000Z" });
    const state = captureMigrationProjectState(fresh);
    expect(state).toMatchObject({ sourceTimestampField: "createdAt", sourceTimestamp: "2026-08-26T08:00:00.000Z", topLevelUpdatedAt: null, topLevelCreatedAt: "2026-08-26T08:00:00.000Z", sourceProjectRevision: Date.parse("2026-08-26T08:00:00.000Z") });
    expect(migrationProjectPointerCasFilter(state)).toEqual({ _id: projectId, createdAt: "2026-08-26T08:00:00.000Z", lastUpdatedAt: { $exists: false }, version: 9, pluginMigrationPointer: { $exists: false } });
  });

  it("uses scalar CAS identity and never embeds BSON-order-sensitive workflow documents", () => {
    const state = captureMigrationProjectState(project()); const filter = migrationProjectPointerCasFilter(state);
    expect(filter).toEqual({ _id: projectId, lastUpdatedAt: top, version: 9, pluginMigrationPointer: { $exists: false } });
    expect(filter).not.toHaveProperty("draftWorkflow"); expect(filter).not.toHaveProperty("liveWorkflow");
  });

  it("distinguishes an absent migration pointer from an explicit null prior pointer", () => {
    const absent = captureMigrationProjectState(project()); const presentNull = captureMigrationProjectState({ ...project(), pluginMigrationPointer: null });
    expect(absent.pointerFieldPresent).toBe(false); expect(presentNull.pointerFieldPresent).toBe(true);
    expect(migrationProjectPointerCasFilter(absent).pluginMigrationPointer).toEqual({ $exists: false });
    expect(migrationProjectPointerCasFilter(presentNull).pluginMigrationPointer).toBeNull();
    expect(migrationProjectManifestEntryFromState(absent).scalarIdentityDigest).not.toBe(migrationProjectManifestEntryFromState(presentNull).scalarIdentityDigest);
  });

  it("captures a descriptor-safe digest-only full-state manifest independent of BSON key order", () => {
    const left = project(); const right = Object.fromEntries(Object.entries(project()).reverse());
    expect(captureMigrationProjectManifestEntry(left)).toEqual(captureMigrationProjectManifestEntry(right));
    expect(migrationProjectManifestEntryFromState(captureMigrationProjectState(project()))).toEqual(captureMigrationProjectManifestEntry(left));
    let calls = 0; Object.defineProperty(left, "version", { enumerable: true, get: () => { calls += 1; return 9; } });
    expect(() => captureMigrationProjectManifestEntry(left)).toThrow("source_invalid"); expect(calls).toBe(0);
  });

  it("binds full real-size workflow content so a same-millisecond edit changes only the manifest state digest", () => {
    const realWorkflow = { ...customerSupport, lastUpdatedAt: "2026-08-26T09:59:59.000Z" };
    const original = captureMigrationProjectManifestCandidate({ ...project(), draftWorkflow: realWorkflow, liveWorkflow: realWorkflow });
    const edited = captureMigrationProjectManifestCandidate({ ...project(), draftWorkflow: realWorkflow, liveWorkflow: { ...realWorkflow, startAgent: "same-ms-edit" } });
    expect(original.capturedBytes).toBeGreaterThan(1_000); expect(edited.scalarIdentityDigest).toBe(original.scalarIdentityDigest);
    expect(edited.stateDigest).not.toBe(original.stateDigest);
  });

  it("derives the same scalar identity from server-side size metadata without workflows", () => {
    const full = captureMigrationProjectManifestCandidate(project());
    const metadata = captureMigrationProjectSizeCandidate({ _id: projectId, lastUpdatedAt: top, version: 9, projectBsonBytes: 4096 });
    expect(metadata).toEqual({ projectId, scalarIdentityDigest: full.scalarIdentityDigest, projectBsonBytes: 4096 });
  });

  it.each([
    ["both absent", { createdAt: undefined, lastUpdatedAt: undefined }],
    ["created invalid", { createdAt: "not-an-iso-date", lastUpdatedAt: undefined }],
    ["last invalid", { createdAt: top, lastUpdatedAt: "not-an-iso-date" }],
  ])("fails closed when the authoritative timestamp is %s", (_name, timestamps) => {
    const selected = { ...project(), ...timestamps }; if (timestamps.createdAt === undefined) delete selected.createdAt; if (timestamps.lastUpdatedAt === undefined) delete selected.lastUpdatedAt;
    expect(() => captureMigrationProjectState(selected)).toThrow("migration_project_invalid");
  });

  it.each(["top", "version", "draft", "live"])("changes the complete project digest on a %s mutation", mutation => {
    const original = project(); const changed = project();
    if (mutation === "top") changed.lastUpdatedAt = "2026-08-26T10:00:01.000Z";
    if (mutation === "version") changed.version += 1;
    if (mutation === "draft") changed.draftWorkflow = workflow(changed.draftWorkflow.lastUpdatedAt, "edited-draft");
    if (mutation === "live") changed.liveWorkflow = workflow(changed.liveWorkflow.lastUpdatedAt, "published-live");
    expect(captureMigrationProjectState(changed).stateDigest).not.toBe(captureMigrationProjectState(original).stateDigest);
  });

  it("uses the digest to detect same-millisecond edits and captures immutable whole-state rollback provenance", () => {
    const original = captureMigrationProjectState(project()); const edited = project(); edited.liveWorkflow = workflow(edited.liveWorkflow.lastUpdatedAt, "same-ms-edit");
    const changed = captureMigrationProjectState(edited); expect(changed.sourceProjectRevision).toBe(original.sourceProjectRevision); expect(changed.stateDigest).not.toBe(original.stateDigest);
    expect(changed.rollbackSnapshot).toMatchObject({ projectId, topLevelUpdatedAt: top, draftWorkflow: edited.draftWorkflow, liveWorkflow: edited.liveWorkflow });
    expect(Object.isFrozen(changed.rollbackSnapshot)).toBe(true); expect(JSON.stringify(changed.rollbackSnapshot)).not.toMatch(/credential|secret|password/i);
  });

  it("accepts unchanged real-card content despite BSON key order and rejects a same-ms content edit before writes", () => {
    const updatedAt = "2026-08-26T09:59:59.000Z"; const canonicalWorkflow = { ...customerSupport, lastUpdatedAt: updatedAt };
    const reorderedWorkflow = Object.fromEntries(Object.entries(canonicalWorkflow).reverse());
    const expected = captureMigrationProjectState({ ...project(), draftWorkflow: canonicalWorkflow, liveWorkflow: canonicalWorkflow });
    const unchanged = captureMigrationProjectState({ ...project(), draftWorkflow: reorderedWorkflow, liveWorkflow: reorderedWorkflow });
    expect(() => assertMigrationProjectStateUnchanged(expected, unchanged)).not.toThrow();
    const changed = captureMigrationProjectState({ ...project(), draftWorkflow: reorderedWorkflow, liveWorkflow: { ...reorderedWorkflow, startAgent: "same-ms-mutation" } });
    expect(() => assertMigrationProjectStateUnchanged(expected, changed)).toThrow("migration_pointer_conflict");
  });

  it("rejects accessor-backed project captures without invoking the accessor", () => {
    let calls = 0; const selected = project() as Record<string, unknown>; Object.defineProperty(selected, "liveWorkflow", { enumerable: true, get: () => { calls += 1; return workflow(top, "bad"); } });
    expect(() => captureMigrationProjectState(selected)).toThrow("source_invalid"); expect(calls).toBe(0);
  });

  it("maps a malformed pointed migration record to the typed project-local pointer blocker", () => {
    expect(() => parseMigrationPointerRecord({ id: "raw-invalid" })).toThrow("migration_pointer_invalid");
  });
});
