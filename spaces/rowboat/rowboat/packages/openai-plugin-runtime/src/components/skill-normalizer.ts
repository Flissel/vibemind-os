import { relative, sep } from "node:path";
import { parse } from "yaml";
import { PluginSourceSecurityError } from "../import/path-guard.js";
import { readBoundedContainedFile } from "./asset-normalizer.js";
import {
  assertSafeDescriptorPath,
  resolveComponentRoot,
  resolveExistingComponentPath,
} from "./component-path-security.js";

const MAX_SKILL_BYTES = 1024 * 1024;
const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/;
const MARKDOWN_DESTINATION = /!?\[[^\]]*\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g;
const INLINE_CODE_RESOURCE = /`((?:(?:\.\.?\/)+|(?:references|scripts|assets)\/)[^`\s]+)`/g;
const PROSE_RESOURCE = /(?:^|[\s"'(\[])((?:(?:\.\.?\/)*(?:references|scripts|assets)\/)[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.[A-Za-z0-9][A-Za-z0-9_-]*)(?=$|[.\s`"'),;:!?\]])/gmu;

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
  const add = (raw: string): void => {
    if (raw.startsWith("#") || /^[a-z][a-z0-9+.-]*:/i.test(raw)) return;
    let decoded: string;
    try {
      decoded = decodeURIComponent(raw.split(/[?#]/, 1)[0] ?? "");
    } catch {
      throw new PluginSourceSecurityError("path_escape", "invalid resource pointer");
    }
    if (decoded !== "") references.add(decoded);
  };
  for (const match of markdown.matchAll(MARKDOWN_DESTINATION)) {
    const raw = match[1];
    if (raw !== undefined) add(raw);
  }
  for (const match of markdown.matchAll(INLINE_CODE_RESOURCE)) {
    const raw = match[1];
    if (raw !== undefined) add(raw);
  }
  for (const match of markdown.matchAll(PROSE_RESOURCE)) {
    const raw = match[1];
    if (raw !== undefined) add(raw);
  }
  return Object.freeze([...references].sort(compareCodePoints));
}

export async function normalizeSkill(
  skillRoot: string,
  pluginRoot: string,
): Promise<NormalizedSkill> {
  const roots = await resolveComponentRoot(pluginRoot, skillRoot);
  const canonicalSkillRoot = roots.canonicalComponentRoot;
  const skillFile = await readBoundedContainedFile(
    `${canonicalSkillRoot}${sep}SKILL.md`,
    pluginRoot,
    MAX_SKILL_BYTES,
    canonicalSkillRoot,
  );
  const instructions = new TextDecoder("utf-8", { fatal: true }).decode(skillFile.bytes);
  const frontmatter = FRONTMATTER.exec(instructions);
  if (frontmatter?.[1] === undefined) throw new Error("skill_invalid: YAML frontmatter missing");
  const parsed: unknown = parse(frontmatter[1]);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("skill_invalid: YAML frontmatter must be a mapping");
  }
  const record = parsed as Record<string, unknown>;
  if (typeof record.name !== "string" || record.name === "") throw new Error("skill_invalid: name missing");
  assertSafeDescriptorPath(record.name);
  if (typeof record.description !== "string") throw new Error("skill_invalid: description missing");
  const resourcesByCanonicalPath = new Map<string, NormalizedSkillResource>();
  for (const pointer of localReferences(instructions)) {
    const canonical = (await resolveExistingComponentPath(
      pluginRoot,
      canonicalSkillRoot,
      `${canonicalSkillRoot}${sep}${pointer}`,
    )).canonicalPath;
    if (resourcesByCanonicalPath.has(canonical)) continue;
    const file = await readBoundedContainedFile(
      canonical,
      pluginRoot,
      MAX_SKILL_BYTES,
      canonicalSkillRoot,
    );
    resourcesByCanonicalPath.set(canonical, Object.freeze({
      path: assertSafeDescriptorPath(slashPath(relative(canonicalSkillRoot, canonical))),
      digest: file.digest,
    }));
  }
  const resources = [...resourcesByCanonicalPath.values()]
    .sort((left, right) => compareCodePoints(left.path, right.path));
  return Object.freeze({
    name: record.name,
    description: record.description,
    instructions,
    triggerRules: stringArray(record.trigger_rules ?? record.triggerRules),
    resources: Object.freeze(resources),
  });
}
