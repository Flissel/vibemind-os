import { describe, expect, it } from "vitest";
import { captureMigrationProjectState } from "@/src/application/services/plugin-migration-project-state";

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

  it("rejects accessor-backed project captures without invoking the accessor", () => {
    let calls = 0; const selected = project() as Record<string, unknown>; Object.defineProperty(selected, "liveWorkflow", { enumerable: true, get: () => { calls += 1; return workflow(top, "bad"); } });
    expect(() => captureMigrationProjectState(selected)).toThrow("source_invalid"); expect(calls).toBe(0);
  });
});
