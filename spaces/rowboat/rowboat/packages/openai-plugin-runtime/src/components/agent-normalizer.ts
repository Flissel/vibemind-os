import { lstat, readdir } from "node:fs/promises";
import { basename, extname, join, relative, sep } from "node:path";
import { parse } from "yaml";
import { PluginSourceSecurityError, resolveContainedPath } from "../import/path-guard.js";
import { readBoundedContainedFile } from "./asset-normalizer.js";

const MAX_AGENT_BYTES = 1024 * 1024;

type ImmutableMetadata = null | string | number | boolean | readonly ImmutableMetadata[] | {
  readonly [key: string]: ImmutableMetadata;
};

export type NormalizedAgent =
  | {
      readonly name: string;
      readonly path: string;
      readonly digest: string;
      readonly surface: "composer_metadata";
      readonly metadata: ImmutableMetadata;
      readonly executionAuthority: false;
    }
  | {
      readonly name: string;
      readonly path: string;
      readonly digest: string;
      readonly surface: "agent_template";
      readonly instructions: string;
      readonly executionAuthority: false;
    };

function compareCodePoints(left: string, right: string): number {
  const leftIterator = left[Symbol.iterator]();
  const rightIterator = right[Symbol.iterator]();
  while (true) {
    const leftCharacter = leftIterator.next();
    const rightCharacter = rightIterator.next();
    if (leftCharacter.done || rightCharacter.done) return leftCharacter.done === rightCharacter.done ? 0 : leftCharacter.done ? -1 : 1;
    const leftPoint = leftCharacter.value.codePointAt(0) ?? 0;
    const rightPoint = rightCharacter.value.codePointAt(0) ?? 0;
    if (leftPoint !== rightPoint) return leftPoint < rightPoint ? -1 : 1;
  }
}

function slashPath(value: string): string {
  return value.split(sep).join("/");
}

function immutableMetadata(value: unknown): ImmutableMetadata {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return Object.freeze(value.map(immutableMetadata));
  if (typeof value === "object") {
    const result: Record<string, ImmutableMetadata> = Object.create(null) as Record<string, ImmutableMetadata>;
    for (const key of Object.keys(value).sort(compareCodePoints)) {
      if (key === "__proto__" || key === "constructor" || key === "prototype") {
        throw new Error("agent_invalid: unsafe metadata key");
      }
      const nested = (value as Record<string, unknown>)[key];
      if (nested === undefined) throw new Error("agent_invalid: undefined metadata value");
      result[key] = immutableMetadata(nested);
    }
    return Object.freeze(result);
  }
  throw new Error("agent_invalid: unsupported metadata value");
}

async function collectFiles(directory: string, pluginRoot: string, output: string[]): Promise<void> {
  for (const name of (await readdir(directory)).sort(compareCodePoints)) {
    const candidate = await resolveContainedPath(pluginRoot, relative(pluginRoot, join(directory, name)));
    const stats = await lstat(candidate);
    if (stats.isSymbolicLink()) throw new PluginSourceSecurityError("path_escape", "agent link rejected");
    if (stats.isDirectory()) await collectFiles(candidate, pluginRoot, output);
    else if (stats.isFile()) output.push(candidate);
    else throw new PluginSourceSecurityError("path_escape", "unsupported agent entry");
  }
}

export async function normalizeAgents(
  agentsRoot: string,
  pluginRoot: string,
): Promise<readonly NormalizedAgent[]> {
  const canonicalRoot = await resolveContainedPath(pluginRoot, relative(pluginRoot, agentsRoot));
  const files: string[] = [];
  await collectFiles(canonicalRoot, pluginRoot, files);
  const agents: NormalizedAgent[] = [];
  for (const candidate of files.sort(compareCodePoints)) {
    const filename = basename(candidate);
    if (filename.endsWith(".md.tmpl")) continue;
    const isComposerMetadata = candidate === join(canonicalRoot, "openai.yaml");
    const isTemplate = extname(filename).toLowerCase() === ".md";
    if (!isComposerMetadata && !isTemplate) continue;
    const file = await readBoundedContainedFile(candidate, pluginRoot, MAX_AGENT_BYTES);
    const name = filename.replace(/\.(?:yaml|md)$/i, "");
    if (isComposerMetadata) {
      agents.push(Object.freeze({
        name,
        path: slashPath(relative(pluginRoot, candidate)),
        digest: file.digest,
        surface: "composer_metadata",
        metadata: immutableMetadata(parse(file.bytes.toString("utf8"))),
        executionAuthority: false,
      }));
    } else {
      agents.push(Object.freeze({
        name,
        path: slashPath(relative(pluginRoot, candidate)),
        digest: file.digest,
        surface: "agent_template",
        instructions: new TextDecoder("utf-8", { fatal: true }).decode(file.bytes),
        executionAuthority: false,
      }));
    }
  }
  return Object.freeze(agents);
}
