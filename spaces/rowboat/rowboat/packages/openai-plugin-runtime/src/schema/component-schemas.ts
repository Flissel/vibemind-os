import { z } from "zod";

const EnvironmentVariableNameSchema = z
  .string()
  .regex(/^[A-Z][A-Z0-9_]*$/);

export const AppDeclarationSchema = z
  .object({
    id: z
      .string()
      .regex(/^(?:connector|asdk_app|templated_apps)_[a-f0-9]+$/),
    category: z.string().min(1).optional(),
    capabilities: z.array(z.enum(["read", "write"])).optional(),
  })
  .strict();

export const AppFileEnvelopeSchema = z
  .object({ apps: z.record(z.string(), z.unknown()) })
  .strict();

export const AppFileSchema = z
  .object({
    apps: z.record(z.string(), AppDeclarationSchema),
  })
  .strict();

export const HttpMcpSchema = z
  .object({
    type: z.literal("http"),
    url: z.string().url(),
    oauth_resource: z.string().url().optional(),
    bearer_token_env_var: EnvironmentVariableNameSchema.optional(),
    note: z.string().optional(),
  })
  .strict();

export const ProcessMcpSchema = z
  .object({
    command: z.string().min(1),
    args: z.array(z.string()).optional(),
    cwd: z.string().optional(),
    env: z.record(z.string(), z.string()).optional(),
    env_vars: z.array(EnvironmentVariableNameSchema).optional(),
    tool_timeout_sec: z.number().positive().optional(),
  })
  .strict()
  .transform((process) => ({ type: "process" as const, ...process }));

export const McpServerSchema = z.union([HttpMcpSchema, ProcessMcpSchema]);

export const McpFileEnvelopeSchema = z
  .object({ mcpServers: z.record(z.string(), z.unknown()) })
  .strict();

export const McpFileSchema = z
  .object({
    mcpServers: z.record(z.string(), McpServerSchema),
  })
  .strict();

export const HookFileSchema = z
  .object({
    hooks: z.record(z.string(), z.unknown()),
  })
  .strict();

export type AppFile = z.infer<typeof AppFileSchema>;
export type HttpMcp = z.infer<typeof HttpMcpSchema>;
export type ProcessMcp = z.infer<typeof ProcessMcpSchema>;
