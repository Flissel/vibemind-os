import { z } from "zod";

const RelativePointer = z.string().min(1);

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
    capabilities: z
      .array(z.enum(["Interactive", "Read", "Write"]))
      .optional(),
    defaultPrompt: z.array(z.string()).optional(),
    brandColor: z.string().optional(),
    composerIcon: RelativePointer.optional(),
    logo: RelativePointer.optional(),
    screenshots: z.array(RelativePointer).optional(),
    websiteURL: z.string().url().optional(),
    privacyPolicyURL: z.string().url().optional(),
    termsOfServiceURL: z.string().url().optional(),
  })
  .strict();

export const PluginManifestSchema = z
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

export type PluginManifest = z.infer<typeof PluginManifestSchema>;

function formatIssuePath(path: ReadonlyArray<string | number>): string {
  return path.length === 0 ? "<root>" : path.join(".");
}

export function parsePluginManifest(input: unknown): PluginManifest {
  const result = PluginManifestSchema.safeParse(input);

  if (result.success) {
    return result.data;
  }

  const issuePaths = result.error.issues
    .map((issue) => formatIssuePath(issue.path))
    .sort()
    .join(", ");

  throw new Error(`manifest_invalid: ${issuePaths}`);
}
