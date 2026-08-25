import { lstat, readdir } from "node:fs/promises";
import { basename, join, relative, sep } from "node:path";
import { PluginSourceSecurityError, resolveContainedPath } from "../import/path-guard.js";
import { readBoundedContainedFile } from "./asset-normalizer.js";

const MAX_COMMAND_BYTES = 1024 * 1024;

export interface NormalizedCommandResource {
  readonly path: string;
  readonly digest: string;
}

export interface NormalizedCommand {
  readonly name: string;
  readonly path: string;
  readonly digest: string;
  readonly instructions: string;
  readonly invocation: "explicit_user";
  readonly resources: readonly NormalizedCommandResource[];
}

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

async function collectFiles(directory: string, pluginRoot: string, output: string[]): Promise<void> {
  for (const name of (await readdir(directory)).sort(compareCodePoints)) {
    const candidate = await resolveContainedPath(pluginRoot, relative(pluginRoot, join(directory, name)));
    const stats = await lstat(candidate);
    if (stats.isSymbolicLink()) throw new PluginSourceSecurityError("path_escape", "command link rejected");
    if (stats.isDirectory()) await collectFiles(candidate, pluginRoot, output);
    else if (stats.isFile()) output.push(candidate);
    else throw new PluginSourceSecurityError("path_escape", "unsupported command entry");
  }
}

function isAction(candidate: string): boolean {
  const filename = basename(candidate);
  return filename.endsWith(".md") && filename !== "_conventions.md";
}

export async function normalizeCommands(
  commandsRoot: string,
  pluginRoot: string,
): Promise<readonly NormalizedCommand[]> {
  const canonicalRoot = await resolveContainedPath(pluginRoot, relative(pluginRoot, commandsRoot));
  const files: string[] = [];
  await collectFiles(canonicalRoot, pluginRoot, files);
  files.sort((left, right) => compareCodePoints(slashPath(relative(canonicalRoot, left)), slashPath(relative(canonicalRoot, right))));
  const resources: NormalizedCommandResource[] = [];
  for (const candidate of files.filter((file) => !isAction(file))) {
    const content = await readBoundedContainedFile(candidate, pluginRoot, MAX_COMMAND_BYTES);
    resources.push(Object.freeze({ path: content.path, digest: content.digest }));
  }
  const frozenResources = Object.freeze(resources);
  const commands: NormalizedCommand[] = [];
  for (const candidate of files.filter(isAction)) {
    const content = await readBoundedContainedFile(candidate, pluginRoot, MAX_COMMAND_BYTES);
    const relativeName = slashPath(relative(canonicalRoot, candidate)).replace(/\.md$/, "");
    commands.push(Object.freeze({
      name: relativeName,
      path: content.path,
      digest: content.digest,
      instructions: new TextDecoder("utf-8", { fatal: true }).decode(content.bytes),
      invocation: "explicit_user",
      resources: frozenResources,
    }));
  }
  return Object.freeze(commands);
}
