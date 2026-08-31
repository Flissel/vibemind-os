import { z } from "zod";

function isPortableRelativePointer(pointer: string): boolean {
  if (
    pointer.includes("\0") ||
    pointer.includes("\\") ||
    pointer.startsWith("/") ||
    /^[A-Za-z]:/.test(pointer)
  ) {
    return false;
  }
  const withoutPrefix = pointer.startsWith("./") ? pointer.slice(2) : pointer;
  const withoutTrailingSeparator = withoutPrefix.endsWith("/")
    ? withoutPrefix.slice(0, -1)
    : withoutPrefix;
  if (withoutTrailingSeparator.length === 0) return false;
  return withoutTrailingSeparator
    .split("/")
    .every((segment) => segment !== "" && segment !== "." && segment !== "..");
}

const RelativePointer = z.string().min(1).refine(isPortableRelativePointer);

const AuthorSchema = z
  .object({
    name: z.string().min(1),
    email: z.string().email().optional(),
    url: z.string().url().optional(),
  })
  .strict();

const PluginInterfaceSchema = z
  .object({
    displayName: z.string().min(1),
    shortDescription: z.string().optional(),
    longDescription: z.string().optional(),
    developerName: z.string().optional(),
    category: z.string().optional(),
    capabilities: z.array(z.string().min(1)).optional(),
    defaultPrompt: z.union([z.string(), z.array(z.string())]).optional(),
    brandColor: z.string().optional(),
    composerIcon: RelativePointer.optional(),
    logo: RelativePointer.optional(),
    logoDark: RelativePointer.optional(),
    screenshots: z.array(RelativePointer).optional(),
    websiteURL: z.string().url().optional(),
    privacyPolicyURL: z.string().url().optional(),
    termsOfServiceURL: z.string().url().optional(),
  })
  .strict();

const RawPluginManifestSchema = z
  .object({
    name: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
    version: z.string().min(1),
    description: z.string().min(1),
    author: AuthorSchema.optional(),
    homepage: z.string().url().optional(),
    repository: z.string().min(1).optional(),
    license: z.string().optional(),
    keywords: z.array(z.string()).optional(),
    skills: RelativePointer.optional(),
    agents: RelativePointer.optional(),
    commands: RelativePointer.optional(),
    hooks: RelativePointer.optional(),
    mcpServers: RelativePointer.optional(),
    apps: RelativePointer.optional(),
    interface: PluginInterfaceSchema,
  })
  .strict();

export type DeepReadonly<T> = T extends readonly (infer Item)[]
  ? readonly DeepReadonly<Exclude<Item, undefined>>[]
  : T extends object
    ? { readonly [Key in keyof T]: DeepReadonly<Exclude<T[Key], undefined>> }
    : T;

export type PluginManifest = DeepReadonly<z.infer<typeof RawPluginManifestSchema>>;

function isReadonlyArray(value: unknown): value is readonly unknown[] {
  return Array.isArray(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !isReadonlyArray(value);
}

function normalizeAndFreeze(value: unknown): unknown {
  if (isReadonlyArray(value)) {
    return Object.freeze(value.map((item) => normalizeAndFreeze(item)));
  }

  if (isRecord(value)) {
    const normalized: Record<string, unknown> = {};

    for (const [key, nestedValue] of Object.entries(value)) {
      if (nestedValue !== undefined) {
        normalized[key] = normalizeAndFreeze(nestedValue);
      }
    }

    return Object.freeze(normalized);
  }

  return value;
}

export const PluginManifestSchema = RawPluginManifestSchema.transform(
  (manifest): PluginManifest => normalizeAndFreeze(manifest) as PluginManifest,
);

function formatIssuePath(path: ReadonlyArray<string | number>): string {
  return path.length === 0 ? "<root>" : path.join(".");
}

export class PluginManifestValidationError extends Error {
  readonly code = "manifest_invalid" as const;
  readonly issuePaths: readonly string[];

  constructor(issuePaths: readonly string[]) {
    const sortedIssuePaths = Object.freeze([...issuePaths].sort());

    super(`manifest_invalid: ${sortedIssuePaths.join(", ")}`);
    this.name = "PluginManifestValidationError";
    this.issuePaths = sortedIssuePaths;
  }
}

export function parsePluginManifest(input: unknown): PluginManifest {
  const result = PluginManifestSchema.safeParse(input);

  if (result.success) {
    return result.data;
  }

  const issuePaths = result.error.issues.map((issue) => formatIssuePath(issue.path));

  throw new PluginManifestValidationError(issuePaths);
}
