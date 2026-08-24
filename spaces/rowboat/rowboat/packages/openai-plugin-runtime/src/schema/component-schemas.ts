import { z } from "zod";

const EnvironmentVariableNameSchema = z
  .string()
  .regex(/^[A-Z][A-Z0-9_]*$/);

export const AppFileSchema = z
  .object({
    apps: z.record(
      z.string(),
      z
        .object({
          id: z.string().regex(/^connector_[a-f0-9]+$/),
        })
        .strict(),
    ),
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
    type: z.literal("process"),
    command: z.string().min(1),
    args: z.array(z.string()).optional(),
    cwd: z.string().min(1).optional(),
    env: z.record(z.string(), z.string()).optional(),
    env_vars: z.array(EnvironmentVariableNameSchema).optional(),
    tool_timeout_sec: z.number().positive().optional(),
  })
  .strict();

export const McpServerSchema = z.discriminatedUnion("type", [
  HttpMcpSchema,
  ProcessMcpSchema,
]);

export const McpFileSchema = z.record(z.string(), McpServerSchema);

export const HookFileSchema = z
  .object({
    hooks: z.record(z.string(), z.unknown()),
  })
  .strict();

export type AppFile = z.infer<typeof AppFileSchema>;
export type HttpMcp = z.infer<typeof HttpMcpSchema>;
export type ProcessMcp = z.infer<typeof ProcessMcpSchema>;
