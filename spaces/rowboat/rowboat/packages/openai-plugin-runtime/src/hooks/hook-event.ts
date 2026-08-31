export type HookEventType = "post_tool_use" | "stop";

export function normalizeHookEvent(source: unknown): HookEventType {
  if (source === "PostToolUse") return "post_tool_use";
  if (source === "Stop") return "stop";
  throw new Error("component_unsupported");
}
