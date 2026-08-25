import { relative, sep } from "node:path";
import { parse } from "yaml";
import { PluginSourceSecurityError, resolveContainedPath } from "../import/path-guard.js";
import { readBoundedContainedFile } from "./asset-normalizer.js";

const MAX_SKILL_BYTES = 1024 * 1024;
const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/;
const MARKDOWN_DESTINATION = /!?\[[^\]]*\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g;

export interface NormalizedSkillResource {
  readonly path: string;
  readonly digest: string;
}

export interface NormalizedSkill {
  readonly name: string;
  readonly description: string;
  readonly instructions: string;
  readonly triggerRules: readonly string[];
  readonly resources: readonly NormalizedSkillResource[];
}

function compareCodePoints(left: string, right: string): number {
  const leftPoints = [...left].map((value) => value.codePointAt(0) ?? 0);
  const rightPoints = [...right].map((value) => value.codePointAt(0) ?? 0);
  for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index += 1) {
    const difference = (leftPoints[index] ?? 0) - (rightPoints[index] ?? 0);
    if (difference !== 0) return difference < 0 ? -1 : 1;
  }
  return leftPoints.length - rightPoints.length;
}

function slashPath(value: string): string {
  return value.split(sep).join("/");
}

function stringArray(value: unknown): readonly string[] {
  if (value === undefined) return Object.freeze([]);
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error("skill_invalid: trigger rules must be strings");
  }
  return Object.freeze([...value]);
}

function localReferences(markdown: string): readonly string[] {
  const references = new Set<string>();
  for (const match of markdown.matchAll(MARKDOWN_DESTINATION)) {
    const raw = match[1];
    if (raw === undefined || raw.startsWith("#") || /^[a-z][a-z0-9+.-]*:/i.test(raw)) continue;
    let decoded: string;
    try {
      decoded = decodeURIComponent(raw.split(/[?#]/, 1)[0] ?? "");
    } catch {
      throw new PluginSourceSecurityError("path_escape", "invalid resource pointer");
    }
    if (decoded !== "") references.add(decoded);
  }
  return Object.freeze([...references].sort(compareCodePoints));
}

export async function normalizeSkill(
  skillRoot: string,
  pluginRoot: string,
): Promise<NormalizedSkill> {
  const canonicalSkillRoot = await resolveContainedPath(pluginRoot, relative(pluginRoot, skillRoot));
  const skillFile = await readBoundedContainedFile(`${canonicalSkillRoot}${sep}SKILL.md`, pluginRoot, MAX_SKILL_BYTES);
  const instructions = new TextDecoder("utf-8", { fatal: true }).decode(skillFile.bytes);
  const frontmatter = FRONTMATTER.exec(instructions);
  if (frontmatter?.[1] === undefined) throw new Error("skill_invalid: YAML frontmatter missing");
  const parsed: unknown = parse(frontmatter[1]);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("skill_invalid: YAML frontmatter must be a mapping");
  }
  const record = parsed as Record<string, unknown>;
  if (typeof record.name !== "string" || record.name === "") throw new Error("skill_invalid: name missing");
  if (typeof record.description !== "string") throw new Error("skill_invalid: description missing");
  const resources: NormalizedSkillResource[] = [];
  for (const pointer of localReferences(instructions)) {
    const canonical = await resolveContainedPath(canonicalSkillRoot, pointer);
    await resolveContainedPath(pluginRoot, relative(pluginRoot, canonical));
    const file = await readBoundedContainedFile(canonical, pluginRoot, MAX_SKILL_BYTES);
    resources.push(Object.freeze({
      path: slashPath(relative(canonicalSkillRoot, canonical)),
      digest: file.digest,
    }));
  }
  return Object.freeze({
    name: record.name,
    description: record.description,
    instructions,
    triggerRules: stringArray(record.trigger_rules ?? record.triggerRules),
    resources: Object.freeze(resources),
  });
}
